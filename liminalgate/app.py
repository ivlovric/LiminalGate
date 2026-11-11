"""FastAPI application: REST, WebSocket, and the built dashboard."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from liminalgate import __version__
from liminalgate.api import router
from liminalgate.errors import LiminalError
from liminalgate.hub import Hub
from liminalgate.settings import Settings, load_settings

_OPEN_API = {"/api/health"}


def _presented_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    custom = request.headers.get("x-liminal-token", "")
    if custom:
        return custom.strip()
    return request.query_params.get("token", "").strip()


def token_ok(settings: Settings, presented: str) -> bool:
    expected = settings.token
    if not expected:
        return True
    if not presented:
        return False
    return secrets.compare_digest(presented, expected)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub = Hub(app.state.settings)
        app.state.hub = hub
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()

    app = FastAPI(
        title="LiminalGate",
        version=__version__,
        description=(
            "Open, self-hosted neuro gateway for live EEG, custom bands, markers, and biofeedback. No telemetry."
        ),
        license_info={"name": "MIT"},
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.exception_handler(LiminalError)
    async def liminal_error(_request: Request, exc: LiminalError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @app.middleware("http")
    async def access_token(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api") and path not in _OPEN_API and not token_ok(settings, _presented_token(request)):
            return JSONResponse({"detail": "Missing or invalid access token"}, status_code=401)
        return await call_next(request)

    app.include_router(router)

    @app.websocket("/ws")
    async def websocket(socket: WebSocket) -> None:
        presented = socket.query_params.get("token", "")
        header = socket.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            presented = header[7:].strip()
        if not token_ok(settings, presented):
            await socket.close(code=1008)
            return
        await app.state.hub.handle_socket(socket)

    dist = Path(settings.static_dir) if settings.static_dir else _default_dist()
    if (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    else:

        @app.get("/", include_in_schema=False)
        def ui_missing() -> dict[str, str]:
            return {
                "service": "LiminalGate",
                "version": __version__,
                "ui": "frontend is not built",
                "docs": "/docs",
                "health": "/api/health",
            }

    return app


def _default_dist() -> Path:
    return Path(__file__).resolve().parent.parent / "frontend" / "dist"
