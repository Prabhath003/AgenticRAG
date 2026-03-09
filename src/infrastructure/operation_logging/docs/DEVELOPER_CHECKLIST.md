# Developer Checklist: Using Operation Logging

Use this checklist when implementing endpoints or background tasks that need operation logging.

---

## Quick Endpoint (Auto-Complete)

For endpoints that complete synchronously:

```python
# ✓ Checklist
- [ ] Import required modules
  from src.infrastructure.operation_logging import (
      operation_endpoint, log_service,
      OperationType, ServiceType
  )

- [ ] Add @operation_endpoint decorator
  @app.post("/api/endpoint")
  @operation_endpoint(
      OperationType.GET_KNOWLEDGE_BASE,  # ← Select appropriate type
      description_formatter=lambda **kw: "Fetch KB"  # ← Optional
  )
  async def endpoint(...):
      pass

- [ ] Extract api_key from parameters
  async def endpoint(..., api_key: str = Depends(verify_api_key)):
      # api_key will be automatically extracted by decorator
      pass

- [ ] Log services as they're used
  @operation_endpoint(OperationType.GET_KNOWLEDGE_BASE)
  async def get_kb(kb_id: str, api_key: str):
      result = db.get_kb(kb_id)
      log_service(
          ServiceType.MONGODB,
          estimated_cost_usd=0.01,
          description="Queried KB metadata"
      )
      return result

- [ ] No manual operation management needed
  # ✓ Operation automatically created on entry
  # ✓ Operation automatically logged on exit
```

---

## Background Task (Deferred Completion)

For endpoints that return immediately but have background work:

```python
# ✓ Checklist
- [ ] Understand the pattern
  Endpoint: Creates operation
  Background: Completes and logs operation

- [ ] Set auto_complete=False in decorator
  @operation_endpoint(
      OperationType.KNOWLEDGE_BASE_BUILD,
      auto_complete=False  # ← KEY: Don't auto-log
  )
  async def trigger_build(...):
      pass

- [ ] Extract operation context in endpoint
  from src.infrastructure.operation_logging import get_current_operation

  @operation_endpoint(..., auto_complete=False)
  async def trigger_build(kb_id: str, api_key: str):
      log_service(ServiceType.MONGODB, 0.01)
      # Context is active here

- [ ] Copy context when submitting to executor
  from contextvars import copy_context

  context = copy_context()
  executor.submit(context.run, background_work, kb_id, api_key)
  # ✓ Context preserved in background task

- [ ] Accept operation context in background function
  def background_work(kb_id: str, api_key: str):
      # ✓ get_current_operation() will work here
      pass

- [ ] Log services in background task
  def background_work(kb_id: str, api_key: str):
      log_service(ServiceType.FILE_PROCESSOR, 0.15)
      log_service(ServiceType.KNOWLEDGE_BASE_BUILD, 0.20)

- [ ] Call mark_operation_complete() when done
  def background_work(kb_id: str, api_key: str):
      try:
          log_service(ServiceType.FILE_PROCESSOR, 0.15)
          # ... long work ...
          mark_operation_complete()  # ✓ Logs operation
      except Exception as e:
          mark_operation_failed(str(e))  # ✓ Logs on error

- [ ] Handle exceptions properly
  from src.infrastructure.operation_logging import (
      mark_operation_complete,
      mark_operation_failed
  )

  try:
      # ... work ...
      mark_operation_complete()
  except Exception as e:
      mark_operation_failed(f"Error: {str(e)}")
      raise  # Re-raise after logging
```

---

## Service Logging

For every service call:

```python
# ✓ Checklist
- [ ] Identify service type
  # Choose from: MONGODB, FILE_PROCESSOR, KNOWLEDGE_BASE_BUILD, etc.
  ServiceType.MONGODB

- [ ] Calculate estimated cost
  estimated_cost_usd = 0.02
  # or query from your cost model

- [ ] Log the service
  log_service(
      ServiceType.MONGODB,
      estimated_cost_usd=0.02
  )

- [ ] Add optional details
  log_service(
      ServiceType.FILE_PROCESSOR,
      estimated_cost_usd=0.15,
      breakdown={
          "chunks": 100,
          "cost_per_chunk": 0.0015
      },
      description="Chunked 100 documents",
      metadata={"source": "pdf"}
  )

- [ ] Log INSIDE operation context
  ✓ Inside @operation_endpoint decorated function
  ✓ Inside with OperationContext(...): block
  ✓ Inside function called from above
  ❌ NOT outside operation context
```

