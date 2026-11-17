"""ADK agent that orchestrates the earthquake data pipeline.

The root agent is an LLM-driven orchestrator. It reads the user's request,
decides the parameters (time window, magnitude threshold), calls each
pipeline tool in order, checks each result, and stops with a clear message
if a step fails.
"""

import os

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types

from .tools import build_report, fetch_earthquakes, process_earthquakes, store_to_cloud

INSTRUCTION = """
You are a data engineering agent that runs an earthquake analytics pipeline.

Run these stages in order, one tool call at a time:
1. fetch_earthquakes  - ingest data from the USGS API.
   Infer `days` (1-30) and `min_magnitude` from the user's request.
   Defaults if not specified: days=7, min_magnitude=2.5.
2. process_earthquakes - clean, filter, and aggregate the raw data.
3. build_report        - generate the HTML visualization.
4. store_to_cloud      - upload raw data, processed data, and the report to cloud storage.

Rules:
- After every tool call, check the "status" field before moving on.
- If a step returns status "error", do not continue. Explain what failed and
  suggest a fix (for example: set GCS_BUCKET, or run `gcloud auth application-default login`).
- A store_to_cloud status of "skipped" is fine; mention that files were kept locally.
- Never invent numbers. Only report figures returned by the tools.

When finished, reply with a short run summary:
- parameters used
- total events, max magnitude, and the top 3 regions
- the local report path
- the cloud storage URIs (or a note that storage was skipped)
"""

root_agent = Agent(
    name="earthquake_pipeline_agent",
    model=Gemini(
        model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        # Retry temporary Gemini errors (429 rate limit, 503 high demand) with backoff
        retry_options=types.HttpRetryOptions(attempts=5, initial_delay=2, max_delay=30,
                                             http_status_codes=[429, 500, 503]),
    ),
    description="Orchestrates ingest -> process -> visualize -> store for USGS earthquake data.",
    instruction=INSTRUCTION,
    tools=[fetch_earthquakes, process_earthquakes, build_report, store_to_cloud],
)
