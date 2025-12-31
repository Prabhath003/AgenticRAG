# Deleted Items Non-Queryable Policy

## Overview

As of the latest update, **deleted/inactive entities and sessions are no longer queryable** through the request tracking API. This ensures that:

1. Cost reports exclude costs for deleted items
2. Request history is filtered to show only active items
3. Task costs are unavailable if the entity/session has been deleted
4. Individual request details return 404 if the entity/session is inactive

## Behavior

### When an Entity is Deleted

When you delete an entity:

```bash
DELETE /api/entities/company_123
```

All associated sessions are marked as `inactive`. Subsequently:

- ❌ **Cannot query** cost reports for this entity
- ❌ **Cannot query** request history for this entity
- ❌ **Cannot query** task costs for operations tied to this entity
- ❌ **Cannot query** individual requests from deleted entity

### When a Session is Deleted

When you delete a session:

```bash
DELETE /api/entities/company_123/sessions/session_456
```

The session is marked as `inactive`. Subsequently:

- ❌ **Cannot query** cost reports for this session
- ❌ **Cannot query** request history for this session
- ❌ **Cannot query** task costs from this session
- ❌ **Cannot query** individual requests from this session

## API Behavior

### GET /api/requests

Deleted items are filtered out:

```bash
# Before deletion
curl "http://localhost:8000/api/requests?session_id=session_456"
# Returns: [req_1, req_2, req_3]

# After deletion
curl "http://localhost:8000/api/requests?session_id=session_456"
# Returns: [] (empty list)
```

### GET /api/requests/{request_id}

Returns 404 if the request's entity/session is inactive:

```bash
# Before deletion
curl "http://localhost:8000/api/requests/req_abc123"
# Returns: 200 OK with request details

# After deletion
curl "http://localhost:8000/api/requests/req_abc123"
# Returns: 404 Not Found
# {"detail": "Request req_abc123 not found"}
```

### GET /api/cost-report

Cost report excludes deleted items:

```bash
# Before deletion
curl "http://localhost:8000/api/cost-report?session_id=session_456"
# Returns: {"total_cost_usd": 0.50, "total_requests": 3, ...}

# After deletion
curl "http://localhost:8000/api/cost-report?session_id=session_456"
# Returns: {"total_cost_usd": 0.0, "total_requests": 0, ...}
```

### GET /api/tasks/{task_id}/cost

Returns 404 if the task's entity/session is inactive:

```bash
# Before deletion
curl "http://localhost:8000/api/tasks/task_abc123/cost"
# Returns: 200 OK with cost breakdown

# After deletion
curl "http://localhost:8000/api/tasks/task_abc123/cost"
# Returns: null (or 404 if no valid requests found)
```

## Implementation Details

### Filtering Logic

The RequestTracker class implements three helper methods:

```python
def _is_entity_active(self, entity_id: str) -> bool:
    """Check if an entity is active (not deleted)"""
    entity = entities_storage.find_one(ENTITIES_COLLECTION, {"entity_id": entity_id})
    return entity.get("status", "active") == "active" if entity else False

def _is_session_active(self, session_id: str) -> bool:
    """Check if a session is active (not deleted)"""
    session = chat_sessions_storage.find_one(CHAT_SESSIONS_COLLECTION, {"session_id": session_id})
    return session.get("status", "active") == "active" if session else False

def _is_request_valid(self, request: Dict[str, Any]) -> bool:
    """Check if a request should be visible (entity and session are both active)"""
    entity_id = request.get("entity_id")
    session_id = request.get("session_id")

    if entity_id and not self._is_entity_active(entity_id):
        return False

    if session_id and not self._is_session_active(session_id):
        return False

    return True
```

### Affected Methods

The following RequestTracker methods now filter out inactive items:

1. **`get_request(request_id)`**
   - Returns None for requests from inactive entities/sessions
   - API endpoint returns 404

2. **`get_requests(entity_id, session_id, task_id, doc_id, page, page_size)`**
   - Filters all results to exclude inactive items
   - Pagination counts only active items

