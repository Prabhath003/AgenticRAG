# Troubleshooting: Why Zero Chunks Are Generated

When you see this error:
```
INFO | Total chunks retrieved: 0
WARNING | KB {kb_id} index build failed. No chunks available.
```

This guide helps identify where the chunking process failed.

## Chunking Flow Diagram

```
1. build_index(kb_id)
   ↓
2. Get documents from KB
   ↓
3. For each document source:
   ├─ Load content from disk or S3
   ├─ Send to FileProcessorClient.batch_chunk_bytes()
   ├─ Receive chunks from file processor
   └─ Store chunks in ChromaDB
   ↓
4. Retrieve all chunks from ChromaDB
   ↓
5. If chunks > 0: Index complete
   If chunks == 0: Index failed ❌
```

## Common Causes of 0 Chunks

### 1. **Content File Not Found** (Most Common)

**What happens**:
```python
# Line 364-366 or 390-392
if not content_path:
    logger.error(f"Content path not found for doc {doc.doc_id}")
    continue  # Skips this document!
```

**Log signs**:
```
ERROR | Content path not found for doc {doc_id}
ERROR | Could not retrieve content for doc {doc_id}
```

**How to fix**:
- Verify files uploaded to `/app/data/uploads/` or S3
- Check `Document.content_id` points to actual file
- Verify file permissions readable by the process

**Debug**:
```bash
# Check if file exists
ls -la /app/data/uploads/{content_path}

# Check S3
aws s3 ls s3://bucket/documents/{content_path}
```

---

### 2. **FileProcessor Service Failed**

**What happens**:
```python
# Line 416-418
chunk_result = file_processor.batch_chunk_bytes(
    file_contents, wait=True, poll_interval=5.0
)
```

The file processor (PDF/document parser) returns empty or error results.

**Log signs**:
```
ERROR | Failed to chunk document: {error}
WARNING | FileProcessor returned no results
INFO | Processing 0 chunks for doc {doc_id}
```

**Possible reasons**:
- File is corrupted or invalid format
- File processor service is down/timeout
- File is too large or too small
- Unsupported file format

**How to fix**:
- Check FileProcessor logs: `logs/file_processor.log`
- Test file with FileProcessor directly
- Verify file format is PDF/DOCX/TXT/etc.
- Check file size is reasonable (1KB - 500MB)

**Debug**:
```python
from src.infrastructure.clients import FileProcessorClient

client = FileProcessorClient()
result = client.batch_chunk_bytes([{
    "content": open("document.pdf", "rb").read(),
    "filename": "document.pdf",
    "source": "test",
    "mime_type": "application/pdf"
}], wait=True)

if not result.get("results"):
    print(f"FileProcessor failed: {result}")
```

---

### 3. **FileProcessor Status Not COMPLETED**

**What happens**:
```python
# Line 436
if file_status == TaskStatus.COMPLETED.value:
    # Only processes if COMPLETED
    # Otherwise, skips the file!
```

**Log signs**:
```
WARNING | File {filename} status is {status}, not COMPLETED
```

**Possible statuses**:
- `PENDING` - Still processing
- `FAILED` - Processing failed
- `TIMEOUT` - Took too long

**How to fix**:
- Increase `poll_interval` in batch_chunk_bytes()
- Check FileProcessor logs for errors
- Try with smaller file
- Restart FileProcessor service

---

### 4. **FileProcessor Returns Empty Results**

**What happens**:
```python
# Line 437-438
if file_hash in results:
    chunks = results.get(file_hash, [])
    if not chunks:  # Empty list!
        logger.info(f"Processing 0 chunks for doc {doc_id}")
        continue  # Skip!
```

**Log signs**:
```
INFO | Processing 0 chunks for doc {doc_id}
```

**Reasons**:
- File is actually empty (0 bytes)
- File contains only whitespace/formatting
- Chunking algorithm filtered out all content
- File processor service error

**How to fix**:
- Verify file has actual text content
- Check minimum chunk size settings
- Increase document size or content density

---

### 5. **ChromaDB Storage Failed (Silent Failure)**

**What happens**:
```python
# Line 483-489
try:
    added_ids = self.chroma_store.add_chunks(collection_name, chunk_models)
except Exception as chroma_error:
    logger.error(f"Failed to store chunks in ChromaDB...")
    raise  # Error is re-raised
```

