"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app import __version__
from app.config import settings
from app.database import SessionLocal, init_db
from app.deps import rate_limiter
from app.models import User
from app.routers import auth, dashboard, machines, operations, telemetry
from app.security import hash_password

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("nigrani")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def seed_admin() -> None:
    """Create the first admin if the users table is empty."""
    with SessionLocal() as db:
        if db.scalar(select(User).limit(1)) is not None:
            return
        admin = User(
            email=settings.seed_admin_email,
            full_name="Plant Administrator",
            hashed_password=hash_password(settings.seed_admin_password),
            role="admin",
        )
        db.add(admin)
        db.commit()
        logger.info("Seeded admin account %s", settings.seed_admin_email)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    seed_admin()
    logger.info("Nigrani %s started in %s mode", __version__, settings.environment)
    yield


app = FastAPI(
    title="Nigrani",
    description=(
        "Predictive maintenance and RPA control plane for factory machinery. "
        "Every health score is traceable to the reading that produced it."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": __version__, "environment": settings.environment}


app.include_router(auth.router, dependencies=[Depends(rate_limiter)])
app.include_router(machines.router, dependencies=[Depends(rate_limiter)])
app.include_router(telemetry.router, dependencies=[Depends(rate_limiter)])
app.include_router(operations.router, dependencies=[Depends(rate_limiter)])
app.include_router(dashboard.router, dependencies=[Depends(rate_limiter)])

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
