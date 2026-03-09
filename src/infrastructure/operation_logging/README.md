# Operation & Service Logging Infrastructure

Context-aware logging system for tracking API operations and service usage, similar to Python's built-in `logging` library. Automatically detects scope using context variables and links services to operations without explicit passing.

## Why This Matters

In microservices and complex APIs, you need to:
- Track which user initiated each operation
- Know which services (databases, APIs, ML models) each operation uses
- Calculate costs per operation
- Maintain audit trails

**Before this system**: Manual logging code at every step, error-prone, verbose.
**After this system**: Automatic scope detection, minimal boilerplate, consistent tracking.

## How Python's Logging Inspired This

Python's `logging` module uses clever scope detection:
```python
# logging library uses __name__ and stack inspection
logger = logging.getLogger(__name__)  # Automatically gets module context
logger.info("Message")  # Logger knows which module it's from
```

We use a similar pattern with `contextvars`:
```python
# Our system uses context variables for async-safe scope detection
@operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
async def endpoint(...):
    log_service(ServiceType.FILE_PROCESSOR, cost=0.15)  # Finds operation automatically
```

## Core Components

### 1. OperationContext (Context Manager)

Tracks a single operation and all services used within it.

```python
with OperationContext(api_key, OperationType.CREATE_KNOWLEDGE_BASE) as op:
    # operation automatically created with unique ID
    # operation automatically logged on exit
    pass
```

**Handles**:
- Creating Operation entry with UUID
- Setting as current operation in thread-local context
- Tracking status transitions (PENDING → COMPLETED/FAILED)
- Database logging on exit
- Exception handling and status updates

### 2. operation_endpoint (Decorator)

Wraps FastAPI endpoints in operation context automatically.

```python
@operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
async def endpoint(request, api_key: str):
    # operation context created automatically
    # api_key extracted for audit trail
    log_service(ServiceType.MONGODB, cost=0.02)
    # operation logged on endpoint exit
    pass
```

**Handles**:
- Extracting api_key from endpoint kwargs
- Creating operation context on entry
- Logging on exit (success or failure)
- Exception propagation

### 3. log_service (Service Logging)

Logs service usage and links to current operation.

```python
log_service(
    ServiceType.FILE_PROCESSOR,
    estimated_cost_usd=0.15,
    breakdown={"chunks": 100, "cost_per_chunk": 0.0015}
)
```

**Handles**:
- Creating Service entry with UUID
- Finding current operation from context
- Adding service ID to operation.services_used
- Updating operation's estimated cost
- Database logging

### 4. Context Variables

Thread-safe, async-safe tracking of current operation.

```python
_current_operation: ContextVar[Optional[Operation]]  # Current operation
_operation_stack: ContextVar[List[Operation]]        # Nested operations
```

## Quick Start

### Installation

```python
# Already in: src/infrastructure/operation_logging/
from src.infrastructure.operation_logging import (
    OperationContext,
    operation_endpoint,
    log_service,
    get_current_operation,
)
from src.core.management.core_models import OperationType, ServiceType
```

### Basic Usage

#### In FastAPI Endpoints (Recommended)

```python
from fastapi import FastAPI
from src.infrastructure.operation_logging import operation_endpoint, log_service
from src.core.management.core_models import OperationType, ServiceType

@app.post("/knowledge-base/create")
@operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
async def create_knowledge_base(request, api_key: str):
    # Operation automatically created and tracked

    result = task_manager.create_knowledge_base(
        title=request.title,
    )

    # Log the service call
    log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.02,
        description="Inserted KB entry"
    )

    # On endpoint exit: operation automatically logged
    return {"success": True, "kb_id": result["_id"]}
```

#### In Regular Functions

```python
from src.infrastructure.operation_logging import OperationContext, log_service

def process_knowledge_base(api_key, kb_data):
    with OperationContext(
        user_id_or_api_key=api_key,
        operation_type=OperationType.CREATE_KNOWLEDGE_BASE,
        description=f"Process KB: {kb_data['title']}"
    ):
        # Do work...
        result = _insert_kb(kb_data)

        # Log services
        log_service(ServiceType.MONGODB, cost=0.02)
        log_service(ServiceType.FILE_PROCESSOR, cost=0.10)

        # On exit: operation automatically logged
        return result
```

## Database Schema

### OPERATION_LOGS_COLLECTION

