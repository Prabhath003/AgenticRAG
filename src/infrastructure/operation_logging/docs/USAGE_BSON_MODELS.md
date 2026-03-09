# Quick Reference: Using BSON Models

## Imports

```python
# ✓ Recommended: Use from operation_logging
from src.infrastructure.operation_logging import (
    Operation,
    Service,
    OperationType,
    ServiceType,
    TaskStatus,
    OperationContext,
    operation_endpoint,
    log_service,
)
```

## Creating Operations

### In FastAPI Endpoint (with decorator)

```python
from fastapi import FastAPI, Depends
from src.infrastructure.operation_logging import operation_endpoint, log_service, OperationType, ServiceType

app = FastAPI()

@app.post("/knowledge-base/create")
@operation_endpoint(
    OperationType.CREATE_KNOWLEDGE_BASE,
    description_formatter=lambda request, **kw: f"Create: {request.title}"
)
async def create_knowledge_base(request, api_key: str):
    # Operation automatically created

    log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.02,
        description="Insert KB entry"
    )

    # Operation automatically logged to database on exit
    return {"success": True, "kb_id": "kb-123"}
```

### Manual Context Manager

```python
from src.infrastructure.operation_logging import OperationContext, log_service, OperationType, ServiceType

with OperationContext(api_key, OperationType.CREATE_KNOWLEDGE_BASE) as op:
    log_service(ServiceType.MONGODB, 0.02)
    # operation automatically logged on exit
```

### Deferred Completion (for background tasks)

```python
from contextvars import copy_context
from src.infrastructure.operation_logging import (
    operation_endpoint,
    log_service,
    mark_operation_complete,
    mark_operation_failed,
    OperationType,
    ServiceType,
)

@app.post("/knowledge-base/{kb_id}/build")
@operation_endpoint(
    OperationType.KNOWLEDGE_BASE_BUILD,
    auto_complete=False  # Don't auto-log on endpoint exit
)
async def trigger_build(kb_id: str, api_key: str):
    log_service(ServiceType.MONGODB, 0.01)

    context = copy_context()
    executor.submit(context.run, background_build, kb_id, api_key)

    return {"status": "queued"}
    # Operation NOT logged yet

def background_build(kb_id: str, api_key: str):
    try:
        log_service(ServiceType.FILE_PROCESSOR, 0.15)
        # ... long-running work ...
        mark_operation_complete()  # Logs operation when done
    except Exception as e:
        mark_operation_failed(str(e))  # Logs operation with error
```

## Creating and Logging Services

### Automatic Logging (inside operation context)

```python
from src.infrastructure.operation_logging import log_service, ServiceType

log_service(
    ServiceType.FILE_PROCESSOR,
    estimated_cost_usd=0.15,
    breakdown={"chunks": 100, "cost_per_chunk": 0.0015},
    description="Chunked 100 documents",
    metadata={"source": "pdf"}
)
```

## Accessing Operation Data

### Get Current Operation

```python
from src.infrastructure.operation_logging import get_current_operation

op = get_current_operation()
if op:
    print(f"Operation ID: {op._id}")
    print(f"Status: {op.status.value}")
    print(f"Cost: ${op.estimated_cost_usd:.2f}")
    print(f"Duration: {op.get_duration_seconds()}s")
```

### Get Operation Summary

```python
from src.infrastructure.operation_logging import get_operation_summary

summary = get_operation_summary()
# Returns: {
#     "operation_id": "...",
#     "operation_type": "create_knowledge_base",
#     "status": "completed",
#     "estimated_cost_usd": 0.17,
#     "actual_cost_usd": 0.17,
#     "duration_seconds": 1.234,
#     "created_at": "2025-12-03T10:00:00.000Z",
#     "completed_at": "2025-12-03T10:00:01.234Z"
# }
```

### Get Operation Stack (nested contexts)

```python
from src.infrastructure.operation_logging import get_operation_stack

stack = get_operation_stack()
for i, op in enumerate(stack):
    print(f"Level {i}: {op.operation_type.value}")
```

## Database Queries

### Find Operations

```python
from src.infrastructure.database import get_db_session
from src.config import Config

with get_db_session() as db:
    # Find single operation
    op_doc = db[Config.OPERATION_LOGS_COLLECTION].find_one({
        "_id": "op-123"
    })

    # Find by user
    user_ops = db[Config.OPERATION_LOGS_COLLECTION].find({
        "user_id_or_api_key": "user@example.com"
    })

    # Find completed operations
    completed = db[Config.OPERATION_LOGS_COLLECTION].find({
        "status": "completed"
    })

    # Find operations by type
    create_ops = db[Config.OPERATION_LOGS_COLLECTION].find({
        "operation_type": "create_knowledge_base"
    })
```

### Find Services for Operation

