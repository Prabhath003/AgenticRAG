# ============================================================================
# Operation Context Manager - Similar to Python's logging scope detection
# Automatically tracks operations and links services without explicit passing
# ============================================================================

from contextvars import ContextVar
from typing import Optional, Dict, Any, List, Callable, Type, Literal
from types import TracebackType
from functools import wraps
import inspect
import threading

from ...core.models.operation_audit import (
    Operation,
    OperationType,
    TaskStatus,
    Service,
    ServiceType,
)
from ..database import get_db_session
from ...config import Config
from ...log_creator import get_file_logger

logger = get_file_logger()

# Context variables for automatic scope detection (like logging.__name__)
# _current_operation: ContextVar[Optional[Operation]] = ContextVar('current_operation', default=None)
current_operation: ContextVar[Optional[Operation]] = ContextVar("current_operation", default=None)
_operation_stack: ContextVar[List[Operation]] = ContextVar("operation_stack", default=[])
# Context-specific locks for metadata updates (per-operation, not global)
operation_locks: ContextVar[Dict[str, threading.Lock]] = ContextVar("operation_locks", default={})


class OperationContext:
    """
    Context manager for operations - automatically detects scope like Python's logging.

    Automatically:
    - Creates operation entry with unique ID
    - Sets current operation in context
    - Handles status transitions (PENDING -> COMPLETED/FAILED)
    - Logs to database on exit (if auto_log_on_exit=True)
    - Restores parent operation on nested contexts

    Usage (auto-complete on exit):
        with OperationContext(user_id, OperationType.CREATE_KNOWLEDGE_BASE) as op:
            log_service(ServiceType.MONGODB, cost=0.15)  # logs operation_id to service
            # on exit, operation automatically logged to database (if no exception)

    Usage (deferred completion for background tasks):
        with OperationContext(
            user_id, OperationType.KNOWLEDGE_BASE_BUILD,
            auto_log_on_exit=False  # Don't log on exit
        ) as op:
            executor.submit(context.run, background_work, ...)
            # Later in background: mark_operation_complete()

    Args:
        user_id: User ID for audit trail
        operation_type: Type of operation
        description: Optional description of operation
        metadata: Optional metadata dictionary
        auto_log_on_exit: If True (default), log to DB on context exit.
                        If False, manual mark_operation_complete() required
    """

    def __init__(
        self,
        user_id: str,
        operation_type: OperationType,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        auto_log_on_exit: bool = True,
    ):
        self.user_id = user_id
        self.operation_type = operation_type
        self.description = description
        self.metadata = metadata
        self.auto_log_on_exit = auto_log_on_exit
        self.operation: Optional[Operation] = None
        self.parent_operation: Optional[Operation] = None

    def __enter__(self) -> Operation:
        """Enter operation context - creates and sets current operation"""
        logger.debug(
            f"[CONTEXT] __enter__ called for operation_type={self.operation_type.value}, user_id={self.user_id}"
        )

        self.operation = Operation(
            user_id=self.user_id,
            operation_type=self.operation_type,
            status=TaskStatus.PENDING,
            description=self.description,
            metadata=self.metadata,
        )

        logger.debug(f"[CONTEXT] Created operation {self.operation.get_id()}")

        # Save current operation and push to stack
        self.parent_operation = current_operation.get()
        current_operation.set(self.operation)

        stack = _operation_stack.get().copy()
        stack.append(self.operation)
        _operation_stack.set(stack)

        # Create and store a context-specific lock for this operation (per-operation, not global)
        locks = operation_locks.get().copy()
        locks[self.operation.get_id()] = threading.Lock()
        operation_locks.set(locks)

        # Persist initial operation entry to database immediately
        _persist_operation_to_db(self.operation)

        logger.debug(
            f"[CONTEXT] Entered operation context: {self.operation.get_id()} "
            f"({self.operation_type.value}), parent_op={self.parent_operation.get_id() if self.parent_operation else 'None'}"
        )
        return self.operation

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> Literal[False]:
        """Exit operation context - updates status and optionally logs"""
        logger.debug(
            f"[CONTEXT] __exit__ called for {self.operation.get_id() if self.operation else 'NONE'}, exc_type={exc_type}, auto_log_on_exit={self.auto_log_on_exit}"
        )

        # Handle operation status
        if self.operation:
            if exc_type is not None:
                self.operation.mark_failed(f"{exc_type.__name__}: {str(exc_val)}")
                logger.error(
                    f"Operation failed: {self.operation.get_id()} - {exc_type.__name__}: {exc_val}"
                )
            else:
                # Only auto-mark completed if auto_log_on_exit is True
                # Otherwise, wait for manual mark_operation_complete() call
                logger.debug(f"{type(self.operation.status)}")
                if self.auto_log_on_exit:
                    # Mark as completed only if not already marked as completed or failed
                    if self.operation.status not in [
                        TaskStatus.COMPLETED,
                        TaskStatus.FAILED,
                    ]:
                        self.operation.mark_completed()
                        logger.info(f"Operation completed: {self.operation.get_id()}")
                    else:
                        logger.debug(
                            f"[CONTEXT] Operation already completed/failed: {self.operation.get_id()}, status={self.operation.status}"
                        )
                else:
                    logger.debug(
                        f"[CONTEXT] Skipping auto-mark-completed: auto_log_on_exit=False, status={self.operation.status}"
                    )

            # Restore parent operation
            current_operation.set(self.parent_operation)

            # Pop from stack
            stack = _operation_stack.get().copy()
            if stack and stack[-1].get_id() == self.operation.get_id():
                stack.pop()
            _operation_stack.set(stack)

            # Clean up context-specific lock for this operation
            locks = operation_locks.get().copy()
            if self.operation.get_id() in locks:
                del locks[self.operation.get_id()]
            operation_locks.set(locks)

            # Persist final operation state to database (only if auto_log_on_exit is True)
            # Note: Operation was already inserted in __enter__, so we use replace_one (via _persist_operation_to_db)
            if self.auto_log_on_exit:
                _persist_operation_to_db(self.operation)

            logger.debug(f"[CONTEXT] __exit__ COMPLETE for {self.operation.get_id()}")

        return False  # Don't suppress exceptions


