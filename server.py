"""AgentDirectMessage Relay Server — strict turn-based 1:1 agent collaboration."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

MAX_MESSAGE_BYTES = 65_536  # 64 KB
MAX_SESSIONS = 1000
IDLE_TIMEOUT_SECONDS = 1800  # 30 minutes
LONG_POLL_TIMEOUT = 20  # seconds


def _make_events() -> dict[str, asyncio.Event]:
    return {"A": asyncio.Event(), "B": asyncio.Event()}


@dataclass
class Session:
    token: str
    participants: dict[str, str] = field(default_factory=dict)      # role -> pid
    participant_roles: dict[str, str] = field(default_factory=dict)  # pid -> role
    # messages[role] = outbox of that role (written by role, read by the other)
    messages: dict[str, str | None] = field(default_factory=lambda: {"A": None, "B": None})
    # events[role] is set when role has something new to read
    events: dict[str, asyncio.Event] = field(default_factory=_make_events)
    closed: bool = False
    last_activity: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


sessions: dict[str, Session] = {}
participant_to_token: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _other_role(role: str) -> str:
    return "B" if role == "A" else "A"


def _lookup_session(participant_id: str) -> tuple[Session, str]:
    token = participant_to_token.get(participant_id)
    if token is None or token not in sessions:
        raise HTTPException(404, detail="Unknown participant")
    session = sessions[token]
    role = session.participant_roles.get(participant_id)
    if role is None:
        raise HTTPException(404, detail="Unknown participant")
    return session, role


def _destroy_session(token: str) -> None:
    session = sessions.pop(token, None)
    if session is None:
        return
    for pid in list(session.participant_roles):
        participant_to_token.pop(pid, None)


# ---------------------------------------------------------------------------
# Background cleanup
# ---------------------------------------------------------------------------


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired = [
            t for t, s in sessions.items() if now - s.last_activity > IDLE_TIMEOUT_SECONDS
        ]
        for token in expired:
            session = sessions.get(token)
            if session:
                async with session.lock:
                    session.closed = True
                    session.events["A"].set()
                    session.events["B"].set()
            _destroy_session(token)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_cleanup_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="AgentDirectMessage Relay", lifespan=lifespan)


# ---------------------------------------------------------------------------
# POST /check — join the token channel and check for a pending message
#
# - If the token is new: creates the session, registers caller as A.
# - If the token exists with one participant: registers caller as B.
# - Consumes the other side's pending message if any.
#
# Returns: {participant_id, role, message: string | null}
# ---------------------------------------------------------------------------

class CheckBody(BaseModel):
    token: str


@app.post("/check")
async def check(body: CheckBody):
    token = body.token

    if token not in sessions:
        # New session → participant A
        if len(sessions) >= MAX_SESSIONS:
            raise HTTPException(503, detail="Too many active sessions")
        session = Session(token=token)
        pid = uuid.uuid4().hex
        session.participants["A"] = pid
        session.participant_roles[pid] = "A"
        sessions[token] = session
        participant_to_token[pid] = token
        return {"participant_id": pid, "role": "A", "message": None}

    session = sessions[token]
    if session.closed:
        raise HTTPException(410, detail="Session closed")

    if "B" not in session.participants:
        # Second participant → B
        pid = uuid.uuid4().hex
        session.participants["B"] = pid
        session.participant_roles[pid] = "B"
        participant_to_token[pid] = token
        role = "B"
    else:
        raise HTTPException(409, detail="Session full")

    other = _other_role(role)
    message = None

    async with session.lock:
        if session.messages[other] is not None:
            message = session.messages[other]
            session.messages[other] = None
            session.events[role].clear()
        session.last_activity = time.time()

    return {"participant_id": pid, "role": role, "message": message}


# ---------------------------------------------------------------------------
# POST /send — deposit a message
#
# Requires X-Participant-ID from a prior /check call.
# Deposits the message into the caller's outbox slot.
# ---------------------------------------------------------------------------

class SendBody(BaseModel):
    message: str


@app.post("/send")
async def send(body: SendBody, x_participant_id: str = Header(...)):
    if len(body.message.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise HTTPException(413, detail="Message too large (64 KB max)")

    session, role = _lookup_session(x_participant_id)
    other = _other_role(role)

    async with session.lock:
        if session.closed:
            raise HTTPException(410, detail="Session closed")
        if session.messages[role] is not None:
            raise HTTPException(409, detail="Previous message not yet consumed")
        session.messages[role] = body.message
        session.events[other].set()
        session.last_activity = time.time()

    return {"status": "sent"}


# ---------------------------------------------------------------------------
# GET /wait — long-poll for the other side's message
# ---------------------------------------------------------------------------

@app.get("/wait")
async def wait(x_participant_id: str = Header(...)):
    session, role = _lookup_session(x_participant_id)
    other = _other_role(role)

    # Immediate check
    async with session.lock:
        if session.closed:
            return {"status": "closed"}
        if session.messages[other] is not None:
            msg = session.messages[other]
            session.messages[other] = None
            session.events[role].clear()
            session.last_activity = time.time()
            return {"status": "message", "message": msg, "from": other}

    # Long-poll on my event
    try:
        await asyncio.wait_for(session.events[role].wait(), timeout=LONG_POLL_TIMEOUT)
    except asyncio.TimeoutError:
        return {"status": "timeout"}

    # Re-check after wake
    async with session.lock:
        if session.closed:
            return {"status": "closed"}
        if session.messages[other] is not None:
            msg = session.messages[other]
            session.messages[other] = None
            session.events[role].clear()
            session.last_activity = time.time()
            return {"status": "message", "message": msg, "from": other}

    return {"status": "timeout"}


# ---------------------------------------------------------------------------
# POST /exit — destroy session
# ---------------------------------------------------------------------------

@app.post("/exit")
async def exit_session(x_participant_id: str = Header(...)):
    session, _role = _lookup_session(x_participant_id)

    async with session.lock:
        session.closed = True
        session.events["A"].set()
        session.events["B"].set()

    _destroy_session(session.token)
    return {"status": "closed"}


@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(sessions)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("AGENT_DM_PORT", "11451"))
    uvicorn.run(app, host="0.0.0.0", port=port)
