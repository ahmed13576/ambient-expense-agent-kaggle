# submission_frontend

Standalone FastAPI manager dashboard for the Ambient Expense agent.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/` | Interactive dashboard UI |
| GET    | `/api/pending` | List pending approvals |
| POST   | `/api/action/{session_id}` | Resume a paused session |
| GET    | `/api/compliance/{session_id}` | Retrieve final compliance review |

## Configuration

| Env var | Required | Description |
|---------|----------|-------------|
| `GCP_PROJECT` | Yes (for live calls) | GCP project hosting Agent Runtime |
| `GCP_LOCATION` | No | Defaults to `global` |
| `AGENT_RUNTIME_ID` | Yes (for live calls) | ID of the deployed agent runtime |

## Run locally

```bash
uv sync
uv run uvicorn submission_frontend.main:app --host 127.0.0.1 --port 9000
```