def operation_endpoint(
    operation_type: OperationType,
    description_formatter: Optional[Callable[..., str]] = None,
    auto_complete: bool = True,
):
    """
    Decorator for FastAPI endpoints - automatically creates operation context.

    Automatically:
    - Creates operation entry on endpoint entry
    - Extracts user_id from dependency injection for audit trail
    - Logs operation status and services on exit (if auto_complete=True)
    - Handles exceptions and marks operation as failed

    Usage (auto-complete on exit):
        @app.post("/knowledge-base/create")
        @operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
        async def create_knowledge_base(request, user_id: str = Depends(verify_api_key_header)):
            # operation automatically created and logged on exit
            log_service(ServiceType.FILE_PROCESSOR, 0.15)
            return {"success": True}

    Usage (deferred completion for background tasks):
        @app.post("/knowledge-base/{kb_id}/trigger-build")
        @operation_endpoint(
            OperationType.KNOWLEDGE_BASE_BUILD,
            auto_complete=False  # Don't auto-log on exit
        )
        async def trigger_build(kb_id: str, user_id: str = Depends(verify_api_key_header)):
            # operation created but NOT logged on exit
            # Background task must call mark_operation_complete()
            context = copy_context()
            executor.submit(context.run, background_work, kb_id, user_id)
            return {"status": "queued"}

    Args:
        operation_type: Type of operation
        description_formatter: Optional callable(request, **kwargs) -> str for description
        auto_complete: If True (default), operation auto-logged on exit.
                      If False, background task must call mark_operation_complete()
    """

    def decorator(func: Callable[..., Any]):
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any):
            # Extract user_id from kwargs (dependency injection parameter: _user_id)
            user_id = kwargs.get("user_id") or kwargs.get("_user_id", "unknown")

            logger.debug(
                f"[DECORATOR] async_wrapper ENTERED for {func.__name__}, user_id={user_id}"
            )

            # Generate description
            description = None
            if description_formatter:
                try:
                    description = description_formatter(*args, **kwargs)
                except Exception as e:
                    logger.debug(f"Failed to format description: {str(e)}")

            logger.debug(f"[DECORATOR] Creating OperationContext for {func.__name__}")
            with OperationContext(
                user_id=user_id,
                operation_type=operation_type,
                description=description,
                auto_log_on_exit=auto_complete,
            ):
                logger.debug(f"[DECORATOR] Inside context, about to call {func.__name__}")
                result = await func(*args, **kwargs)
                logger.debug(f"[DECORATOR] Function returned, exiting context for {func.__name__}")
                return result

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any):
            # Extract user_id from kwargs (dependency injection parameter: _user_id)
            user_id = kwargs.get("user_id") or kwargs.get("_user_id", "unknown")

            logger.debug(f"[DECORATOR] sync_wrapper ENTERED for {func.__name__}, user_id={user_id}")

            # Generate description
            description: Optional[str] = None
            if description_formatter:
                try:
                    description = description_formatter(*args, **kwargs)
                except Exception as e:
                    logger.debug(f"Failed to format description: {str(e)}")

            logger.debug(f"[DECORATOR] Creating OperationContext for {func.__name__}")
            with OperationContext(
                user_id=user_id,
                operation_type=operation_type,
                description=description,
                auto_log_on_exit=auto_complete,
            ):
                logger.debug(f"[DECORATOR] Inside context, about to call {func.__name__}")
                result = func(*args, **kwargs)
                logger.debug(f"[DECORATOR] Function returned, exiting context for {func.__name__}")
                return result

        # Use async wrapper if function is async, else sync
        is_async = inspect.iscoroutinefunction(func)
        logger.debug(f"[DECORATOR] Decorating {func.__name__}, is_async={is_async}")

        if is_async:
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# def get_current_operation() -> Optional[Operation]:
#     """
#     Get the current operation from context (like logging.getLogger()).

