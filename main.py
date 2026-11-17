"""Run the ADK earthquake pipeline agent from the command line.

Usage:
    python main.py
    python main.py "Analyze the last 3 days of earthquakes magnitude 4 and up"
"""

import asyncio
import sys
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)  # load .env before importing the agent (model name, bucket, API key)

from google.adk.runners import Runner  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402

from pipeline_agent.agent import root_agent  # noqa: E402

APP_NAME = "earthquake_pipeline"
USER_ID = "student"
DEFAULT_PROMPT = "Run the earthquake pipeline for the last 7 days, magnitude 2.5 and above."


async def main(prompt: str) -> None:
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)

    print(f"\n> {prompt}\n")
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    async for event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
        for call in event.get_function_calls():
            print(f"[agent] calling {call.name}({dict(call.args or {})})")
        for resp in event.get_function_responses():
            status = (resp.response or {}).get("status", "?")
            print(f"[tool]  {resp.name} -> {status}")
        if event.is_final_response() and event.content and event.content.parts:
            print("\n" + "".join(p.text or "" for p in event.content.parts))

    # Open the report locally if the run produced one
    final = await session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session.id)
    report = final.state.get("report_path")
    if report and Path(report).exists():
        print(f"\nReport: {Path(report).resolve()}")
        webbrowser.open(Path(report).resolve().as_uri())


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or DEFAULT_PROMPT))
