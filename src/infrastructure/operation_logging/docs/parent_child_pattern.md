# Parent-Child Operation Pattern

Clean implementation of parent-child operation linking with proper nested executor handling.

## Architecture

```
API Endpoint (Fast Operation)
    ├─ Operation: TRIGGER_KB_BUILD
    ├─ Log service: MONGODB (query KB)
    ├─ Create parent_operation_id
    └─ Submit background task with parent_id
        │
        └─ Background Thread (Slow Operation)
             ├─ Operation: KNOWLEDGE_BASE_BUILD
             ├─ metadata: {parent_operation_id: "uuid-123"}
             ├─ Submit nested chunk tasks with context
             │
             └─ Nested Thread (Chunk Processing)
                  ├─ Has operation context from parent
                  ├─ Log services: FILE_PROCESSOR, etc.
                  └─ Context preserved through nesting
```

## Implementation

### 1. Helper Method for Submitting with Context

```python
from contextvars import copy_context
from concurrent.futures import Executor

class KnowledgeBaseManager:

    @staticmethod
    def _submit_with_context(executor: Executor, func, *args, **kwargs):
        """
        Submit function to executor while preserving operation context.

        This ensures nested submissions inherit parent operation context.
        """
        context = copy_context()
        return executor.submit(context.run, func, *args, **kwargs)
```

### 2. API Endpoint (Quick Operation)

```python
from fastapi import FastAPI
from src.infrastructure.operation_logging import operation_endpoint, log_service, get_current_operation
from src.core.management.core_models import OperationType, ServiceType

app = FastAPI()

@app.post("/knowledge-base/{kb_id}/trigger-build")
@operation_endpoint(
    OperationType.TRIGGER_KB_BUILD,
    description_formatter=lambda kb_id, **kw: f"Trigger KB build: {kb_id}"
)
async def trigger_knowledge_base_building(
    kb_id: str,
    api_key: str = Depends(verify_api_key_header)
):
    """
    Endpoint operation (quick completion).

    Parent operation: TRIGGER_KB_BUILD
    Child operation: KNOWLEDGE_BASE_BUILD (in background)
    """

    # Get parent operation ID for linking
    parent_operation = get_current_operation()
    parent_op_id = parent_operation._id if parent_operation else None

    # Log endpoint operation
    log_service(
        ServiceType.MONGODB,
        estimated_cost_usd=0.01,
        description="Queried KB to check status"
    )

    # Submit background task with parent operation reference
    context = copy_context()
    executor.submit(
        context.run,
        task_manager.trigger_knowledge_base_building_background,
        kb_id,
        api_key,
        parent_op_id  # ← Pass parent operation ID
    )

    # Endpoint operation completes here (fast)
    return {
        "message": "Knowledge base building queued",
        "kb_id": kb_id,
        "status": "queued",
        "parent_operation_id": parent_op_id  # For tracking
    }
```

### 3. Manager Method (Background Operation)

