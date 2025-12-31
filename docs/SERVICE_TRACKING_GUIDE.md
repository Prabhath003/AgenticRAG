# Service & Task Tracking Integration Guide

## Overview

The request tracking system now integrates with the metrics system to track:
1. **Task Types**: UPLOAD vs QUERY operations
2. **Services Used**: OpenAI, FILE_PROCESSOR, TRANSFORMER, NATIVE
3. **Cost Per Service**: Breakdown of costs by service type
4. **Cost Per Task Type**: Upload costs vs Query costs

## Task Types

### UPLOAD Tasks
File upload and processing operations

**Operation:** `file_upload` (POST /api/entities/{id}/files)

**Services Used:**
- `FILE_PROCESSOR`: Processes file format (PDF, DOCX, etc.)
- `TRANSFORMER`: GPU-intensive encoding service for building vector store

**Cost Breakdown:**
- `pages_processed`: Number of pages
- `encoding_dimension`: Vector embedding dimensions
- `tokens_encoded`: Tokens processed by transformer

**Example Metrics:**
```json
{
  "request_id": "req_abc123",
  "task_type": "upload",
  "operation_type": "file_upload",
  "services_used": [
    {
      "service_type": "file_processor",
      "cost_usd": 0.02,
      "breakdown": {
        "pages": 10,
        "file_format": "pdf"
      }
    },
    {
      "service_type": "transformer",
      "cost_usd": 0.15,
      "breakdown": {
        "chunks": 45,
        "embedding_dimension": 1536,
        "gpu_intensive": true
      }
    }
  ],
  "total_cost_usd": 0.17
}
```

### QUERY Tasks
Search and chat operations that consume services

**Operations:**
- `chat_message` (POST /api/chat)
- `search` (POST /api/search)

**Chat Services Used:**
1. `TRANSFORMER`: Semantic search against vector store
   - Embeds user query
   - Searches vector database

2. `OPENAI`: LLM for generating response
   - Processes context + query
   - Generates answer

**Search Services Used:**
1. `TRANSFORMER`: Semantic search
   - Embeds query
   - Similarity search on vectors

**Cost Breakdown:**
```json
{
  "request_id": "req_xyz789",
  "task_type": "query",
  "operation_type": "chat_message",
  "services_used": [
    {
      "service_type": "transformer",
      "cost_usd": 0.001,
      "breakdown": {
        "query_tokens": 25,
        "dimension": 1536
      }
    },
    {
      "service_type": "openai",
      "cost_usd": 0.0045,
      "breakdown": {
        "model": "gpt-4",
        "prompt_tokens": 450,
        "completion_tokens": 150,
        "total_tokens": 600
      }
    }
  ],
  "total_cost_usd": 0.0055
}
```

## Integrating Service Tracking

### Step 1: Capture Services in Endpoint Logic

When your endpoint processes a request and uses services, capture the metrics:

```python
from src.infrastructure.metrics import Service, ServiceType

# Example: File upload endpoint
@app.post("/api/entities/{entity_id}/files")
async def upload_file(entity_id: str, file: UploadFile):
    try:
        # ... file processing logic ...

        # Capture services used
        services_used = []

        # FILE_PROCESSOR service
        if file_processor_used:
            services_used.append({
                "service_type": ServiceType.FILE_PROCESSOR.value,
                "estimated_cost_usd": 0.02,
                "breakdown": {
                    "pages": 10,
                    "file_format": "pdf"
                }
            })

        # TRANSFORMER service (encoding)
        if encoding_used:
            services_used.append({
                "service_type": ServiceType.TRANSFORMER.value,
                "estimated_cost_usd": 0.15,
                "breakdown": {
                    "chunks": 45,
                    "embedding_dimension": 1536
                }
            })

        # ... rest of logic ...

        # End tracking with services
        request_tracker.end_request(
            request_id,
            status_code=200,
            services_used=services_used
        )

    except Exception as e:
        request_tracker.end_request(
            request_id,
            status_code=500,
            services_used=services_used,
            error_message=str(e)
        )
```

### Step 2: Chat Operations

For chat messages, track both TRANSFORMER (search) and OPENAI (LLM):

