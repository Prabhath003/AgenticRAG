# Operation Updates Guide: Get & Store Operation/Service IDs

This guide shows how to work with operation IDs, service IDs, and the new operation update features.

---

## Getting Service ID from log_service()

The `log_service()` function returns the created Service object, which includes the service ID.

### Basic Usage

```python
from src.infrastructure.operation_logging import log_service, ServiceType

# log_service() returns the Service object
service = log_service(
    ServiceType.FILE_PROCESSOR,
    estimated_cost_usd=0.15,
    description="Chunked documents"
)

# Access the service ID
if service:
    service_id = service._id
    print(f"Created service: {service_id}")
```

### Store Service ID in Metadata

```python
from src.infrastructure.operation_logging import (
    log_service,
    update_operation_metadata,
    ServiceType
)

# Create service and get its ID
service = log_service(
    ServiceType.FILE_PROCESSOR,
    estimated_cost_usd=0.15
)

if service:
    # Store service ID in operation metadata
    update_operation_metadata({
        "file_processor_service_id": service._id,
        "chunks_processed": 100
    })
```

### Store Multiple Service IDs

```python
services_created = {}

# Process multiple files
for file in files:
    service = log_service(
        ServiceType.FILE_PROCESSOR,
        estimated_cost_usd=0.15,
        description=f"Chunked {file.name}"
    )
    if service:
        services_created[file.name] = service._id

# Store all service IDs in metadata
update_operation_metadata({
    "services_by_file": services_created,
    "total_files": len(services_created)
})
```

---

## Getting Operation ID

### Quick Access

```python
from src.infrastructure.operation_logging import get_operation_id

operation_id = get_operation_id()
# Returns: "550e8400-e29b-41d4-a716-446655440000" or None
```

### Return in API Response

```python
from fastapi import FastAPI
from src.infrastructure.operation_logging import (
    operation_endpoint,
    log_service,
    get_operation_id,
    OperationType,
    ServiceType,
)

app = FastAPI()

@app.post("/api/knowledge-base/create")
@operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
async def create_kb(request, api_key: str):
    # Do work
    log_service(ServiceType.MONGODB, 0.02)

    # Get operation ID for response
    operation_id = get_operation_id()

    return {
        "success": True,
        "kb_id": "kb-123",
        "operation_id": operation_id,  # ← Return to client
    }
```

---

## Updating Operation Metadata

Update operation metadata anytime during execution. Metadata is merged (not replaced).

### Add Metadata

```python
from src.infrastructure.operation_logging import update_operation_metadata

update_operation_metadata({
    "kb_title": "My Knowledge Base",
    "source_count": 5,
    "file_processor_id": "svc-123"
})
```

### Update Metadata Multiple Times

```python
# Initial metadata
update_operation_metadata({
    "kb_title": "My KB"
})

# Later, add more data
update_operation_metadata({
    "doc_count": 50,
    "chunk_count": 1000
})

# Result: {
#   "kb_title": "My KB",
#   "doc_count": 50,
#   "chunk_count": 1000
# }
```

### Store Service IDs in Metadata

```python
# Create services and collect IDs
service_ids = []

for item in items:
    service = log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.01
    )
    if service:
        service_ids.append(service._id)

# Store all service IDs
update_operation_metadata({
    "service_ids": service_ids,
    "total_services": len(service_ids)
})
```

---

## Updating Operation Description

Change or append to the operation description.

### Replace Description

```python
from src.infrastructure.operation_logging import update_operation_description

# Replace entire description
update_operation_description("Processing completed with 50 documents")
```

### Append to Description

```python
# Start with initial description
update_operation_description("Started processing")

# Later, append more info
update_operation_description("Chunking completed", append=True)
update_operation_description("Classification done", append=True)

# Result: "Started processing; Chunking completed; Classification done"
```

### Build Description Progressively

```python
from src.infrastructure.operation_logging import (
    update_operation_description,
    log_service,
    ServiceType
)

@operation_endpoint(OperationType.KNOWLEDGE_BASE_BUILD)
async def build_kb(kb_id: str, api_key: str):
    try:
        update_operation_description("Starting KB build")

        log_service(ServiceType.FILE_PROCESSOR, 0.15)
        update_operation_description("Chunked documents", append=True)

        log_service(ServiceType.KNOWLEDGE_BASE_BUILD, 0.20)
        update_operation_description("Built knowledge bases", append=True)

        return {"status": "success"}

    except Exception as e:
        update_operation_description(f"Error: {str(e)}", append=True)
        raise
```

---

## Getting Operation with Services (Breakdown Pattern)

Return operation data with optional service breakdown.

### Without Breakdown (Simple)

```python
from src.infrastructure.operation_logging import get_operation_with_services

@app.get("/api/task/{task_id}/status")
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_task_status(task_id: str, api_key: str):
    # Simple response: just operation data
    return get_operation_with_services(get_breakdown=False)

# Response:
# {
#   "operation_id": "op-123",
#   "operation_type": "get_knowledge_base",
#   "status": "completed",
#   "estimated_cost_usd": 0.02,
#   "created_at": "2025-12-03T10:00:00.000Z"
# }
```

