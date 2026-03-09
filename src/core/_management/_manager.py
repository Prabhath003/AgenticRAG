from typing import Optional, Dict, Any, List, Union, cast

from ...config import Config
from ...log_creator import get_file_logger
from ...infrastructure.database import get_db_session
from ..models.response_models import (
    GetOperationStatsResponse,
    ListOperationsResponse,
    ListServicesResponse,
)
from ..models.operation_audit import Operation, Service
from ._sub_managers import KnowledgeBaseManager, ConversationManager, MCPServer

logger = get_file_logger()


class Manager:
    def __init__(self):
        self.kb_manager = KnowledgeBaseManager()
        self.conv_manager = ConversationManager()
        self.mcp_server = MCPServer(self.conv_manager, kb_manager=self.kb_manager)

        logger.info("Initialized Manager with session cleanup enabled")

        # ==================== Task Operations ====================

    def get_operation_status(self, operation_id: str, user_id: str) -> GetOperationStatsResponse:
        """Get the status of an async task."""
        query_filter = {"_id": operation_id}

        query_filter["user_id"] = user_id

        with get_db_session() as db:
            operation_entry = db[Config.OPERATION_LOGS_COLLECTION].find_one(query_filter)
            services_query = {"operation_id": operation_id}
            services_query["user_id"] = user_id
            services_used = db[Config.SERVICE_LOGS_COLLECTION].find(services_query)

        if not operation_entry:
            return GetOperationStatsResponse(
                success=False,
                message=f"Task with task_id: {operation_id}, Does not exist",
            )

        operation_model = Operation(**operation_entry)
        services_used_models = [Service(**service) for service in services_used]

        return GetOperationStatsResponse(
            operation=operation_model, services_used=services_used_models
        )

    def list_operations(
        self,
        user_id: str,
        filters: Optional[Dict[str, Any]] = None,
        projections: Optional[Dict[str, Any]] = None,
    ) -> ListOperationsResponse:
        """List operations for a user with optional filtering and projections.

        When projections are used, returns raw dictionary data. Otherwise returns Pydantic models.
        """
        query_filter = {"user_id": user_id}

        if filters:
            query_filter.update(filters)

        with get_db_session() as db:
            operations_cursor = db[Config.OPERATION_LOGS_COLLECTION].find(query_filter, projections)
            operations: List[Dict[str, Any]] = list(operations_cursor)

        # If projections are used, return raw data; otherwise convert to Pydantic models
        if projections:
            operations_data: List[Union[Operation, Dict[str, Any]]] = cast(
                List[Union[Operation, Dict[str, Any]]], operations
            )
        else:
            operations_data = cast(
                List[Union[Operation, Dict[str, Any]]], [Operation(**op) for op in operations]
            )

        return ListOperationsResponse(
            success=True,
            message=f"Found {len(operations_data)} operations",
            operations=operations_data,
            count=len(operations_data),
            filters=filters or {},
        )

    def list_services(
        self,
        user_id: str,
        filters: Optional[Dict[str, Any]] = None,
        projections: Optional[Dict[str, Any]] = None,
    ) -> ListServicesResponse:
        """List services for a user with optional filtering and projections.

        When projections are used, returns raw dictionary data. Otherwise returns Pydantic models.
        """
        query_filter = {"user_id": user_id}

        if filters:
            query_filter.update(filters)

        with get_db_session() as db:
            services_cursor = db[Config.SERVICE_LOGS_COLLECTION].find(query_filter, projections)
            services: List[Dict[str, Any]] = list(services_cursor)

        # If projections are used, return raw data; otherwise convert to Pydantic models
        if projections:
            services_data: List[Union[Service, Dict[str, Any]]] = cast(
                List[Union[Service, Dict[str, Any]]], services
            )
        else:
            services_data = cast(
                List[Union[Service, Dict[str, Any]]], [Service(**service) for service in services]
            )

        return ListServicesResponse(
            success=True,
            message=f"Found {len(services_data)} services",
            services=services_data,
            count=len(services_data),
            filters=filters or {},
        )
