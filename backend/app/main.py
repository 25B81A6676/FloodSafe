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

    from app.services import region_service

    region_service.reload_regions()
    region_service.sync_locations_to_db()

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


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    if request.url.path.startswith("/api") and elapsed_ms > 1500:
        log.info("slow request %s %s took %.0f ms", request.method, request.url.path, elapsed_ms)
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
    gis,
    health,
    monitoring,
    regions,
    risk,
    simulation,
    terrain,
    weather,
)

API = "/api"
app.include_router(health.router, prefix=API)
app.include_router(regions.router, prefix=API)
app.include_router(weather.router, prefix=API)
app.include_router(terrain.router, prefix=API)
app.include_router(gis.router, prefix=API)
app.include_router(risk.router, prefix=API)
app.include_router(monitoring.router, prefix=API)
app.include_router(dashboard.router, prefix=API)
app.include_router(alerts.router, prefix=API)
app.include_router(simulation.router, prefix=API)


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
