import tracing

# Must run before Strands is imported (it reads the setting when its tracer is first
# created). Without it, Agent Manager's Traces view shows no prompt or reply text and
# no tool input/output: see tracing.py.
tracing.configure()

import logging
import threading
import traceback
import uuid
from collections import OrderedDict
from importlib import metadata
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from strands import Agent

from agent import build_agent
from config import Config

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("banking-agent")

CONFIG = Config.from_env()


def _pkg_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not installed"


# Shows in the runtime log which dependency set this build actually shipped, so a
# stale image or an unpinned build is obvious without opening the build logs.
log.info(
    "banking agent starting: agent_version=%s llm_route=%s strands-agents=%s mcp=%s wrapt=%s",
    CONFIG.agent_version,
    CONFIG.llm_route,
    _pkg_version("strands-agents"),
    _pkg_version("mcp"),
    _pkg_version("wrapt"),
)

# Demo-grade session store: one Agent (and its history) per caller-supplied
# session_id, oldest evicted first. In production, key on the authenticated
# subject and persist history outside the process.
MAX_SESSIONS = 500
SESSIONS: "OrderedDict[str, tuple[Agent, threading.Lock]]" = OrderedDict()
_sessions_lock = threading.Lock()


class ChatRequest(BaseModel):
    message: str = Field(max_length=4000)
    session_id: str | None = Field(default=None, max_length=128)
    context: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    agent_version: str


app = FastAPI(title="Banking Assistant Agent", version="1.0.0")


def _session(session_id: str) -> tuple[Agent, threading.Lock]:
    # The endpoint runs on a threadpool, so lookup, insert and eviction share one lock.
    with _sessions_lock:
        entry = SESSIONS.get(session_id)
        if entry is None:
            entry = (build_agent(CONFIG), threading.Lock())
            SESSIONS[session_id] = entry
            while len(SESSIONS) > MAX_SESSIONS:
                SESSIONS.popitem(last=False)
        else:
            SESSIONS.move_to_end(session_id)
        return entry


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "bank": CONFIG.bank_name,
        "agent_version": CONFIG.agent_version,
        # direct-bedrock, or agent-manager-gateway when model calls go through an
        # Agent Manager LLM provider (the only route its guardrails can govern).
        "llm_route": CONFIG.llm_route,
    }


def _forget_unanswered_message(agent: Agent, last_before: Any, message: str) -> None:
    """Drop the user message of a turn that failed before the model answered it.

    Strands appends the message to the history before it calls the model, and keeps
    it there when the call fails. Left in place it is sent again with every later
    message of the session, so one rejected message would make every later message
    of that session fail too. This applies to any failed model call, whatever the
    reason.

    Nothing else is removed: once a tool has run in the turn, the history ends with
    its result, which is the only record of what the tool did.
    """
    messages = agent.messages
    if not messages or messages[-1] is last_before:
        return  # the turn added nothing
    last = messages[-1]
    # Compare role and content only: Strands also stores its own bookkeeping fields.
    if last.get("role") == "user" and last.get("content") == [{"text": message}]:
        messages.pop()


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")

    # A caller that omits session_id gets a fresh one, so each such request is
    # its own conversation.
    session_id = req.session_id or str(uuid.uuid4())
    agent, agent_lock = _session(session_id)

    try:
        # A Strands Agent cannot run two turns at once; serialise per session.
        with agent_lock:
            last_before = agent.messages[-1] if agent.messages else None
            try:
                result = agent(req.message)
            except Exception:
                _forget_unanswered_message(agent, last_before, req.message)
                raise
    except Exception as exc:
        # Upstream errors can carry provider URLs and payloads: log, never return.
        log.error(
            "agent invocation failed: session_id=%a error=%a traceback=%a",
            session_id,
            f"{type(exc).__name__}: {exc}",
            traceback.format_exc(),
        )
        raise HTTPException(status_code=500, detail="agent invocation failed") from exc

    return ChatResponse(
        response=str(result),
        session_id=session_id,
        agent_version=CONFIG.agent_version,
    )
