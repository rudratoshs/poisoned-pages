"""
Web chat for the Brightside support agent, with a live security trace.

    python web.py      # then open http://localhost:8000

One WebSocket per browser tab = one conversation. The server pushes every
knowledge-base read and guard decision as it happens, and waits for the
reviewer's click on "ask" decisions.
"""

import asyncio
import itertools
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from brightside import backend
from brightside.agent import SupportAgent
from brightside.guard import build_policy, fetch_settings
from brightside.kb import KnowledgeBase
from brightside.raw import RawContent

MODELS = {"opus": "claude-opus-5", "haiku": "claude-haiku-4-5"}
STATE = {}

# Limits for a public demo: every turn costs real API money.
MAX_TURNS_PER_CHAT = int(os.environ.get("MAX_TURNS_PER_CHAT", 12))
MAX_MESSAGE_CHARS = int(os.environ.get("MAX_MESSAGE_CHARS", 1000))
MAX_TURNS_PER_IP_HOUR = int(os.environ.get("MAX_TURNS_PER_IP_HOUR", 30))
MAX_TURNS_PER_DAY = int(os.environ.get("MAX_TURNS_PER_DAY", 400))
MAX_CONCURRENT_CHATS = int(os.environ.get("MAX_CONCURRENT_CHATS", 20))
IDLE_TIMEOUT_SECONDS = int(os.environ.get("IDLE_TIMEOUT_SECONDS", 900))


class Limits:
    def __init__(self):
        self.open_chats = 0
        self.per_ip = defaultdict(deque)        # ip -> timestamps of recent turns
        self.day, self.day_turns = time.strftime("%Y-%m-%d"), 0

    def allow_turn(self, ip):
        """Returns None if the turn may run, otherwise a message for the user."""
        today = time.strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.day_turns = today, 0
        if self.day_turns >= MAX_TURNS_PER_DAY:
            return "The demo has reached today's usage limit. Please try again tomorrow."
        recent = self.per_ip[ip]
        while recent and recent[0] < time.time() - 3600:
            recent.popleft()
        if len(recent) >= MAX_TURNS_PER_IP_HOUR:
            return "You've reached the hourly limit for this demo. Please try again later."
        recent.append(time.time())
        self.day_turns += 1
        return None


LIMITS = Limits()


def client_ip(ws):
    forwarded = ws.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (ws.client.host if ws.client else "unknown")


@asynccontextmanager
async def lifespan(app):
    async with AsyncExitStack() as stack:
        STATE["settings"] = fetch_settings()
        STATE["policy"] = build_policy(STATE["settings"])
        STATE["sources"] = {
            "kb": await stack.enter_async_context(KnowledgeBase()),
            "raw": await stack.enter_async_context(RawContent()),
        }
        yield


app = FastAPI(lifespan=lifespan)
STATIC = Path(__file__).parent / "static"


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/api/info")
async def info():
    """Structured limits from Sanity + the demo customer, for the sidebar."""
    orders = [{"id": oid, **o} for oid, o in backend.ORDERS.items() if o["customer"] == backend.CUSTOMER["id"]]
    return {"settings": STATE["settings"], "customer": backend.CUSTOMER, "orders": orders}


@app.websocket("/ws")
async def chat(ws: WebSocket):
    await ws.accept()
    if LIMITS.open_chats >= MAX_CONCURRENT_CHATS:
        await ws.send_json({"type": "error", "text": "The demo is busy right now. Please try again in a few minutes."})
        await ws.close()
        return
    LIMITS.open_chats += 1
    ip, turns_used = client_ip(ws), 0
    mode = ws.query_params.get("mode", "kb")
    mode = mode if mode in STATE["sources"] else "kb"
    model = MODELS.get(ws.query_params.get("model", "opus"), MODELS["opus"])
    pending = {}                       # approval id -> Future[bool]
    ids = itertools.count(1)
    outbox = asyncio.Queue()           # one queue keeps trace events and replies in order

    async def sender():
        while True:
            await ws.send_json(await outbox.get())

    push = outbox.put_nowait
    sending = asyncio.create_task(sender())

    def on_event(event):
        push({"type": "event", "event": event})

    async def approve(decision, args):
        approval_id = next(ids)
        answer = pending[approval_id] = asyncio.get_running_loop().create_future()
        push({"type": "approval_request", "id": approval_id, "tool": decision.tool,
              "args": args, "reasons": decision.reasons})
        try:
            return await answer
        finally:
            pending.pop(approval_id, None)

    agent = SupportAgent(STATE["sources"][mode], STATE["policy"], approve, on_event, model=model)
    push({"type": "ready", "mode": mode, "model": model})
    turn = None
    try:
        while True:
            try:
                message = await asyncio.wait_for(ws.receive_json(), timeout=IDLE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                # Idle means nobody is doing anything: not while the agent is still working,
                # but yes while it's waiting on a human who has walked away.
                if turn and not turn.done() and not pending:
                    continue
                break
            kind = message.get("type") if isinstance(message, dict) else None
            if kind == "approval" and message.get("id") in pending and not pending[message["id"]].done():
                pending[message["id"]].set_result(bool(message.get("approved")))
            elif kind == "user" and (turn is None or turn.done()):
                text = str(message.get("text", "")).strip()
                refusal = (
                    "Please type a question." if not text else
                    f"Please keep messages under {MAX_MESSAGE_CHARS} characters." if len(text) > MAX_MESSAGE_CHARS else
                    f"This chat has reached its {MAX_TURNS_PER_CHAT}-message limit. Click New chat to start another."
                    if turns_used >= MAX_TURNS_PER_CHAT else LIMITS.allow_turn(ip))
                if refusal:
                    push({"type": "error", "text": refusal})
                    continue
                turns_used += 1
                turn = asyncio.create_task(_turn(agent, text, push))
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        LIMITS.open_chats -= 1
        for answer in pending.values():     # unblock a turn waiting on an approval
            if not answer.done():
                answer.set_result(False)
        if turn:
            turn.cancel()
        sending.cancel()
        try:
            await ws.close()
        except Exception:
            pass   # already closed by the client


async def _turn(agent, text, push):
    try:
        push({"type": "reply", "text": await agent.send(text)})
    except asyncio.CancelledError:
        raise
    except Exception:  # details go to the server log, not to a public visitor
        logging.exception("turn failed")
        push({"type": "error", "text": "Sorry, something went wrong on our side. Please try again."})


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))