---

## Database Queries

For querying operations:

```python
# ✓ Checklist
- [ ] Get database session
  from src.infrastructure.database import get_db_session
  from src.config import Config

  with get_db_session() as db:
      # Query here

- [ ] Query operations
  # Find single operation
  op = db[Config.OPERATION_LOGS_COLLECTION].find_one({
      "_id": operation_id
  })

  # Find user's operations
  ops = db[Config.OPERATION_LOGS_COLLECTION].find({
      "user_id_or_api_key": "user@example.com"
  })

  # Find completed operations
  completed = db[Config.OPERATION_LOGS_COLLECTION].find({
      "status": "completed"
  })

- [ ] Query services for operation
  services = db[Config.SERVICE_LOGS_COLLECTION].find({
      "operation_id": operation_id
  })

- [ ] Calculate costs
  total_cost = sum(s["estimated_cost_usd"] for s in services)

- [ ] Use string values in queries (not enums)
  db[Config.OPERATION_LOGS_COLLECTION].find({
      "status": "completed"  # ✓ String
      # NOT: status: TaskStatus.COMPLETED  (enum)
  })
```

---

## Testing

When testing your code:

```python
# ✓ Checklist
- [ ] Mock database operations
  from unittest.mock import patch, MagicMock

  @patch("src.infrastructure.database.get_db_session")
  def test_endpoint(mock_get_db):
      mock_db = MagicMock()
      mock_get_db.return_value.__enter__.return_value = mock_db
      # Test code

- [ ] Verify operation was created
  from src.infrastructure.operation_logging import get_current_operation

  op = get_current_operation()
  assert op is not None
  assert op.operation_type == OperationType.GET_KB

- [ ] Verify services were logged
  # Inside operation context:
  log_service(ServiceType.MONGODB, 0.01)
  op = get_current_operation()
  assert op.estimated_cost_usd == 0.01

- [ ] Test deferred completion
  @patch("src.infrastructure.database.get_db_session")
  async def test_deferred(mock_get_db):
      # Endpoint returns
      response = await trigger_build(...)

      # Database insert not called yet
      mock_get_db.return_value.__enter__.return_value.\
          __getitem__.return_value.insert_one.assert_not_called()

      # Background task completes
      mark_operation_complete()

      # Now database insert called
      mock_get_db.return_value.__enter__.return_value.\
          __getitem__.return_value.insert_one.assert_called_once()

- [ ] Verify operation status transitions
  assert op.status == TaskStatus.PENDING  # Initially
  # ... work ...
  mark_operation_complete()
  assert op.status == TaskStatus.COMPLETED

- [ ] Test error handling
  try:
      mark_operation_failed("Test error")
      op = get_current_operation()
      assert op.status == TaskStatus.FAILED
  finally:
      pass
```

---

## Common Mistakes

### ❌ Mistake 1: Forgetting the decorator

```python
# ❌ WRONG: No operation tracking
async def endpoint(...):
    log_service(ServiceType.MONGODB, 0.01)  # Warning: no operation context!

# ✓ CORRECT
@operation_endpoint(OperationType.GET_KB)
async def endpoint(...):
    log_service(ServiceType.MONGODB, 0.01)
```

### ❌ Mistake 2: Using old imports

```python
# ❌ WRONG: Not BSON optimized
from src.core.management.core_models import Operation

# ✓ CORRECT: BSON optimized
from src.infrastructure.operation_logging import Operation
```

### ❌ Mistake 3: Using model_dump() for database

```python
# ❌ WRONG: Not optimized for BSON
db[collection].insert_one(operation.model_dump())

# ✓ CORRECT: BSON optimized
db[collection].insert_one(operation.model_dump_bson())
```

