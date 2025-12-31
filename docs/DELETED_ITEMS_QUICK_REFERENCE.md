# Deleted Items Non-Queryable - Quick Reference

## TL;DR

**Deleted entities and sessions are no longer queryable through the request tracking API.**

## What Changed

| Operation | Before | After |
|-----------|--------|-------|
| Query deleted entity requests | ✓ Returns requests | ✗ Returns empty list |
| Query deleted session requests | ✓ Returns requests | ✗ Returns empty list |
| Get cost report for deleted entity | ✓ Returns cost | ✗ Returns empty report |
| Get cost report for deleted session | ✓ Returns cost | ✗ Returns empty report |
| Query task cost from deleted entity | ✓ Returns cost | ✗ Returns 404 |
| Get request details from deleted entity | ✓ Returns request | ✗ Returns 404 |

## API Response Changes

### GET /api/requests (Deleted Entity)
```bash
BEFORE:
curl "http://localhost:8000/api/requests?entity_id=deleted_entity"
# Returns: [req_1, req_2, req_3]

AFTER:
curl "http://localhost:8000/api/requests?entity_id=deleted_entity"
# Returns: [] (empty list)
```

### GET /api/requests/{request_id} (From Deleted Entity)
```bash
BEFORE:
curl "http://localhost:8000/api/requests/req_abc123"
# Returns: 200 OK with request details

AFTER:
curl "http://localhost:8000/api/requests/req_abc123"
# Returns: 404 Not Found
```

### GET /api/cost-report (Deleted Session)
```bash
BEFORE:
curl "http://localhost:8000/api/cost-report?session_id=deleted_session"
# Returns: {"total_cost_usd": 0.50, ...}

AFTER:
curl "http://localhost:8000/api/cost-report?session_id=deleted_session"
# Returns: {"total_cost_usd": 0.0, "total_requests": 0, ...}
```

### GET /api/tasks/{task_id}/cost (From Deleted Entity)
```bash
BEFORE:
curl "http://localhost:8000/api/tasks/task_abc123/cost"
# Returns: {"task_id": "task_abc123", "total_cost_usd": 0.17, ...}

AFTER:
curl "http://localhost:8000/api/tasks/task_abc123/cost"
# Returns: 404 Not Found
```

## Code Changes

### RequestTracker Class - New Methods

```python
def _is_entity_active(self, entity_id: str) -> bool:
    """Check if entity is active"""

def _is_session_active(self, session_id: str) -> bool:
    """Check if session is active"""

def _is_request_valid(self, request: Dict) -> bool:
    """Check if request should be visible"""
```

### RequestTracker Class - Modified Methods

```python
def get_request(request_id)
    # Now returns None if entity/session is inactive

def get_requests(entity_id, session_id, task_id, doc_id, page, page_size)
    # Now filters out inactive items

def get_cost_report(entity_id, session_id)
    # Now excludes inactive items from aggregation

def get_task_cost(task_id)
    # Now filters out inactive requests
```

## How Filtering Works

```
1. Request arrives: GET /api/requests?session_id=session_123
   ↓
2. Retrieve requests from storage
   ↓
3. For each request:
   - Check: Is referenced entity active?
   - Check: Is referenced session active?
   ↓
4. Keep only requests where BOTH are active
   ↓
5. Return filtered results
```

## Data Preservation

Raw data is **NOT deleted**, just filtered from queries:

```bash
# Raw storage (internal only)
storage/data/api_storage/request_metrics.json    # All requests preserved
storage/data/api_storage/entities.json           # Shows status="inactive"
storage/data/api_storage/chat_sessions.json      # Shows status="inactive"

# API queries (filtered)
GET /api/requests                                 # Excludes inactive
GET /api/cost-report                             # Excludes inactive
```

## Deletion Cascade Effect

```
DELETE /api/entities/company_123
    ↓
- Entity marked: status="inactive"
- All sessions of entity: status="inactive"
- All requests from those sessions: FILTERED OUT
```

## Status Codes

| Scenario | Status Code | Response |
|----------|-------------|----------|
| Query active entity | 200 | Returns requests |
| Query deleted entity | 200 | Returns [] (empty) |
| Get active request | 200 | Returns request |
| Get request from deleted entity | 404 | Not found |
| Report for active entity | 200 | Returns costs |
| Report for deleted entity | 200 | Returns empty report |
| Task cost for active task | 200 | Returns cost |
| Task cost for deleted entity | 404 | Not found |

## Client Code Migration

