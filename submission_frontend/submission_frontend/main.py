"""Submission frontend service.

Provides a FastAPI application exposing:
* GET  /                       -> interactive manager dashboard UI
* GET  /api/pending            -> list pending approval sessions
* POST /api/action/{session_id} -> resume a paused session on Agent Runtime
* GET  /api/compliance/{session_id} -> fetch the final compliance review for a session

The service is intentionally self‑contained – it can be run locally for demos
without any external infrastructure.  When deployed it connects to the
Vertex AI Agent Runtime using environment variables ``GCP_PROJECT``,
``GCP_LOCATION`` and ``AGENT_RUNTIME_ID``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GCP_PROJECT: str = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or ""
GCP_LOCATION: str = os.getenv("GCP_LOCATION") or "global"
AGENT_RUNTIME_ID: str = os.getenv("AGENT_RUNTIME_ID", "")

# Parse AGENT_RUNTIME_ID if it is passed as a full resource name
if AGENT_RUNTIME_ID.startswith("projects/"):
    parts = AGENT_RUNTIME_ID.split("/")
    if len(parts) >= 6:
        if not GCP_PROJECT:
            GCP_PROJECT = parts[1]
        if parts[2] == "locations":
            GCP_LOCATION = parts[3]
        AGENT_RUNTIME_ID = parts[-1]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
)
log = logging.getLogger("submission_frontend")

# ---------------------------------------------------------------------------
# ADK / Vertex AI session service
# ---------------------------------------------------------------------------
try:
    from google.adk.sessions import VertexAiSessionService  # type: ignore

    _session_service_cls = VertexAiSessionService
except Exception as exc:  # pragma: no cover – optional dependency
    log.warning("VertexAiSessionService not available: %s", exc)
    _session_service_cls = None

try:
    from google.cloud import aiplatform  # type: ignore

    _aiplatform_available = True
except Exception as exc:  # pragma: no cover – optional dependency
    log.warning("google.cloud.aiplatform not available: %s", exc)
    _aiplatform_available = False


def _get_session_service():
    """Instantiate a Vertex AI session service.

    Returns ``None`` if the required libraries or configuration are missing.
    """
    if _session_service_cls is None:
        return None
    if not (GCP_PROJECT and AGENT_RUNTIME_ID):
        log.warning("Session service disabled – missing GCP_PROJECT or AGENT_RUNTIME_ID")
        return None
    try:
        return _session_service_cls(
            project=GCP_PROJECT,
            location=GCP_LOCATION,
            agent_runtime_id=AGENT_RUNTIME_ID,
        )
    except Exception as exc:  # pragma: no cover – runtime specific
        log.error("Failed to instantiate VertexAiSessionService: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def _collect_pending(session_service) -> List[Dict[str, Any]]:
    """Return a list of pending approvals.

    Each entry contains the session id, interrupt id and the underlying expense
    payload extracted from session state.
    """
    if session_service is None:
        return []

    try:
        sessions = asyncio.run(session_service.list_sessions(app_name="expense_agent"))
    except Exception as exc:
        log.error("list_sessions failed: %s", exc)
        return []

    pending: List[Dict[str, Any]] = []
    for sess in sessions:
        # Retrieve full session including events
        try:
            full = asyncio.run(
                session_service.get_session(
                    app_name="expense_agent",
                    user_id="default-user",
                    session_id=sess.id,
                )
            )
        except Exception as exc:
            log.warning("get_session(%s) failed: %s", sess.id, exc)
            continue

        events = getattr(full, "events", []) or []
        # Gather ids of function responses already supplied
        responded_ids = {
            ev.function_response.id
            for ev in events
            if getattr(ev, "function_response", None)
            and ev.function_response.name == "adk_request_input"
        }
        # Find function_call events that have no matching response
        for ev in events:
            fc = getattr(ev, "function_call", None)
            if fc and fc.name == "adk_request_input" and fc.id not in responded_ids:
                expense = (full.state or {}).get("expense", {})
                pending.append(
                    {
                        "session_id": sess.id,
                        "interrupt_id": fc.id,
                        "expense": expense,
                        "risk_analysis": (full.state or {}).get("risk_analysis", ""),
                    }
                )
                break  # one pending per session is sufficient
    return pending


def _fetch_compliance_review(session_service, session_id: str) -> Optional[str]:
    """Return the last assistant message containing a compliance review."""
    if session_service is None:
        return None
    try:
        full = asyncio.run(
            session_service.get_session(
                app_name="expense_agent",
                user_id="default-user",
                session_id=session_id,
            )
        )
    except Exception as exc:
        log.warning("get_session for compliance failed: %s", exc)
        return None

    for ev in reversed(full.events or []):
        content = getattr(ev, "content", None)
        if not content or not getattr(content, "parts", None):
            continue
        for part in content.parts:
            text = getattr(part, "text", None)
            if text and "risk" in text.lower():
                return text
    return None


def _resume_session(session_id: str, interrupt_id: str, approved: bool) -> Dict[str, Any]:
    """Resume a paused session on Agent Runtime.

    The payload is built exactly as the ADK SDK expects and is passed directly
    as the ``message`` argument. ``user_id`` is forced to ``"default-user"`` to
    avoid ownership mismatches.
    """
    if not (GCP_PROJECT and AGENT_RUNTIME_ID):
        raise RuntimeError("GCP_PROJECT and AGENT_RUNTIME_ID must be set to resume sessions")

    payload: Dict[str, Any] = {
        "role": "user",
        "parts": [
            {
                "function_response": {
                    "id": interrupt_id,
                    "name": "adk_request_input",
                    "response": {"approved": approved},
                }
            }
        ],
    }

    if not _aiplatform_available:
        log.warning("aiplatform client not available – returning mock response")
        return {"mock": True, "session_id": session_id, "payload": payload}

    # Lazy import of the client to avoid heavy module load when not needed
    try:
        from google.cloud.aiplatform_v1beta1.services.agent_service import (
            AgentServiceClient,
        )
        client = AgentServiceClient()
    except Exception as exc:
        log.error("Failed to construct AgentServiceClient: %s", exc)
        raise

    name = (
        f"projects/{GCP_PROJECT}/locations/{GCP_LOCATION}"
        f"/agents/{AGENT_RUNTIME_ID}/sessions/{session_id}"
    )
    request = {"name": name, "message": payload}
    try:
        response = client.resume_session(request=request)
        return {"name": response.name}
    except Exception as exc:  # pragma: no cover – depends on backend
        log.error("resume_session failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class ActionRequest(BaseModel):
    interrupt_id: str
    approved: bool


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(title="Submission Frontend", version="0.1.0")


# ---------------------------------------------------------------------------
# HTML / CSS / JS
# ---------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Ambient Expense — Manager Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0b0d12;
  --bg-elev:#11141b;
  --card:rgba(255,255,255,0.06);
  --card-border:rgba(255,255,255,0.14);
  --text:#e7e9ee;
  --muted:#8a93a6;
  --accent:#7b9cff;
  --accent-2:#a06bff;
  --success:#22c55e;
  --danger:#ef4444;
  --radius:16px;
  --shadow:0 10px 40px rgba(0,0,0,0.35);
}
*{box-sizing:border-box}
html,body{margin:0;height:100%;font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);overflow-x:hidden}
body{
  background:
    radial-gradient(800px 500px at 12% 18%, rgba(123,156,255,0.25), transparent 60%),
    radial-gradient(600px 400px at 88% 10%, rgba(160,107,255,0.20), transparent 60%),
    radial-gradient(700px 500px at 50% 95%, rgba(34,197,94,0.12), transparent 60%),
    var(--bg);
}
header{
  padding:48px 32px 24px;
  display:flex;align-items:center;justify-content:space-between;
}
header h1{font-size:28px;font-weight:600;margin:0;letter-spacing:-0.02em}
header .badge{
  background:var(--card);
  border:1px solid var(--card-border);
  padding:6px 12px;border-radius:999px;font-size:12px;color:var(--muted);
}
main{
  max-width:1200px;margin:0 auto;padding:0 32px 64px;
}
.section-title{
  font-size:14px;font-weight:600;letter-spacing:0.16em;text-transform:uppercase;
  color:var(--muted);margin:32px 0 16px;
}
.grid{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:24px;
}
.card{
  background:var(--card);
  border:1px solid var(--card-border);
  border-radius:var(--radius);
  padding:24px;
  backdrop-filter:blur(14px) saturate(160%);
  -webkit-backdrop-filter:blur(14px) saturate(160%);
  box-shadow:var(--shadow);
  transition:transform .25s ease, box-shadow .25s ease, border-color .25s ease;
}
.card:hover{transform:translateY(-2px);border-color:rgba(255,255,255,0.25)}
.card h3{margin:0 0 4px;font-size:18px;font-weight:600}
.card .amount{font-size:28px;font-weight:700;letter-spacing:-0.01em;color:#fff}
.card .meta{color:var(--muted);font-size:13px;margin-bottom:12px}
.card .desc{font-size:14px;line-height:1.5;margin:12px 0 18px;color:#cdd2dc}
.card .actions{display:flex;gap:12px}
.btn{
  flex:1;display:inline-flex;align-items:center;justify-content:center;gap:8px;
  padding:12px 16px;border-radius:12px;font-weight:600;font-size:14px;cursor:pointer;
  border:none;transition:transform .15s ease, background .25s ease, box-shadow .25s ease;
  color:#fff;
}
.btn:disabled{opacity:.6;cursor:not-allowed}
.btn-approve{background:linear-gradient(135deg,#22c55e,#15803d)}
.btn-approve:hover:not(:disabled){box-shadow:0 8px 24px rgba(34,197,94,0.45);transform:translateY(-1px)}
.btn-reject{background:linear-gradient(135deg,#ef4444,#991b1b)}
.btn-reject:hover:not(:disabled){box-shadow:0 8px 24px rgba(239,68,68,0.45);transform:translateY(-1px)}
.spinner{
  width:14px;height:14px;border:2px solid rgba(255,255,255,0.4);
  border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite;
}
@keyframes spin{to{transform:rotate(360deg)}}
.empty{
  border:1px dashed var(--card-border);border-radius:var(--radius);
  padding:48px;text-align:center;color:var(--muted);
}
/* Modal */
.modal{
  position:fixed;top:0;right:0;height:100%;width:420px;max-width:90vw;
  background:var(--bg-elev);
  box-shadow:-20px 0 60px rgba(0,0,0,0.5);
  transform:translateX(100%);transition:transform .35s cubic-bezier(.2,.8,.2,1);
  display:flex;flex-direction:column;border-left:1px solid var(--card-border);
}
.modal.open{transform:translateX(0)}
.modal header{padding:20px 24px;border-bottom:1px solid var(--card-border);display:flex;justify-content:space-between;align-items:center}
.modal h2{margin:0;font-size:18px;font-weight:600}
.modal .content{padding:24px;flex:1;overflow:auto;font-size:14px;line-height:1.6;white-space:pre-wrap}
.modal .close{background:none;border:none;color:var(--muted);cursor:pointer;font-size:20px}
</style>
</head>
<body>
<header>
  <h1>Manager Dashboard</h1>
  <span class="badge">Ambient Expense • Agent Runtime</span>
</header>
<main>
  <div class="section-title">Pending approvals</div>
  <div id="grid" class="grid"></div>
</main>

<div id="modal" class="modal" aria-hidden="true">
  <header>
    <h2>Compliance review</h2>
    <button class="close" onclick="closeModal()">×</button>
  </header>
  <div class="content" id="modalContent"></div>
</div>

<script>
async function loadPending(){
  const grid = document.getElementById('grid');
  grid.innerHTML = '<div class="empty">Loading…</div>';
  try{
    const res = await fetch('/api/pending');
    if(!res.ok) throw new Error('Failed to load');
    const items = await res.json();
    if(items.length === 0){
      grid.innerHTML = '<div class="empty">No pending approvals right now.</div>';
      return;
    }
    grid.innerHTML = '';
    items.forEach(item => {
      const e = item.expense || {};
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = `
        <h3>${e.submitter || 'Unknown submitter'}</h3>
        <div class="amount">$${Number(e.amount||0).toFixed(2)}</div>
        <div class="meta">${e.category||''} • ${e.date||''}</div>
        <div class="desc">${e.description||''}</div>
        ${item.risk_analysis ? `<div class="desc"><strong>Risk:</strong> ${item.risk_analysis}</div>` : ''}
        <div class="actions">
          <button class="btn btn-approve" onclick="act('${item.session_id}','${item.interrupt_id}',true,this)">Approve</button>
          <button class="btn btn-reject" onclick="act('${item.session_id}','${item.interrupt_id}',false,this)">Reject</button>
        </div>`;
      grid.appendChild(card);
    });
  }catch(err){
    grid.innerHTML = '<div class="empty">Unable to load pending items.</div>';
  }
}

async function act(sessionId, interruptId, approved, btn){
  const buttons = btn.parentElement.querySelectorAll('button');
  buttons.forEach(b => b.disabled = true);
  const original = btn.innerHTML;
  btn.innerHTML = '<span class="spinner"></span> Working…';
  try{
    const res = await fetch(`/api/action/${sessionId}`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({interrupt_id: interruptId, approved})
    });
    if(!res.ok) throw new Error('Action failed');
    await loadPending();
    const c = await fetch(`/api/compliance/${sessionId}`);
    const data = await c.json();
    openModal(data.review || 'No review available.');
  }catch(err){
    alert('Action failed: ' + err.message);
  }finally{
    buttons.forEach(b => b.disabled = false);
    btn.innerHTML = original;
  }
}

function openModal(text){
  document.getElementById('modalContent').innerText = text;
  document.getElementById('modal').classList.add('open');
}
function closeModal(){
  document.getElementById('modal').classList.remove('open');
}

document.addEventListener('DOMContentLoaded', loadPending);
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root() -> HTMLResponse:
    return HTMLResponse(HTML_TEMPLATE)


@app.get("/api/pending")
async def api_pending() -> JSONResponse:
    svc = _get_session_service()
    pending = await asyncio.to_thread(_collect_pending, svc)
    return JSONResponse(pending)


@app.get("/api/compliance/{session_id}")
async def api_compliance(session_id: str) -> JSONResponse:
    svc = _get_session_service()
    review = await asyncio.to_thread(_fetch_compliance_review, svc, session_id)
    return JSONResponse({"review": review or "No review recorded."})


@app.post("/api/action/{session_id}")
async def api_action(session_id: str, payload: ActionRequest) -> JSONResponse:
    try:
        result = await asyncio.to_thread(
            _resume_session, session_id, payload.interrupt_id, payload.approved
        )
    except Exception as exc:
        log.error("Resume failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return JSONResponse(result)