```python
from src.infrastructure.operation_logging import OperationContext, log_service, get_current_operation
from src.core.management.core_models import ServiceType

class KnowledgeBaseManager:

    def trigger_knowledge_base_building_background(
        self,
        kb_id: str,
        api_key: str,
        parent_operation_id: str = None
    ):
        """
        Background work with parent-child operation linking.

        Creates new operation for background task and links to parent.
        All nested submissions preserve this operation context.
        """

        # Create new operation for background work
        with OperationContext(
            user_id_or_api_key=api_key,
            operation_type=OperationType.KNOWLEDGE_BASE_BUILD,
            description=f"Background: Build KG for KB {kb_id}",
            metadata={
                "parent_operation_id": parent_operation_id,
                "kb_id": kb_id,
                "type": "background_task"
            }
        ) as operation:
            try:
                logger.info(f"Starting background KB build: {kb_id} (parent: {parent_operation_id})")

                # Call main processing with operation context preserved
                self._process_docs(kb_id, api_key)

                logger.info(f"Completed background KB build: {kb_id}")

            except Exception as e:
                # Operation automatically marked as FAILED
                logger.error(f"Background KB build failed: {kb_id} - {str(e)}", exc_info=True)

    def _process_docs(self, kb_id: str, api_key: str):
        """
        Process documents with nested executor submissions.

        All nested submissions use _submit_with_context helper
        to preserve operation context.
        """
        try:
            logger.info(f"Processing docs for KB {kb_id}")

            # ... get KB entry, docs ...

            # Step 1: Chunk documents in parallel
            chunk_futures = []
            for source, source_docs in docs_by_source.items():
                logger.info(f"Queuing chunk processing for {len(source_docs)} docs from {source}")

                # ✓ Use helper to preserve operation context
                future = self._submit_with_context(
                    executor,
                    self._chunk_documents_by_source,
                    source,
                    source_docs,
                    content_map,
                    file_processor,
                    kb_id,
                    processing_started_at,
                    all_chunks,
                    api_key
                )
                chunk_futures.append(future)

            # Wait for all chunk futures
            total_chunking_cost = 0.0
            for future in as_completed(chunk_futures):
                try:
                    source_cost = future.result()
                    total_chunking_cost += source_cost
                except Exception as e:
                    logger.error(f"Error in chunk processing: {str(e)}")

            # Log total chunking cost
            if total_chunking_cost > 0:
                log_service(
                    ServiceType.FILE_PROCESSOR,
                    estimated_cost_usd=total_chunking_cost,
                    description="Chunked all documents",
                    breakdown={"total_cost": total_chunking_cost}
                )

            # Log classification services
            if classification_services:
                total_classification_cost = 0.0
                for service in classification_services:
                    total_classification_cost += service.estimated_cost_usd

                log_service(
                    ServiceType.KNOWLEDGE_BASE_BUILD,
                    estimated_cost_usd=total_classification_cost,
                )

            # Log KB creation cost
            if kb_ids:
                log_service(
                    ServiceType.KNOWLEDGE_BASE_BUILD,
                    estimated_cost_usd=0.20,
                    description="Created and populated knowledge bases",
                    breakdown={"kb_count": len(kb_ids)}
                )

            # Step 4: Update KB
            processing_completed_at = datetime.now(timezone.utc).isoformat()
            with self._db_lock:
                with get_db_session() as db:
                    update_data = {
                        "$addToSet": {"kb_ids": {"$each": kb_ids}},
                        "$set": {
                            "processing_completed_at": processing_completed_at,
                            "status": TaskStatus.COMPLETED.value,
                            "index_build_at": processing_completed_at
                        }
                    }
                    db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                        {"_id": kb_id},
                        update_data
                    )

                    # Update KBs to completed
                    if kb_ids:
                        db[Config.KNOWLEDGE_BASES_COLLECTION].update_many(
                            {"_id": {"$in": kb_ids}},
                            {
                                "$set": {
                                    "status": TaskStatus.COMPLETED.value,
                                    "completed_at": processing_completed_at
                                }
                            }
                        )

            logger.info(f"Successfully completed KB processing: {kb_id}")

        except Exception as e:
            logger.error(f"Critical error in _process_docs: {str(e)}", exc_info=True)
            raise

    def _chunk_documents_by_source(
        self,
        source: str,
        source_docs: List[Dict[str, Any]],
        content_map: Dict[str, str],
        file_processor,
        kb_id: str,
        processing_started_at: str,
        all_chunks: Dict[str, List[Dict[str, Any]]],
        api_key: Optional[str] = None
    ) -> float:
        """
        Chunk documents for a source.

        This runs in a separate thread but has operation context
        because parent called _submit_with_context().
        """
        from src.infrastructure.operation_logging import get_current_operation, log_service

        # ✓ Operation context is available here!
        current_op = get_current_operation()
        logger.info(f"Chunking {len(source_docs)} docs from {source} (operation: {current_op._id if current_op else 'none'})")

        total_cost = 0.0

        try:
            # ... read content files ...
            # ... call file_processor.batch_chunk_bytes() ...
            # ... process chunks ...

            # ✓ Log service in this nested thread
            if processed_count > 0:
                doc_cost = file_info.get("estimated_cost_usd", 0.0)
                total_cost += doc_cost

                log_service(
                    ServiceType.FILE_PROCESSOR,
                    estimated_cost_usd=doc_cost,
                    description=f"Chunked {processed_count} docs from {source}",
                    breakdown={
                        "source": source,
                        "chunk_count": len(chunk_entries),
                        "cost": doc_cost
                    }
                )

            return total_cost

        except Exception as e:
            logger.error(f"Error chunking source {source}: {str(e)}")
            raise
```

## Database Records