#     Returns:
#         Current Operation object or None if no active operation
#     """
#     return current_operation.get()


def get_operation_stack() -> List[Operation]:
    """
    Get the current operation stack (nested contexts).

    Returns:
        Copy of operation stack
    """
    return _operation_stack.get().copy()


def _persist_operation_to_db(operation: Operation) -> bool:
    """
    Internal helper: Persist operation state to database.

    Uses MongoDB replace_one with upsert to atomically update or insert operation.
    Called after every operation state change for durability and real-time visibility.

    Args:
        operation: Operation object to persist

    Returns:
        True if successful, False otherwise
    """
    try:
        with get_db_session() as db:
            db[Config.OPERATION_LOGS_COLLECTION].replace_one(
                {"_id": operation.get_id()},
                operation.model_dump(by_alias=True, exclude_none=True),
                upsert=True,
            )
            logger.debug(f"Operation persisted to DB: {operation.get_id()}")
            return True
    except Exception as e:
        logger.error(f"Failed to persist operation to DB: {str(e)}")
        return False


def log_service(
    service_type: ServiceType,
    estimated_cost_usd: float = 0.0,
    breakdown: Optional[Dict[str, Any]] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Service]:
    """
    Log a service within the current operation context.
    Automatically links service to current operation.

    Automatically:
    - Creates Service entry with unique ID and operation_id reference
    - Updates operation's estimated cost
    - Logs service to SERVICE_LOGS_COLLECTION
    - Warns if no active operation context

    Usage (inside operation):
        log_service(
            ServiceType.FILE_PROCESSOR,
            estimated_cost_usd=0.15,
            breakdown={"chunks": 100, "cost_per_chunk": 0.0015},
            description="Processed 100 chunks"
        )

    Args:
        service_type: Type of service used
        estimated_cost_usd: Cost of the service
        breakdown: Cost breakdown details
        description: Optional description
        metadata: Optional metadata

    Returns:
        The created Service object, or None if no active operation
    """
    operation = get_current_operation()

    if not operation:
        logger.warning(f"log_service called without active operation context: {service_type.value}")
        return None

    # Create service entry with operation_id reference
    service = Service(
        operation_id=operation.get_id(),
        user_id=operation.user_id,
        service_type=service_type,
        breakdown=breakdown or {},
        estimated_cost_usd=round(estimated_cost_usd, 6),
        status=TaskStatus.COMPLETED,
        description=description,
        metadata=metadata,
    )

    # Update operation's estimated cost
    operation.estimated_cost_usd = round(operation.estimated_cost_usd + estimated_cost_usd, 6)

    # Persist updated operation to database (cost changed)
    _persist_operation_to_db(operation)

    # Log service to database
    try:
        with get_db_session() as db:
            db[Config.SERVICE_LOGS_COLLECTION].insert_one(
                service.model_dump(by_alias=True, exclude_none=True)
            )
            logger.debug(
                f"Service logged: {service.get_id()} (type: {service_type.value}, "
                f"cost: ${service.estimated_cost_usd:.6f}, operation: {operation.get_id()})"
            )
    except Exception as e:
        logger.error(f"Failed to log service: {str(e)}")
        return None

    return service


