# api/main.py
"""AgenticRAG API - FastAPI application for AgenticRAG core technology."""

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.openapi.utils import get_openapi
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from ._middleware_logging import (
    RequestLoggingMiddleware,
    AuthenticationLoggingMiddleware,
    CORSLoggingMiddleware,
)
from src.log_creator import get_file_logger, configure_uvicorn_logging
from src.config import Config

from ._routes import (
    health_router,
    admin_router,
    operations_router,
    documents_router,
    conversation_router,
    knowledge_base_router,
    mcp_server_router,
)

logger = get_file_logger()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def path_matches_pattern(path: str, pattern: str) -> bool:
    """
    Check if a URL path matches a wildcard pattern.

    Supports:
    - Exact match: /docs
    - Prefix match: /docs/* matches /docs/anything
    - Wildcard patterns: /admin/api_keys/* matches /admin/api_keys/123/delete
    - Full wildcard: * matches everything

    Args:
        path: The actual request path (e.g., /docs/openapi.json)
        pattern: The pattern to match against (e.g., /docs/*)

    Returns:
        True if path matches pattern, False otherwise

    Examples:
        path_matches_pattern("/docs/openapi.json", "/docs/*") -> True
        path_matches_pattern("/docs", "/docs") -> True
        path_matches_pattern("/admin/api_keys/abc123/delete", "/admin/api_keys/*") -> True
        path_matches_pattern("/other", "/docs/*") -> False
    """
    # Full wildcard matches everything
    if pattern == "*":
        return True

    # Exact match
    if path == pattern:
        return True

    # Pattern ends with /* - prefix matching
    if pattern.endswith("/*"):
        base_pattern = pattern[:-2]  # Remove /*
        return path.startswith(base_pattern + "/") or path == base_pattern

    # Pattern ends with * but not /* - suffix/glob matching
    if pattern.endswith("*"):
        prefix = pattern[:-1]  # Remove *
        return path.startswith(prefix)

    # No wildcard - exact match only
    return False


# ============================================================================
# APP LIFESPAN AND INITIALIZATION
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """lifespan context manager for startup and shutdown"""
    # Startup
    configure_uvicorn_logging()
    logger.info("Starting Core Backend API...")
    logger.info("Core Manager initialized")

    # Initialize OpenAPI schema
    get_openapi_schema()

    yield

    # Shutdown
    logger.info("Shutting down Core Backend...")


# Create FastAPI app
app = FastAPI(
    title="AgenticRAG API",
    description="API interface for accessing AgenticRAG core tech",
    version="0.2.0",
    lifespan=lifespan,
    default_response_class=JSONResponse,
)


# ============================================================================
# MIDDLEWARE
# ============================================================================


# Custom middleware to restrict /docs access by hostname
@app.middleware("http")
async def restrict_docs_by_host(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """
    Restrict special paths access to allowed hosts only.

    Supports wildcard patterns in SPECIAL_ACCESS_URL_PATHS:
    - Exact: /docs
    - Prefix: /docs/* (matches /docs/anything)
    - Wildcard: /admin/* (matches /admin/anything)

    Allowed hosts are configured via DOCS_ALLOWED_HOSTS environment variable.
    Default allowed hosts: localhost,127.0.0.1
    """
    request_path = request.url.path
    is_special_path = False

    # Check if request path matches any special access pattern
    for pattern in Config.SPECIAL_ACCESS_URL_PATHS:
        if path_matches_pattern(request_path, pattern):
            is_special_path = True
            break

    if is_special_path:
        # Extract hostname without port
        host = request.headers.get("host", "").split(":")[0]
        allowed_hosts = [h.strip() for h in Config.DOCS_ALLOWED_HOSTS]

        if host not in allowed_hosts:
            logger.warning(f"Access denied for host: {host} to path: {request_path}")
            return JSONResponse(status_code=403, content={"detail": "Access denied"})

    return await call_next(request)


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Alternative frontend port
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
        # Add production URLs here when deploying
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=500)

# Add logging middleware (in reverse order - last added is outermost)
app.add_middleware(AuthenticationLoggingMiddleware)
app.add_middleware(CORSLoggingMiddleware)
app.add_middleware(RequestLoggingMiddleware)


# ============================================================================
# OPENAPI SCHEMA CUSTOMIZATION
# ============================================================================


def get_openapi_schema():
    """Configure OpenAPI security scheme for global API key"""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="AgenticRAG API",
        version="0.2.0",
        description="API interface for accessing AgenticRAG tech",
        routes=app.routes,
    )

    # Preserve existing components and add security scheme
    if "components" not in openapi_schema:
        openapi_schema["components"] = {}

    if "securitySchemes" not in openapi_schema["components"]:
        openapi_schema["components"]["securitySchemes"] = {}

    openapi_schema["components"]["securitySchemes"]["API Key"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "API Key for authentication",
    }

    # Apply the security scheme to all endpoints
    openapi_schema["security"] = [{"API Key": []}]

    # Remove X-API-Key from individual endpoint parameters
    for path in openapi_schema.get("paths", {}).values():
        for operation in path.values():
            if isinstance(operation, dict) and "parameters" in operation:
                operation["parameters"] = [
                    param
                    for param in operation["parameters"]  # type: ignore
                    if param.get("name") != "x-api-key"  # type: ignore
                ]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


# ============================================================================
# ROUTE INCLUSION
# ============================================================================

# Include all routers
app.include_router(health_router)
app.include_router(admin_router)
app.include_router(operations_router)
app.include_router(conversation_router)
app.include_router(knowledge_base_router)
app.include_router(documents_router)
app.include_router(mcp_server_router)
