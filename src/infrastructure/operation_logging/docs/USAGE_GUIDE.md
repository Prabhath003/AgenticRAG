# Operation & Service Logging - Usage Guide

Context-aware logging system for operations and services, similar to Python's `logging` library. Automatically detects scope and links services to operations.

## How It Works

Python's `logging` library uses `__name__` and stack inspection to identify where logs come from. This system uses **context variables** (`contextvars`) for thread-safe, async-safe automatic scope detection.

### Key Concepts

1. **Operation**: A top-level action initiated by a user (e.g., "create knowledge base")
2. **Service**: External services used within an operation (e.g., file processor, database)
3. **Context**: Thread-local (async-safe) tracking of current operation
4. **Automatic Linking**: Services automatically know which operation they belong to

## Implementation Details

### Context Variables (Like Python's logging)

```python
_current_operation: ContextVar[Optional[Operation]]  # Current operation in scope
_operation_stack: ContextVar[List[Operation]]        # Nested operation stack
```

These are similar to how Python's `logging` module uses thread-local storage to track logger names.

## Usage Patterns

### Pattern 1: Decorator on FastAPI Endpoints (Recommended)

```python
from fastapi import FastAPI
from src.infrastructure.operation_logging import operation_endpoint, log_service
from src.core.management.core_models import OperationType, ServiceType

app = FastAPI()

@app.post("/knowledge-base/create")
@operation_endpoint(
    OperationType.CREATE_KNOWLEDGE_BASE,
    description_formatter=lambda request, **kw: f"Create KB: {request.title}"
)
async def create_knowledge_base(request, api_key: str):
    """
    Endpoint is wrapped in operation context automatically.

    Flow:
    1. @operation_endpoint decorator creates OperationContext
    2. Operation entry created and added to context
    3. api_key extracted from kwargs for audit trail
    4. When service is called: log_service() finds current operation automatically
    5. Service logged and linked to operation
    6. On endpoint exit: operation logged to database
    """
    # Operation created automatically by decorator
    # (No need to create context or pass operation around)

    # Call your service logic
    result = process_knowledge_base(request)

    # Log the service call - automatically linked to current operation
    log_service(
        ServiceType.FILE_PROCESSOR,
        estimated_cost_usd=0.15,
        breakdown={"chunks": 100, "cost_per_chunk": 0.0015}
    )

    return {"success": True, "kb_id": result["_id"]}


# In your service function (no need to pass operation around):
def process_knowledge_base(request):
    # ... processing ...

    # Service logging is automatic - finds current operation from context
    log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.05,
        description="Stored KB entry"
    )

    return {"_id": kb_id}
```

### Pattern 2: Context Manager

```python
from src.infrastructure.operation_logging import OperationContext, log_service
from src.core.management.core_models import OperationType, ServiceType

def create_knowledge_base_sync(api_key, request):
    """Synchronous function using context manager"""

    with OperationContext(
        user_id_or_api_key=api_key,
        operation_type=OperationType.CREATE_KNOWLEDGE_BASE,
        description=f"Create KB: {request.title}"
    ) as operation:
        # operation is in context automatically

        # Do work
        result = process_knowledge_base(request)

        # Log service - automatically linked
        log_service(
            ServiceType.FILE_PROCESSOR,
            estimated_cost_usd=0.15
        )

        # On exit: operation automatically logged to database
        # Status transition: PENDING -> COMPLETED (if no exception)

    return result
```

### Pattern 3: Nested Operations

```python
@operation_endpoint(OperationType.QUERY_KNOWLEDGE_BASE)
async def query_kb(kb_id: str, query: str, api_key: str):
    """Nested operations work automatically"""

    # Parent operation created by decorator
    log_service(ServiceType.MONGODB, cost=0.05)  # Linked to parent

    # Nested operation
    with OperationContext(
        api_key,
        OperationType.EMBEDDING,
        description="Generate embeddings"
    ) as nested_op:
        # This is a child operation
        log_service(ServiceType.TRANSFORMER, cost=0.10)  # Linked to nested_op

    # Back to parent operation
    log_service(ServiceType.KNOWLEDGE_BASE_QUERY, cost=0.20)  # Linked to parent

    return results
```

## API Reference

### OperationContext (Context Manager)

```python
with OperationContext(
    user_id_or_api_key: str,
    operation_type: OperationType,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) as operation:
    # operation is current in context
    # On exit: logged to OPERATION_LOGS_COLLECTION
    pass
```

### operation_endpoint (Decorator)

```python
@operation_endpoint(
    operation_type: OperationType,
    description_formatter: Optional[Callable] = None
)
async def endpoint(request, api_key: str):
    # operation context created automatically
    # operation logged on endpoint exit
    pass
```

### log_service (Service Logging)

```python
service = log_service(
    service_type: ServiceType,
    estimated_cost_usd: float = 0.0,
    breakdown: Optional[Dict[str, Any]] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[Service]
```

