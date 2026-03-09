# Upload Docs/Chunks Error Handling Analysis

## Critical Issues Found

### Issue 1: Document Processing Failures Without Rollback (upload_docs)

**File**: `src/core/_management/_sub_managers/_knowledge_base_manager.py`
**Function**: `upload_docs_to_knowledge_base()` (lines 704-959)

**Problem**: Steps 1-5 are COMPLETELY OUTSIDE the try-except block:

```python
def upload_docs_to_knowledge_base(...):
    # Lines 751-768: UNPROTECTED OPERATIONS
    new_content_to_write, content_entries, doc_entries, content_ids, doc_ids = (
        self._process_documents_for_upload(docs, created_at)  # ← CAN FAIL
    )

    _, _, current_kb_doc_ids = self._fetch_existing_documents(...)  # ← CAN FAIL

    self._write_files_in_parallel(new_content_to_write)  # ← CAN FAIL - FILES WRITTEN!

    self._insert_documents_to_db(content_entries, doc_entries)  # ← CAN FAIL - FILES WRITTEN!

    # Line 774: try-except STARTS HERE
    try:
        if new_doc_ids_for_kb:
            # KB update and task submission
```

**What happens if each fails?**

1. **`_process_documents_for_upload()` fails**:
   - No error returned
   - Function silently exits
   - No rollback needed (nothing written yet)
   - But caller gets no indication of failure

2. **`_fetch_existing_documents()` fails**:
   - `current_kb_doc_ids` not set
   - Processing continues with wrong data
   - Files written, documents inserted
   - No rollback triggered

3. **`_write_files_in_parallel()` fails**:
   - FILES ARE WRITTEN TO DISK
   - Documents not yet in DB (next step)
   - Exception propagates, no rollback for files
   - Documents in DB and content in DB but files exist

4. **`_insert_documents_to_db()` fails**:
   - FILES ALREADY WRITTEN TO DISK
   - Documents/content NOT in DB
   - Orphaned files left on disk
   - No rollback to delete written files

### Issue 2: Missing Rollback for File Writes

**Root Cause**:
- Files are written before database entries are created
- If database write fails, files are orphaned
- No rollback mechanism exists for file cleanup on database errors

**Impact**:
- Disk space leaks
- Files with no corresponding database entries
- No way to trace which files are orphaned

### Issue 3: Unprotected Early Returns in upload_chunks

**File**: `src/core/_management/_sub_managers/_knowledge_base_manager.py`
**Function**: `upload_chunks_to_knowledge_base()` (lines 961-1118)

**Problem**: Backfill document creation error handling:

```python
def upload_chunks_to_knowledge_base(...):
    # Lines 1015-1065: CREATE BACKFILL DOCUMENTS
    with self._db_lock:
        with get_db_session() as db:
            if chunk_doc_ids:
                # Check existing docs
                # Create missing document entries
                if missing_doc_ids:
                    documents_to_create: List[Dict[str, Any]] = []
                    for doc_id in missing_doc_ids:
                        # ... document creation ...
                        backfilled_doc_ids.append(doc_id)

                    # Batch insert missing documents
                    if documents_to_create:
                        db[Config.DOCUMENTS_COLLECTION].insert_many(documents_to_create)
                        # ↑ THIS CAN FAIL WITH NO EXCEPTION HANDLING!

    # Line 1068: try-except STARTS HERE (only covers metadata update & task submission)
    try:
        update_operation_metadata(...)
        executor.submit(...)
        return UploadChunksToKnowledgeBaseResponse(...)
    except Exception as e:
        # Rollback backfilled docs
        if backfilled_doc_ids:
            # Delete them
```

**The Critical Gap**:
- If `insert_many()` at line 1062 fails, exception is NOT caught
- Exception propagates up to API endpoint
- `backfilled_doc_ids` list is populated (in loop before the insert)
- But exception occurs BEFORE the try-except at line 1068
- Caller gets error but `backfilled_doc_ids` was never passed to the rollback handler

**Result**: Partial backfilled documents left in database (ORPHANED)

### Issue 4: Missing Error Propagation in Data Indexer

**File**: `src/core/_data_indexer.py`
**Function**: `_chunk_documents_by_source()` (lines 312-480)

**Problem**: Multiple steps that can fail:

