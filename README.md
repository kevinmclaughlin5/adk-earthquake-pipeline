# Agentic Earthquake Data Pipeline (Google ADK)

An end-to-end data engineering pipeline orchestrated by a Google Agent Development Kit (ADK) agent. The agent:

1. **Ingests** recent earthquake events from the public [USGS Earthquake API](https://earthquake.usgs.gov/fdsnws/event/1/) (no key needed)
2. **Processes** them: removes non-earthquake events (quarry blasts, explosions) and records with no magnitude, then aggregates by region, by day, and by magnitude band
3. **Visualizes** the results in a self-contained HTML report (KPIs, 3 charts, top-10 table)
4. **Stores** the raw data, processed data, and report in **Google Cloud Storage**

## How it works

```
User prompt ──► ADK root agent (Gemini)
                  │  decides parameters, calls tools in order,
                  │  checks each status, stops on errors
                  ├─► fetch_earthquakes   → output/<run_id>/raw_earthquakes.geojson
                  ├─► process_earthquakes → clean_earthquakes.csv, summary.json
                  ├─► build_report        → report.html
                  └─► store_to_cloud      → gs://<bucket>/earthquake-pipeline/<run_id>/{raw,processed,report}/
```

The agent works from a plain-English request. For example, *"last 3 days, magnitude 4+"* sets the tool parameters. Tools pass file paths to each other through ADK session state, so large datasets never go through the LLM.

```
├── main.py                      # CLI runner (ADK Runner + in-memory session)
├── pipeline_agent/
│   ├── agent.py                 # root_agent definition + instructions
│   ├── tools.py                 # ingest / process / report / store tools
│   └── report_template.html     # Chart.js report template
├── requirements.txt
└── .env.example
```

## Setup

**Prerequisites:** Python 3.10+, a Google account, and (for cloud storage) the [gcloud CLI](https://cloud.google.com/sdk/docs/install) and a GCP project.

```bash
git clone <this-repo-url>
cd adk-earthquake-pipeline
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
```

### 1. Gemini API key (required)
Create a free key at <https://aistudio.google.com/apikey>. Paste it into `.env` as `GOOGLE_API_KEY`.

### 2. Google Cloud Storage (for the storage step)
```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
gcloud storage buckets create gs://YOUR_BUCKET_NAME --location=us-central1
```
Set `GCS_BUCKET=YOUR_BUCKET_NAME` in `.env`.

> **No GCP project?** Set `STORAGE_BACKEND=local` in `.env`. Everything else runs the same, and the files stay in `./output/`.

## Run

**Option A: command line**
```bash
python main.py
# or give the agent a custom request:
python main.py "Analyze the last 14 days of earthquakes magnitude 4.5 and above"
```
The console shows each tool call the agent makes, then a run summary. The HTML report opens in your browser automatically.

**Option B: ADK web UI** (chat with the agent and inspect each tool call and event)
```bash
adk web
```
Open <http://localhost:8000>, select `pipeline_agent`, and type a request such as *"run the pipeline for the last 7 days"*.

## Output

Each run writes to `output/<run_id>/` and to `gs://<bucket>/earthquake-pipeline/<run_id>/`:

| File | Folder in bucket | Contents |
|---|---|---|
| `raw_earthquakes.geojson` | `raw/` | Untouched API response |
| `clean_earthquakes.csv` | `processed/` | Filtered, flattened event records |
| `summary.json` | `processed/` | Aggregates: by region, by day, by magnitude band, top 10 |
| `report.html` | `report/` | Visualization page |

To check the upload: `gcloud storage ls -r gs://YOUR_BUCKET_NAME/earthquake-pipeline/`

## Security note
No credentials are committed to this repo. `.env` is git-ignored, and GCS uses Application Default Credentials from `gcloud auth application-default login`.
