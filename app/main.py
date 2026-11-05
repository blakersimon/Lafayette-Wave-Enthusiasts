"""
app/main.py
FastAPI application entrypoint for Louisiana Surf Finder.
Configures lifespan startup/shutdown, non-blocking background cache warming,
static asset serving, Jinja2 HTML shell rendering, and API routers.
"""

from __future__ import annotations
import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import close_all_services, router as api_router, warm_live_cache_background
from app.config import get_settings
from app.services.cache_service import get_cache_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("louisiana_surf.main")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


async def _periodic_refresh_loop(interval_seconds: int = 900) -> None:
    """Background loop that refreshes live marine telemetry every 15 minutes."""
    # Initial non-blocking warm shortly after startup
    await asyncio.sleep(0.5)
    await warm_live_cache_background()
    while True:
        await asyncio.sleep(interval_seconds)
        await warm_live_cache_background()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    FastAPI lifespan manager:
    1. Synchronously loads static disk JSON fallbacks into memory (<10ms) so
       Railway /api/health probes pass immediately.
    2. Launches non-blocking background live telemetry refresher (unless disabled in unit tests).
    3. Closes all HTTPX clients cleanly on shutdown.
    """
    cache = get_cache_service()
    cache.load_disk_fallbacks()

    bg_task = None
    if os.environ.get("DISABLE_BG_WARMUP") != "1":
        bg_task = asyncio.create_task(_periodic_refresh_loop())

    try:
        yield
    finally:
        if bg_task is not None:
            bg_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bg_task
        await close_all_services()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "Multi-source Gulf of Mexico surf consensus & wave shoaling prediction engine "
            "for Holly Beach (Cameron Parish) and Grand Isle (Jefferson Parish), Louisiana."
        ),
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )

    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "app_name": settings.APP_NAME,
                "app_version": settings.APP_VERSION,
            },
        )

    app.include_router(api_router)
    return app


app = create_app()