```json
{
  "_id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id_or_api_key": "user@example.com",
  "operation_type": "create_knowledge_base",
  "services_used": [
    "550e8400-e29b-41d4-a716-446655440001",
    "550e8400-e29b-41d4-a716-446655440002"
  ],
  "estimated_cost_usd": 0.17,
  "actual_cost_usd": 0.17,
  "status": "completed",
  "created_at": "2025-12-03T10:00:00.000Z",
  "completed_at": "2025-12-03T10:00:00.500Z",
  "description": "Create KB 'My Knowledge Base'",
  "metadata": {
    "kb_title": "My Knowledge Base",
  }
}
```

### SERVICE_LOGS_COLLECTION

```json
{
  "_id": "550e8400-e29b-41d4-a716-446655440001",
  "user_id_or_api_key": "user@example.com",
  "service_type": "mongodb",
  "breakdown": {
    "operation": "insert_one",
    "collection": "knowledge_bases",
    "document_size_bytes": 2048
  },
  "estimated_cost_usd": 0.02,
  "actual_cost_usd": 0.02,
  "status": "completed",
  "created_at": "2025-12-03T10:00:00.000Z",
  "completed_at": "2025-12-03T10:00:00.000Z",
  "description": "Insert KB entry",
  "metadata": null
}
```

## API Reference

### OperationContext

Context manager for automatic operation tracking.

```python
with OperationContext(
    user_id_or_api_key: str,          # User identifier or API key
    operation_type: OperationType,     # Type of operation
    description: Optional[str] = None, # Operation description
    metadata: Optional[Dict] = None    # Additional metadata
) as operation:
    # operation is current operation
    # On exit: automatically logged to OPERATION_LOGS_COLLECTION
```

### operation_endpoint

Decorator for FastAPI endpoints.

```python
@operation_endpoint(
    operation_type: OperationType,
    description_formatter: Optional[Callable] = None
)
async def endpoint(request, api_key: str):
    # operation context created automatically
    pass

# With description formatter:
def format_desc(request, **kwargs):
    return f"Create: {request.title}"

@operation_endpoint(
    OperationType.CREATE_KNOWLEDGE_BASE,
    description_formatter=format_desc
)
async def endpoint(request, api_key: str):
    pass
```

### log_service

Log service usage within operation.

```python
service = log_service(
    service_type: ServiceType,                      # Service type
    estimated_cost_usd: float = 0.0,               # Service cost
    breakdown: Optional[Dict[str, Any]] = None,    # Cost breakdown
    description: Optional[str] = None,              # Service description
    metadata: Optional[Dict[str, Any]] = None      # Additional data
) -> Optional[Service]

# Returns Service object or None if no active operation
```

### Helper Functions

```python
# Get current operation from context
operation = get_current_operation() -> Optional[Operation]

# Get operation stack (nested contexts)
stack = get_operation_stack() -> List[Operation]

# Mark operation as completed
mark_operation_complete(actual_cost_usd: Optional[float] = None)

# Mark operation as failed
mark_operation_failed(error_description: str)

# Get operation summary
summary = get_operation_summary() -> Optional[Dict[str, Any]]
```

## Key Features

### 1. Automatic Scope Detection

No need to pass operation objects around:

```python
# Bad (old way)
def process(operation_id):
    result = db.insert(...)
    log_operation_service(operation_id, ServiceType.MONGODB, cost=0.02)

# Good (new way)
def process():
    result = db.insert(...)
    log_service(ServiceType.MONGODB, cost=0.02)  # Finds operation automatically
```

### 2. Nested Operations

Support for hierarchical operations:

```python
@operation_endpoint(OperationType.CREATE_KNOWLEDGE_BASE)
async def create_kb():
    log_service(ServiceType.MONGODB, cost=0.02)  # Linked to CREATE_KB

    with OperationContext(api_key, OperationType.EMBEDDING):
        # Nested operation
        log_service(ServiceType.TRANSFORMER, cost=0.10)  # Linked to EMBEDDING

    log_service(ServiceType.KNOWLEDGE_BASE_BUILD, cost=0.15)  # Back to CREATE_KB
```

### 3. Exception Handling

Automatic failure tracking:

```python
try:
    with OperationContext(api_key, OperationType.CREATE_KB):
        if invalid_data:
            raise ValueError("Invalid data")
        # ...
except ValueError:
    # Operation automatically marked as FAILED
    # Status set to failed, error description captured
    # Logged to database
    pass
```

### 4. Cost Tracking

Accumulate service costs:

```python
with OperationContext(api_key, OperationType.CREATE_KB):
    log_service(ServiceType.MONGODB, 0.02)      # Total: 0.02
    log_service(ServiceType.FILE_PROCESSOR, 0.10)  # Total: 0.12
    log_service(ServiceType.TRANSFORMER, 0.08)  # Total: 0.20

# Operation.estimated_cost_usd = 0.20 (automatically)
```

### 5. Thread & Async Safe

Uses `contextvars` for thread-local and async-safe context:

```python
async def concurrent_operations():
    async def op1():
        with OperationContext(user1, OperationType.CREATE_KB):
            log_service(ServiceType.MONGODB, 0.02)  # Linked to op1

    async def op2():
        with OperationContext(user2, OperationType.CREATE_KB):
            log_service(ServiceType.MONGODB, 0.02)  # Linked to op2

    # No context pollution
    await asyncio.gather(op1(), op2())
```

## Architecture

```
API Endpoint
    ↓
@operation_endpoint decorator
    ↓
OperationContext.__enter__() creates Operation
    ↓
_current_operation ContextVar set
    ↓
Endpoint code executes
    ↓
log_service() called
    ↓
Gets current operation from context
    ↓
Creates Service entry
    ↓
Updates operation.services_used
    ↓
Logs to SERVICE_LOGS_COLLECTION
    ↓
Endpoint completes
    ↓
OperationContext.__exit__()
    ↓
Sets operation.status = COMPLETED/FAILED
    ↓
Logs to OPERATION_LOGS_COLLECTION
    ↓
Context variables restored
```

## Best Practices

1. **Always use @operation_endpoint on API endpoints**
2. **Use OperationContext for functions called from endpoints**
3. **Log services at the point of use** (not later)
4. **Provide meaningful descriptions** for operations and services
5. **Set breakdown details** for cost analysis
6. **Use actual_cost_usd** when available (not just estimated)
7. **Handle exceptions inside operation context** for proper failure tracking

## Files in This Module

- `__init__.py` - Public API exports
- `operation_context.py` - Core implementation
- `README.md` - This file
- `USAGE_GUIDE.md` - Detailed usage examples
- `INTEGRATION_EXAMPLE.md` - Integration with knowledge_base_manager

## Related Models

- `src/core/management/core_models.py`:
  - `Operation` - Tracks operations
  - `Service` - Tracks service usage
  - `OperationType` - Types of operations
  - `ServiceType` - Types of services
  - `TaskStatus` - Operation/service status

## Testing

```python
from src.infrastructure.operation_logging import OperationContext, log_service
from src.core.management.core_models import OperationType, ServiceType

def test_operation_context():
    with OperationContext("test_user", OperationType.CREATE_KNOWLEDGE_BASE) as op:
        assert op._id is not None
        assert op.status == TaskStatus.PENDING

        log_service(ServiceType.MONGODB, 0.05)
        log_service(ServiceType.FILE_PROCESSOR, 0.10)

        assert len(op.services_used) == 2
        assert op.estimated_cost_usd == 0.15

    # After exiting context:
    assert op.status == TaskStatus.COMPLETED
    assert op.completed_at is not None
    # Operation logged to database
```

## Troubleshooting

### Service logged outside operation context
```
WARNING: log_service called without active operation context: file_processor
```
**Solution**: Ensure log_service is called inside operation context

### Services not linking to operation
```
# Check if operation is active:
from src.infrastructure.operation_logging import get_current_operation
op = get_current_operation()  # Should not be None
```

### Nested operations confusing logs
```
# Use get_operation_stack() to debug:
from src.infrastructure.operation_logging import get_operation_stack
stack = get_operation_stack()
for i, op in enumerate(stack):
    print(f"  Level {i}: {op.operation_type.value}")
```

## Performance

- **Overhead**: Minimal - just context variable lookup (~1μs)
- **Memory**: One Operation object per active operation (~500 bytes)
- **Database**: Batch writes with existing infrastructure

## Future Enhancements

- [ ] Operation & service querying by user/operation_type/status
- [ ] Cost analysis dashboards
- [ ] SLA tracking (duration, cost)
- [ ] Rate limiting per user per operation type
- [ ] Automatic retry with exponential backoff
- [ ] Operation templates for common patterns

---

**Related**: [USAGE_GUIDE.md](USAGE_GUIDE.md) | [INTEGRATION_EXAMPLE.md](INTEGRATION_EXAMPLE.md)