**Log signs**:
```
ERROR | Failed to store chunks in ChromaDB: {error}
ERROR | Non-empty lists are required for ['ids', 'metadatas', 'documents'] in add.
```

**Note**: This is now fixed with empty input check!

---

### 6. **No Documents in Knowledge Base**

**What happens**:
```python
# Line 540-541 in get_all_chunks_for_kb()
doc_ids = kb_entry.get("doc_ids", [])
if not doc_ids:
    return []  # No documents to chunk!
```

**Log signs**:
```
WARNING | KB {kb_id} has no documents
INFO | Total chunks retrieved: 0
```

**How to fix**:
- Verify documents were uploaded to KB
- Check KB.doc_ids in database
- Use API to list documents in KB

**Debug**:
```python
from src.infrastructure.database import get_db_session
from src.config import Config

with get_db_session() as db:
    kb = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one({"_id": "kb_id"})
    print(f"Documents in KB: {kb.get('doc_ids', [])}")
```

---

### 7. **Collection Name Mismatch**

**What happens**:
```python
# Line 546 & 478
collection_name = f"chunks_{user_id}"
```

If `user_id` changes or is incorrect, chunks are stored in wrong collection.

**How to fix**:
- Verify `get_operation_user_id()` is consistent
- Check operation logging context
- Verify collection exists in ChromaDB

**Debug**:
```python
from src.infrastructure.storage import get_chromadb_store

store = get_chromadb_store()
collections = store.list_collections()
print(f"Available collections: {collections}")
```

---

## Debugging Checklist

When zero chunks are returned, check in this order:

- [ ] **Documents exist in KB**
  ```python
  # Check KB has documents
  kb = db[KNOWLEDGE_BASES_COLLECTION].find_one({"_id": kb_id})
  has_docs = len(kb.get("doc_ids", [])) > 0
  ```

- [ ] **Content files exist**
  ```bash
  # Check upload directory
  ls -la /app/data/uploads/

  # Or check S3
  aws s3 ls s3://bucket/documents/ --recursive
  ```

- [ ] **FileProcessor working**
  ```python
  # Test FileProcessor directly
  from src.infrastructure.clients import FileProcessorClient
  client = FileProcessorClient()
  # Send test file
  ```

- [ ] **ChromaDB collection exists**
  ```python
  store.list_collections()  # Should include chunks_{user_id}
  ```

- [ ] **Chunks stored in ChromaDB**
  ```python
  # Query ChromaDB directly
  chunk = store.get_chunk_by_id(collection_name, chunk_id)
  ```

- [ ] **Check operation context**
  ```python
  from src.infrastructure.operation_logging import get_operation_user_id
  user_id = get_operation_user_id()
  print(f"Operation user: {user_id}")
  ```

---

## Enhanced Logging

To improve debugging, add these log captures at key points:

### In `build_index()`:
```python
# After loading documents
logger.info(f"Documents loaded: {len(documents)} docs")

# After chunking
logger.info(f"Chunks generated: {len(all_chunks)} chunks")
```

### In `_chunk_documents_by_source()`:
```python
# After loading file_contents
logger.info(f"Content loaded for: {len(file_contents)} files")

# After FileProcessor
logger.info(f"FileProcessor results: {len(results)} files processed")

# Before storing in ChromaDB
logger.info(f"Storing {len(chunk_models)} chunks for doc {doc_id}")
```

---

## Production Recommendations

1. **Add pre-checks before indexing**:
   ```python
   # Verify documents exist
   # Verify files exist before processing
   # Validate file formats
   ```

2. **Improve error messages**:
   - Show which document failed
   - Show why (file not found, processor error, etc.)
   - Include remediation steps

3. **Add metrics**:
   - Track success rate per document source
   - Monitor average chunk count per document
   - Alert on 0 chunks

4. **Retry logic**:
   - Retry failed files
   - Exponential backoff for FileProcessor
   - Alternative storage locations (S3 fallback)

---

## Related Files

- **Chunking logic**: `src/core/_data_indexer.py`
  - `_chunk_documents_by_source()`: Lines 349-522
  - `get_all_chunks_for_kb()`: Lines 524-559
  - `build_index()`: Lines 112-254

- **FileProcessor client**: `src/infrastructure/clients/_file_processor_client.py`

- **ChromaDB store**: `src/infrastructure/storage/_chromadb_store.py`

- **Logs**: `logs/src/core/_data_indexer.log`
