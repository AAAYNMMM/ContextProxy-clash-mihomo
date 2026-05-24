import asyncio

from fastapi import FastAPI
from pydantic import BaseModel

from backend.batch_processor import get_request_queue
from backend.config import RECEIVER_PORT
from backend.activity_bus import emit_activity


app = FastAPI()
receiver_server = None


class Report(BaseModel):
    tabHost: str
    requestHost: str


@app.post("/report")
async def report_endpoint(data: Report):
    await get_request_queue().put(data)
    return {"ok": True}


@app.get("/health")
async def health_endpoint():
    return {"ok": True}


async def start_receiver():
    global receiver_server

    import uvicorn

    host = "127.0.0.1"
    port = RECEIVER_PORT
    emit_activity(f"Tab 上报接收器已启动：{host}:{port}", "INFO", key="receiver-started", ttl=5)

    config_uvicorn = uvicorn.Config(
        "backend.receiver:app",
        host=host,
        port=port,
        log_level="info",
    )
    receiver_server = uvicorn.Server(config_uvicorn)

    try:
        await receiver_server.serve()
    except asyncio.CancelledError:
        if receiver_server:
            receiver_server.should_exit = True
    finally:
        receiver_server = None


async def stop_receiver():
    global receiver_server

    if receiver_server:
        receiver_server.should_exit = True
        await asyncio.sleep(0)