```python
def _chunk_documents_by_source(...):
    added_chunk_ids: List[str] = []
    try:
        # Step 1: Chunk processing (can fail)
        # Step 2: Add to ChromaDB (CAN FAIL - chunks already created)
        added_chunk_ids = self.chroma_store.add_chunks(collection_name, chunks)

        # Step 3: Database updates (can fail)
        # Step 4: KB finalization (can fail)
    except Exception as e:
        # Rollback
        if added_chunk_ids:
            self.chroma_store.delete_chunks(collection_name, added_chunk_ids)
        # Update KB status to FAILED
        mark_operation_failed(str(e))
```

**Missing**: If `add_chunks()` succeeds partially (some chunks added, some failed), but operation continues:
- `added_chunk_ids` may not reflect actual added chunks
- Metadata flattening errors could add chunks without proper metadata
- Rollback tries to delete chunks that may not all be in ChromaDB

## Fix Strategy

### Fix 1: Wrap All Document Processing in Try-Except

Move the try-except to include ALL steps (lines 751-768):

```python
def upload_docs_to_knowledge_base(...):
    submission_time = datetime.now(timezone.utc)

    with self._upload_lock:
        # ... validation ...

        created_at = datetime.now(timezone.utc)

        doc_ids: List[str] = []
        content_ids: List[str] = []
        new_content_to_write: Dict[str, str] = {}

        try:
            # Step 1: Process documents
            new_content_to_write, content_entries, doc_entries, content_ids, doc_ids = (
                self._process_documents_for_upload(docs, created_at)
            )

            if not doc_ids:
                return UploadDocumentsToKnowledgeBaseResponse(...)

            # Step 2: Get existing docs
            _, _, current_kb_doc_ids = self._fetch_existing_documents(kb_id, content_ids, doc_ids)

            # Step 3: Write files
            self._write_files_in_parallel(new_content_to_write)

            # Step 4: Insert to DB
            self._insert_documents_to_db(content_entries, doc_entries)

            # Step 5: Update KB and submit task
            # ... rest of processing ...

        except Exception as e:
            logger.error(f"Error during document processing: {str(e)}", exc_info=True)

            # COMPREHENSIVE ROLLBACK:
            # 1. Delete from DB
            if doc_ids or content_ids:
                try:
                    with self._db_lock:
                        with get_db_session() as db:
                            if doc_ids:
                                db[Config.DOCUMENTS_COLLECTION].delete_many({...})
                            if content_ids:
                                db[Config.DOCUMENT_CONTENTS_COLLECTION].delete_many({...})
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback DB entries: {str(rollback_error)}")

            # 2. Delete written files
            for file_path in new_content_to_write.keys():
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                except Exception as file_error:
                    logger.error(f"Failed to delete file {file_path}: {str(file_error)}")

            raise
```

### Fix 2: Protect Backfill Document Creation

Wrap backfill operations in try-except:

```python
def upload_chunks_to_knowledge_base(...):
    # ... validation ...

    backfilled_doc_ids: List[str] = []

    try:
        # Create backfill documents
        with self._db_lock:
            with get_db_session() as db:
                # ... logic ...
                if documents_to_create:
                    db[Config.DOCUMENTS_COLLECTION].insert_many(documents_to_create)
                    backfilled_doc_ids = list(missing_doc_ids)  # Track before insert succeeds

        # Then wrap metadata and submission in try-except
        try:
            update_operation_metadata(...)
            executor.submit(...)
            return UploadChunksToKnowledgeBaseResponse(...)
        except Exception as e:
            if backfilled_doc_ids:
                try:
                    with self._db_lock:
                        with get_db_session() as db:
                            db[Config.DOCUMENTS_COLLECTION].delete_many({...})
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback: {str(rollback_error)}")
            raise

    except Exception as e:
        logger.error(f"Error creating backfill documents: {str(e)}", exc_info=True)
        raise UploadChunksToKnowledgeBaseResponse(success=False, message=str(e))
```

## Summary of Unprotected Operations

| Operation | Location | Impact | Current Handling |
|-----------|----------|--------|------------------|
| Process documents | upload_docs:751 | Data loss detection | None |
| Fetch existing docs | upload_docs:762 | Wrong merge logic | None |
| Write files | upload_docs:765 | Orphaned files | None |
| Insert to DB | upload_docs:768 | Orphaned files | None |
| Backfill document insert | upload_chunks:1062 | Orphaned docs | None |
| Create chunks in ChromaDB | _data_indexer:275 | Partial chunks | Partial (no verification) |