Returns the Service object, or None if no active operation.

### Helper Functions

```python
# Get current operation from context
current_op = get_current_operation() -> Optional[Operation]

# Get all operations in stack (nested contexts)
stack = get_operation_stack() -> List[Operation]

# Mark operation as completed
mark_operation_complete(actual_cost_usd: Optional[float] = None)

# Mark operation as failed
mark_operation_failed(error_description: str)

# Get summary of current operation
summary = get_operation_summary() -> Optional[Dict[str, Any]]
```

## Database Collections

### OPERATION_LOGS_COLLECTION

Stores completed/failed operations. Each entry includes:
- `_id`: Unique operation ID
- `user_id_or_api_key`: Who initiated operation
- `operation_type`: Type of operation
- `services_used`: List of service IDs used
- `estimated_cost_usd`: Total estimated cost
- `actual_cost_usd`: Actual cost (set when completed)
- `status`: PENDING, PROCESSING, COMPLETED, FAILED
- `created_at`: When operation started
- `completed_at`: When operation finished
- `description`: Operation description
- `metadata`: Additional metadata

### SERVICE_LOGS_COLLECTION

Stores service usage details. Each entry includes:
- `_id`: Unique service log ID
- `user_id_or_api_key`: Who initiated service
- `service_type`: Type of service (FILE_PROCESSOR, MONGODB, etc.)
- `breakdown`: Cost breakdown details
- `estimated_cost_usd`: Service cost
- `status`: Usually COMPLETED
- `created_at`: When service was used
- `description`: Service description
- `metadata`: Additional metadata

## Example: Full Endpoint Implementation

```python
from fastapi import FastAPI, Depends
from src.infrastructure.operation_logging import operation_endpoint, log_service
from src.core.management.core_models import OperationType, ServiceType
from src.core.management import Manager

app = FastAPI()
task_manager = Manager()

def format_create_kb_description(request, **kwargs):
    """Description formatter for endpoint"""
    return f"Create KB '{request.title}'"

@app.post("/knowledge-base/create", tags=["Knowledge Base"])
@operation_endpoint(
    OperationType.CREATE_KNOWLEDGE_BASE,
    description_formatter=format_create_kb_description
)
async def create_knowledge_base(request, api_key: str = Depends(verify_api_key)):
    """
    Create a new knowledge base.

    Automatic behavior:
    1. OperationContext created with api_key
    2. Description set from formatter
    3. api_key extracted for audit trail
    4. Operation ID assigned
    5. All service logs automatically linked
    """
    try:
        # Call manager function
        result = task_manager.create_knowledge_base(
            user_id_or_api_key=api_key,
            title=request.title,
            metadata=request.metadata
        )

        # Log the database operation
        log_service(
            ServiceType.MONGODB,
            estimated_cost_usd=0.02,
            description="Inserted KB entry"
        )

        return {
            "success": True,
            "kb_id": result["_id"],
            "title": result["title"]
        }

    except Exception as e:
        # Operation automatically marked as FAILED by @operation_endpoint
        logger.error(f"KB creation failed: {str(e)}")
        raise
```

## Comparison: Python Logging vs. Operation Logging

| Aspect | Python Logging | Operation Logging |
|--------|---|---|
| Scope Detection | `__name__` + stack inspection | `contextvars` + context manager |
| Thread-Safety | Thread-local storage | Context variables (async-safe) |
| Logger Instance | `getLogger(name)` | `get_current_operation()` |
| Logger Stack | Module hierarchy | Operation stack (nested contexts) |
| Log Level Filtering | Per-logger configuration | Per-operation status |
| Performance | Built-in Python, minimal overhead | Lightweight context variables |
| Async Support | Works with async (2.8+) | Full async support |

## Best Practices

1. **Always use @operation_endpoint for API endpoints** - Ensures operation tracking
2. **Log services inside operations** - Services should only be logged within operation context
3. **Use description_formatter for context** - Helps identify what operation is doing
4. **Set actual_cost_usd when available** - For accurate cost tracking
5. **Use breakdown for cost details** - Helps audit and understand costs
6. **Catch exceptions inside operations** - Context manager handles marking as failed
7. **Use nested contexts for sub-operations** - Maintain operation hierarchy

## Thread Safety & Async

```python
import asyncio

# Safe with concurrent operations
async def concurrent_operations():
    """Each async task has its own operation context"""

    async def create_kb1():
        with OperationContext(api_key1, OperationType.CREATE_KNOWLEDGE_BASE):
            log_service(ServiceType.MONGODB, cost=0.1)

    async def create_kb2():
        with OperationContext(api_key2, OperationType.CREATE_KNOWLEDGE_BASE):
            log_service(ServiceType.MONGODB, cost=0.1)

    # No context pollution - each task has own operation context
    await asyncio.gather(create_kb1(), create_kb2())
```
