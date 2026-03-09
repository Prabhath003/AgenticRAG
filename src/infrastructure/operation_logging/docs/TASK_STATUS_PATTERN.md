# Task Status Pattern with Breakdown

Complete implementation for task status endpoints that return different data based on a `get_breakdown` flag.

---

## Overview

This pattern is perfect for endpoints like `/task/{id}/status` where:
- **Without breakdown** (`get_breakdown=False`): Return lightweight response with just operation_id and status
- **With breakdown** (`get_breakdown=True`): Return detailed response with all services and costs

---

## Quick Example

```python
from fastapi import FastAPI
from src.infrastructure.operation_logging import (
    operation_endpoint,
    log_service,
    get_operation_with_services,
    OperationType,
    ServiceType,
)

app = FastAPI()

@app.get("/api/task/{task_id}/status")
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_task_status(
    task_id: str,
    get_breakdown: bool = False,  # ← Query parameter for breakdown
    api_key: str = ""
):
    """Get task status with optional cost breakdown"""

    # Log the lookup operation
    log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.01,
        description=f"Fetched task {task_id}"
    )

    # Return based on get_breakdown flag
    return get_operation_with_services(get_breakdown=get_breakdown)
```

---

## API Response Examples

### Response Without Breakdown (Lightweight)

**Request:**
```
GET /api/task/task-123/status
```

**Response:**
```json
{
  "operation_id": "op-550e8400-e29b-41d4-a716-446655440000",
  "operation_type": "get_knowledge_base",
  "status": "completed",
  "estimated_cost_usd": 0.01,
  "created_at": "2025-12-03T10:00:00.000Z"
}
```

### Response With Breakdown (Detailed)

**Request:**
```
GET /api/task/task-123/status?get_breakdown=true
```

**Response:**
```json
{
  "operation_id": "op-550e8400-e29b-41d4-a716-446655440000",
  "operation_type": "get_knowledge_base",
  "status": "completed",
  "estimated_cost_usd": 0.01,
  "created_at": "2025-12-03T10:00:00.000Z",
  "services": [
    {
      "_id": "svc-1-mongodb",
      "operation_id": "op-550e8400-e29b-41d4-a716-446655440000",
      "user_id_or_api_key": "user@example.com",
      "service_type": "mongodb",
      "breakdown": {},
      "estimated_cost_usd": 0.01,
      "actual_cost_usd": 0.01,
      "status": "completed",
      "created_at": "2025-12-03T10:00:00.100Z",
      "completed_at": "2025-12-03T10:00:00.100Z",
      "description": "Fetched task task-123"
    }
  ],
  "service_count": 1,
  "total_service_cost": 0.01
}
```

---

## Complete Example: Task Management API

Here's a complete task management system using the breakdown pattern:

```python
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
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
from src.infrastructure.database import get_db_session
from src.config import Config

app = FastAPI()

# Models
class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    sources: Optional[List[str]] = []

class TaskResponse(BaseModel):
    task_id: str
    operation_id: str
    title: str
    status: str
    created_at: str

class TaskStatusResponse(BaseModel):
    operation_id: str
    operation_type: str
    status: str
    estimated_cost_usd: float
    created_at: str
    services: Optional[List[dict]] = None
    service_count: Optional[int] = None
    total_service_cost: Optional[float] = None

# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/task/create", response_model=TaskResponse)
@operation_endpoint(
    OperationType.CREATE_KNOWLEDGE_BASE,
    description_formatter=lambda request, **kw: f"Create task: {request.title}"
)
async def create_task(request: TaskCreate, api_key: str):
    """
    Create a new task.

    Operation auto-logs on exit with operation_id in response.
    """

    # Log MongoDB operation
    service = log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.02,
        description="Created task entry"
    )

    # Get operation ID for response
    operation_id = get_operation_id()

    # Store metadata
    if service:
        update_operation_metadata({
            "mongodb_service_id": service._id,
            "title": request.title,
            "source_count": len(request.sources) if request.sources else 0,
        })

    # Generate task ID
    task_id = f"task-{operation_id[:8]}"

    # In real app, save to database
    # db.tasks.insert_one({...})

    return TaskResponse(
        task_id=task_id,
        operation_id=operation_id,
        title=request.title,
        status="created",
        created_at=datetime.now(timezone.utc).isoformat()
    )

# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/task/{task_id}/status")
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_task_status(
    task_id: str,
    get_breakdown: bool = False,
    api_key: str = ""
) -> dict:
    """
    Get task status with optional service breakdown.

    GET /api/task/task-123/status
        → Lightweight response

    GET /api/task/task-123/status?get_breakdown=true
        → Detailed response with all services
    """

    # Validate task exists
    with get_db_session() as db:
        task = db[Config.TASKS_COLLECTION].find_one({"_id": task_id})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

    # Log the lookup
    service = log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.01,
        description=f"Fetched task status for {task_id}"
    )

    # Update metadata with task info
    update_operation_metadata({
        "task_id": task_id,
        "task_status": task.get("status", "unknown"),
        "lookup_type": "with_breakdown" if get_breakdown else "simple"
    })

    # Return based on breakdown flag
    return get_operation_with_services(get_breakdown=get_breakdown)

# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/task/{task_id}/trigger-build")
@operation_endpoint(
    OperationType.KNOWLEDGE_BASE_BUILD,
    description_formatter=lambda task_id, **kw: f"Trigger build for {task_id}",
    auto_complete=False  # ← Deferred completion
)
async def trigger_task_build(task_id: str, api_key: str):
    """
    Trigger background build for a task.

    Uses deferred completion pattern:
    - Endpoint returns immediately
    - Operation not logged yet
    - Background task logs when complete
    """
    from contextvars import copy_context
    from concurrent.futures import ThreadPoolExecutor

    executor = ThreadPoolExecutor(max_workers=4)

    # Get operation ID
    operation_id = get_operation_id()

    # Log endpoint work
    log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.01,
        description="Fetched task to check status"
    )

    # Update metadata with task info
    update_operation_metadata({
        "task_id": task_id,
        "trigger_timestamp": datetime.now(timezone.utc).isoformat()
    })

    # Submit background build with context
    context = copy_context()
    executor.submit(context.run, background_build_task, task_id, api_key)

    # Return immediately (operation NOT logged yet)
    return {
        "message": "Task build queued",
        "task_id": task_id,
        "operation_id": operation_id,
        "status": "queued"
    }

# ═══════════════════════════════════════════════════════════════════════════════

def background_build_task(task_id: str, api_key: str):
    """
    Background task - runs in thread pool with inherited operation context.

    Logs services and calls mark_operation_complete() when done.
    """
    from src.infrastructure.operation_logging import (
        log_service,
        mark_operation_complete,
        mark_operation_failed,
        update_operation_description,
    )

    try:
        # Long-running work
        update_operation_description("Starting background build")

        # Simulate file processing
        log_service(
            ServiceType.FILE_PROCESSOR,
            estimated_cost_usd=0.15,
            description="Chunked documents"
        )
        update_operation_description("Chunked documents", append=True)

        # Simulate knowledge base building
        log_service(
            ServiceType.KNOWLEDGE_BASE_BUILD,
            estimated_cost_usd=0.20,
            description="Built knowledge baes"
        )
        update_operation_description("Built knowledge bases", append=True)

        # Mark complete (logs operation to database)
        mark_operation_complete()
        update_operation_description("Build completed successfully", append=True)

    except Exception as e:
        # Mark failed (logs operation to database with error)
        mark_operation_failed(f"Build failed: {str(e)}")
        update_operation_description(f"Error: {str(e)}", append=True)
        raise

# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/task/{task_id}/status")
@operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
async def get_task_build_status(
    task_id: str,
    get_breakdown: bool = False,
    api_key: str = ""
) -> dict:
    """
    Get task build status.

    Shows status of the background build operation.
    Optionally returns all services used in the build.
    """

    # Log the status check
    log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.01,
        description=f"Checked build status for {task_id}"
    )

    # Return operation data (with or without services)
    return get_operation_with_services(get_breakdown=get_breakdown)
```

---

## Real-World Usage Scenarios

### Scenario 1: Client Checks Task Status (Lightweight)

**Client Code:**
```javascript
// JavaScript client
async function checkTaskStatus(taskId) {
    const response = await fetch(
        `/api/task/${taskId}/status`,
        { headers: { 'Authorization': `Bearer ${apiKey}` } }
    );
    const data = await response.json();
    console.log(`Task status: ${data.status}`);
    return data.operation_id; // For tracking
}
```