def mark_operation_complete(actual_cost_usd: Optional[float] = None) -> None:
    """
    Mark current operation as completed and log to database.

    This is used when auto_log_on_exit=False in OperationContext.
    Marks operation as complete and logs it to OPERATION_LOGS_COLLECTION.

    Args:
        actual_cost_usd: Actual cost of operation (optional)
    """
    operation = get_current_operation()
    if operation:
        operation.mark_completed(actual_cost_usd)
        logger.info(f"Operation marked complete: {operation.get_id()}")

        # Persist to database using replace_one (operation already exists from __enter__)
        _persist_operation_to_db(operation)
    else:
        logger.warning("mark_operation_complete called without active operation")


def mark_operation_failed(error_description: str) -> None:
    """
    Mark current operation as failed and log to database.

    This is used when an exception occurs in background tasks with auto_log_on_exit=False.
    Marks operation as failed and logs it to OPERATION_LOGS_COLLECTION.

    Args:
        error_description: Description of the error
    """
    operation = get_current_operation()
    if operation:
        operation.mark_failed(error_description)
        logger.error(f"Operation marked failed: {operation.get_id()} - {error_description}")

        # Persist to database using replace_one (operation already exists from __enter__)
        _persist_operation_to_db(operation)
    else:
        logger.warning("mark_operation_failed called without active operation")


def update_operation_status(status: TaskStatus) -> Optional[str]:
    """
    Update operation status to any TaskStatus value.

    Useful for setting status to QUEUED, PROCESSING, etc. during long-running operations.
    Status changes are persisted to database immediately for real-time visibility.

    Usage:
        # Set to processing
        update_operation_status(TaskStatus.PROCESSING)

        # Set to queued
        update_operation_status(TaskStatus.QUEUED)

    Args:
        status: The TaskStatus to set (PENDING, QUEUED, PROCESSING, COMPLETED, FAILED)

    Returns:
        Operation ID if updated, None if no active operation
    """
    operation = get_current_operation()
    if not operation:
        logger.warning("update_operation_status called without active operation")
        return None

    operation.status = status
    logger.debug(f"Operation status updated: {operation.get_id()} -> {status.value}")

    # Persist status change to database
    _persist_operation_to_db(operation)

    return operation.get_id()


def get_operation_summary() -> Optional[Dict[str, Any]]:
    """
    Get summary of current operation.

    Returns:
        Dictionary with operation summary or None if no active operation.
        Note: services_count can be retrieved separately by querying SERVICE_LOGS_COLLECTION
            with operation_id filter.
    """
    operation = get_current_operation()
    if not operation:
        return None

    return {
        "operation_id": operation.get_id(),
        "operation_type": operation.operation_type.value,
        "status": operation.status.value,
        "estimated_cost_usd": operation.estimated_cost_usd,
        "actual_cost_usd": operation.actual_cost_usd,
        "duration_seconds": operation.get_duration_seconds(),
        "created_at": operation.created_at.isoformat(),
        "completed_at": (operation.completed_at.isoformat() if operation.completed_at else None),
    }


