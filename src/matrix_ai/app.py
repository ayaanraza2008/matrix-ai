"""FastAPI application factory.

Security posture:
* loopback binding by default;
* bearer auth on everything except /api/health and /api/auth/pair;
* restrictive security headers and optional explicit CORS origins;
* errors are returned as stable JSON codes, never stack traces.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.routes import router
from .config import MatrixConfig
from .errors import MatrixError
from .runtime import MatrixRuntime

log = logging.getLogger("matrix.app")
WEB_DIR = Path(__file__).parent / "web"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
}

UI_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


def create_app(
    config: MatrixConfig | None = None,
    *,
    runtime: MatrixRuntime | None = None,
    start_background: bool = True,
) -> FastAPI:
    runtime = runtime or MatrixRuntime.build(config)
    if not runtime.config.is_loopback():
        log.warning(
            "MATRIX is bound to %s (not loopback). Use private networking and TLS; "
            "the API is protected by bearer tokens only.",
            runtime.config.host,
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_background:
            await runtime.goal_supervisor.recover_cancellations()
            await runtime.orchestrator.recover_after_restart()
            await runtime.goal_supervisor.recover_after_restart()
            runtime.start_background()
        try:
            yield
        finally:
            await runtime.shutdown()

    app = FastAPI(
        title="MATRIX",
        description=(
            "Local-first personal AI assistant. Tool execution is policy-checked server-side; "
            "high-risk actions require exact owner approval."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.matrix = runtime

    if runtime.config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(runtime.config.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.exception_handler(MatrixError)
    async def matrix_error_handler(_request: Request, exc: MatrixError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
            headers={"X-MATRIX-Error": exc.code},
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        path = request.url.path
        if path == "/" or path.startswith("/assets"):
            response.headers.setdefault("Content-Security-Policy", UI_CSP)
        return response

    app.include_router(router)

    if WEB_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=str(WEB_DIR)), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/meta", include_in_schema=False)
    async def meta() -> dict:
        return {"name": "MATRIX", "version": __version__, "ui": WEB_DIR.is_dir()}

    return app
