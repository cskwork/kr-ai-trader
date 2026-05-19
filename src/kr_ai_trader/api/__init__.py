"""FastAPI 백엔드 — Tauri 데스크톱 앱이 호출하는 REST + WebSocket 엔드포인트."""

from .server import app

__all__ = ["app"]