def update_operation_metadata(metadata: Dict[str, Any]) -> Optional[str]:
    """
    Update operation metadata with MongoDB-style operators for powerful control.

    Supports MongoDB update operators:
    - $set: Set/overwrite field values
    - $addToSet: Append to list (no duplicates)
    - $push: Append to list (allows duplicates)
    - $inc: Increment numeric fields
    - $unset: Remove fields
    - Scalar values (no operator): Simple key-value pairs (same as $set)

    Thread-safe: Uses context-specific locks (per-operation, not global) for metadata updates.
    Each operation gets its own lock, allowing parallel operations to proceed independently.

    Usage:
        # Simple set (old style - still works)
        update_operation_metadata({"kb_title": "My KB", "source_count": 5})

        # With operators for nested function calls
        update_operation_metadata({
            "$set": {"kb_title": "My KB"},
            "$addToSet": {"kb_ids": ["kb1"]},  # Append to list
            "$inc": {"doc_count": 5}
        })

    Args:
        metadata: Dictionary with MongoDB operators or simple key-value pairs

    Returns:
        Operation ID if updated, None if no active operation
    """
    operation = get_current_operation()
    if not operation:
        logger.warning("update_operation_metadata called without active operation")
        return None

    # Get the context-specific lock for this operation
    locks = operation_locks.get()
    operation_lock = locks.get(operation.get_id())

    if not operation_lock:
        logger.warning(f"No lock found for operation {operation.get_id()}")
        return None

    # Use context-specific lock (per-operation, not global)
    with operation_lock:
        if operation.metadata is None:
            operation.metadata = {}

        # Handle MongoDB-style operators
        if any(key.startswith("$") for key in metadata.keys()):
            # Process operators
            if "$set" in metadata:
                for key, value in metadata["$set"].items():
                    operation.metadata[key] = value

            if "$addToSet" in metadata:
                for key, value in metadata["$addToSet"].items():
                    if key not in operation.metadata:
                        operation.metadata[key] = []
                    if not isinstance(operation.metadata[key], list):
                        operation.metadata[key] = [operation.metadata[key]]
                    # Handle both single values and lists
                    if isinstance(value, list):
                        for item in value:  # type: ignore
                            if item not in operation.metadata[key]:
                                operation.metadata[key].append(item)
                    else:
                        if value not in operation.metadata[key]:
                            operation.metadata[key].append(value)

            if "$push" in metadata:
                for key, value in metadata["$push"].items():
                    if key not in operation.metadata:
                        operation.metadata[key] = []
                    if not isinstance(operation.metadata[key], list):
                        operation.metadata[key] = [operation.metadata[key]]
                    # Handle both single values and lists
                    if isinstance(value, list):
                        operation.metadata[key].extend(value)
                    else:
                        operation.metadata[key].append(value)

            if "$inc" in metadata:
                for key, value in metadata["$inc"].items():
                    if key not in operation.metadata:
                        operation.metadata[key] = 0
                    operation.metadata[key] = operation.metadata[key] + value

            if "$unset" in metadata:
                for key in metadata["$unset"].keys():
                    if key in operation.metadata:
                        del operation.metadata[key]
        else:
            # No operators - treat as simple $set (backward compatible)
            operation.metadata.update(metadata)

        logger.debug(f"Operation metadata updated: {operation.get_id()}")

        # Persist metadata changes to database
        _persist_operation_to_db(operation)

    return operation.get_id()


def update_operation_description(description: str, append: bool = False) -> Optional[str]:
    """
    Update operation description.

    Description changes are persisted to database immediately for audit trail.

    Usage:
        # Replace description
        update_operation_description("New description")

        # Append to existing description
        update_operation_description("Additional info", append=True)

    Args:
        description: New description text
        append: If True, append to existing description. If False, replace it.

    Returns:
        Operation ID if updated, None if no active operation
    """
    operation = get_current_operation()
    if not operation:
        logger.warning("update_operation_description called without active operation")
        return None

    if append and operation.description:
        operation.description = f"{operation.description}; {description}"
    else:
        operation.description = description

    logger.debug(f"Operation description updated: {operation.get_id()}")

    # Persist description change to database
    _persist_operation_to_db(operation)

    return operation.get_id()


# def get_operation_id() -> Optional[str]:
#     """
#     Get current operation ID.