### Active Items (No Change)
```python
# This still works exactly the same
response = client.get("/api/requests", params={"entity_id": "active_entity"})
assert response.status_code == 200
```

### Deleted Items (Need Handling)
```python
# Before: Worked
response = client.get("/api/requests", params={"entity_id": "deleted_entity"})
requests = response.json()["requests"]

# After: Returns empty list
response = client.get("/api/requests", params={"entity_id": "deleted_entity"})
requests = response.json()["requests"]
assert requests == []  # Now empty!

# Or for individual requests
response = client.get("/api/requests/req_from_deleted_entity")
if response.status_code == 404:
    print("Request is from deleted entity")
```

## Best Practices

1. **Query Before Deletion**: Get cost reports before deleting
2. **Store Task IDs**: Save task_ids for offline tracking
3. **Handle 404s**: Prepare code for 404 responses
4. **Archive Strategy**: Consider archiving instead of deleting
5. **Cascade Awareness**: Remember entity deletion affects all sessions

## Decision Logic

When querying by entity_id:
```
┌─ Is entity active?
│  ├─ YES → Return its requests ✓
│  └─ NO → Return empty list []
```

When querying by session_id:
```
┌─ Is session active?
│  ├─ YES → Return its requests ✓
│  └─ NO → Return empty list []
```

When querying individual request:
```
┌─ Is entity active?
│  ├─ NO → Return 404 ✗
│  └─ YES → Continue
      └─ Is session active?
         ├─ NO → Return 404 ✗
         └─ YES → Return request ✓
```

## Implementation Files

```
api/main.py
├─ RequestTracker._is_entity_active()      [New]
├─ RequestTracker._is_session_active()     [New]
├─ RequestTracker._is_request_valid()      [New]
├─ RequestTracker.get_request()            [Modified]
├─ RequestTracker.get_requests()           [Modified]
├─ RequestTracker.get_cost_report()        [Modified]
└─ RequestTracker.get_task_cost()          [Modified]

tests/test_request_tracking_filtering.py   [New]

docs/DELETED_ITEMS_NON_QUERYABLE.md        [New]
docs/DELETED_ITEMS_IMPLEMENTATION.md       [New]
docs/DELETED_ITEMS_QUICK_REFERENCE.md      [New - This file]
```

## FAQ

**Q: Can I still see deleted data?**
A: Raw data is in storage files, but not queryable via API.

**Q: Will this break my code?**
A: Only if code queries deleted items. Active items work normally.

**Q: Can I recover deleted items?**
A: Data is preserved. Manual recovery possible via storage access.

**Q: What about compliance?**
A: Raw data preserved for audit. Consider snapshots for compliance.

**Q: Performance impact?**
A: Minimal - just status lookups per request.

**Q: Can I query deleted items?**
A: No - by design. Ensures clean separation.

## Examples

### Example 1: Delete and Query
```bash
# 1. Delete session
curl -X DELETE "http://localhost:8000/api/entities/company_123/sessions/session_456"

# 2. Try to query it
curl "http://localhost:8000/api/requests?session_id=session_456"
# Returns: {"requests": [], "total": 0}  # Empty!
```

### Example 2: Entity Cascade
```bash
# 1. Entity has 10 requests total
# 2. Entity has 2 sessions with requests
# 3. Delete entity
curl -X DELETE "http://localhost:8000/api/entities/company_123"

# 4. All requests now filtered
curl "http://localhost:8000/api/requests?entity_id=company_123"
# Returns: {"requests": [], "total": 0}  # All filtered!

# 5. Cost report shows $0
curl "http://localhost:8000/api/cost-report?entity_id=company_123"
# Returns: {"total_cost_usd": 0.0, "total_requests": 0, ...}
```

### Example 3: Mixed Active/Deleted
```bash
# Scenario:
# - Entity has 2 sessions
# - Session A: active (5 requests, $0.50)
# - Session B: deleted (3 requests, $0.30)

# Query entity
curl "http://localhost:8000/api/requests?entity_id=company_123"
# Returns: 5 requests from session A only  # Session B filtered!

# Cost report for entity
curl "http://localhost:8000/api/cost-report?entity_id=company_123"
# Returns: {"total_cost_usd": 0.50, "total_requests": 5}  # Only session A!
```

## Key Takeaway

✅ Active items work normally
❌ Deleted items are not queryable
📊 Data is preserved but hidden from queries
🔄 Cascade deletions automatically handled
🛡️ Clean data isolation enforced