3. **`get_cost_report(entity_id, session_id)`**
   - Aggregates costs only for active items
   - Returns empty report if all items are inactive

4. **`get_task_cost(task_id)`**
   - Returns None if all requests for task are from inactive entities/sessions
   - API endpoint returns None

## Use Cases

### Scenario 1: Audit Records

**Goal**: Keep historical data but prevent accidental querying of deleted items

**Solution**: Soft deletes preserve the data in storage, but queries exclude them

```python
# Raw data still exists in JSON storage (internal only)
request_metrics_storage.find(REQUEST_METRICS_COLLECTION)
# Returns all requests including those from deleted entities

# But public API filters them out
GET /api/requests
# Returns only active items
```

### Scenario 2: Multi-Tenant Isolation

**Goal**: Ensure deleted tenants cannot be queried

**Solution**: Deleting a tenant entity marks all its sessions as inactive

```bash
# Delete tenant
DELETE /api/entities/tenant_456

# Subsequent queries return nothing
GET /api/requests?entity_id=tenant_456
# Returns: [] (empty)
```

### Scenario 3: Session Cleanup

**Goal**: Archive completed sessions without querying them

**Solution**: Delete session, cost history becomes inaccessible

```bash
# Delete old session
DELETE /api/entities/company_123/sessions/old_session

# Cost report excludes it
GET /api/cost-report?session_id=old_session
# Returns: empty report
```

## Data Preservation

Even though deleted items are non-queryable through the API, the underlying data is preserved:

```
storage/data/api_storage/
├── entities.json              # Contains status="inactive" records
├── chat_sessions.json         # Contains status="inactive" records
└── request_metrics.json       # Contains all requests (no deletion)
```

This allows:

- **Audit trails**: Raw data shows when items were deactivated
- **Recovery**: Marking items as inactive (not deleting data) allows recovery if needed
- **Compliance**: Full history preserved for regulatory requirements

## API Response Changes

### Before Update

```bash
# Session still queryable after deletion
curl "http://localhost:8000/api/requests?session_id=deleted_session"
# Returns: [old_requests...] ✓ Queryable
```

### After Update

```bash
# Session no longer queryable after deletion
curl "http://localhost:8000/api/requests?session_id=deleted_session"
# Returns: [] ✓ Filtered out
```

## Best Practices

1. **Query Before Deletion**: If you need cost reports, generate them before deleting
2. **Use Task IDs**: Store task_ids before deletion for cost tracking
3. **Archive Strategy**: Consider archiving sessions instead of deleting
4. **Cascade Awareness**: Know that deleting an entity affects all its sessions

## Migration Guide

If you have code relying on querying deleted items:

### Old Code (Still Works for Active Items)

```python
# This still works for active entities
response = client.get("/api/requests", params={"entity_id": "active_entity"})
```

### New Code (Explicit Handling)

```python
# Handle case where entity might be deleted
response = client.get("/api/requests", params={"entity_id": "entity_123"})
if response.status_code == 200:
    requests = response.json()["requests"]
    if not requests:
        print("Entity is deleted or has no requests")
else:
    print("Error querying entity")
```

## Performance Impact

- **Minimal**: Status checks are done in-memory from storage
- **Scalable**: Filtering happens after retrieval, not in storage queries
- **Efficient**: Cache-friendly since status rarely changes

## Error Codes

| Endpoint | Status | Meaning |
|----------|--------|---------|
| GET /api/requests | 200 | Returns filtered list (may be empty) |
| GET /api/requests/{id} | 404 | Request from deleted entity/session |
| GET /api/cost-report | 200 | Returns empty report if all items deleted |
| GET /api/tasks/{id}/cost | 404 | Task from deleted entity/session |

## Related Documentation

- [SOFT_DELETE_ARCHITECTURE.md](SOFT_DELETE_ARCHITECTURE.md) - Soft delete implementation
- [REQUEST_TRACKING.md](REQUEST_TRACKING.md) - Complete request tracking guide
- [TASK_COST_TRACKING.md](TASK_COST_TRACKING.md) - Task cost tracking details
