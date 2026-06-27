import json
import os
import logging
from typing import Any, Dict, List

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# Paths
DATASET_PATH = os.path.join(os.path.dirname(__file__), "datasets", "basic-dataset.json")
TRACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "artifacts", "traces"))

# Helper to locate a HITL pause (function call) in the event stream
def find_hitl_event(events: List[Any]):
    for ev in events:
        if getattr(ev, "content", None):
            for part in ev.content.parts:
                if getattr(part, "function_call", None) and part.function_call.name == "adk_request_input":
                    return ev, part.function_call.id
    return None, None

def run_case(case: Dict[str, Any], runner: Runner, session_service: InMemorySessionService):
    case_id = case.get("eval_case_id")
    prompt = case.get("prompt")

    # Build the initial message
    message = types.Content(role=prompt["role"], parts=[types.Part.from_text(text=prompt["parts"][0]["text"])])

    # Create a fresh session for each case
    session = session_service.create_session_sync(user_id="test_user", app_name="expense_agent")

    # Run the workflow until completion (including possible HITL pause)
    events: List[Any] = []
    async def _run():
        async for ev in runner.run_async(new_message=message, user_id="test_user", session_id=session.id):
            events.append(ev)
    # Execute the async runner synchronously
    import asyncio
    asyncio.run(_run())

    # Check for a human approval pause
    hitl_event, fc_id = find_hitl_event(events)
    if hitl_event and fc_id:
        # Retrieve session state to decide automatically
        session_state = session_service.get_session_sync(app_name="expense_agent", user_id="test_user", session_id=session.id).state
        injection = session_state.get("injection_flagged", False)
        decision = "reject" if injection else "approve"
        # Build resume message
        resume_msg = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=fc_id,
                        name="adk_request_input",
                        response={"approve_decision": decision},
                    )
                )
            ],
        )
        # Run the resumed workflow
        async def _resume():
            async for ev in runner.run_async(new_message=resume_msg, user_id="test_user", session_id=session.id):
                events.append(ev)
        asyncio.run(_resume())

    # Return the collected events for this case
    return {"case_id": case_id, "events": events}

def main():
    logging.basicConfig(level=logging.INFO)
    os.makedirs(TRACE_DIR, exist_ok=True)
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    cases = dataset.get("eval_cases", [])
    # Initialise ADK runner and session service
    session_service = InMemorySessionService()
    runner = Runner(app_name="expense_agent", session_service=session_service)
    for case in cases:
        result = run_case(case, runner, session_service)
        trace_path = os.path.join(TRACE_DIR, f"{result['case_id']}.json")
        with open(trace_path, "w", encoding="utf-8") as tf:
            json.dump(result["events"], tf, default=str, indent=2)
        logging.info(f"Trace written: {trace_path}")

if __name__ == "__main__":
    main()
