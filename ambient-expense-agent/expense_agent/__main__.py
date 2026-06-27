"""Entry-point for python -m expense_agent.server."""
from expense_agent.server import server
import uvicorn

uvicorn.run(server, host="0.0.0.0", port=8080, reload=False)
