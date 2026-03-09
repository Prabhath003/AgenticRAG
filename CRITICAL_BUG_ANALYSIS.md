# Critical Error Handling Bug Analysis

## Issue Summary
Background tasks for document and chunk indexing have **unprotected status updates** that can fail silently without triggering rollback mechanisms.

## Root Cause

### Problem 1: Status Updates Outside Try-Except in Background Tasks

**File**: `src/core/_management/_sub_managers/_knowledge_base_manager.py`

**`_index_documents_background()` at line 2052**:
```python
def _index_documents_background(self, kb_id: str, doc_ids: List[str]) -> None:
    update_operation_status(TaskStatus.PROCESSING)  # ← UNPROTECTED!
    try:
        # ... indexing work ...
        update_operation_status(TaskStatus.COMPLETED)  # ← UNPROTECTED!
    except Exception as e:
        # ... error handling ...
```

**`_index_chunks_background()` at line 2101**:
```python
def _index_chunks_background(self, kb_id: str, chunks: List[Chunk]) -> None:
    update_operation_status(TaskStatus.PROCESSING)  # ← UNPROTECTED!
    try:
        # ... indexing work ...
        update_operation_status(TaskStatus.COMPLETED)  # ← UNPROTECTED!
    except Exception as e:
        # ... error handling ...
```

**What happens if status update fails?**
- If `update_operation_status(TaskStatus.PROCESSING)` throws an exception BEFORE the try block, it's UNHANDLED
- The exception propagates without catching, no rollback is initiated
- Operation remains in PENDING state in database while background task may be partially processing

### Problem 2: Silent Failures in Operation Logging

**File**: `src/infrastructure/operation_logging/operation_context.py`

**`_persist_operation_to_db()` at line 316**:
```python
def _persist_operation_to_db(operation: Operation) -> bool:
    try:
        # ... persist to DB ...
        return True
    except Exception as e:
        logger.error(f"Failed to persist operation to DB: {str(e)}")
        return False  # ← Returns False but caller doesn't check!
```

**`update_operation_status()` at line 461**:
```python
def update_operation_status(status: TaskStatus) -> Optional[str]:
    operation = get_current_operation()
    operation.status = status
    _persist_operation_to_db(operation)  # ← Return value IGNORED!
    return operation.get_id()
```

**What happens if persistence fails?**
- Status is updated in-memory
- `_persist_operation_to_db()` returns False but return value is ignored
- Caller doesn't know persistence failed
- Database shows PENDING but application thinks it's PROCESSING
- **Silent desynchronization between memory and DB state**

### Problem 3: Similar Issue in `update_operation_metadata()`

**Line 621**: Also calls `_persist_operation_to_db()` without checking return value

## Impact Scenarios

### Scenario 1: Database Connection Failure During Background Task
1. User uploads documents → GET immediate response with task_id
2. Background task starts, tries to update status to PROCESSING
3. Database is temporarily unavailable, `_persist_operation_to_db()` fails
4. In-memory status updated to PROCESSING, but DB still shows PENDING
5. User checks operation status → sees PENDING forever
6. Indexing may or may not be happening in background (undefined state)

### Scenario 2: Multiple Status Updates With Mixed Success
1. Status 1: PROCESSING → Successfully persisted
2. Status 2: (error during update_operation_metadata) → Persistence fails silently
3. Status 3: FAILED → Successfully persisted
4. User sees: PROCESSING → FAILED (missing the metadata error info)

### Scenario 3: No Rollback on Unhandled Status Update Exception
1. Background task status update throws exception before try block
2. Exception unhandled → thread dies
3. No rollback initiated → documents/chunks left in partial state in database
4. KB is partially indexed with no indication of failure

## Required Fixes

### Fix 1: Wrap Status Updates in Background Tasks
Both `_index_documents_background()` and `_index_chunks_background()` must:
```python
def _index_documents_background(self, kb_id: str, doc_ids: List[str]) -> None:
    """
    Background task to index documents for a knowledge base.
    Called via executor.submit() for non-blocking operation.
    """
    try:
        update_operation_status(TaskStatus.PROCESSING)  # NOW PROTECTED
    except Exception as e:
        logger.error(f"Failed to set PROCESSING status: {str(e)}")
        try:
            update_operation_status(TaskStatus.FAILED)  # Try to mark failed
        except:
            pass
        return  # Exit without doing work

    try:
        # ... indexing work ...
        try:
            update_operation_status(TaskStatus.COMPLETED)  # NOW PROTECTED
        except Exception as status_error:
            logger.error(f"Failed to set COMPLETED status: {str(status_error)}")
            try:
                update_operation_status(TaskStatus.FAILED)
            except:
                pass
    except Exception as e:
        # ... existing error handling ...
```

### Fix 2: Make Status Updates Fail Loudly
Change `update_operation_status()` to check persistence result:
```python
def update_operation_status(status: TaskStatus) -> Optional[str]:
    operation = get_current_operation()
    if not operation:
        logger.warning("update_operation_status called without active operation")
        return None

    operation.status = status
    logger.debug(f"Operation status updated: {operation.get_id()} -> {status.value}")

    # Persist status change to database
    success = _persist_operation_to_db(operation)
    if not success:
        raise RuntimeError(
            f"Failed to persist operation status to database. "
            f"Operation: {operation.get_id()}, Status: {status.value}"
        )

    return operation.get_id()
```

Similarly for `update_operation_metadata()`.

### Fix 3: Enhanced Error Handling in Background Tasks
Ensure ALL error paths update operation status to FAILED:
```python
except Exception as e:
    logger.error(f"Error in background task: {str(e)}", exc_info=True)

    # Try to update operation status to FAILED
    try:
        update_operation_status(TaskStatus.FAILED)
    except Exception as status_error:
        logger.critical(
            f"CRITICAL: Cannot update operation status to FAILED. "
            f"Original error: {str(e)}, Status update error: {str(status_error)}"
        )
        # Last resort: try to update metadata with error
        try:
            update_operation_metadata({"$set": {"error": str(e), "unrecoverable_error": True}})
        except:
            logger.critical("CRITICAL: Cannot update operation metadata either. Manual intervention required.")

    # Do other error handling (rollback, etc.)
    ...
```

## Files to Fix

1. `src/core/_management/_sub_managers/_knowledge_base_manager.py`
   - Line 2037-2084: `_index_documents_background()`
   - Line 2086-2153: `_index_chunks_background()`

2. `src/infrastructure/operation_logging/operation_context.py`
   - Line 461-492: `update_operation_status()` - Make it check persistence result
   - Line 520-623: `update_operation_metadata()` - Make it check persistence result

## Testing Strategy

1. **Database Connection Failure Test**: Kill MongoDB during background task, verify operation marked FAILED
2. **Status Update Exception Test**: Mock `_persist_operation_to_db()` to raise exception, verify rollback triggered
3. **Partial Success Test**: Update status successfully, then metadata fails, verify both tracked
4. **Timeout Test**: Long-running indexing with intermediate status updates, verify all statuses reach database
