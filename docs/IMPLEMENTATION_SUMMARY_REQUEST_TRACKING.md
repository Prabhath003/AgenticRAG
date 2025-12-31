# Request Tracking Implementation Summary

## Overview
A comprehensive request tracking and cost monitoring system has been implemented in the AgenticRAG API. All API requests are now automatically tracked with their associated costs, services used, and processing metrics.

## What Was Implemented

### 1. Request Tracking Models (`api/models.py`)
- **RequestOperationType**: Enum of all tracked operation types
- **ServiceUsageResponse**: Model for service usage details
- **RequestMetricsResponse**: Response model for request metrics
- **RequestListResponse**: Paginated list of requests
- **CostReportResponse**: Detailed cost report with breakdowns

### 2. RequestTracker Class (`api/main.py`)
Core tracking system with methods:
- `start_request()`: Begin tracking a request
- `end_request()`: Complete tracking and save to storage
- `get_request()`: Retrieve specific request
- `get_requests()`: Get paginated requests with filtering
- `get_cost_report()`: Generate cost analysis with breakdowns

### 3. RequestTrackingMiddleware (`api/main.py`)
- Intercepts all `/api/*` requests
- Extracts operation type from endpoint and method
- Parses entity_id and session_id from URL
- Tracks processing time and status
- Handles errors and exceptions

### 4. New API Endpoints

#### GET /api/requests
- Lists all tracked requests
- Supports pagination (page, page_size)
- Supports filtering by entity_id and session_id
- Returns 20 results per page by default

#### GET /api/requests/{request_id}
- Get detailed metrics for a specific request
- Shows all services used and costs
- Shows processing time and status

#### GET /api/cost-report
- Generate cost analysis
- Breakdown by service type
- Breakdown by operation type
- Breakdown by entity
- Breakdown by chat session
- Average cost per request

### 5. Soft Delete Implementation

All delete endpoints now use soft deletes:
- **DELETE /api/entities/{entity_id}**: Marks entity as inactive
- **DELETE /api/chat/sessions/{session_id}**: Marks session as inactive
- **DELETE /api/entities/{entity_id}/files/{doc_id}**: Marks document as inactive

#### Soft Delete Benefits:
- ✅ All data preserved for audit
- ✅ Cost history remains intact
- ✅ No data loss
- ✅ Deactivation timestamps recorded
- ✅ Complete audit trail

### 6. Storage Integration

Requests stored in: `data/api_storage/request_metrics.json`

Each request includes:
- Unique request ID
- Timestamp
- Operation type
- Endpoint and method
- Entity ID (if applicable)
- Session ID (if applicable)
- Status code
- Processing time
- Services used with costs
- Total cost in USD
- Error message (if failed)

## Key Features

### Automatic Tracking
- Zero configuration needed
- Every request automatically tracked
- Processing time calculated automatically

### Cost Aggregation
- Per-request costs
- Service-level costs
- Operation-type costs
- Entity-level costs
- Session-level costs

### Flexible Querying
- Filter by entity
- Filter by session
- Paginate through results
- Sort by timestamp (newest first)

### Complete Audit Trail
- Soft deletes preserve data
- Deactivation timestamps
- All operational history
- Cost tracking for inactive items

## Files Modified

1. **api/models.py**
   - Added request tracking models
   - Added cost report models
   - Added operation type enum
   - Imported ServiceType from metrics

2. **api/main.py**
   - Added RequestTrackingMiddleware
   - Added RequestTracker class
   - Added request storage initialization
   - Added 3 new tracking endpoints
   - Modified DELETE endpoints for soft deletes
   - Updated entity/session creation with status field

## Files Created

1. **docs/REQUEST_TRACKING.md**
   - Complete user documentation
   - API endpoint details
   - Usage examples
   - Best practices
   - Troubleshooting guide

2. **docs/IMPLEMENTATION_SUMMARY_REQUEST_TRACKING.md**
   - This file

## Architecture

```
Request Flow:
┌─────────────────┐
│ Incoming Request│
└────────┬────────┘
         │
         ▼
┌──────────────────────────┐
│RequestTrackingMiddleware │
├──────────────────────────┤
│ - Generate request_id    │
│ - Determine op_type      │
│ - Extract entity/session │
│ - Start tracking         │
└────────┬─────────────────┘
         │
         ▼
┌──────────────────────────┐
│ Endpoint Logic           │
├──────────────────────────┤
│ (Entity, File, Chat ops)│
└────────┬─────────────────┘
         │
         ▼
┌──────────────────────────┐
│RequestTracker.end_request│
├──────────────────────────┤
│ - Calculate time         │
│ - Aggregate costs        │
│ - Record status          │
│ - Save to storage        │
└────────┬─────────────────┘
         │
         ▼
┌──────────────────────────┐
│ Response to Client       │
└──────────────────────────┘
```

