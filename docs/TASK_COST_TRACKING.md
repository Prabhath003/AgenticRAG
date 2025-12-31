# Task Cost Tracking & Async Operations

## Overview

The request tracking system now supports linking async background tasks (like file uploads) to request metrics, allowing you to track the complete cost breakdown for any intensive operation using a `task_id`.

## Key Concepts

### Task ID
- Generated for async operations (e.g., file uploads)
- Returned immediately to client
- Used to track costs as operation progresses
- Persists in request metrics

### Document ID
- Associated with file operations
- Tracks which document generated the costs
- Used for granular cost analysis per uploaded file

### Request ID
- Generated for every API request
- Links to task_id if operation is async
- Stores complete service breakdown

## Flow for Async Upload Operations

```
1. Client uploads file
   ↓
2. Upload endpoint generates task_id & request_id
   ↓
3. Background task processes file
   ├─ Tracks FILE_PROCESSOR service
   ├─ Tracks TRANSFORMER service (encoding)
   └─ Accumulates costs
   ↓
4. Request marked with:
   ├─ request_id: Unique for this upload attempt
   ├─ task_id: Returned to client immediately
   ├─ doc_id: Final document identifier
   ├─ entity_id: Associated entity
   └─ services_used: All consumed services
   ↓
5. Client can query costs by task_id anytime
   ├─ Check progress
   ├─ Get cost so far
   ├─ View services breakdown
   └─ Monitor completion
```

## API Endpoints

### List Requests with Task Filter

```bash
# Get all requests for a specific upload task
curl "http://localhost:8000/api/requests?task_id=task_abc123"

# Get all requests for a specific document
curl "http://localhost:8000/api/requests?doc_id=doc_xyz789"

# Combine filters
curl "http://localhost:8000/api/requests?task_id=task_abc123&entity_id=company_123"
```

**Response:**
```json
{
  "requests": [
    {
      "request_id": "req_123abc",
      "task_id": "task_abc123",
      "doc_id": "doc_xyz789",
      "operation_type": "file_upload",
      "task_type": "upload",
      "entity_id": "company_123",
      "status_code": 200,
      "processing_time_ms": 3500,
      "services_used": [
        {
          "service_type": "file_processor",
          "cost_usd": 0.02,
          "breakdown": {"pages": 10}
        },
        {
          "service_type": "transformer",
          "cost_usd": 0.15,
          "breakdown": {"chunks": 45}
        }
      ],
      "total_cost_usd": 0.17
    }
  ],
  "total": 1
}
```

### Get Cost for Specific Task

```bash
# Get cost breakdown for a task
curl http://localhost:8000/api/tasks/task_abc123/cost
```

**Response:**
```json
{
  "task_id": "task_abc123",
  "total_cost_usd": 0.17,
  "period_start": "2025-01-15T10:30:45.123Z",
  "period_end": "2025-01-15T10:32:15.456Z",
  "breakdown_by_service": {
    "file_processor": 0.02,
    "transformer": 0.15
  },
  "request_count": 1
}
```

### Get Specific Request

```bash
# Get detailed metrics for a request
curl http://localhost:8000/api/requests/req_123abc
```

**Response:**
```json
{
  "request_id": "req_123abc",
  "task_id": "task_abc123",
  "doc_id": "doc_xyz789",
  "task_type": "upload",
  "operation_type": "file_upload",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "processing_time_ms": 3500,
  "entity_id": "company_123",
  "services_used": [...]
}
```

## Implementation in Upload Endpoint

Here's how to integrate task tracking in your file upload endpoint:

```python
from src.infrastructure.metrics import ServiceType

@app.post("/api/entities/{entity_id}/files")
async def upload_file(entity_id: str, file: UploadFile):
    # Generate task_id for this upload
    task_id = f"upload_{uuid.uuid4().hex[:12]}"
    request_id = f"req_{uuid.uuid4().hex[:12]}"

    # Start tracking with task_id
    request_tracker.start_request(
        request_id,
        RequestOperationType.FILE_UPLOAD,
        "/api/entities/{entity_id}/files",
        "POST",
        entity_id=entity_id,
        task_id=task_id,
        task_type="upload"
    )

    services_used = []

    try:
        # Process file
        file_bytes = await file.read()

        # FILE_PROCESSOR service
        pages = count_pages(file_bytes)
        services_used.append({
            "service_type": ServiceType.FILE_PROCESSOR.value,
            "estimated_cost_usd": pages * 0.02,
            "breakdown": {
                "pages": pages,
                "format": get_file_format(file)
            }
        })

        # Index document (returns doc_id)
        doc_id = index_document_entity_scoped(entity_id, file_bytes)

        # TRANSFORMER service (encoding)
        chunks = count_chunks(doc_id)
        services_used.append({
            "service_type": ServiceType.TRANSFORMER.value,
            "estimated_cost_usd": chunks * 0.003,
            "breakdown": {
                "chunks": chunks,
                "model": "sentence-transformers/all-MiniLM-L6-v2"
            }
        })

        # End tracking with services and doc_id
        request_tracker.end_request(
            request_id,
            status_code=200,
            services_used=services_used
        )

        # Return task_id to client for tracking
        return {
            "success": True,
            "task_id": task_id,
            "doc_id": doc_id,
            "total_cost_usd": sum(s["estimated_cost_usd"] for s in services_used),
            "message": "File uploaded successfully"
        }

    except Exception as e:
        request_tracker.end_request(
            request_id,
            status_code=500,
            services_used=services_used,
            error_message=str(e)
        )
        raise
```

## Data Structure

### Request Metrics with Task Info

```python
{
    "request_id": "req_abc123xyz",        # Unique per request
    "task_id": "task_abc123xyz",          # For async tracking
    "doc_id": "doc_xyz789",                # Document created
    "timestamp": "2025-01-15T10:30:45Z",
    "operation_type": "file_upload",
    "task_type": "upload",
    "entity_id": "company_123",
    "status_code": 200,
    "processing_time_ms": 3500.5,
    "services_used": [
        {
            "service_type": "file_processor",
            "estimated_cost_usd": 0.02,
            "breakdown": {
                "pages": 10,
                "format": "pdf"
            }
        },
        {
            "service_type": "transformer",
            "estimated_cost_usd": 0.15,
            "breakdown": {
                "chunks": 45,
                "embedding_model": "sentence-transformers/all-MiniLM-L6-v2"
            }
        }
    ],
    "total_cost_usd": 0.17
}
```

## Client Usage Patterns

### Pattern 1: Check Upload Cost After Completion

```python
# Client uploads file, gets task_id
response = client.post(f"/api/entities/company_123/files", files={"file": file})
task_id = response.json()["task_id"]

# Poll for cost information
while True:
    cost_response = client.get(f"/api/tasks/{task_id}/cost")
    if cost_response.status_code == 200:
        cost_data = cost_response.json()
        print(f"Cost: ${cost_data['total_cost_usd']:.4f}")
        print(f"Services: {cost_data['breakdown_by_service']}")
        break
    time.sleep(1)
```

### Pattern 2: Track All Uploads for an Entity

```python
# Get all uploads for an entity by task_id pattern
response = client.get("/api/requests", params={
    "entity_id": "company_123",
    "task_type": "upload"
})

total_cost = sum(r["total_cost_usd"] for r in response.json()["requests"])
print(f"Total upload cost for entity: ${total_cost:.4f}")
```

### Pattern 3: Monitor Document Costs

```python
# Get all costs for a specific document
response = client.get("/api/requests", params={
    "doc_id": "doc_xyz789"
})

for request in response.json()["requests"]:
    print(f"{request['service_type']}: ${request['total_cost_usd']:.6f}")
```

### Pattern 4: Batch Upload Cost Tracking

```python
# Upload multiple files and track total cost
task_ids = []
for file in files:
    response = client.post(
        f"/api/entities/{entity_id}/files",
        files={"file": file}
    )
    task_ids.append(response.json()["task_id"])

# Get total cost for all uploads
total_cost = 0.0
for task_id in task_ids:
    cost_data = client.get(f"/api/tasks/{task_id}/cost").json()
    total_cost += cost_data["total_cost_usd"]

print(f"Total batch cost: ${total_cost:.4f}")
```

