# Request Tracking Quick Reference Guide

## Overview
The AgenticRAG API now automatically tracks all requests with full cost accounting and audit trails. Deleted items are marked as "inactive" rather than permanently removed.

## Quick Start

### View All Requests
```bash
curl http://localhost:8000/api/requests
```

### Get Cost Report
```bash
curl http://localhost:8000/api/cost-report
```

### Filter by Entity
```bash
# All requests for a specific entity
curl http://localhost:8000/api/requests?entity_id=company_123

# Cost breakdown for a specific entity
curl http://localhost:8000/api/cost-report?entity_id=company_123
```

### Filter by Session
```bash
# All requests for a specific chat session
curl http://localhost:8000/api/requests?session_id=session_abc123

# Cost for a specific chat session
curl http://localhost:8000/api/cost-report?session_id=session_abc123
```

### Get Request Details
```bash
curl http://localhost:8000/api/requests/req_abc123xyz
```

## Tracking Modes

| Operation | Endpoint | Method | Tracked As |
|-----------|----------|--------|-----------|
| Create Entity | `/api/entities` | POST | `entity_create` |
| Get Entity | `/api/entities/{id}` | GET | `entity_read` |
| Delete Entity | `/api/entities/{id}` | DELETE | `entity_delete` |
| Upload File | `/api/entities/{id}/files` | POST | `file_upload` |
| Delete File | `/api/entities/{id}/files/{doc_id}` | DELETE | `file_delete` |
| Send Message | `/api/chat` | POST | `chat_message` |
| Create Session | `/api/chat/sessions` | POST | `chat_session_create` |
| Delete Session | `/api/chat/sessions/{id}` | DELETE | `chat_session_delete` |
| Search | `/api/search` | POST | `search` |
| Knowledge Graph | `/api/knowledge-graph` | GET | `knowledge_graph` |
| Status | `/api/status/workers` | GET | `status` |
| Health | `/health` | GET | `health` |

## Soft Delete Behavior

### Before Delete
```json
{
  "entity_id": "company_123",
  "status": "active",
  "created_at": "2025-01-15T10:00:00Z"
}
```

### After Soft Delete (DELETE /api/entities/company_123)
```json
{
  "entity_id": "company_123",
  "status": "inactive",
  "created_at": "2025-01-15T10:00:00Z",
  "deactivated_at": "2025-01-15T15:30:00Z"
}
```

**Data preserved in:**
- Cost reports
- Request history
- Audit trails

## Request Metrics Structure

Each tracked request includes:

```json
{
  "request_id": "req_abc123xyz",
  "timestamp": "2025-01-15T10:30:45.123Z",
  "operation_type": "chat_message",
  "endpoint": "/api/chat",
  "method": "POST",
  "entity_id": "company_123",
  "session_id": "session_xyz789",
  "status_code": 200,
  "processing_time_ms": 1234.5,
  "services_used": [
    {
      "service_type": "openai",
      "cost_usd": 0.0045,
      "breakdown": {}
    }
  ],
  "total_cost_usd": 0.0045,
  "error_message": null
}
```

## Cost Report Structure

```json
{
  "period_start": "2025-01-15T00:00:00Z",
  "period_end": "2025-01-15T23:59:59Z",
  "total_requests": 150,
  "total_cost_usd": 2.5430,
  "breakdown_by_service": {
    "openai": 2.1200,
    "file_processor": 0.4230,
    "transformer": 0.0000,
    "native": 0.0000
  },
  "breakdown_by_operation": {
    "chat_message": 1.8900,
    "file_upload": 0.5200,
    "search": 0.1330
  },
  "breakdown_by_entity": {
    "company_123": 1.5600,
    "company_456": 0.9830
  },
  "breakdown_by_session": {
    "session_abc123": 0.8450,
    "session_def456": 0.6700
  },
  "average_cost_per_request": 0.0169
}
```

## Common Tasks

### Find Expensive Requests
```bash
curl http://localhost:8000/api/requests?page_size=100 | \
  jq '.requests | sort_by(.total_cost_usd) | reverse | .[0:5]'
```

### Find Failed Requests
```bash
curl http://localhost:8000/api/requests?page_size=100 | \
  jq '.requests | map(select(.status_code >= 400))'
```

### Get Average Processing Time
```bash
curl http://localhost:8000/api/requests?page_size=100 | \
  jq '[.requests[].processing_time_ms] | add / length'
```

### Find Slowest Requests
```bash
curl http://localhost:8000/api/requests?page_size=100 | \
  jq '.requests | sort_by(.processing_time_ms) | reverse | .[0:5]'
```

### Get Entity Cost Ranking
```bash
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_entity | to_entries | sort_by(.value) | reverse'
```

### Monitor Service Costs
```bash
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_service | to_entries | map({service: .key, cost: .value})'
```

## API Pagination

### Request List Pagination
```bash
# Page 1, 20 results per page (default)
curl http://localhost:8000/api/requests

# Page 2, 50 results per page
curl "http://localhost:8000/api/requests?page=2&page_size=50"

# Maximum page_size is 100
curl "http://localhost:8000/api/requests?page=1&page_size=100"
```

Results are sorted by timestamp (newest first).

## Response Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 404 | Request/entity not found |
| 400 | Bad request (invalid params) |
| 500 | Server error |

## Files and Locations

| File | Purpose |
|------|---------|
| `api/models.py` | Request tracking models |
| `api/main.py` | Tracking middleware and endpoints |
| `data/api_storage/request_metrics.json` | Persisted request data |
| `docs/REQUEST_TRACKING.md` | Full documentation |
| `docs/IMPLEMENTATION_SUMMARY_REQUEST_TRACKING.md` | Implementation details |

## Status Values

Items can have these status values:

- `active` - Currently active
- `inactive` - Soft deleted (data preserved)

All operations track timestamps:
- `created_at` - When created
- `deactivated_at` - When marked inactive (if deleted)

## Services Tracked

| Service | Type | Cost Model |
|---------|------|-----------|
| OpenAI | LLM | Per token |
| FILE_PROCESSOR | File Processing | Per page |
| TRANSFORMER | Local Models | Estimated per operation |
| NATIVE | Local Files | Usually free |

## Troubleshooting

**Q: Why is my request not tracked?**
A: Only `/api/*` endpoints are tracked. Root endpoints like `/health` aren't included by default.

**Q: Can I still access deleted data?**
A: Yes! Soft-deleted data is preserved and visible in cost reports and request history.

**Q: How long are requests kept?**
A: Indefinitely in the JSON storage. Archive old data as needed for performance.

**Q: How accurate are the cost estimates?**
A: As accurate as the service cost data provided. Costs come from service responses.

**Q: Can I export the data?**
A: Yes, export from `data/api_storage/request_metrics.json` directly.

## Integration Example

```python
from datetime import datetime

# Get all requests for last 24 hours
all_requests = get_all_requests()
recent = [r for r in all_requests
          if (datetime.now() - r['timestamp']).days == 0]

# Calculate metrics
total_cost = sum(r['total_cost_usd'] for r in recent)
avg_time = sum(r['processing_time_ms'] for r in recent) / len(recent)
failed = [r for r in recent if r['status_code'] >= 400]

print(f"Cost: ${total_cost:.2f}")
print(f"Avg Time: {avg_time:.0f}ms")
print(f"Failed: {len(failed)}")
```

## See Also

- [Full Request Tracking Documentation](REQUEST_TRACKING.md)
- [Implementation Details](IMPLEMENTATION_SUMMARY_REQUEST_TRACKING.md)
- [API Documentation](/docs)
