# Integration Example: Knowledge Base Manager

How to integrate the operation & service logging system into existing `create_knowledge_base` and `get_knowledge_base` functions.

## Before: Manual Operation Logging

```python
def create_knowledge_base(self, user_id_or_api_key: str, ...):
    """Create a new knowledge base."""
    kb_id = generate_kb_id()
    created_at = datetime.now(timezone.utc).isoformat()

    entry: Dict[str, Any] = {
        "_id": kb_id,
        "title": title,
        # ... other fields ...
    }

    try:
        with get_db_session() as db:
            db[Config.KNOWLEDGE_BASES_COLLECTION].insert_one(entry)

            # Manual operation logging - verbose and error-prone
            db[Config.OPERATION_LOGS_COLLECTION].insert_one(
                Operation(
                    user_id_or_api_key=user_id_or_api_key,
                    operation_type=OperationType.CREATE_KNOWLEDGE_BASE,
                    status=TaskStatus.COMPLETED,
                    description=f"Created KB {kb_id}"
                ).model_dump()
            )

        return entry

    except DuplicateKeyError:
        # Had to manually log failed operations
        db[Config.OPERATION_LOGS_COLLECTION].insert_one(
            Operation(
                user_id_or_api_key=user_id_or_api_key,
                operation_type=OperationType.CREATE_KNOWLEDGE_BASE,
                status=TaskStatus.FAILED,
                description="Duplicate KB creation"
            ).model_dump()
        )
        raise
```

## After: Automatic Operation Logging

### Option A: In FastAPI Endpoint

```python
from fastapi import FastAPI, Depends, HTTPException
from src.infrastructure.operation_logging import operation_endpoint, log_service
from src.core.management.core_models import OperationType, ServiceType

app = FastAPI()

def format_create_kb_desc(request, **kwargs):
    return f"Create KB '{request.title}'"

@app.post("/knowledge-base/create", tags=["Knowledge Base"])
@operation_endpoint(
    OperationType.CREATE_KNOWLEDGE_BASE,
    description_formatter=format_create_kb_desc
)
async def create_knowledge_base_endpoint(request: CreateKnowledgeBaseRequest, api_key: str):
    """
    Endpoint automatically wrapped in operation context.

    @operation_endpoint decorator handles:
    - Creating Operation entry automatically
    - Setting current operation in context
    - Extracting api_key for audit trail
    - Logging operation on exit (COMPLETED or FAILED)
    - No manual Operation() creation needed
    """
    try:
        # Call manager - no api_key needed, context is automatic
        result = task_manager.create_knowledge_base(
            title=request.title,
            metadata=request.metadata or {}
        )

        # Log DB operation - automatically linked to operation
        log_service(
            ServiceType.MONGODB,
            estimated_cost_usd=0.02,
            breakdown={"operation": "insert_one", "collection": "knowledge_bases"}
        )

        return JSONResponse(status_code=201, content={
            "success": True,
            "message": "Knowledge base created successfully",
            "kb_id": result["_id"],
        })

    except Exception as e:
        # Operation automatically marked as FAILED by decorator
        # (No manual error logging needed)
        logger.error(f"Error creating knowledge base: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
```

### Option B: In Manager Method

If you want operation logging inside the manager:

```python
from src.infrastructure.operation_logging import OperationContext, log_service

class KnowledgeBaseManager:

    def create_knowledge_base(
        self,
        user_id_or_api_key: str,
        title: Optional[str] = None,
        metadata: Dict[str, Any] = {}
    ) -> Dict[str, Any]:
        """Create a new knowledge base with automatic operation logging."""

        # Wrap entire operation in context
        with OperationContext(
            user_id_or_api_key=user_id_or_api_key,
            operation_type=OperationType.CREATE_KNOWLEDGE_BASE,
        ):
            # Validate inputs

            kb_id = generate_kb_id()
            created_at = datetime.now(timezone.utc).isoformat()

            entry: Dict[str, Any] = {
                "_id": kb_id,
                "title": title,
                "metadata": metadata,
                # ... other fields ...
            }

            try:
                with self._db_lock:
                    with get_db_session() as db:
                        # Insert KB entry
                        db[Config.KNOWLEDGE_BASES_COLLECTION].insert_one(entry)

                        # Log the DB operation
                        log_service(
                            ServiceType.MONGODB,
                            estimated_cost_usd=0.02,
                            description="Inserted KB entry",
                            breakdown={
                                "operation": "insert_one",
                                "collection": "knowledge_bases"
                            }
                        )

                        logger.info(f"Created KB {kb_id}")

            except DuplicateKeyError:
                # Operation automatically marked as FAILED on exception
                # (No manual failure logging needed)
                error_msg = f"KB {kb_id} already exists"
                logger.error(error_msg)
                raise ValueError(error_msg)

            # On exit: operation automatically logged to OPERATION_LOGS_COLLECTION
            # Status set to COMPLETED if no exception, FAILED if exception
            return entry

    def get_knowledge_base(self, user_id_or_api_key: str, kb_id: str) -> Dict[str, Any]:
        """Get knowledge base information with automatic operation logging."""

        with OperationContext(
            user_id_or_api_key=user_id_or_api_key,
            operation_type=OperationType.GET_KNOWLEDGE_BASE,
            description=f"Retrieve KB {kb_id}"
        ):
            # Validate inputs
            if not kb_id or not kb_id.strip():
                raise ValueError("kb_id cannot be empty")

            try:
                with self._db_lock:
                    with get_db_session() as db:
                        kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one({
                            "_id": kb_id,
                            "deleted": {"$ne": True}
                        })

                        # Log the DB query
                        log_service(
                            ServiceType.MONGODB,
                            estimated_cost_usd=0.01,
                            description="Query KB entry",
                            breakdown={"operation": "find_one", "indexed": True}
                        )

                if not kb_entry:
                    # Check if deleted
                    with get_db_session() as db:
                        deleted_kb = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one({
                            "_id": kb_id,
                            "deleted": True
                        })

                    if deleted_kb:
                        raise ValueError(f"KB {kb_id} has been deleted")
                    else:
                        raise ValueError(f"KB {kb_id} not found")

                # Transform response
                kb_entry["kb_id"] = kb_entry.pop("_id", None)
                logger.info(f"Retrieved KB {kb_id}")

                return kb_entry

            except ValueError:
                # Validation errors are re-raised
                # Operation automatically marked as FAILED
                raise

            # On exit: operation automatically logged
            # No manual logging needed
```