```python
@app.post("/api/chat")
async def chat(request: ChatRequest):
    try:
        services_used = []

        # TRANSFORMER for semantic search
        services_used.append({
            "service_type": ServiceType.TRANSFORMER.value,
            "estimated_cost_usd": 0.001,
            "breakdown": {
                "query_tokens": 25,
                "dimension": 1536
            }
        })

        # Call agent to get response
        response = await agent.research_question(...)

        # OPENAI for LLM
        # Extract token usage from response if available
        services_used.append({
            "service_type": ServiceType.OPENAI.value,
            "estimated_cost_usd": 0.0045,
            "breakdown": {
                "model": "gpt-4",
                "prompt_tokens": 450,
                "completion_tokens": 150
            }
        })

        # Track request
        request_tracker.end_request(
            request_id,
            status_code=200,
            services_used=services_used
        )

    except Exception as e:
        request_tracker.end_request(request_id, status_code=500, error_message=str(e))
```

### Step 3: Search Operations

For search, track TRANSFORMER service:

```python
@app.post("/api/search")
async def search(request: SearchRequest):
    try:
        services_used = []

        # TRANSFORMER for semantic search
        services_used.append({
            "service_type": ServiceType.TRANSFORMER.value,
            "estimated_cost_usd": 0.001,
            "breakdown": {
                "query_tokens": len(request.query.split()),
                "dimension": 1536,
                "k_results": request.k
            }
        })

        # Perform search
        results = search_entity_scoped(...)

        request_tracker.end_request(
            request_id,
            status_code=200,
            services_used=services_used
        )

    except Exception as e:
        request_tracker.end_request(request_id, status_code=500, error_message=str(e))
```

## Cost Report Structure

### Breakdown by Task Type

```json
{
  "breakdown_by_task_type": {
    "upload": 2.50,
    "query": 1.80
  }
}
```

This shows:
- **upload**: Total cost of all file uploads (FILE_PROCESSOR + TRANSFORMER)
- **query**: Total cost of all searches and chats (TRANSFORMER + OPENAI)

### Breakdown by Service

```json
{
  "breakdown_by_service": {
    "openai": 1.45,
    "file_processor": 0.40,
    "transformer": 2.35,
    "native": 0.10
  }
}
```

### Breakdown by Entity

Shows cost per entity, including all operations:

```json
{
  "breakdown_by_entity": {
    "company_123": 2.15,
    "company_456": 1.80
  }
}
```

### Breakdown by Session

Shows cost per chat session:

```json
{
  "breakdown_by_session": {
    "session_abc": 0.65,
    "session_def": 0.80
  }
}
```

## API Examples

### Get Cost Report with Task Type Breakdown

```bash
curl http://localhost:8000/api/cost-report
```

Response:
```json
{
  "period_start": "2025-01-15T00:00:00Z",
  "period_end": "2025-01-15T23:59:59Z",
  "total_requests": 150,
  "total_cost_usd": 4.30,
  "breakdown_by_service": {
    "openai": 1.45,
    "file_processor": 0.40,
    "transformer": 2.35,
    "native": 0.10
  },
  "breakdown_by_task_type": {
    "upload": 2.50,
    "query": 1.80
  },
  "breakdown_by_operation": {
    "file_upload": 2.50,
    "chat_message": 1.50,
    "search": 0.30
  },
  "breakdown_by_entity": {
    "company_123": 2.15,
    "company_456": 1.80,
    "company_789": 0.35
  },
  "breakdown_by_session": {
    "session_abc": 0.85,
    "session_def": 0.65
  },
  "average_cost_per_request": 0.0287
}
```

### Get Cost for Specific Task Type

```bash
# Get all costs for uploads only
curl "http://localhost:8000/api/requests?page=1" | \
  jq '.requests[] | select(.task_type == "upload")'
```

### Cost Analysis

```bash
# Calculate total upload vs query costs
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_task_type | "Upload: \(.upload), Query: \(.query)"'

# Find most expensive service
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_service | to_entries | max_by(.value)'

# Get cost per entity sorted
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_entity | to_entries | sort_by(.value) | reverse'
```

