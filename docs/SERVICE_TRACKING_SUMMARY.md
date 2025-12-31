# Service & Task Tracking Implementation Summary

## What Was Enhanced

The request tracking system now fully integrates with the metrics system to track:

### 1. Task Type Classification
- **UPLOAD**: File upload operations using FILE_PROCESSOR + TRANSFORMER
- **QUERY**: Search/chat operations using TRANSFORMER + OPENAI

### 2. Service Tracking
Four service types tracked per request:
- **OpenAI**: LLM services for chat
- **FILE_PROCESSOR**: Document parsing and processing
- **TRANSFORMER**: Vector embeddings and semantic search (GPU-intensive)
- **NATIVE**: Local file processing (JSON, MD, TXT)

### 3. Cost Breakdown (5 Levels)
- By service type (openai, file_processor, transformer, native)
- By task type (upload, query)
- By operation type (file_upload, chat_message, search, etc.)
- By entity (who used the service)
- By session (which chat session)

## Key Implementation Details

### Models Enhanced (`api/models.py`)

```python
# Added task_type to tracking
class RequestMetricsResponse:
    task_type: Optional[str]  # "upload" or "query"
    services_used: List[ServiceUsageResponse]

# Added breakdown_by_task_type to cost report
class CostReportResponse:
    breakdown_by_task_type: Dict[str, float]  # {upload: cost, query: cost}
```

### Request Tracker Enhanced (`api/main.py`)

```python
class RequestTracker:
    def start_request(self, ..., task_type: Optional[str] = None):
        # Now tracks task type (upload/query)

    def get_cost_report(self):
        # Now includes breakdown_by_task_type
```

### Task Type Detection

```python
class RequestTrackingMiddleware:
    def _determine_task_type(endpoint, method, operation_type):
        # FILE_UPLOAD → "upload"
        # CHAT_MESSAGE, SEARCH → "query"
        # Others → None
```

## Data Flow

```
Request arrives
    ↓
Middleware determines task type
    ↓
Operation executes
    ↓
Services captured with costs & breakdown:
  - OpenAI: model, tokens
  - FILE_PROCESSOR: pages, format
  - TRANSFORMER: chunks, dimension
  - NATIVE: file_type, size
    ↓
Request tracked with:
  - Request ID
  - Task type (upload/query)
  - Operation type
  - Entity & Session IDs
  - Services used
  - Total cost
  - Processing time
    ↓
Data persisted to request_metrics.json
```

## Example Cost Tracking

### Upload Operation (POST /api/entities/{id}/files)
```json
{
  "request_id": "req_abc123",
  "task_type": "upload",
  "operation_type": "file_upload",
  "processing_time_ms": 3500,
  "services_used": [
    {
      "service_type": "file_processor",
      "cost_usd": 0.02,
      "breakdown": {"pages": 10, "format": "pdf"}
    },
    {
      "service_type": "transformer",
      "cost_usd": 0.15,
      "breakdown": {"chunks": 45, "dimension": 1536}
    }
  ],
  "total_cost_usd": 0.17
}
```

### Query Operation (POST /api/chat)
```json
{
  "request_id": "req_xyz789",
  "task_type": "query",
  "operation_type": "chat_message",
  "session_id": "session_abc123",
  "entity_id": "company_123",
  "processing_time_ms": 2150,
  "services_used": [
    {
      "service_type": "transformer",
      "cost_usd": 0.001,
      "breakdown": {"tokens": 25, "dimension": 1536}
    },
    {
      "service_type": "openai",
      "cost_usd": 0.0045,
      "breakdown": {
        "model": "gpt-4",
        "prompt_tokens": 450,
        "completion_tokens": 150
      }
    }
  ],
  "total_cost_usd": 0.0055
}
```

## API Enhancements

### Cost Report Now Shows

```bash
curl http://localhost:8000/api/cost-report
```

