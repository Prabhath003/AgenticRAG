# Request Tracking & Cost Monitoring System

## Overview

The Entity-Scoped RAG API now includes comprehensive request tracking and cost monitoring. Every API request is automatically tracked, including:

- Request metadata (endpoint, method, timestamps)
- Processing time
- Associated entity and session IDs
- Services used and their costs
- Error messages (if any)

All data is persisted for audit and cost analysis purposes.

## Key Features

### 1. Automatic Request Tracking
- Every API request is automatically tracked via middleware
- Request ID is generated for each request (format: `req_{uuid}`)
- Tracking includes: operation type, endpoint, HTTP method, entity, session, status code, processing time, costs, and errors

### 2. Soft Deletes (Audit Trail)
Instead of permanently deleting data, the system now marks items as `inactive`:
- **Entities**: Marked inactive with `deactivated_at` timestamp
- **Chat Sessions**: Marked inactive with `deactivated_at` timestamp
- **Documents**: Marked inactive with `deactivated_at` timestamp

All inactive data is preserved in storage for:
- Cost tracking and reporting
- Audit trails
- Historical analysis

### 3. Cost Tracking Per Request
Each request tracks:
- Services used (OpenAI, FILE_PROCESSOR, NATIVE, TRANSFORMER)
- Estimated cost in USD per service
- Total cost aggregated per request

### 4. Cost Report & Analytics
Generate detailed cost reports with breakdowns by:
- **By Service**: How much each service cost
- **By Operation Type**: Cost per operation (entity_create, file_upload, chat_message, etc.)
- **By Entity**: Cost per entity
- **By Session**: Cost per chat session

## New API Endpoints

### List All Requests
```
GET /api/requests?page=1&page_size=20&entity_id=optional&session_id=optional
```

**Query Parameters:**
- `page` (int, default=1): Page number for pagination
- `page_size` (int, default=20, max=100): Results per page
- `entity_id` (string, optional): Filter by entity
- `session_id` (string, optional): Filter by chat session

**Response:**
```json
{
  "requests": [
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
          "breakdown": {"tokens": 150}
        }
      ],
      "total_cost_usd": 0.0045,
      "error_message": null
    }
  ],
  "total": 250,
  "page": 1,
  "page_size": 20
}
```

### Get Specific Request Details
```
GET /api/requests/{request_id}
```

**Response:** Single request object with full details

### Cost Report
```
GET /api/cost-report?entity_id=optional&session_id=optional
```

**Query Parameters:**
- `entity_id` (string, optional): Filter costs by entity
- `session_id` (string, optional): Filter costs by chat session

**Response:**
```json
{
  "period_start": "2025-01-15T00:00:00Z",
  "period_end": "2025-01-15T23:59:59Z",
  "total_requests": 150,
  "total_cost_usd": 2.5430,
  "breakdown_by_service": {
    "openai": 2.1200,
    "file_processor": 0.4230,
    "transformer": 0.0000
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
    "session_def456": 0.6700,
    "session_ghi789": 1.0280
  },
  "average_cost_per_request": 0.0169
}
```

## Request Tracking Flow

### How Requests Are Tracked

1. **Request Arrives**: Middleware intercepts incoming request
2. **Request ID Generated**: Unique ID created (`req_{uuid}`)
3. **Operation Type Determined**: Based on endpoint and HTTP method
4. **Entity/Session Extracted**: From URL path if applicable
5. **Request Started**: Initial metadata recorded
6. **Request Processed**: Endpoint logic executes
7. **Request Completed**:
   - Status code recorded
   - Processing time calculated
   - Services and costs aggregated
   - Data persisted to storage

### Operation Types Tracked

```python
ENTITY_CREATE        # POST /api/entities
ENTITY_READ          # GET /api/entities/{id}
ENTITY_DELETE        # DELETE /api/entities/{id}
FILE_UPLOAD          # POST /api/entities/{id}/files
FILE_DELETE          # DELETE /api/entities/{id}/files/{doc_id}
CHAT_MESSAGE         # POST /api/chat
CHAT_SESSION_CREATE  # POST /api/chat/sessions
CHAT_SESSION_DELETE  # DELETE /api/chat/sessions/{id}
SEARCH               # POST /api/search
KNOWLEDGE_GRAPH      # GET /api/knowledge-graph
STATUS               # GET /api/status/workers
HEALTH               # GET /health
OTHER                # Other endpoints
```

## Soft Delete Details

### What Happens When You Delete

**Before (Hard Delete):**
- Data was permanently removed
- No audit trail
- No cost history

**After (Soft Delete):**
- Data marked with `status: "inactive"`
- `deactivated_at` timestamp recorded
- Full data preserved in storage
- All cost data intact
- Audit trail available

### Example: Deleting an Entity

```bash
curl -X DELETE http://localhost:8000/api/entities/company_123
```

**Response:**
```json
{
  "success": true,
  "entity_id": "company_123",
  "message": "Entity company_123 marked as inactive",
  "sessions_deactivated": 5,
  "note": "Data preserved for audit and cost tracking"
}
```

**What Happens Internally:**
1. Entity status changed to `inactive`
2. `deactivated_at` timestamp added
3. All sessions for entity marked `inactive`
4. Request tracked with operation_type: `entity_delete`