**API Returns:**
```json
{
  "operation_id": "op-123",
  "operation_type": "get_knowledge_base",
  "status": "completed",
  "estimated_cost_usd": 0.01,
  "created_at": "2025-12-03T10:00:00.000Z"
}
```

### Scenario 2: Admin Dashboard (Detailed Breakdown)

**Client Code:**
```javascript
// Get detailed breakdown for dashboard
async function getDashboardTaskInfo(taskId) {
    const response = await fetch(
        `/api/task/${taskId}/status?get_breakdown=true`,
        { headers: { 'Authorization': `Bearer ${adminApiKey}` } }
    );
    return response.json();
}
```

**API Returns:**
```json
{
  "operation_id": "op-123",
  "status": "completed",
  "estimated_cost_usd": 0.36,
  "services": [
    { "service_type": "mongodb", "estimated_cost_usd": 0.01 },
    { "service_type": "file_processor", "estimated_cost_usd": 0.15 },
    { "service_type": "knowledge_base_build", "estimated_cost_usd": 0.20 }
  ],
  "service_count": 3,
  "total_service_cost": 0.36
}
```

### Scenario 3: Billing System (Cost Aggregation)

```python
# Aggregate costs for user
with get_db_session() as db:
    # Find all operations for user
    ops = db[Config.OPERATION_LOGS_COLLECTION].find({
        "user_id_or_api_key": "user@example.com"
    })

    total_cost = 0
    for op in ops:
        # Either use operation estimated_cost
        total_cost += op.get("estimated_cost_usd", 0)

        # Or aggregate services
        services = db[Config.SERVICE_LOGS_COLLECTION].find({
            "operation_id": op["_id"]
        })
        service_cost = sum(s.get("estimated_cost_usd", 0) for s in services)
        total_cost += service_cost
```

---

## Storage Pattern: Service IDs in Task Metadata

When you want to store which services were used by a task:

```python
@app.post("/api/task/create")
@operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
async def create_task(request: TaskCreate, api_key: str):
    """Create task and store service IDs in metadata"""

    # Create task and log services
    services_used = {}

    # Service 1: MongoDB
    service1 = log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.02,
        description="Inserted task"
    )
    if service1:
        services_used["mongodb"] = service1._id

    # Service 2: File processor
    service2 = log_service(
        ServiceType.FILE_PROCESSOR,
        estimated_cost_usd=0.10,
        description="Processed files"
    )
    if service2:
        services_used["file_processor"] = service2._id

    # Store all service IDs in operation metadata
    update_operation_metadata({
        "services_by_type": services_used,
        "title": request.title,
        "service_count": len(services_used)
    })

    return TaskResponse(
        task_id=f"task-{get_operation_id()[:8]}",
        operation_id=get_operation_id(),
        title=request.title,
        status="created",
        created_at=datetime.now(timezone.utc).isoformat()
    )
```

---

## Database Queries for Task Status

### Get Operation with Services

```python
# Find operation
operation = db[OPERATION_LOGS_COLLECTION].find_one({
    "_id": operation_id
})

# Get all services
services = list(db[SERVICE_LOGS_COLLECTION].find({
    "operation_id": operation_id
}))

# Calculate totals
total_cost = sum(s["estimated_cost_usd"] for s in services)
```

### Query by Task ID (from metadata)

```python
# Find operations for a task
ops = db[OPERATION_LOGS_COLLECTION].find({
    "metadata.task_id": task_id
})

# Get services for all operations
all_services = []
for op in ops:
    services = db[SERVICE_LOGS_COLLECTION].find({
        "operation_id": op["_id"]
    })
    all_services.extend(services)
```

---

## Summary

| Aspect | Implementation |
|--------|---|
| **Get breakdown flag** | Add `get_breakdown: bool = False` query parameter |
| **Without breakdown** | Return `get_operation_with_services(get_breakdown=False)` |
| **With breakdown** | Return `get_operation_with_services(get_breakdown=True)` |
| **Store service IDs** | Use `update_operation_metadata({"service_ids": [...]})` |
| **Return operation_id** | Use `get_operation_id()` in response |
| **Background tasks** | Use deferred completion with `auto_complete=False` |
| **Log background work** | Call `log_service()` in background task |

This pattern provides flexible, efficient task status endpoints with optional detailed breakdowns!
