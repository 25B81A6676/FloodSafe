"""FloodSafe backend application.

SIH26192 - Flash Flood Prediction System for Hilly Regions using Multi-Source Data.

This is a decision-support prototype. It is NOT a certified operational
flood-warning system and does not issue official evacuation orders.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config.logging_config import configure_logging, get_logger
from app.config.settings import settings
from app.database.db import init_db

configure_logging(settings.log_level)
log = get_logger(__name__)

DISCLAIMER = (
    "FloodSafe is a decision-support and early-warning prototype built for "
    "SIH26192. Risk scores come from a transparent weighted model over open "
    "environmental data, not from a validated hydrological forecast. It must "
    "not be used as the sole basis for emergency decisions."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=" * 74)
    log.info("  %s v%s - %s", settings.app_name, settings.version, settings.app_tagline)
    log.info("=" * 74)
    init_db()

    from app.services import region_service, seed_cache

    region_service.reload_regions()
    region_service.sync_locations_to_db()

    # Load the bundled OpenStreetMap / terrain snapshot before anything can ask
    # for it. On a serverless host this is what keeps a cold request from
    # blocking on a 12-16 second Overpass query.
    seed_cache.load_seed()

    if settings.startup_prefetch:
        import asyncio

        from app.services.prefetch import warm_cache

        asyncio.create_task(warm_cache())
        log.info("background cache warm-up scheduled")

    yield

    from app.services.http_client import close_client

    await close_client()
    log.info("shutdown complete")


app = FastAPI(
    title=f"{settings.app_name} API",
    description=DISCLAIMER,
    version=settings.version,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# How long a CDN in front of this app may serve each GET response, in seconds.
# A first request for a state the cache has never seen takes tens of seconds:
# it fetches weather, terrain and river geometry for every district. Serving
# that from the edge afterwards is the difference between a demonstration that
# waits and one that does not.
#
# The second number is stale-while-revalidate: past the first window the edge
# answers instantly from its copy AND refreshes in the background, so only the
# very first visitor ever waits.
#
# Prefix -> (fresh_seconds, stale_seconds)
EDGE_CACHE: tuple[tuple[str, tuple[int, int]], ...] = (
    # Geography and river geometry: administrative data, effectively static.
    ("/api/geography", (3600, 86400)),
    ("/api/gis/", (3600, 86400)),
    ("/api/regions", (3600, 86400)),
    ("/api/locations", (3600, 86400)),
    ("/api/risk/model", (3600, 86400)),
    # Measured conditions. Upstream itself only updates every 15 minutes, so a
    # short window costs no freshness while removing the wait.
    # The long stale window is deliberate. Past the first minute the edge still
    # answers instantly from its copy while refreshing behind the request, so a
    # slow or failing upstream degrades into slightly older data rather than a
    # spinner or a gateway timeout. Nothing pretends to be fresher than it is:
    # every payload carries the real observation time, which the UI displays.
    ("/api/dashboard/", (60, 86400)),
    ("/api/risk/map", (60, 86400)),
    ("/api/monitoring/", (60, 86400)),
    ("/api/weather", (60, 86400)),
    ("/api/terrain", (3600, 86400)),
)

# Never cached, whatever the path prefixes above say: these either change on
# every call or report live state that must not be stale.
EDGE_CACHE_NEVER = ("/api/notifications", "/api/simulation", "/api/alerts", "/api/health")


def _edge_cache_header(request: Request) -> str | None:
    """Cache-Control for this request, or None to leave it uncached."""
    if request.method not in ("GET", "HEAD"):
        return None
    path = request.url.path
    if path.startswith(EDGE_CACHE_NEVER):
        return None
    # An explicit refresh means the caller wants live data, not a copy.
    if request.query_params.get("refresh", "").lower() in ("1", "true", "yes"):
        return None
    # The frontend stamps reads with the simulation's state while the simulator
    # is running, precisely so they miss the CDN. Honour that without consulting
    # the database: a CDN hit never reaches this process, so by the time the
    # simulation starts the cached copy is already being served, and the client
    # changing the URL is the only thing that can break through it.
    if "_sim" in request.query_params:
        return "no-store"

    for prefix, (fresh, stale) in EDGE_CACHE:
        if path.startswith(prefix):
            # While the simulator is driving the risk engine, every reading it
            # touches must be live or the demonstration would show a cached
            # pre-flood picture. Correctness beats speed for these few minutes.
            if fresh < 3600 and _simulation_active():
                return "no-store"
            return f"public, max-age=0, s-maxage={fresh}, stale-while-revalidate={stale}"
    return None


def _simulation_active() -> bool:
    try:
        from app.database import repository

        return bool(repository.load_simulation_state().get("active"))
    except Exception:  # noqa: BLE001 - never fail a request over a cache hint
        return False


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    if request.url.path.startswith("/api") and elapsed_ms > 1500:
        log.info("slow request %s %s took %.0f ms", request.method, request.url.path, elapsed_ms)
    if response.status_code == 200:
        cache_control = _edge_cache_header(request)
        if cache_control:
            response.headers["Cache-Control"] = cache_control
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": str(exc)[:300],
            "path": request.url.path,
        },
    )


# --------------------------------------------------------------------------
# Routers
# --------------------------------------------------------------------------
from app.api.routes import (  # noqa: E402
    alerts,
    dashboard,
    geography,
    gis,
    health,
    monitoring,
    notifications,
    regions,
    risk,
    simulation,
    terrain,
    weather,
)

API = "/api"
app.include_router(health.router, prefix=API)
app.include_router(regions.router, prefix=API)
app.include_router(geography.router, prefix=API)
app.include_router(weather.router, prefix=API)
app.include_router(terrain.router, prefix=API)
app.include_router(gis.router, prefix=API)
app.include_router(risk.router, prefix=API)
app.include_router(monitoring.router, prefix=API)
app.include_router(dashboard.router, prefix=API)
app.include_router(alerts.router, prefix=API)
app.include_router(simulation.router, prefix=API)
app.include_router(notifications.router, prefix=API)


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "name": settings.app_name,
        "tagline": settings.app_tagline,
        "version": settings.version,
        "problem_statement": "SIH26192 - Flash Flood Prediction System for Hilly Regions using Multi-Source Data",
        "disclaimer": DISCLAIMER,
        "docs": "/docs",
        "health": "/api/health",
    }
