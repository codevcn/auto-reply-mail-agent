"""FastAPI application entrypoint for Mail Agent."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.attachment.router import attachment_router
from app.auth.router import auth_router, users_router
from app.auth.services import AuthBusinessError
from app.config import get_settings
from app.core.logging import setup_logging
from app.delivery.router import delivery_router
from app.draft.router import draft_router
from app.mail.router import mail_router
from app.proxy.router import proxy_router
from app.queue.router import queue_router
from app.retention.router import retention_router
from app.shopify.router import shopify_router
from app.store.router import store_router
from app.system.router import probes_router, system_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle management."""
    setup_logging(
        level=settings.LOG_LEVEL,
        json_format=settings.JSON_LOGS,
        extra_secrets=[settings.SECRET_KEY],
    )
    yield


app = FastAPI(
    title="Mail Agent API",
    description="Automated customer support email agent for Shopify stores",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AuthBusinessError)
async def auth_business_error_handler(request: Request, exc: AuthBusinessError) -> JSONResponse:
    """Standardized error handler for AuthBusinessError exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_code": exc.code, "message": exc.message},
    )


@app.get("/api/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "ok", "environment": settings.ENVIRONMENT}


# Root Probes (for Docker healthcheck and Kubernetes probes)
app.include_router(probes_router)

# Mount Routers (both /api and /api/v1 aliases for backward & forward compatibility)
app.include_router(auth_router, prefix="/api")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api")
app.include_router(users_router, prefix="/api/v1")
app.include_router(proxy_router, prefix="/api")
app.include_router(proxy_router, prefix="/api/v1")
app.include_router(shopify_router, prefix="/api")
app.include_router(shopify_router, prefix="/api/v1")
app.include_router(store_router, prefix="/api")
app.include_router(store_router, prefix="/api/v1")
app.include_router(mail_router, prefix="/api")
app.include_router(mail_router, prefix="/api/v1")
app.include_router(queue_router, prefix="/api")
app.include_router(queue_router, prefix="/api/v1")
app.include_router(draft_router, prefix="/api")
app.include_router(draft_router, prefix="/api/v1")
app.include_router(delivery_router, prefix="/api")
app.include_router(delivery_router, prefix="/api/v1")
app.include_router(attachment_router, prefix="/api")
app.include_router(attachment_router, prefix="/api/v1")
app.include_router(system_router, prefix="/api")
app.include_router(system_router, prefix="/api/v1")
app.include_router(retention_router, prefix="/api")
app.include_router(retention_router, prefix="/api/v1")

# Static Files & SPA Fallback for Frontend UI
import os
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if not os.path.exists(static_dir):
    static_dir = "/app/static"

if os.path.exists(static_dir):
    assets_dir = os.path.join(static_dir, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(request: Request, full_path: str):
        if full_path.startswith("api") or full_path.startswith("health"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        target_file = os.path.join(static_dir, full_path)
        if os.path.isfile(target_file):
            return FileResponse(target_file)
        index_file = os.path.join(static_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return JSONResponse(status_code=404, content={"detail": "Index not found"})

