# Document Processing Pipeline: From Upload to Chunks

When you upload a document, it goes through several stages to become searchable chunks in ChromaDB. Here's the complete flow:

## Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│ USER UPLOAD API                                                     │
│ POST /knowledge-bases/{kb_id}/documents                             │
│ (Upload PDF, DOCX, TXT, etc.)                                       │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 1: STORE DOCUMENT METADATA                                     │
│ ✓ Save file to /app/data/uploads/ (or S3)                          │
│ ✓ Create Document record in MongoDB                                 │
│ ✓ Add to Knowledge Base (KB)                                        │
│ ✓ Status: uploaded, NOT YET chunked                                │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
                         ╔═════════════════════╗
                         ║ BACKGROUND TASK     ║
                         ║ build_index()       ║
                         ║ (runs asynchronously)
                         ╚═════════════════════╝
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 2: LOAD DOCUMENT CONTENT                                       │
│ Location: _data_indexer.py:_chunk_documents_by_source()            │
│                                                                      │
│ ✓ Check if file exists in /app/data/uploads/                       │
│ ✓ If NOT found → Try S3 bucket                                     │
│ ✓ If STILL not found → SKIP document (0 chunks for this doc!)      │
│                                                                      │
│ Logs:                                                               │
│ ✓ "Loaded document content from disk"                              │
│ ✗ "Local file not found at {path}"                                 │
│ ✗ "Could not retrieve content for doc {doc_id}"                    │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
                        ⚠️  CRITICAL DECISION POINT  ⚠️
                              FILE FOUND?
                        ↙              ↘
                   YES              NO
                    ↓                ↓
                    ↓          ❌ SKIP & CONTINUE
                    ↓          Next document...
                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 3: SEND TO FILE PROCESSOR SERVICE                              │
│ Service: http://localhost:8003 (external service)                  │
│ Method: FileProcessorClient.batch_chunk_bytes()                    │
│                                                                      │
│ INPUT:                                                              │
│ {                                                                   │
│   "content": <file bytes>,                                         │
│   "filename": "document.pdf",                                      │
│   "source": "upload",                                              │
│   "mime_type": "application/pdf"                                   │
│ }                                                                   │
│                                                                      │
│ What it does:                                                       │
│ 1. Detects file type (PDF, DOCX, TXT, etc.)                       │
│ 2. Parses document content                                         │
│ 3. Splits content into semantic chunks (paragraphs, sections)     │
│ 4. Returns list of chunks with metadata                            │
│                                                                      │
│ Returns:                                                            │
│ {                                                                   │
│   "task_id": "task_123",                                          │
│   "files": [                                                        │
│     {                                                               │
│       "filename": "document.pdf",                                  │
│       "file_hash": "abc123",                                       │
│       "status": "COMPLETED",                                       │
│       "estimated_cost_usd": 0.0012                                 │
│     }                                                               │
│   ],                                                                │
│   "results": {                                                      │
│     "abc123": [  ← chunks for this file                            │
│       {                                                             │
│         "content": {                                               │
│           "text": "Chapter 1: Introduction...",                    │
│           "chunk_order_index": 0                                   │
│         },                                                          │
│         "metadata": { ... }                                        │
│       },                                                            │
│       {                                                             │
│         "content": {                                               │
│           "text": "Chapter 2: Background...",                      │
│           "chunk_order_index": 1                                   │
│         },                                                          │
│         "metadata": { ... }                                        │
│       }                                                             │
│       ...more chunks...                                            │
│     ]                                                               │
│   }                                                                 │
│ }                                                                   │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
                        ⚠️  ANOTHER CRITICAL POINT ⚠️
                    DID FILE PROCESSOR RETURN CHUNKS?
                        ↙              ↘
                   YES              NO (empty results)
                    ↓                ↓
                    ↓          ❌ 0 CHUNKS FOR THIS DOC
                    ↓          (Document stored but NOT indexed)
                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 4: CREATE CHUNK MODELS                                         │
│ Location: _data_indexer.py:_chunk_documents_by_source() line 441   │
│                                                                      │
│ For each chunk from FileProcessor:                                  │
│ ✓ Create Chunk model object                                        │
│ ✓ Add metadata (kb_id, doc_id, etc.)                               │
│ ✓ Generate unique chunk_id from doc content                        │
│                                                                      │
│ Result: List[Chunk] with 5, 10, 20... chunks                       │
│ (Depends on document size and complexity)                           │
│                                                                      │
│ Log: "Processing {N} chunks for doc {doc_id}"                      │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 5: STORE CHUNKS IN CHROMADB                                    │
│ Location: ChromaDBStore.add_chunks()                                │
│ Service: Local ChromaDB (vectore database)                         │
│                                                                      │
│ For each chunk:                                                     │
│ ✓ Generate text embeddings (vector representation)                 │
│ ✓ Store in ChromaDB collection: chunks_{user_id}                   │
│                                                                      │
│ What happens inside:                                                │
│ 1. Check if chunk_id already exists (duplicate detection)          │
│ 2. Serialize chunk content to JSON                                 │
│ 3. Sanitize metadata (ChromaDB compatibility)                      │
│ 4. Call ChromaDB API: collection.add()                             │
│ 5. Chunks are now SEARCHABLE!                                      │
│                                                                      │
│ Logs:                                                               │
│ ✓ "Stored {N} chunks in ChromaDB for doc {doc_id}"                 │
│ ✗ "Failed to store chunks in ChromaDB"                             │
│ ✗ "Non-empty lists required" (now prevented!)                      │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 6: UPDATE DOCUMENT STATUS                                      │
│ Location: _data_indexer.py:_chunk_documents_by_source() line 463   │
│                                                                      │
│ MongoDB Update:                                                     │
│ {                                                                   │
│   "chunked": true,                                                 │
│   "estimated_cost_usd": 0.0012                                     │
│ }                                                                   │
│                                                                      │
│ Log: "Marked document as chunked"                                  │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ STEP 7: RETRIEVE ALL CHUNKS FOR KB                                  │
│ Location: _data_indexer.py:get_all_chunks_for_kb()                │
│                                                                      │
│ ✓ Get all doc_ids in KB                                            │
│ ✓ Query ChromaDB for chunks from each doc                          │
│ ✓ Return aggregated chunk list                                     │
│                                                                      │
│ Result: List[Chunk] from ChromaDB                                  │
│ Log: "Total chunks retrieved: {N}"                                 │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
                    ⚠️  FINAL CHECK: DID WE GET CHUNKS?
                        ↙              ↘
                   > 0              0 (empty)
                    ↓                ↓
                    ✓ SUCCESS        ✗ FAILED
                    ↓                ↓
            Index COMPLETED    Index FAILED
            Status: COMPLETED  Status: FAILED
                              Error: "No chunks available"