### Endpoint Operation (Fast completion)
```json
{
  "_id": "uuid-endpoint",
  "operation_type": "trigger_knowledge_base_building",
  "status": "completed",
  "services_used": ["uuid-mongodb-1"],
  "estimated_cost_usd": 0.01,
  "completed_at": "2025-12-03T10:00:00.500Z",
  "metadata": null
}
```

### Background Operation (Slow completion, linked to parent)
```json
{
  "_id": "uuid-background",
  "operation_type": "knowledge_base_build",
  "status": "completed",
  "services_used": [
    "uuid-file-processor-1",
    "uuid-file-processor-2",
    "uuid-kb-build-1"
  ],
  "estimated_cost_usd": 0.41,
  "completed_at": "2025-12-03T10:00:15.200Z",
  "metadata": {
    "parent_operation_id": "uuid-endpoint",  # ← Link to parent
    "kb_id": "kb-123",
    "type": "background_task"
  }
}
```

### Service Logs (All linked to background operation)
```json
[
  {
    "_id": "uuid-file-processor-1",
    "service_type": "file_processor",
    "estimated_cost_usd": 0.15,
    "description": "Chunked 100 docs from source-1"
  },
  {
    "_id": "uuid-file-processor-2",
    "service_type": "file_processor",
    "estimated_cost_usd": 0.15,
    "description": "Chunked 80 docs from source-2"
  },
  {
    "_id": "uuid-kb-build-1",
    "service_type": "knowledge_base_build",
    "estimated_cost_usd": 0.11,
    "description": "Created 3 knowledge bases"
  }
]
```

## Key Features of Parent-Child Pattern

✓ **Endpoint operation completes quickly**
- Endpoint operation logged fast
- User gets immediate response
- API doesn't wait for background work

✓ **Background operation tracked separately**
- Captures all background work
- Records actual duration
- Accumulates all service costs

✓ **Parent-child link in metadata**
- Query parent ops: `metadata.parent_operation_id: "uuid-endpoint"`
- Query children: `metadata.parent_operation_id: {"$exists": true}`
- Build operation hierarchy

✓ **Nested executor calls work seamlessly**
- All nested submissions use `_submit_with_context()` helper
- Operation context preserved through all threads
- No context pollution between concurrent operations

✓ **All services logged to background operation**
- Services from all nested threads linked correctly
- Total cost accumulated properly
- Complete audit trail maintained

## Querying Operations

```python
# Find parent operation
db[Config.OPERATION_LOGS_COLLECTION].find_one({
    "_id": "uuid-endpoint",
    "operation_type": "trigger_knowledge_base_building"
})

# Find all child operations for a parent
db[Config.OPERATION_LOGS_COLLECTION].find({
    "metadata.parent_operation_id": "uuid-endpoint"
})

# Find all background tasks
db[Config.OPERATION_LOGS_COLLECTION].find({
    "metadata.type": "background_task"
})

# Calculate total cost (parent + children)
operations = db[Config.OPERATION_LOGS_COLLECTION].aggregate([
    {"$match": {
        "$or": [
            {"_id": "uuid-endpoint"},
            {"metadata.parent_operation_id": "uuid-endpoint"}
        ]
    }},
    {"$group": {
        "_id": None,
        "total_cost": {"$sum": "$estimated_cost_usd"},
        "operation_count": {"$sum": 1},
        "duration_seconds": {
            "$subtract": [
                {"$max": "$completed_at"},
                {"$min": "$created_at"}
            ]
        }
    }}
])
```

## Trade-offs vs Other Solutions

| Aspect | Solution B (Separate Ops) | Parent-Child (This) | Solution A (No Decorator) |
|--------|---|---|---|
| **Endpoint operation tracked** | ✓ Yes | ✓ Yes | ✗ No |
| **Background operation tracked** | ✓ Yes | ✓ Yes | ✓ Yes |
| **Parent-child link** | ✗ No | ✓ Yes (metadata) | ✗ No |
| **Nesting complexity** | Medium | Low (helper method) | Low |
| **Query parent ops** | By type | By metadata.parent_op_id | N/A |
| **Cost calculation** | Sum both | Sum parent + children | Just background |

## When to Use Parent-Child

✓ When you want hierarchy tracking
✓ When you want endpoint operation recorded
✓ When parent and child are related business operations
✓ When you need to query by parent operation

This pattern is **BEST for most API use cases**.