## Benefits of Automatic Operation Logging

### Before (Manual)
```python
# Lots of boilerplate code:
try:
    db[Config.OPERATION_LOGS_COLLECTION].insert_one(
        Operation(
            user_id_or_api_key=api_key,
            operation_type=OperationType.CREATE_KNOWLEDGE_BASE,
            services_used=[],
            estimated_cost_usd=0.0,
            status=TaskStatus.COMPLETED,
            description=f"Created KB {kb_id}"
        ).model_dump()
    )
except Exception as log_error:
    logger.error(f"Failed to log operation: {str(log_error)}")
```

### After (Automatic)
```python
# Just wrap operation:
with OperationContext(api_key, OperationType.CREATE_KNOWLEDGE_BASE):
    # operation automatically created, tracked, and logged
    pass
```

## Tracking Operations and Services

### Example Trace

```
API Call: POST /knowledge-base/create with api_key="user123"
  ↓
@operation_endpoint creates OperationContext(user123, CREATE_KNOWLEDGE_BASE)
  ↓
Operation._id = "uuid-123" created and set as current
  ↓
create_knowledge_base() called
  ↓
DB insert: log_service(MONGODB, cost=0.02)
  ↓
Service logged: Service._id = "uuid-456"
Operation.services_used = ["uuid-456"]
Operation.estimated_cost_usd = 0.02
  ↓
Return response
  ↓
@operation_endpoint catches completion
Operation.status = COMPLETED
Operation.completed_at = now()
  ↓
Operation logged to database
OPERATION_LOGS_COLLECTION insert: {"_id": "uuid-123", ..., "services_used": ["uuid-456"]}
SERVICE_LOGS_COLLECTION insert: {"_id": "uuid-456", ...}
```

## Database Records

### OPERATION_LOGS_COLLECTION
```json
{
  "_id": "uuid-123",
  "user_id_or_api_key": "user123",
  "operation_type": "create_knowledge_base",
  "services_used": ["uuid-456"],
  "estimated_cost_usd": 0.02,
  "actual_cost_usd": 0.02,
  "status": "completed",
  "created_at": "2025-12-03T10:00:00Z",
  "completed_at": "2025-12-03T10:00:00.500Z",
  "description": "Create KB 'My KB'",
  "metadata": null
}
```

### SERVICE_LOGS_COLLECTION
```json
{
  "_id": "uuid-456",
  "user_id_or_api_key": "user123",
  "service_type": "mongodb",
  "breakdown": {
    "operation": "insert_one",
    "collection": "knowledge_bases"
  },
  "estimated_cost_usd": 0.02,
  "actual_cost_usd": 0.02,
  "status": "completed",
  "created_at": "2025-12-03T10:00:00Z",
  "completed_at": "2025-12-03T10:00:00Z",
  "description": "Inserted KB entry",
  "metadata": null
}
```

## Migration Path

1. **Add @operation_endpoint to endpoints** - Enables automatic operation tracking
2. **Replace manual Operation() logging with OperationContext** - Cleaner code
3. **Add log_service() calls for all services** - Automatic cost tracking
4. **Remove manual operation logging code** - Reduces boilerplate

## Code Simplification

### Original create_knowledge_base: ~170 lines
```python
def create_knowledge_base(self, ...):
    error_message = None
    created_at = datetime.now(timezone.utc).isoformat()
    error_message = None

    try:
        # Validation...
        if not user_id_or_api_key or not user_id_or_api_key.strip():
            error_message = "..."
            raise ValueError(...)

        # ... more validation ...

        # Create entry...
        entry = {...}

        try:
            with self._db_lock:
                with get_db_session() as db:
                    # Insert...
                    db[...].insert_one(entry)

                    # Log operation...
                    db[Config.OPERATION_LOGS_COLLECTION].insert_one(
                        Operation(...).model_dump()
                    )

        except DuplicateKeyError:
            # Log error...
            try:
                db[Config.OPERATION_LOGS_COLLECTION].insert_one(...)
            except Exception as log_error:
                logger.error(...)
            raise

        except Exception as e:
            # Log error...
            try:
                db[Config.OPERATION_LOGS_COLLECTION].insert_one(...)
            except Exception as log_error:
                logger.error(...)
            raise
```

### Simplified with operation_logging: ~50 lines
```python
with OperationContext(user_id_or_api_key, OperationType.CREATE_KNOWLEDGE_BASE):
    # Validation...
    if not user_id_or_api_key or not user_id_or_api_key.strip():
        raise ValueError("...")

    # ... more validation ...

    # Create entry...
    entry = {...}

    with self._db_lock:
        with get_db_session() as db:
            db[Config.KNOWLEDGE_BASES_COLLECTION].insert_one(entry)
            log_service(ServiceType.MONGODB, estimated_cost_usd=0.02)

    # On exit: operation automatically logged
    # Status: COMPLETED or FAILED based on exceptions
```

**Result: 120 lines of code reduction, same functionality, better error handling!**
