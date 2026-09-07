# app.py
# FastAPI + a single websocket endpoint. The socket carries the client commands
# in ({"cmd":"run",...} / {"cmd":"kill"} / {"cmd":"plan",...} / {"cmd":"execute",...})
# and the backend events out (node / link / log / status / plan / graph). All the
# actual behaviour lives in agents.py.

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse


def _load_dotenv():
    """Minimal, dependency-free .env loader so ANTHROPIC_API_KEY (and friends)
    can live in backend/.env instead of the shell. Real env vars win; the file
    is git-ignored so the key never gets committed."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)   # don't override an already-set env var


_load_dotenv()

from scenarios import SCENARIOS
from agents import Session, run_case, run_plan, run_executed_plan

app = FastAPI(title="agent-ops-sandbox")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/")
async def index():
    return FileResponse(FRONTEND / "index.html")


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()

    # per-connection state: the current case task + its session
    cur = {"session": None, "task": None}

    async def send(event):
        await sock.send_text(json.dumps(event))

    async def cancel_current():
        s, t = cur["session"], cur["task"]
        if s:
            await s.cancel_all()      # cascade to orchestrator + worker tasks
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    def _cfg(msg):
        return SCENARIOS.get(msg.get("scenario", "campaign"), SCENARIOS["campaign"])

    try:
        while True:
            msg = json.loads(await sock.receive_text())
            cmd = msg.get("cmd")

            if cmd == "run":
                await cancel_current()
                mode = msg.get("mode", "orchestrator")
                session = Session(send, _cfg(msg), msg.get("scenario", "campaign"), mode)
                cur["session"] = session
                cur["task"] = asyncio.create_task(run_case(session))

            elif cmd == "kill":
                if cur["session"]:
                    # target = a specific step id (failure gallery); omit to kill the coordinator
                    await cur["session"].kill(msg.get("target"))

            elif cmd == "plan":
                await cancel_current()
                goal = (msg.get("goal") or "").strip()
                session = Session(send, _cfg(msg), msg.get("scenario", "campaign"), "plan")
                cur["session"] = session
                cur["task"] = asyncio.create_task(run_plan(session, goal))

            elif cmd == "execute":
                # Option C — compose the plan, then EXECUTE it under a topology,
                # with the kill switch live, on the model's own DAG.
                await cancel_current()
                goal = (msg.get("goal") or "").strip()
                mode = msg.get("mode", "mesh")
                session = Session(send, _cfg(msg), msg.get("scenario", "campaign"), mode)
                cur["session"] = session
                cur["task"] = asyncio.create_task(run_executed_plan(session, goal))

    except WebSocketDisconnect:
        await cancel_current()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
