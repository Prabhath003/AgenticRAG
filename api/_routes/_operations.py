"""Operation status tracking endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .._authentication import verify_api_key_header
from .._dependencies import task_manager
from src.core.models._request_models import (
    ListOperationsRequest,
    ListServicesRequest,
)
from src.core.models.operation_audit import OperationType
from src.infrastructure.operation_logging import operation_endpoint
from src.log_creator import get_file_logger

logger = get_file_logger()
router = APIRouter(prefix="/operation", tags=["Operations"])


@router.get("/{operation_id}", tags=["Operations"])
async def get_operation_status(operation_id: str, user_id: str = Depends(verify_api_key_header)):
    """Get the status of an async task"""
    try:
        response = task_manager.get_operation_status(operation_id, user_id)
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/list", tags=["Operations"])
@operation_endpoint(OperationType.LIST_OPERATIONS)
async def list_operations(
    request: ListOperationsRequest, user_id: str = Depends(verify_api_key_header)
):
    """List operations for the authenticated user with optional filtering and projections.

    Request body:
        filters: MongoDB-style filters to apply to operations
        projections: MongoDB-style projections to specify which fields to include/exclude
    """
    try:
        response = task_manager.list_operations(
            user_id=user_id,
            filters=request.filters,
            projections=request.projections,
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error listing operations: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/services/list", tags=["Operations"])
@operation_endpoint(OperationType.LIST_SERVICES)
async def list_services(
    request: ListServicesRequest, user_id: str = Depends(verify_api_key_header)
):
    """List services for the authenticated user with optional filtering and projections.

    Request body:
        filters: MongoDB-style filters to apply to services
        projections: MongoDB-style projections to specify which fields to include/exclude
    """
    try:
        response = task_manager.list_services(
            user_id=user_id,
            filters=request.filters,
            projections=request.projections,
        )
        if not response.success:
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json", exclude_none=True),
            )
        return response
    except Exception as e:
        logger.error(f"Error listing services: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal Server Error")