### With Breakdown (Detailed)

```python
@app.get("/api/task/{task_id}/status")
@app.get("/api/task/{task_id}/status", params=[{"name": "details", "in": "query", "schema": {"type": "boolean", "default": False}}])
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_task_status(task_id: str, details: bool = False, api_key: str = ""):
    # Return breakdown only if requested
    return get_operation_with_services(get_breakdown=details)

# Without details (get_breakdown=False):
# {
#   "operation_id": "op-123",
#   "operation_type": "get_knowledge_base",
#   "status": "completed",
#   "estimated_cost_usd": 0.02,
#   "created_at": "2025-12-03T10:00:00.000Z"
# }

# With details=true (get_breakdown=True):
# {
#   "operation_id": "op-123",
#   "operation_type": "get_knowledge_base",
#   "status": "completed",
#   "estimated_cost_usd": 0.02,
#   "created_at": "2025-12-03T10:00:00.000Z",
#   "services": [
#     {
#       "_id": "svc-1",
#       "operation_id": "op-123",
#       "service_type": "mongodb",
#       "estimated_cost_usd": 0.01,
#       "description": "Queried KB",
#       "created_at": "2025-12-03T10:00:00.100Z"
#     },
#     {
#       "_id": "svc-2",
#       "operation_id": "op-123",
#       "service_type": "file_processor",
#       "estimated_cost_usd": 0.01,
#       "description": "Chunked docs",
#       "created_at": "2025-12-03T10:00:00.200Z"
#     }
#   ],
#   "service_count": 2,
#   "total_service_cost": 0.02
# }
```

---

## Complete Example: Task with Get Breakdown

Here's a complete example showing all the features together:

```python
from fastapi import FastAPI, Depends
from src.infrastructure.operation_logging import (
    operation_endpoint,
    log_service,
    get_operation_id,
    update_operation_metadata,
    update_operation_description,
    get_operation_with_services,
    OperationType,
    ServiceType,
)

app = FastAPI()

class Task:
    """Simulated task object"""
    pass

@app.post("/api/task/create")
@operation_endpoint(
    OperationType.CREATE_KNOWLEDGE_BASE,
    description_formatter=lambda request, **kw: f"Create task: {request.title}"
)
async def create_task(request, api_key: str):
    """Create a task and return operation info"""

    # Log initial work
    service = log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.02,
        description="Created task entry"
    )

    # Get operation ID for response
    operation_id = get_operation_id()

    # Store service info in metadata
    if service:
        update_operation_metadata({
            "mongodb_service_id": service._id,
            "task_title": request.title,
            "source_count": len(request.sources) if hasattr(request, 'sources') else 0
        })

    return {
        "success": True,
        "task_id": "task-123",
        "operation_id": operation_id,
    }


@app.get("/api/task/{task_id}/status")
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_task_status(
    task_id: str,
    get_breakdown: bool = False,
    api_key: str = ""
):
    """Get task status with optional service breakdown"""

    # Log the lookup
    log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.01,
        description=f"Fetched task {task_id}"
    )

    # Update metadata with task info
    update_operation_metadata({
        "task_id": task_id,
        "lookup_timestamp": "2025-12-03T10:00:00Z"
    })

    # Return based on get_breakdown flag
    return get_operation_with_services(get_breakdown=get_breakdown)

    # If get_breakdown=False:
    # {
    #   "operation_id": "op-456",
    #   "operation_type": "get_knowledge_base",
    #   "status": "pending",
    #   "estimated_cost_usd": 0.01,
    #   "created_at": "2025-12-03T10:00:00.000Z"
    # }

    # If get_breakdown=True:
    # {
    #   "operation_id": "op-456",
    #   "operation_type": "get_knowledge_base",
    #   "status": "pending",
    #   "estimated_cost_usd": 0.01,
    #   "created_at": "2025-12-03T10:00:00.000Z",
    #   "services": [...],
    #   "service_count": 1,
    #   "total_service_cost": 0.01
    # }
```

---

## Storing Service IDs in Other Objects

When you need to link services to other entities (tasks, documents, etc.):

### Pattern 1: Store in Metadata

```python
# During operation
service1 = log_service(ServiceType.FILE_PROCESSOR, 0.10)
service2 = log_service(ServiceType.KNOWLEDGE_BASE_BUILD, 0.20)

if service1 and service2:
    update_operation_metadata({
        "processing_services": {
            "file_processor": service1._id,
            "b_build": service2._id
        }
    })
```

### Pattern 2: Store in Document

