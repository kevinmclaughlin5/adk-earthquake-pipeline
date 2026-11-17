"""Pipeline tools the ADK agent can call.

Each tool does one stage of the pipeline. Instead of passing large payloads
through the LLM, tools write files to a local run folder and pass *paths*
to each other through the ADK session state (tool_context.state). The agent
only sees short summaries, which keeps it cheap and fast.
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from google.adk.tools.tool_context import ToolContext

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
OUTPUT_ROOT = Path(os.getenv("OUTPUT_DIR", "output"))
MAG_BUCKETS = [(0, 3, "<3.0"), (3, 4, "3.0-3.9"), (4, 5, "4.0-4.9"), (5, 6, "5.0-5.9"), (6, 99, "6.0+")]


def _run_dir(tool_context: ToolContext) -> Path:
    run_id = tool_context.state.get("run_id")
    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tool_context.state["run_id"] = run_id
    path = OUTPUT_ROOT / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------
# 1. INGEST
# --------------------------------------------------------------------------
def fetch_earthquakes(days: int, min_magnitude: float, tool_context: ToolContext) -> dict:
    """Ingest earthquake events from the public USGS Earthquake API.

    Args:
        days: How many days back to pull (1-30).
        min_magnitude: Minimum magnitude to include (e.g. 2.5).

    Returns:
        A status dict with the number of events fetched and the raw file path.
    """
    days = max(1, min(int(days), 30))
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params = {
        "format": "geojson",
        "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": end.strftime("%Y-%m-%dT%H:%M:%S"),
        "minmagnitude": min_magnitude,
        "orderby": "time",
        "limit": 20000,
    }
    try:
        resp = requests.get(USGS_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return {"status": "error", "message": f"USGS request failed: {exc}"}

    raw_path = _run_dir(tool_context) / "raw_earthquakes.geojson"
    raw_path.write_text(json.dumps(data), encoding="utf-8")
    tool_context.state["raw_path"] = str(raw_path)
    tool_context.state["params"] = {"days": days, "min_magnitude": min_magnitude}

    return {"status": "success", "events_fetched": len(data.get("features", [])), "raw_path": str(raw_path)}


# --------------------------------------------------------------------------
# 2. PROCESS
# --------------------------------------------------------------------------
def _region(place: str | None) -> str:
    """'12 km SW of Ridgecrest, CA' -> 'CA'; 'Fiji region' -> 'Fiji region'."""
    if not place:
        return "Unknown"
    return place.split(", ")[-1].strip() if ", " in place else place.strip()


def _bucket(mag: float) -> str:
    for low, high, label in MAG_BUCKETS:
        if low <= mag < high:
            return label
    return "Unknown"


def process_earthquakes(tool_context: ToolContext) -> dict:
    """Clean, filter, and aggregate the raw earthquake data.

    Filters out non-earthquake events (quarry blasts, explosions) and records
    with no magnitude, then aggregates by region, by day, and by magnitude band.

    Returns:
        A status dict with headline stats and the processed file paths.
    """
    raw_path = tool_context.state.get("raw_path")
    if not raw_path or not Path(raw_path).exists():
        return {"status": "error", "message": "No raw data found. Run fetch_earthquakes first."}

    features = json.loads(Path(raw_path).read_text(encoding="utf-8")).get("features", [])

    # --- Clean + filter ---
    events, dropped = [], 0
    for f in features:
        p = f.get("properties", {})
        if p.get("type") != "earthquake" or p.get("mag") is None:
            dropped += 1
            continue
        lon, lat, depth = (f.get("geometry", {}).get("coordinates") or [None, None, None])[:3]
        ts = datetime.fromtimestamp(p["time"] / 1000, tz=timezone.utc)
        events.append({
            "id": f.get("id"),
            "time_utc": ts.isoformat(),
            "date": ts.date().isoformat(),
            "magnitude": round(float(p["mag"]), 2),
            "place": p.get("place") or "Unknown",
            "region": _region(p.get("place")),
            "depth_km": depth,
            "latitude": lat,
            "longitude": lon,
            "tsunami_flag": p.get("tsunami", 0),
            "url": p.get("url"),
        })

    if not events:
        return {"status": "error", "message": "No earthquake records left after filtering."}

    # --- Aggregate ---
    by_region = defaultdict(list)
    for e in events:
        by_region[e["region"]].append(e["magnitude"])
    region_stats = sorted(
        (
            {"region": r, "count": len(m), "avg_magnitude": round(sum(m) / len(m), 2), "max_magnitude": max(m)}
            for r, m in by_region.items()
        ),
        key=lambda x: x["count"],
        reverse=True,
    )
    daily_counts = dict(sorted(Counter(e["date"] for e in events).items()))
    bucket_counts = Counter(_bucket(e["magnitude"]) for e in events)
    magnitude_bands = {label: bucket_counts.get(label, 0) for _, _, label in MAG_BUCKETS}
    strongest = sorted(events, key=lambda e: e["magnitude"], reverse=True)[:10]

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": tool_context.state.get("params", {}),
        "total_events": len(events),
        "dropped_records": dropped,
        "regions_count": len(region_stats),
        "avg_magnitude": round(sum(e["magnitude"] for e in events) / len(events), 2),
        "max_magnitude": strongest[0]["magnitude"],
        "tsunami_flagged": sum(e["tsunami_flag"] for e in events),
        "region_stats": region_stats,
        "daily_counts": daily_counts,
        "magnitude_bands": magnitude_bands,
        "strongest_events": strongest,
    }

    run_dir = _run_dir(tool_context)
    clean_csv = run_dir / "clean_earthquakes.csv"
    with clean_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(events[0].keys()))
        writer.writeheader()
        writer.writerows(events)
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    tool_context.state["clean_csv_path"] = str(clean_csv)
    tool_context.state["summary_path"] = str(summary_path)

    return {
        "status": "success",
        "total_events": summary["total_events"],
        "dropped_records": dropped,
        "max_magnitude": summary["max_magnitude"],
        "top_regions": [r["region"] for r in region_stats[:5]],
        "files": [str(clean_csv), str(summary_path)],
    }


# --------------------------------------------------------------------------
# 3. VISUALIZE
# --------------------------------------------------------------------------
def build_report(tool_context: ToolContext) -> dict:
    """Build a self-contained HTML report (charts + table) from the processed summary.

    Returns:
        A status dict with the local path of the HTML report.
    """
    summary_path = tool_context.state.get("summary_path")
    if not summary_path or not Path(summary_path).exists():
        return {"status": "error", "message": "No processed summary found. Run process_earthquakes first."}

    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    template = (Path(__file__).parent / "report_template.html").read_text(encoding="utf-8")
    html = template.replace("__SUMMARY_JSON__", json.dumps(summary))

    report_path = _run_dir(tool_context) / "report.html"
    report_path.write_text(html, encoding="utf-8")
    tool_context.state["report_path"] = str(report_path)
    return {"status": "success", "report_path": str(report_path.resolve())}


# --------------------------------------------------------------------------
# 4. STORE
# --------------------------------------------------------------------------
def store_to_cloud(tool_context: ToolContext) -> dict:
    """Upload the raw data, processed data, and HTML report to cloud storage.

    Uses Google Cloud Storage when STORAGE_BACKEND=gcs (default), with the
    bucket from the GCS_BUCKET env var. STORAGE_BACKEND=local skips the upload.

    Returns:
        A status dict listing the storage URIs of each uploaded file.
    """
    keys = {"raw_path": "raw", "clean_csv_path": "processed", "summary_path": "processed", "report_path": "report"}
    files = [(tool_context.state.get(k), folder) for k, folder in keys.items() if tool_context.state.get(k)]
    if not files:
        return {"status": "error", "message": "Nothing to upload yet."}

    backend = os.getenv("STORAGE_BACKEND", "gcs").lower()
    run_id = tool_context.state.get("run_id", "manual")

    if backend == "local":
        return {"status": "skipped", "message": "STORAGE_BACKEND=local, files kept on disk only.",
                "files": [p for p, _ in files]}

    bucket_name = os.getenv("GCS_BUCKET")
    if not bucket_name:
        return {"status": "error", "message": "GCS_BUCKET env var is not set."}

    try:
        from google.cloud import storage  # imported here so local mode needs no GCP auth

        bucket = storage.Client().bucket(bucket_name)
        uris = []
        for local_path, folder in files:
            blob_name = f"earthquake-pipeline/{run_id}/{folder}/{Path(local_path).name}"
            blob = bucket.blob(blob_name)
            content_type = "text/html" if local_path.endswith(".html") else None
            blob.upload_from_filename(local_path, content_type=content_type)
            uris.append(f"gs://{bucket_name}/{blob_name}")
    except Exception as exc:  # surface auth/permission problems to the agent
        return {"status": "error", "message": f"GCS upload failed: {exc}"}

    tool_context.state["cloud_uris"] = uris
    return {"status": "success", "uploaded": uris}