#     Useful for returning operation_id in API responses.

#     Usage:
#         @operation_endpoint(OperationType.GET_KB)
#         async def get_kb(kb_id: str, user_id: str = Depends(verify_api_key_header)):
#             # ... work ...
#             op_id = get_operation_id()
#             return {"kb_id": kb_id, "operation_id": op_id}

#     Returns:
#         Operation ID string or None if no active operation
#     """
#     operation = get_current_operation()
#     return operation.get_id() if operation else None


# def get_operation_user_id() -> Optional[str]:
#     """
#     Get current operation user ID for audit trail and filtering.

#     Useful for filtering database queries by user (user_id).

#     Usage:
#         @operation_endpoint(OperationType.GET_KB)
#         async def get_kb(kb_id: str, user_id: str = Depends(verify_api_key_header)):
#             current_user_id = get_operation_user_id()
#             # Query documents filtered by both kb_id AND user_id
#             kb = db[collection].find_one({"_id": kb_id, "user_id": current_user_id})

#     Returns:
#         User ID string or None if no active operation
#     """
#     operation = get_current_operation()
#     return operation.user_id if operation else None


def get_operation_with_services(
    get_breakdown: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Get operation data with optional service breakdown.

    Perfect for API responses where you want:
    - Without breakdown: just operation_id and status
    - With breakdown: operation_id, status, and all services

    Usage:
        @operation_endpoint(OperationType.GET_KB)
        async def get_kb(...):
            # ... work ...

            # Simple response
            return get_operation_with_services(get_breakdown=False)

            # Or detailed response
            return get_operation_with_services(get_breakdown=True)

    Args:
        get_breakdown: If True, include all services. If False, just operation data.

    Returns:
        Dict with operation data (+ services if get_breakdown=True), or None if no active operation
    """
    operation = get_current_operation()
    if not operation:
        return None

    result: Dict[str, Any] = {
        "operation_id": operation.get_id(),
        "operation_type": operation.operation_type.value,
        "status": operation.status.value,
        "estimated_cost_usd": operation.estimated_cost_usd,
        "created_at": operation.created_at.isoformat(),
    }

    if get_breakdown:
        try:
            with get_db_session() as db:
                # Fetch all services for this operation
                services = list(
                    db[Config.SERVICE_LOGS_COLLECTION].find({"operation_id": operation.get_id()})
                )

                # Convert ObjectIds to strings
                for service in services:
                    if "_id" in service:
                        service["_id"] = str(service["_id"])

                result["services"] = services
                result["service_count"] = len(services)
                result["total_service_cost"] = sum(s.get("estimated_cost_usd", 0) for s in services)
        except Exception as e:
            logger.error(f"Failed to fetch services for operation {operation.get_id()}: {str(e)}")
            result["services"] = []
            result["service_count"] = 0

    return result


def get_current_operation() -> Optional[Operation]:
    """
    Get the current operation from context (like logging.getLogger()).

    Returns:
        Current Operation object or None if no active operation
    """
    return current_operation.get()


def get_operation_id() -> Optional[str]:
    """
    Get current operation ID.

    Useful for returning operation_id in API responses.

    Usage:
        @operation_endpoint(OperationType.GET_KB)
        async def get_kb(kb_id: str, user_id: str = Depends(verify_api_key_header)):
            # ... work ...
            op_id = get_operation_id()
            return {"kb_id": kb_id, "operation_id": op_id}

    Returns:
        Operation ID string or None if no active operation
    """
    operation = get_current_operation()
    return operation.get_id() if operation else None


def get_operation_user_id() -> Optional[str]:
    """
    Get current operation user ID for audit trail and filtering.

    Useful for filtering database queries by user (user_id).

    Usage:
        @operation_endpoint(OperationType.GET_KB)
        async def get_kb(kb_id: str, user_id: str = Depends(verify_api_key_header)):
            current_user_id = get_operation_user_id()
            # Query documents filtered by both kb_id AND user_id
            kb = db[collection].find_one({"_id": kb_id, "user_id": current_user_id})

    Returns:
        User ID string or None if no active operation
    """
    operation = get_current_operation()
    return operation.user_id if operation else None