```python
# All services for an operation
services = db[Config.SERVICE_LOGS_COLLECTION].find({
    "operation_id": "op-123"
})

# By service type
file_services = db[Config.SERVICE_LOGS_COLLECTION].find({
    "operation_id": "op-123",
    "service_type": "file_processor"
})

# Calculate total cost
total_cost = sum(s["estimated_cost_usd"] for s in services)
```

### Advanced Aggregations

```python
# Costs by service type for a user
pipeline = [
    {"$match": {"user_id_or_api_key": "user@example.com"}},
    {"$group": {
        "_id": "$service_type",
        "count": {"$sum": 1},
        "total_cost": {"$sum": "$estimated_cost_usd"}
    }},
    {"$sort": {"total_cost": -1}}
]

results = db[Config.SERVICE_LOGS_COLLECTION].aggregate(pipeline)
for result in results:
    print(f"{result['_id']}: {result['count']} calls, ${result['total_cost']:.2f}")
```

## Status Values

```python
from src.infrastructure.operation_logging import TaskStatus

# Available statuses
TaskStatus.PENDING      # "pending"
TaskStatus.QUEUED       # "queued"
TaskStatus.PROCESSING   # "processing"
TaskStatus.COMPLETED    # "completed"
TaskStatus.FAILED       # "failed"

# When querying MongoDB
db[OPERATION_LOGS_COLLECTION].find({"status": "completed"})
db[OPERATION_LOGS_COLLECTION].find({"status": TaskStatus.COMPLETED.value})
```

## Operation Types

```python
from src.infrastructure.operation_logging import OperationType

OperationType.CREATE_KNOWLEDGE_BASE
OperationType.GET_KNOWLEDGE_BASE
OperationType.DELETE_KNOWLEDGE_BASE
OperationType.UPDATE_KNOWLEDGE_BASE
OperationType.QUERY_KNOWLEDGE_BASE
OperationType.ADD_DOCUMENT
OperationType.DELETE_DOCUMENT
OperationType.CHAT
OperationType.EMBEDDING
```

## Service Types

```python
from src.infrastructure.operation_logging import ServiceType

ServiceType.MONGODB
ServiceType.S3_STORAGE
ServiceType.OPENAI
ServiceType.TRANSFORMER
ServiceType.FILE_PROCESSOR
ServiceType.KNOWLEDGE_BASE_BUILD
ServiceType.KNOWLEDGE_BASE_QUERY
ServiceType.OXYLABS
```

## Common Patterns

### Pattern 1: Quick Endpoint (Default)

```python
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_kb(kb_id: str, api_key: str):
    log_service(ServiceType.MONGODB, 0.01)
    return db.get_kb(kb_id)
    # ✓ Auto-logged on exit
```

### Pattern 2: Background Task (Deferred)

```python
@operation_endpoint(OperationType.KNOWLEDGE_BASE_BUILD, auto_complete=False)
async def trigger_build(kb_id: str, api_key: str):
    log_service(ServiceType.MONGODB, 0.01)
    context = copy_context()
    executor.submit(context.run, background_work, kb_id, api_key)
    return {"status": "queued"}

def background_work(kb_id: str, api_key: str):
    try:
        log_service(ServiceType.FILE_PROCESSOR, 0.15)
        # ... work ...
        mark_operation_complete()
    except Exception as e:
        mark_operation_failed(str(e))
```

### Pattern 3: Manual Context (Advanced)

```python
def process_data(api_key: str):
    with OperationContext(api_key, OperationType.EMBEDDING) as op:
        for item in items:
            log_service(ServiceType.TRANSFORMER, 0.05)
        # Operation auto-logged on exit
```

## Common Mistakes to Avoid

### ❌ Don't: Use old core_models imports

```python
from src.core.management.core_models import Operation  # ❌ Not BSON optimized
```

### ✓ Do: Use operation_logging imports

```python
from src.infrastructure.operation_logging import Operation  # ✓ BSON optimized
```

### ❌ Don't: Use model_dump() for MongoDB

```python
db[collection].insert_one(operation.model_dump())  # ❌ Not optimized
```

### ✓ Do: Use model_dump_bson()

```python
db[collection].insert_one(operation.model_dump_bson())  # ✓ Optimized
```

### ❌ Don't: Log services outside operation context

```python
log_service(ServiceType.MONGODB, 0.02)  # ❌ No operation active
```

### ✓ Do: Log services inside operation context

```python
@operation_endpoint(OperationType.GET_KB)
async def get_kb(...):
    log_service(ServiceType.MONGODB, 0.02)  # ✓ Operation active
```

### ❌ Don't: Forget mark_operation_complete() for deferred tasks

```python
@operation_endpoint(..., auto_complete=False)
async def trigger_build(...):
    executor.submit(context.run, background_work, ...)
    # ❌ Operation not logged if background_work doesn't call mark_operation_complete()
```

### ✓ Do: Always call mark_operation_complete/failed in background tasks

```python
def background_work(...):
    try:
        # ... work ...
        mark_operation_complete()  # ✓ Logs when done
    except Exception as e:
        mark_operation_failed(str(e))  # ✓ Logs on error
```
