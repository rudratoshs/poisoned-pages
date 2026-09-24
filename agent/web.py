"""
Web chat for the Brightside support agent, with a live security trace.

    python web.py      # then open http://localhost:8000

One WebSocket per browser tab = one conversation. The server pushes every
knowledge-base read and guard decision as it happens, and waits for the
reviewer's click on "ask" decisions.
"""

import asyncio
import itertools
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


@app.get("/api/info")
async def info():
    """Structured limits from Sanity + the demo customer, for the sidebar."""
    orders = [{"id": oid, **o} for oid, o in backend.ORDERS.items() if o["customer"] == backend.CUSTOMER["id"]]
    return {"settings": STATE["settings"], "customer": backend.CUSTOMER, "orders": orders}


@app.websocket("/ws")
async def chat(ws: WebSocket):
    await ws.accept()
    mode = ws.query_params.get("mode", "kb")
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
            message = await ws.receive_json()
            if message["type"] == "approval" and message["id"] in pending:
                pending[message["id"]].set_result(bool(message["approved"]))
            elif message["type"] == "user" and (turn is None or turn.done()):
                turn = asyncio.create_task(_turn(agent, message["text"], push))
    except WebSocketDisconnect:
        if turn:
            turn.cancel()
        sending.cancel()


async def _turn(agent, text, push):
    try:
        push({"type": "reply", "text": await agent.send(text)})
    except Exception as e:  # show errors in the page instead of dropping the socket
        push({"type": "error", "text": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
