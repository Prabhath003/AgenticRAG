# Task IDs for Queries & Chat Messages

## Overview

Task IDs are now generated automatically for **all intensive operations**:
- **Upload operations** (FILE_UPLOAD)
- **Query operations** (CHAT_MESSAGE, SEARCH)

This enables you to track costs for chat queries and searches the same way you track file uploads.

## Task ID Auto-Generation

The middleware automatically generates task_ids for:

```
UPLOAD OPERATIONS (task_type: "upload")
├─ FILE_UPLOAD → task_id: "task_xyz123"

QUERY OPERATIONS (task_type: "query")
├─ CHAT_MESSAGE → task_id: "task_abc456"
├─ SEARCH → task_id: "task_def789"
```

## Chat Query Cost Tracking

### Flow for Chat Messages

```
1. Client sends chat message
   POST /api/chat
   ↓
2. Middleware generates:
   - request_id: "req_abc123"
   - task_id: "task_xyz789"  ← For tracking chat cost
   ↓
3. Start tracking with task_id
   ├─ Detects task_type: "query"
   ├─ Generates task_id
   └─ Stores in request scope
   ↓
4. Chat endpoint processes:
   - TRANSFORMER: Semantic search (embed query, find relevant chunks)
   - OPENAI: LLM service (generate response with context)
   ↓
5. Track services_used:
   - transformer: ~$0.001
   - openai: ~$0.0045
   ↓
6. End tracking with services
   ↓
7. Client can query cost:
   GET /api/tasks/{task_id}/cost
   → Returns: {"total_cost_usd": 0.0055, ...}
```

### Getting Chat Query Task ID

**Option 1: From request metadata**
```python
# In your chat endpoint
task_id = request.scope.get("task_id")
# task_id will be: "task_abc123"
```

**Option 2: From request list**
```bash
# Get all chat requests for a session
curl "http://localhost:8000/api/requests?session_id=session_xyz&task_type=query"

# Response includes task_id for each:
{
  "requests": [
    {
      "request_id": "req_abc123",
      "task_id": "task_xyz789",
      "operation_type": "chat_message",
      "task_type": "query",
      "session_id": "session_xyz"
    }
  ]
}
```

## Search Operation Cost Tracking

### Flow for Search

```
1. Client sends search query
   POST /api/search
   ↓
2. Middleware generates:
   - request_id: "req_def456"
   - task_id: "task_search789"
   ↓
3. Search endpoint processes:
   - TRANSFORMER: Embed query, semantic search
   - Services: ~$0.001
   ↓
4. End tracking with services
   ↓
5. Get cost:
   GET /api/tasks/task_search789/cost
   → {"total_cost_usd": 0.001, "breakdown_by_service": {...}}
```

## API Usage Examples

### Track Chat Query Cost

```bash
# 1. Send chat message (task_id generated automatically)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "session_xyz",
    "message": "What are the sales figures?"
  }'

# Response should include task_id (from endpoint implementation)
# OR query to find it:

# 2. Get all requests for this session
curl "http://localhost:8000/api/requests?session_id=session_xyz&task_type=query"

# 3. Get cost for specific task
curl http://localhost:8000/api/tasks/{task_id}/cost

# Result:
{
  "task_id": "task_xyz789",
  "total_cost_usd": 0.0055,
  "period_start": "2025-01-15T10:30:45.123Z",
  "period_end": "2025-01-15T10:30:47.456Z",
  "breakdown_by_service": {
    "transformer": 0.001,
    "openai": 0.0045
  },
  "request_count": 1
}
```

### Track Search Cost

```bash
# 1. Send search query
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "company_123",
    "query": "revenue growth"
  }'

# 2. Get cost
curl "http://localhost:8000/api/requests?task_type=query&entity_id=company_123"

# 3. Find the search request
curl http://localhost:8000/api/tasks/{search_task_id}/cost
```

### Compare Chat vs Search Costs

```bash
# Get all query operations (chats + searches)
curl "http://localhost:8000/api/requests?task_type=query&page_size=100" | \
  jq '[.requests[] | {
    task_id,
    operation: .operation_type,
    cost: .total_cost_usd,
    entity: .entity_id,
    session: .session_id
  }]'

# Result shows both chat and search costs
```

### Monitor Session Costs

```bash
# Get total cost for a chat session
curl "http://localhost:8000/api/cost-report?session_id=session_xyz"

# Result:
{
  "breakdown_by_task_type": {
    "query": 0.0165  # Total chat costs
  },
  "breakdown_by_operation": {
    "chat_message": 0.0155
  },
  "total_requests": 3,
  "total_cost_usd": 0.0165,
  "average_cost_per_request": 0.0055
}
```

## Endpoint Integration

### Chat Endpoint Enhancement

To include task_id in chat response:

```python
@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: Request, chat_request: ChatRequest):
    # Get task_id from middleware
    task_id = request.scope.get("task_id")

    # ... process chat ...

    # Return task_id so client can track cost
    return ChatResponse(
        content=response,
        task_id=task_id,  # Add to response
        ...
    )
```

### Search Endpoint Enhancement

```python
@app.post("/api/search", response_model=SearchResponse)
async def search(request: Request, search_request: SearchRequest):
    # Get task_id from middleware
    task_id = request.scope.get("task_id")

    # ... perform search ...

    # Return task_id
    return SearchResponse(
        results=results,
        task_id=task_id,  # Add to response
        ...
    )
```

## Client Patterns for Chat Cost Tracking

### Pattern 1: Track Single Chat Message Cost