```

## Why You Get 0 Chunks

At any of these decision points, the pipeline can fail:

| Step | What Can Go Wrong | Error Message | What to Check |
|------|------------------|---------------|---------------|
| 2 | File not in disk or S3 | "Local file not found" | File exists? Uploaded? |
| 3 | FileProcessor service down/error | "Failed to chunk document" | Service running on port 8003? |
| 3 | FileProcessor returns empty | "Processing 0 chunks" | File has content? Not corrupted? |
| 5 | ChromaDB error | "Failed to store chunks" | ChromaDB running? Disk space? |
| 7 | No docs in KB | (no error, just 0 chunks) | Documents uploaded to KB? |

## Complete Code Flow

### 1. Upload Endpoint
```python
# api/_routes/_knowledge_base.py
POST /knowledge-bases/{kb_id}/documents
→ upload_docs_to_knowledge_base()
→ Save file & create Document record
→ Trigger background index build
```

### 2. Background Index Task
```python
# src/core/_data_indexer.py
build_index(kb_id)
  ├─ Get documents from KB
  ├─ For each document:
  │   ├─ Load content from disk/S3
  │   ├─ Send to FileProcessor (http://localhost:8003)
  │   ├─ Receive chunks
  │   ├─ Store in ChromaDB
  │   └─ Update document status
  ├─ Get all chunks from ChromaDB
  └─ Finalize KB status (COMPLETED or FAILED)
```

### 3. Key Methods

**_chunk_documents_by_source()**
```python
def _chunk_documents_by_source(source, source_docs, content_map, kb_id):
    # Line 370: Load file from disk
    content = open(full_path, "rb").read()

    # Line 416: Send to FileProcessor
    chunk_result = file_processor.batch_chunk_bytes(
        file_contents, wait=True, poll_interval=5.0
    )

    # Line 436: Check if processing completed
    if file_status == TaskStatus.COMPLETED.value:
        chunks = results.get(file_hash, [])  # Get chunks

        # Line 483: Store in ChromaDB
        added_ids = self.chroma_store.add_chunks(collection_name, chunk_models)
```

**get_all_chunks_for_kb()**
```python
def get_all_chunks_for_kb(kb_id):
    # Get KB and its doc_ids
    kb = db[KNOWLEDGE_BASES_COLLECTION].find_one({"_id": kb_id})
    doc_ids = kb.get("doc_ids", [])

    if not doc_ids:
        return []  # No docs = 0 chunks

    # Query ChromaDB for chunks
    for doc_id in doc_ids:
        doc_chunks = chroma_store.get_document_chunks_in_order(
            collection_name=f"chunks_{user_id}",
            doc_id=doc_id
        )
        all_chunks.extend(doc_chunks)

    return all_chunks  # Returns whatever is in ChromaDB
```

## Quick Debug Checklist

```python
# 1. Is document in KB?
kb = db[KNOWLEDGE_BASES_COLLECTION].find_one({"_id": kb_id})
print(f"Docs in KB: {len(kb.get('doc_ids', []))}")  # Should be > 0

# 2. Is file stored?
import os
doc = db[DOCUMENTS_COLLECTION].find_one({"_id": doc_id})
print(f"File exists: {os.path.exists(f'/app/data/uploads/{doc.content_id}')}")

# 3. Is FileProcessor running?
import requests
requests.get("http://localhost:8003/health")  # Should return 200

# 4. Are chunks in ChromaDB?
from src.infrastructure.storage import get_chromadb_store
store = get_chromadb_store()
chunks = store.get_document_chunks_in_order(f"chunks_{user_id}", doc_id)
print(f"Chunks in ChromaDB: {len(chunks)}")

# 5. Check logs
# tail -f logs/src/core/_data_indexer.log
```

## Key Points

✅ **Document upload** = File saved to disk/S3 + Metadata in MongoDB
✅ **Chunking** = FileProcessor (external service) splits document
✅ **Storage** = Chunks indexed in ChromaDB for search

❌ **Zero chunks** = Failed at one of the decision points above

🔍 **To debug**: Work backwards from step 7 (ChromaDB) to step 2 (file load)