## Operation Types Tracked

```
ENTITY_CREATE           → POST /api/entities
ENTITY_READ             → GET /api/entities/{id}
ENTITY_DELETE           → DELETE /api/entities/{id}
FILE_UPLOAD             → POST /api/entities/{id}/files
FILE_DELETE             → DELETE /api/entities/{id}/files/{doc_id}
CHAT_MESSAGE            → POST /api/chat
CHAT_SESSION_CREATE     → POST /api/chat/sessions
CHAT_SESSION_DELETE     → DELETE /api/chat/sessions/{id}
SEARCH                  → POST /api/search
KNOWLEDGE_GRAPH         → GET /api/knowledge-graph
STATUS                  → GET /api/status/workers
HEALTH                  → GET /health
OTHER                   → Other endpoints
```

## Usage Examples

### List Latest Requests
```bash
curl http://localhost:8000/api/requests?page=1&page_size=20
```

### Get Requests for Specific Entity
```bash
curl http://localhost:8000/api/requests?entity_id=company_123
```

### Get Cost Report
```bash
curl http://localhost:8000/api/cost-report
```

### Get Cost for Specific Entity
```bash
curl http://localhost:8000/api/cost-report?entity_id=company_123
```

### Get Specific Request Details
```bash
curl http://localhost:8000/api/requests/req_abc123xyz
```

## Data Retention

### Request Metrics Storage
- Persisted indefinitely in `request_metrics.json`
- Should be archived periodically for performance
- No automatic cleanup (you control archival)

### Soft-Deleted Data
- All inactive entities, sessions, and documents preserved
- Available in cost reports and request history
- Can be archived as a group

## Integration with Metrics System

The existing metrics system (`src/infrastructure/metrics.py`) is integrated:
- Service types (OpenAI, FILE_PROCESSOR, NATIVE, TRANSFORMER)
- Cost calculations from file processing
- Service breakdown information

Request tracking complements this by:
- Aggregating costs at request level
- Tracking service usage per request
- Providing historical analysis
- Supporting cost reporting and budgeting

## Performance Considerations

### Storage
- One JSON file per collection
- Request metrics collection will grow over time
- Consider archival strategy for >100K requests

### Querying
- Pagination limits queries to max 100 results per page
- Timestamps in ISO format for consistency
- Lock-based synchronization for thread safety

### Overhead
- Minimal: only request start/end recorded
- No per-operation tracking inside endpoints
- Service costs already calculated elsewhere

## Security & Privacy

- Request IDs are UUIDs (non-sequential)
- No sensitive data in request metrics
- Cost data isolated from other systems
- Middleware transparent to endpoint logic

## Testing

To test the tracking system:

1. Create an entity
   ```bash
   curl -X POST http://localhost:8000/api/entities \
     -H "Content-Type: application/json" \
     -d '{"entity_id":"test_entity","entity_name":"Test Entity"}'
   ```

2. View requests
   ```bash
   curl http://localhost:8000/api/requests
   ```

3. View cost report
   ```bash
   curl http://localhost:8000/api/cost-report
   ```

4. Delete entity (soft delete)
   ```bash
   curl -X DELETE http://localhost:8000/api/entities/test_entity
   ```

5. Verify cost history preserved
   ```bash
   curl http://localhost:8000/api/cost-report?entity_id=test_entity
   ```

## Next Steps

Consider implementing:

1. **Cost Budgeting**
   - Set limits per entity/session
   - Alerts when approaching limits

2. **Automatic Archival**
   - Move old requests to separate storage
   - Keep recent data for quick access

3. **Cost Optimization**
   - Identify expensive operations
   - Recommend optimizations
   - Track improvements

4. **Real-time Dashboard**
   - Live cost monitoring
   - Request rate metrics
   - Service usage visualization

5. **Cost Predictions**
   - Based on historical patterns
   - Monthly projections
   - Budget forecasting

## Summary

The request tracking system provides:
- ✅ Complete audit trail of all API activity
- ✅ Cost visibility and reporting
- ✅ Soft delete audit history
- ✅ Per-entity and per-session cost analysis
- ✅ Service-level cost breakdown
- ✅ Zero-configuration automatic tracking
- ✅ Persistent storage for compliance

All tracking is transparent to existing code and endpoints.
