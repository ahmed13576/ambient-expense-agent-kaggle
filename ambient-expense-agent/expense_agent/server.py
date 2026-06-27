# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Ambient Expense Approval Server.

Accepts Pub/Sub push messages and runs each one through the ADK workflow.

Endpoints:
  POST /push            - Pub/Sub push receiver (main entry point)
  POST /approve/{sid}   - Resume a workflow paused for human approval
  GET  /health          - Liveness check
  GET  /sessions        - List pending (HITL-paused) sessions

Pub/Sub push envelope format:
  {
    "subscription": "projects/<proj>/subscriptions/<name>",
    "message": {
      "data": "<base64-encoded JSON expense>",
      "messageId": "...",
      "publishTime": "..."
    }
  }

The subscription path is normalised to its short name (e.g.
"projects/foo/subscriptions/expense-sub" -> "expense-sub") so session
IDs stay short and readable.
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from expense_agent.agent import root_agent

load_dotenv()

# ---------------------------------------------------------------------------
# Logging — plain console, no Cloud sink (otel_to_cloud=False)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ADK runner (in-process, in-memory sessions for local/dev use)
# ---------------------------------------------------------------------------
APP_NAME = "expense_agent"
_session_service = InMemorySessionService()
_runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=_session_service,
)

# Tracks sessions that are paused at the human-approval gate:
# { session_id: { "fc_id": str, "message": str } }
_pending_approvals: dict[str, dict[str, Any]] = {}

# Internal ADK name for the RequestInput function-call hook
_REQUEST_INPUT_FC = "adk_request_input"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_subscription(subscription: str) -> str:
    """Strip the fully-qualified Pub/Sub prefix to a short, readable name.

    'projects/my-proj/subscriptions/expense-approvals' -> 'expense-approvals'
    """
    return subscription.rsplit("/", 1)[-1] if "/" in subscription else subscription


def _find_hitl_event(events: list) -> tuple[str | None, str | None]:
    """Return (function_call_id, interrupt_message) for the HITL pause event, or (None, None)."""
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.function_call and part.function_call.name == _REQUEST_INPUT_FC:
                    fc_id = part.function_call.id
                    args = part.function_call.args or {}
                    message = args.get("message", "Approval required.")
                    return fc_id, message
    return None, None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    logger.info("Expense Approval ambient server starting (port 8080)")
    yield
    logger.info("Expense Approval ambient server stopped")


server = FastAPI(
    title="Ambient Expense Approval",
    description="Pub/Sub push receiver that feeds expense events into an ADK workflow.",
    version="0.1.0",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@server.get("/health")
async def health():
    """Liveness probe."""
    return {"status": "ok", "pending_approvals": len(_pending_approvals)}


@server.get("/sessions")
async def list_pending():
    """Return sessions currently paused for human approval."""
    return {
        "pending": [
            {"session_id": sid, "message": info["message"]}
            for sid, info in _pending_approvals.items()
        ]
    }


@server.post("/push")
async def pubsub_push(request: Request):
    """Accept a Pub/Sub push message and run the expense-approval workflow.

    Returns 200 for auto-approved expenses, 202 when waiting for a human.
    """
    body = await request.json()

    # --- normalise subscription name ---
    raw_subscription = body.get("subscription", "local")
    sub_name = _normalize_subscription(raw_subscription)

    # --- extract message ID for a stable, idempotent session key ---
    message_block = body.get("message", {})
    message_id = message_block.get("messageId") or message_block.get("message_id", "0")

    # Session ID: short-name + message-id so replays are idempotent
    session_id = f"{sub_name}-{message_id}"
    user_id = sub_name

    logger.info("Received Pub/Sub message | sub=%s message_id=%s session=%s", sub_name, message_id, session_id)

    # --- create session (ignore if already exists for idempotency) ---
    try:
        _session_service.create_session_sync(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception:
        logger.debug("Session %s already exists, reusing", session_id)

    # Pass the full Pub/Sub envelope — parse_expense_event handles
    # base64-encoded data and plain-JSON data fields alike.
    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=json.dumps(body))],
    )

    events: list = []
    async for event in _runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    ):
        events.append(event)

    # --- check whether the workflow paused for human input ---
    fc_id, hitl_message = _find_hitl_event(events)
    if fc_id:
        _pending_approvals[session_id] = {"fc_id": fc_id, "message": hitl_message, "user_id": user_id}
        logger.info("Workflow paused for human approval | session=%s", session_id)
        return JSONResponse(
            status_code=202,
            content={
                "status": "pending_human_approval",
                "session_id": session_id,
                "message": hitl_message,
                "approve_url": f"/approve/{session_id}",
            },
        )

    # --- auto-approved path: find the output event ---
    output_events = [e for e in events if e.output is not None]
    final_output = output_events[-1].output if output_events else {}
    logger.info("Workflow completed | session=%s output=%s", session_id, final_output)
    return JSONResponse(
        status_code=200,
        content={"status": "completed", "session_id": session_id, "output": final_output},
    )


@server.post("/approve/{session_id}")
async def approve_expense(session_id: str, request: Request):
    """Resume a workflow paused at the human-approval gate.

    Body: { "decision": "approve" | "reject" }
    """
    if session_id not in _pending_approvals:
        raise HTTPException(
            status_code=404,
            detail=f"No pending approval found for session '{session_id}'. "
                   "Check GET /sessions for active ones.",
        )

    body = await request.json()
    decision: str = str(body.get("decision", "reject")).strip().lower()
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")

    approval_info = _pending_approvals.pop(session_id)
    fc_id = approval_info["fc_id"]
    user_id = approval_info["user_id"]  # stored at pause time, no string-splitting needed

    logger.info("Human decision='%s' | session=%s", decision, session_id)

    # Resume: send a FunctionResponse matching the RequestInput's function_call id
    resume_content = types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=fc_id,
                    name=_REQUEST_INPUT_FC,
                    response={"result": decision},
                )
            )
        ],
    )

    resume_events: list = []
    async for event in _runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=resume_content,
    ):
        resume_events.append(event)

    output_events = [e for e in resume_events if e.output is not None]
    final_output = output_events[-1].output if output_events else {}
    logger.info("Workflow resumed and completed | session=%s output=%s", session_id, final_output)
    return JSONResponse(
        status_code=200,
        content={"status": "completed", "session_id": session_id, "output": final_output},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "expense_agent.server:server",
        host="0.0.0.0",
        port=8080,
        reload=False,
    )
