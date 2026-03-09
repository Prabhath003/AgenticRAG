"""
Operation & Service Logging Infrastructure

Context-aware logging system similar to Python's logging library.
Automatically tracks operations and links services without explicit passing.

Usage:
    from src.infrastructure.operation_logging import (
        OperationContext, operation_endpoint, log_service, get_current_operation,
        OperationType, ServiceType
    )

    # In endpoints:
    @app.post("/knowledge-base/create")
    @operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
    async def create_knowledge_base(request, _api_hash):
        # operation automatically created and tracked
        log_service(ServiceType.FILE_PROCESSOR, cost=0.15)
        return {"success": True}

    # Or with context manager:
    with OperationContext(api_hash, OperationType.CREATE_KNOWLEDGE_BASE) as op:
        log_service(ServiceType.FILE_PROCESSOR, cost=0.15)
        # operation automatically logged on exit
"""

from .operation_context import (
    OperationContext,
    operation_endpoint,
    get_current_operation,
    get_operation_stack,
    log_service,
    mark_operation_complete,
    mark_operation_failed,
    update_operation_status,
    get_operation_summary,
    update_operation_metadata,
    update_operation_description,
    get_operation_with_services,
    get_operation_id,
    get_operation_user_id,
)

__all__ = [
    # Functions
    "OperationContext",
    "operation_endpoint",
    "get_current_operation",
    "get_operation_stack",
    "log_service",
    "mark_operation_complete",
    "mark_operation_failed",
    "update_operation_status",
    "get_operation_summary",
    "update_operation_metadata",
    "update_operation_description",
    "get_operation_with_services",
    "get_operation_id",
    "get_operation_user_id",
]