## Cost Tracking in Action

### Example 1: Track Costs for a Specific Entity

```bash
curl http://localhost:8000/api/cost-report?entity_id=company_123
```

Shows:
- Total cost for company_123
- Breakdown by operation type (uploads, chats, searches)
- Breakdown by session
- Average cost per request for this entity

### Example 2: Track Costs for a Chat Session

```bash
curl http://localhost:8000/api/cost-report?session_id=session_abc123
```

Shows:
- Total cost of all messages in session
- Service breakdown
- Operation breakdown

### Example 3: View All Requests for an Entity

```bash
curl "http://localhost:8000/api/requests?entity_id=company_123&page=1&page_size=50"
```

Returns all requests related to company_123 with:
- Timestamps
- Processing times
- Services used
- Costs
- Errors (if any)

## Storage Structure

Request metrics are stored in:
```
data/api_storage/request_metrics.json
```

Each request record contains:
```python
{
    "request_id": str,           # Unique request ID
    "timestamp": datetime,        # When request was made
    "operation_type": str,        # Type of operation
    "endpoint": str,              # API endpoint path
    "method": str,                # HTTP method
    "entity_id": str | None,      # Associated entity (if any)
    "session_id": str | None,     # Associated session (if any)
    "status_code": int,           # HTTP response status
    "processing_time_ms": float,  # Time to process in ms
    "services_used": [            # Services consumed
        {
            "service_type": str,
            "breakdown": dict,
            "estimated_cost_usd": float
        }
    ],
    "total_cost_usd": float,      # Total cost
    "error_message": str | None   # Error if failed
}
```

## Integration with Existing Systems

### Service Cost Tracking

When services (OpenAI, FILE_PROCESSOR, TRANSFORMER, NATIVE) are used, their costs are:
1. Calculated during processing
2. Recorded in the response
3. Aggregated in the request tracker
4. Persisted with the request

### Request Integration Points

**Entity Creation**
- Operation type: `entity_create`
- Entity ID extracted and tracked
- No services typically used

**File Upload**
- Operation type: `file_upload`
- Services: FILE_PROCESSOR, TRANSFORMER, or NATIVE
- Costs calculated from file processing metrics
- Entity ID tracked

**Chat Messages**
- Operation type: `chat_message`
- Services: OpenAI (LLM), TRANSFORMER (embeddings)
- Session ID tracked
- Entity ID tracked

**Search**
- Operation type: `search`
- Services: TRANSFORMER (embeddings)
- Entity ID tracked

## Usage Examples

### Get Processing Time Statistics

```bash
# Get all requests in last page
curl http://localhost:8000/api/requests?page=1&page_size=100 | \
  jq '.requests | map(.processing_time_ms) | (add/length | "Average: \(.)ms")'
```

### Find Expensive Requests

```bash
# Get all requests sorted by cost
curl http://localhost:8000/api/requests?page_size=100 | \
  jq '.requests | sort_by(.total_cost_usd) | reverse | .[:10]'
```

### Monitor Failed Requests

```bash
# Get all failed requests
curl http://localhost:8000/api/requests?page_size=100 | \
  jq '.requests | map(select(.status_code >= 400))'
```

### Cost Analysis by Entity

```bash
# Get cost breakdown by entity
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_entity | to_entries | sort_by(.value) | reverse'
```

## Configuration

### Request Tracking Middleware

Located in [api/main.py](../api/main.py):

```python
class RequestTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware to track all API requests and their metrics"""

    async def dispatch(self, request: Request, call_next):
        # Skips tracking for non-API endpoints
        if not request.url.path.startswith("/api"):
            return await call_next(request)

        # Tracks all /api/* endpoints
```

**Skip Tracking** (if needed):
- Endpoints not starting with `/api` are not tracked
- Modify middleware condition to customize

## Best Practices

### 1. Regular Cost Reviews
- Check cost reports weekly
- Monitor costs by entity and session
- Identify expensive operations

### 2. Archive Old Data
- Request metrics accumulate over time
- Consider archiving old records periodically
- Keep recent data in main storage for fast queries

### 3. Alert on High Costs
- Monitor `average_cost_per_request`
- Alert if costs exceed thresholds
- Investigate expensive operations

### 4. Session Cost Tracking
- Track cost per chat session
- Identify long-running, expensive sessions
- Optimize session-level costs

### 5. Use Soft Deletes Effectively
- Never need to worry about losing data
- Deactivated data available in reports
- Audit trail always preserved

## Troubleshooting

### Request Not Tracked
**Cause:** Endpoint doesn't start with `/api`
**Solution:** Only `/api/*` endpoints are tracked

### Missing Cost Data
**Cause:** Services not recorded in response
**Solution:** Check service tracking in endpoint logic

### Pagination Not Working
**Cause:** Invalid page or page_size
**Solution:** Use `page >= 1` and `page_size <= 100`

### Cost Report Empty
**Cause:** No requests match filter criteria
**Solution:** Check entity_id and session_id values

## Future Enhancements

- [ ] Cost budgeting and alerts
- [ ] Request replay and debugging
- [ ] Cost prediction based on patterns
- [ ] Automatic old data archival
- [ ] Cost optimization recommendations
- [ ] Real-time cost dashboard