## Query Examples

### Get All Requests with Services

```bash
curl "http://localhost:8000/api/requests?page=1&page_size=50" | \
  jq '.requests[] | {request_id, task_id, doc_id, total_cost: .total_cost_usd}'
```

### Find Most Expensive Task

```bash
curl "http://localhost:8000/api/requests?page=1&page_size=100" | \
  jq '.requests | max_by(.total_cost_usd)'
```

### Get Cost Breakdown by Document

```bash
curl "http://localhost:8000/api/requests?page=1&page_size=100" | \
  jq 'group_by(.doc_id) | map({
    doc_id: .[0].doc_id,
    total_cost: map(.total_cost_usd) | add,
    service_count: map(.services_used | length) | add
  })'
```

### List All Uploads for Entity with Costs

```bash
curl "http://localhost:8000/api/requests?entity_id=company_123&page_size=50" | \
  jq '.requests[] | select(.task_type == "upload") |
      {task_id, doc_id, cost: .total_cost_usd, time: .processing_time_ms}'
```

## Storage & Performance

### Data Storage
- Per request: ~600-800 bytes (with services and task IDs)
- With 1000 uploads/day: ~600KB-800KB/day
- Monthly: ~18-24MB
- Yearly: ~220-290MB

### Query Performance
- task_id lookup: O(n) filtering on collection
- With pagination: Efficient even with large datasets
- JSON storage handles well up to 1M+ requests

### Optimization Tips
1. **Archive Old Requests**: Move completed tasks to archive after 30 days
2. **Indexed Queries**: Consider adding indexes for task_id, doc_id
3. **Batch Queries**: Use pagination to limit memory usage
4. **Cache Task Costs**: Cache get_task_cost results for 5 minutes

## Error Handling

### Task Not Found
```bash
curl http://localhost:8000/api/tasks/nonexistent/cost
# Returns 404
{
  "detail": "Task nonexistent not found"
}
```

### Missing Task ID
```python
# task_id is optional - None if not async operation
if request.get("task_id"):
    print("Async task")
else:
    print("Synchronous request")
```

## Advanced: Multi-Request Tasks

Some intensive operations might spawn multiple requests:

```python
# Multi-chunk upload with multiple requests
task_id = "task_large_upload"
chunks = split_file(file, chunk_size=10_000_000)

for i, chunk in enumerate(chunks):
    request_id = f"req_{task_id}_{i}"
    request_tracker.start_request(
        request_id,
        RequestOperationType.FILE_UPLOAD,
        "/api/entities/{entity_id}/files",
        "POST",
        entity_id=entity_id,
        task_id=task_id,  # Same task_id
        doc_id=doc_id if i == 0 else None
    )

    # Process chunk
    services = [...]
    request_tracker.end_request(request_id, services_used=services)

# Get total cost for entire task
total = request_tracker.get_task_cost(task_id)
```

This aggregates all requests with the same task_id to get total cost!

## Best Practices

1. **Generate Task IDs Immediately**: Before starting async work
2. **Store Task ID Immediately**: Return to client right away
3. **Link Task ID in Requests**: Set during start_request()
4. **Track Services Accurately**: Capture all service costs
5. **Update Periodically**: Allow client to poll for costs
6. **Aggregate Results**: Use get_task_cost() for summary
7. **Archive Tasks**: Clean up old task records periodically

## Integration Checklist

- [ ] Generate task_id for async operations
- [ ] Set task_id in start_request()
- [ ] Track doc_id for document operations
- [ ] Capture all services_used
- [ ] Call end_request() with services_used
- [ ] Return task_id to client
- [ ] Provide cost tracking endpoint
- [ ] Document task tracking in API
- [ ] Test task cost queries
- [ ] Monitor storage growth

## See Also

- [REQUEST_TRACKING.md](REQUEST_TRACKING.md) - Core request tracking
- [SERVICE_TRACKING_GUIDE.md](SERVICE_TRACKING_GUIDE.md) - Service integration
- [SERVICE_TRACKING_SUMMARY.md](SERVICE_TRACKING_SUMMARY.md) - Implementation overview