### ❌ Mistake 4: Forgetting mark_operation_complete()

```python
# ❌ WRONG: Operation never logged
@operation_endpoint(..., auto_complete=False)
async def trigger_build(...):
    executor.submit(context.run, background_work, ...)
    return {"status": "queued"}

def background_work(...):
    # ... work ...
    # Forgot: mark_operation_complete()

# ✓ CORRECT: Always complete or fail
def background_work(...):
    try:
        # ... work ...
        mark_operation_complete()
    except Exception as e:
        mark_operation_failed(str(e))
```

### ❌ Mistake 5: Not copying context for executor

```python
# ❌ WRONG: Context lost in thread pool
executor.submit(background_work, kb_id, api_key)
# get_current_operation() returns None in background_work

# ✓ CORRECT: Context preserved
context = copy_context()
executor.submit(context.run, background_work, kb_id, api_key)
# get_current_operation() works in background_work
```

### ❌ Mistake 6: Missing api_key parameter

```python
# ❌ WRONG: api_key not extracted
@operation_endpoint(OperationType.GET_KB)
async def endpoint(kb_id: str):  # No api_key
    # Decorator can't extract user info

# ✓ CORRECT: api_key in parameters
@operation_endpoint(OperationType.GET_KB)
async def endpoint(kb_id: str, api_key: str = Depends(verify_api_key)):
    # Decorator extracts api_key for audit trail
```

---

## Quick Reference

### Imports Template

```python
from contextvars import copy_context
from fastapi import FastAPI, Depends

from src.infrastructure.operation_logging import (
    operation_endpoint,
    log_service,
    get_current_operation,
    mark_operation_complete,
    mark_operation_failed,
    OperationType,
    ServiceType,
)
from src.infrastructure.database import get_db_session
from src.config import Config

app = FastAPI()
```

### Decorator Template (Auto-Complete)

```python
@app.post("/api/endpoint")
@operation_endpoint(
    OperationType.GET_KNOWLEDGE_BASE,
    description_formatter=lambda **kw: "Description"
)
async def endpoint(
    request: Request,
    api_key: str = Depends(verify_api_key)
):
    log_service(ServiceType.MONGODB, 0.01)
    return {"result": "..."}
```

### Decorator Template (Deferred)

```python
@app.post("/api/trigger")
@operation_endpoint(
    OperationType.KNOWLEDGE_BASE_BUILD,
    auto_complete=False
)
async def trigger_endpoint(
    kb_id: str,
    api_key: str = Depends(verify_api_key)
):
    log_service(ServiceType.MONGODB, 0.01)

    context = copy_context()
    executor.submit(context.run, background_work, kb_id, api_key)

    return {"status": "queued"}

def background_work(kb_id: str, api_key: str):
    try:
        log_service(ServiceType.FILE_PROCESSOR, 0.15)
        # ... work ...
        mark_operation_complete()
    except Exception as e:
        mark_operation_failed(str(e))
        raise
```

---

## Validation Script

Run this to verify your implementation:

```python
# Check imports work
from src.infrastructure.operation_logging import (
    operation_endpoint,
    log_service,
    OperationType,
    ServiceType,
)

# Check models have BSON method
from src.infrastructure.operation_logging import Operation, Service
op = Operation(
    user_id_or_api_key="test",
    operation_type=OperationType.GET_KNOWLEDGE_BASE
)
assert hasattr(op, 'model_dump_bson')
assert callable(op.model_dump_bson)

# Check BSON output
bson_data = op.model_dump_bson()
assert isinstance(bson_data, dict)
assert isinstance(bson_data['operation_type'], str)  # ✓ String, not enum
assert isinstance(bson_data['status'], str)          # ✓ String, not enum

print("✓ All checks passed!")
```

---

## Help & Support

- **API Reference**: See README.md
- **Usage Guide**: See USAGE_GUIDE.md or USAGE_BSON_MODELS.md
- **Implementation Details**: See IMPLEMENTATION_SUMMARY.md
- **BSON Optimization**: See BSON_OPTIMIZATION.md
- **Troubleshooting**: Check README.md troubleshooting section
