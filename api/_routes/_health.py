"""Health check endpoints."""

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from datetime import datetime, timezone

from src.core.models.response_models import HealthResponse
from .._authentication import verify_api_key_header

router = APIRouter(tags=["Health"])


@router.get("/", tags=["Health"])
async def root(user_id: str = Depends(verify_api_key_header)):
    """Root endpoint - redirects to health check"""
    return RedirectResponse("/health")


@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check(user_id: str = Depends(verify_api_key_header)):
    """Health check endpoint"""
    return HealthResponse(status="healthy", timestamp=datetime.now(timezone.utc))