```python
from src.infrastructure.database import get_db_session
from src.config import Config

# Create service
service = log_service(ServiceType.FILE_PROCESSOR, 0.15)

if service:
    # Store service ID in task document
    with get_db_session() as db:
        db[Config.TASKS_COLLECTION].update_one(
            {"_id": task_id},
            {
                "$set": {
                    "service_id": service._id,
                    "processed_at": datetime.now(timezone.utc)
                }
            }
        )
```

### Pattern 3: Store Array of Service IDs

```python
service_ids = []

for item in items:
    service = log_service(ServiceType.MONGODB, 0.01)
    if service:
        service_ids.append(service._id)

# Store array in operation metadata
update_operation_metadata({
    "service_ids": service_ids,
    "service_count": len(service_ids)
})

# Or in a document
with get_db_session() as db:
    db[Config.ITEMS_COLLECTION].update_one(
        {"_id": parent_id},
        {"$set": {"service_ids": service_ids}}
    )
```

---

## API Response Patterns

### Pattern 1: Minimal Response

```python
@app.get("/api/resource/{id}")
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_resource(id: str, api_key: str):
    resource = db.get(id)

    return {
        "id": id,
        "data": resource,
        "operation_id": get_operation_id()  # ← Include for tracking
    }
```

### Pattern 2: Response with Status

```python
@app.get("/api/task/{id}")
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_task(id: str, api_key: str):
    task = db.get_task(id)
    op_data = get_operation_with_services(get_breakdown=False)

    return {
        "task_id": id,
        "status": task.status,
        "operation": op_data  # ← Nested operation info
    }
```

### Pattern 3: Response with Optional Breakdown

```python
@app.get("/api/task/{id}")
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_task(id: str, details: bool = False, api_key: str = ""):
    task = db.get_task(id)

    if details:
        # Heavy response with all services
        return get_operation_with_services(get_breakdown=True)
    else:
        # Light response
        return {
            "task_id": id,
            "operation_id": get_operation_id()
        }
```

---

## Best Practices

### ✓ DO

```python
# ✓ Store service IDs in metadata for tracking
service = log_service(ServiceType.MONGODB, 0.01)
if service:
    update_operation_metadata({"service_id": service._id})

# ✓ Update metadata progressively
update_operation_metadata({"step_1": "done"})
update_operation_metadata({"step_2": "done"})  # Merges with step_1

# ✓ Update description for audit trail
update_operation_description("Initial processing")
update_operation_description("Completed", append=True)

# ✓ Return operation_id in API responses
return {
    "success": True,
    "operation_id": get_operation_id()
}
```

### ❌ DON'T

```python
# ❌ Don't overwrite metadata manually
operation.metadata = {"new": "value"}  # Loses existing data

# ❌ Don't assume log_service() returns None
service = log_service(...)
if service:  # ✓ Always check
    service_id = service._id

# ❌ Don't call functions outside operation context
op_id = get_operation_id()  # ❌ Returns None if no context

# ❌ Within context manager:
with OperationContext(...):
    op_id = get_operation_id()  # ✓ Works inside context
```

---

## Troubleshooting

### Issue: get_operation_id() returns None

**Cause**: Not inside operation context

**Solution**: Ensure function is called inside:
```python
# ✓ Inside @operation_endpoint decorated function
@operation_endpoint(...)
async def endpoint(...):
    op_id = get_operation_id()  # ✓ Works

# ✓ Inside with OperationContext block
with OperationContext(...):
    op_id = get_operation_id()  # ✓ Works

# ❌ Outside operation context
op_id = get_operation_id()  # ❌ Returns None
```

### Issue: log_service() returns None

**Cause**: Outside operation context

**Solution**: Ensure log_service() is called inside operation context (same as above)

### Issue: update_operation_metadata() not working

**Cause**: Calling outside context or after operation already logged

**Solution**: Call before operation is logged (before context exit)
```python
@operation_endpoint(...)
async def endpoint(...):
    # ✓ Updates in-memory, before logging
    update_operation_metadata({"key": "value"})
    # operation logged with metadata on exit
```

For deferred completion:
```python
@operation_endpoint(..., auto_complete=False)
async def endpoint(...):
    # ✓ Updates before logging
    update_operation_metadata({"key": "value"})
    # operation not logged yet

# ✓ Background task - still updates before manual logging
mark_operation_complete()  # Logs with updated metadata
```

### Issue: get_operation_with_services() returns empty services list

**Cause 1**: No services logged yet
**Solution**: Call log_service() before get_operation_with_services()

**Cause 2**: Service was logged but with operation_id mismatch
**Solution**: Ensure log_service() is called inside same operation context

---

## Summary

| Function | Purpose | Returns |
|----------|---------|---------|
| `log_service()` | Create service log | Service object (with `._id`) |
| `get_operation_id()` | Get current operation ID | String or None |
| `update_operation_metadata()` | Merge metadata | Operation ID or None |
| `update_operation_description()` | Update description | Operation ID or None |
| `get_operation_with_services()` | Get operation ± services | Dict or None |

All functions are context-aware and work automatically within `@operation_endpoint` or `OperationContext`.