## Service Cost Estimation

### File Processor Costs

```python
# Approximate: $0.02 per page
pages = 10
cost = pages * 0.02  # $0.20
```

### Transformer Costs (Encoding)

```python
# Approximate: $0.0001 per 1000 tokens
tokens = 45000
cost = (tokens / 1000) * 0.0001  # $0.0045
```

### OpenAI Costs

For GPT-4:
```python
# Input: $0.03 per 1K tokens
# Output: $0.06 per 1K tokens
prompt_tokens = 450
completion_tokens = 150
input_cost = (prompt_tokens / 1000) * 0.03
output_cost = (completion_tokens / 1000) * 0.06
total = input_cost + output_cost
```

## Tracking Per Request, Per Session, Per Entity

### Per Request
Each request tracks all services used:
```python
GET /api/requests/{request_id}
```

### Per Session
Filter requests by session:
```python
GET /api/requests?session_id=session_xyz789
GET /api/cost-report?session_id=session_xyz789
```

### Per Entity
Filter requests by entity:
```python
GET /api/requests?entity_id=company_123
GET /api/cost-report?entity_id=company_123
```

## Example Implementation

Here's a complete example integrating service tracking:

```python
from src.infrastructure.metrics import ServiceType

@app.post("/api/entities/{entity_id}/files")
async def upload_file(entity_id: str, file: UploadFile):
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    request_tracker.start_request(
        request_id,
        RequestOperationType.FILE_UPLOAD,
        "/api/entities/{entity_id}/files",
        "POST",
        entity_id=entity_id,
        task_type="upload"
    )

    services_used = []

    try:
        # Process file
        file_bytes = await file.read()

        # Track FILE_PROCESSOR
        pages = estimate_pages(file)
        services_used.append({
            "service_type": ServiceType.FILE_PROCESSOR.value,
            "estimated_cost_usd": pages * 0.02,
            "breakdown": {
                "pages": pages,
                "file_format": file.filename.split(".")[-1]
            }
        })

        # Index and encode (TRANSFORMER)
        chunks = chunk_document(file_bytes)
        embeddings = encode_chunks(chunks)  # GPU-intensive

        services_used.append({
            "service_type": ServiceType.TRANSFORMER.value,
            "estimated_cost_usd": len(chunks) * 0.003,
            "breakdown": {
                "chunks": len(chunks),
                "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
                "embedding_dimension": 384
            }
        })

        # Store in vector DB
        index_document_entity_scoped(entity_id, embeddings)

        # Track success
        request_tracker.end_request(
            request_id,
            status_code=200,
            services_used=services_used
        )

        return {"success": True, "services_used": services_used}

    except Exception as e:
        request_tracker.end_request(
            request_id,
            status_code=500,
            services_used=services_used,
            error_message=str(e)
        )
        raise
```

## Monitoring & Alerts

```bash
# Check for expensive requests
curl http://localhost:8000/api/cost-report | \
  jq 'if .total_cost_usd > 50 then "HIGH COST ALERT" else "OK" end'

# Monitor upload vs query ratio
curl http://localhost:8000/api/cost-report | \
  jq '(.breakdown_by_task_type.upload / .total_cost_usd * 100 | round) as $pct |
      "Upload: \($pct)%, Query: \(100 - $pct)%"'

# Track service efficiency
curl http://localhost:8000/api/cost-report | \
  jq '.breakdown_by_service | map_values(. / 0.0287 | round) |
      "Service requests (approx) - OpenAI: \(.openai), Transformer: \(.transformer)"'
```

## Best Practices

1. **Always Capture Services**: Track every service used in your operations
2. **Accurate Breakdown**: Include relevant metrics in the breakdown dict
3. **Error Tracking**: Record services even on failures for cost accounting
4. **Per-Request Cost**: Aggregate individual service costs accurately
5. **Task Type**: Always set task_type to "upload" or "query" where applicable

## Future Enhancements

- [ ] Real-time cost warnings when exceeding thresholds
- [ ] Cost optimization recommendations
- [ ] Service usage patterns analysis
- [ ] Predictive cost forecasting
- [ ] Automatic service selection based on cost