```python
# Send message
response = client.post("/api/chat", json={
    "session_id": "session_123",
    "message": "What is total revenue?"
})

task_id = response.json().get("task_id")

# Check cost later
cost_response = client.get(f"/api/tasks/{task_id}/cost")
cost_data = cost_response.json()

print(f"Chat message cost: ${cost_data['total_cost_usd']:.4f}")
print(f"Services breakdown: {cost_data['breakdown_by_service']}")
```

### Pattern 2: Track Session Total Cost

```python
session_id = "session_123"

# Send multiple chat messages
for message in user_messages:
    client.post("/api/chat", json={
        "session_id": session_id,
        "message": message
    })

# Get total session cost
cost_response = client.get(
    "/api/cost-report",
    params={"session_id": session_id}
)

session_cost = cost_response.json()["total_cost_usd"]
print(f"Total session cost: ${session_cost:.4f}")
```

### Pattern 3: Monitor Real-Time Costs

```python
task_ids = []

# Send chat message
response = client.post("/api/chat", json={
    "session_id": session_id,
    "message": message
})
task_ids.append(response.json()["task_id"])

# Monitor cumulative cost
total_cost = 0.0
for task_id in task_ids:
    cost_data = client.get(f"/api/tasks/{task_id}/cost").json()
    total_cost += cost_data["total_cost_usd"]

print(f"Running total cost: ${total_cost:.4f}")
```

### Pattern 4: Compare Chat Types

```python
# Get cost breakdown between uploads and queries
cost_report = client.get("/api/cost-report").json()

upload_cost = cost_report["breakdown_by_task_type"].get("upload", 0)
query_cost = cost_report["breakdown_by_task_type"].get("query", 0)

print(f"Upload cost: ${upload_cost:.4f}")
print(f"Query cost: ${query_cost:.4f}")
print(f"Ratio: {upload_cost / query_cost:.2f}x")
```

## Query Strategies

### Find All Query Tasks for Entity

```bash
curl "http://localhost:8000/api/requests?entity_id=company_123&task_type=query&page_size=50" | \
  jq '.requests[] | {task_id, cost: .total_cost_usd, type: .operation_type}'
```

### Find Most Expensive Chat Message

```bash
curl "http://localhost:8000/api/requests?operation_type=chat_message&page_size=100" | \
  jq '.requests | max_by(.total_cost_usd)'
```

### Compare Search vs Chat Costs

```bash
curl "http://localhost:8000/api/requests?task_type=query&page_size=100" | \
  jq 'group_by(.operation_type) | map({
    type: .[0].operation_type,
    count: length,
    total_cost: map(.total_cost_usd) | add
  })'
```

### Get Average Query Cost

```bash
curl "http://localhost:8000/api/requests?task_type=query&page_size=100" | \
  jq '[.requests[].total_cost_usd] | add / length'
```

## Data Model

Request metrics for chat queries now include:

```json
{
  "request_id": "req_abc123",
  "task_id": "task_xyz789",           ← NEW: For chat query tracking
  "operation_type": "chat_message",
  "task_type": "query",               ← NEW: Indicates it's a query
  "entity_id": "company_123",
  "session_id": "session_xyz",
  "timestamp": "2025-01-15T10:30:45Z",
  "processing_time_ms": 2150.5,
  "services_used": [
    {
      "service_type": "transformer",
      "cost_usd": 0.001,
      "breakdown": {
        "query_tokens": 25,
        "embedding_dimension": 1536
      }
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

## Cost Estimation for Queries

### TRANSFORMER Service (Semantic Search)
```
Query embedding: ~$0.001 per search
- Input: 25-100 tokens
- Model: sentence-transformers
- Cost: ~$0.0001/1K tokens
```

### OPENAI Service (LLM Generation)
```
Response generation: ~0.0045 per message
- Input: 400-1000 tokens (context + query)
- Output: 100-300 tokens (answer)
- GPT-4 pricing:
  - Input: $0.03/1K tokens
  - Output: $0.06/1K tokens
```

### Total Chat Query Cost
```
Average: $0.005 - $0.010 per message
- 1000 messages: $5 - $10
- 10000 messages: $50 - $100
- 100000 messages: $500 - $1000
```

## Best Practices for Query Cost Tracking

1. **Always Include task_id in Response**: Return to client for tracking
2. **Capture All Services**: Don't miss TRANSFORMER or OPENAI costs
3. **Use Session ID**: Group related queries together
4. **Monitor Costs Real-time**: Poll /api/tasks/{task_id}/cost
5. **Set Alerts**: Alert if query costs exceed thresholds
6. **Batch Analysis**: Use /api/cost-report for aggregated view
7. **Track Per Entity**: Know which entities consume most queries
8. **Optimize Expensive Queries**: Identify and optimize expensive operations

## Integration Checklist for Queries

- [ ] Middleware generates task_id for chat_message operations
- [ ] Middleware generates task_id for search operations
- [ ] Chat endpoint returns task_id in response
- [ ] Search endpoint returns task_id in response
- [ ] Track TRANSFORMER service costs in chat
- [ ] Track OPENAI service costs in chat
- [ ] Track TRANSFORMER service costs in search
- [ ] Call end_request with services_used
- [ ] Test task_id lookup for queries
- [ ] Test cost report for queries
- [ ] Document query cost tracking in API

## See Also

- [TASK_COST_TRACKING.md](TASK_COST_TRACKING.md) - Task tracking overview
- [SERVICE_TRACKING_GUIDE.md](SERVICE_TRACKING_GUIDE.md) - Service integration
- [REQUEST_TRACKING.md](REQUEST_TRACKING.md) - Core tracking system
