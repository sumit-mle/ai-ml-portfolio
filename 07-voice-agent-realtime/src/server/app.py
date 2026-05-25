"""FastAPI app + WebSocket bridge for the voice helpdesk."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..audit import AuditLog
from ..config import get_settings
from .realtime_bridge import handle_browser_connection

logger = logging.getLogger(__name__)


def build_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Acme IT Helpdesk Voice Agent", version="0.1.0")
    audit = AuditLog(s.audit_log_path)

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def root() -> FileResponse:
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.websocket("/ws/voice")
    async def ws_voice(ws: WebSocket) -> None:
        await ws.accept()
        await handle_browser_connection(ws, audit)

    return app


def run() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "src.server.app:build_app",
        factory=True,
        host=s.host,
        port=s.port,
        log_level="info",
    )