Returns:
```json
{
  "total_requests": 150,
  "total_cost_usd": 4.30,

  "breakdown_by_task_type": {
    "upload": 2.50,    // File uploads
    "query": 1.80      // Searches & chats
  },

  "breakdown_by_service": {
    "openai": 1.45,
    "file_processor": 0.40,
    "transformer": 2.35,
    "native": 0.10
  },

  "breakdown_by_operation": {
    "file_upload": 2.50,
    "chat_message": 1.50,
    "search": 0.30
  },

  "breakdown_by_entity": {
    "company_123": 2.15,
    "company_456": 1.80
  },

  "breakdown_by_session": {
    "session_abc": 0.85,
    "session_def": 0.65
  },

  "average_cost_per_request": 0.0287
}
```

### Request Details Now Include

```bash
curl http://localhost:8000/api/requests/req_abc123
```

Shows:
```json
{
  "request_id": "req_abc123",
  "task_type": "upload",           // NEW
  "operation_type": "file_upload",
  "services_used": [...],          // Services with breakdown
  "total_cost_usd": 0.17,
  "processing_time_ms": 3500
}
```

## Integration Guide

To integrate service tracking in your endpoints:

```python
@app.post("/api/entities/{entity_id}/files")
async def upload_file(entity_id: str, file: UploadFile):
    # Services used during processing
    services_used = []

    # Add FILE_PROCESSOR
    services_used.append({
        "service_type": ServiceType.FILE_PROCESSOR.value,
        "estimated_cost_usd": 0.02,
        "breakdown": {"pages": 10, "format": "pdf"}
    })

    # Add TRANSFORMER
    services_used.append({
        "service_type": ServiceType.TRANSFORMER.value,
        "estimated_cost_usd": 0.15,
        "breakdown": {"chunks": 45, "dimension": 1536}
    })

    # End tracking with services
    request_tracker.end_request(
        request_id,
        status_code=200,
        services_used=services_used
    )
```

See [SERVICE_TRACKING_GUIDE.md](SERVICE_TRACKING_GUIDE.md) for detailed examples.

## Files Modified

1. **api/models.py**
   - Added `task_type` field to RequestMetricsResponse
   - Added `breakdown_by_task_type` to CostReportResponse
   - Imported TaskType from metrics

2. **api/main.py**
   - Enhanced RequestTracker with task_type parameter
   - Added `_determine_task_type()` method to middleware
   - Updated cost report generation with task breakdown
   - Updated all response models to include task_type

## Performance Impact

- **Minimal**: Only enum comparison for task type detection
- **No additional DB queries**: All data in existing request_metrics
- **Backward compatible**: Existing requests work without task_type

## Query Examples

### Find Most Expensive Task Type
```bash
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_task_type | to_entries | max_by(.value)'
```

### Compare Upload vs Query Costs
```bash
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_task_type | "Upload: $\(.upload), Query: $\(.query)"'
```

### Find Most Expensive Service
```bash
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_service | to_entries | max_by(.value) | .key'
```

### Monitor Service Breakdown
```bash
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_service | map_values(. / 4.30 * 100 | round) as $pct |
      "OpenAI: \($pct.openai)%, Transformer: \($pct.transformer)%,
       FileProcessor: \($pct.file_processor)%"'
```

## Cost Estimation Reference

### Services and Costs

| Service | Unit | Est. Cost | Example |
|---------|------|-----------|---------|
| FILE_PROCESSOR | Per page | $0.02 | 10 pages = $0.20 |
| TRANSFORMER | Per 1K tokens | $0.0001 | 45K tokens = $0.0045 |
| OPENAI (GPT-4) | Input: Per 1K | $0.03 | 450 tokens = $0.0135 |
| OPENAI (GPT-4) | Output: Per 1K | $0.06 | 150 tokens = $0.009 |
| NATIVE | Per file | Free | JSON, MD, TXT |

## Next Steps

1. **Implement Service Tracking**: Add service_used to your endpoints
2. **Monitor Costs**: Use cost report endpoints regularly
3. **Set Alerts**: Configure alerts on cost thresholds
4. **Analyze Patterns**: Use breakdown data to optimize services
5. **Cost Optimization**: Identify and optimize expensive operations

## Documentation Files

- **REQUEST_TRACKING.md** - Complete request tracking guide
- **REQUEST_TRACKING_QUICK_REFERENCE.md** - Quick API reference
- **SERVICE_TRACKING_GUIDE.md** - Detailed service integration guide
- **SERVICE_TRACKING_SUMMARY.md** - This file

All documentation is ready for use!
