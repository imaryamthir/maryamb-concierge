"""
Branded web app for the Layla concierge
============================================================================
This serves a Maryam B.-branded chat site where Layla actually works, powered
by the SAME ADK multi-agent system defined in ../layla/agent.py (coordinator +
stylist + catalog (MCP) + order, with both security guardrails).

Why a backend at all?
  The Gemini API key must NEVER live in the browser — that would expose it to
  every visitor and breaks the competition's "no keys in code" rule. So the
  browser talks to THIS server, the server holds the key (loaded from .env),
  and only Layla's text replies are sent back to the page.

Run it:
    pip install -r requirements.txt
    cp .env.example .env          # then paste your GOOGLE_API_KEY into .env
    python -m webapp.server
    # open http://localhost:8000

If the MCP catalog subprocess ever misbehaves on your machine, you can always
fall back to ADK's own UI with `adk web .` — same agent, different shell.
============================================================================
"""

import inspect
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Load GOOGLE_API_KEY (and any Vertex settings) from .env before importing the
# agent, so the model client picks up credentials.
load_dotenv()

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from layla import root_agent

APP_NAME = "layla_web"
STATIC_DIR = Path(__file__).parent / "static"

# One Runner + session service for the whole app. The Runner orchestrates the
# coordinator and its sub-agents (including the MCP-backed catalog agent) and
# fires the guardrail callbacks on every turn.
session_service = InMemorySessionService()
runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)

app = FastAPI(title="Maryam B. — Layla concierge")

# Track which sessions we've already created so we don't recreate them.
_known_sessions: set[str] = set()


async def _maybe_await(value):
    """Await `value` if it's awaitable; otherwise return it. Keeps this code
    working across ADK versions where session methods may be sync or async."""
    if inspect.isawaitable(value):
        return await value
    return value


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None


@app.get("/")
async def index():
    """Serve the branded chat page."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    """Simple readiness check; also reports whether a key is configured."""
    has_key = bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"))
    return {"status": "ok", "model_configured": has_key}


@app.post("/chat")
async def chat(body: ChatIn):
    """Send one shopper message to Layla and return her reply."""
    user_id = "web-user"
    session_id = body.session_id or str(uuid.uuid4())

    # Lazily create the conversation session the first time we see its id.
    if session_id not in _known_sessions:
        await _maybe_await(
            session_service.create_session(
                app_name=APP_NAME, user_id=user_id, session_id=session_id
            )
        )
        _known_sessions.add(session_id)

    message = types.Content(role="user", parts=[types.Part(text=body.message)])

    reply_parts: list[str] = []
    try:
        # Stream the agent's events and collect the final response text.
        async for event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        reply_parts.append(part.text)
    except Exception as exc:  # surface errors as a friendly message, never a 500 page
        return JSONResponse(
            {"reply": f"Sorry — something went wrong on my side. ({exc})",
             "session_id": session_id},
            status_code=200,
        )

    reply = "\n".join(reply_parts).strip() or "…"
    return JSONResponse({"reply": reply, "session_id": session_id})


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
