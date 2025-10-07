# Parallel Document Upload Flow

## Overview
This document explains the complete flow when multiple upload requests arrive simultaneously.

## Scenario: 3 Concurrent Uploads

**Setup:**
- Entity A uploads `doc1.pdf` (5MB)
- Entity B uploads `doc2.pdf` (5MB)
- Entity C uploads `doc3.pdf` (5MB)
- All requests arrive within 10ms

---

## Phase 1: Request Arrival (0-50ms)

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Server (Uvicorn)                  │
│                                                               │
│  Thread Pool: 8 workers (default)                           │
│                                                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │Worker 1  │  │Worker 2  │  │Worker 3  │                  │
│  │          │  │          │  │          │                  │
│  │Entity A  │  │Entity B  │  │Entity C  │                  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                  │
│       │             │             │                          │
└───────┼─────────────┼─────────────┼──────────────────────────┘
        │             │             │
        ▼             ▼             ▼
```

**What happens:**
1. FastAPI assigns each request to a separate worker thread
2. Each worker calls `upload_file()` independently
3. No blocking yet - all proceed in parallel

**Code Location:** [api/main.py:304-383](../../api/main.py#L304-383)

```python
@app.post("/api/entities/{entity_id}/files")
async def upload_file(entity_id: str, file: UploadFile = File(...)):
    # Each request gets its own thread
    # entity_id: "entity_a", "entity_b", "entity_c"
```

---

## Phase 2: File Saving (50-150ms)

```
┌─────────────────────────────────────────────────────────────┐
│                     File System (Parallel)                   │
│                                                               │
│  UPLOAD_DIR/entity_a/doc1.pdf  ← Worker 1 writes            │
│  UPLOAD_DIR/entity_b/doc2.pdf  ← Worker 2 writes            │
│  UPLOAD_DIR/entity_c/doc3.pdf  ← Worker 3 writes            │
│                                                               │
│  ✓ No contention - different directories                    │
└─────────────────────────────────────────────────────────────┘
```

**What happens:**
1. Each worker saves uploaded file to entity-specific directory
2. Files write in parallel (different paths, no OS-level contention)
3. Each worker now has a local `file_path` variable

**Code Location:** [api/main.py:328-334](../../api/main.py#L328-334)

```python
file_path = UPLOAD_DIR / entity_id / file.filename
file_path.parent.mkdir(parents=True, exist_ok=True)
with open(file_path, "wb") as f:
    content = await file.read()
    f.write(content)  # Parallel writes to different files
```

---

## Phase 3: Indexing Request (150-200ms)

```
┌─────────────────────────────────────────────────────────────┐
│              EntityRAGManager (Singleton)                    │
│                                                               │
│  Shared: embeddings model, thread pool                      │
│                                                               │
│  ┌──────────────────────────────────────────────────┐       │
│  │  _entity_stores: {                                │       │
│  │    "entity_a": EntityVectorStore(RLock A),       │       │
│  │    "entity_b": EntityVectorStore(RLock B),       │       │
│  │    "entity_c": EntityVectorStore(RLock C)        │       │
│  │  }                                                │       │
│  └──────────────────────────────────────────────────┘       │
│                                                               │
│  _stores_lock (only for cache access)                       │
└─────────────────────────────────────────────────────────────┘
```

**What happens:**
1. `index_document_entity_scoped()` calls `manager.add_document()`
2. Manager retrieves or creates entity-specific stores
3. **Brief lock** on `_stores_lock` to access cache (microseconds)
4. Each entity gets its own `EntityVectorStore` with independent `RLock`

**Code Location:** [src/core/entity_scoped_rag.py:573-590](../../src/core/entity_scoped_rag.py#L573-590)

```python
def get_entity_store(self, entity_id: str) -> EntityVectorStore:
    with self._stores_lock:  # Quick cache lookup
        if entity_id not in self._entity_stores:
            self._entity_stores[entity_id] = EntityVectorStore(...)
        return self._entity_stores[entity_id]
```

---

## Phase 4: Document Processing (200ms-2s) ⚡ PARALLEL

```
┌──────────────────────────────────────────────────────────────────┐
│                Entity A Store (Worker 1)                          │
│  NO LOCK HELD                                                     │
│  ✓ Calculate SHA-256 hash        [CPU: 100ms]                   │
│  ✓ Read file content              [I/O: 50ms]                    │
│  ✓ Call file processor API        [Network: 500ms]               │
│  ✓ Chunk into 50 chunks           [CPU: 300ms]                   │
│  ✓ Format metadata                [CPU: 10ms]                    │
│                                                                    │
│  Total: ~1000ms WITHOUT blocking others                          │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                Entity B Store (Worker 2)                          │
│  NO LOCK HELD - Running in parallel with Entity A                │
│  ✓ Calculate SHA-256 hash        [CPU: 100ms]                   │
│  ✓ Read file content              [I/O: 50ms]                    │
│  ✓ Call file processor API        [Network: 500ms]               │
│  ✓ Chunk into 50 chunks           [CPU: 300ms]                   │
│  ✓ Format metadata                [CPU: 10ms]                    │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                Entity C Store (Worker 3)                          │
│  NO LOCK HELD - Running in parallel with Entity A & B            │
│  ✓ Calculate SHA-256 hash        [CPU: 100ms]                   │
│  ✓ Read file content              [I/O: 50ms]                    │
│  ✓ Call file processor API        [Network: 500ms]               │
│  ✓ Chunk into 50 chunks           [CPU: 300ms]                   │
│  ✓ Format metadata                [CPU: 10ms]                    │
└──────────────────────────────────────────────────────────────────┘

KEY OPTIMIZATION: All three entities process in parallel!
Total wall-clock time: ~1000ms (not 3000ms)
```

**What happens:**
1. Each entity processes its document **independently**
2. CPU-intensive operations (hashing, chunking) run **without locks**
3. All three workers utilize separate CPU cores
4. This is the longest phase but fully parallelized

**Code Location:** [src/core/entity_scoped_rag.py:170-187](../../src/core/entity_scoped_rag.py#L170-187)

```python
def add_document(self, file_path: str, metadata: Optional[Dict[str, Any]] = None):
    # NO LOCK YET - all parallel
    content_hash = self._calculate_file_hash(file_path)       # 100ms

    if content_hash and content_hash in self.document_hashes:
        return {"is_duplicate": True}  # Quick exit

    # Still NO LOCK - CPU intensive work
    chunks = self._process_document(file_path, metadata)      # 1000ms
    doc_id = chunks[0]['metadata']['doc_id']

    # NOW acquire lock (see Phase 5)
```

---

## Phase 5: Vector Store Update (2s-2.2s) 🔒 SERIALIZED PER ENTITY

```
Time: 2000ms        2050ms        2100ms        2150ms        2200ms
      │             │             │             │             │
      │             │             │             │             │
A:    [Lock A]──────► Add to     ► Save        ► Release     │
      Acquire        FAISS         FAISS         Lock A       │
      │             │             │             │             │
B:    │             [Lock B]──────► Add to     ► Save        ► Release
      │             Acquire        FAISS         FAISS         Lock B
      │             │             │             │             │
C:    │             │             [Lock C]──────► Add to     ► Save
      │             │             Acquire        FAISS         FAISS
      │             │             │             │             │

Each lock held for ~50ms (critical section only)
Different entities don't block each other!
```

**What happens:**
1. Each entity acquires **its own lock** (not a global lock!)
2. Adds chunks to its FAISS vector store (~30ms)
3. Saves vector store to disk (~20ms)
4. Releases lock immediately

**Code Location:** [src/core/entity_scoped_rag.py:189-208](../../src/core/entity_scoped_rag.py#L189-208)

```python
# CRITICAL SECTION - lock held for ~50ms only
with self._lock:  # Entity-specific RLock
    # Double-check for race conditions
    if content_hash and content_hash in self.document_hashes:
        return {"is_duplicate": True}

    # Add to vector store (30ms)
    self._add_chunks_to_vector_store(chunks)

    if content_hash:
        self.document_hashes[content_hash] = doc_id

    # Save vector store (20ms)
    self._save_vector_store()
# Lock released - other operations on this entity can proceed
```

**Key Point:** Entity A's lock doesn't block Entity B or C!

---

## Phase 6: Metadata Storage (2.2s-2.3s) 🔒 SHARDED LOCKS

```
┌─────────────────────────────────────────────────────────────┐
│              JSONStorage (Sharded by Entity)                 │
│                                                               │
│  storage/doc_id_name_mapping/                               │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ entity_a     │  │ entity_b     │  │ entity_c     │      │
│  │ .json        │  │ .json        │  │ .json        │      │
│  │              │  │              │  │              │      │
│  │ [Lock A]     │  │ [Lock B]     │  │ [Lock C]     │      │
│  │ Write        │  │ Write        │  │ Write        │      │
│  │ doc1 info    │  │ doc2 info    │  │ doc3 info    │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                               │
│  ✓ Independent file locks - no contention!                  │
└─────────────────────────────────────────────────────────────┘
```

**What happens:**
1. Each entity writes to its **own shard file**
2. Shard key automatically detected: `entity_ids: ["entity_a"]`
3. File lock acquired on **entity-specific file** only
4. Atomic write with temp file + rename
5. Lock released

**Code Location:** [src/infrastructure/storage/json_storage.py:256-309](../../src/infrastructure/storage/json_storage.py#L256-309)

```python
def update_one(self, collection_name, query, update, upsert=True):
    # Extract shard key from query/update
    shard_key = self._extract_shard_key(query)  # "entity_a"

    # Load ONLY the relevant shard (not all entities!)
    collection = self._load_collection(collection_name, shard_key)
    #   Locks: doc_id_name_mapping/entity_a.json (NOT entity_b or entity_c)

    # Modify and save
    # ...

    self._save_collection(collection_name, collection, shard_key)
    #   Locks: doc_id_name_mapping/entity_a.json only
```

**Key Optimization:**
- **Before**: Single `doc_id_name_mapping.json` → All entities block each other
- **After**: Separate files → Entities write in parallel

---

## Complete Timeline Comparison

### ❌ Before Optimization (Sequential)

```
Time:  0s    1s    2s    3s    4s    5s    6s    7s    8s    9s    10s   11s   12s
       │     │     │     │     │     │     │     │     │     │     │     │     │
A:     [════════════ Process ═══════════][Lock][Save]
                                                      B: [════════ Process ════════][Lock][Save]
                                                                                              C: [═══════ Process ═══════][Lock][Save]
       │     │     │     │     │     │     │     │     │     │     │     │     │
Total: 12 seconds (all sequential due to global lock)
```

### ✅ After Optimization (Parallel)

```
Time:  0s    1s    2s    3s
       │     │     │     │
A:     [════ Process ════][Lock A][Save A]
B:     [════ Process ════]  [Lock B][Save B]
C:     [════ Process ════]    [Lock C][Save C]
       │     │     │     │
Total: 2.5 seconds (4.8x speedup!)
```

---

## Lock Contention Matrix

| Operation | Entity A | Entity B | Entity C | Blocks Others? |
|-----------|----------|----------|----------|----------------|
| File Save | ✓ Running | ✓ Running | ✓ Running | ❌ No |
| Hash Calc | ✓ Running | ✓ Running | ✓ Running | ❌ No |
| Chunking  | ✓ Running | ✓ Running | ✓ Running | ❌ No |
| VStore Lock | 🔒 Lock A | 🔒 Lock B | 🔒 Lock C | ❌ No (different locks!) |
| Storage Write | 🔒 Shard A | 🔒 Shard B | 🔒 Shard C | ❌ No (different files!) |

**Key Insight:** At no point do the three entities block each other!

---

## Edge Cases

### Case 1: Same Entity, Multiple Documents

```
Entity A - doc1:  [Process doc1]──[Lock A]─[Save]
Entity A - doc2:               [Wait]──────[Lock A]─[Save]
                                    ▲
                               Blocks here
```

**Behavior:** Second upload to same entity waits for first to finish (entity lock)
**Why:** Vector store integrity requires serialization per entity
**Workaround:** Upload to different entities in parallel

### Case 2: Duplicate Detection

```
Worker 1: Hash(doc1) = ABC123 ──► Check cache (not found) ──► Process
Worker 2: Hash(doc1) = ABC123 ──────────────────────────────► Check cache
                                                               ▲
                                                          (found - quick exit)
```

**Behavior:** Second upload detected as duplicate and returns immediately
**Protection:** Double-check locking prevents race conditions

### Case 3: High Concurrency (10+ simultaneous uploads)

```
FastAPI Workers: 8 (default)
Requests: 12

Workers 1-8:  [Processing]
Requests 9-12: [Queued in FastAPI]
                     ▲
                Wait for worker
```

**Behavior:** Extra requests queue at FastAPI level
**Solution:** Increase uvicorn workers: `uvicorn main:app --workers 16`

---

## Performance Characteristics

### CPU Utilization

```
Before:  [██░░░░░░] 25% (single-threaded processing)
After:   [████████] 100% (all cores utilized)
```

### Memory Usage

```
Each entity vector store: ~50-200MB (depending on documents)
3 entities: ~150-600MB total
Embeddings model (shared): ~500MB

Total: ~650MB - 1.1GB for 3 concurrent entities
```

### Disk I/O

```
Before: Sequential writes to single file
  └─ doc_id_name_mapping.json (lock contention)

After: Parallel writes to sharded files
  ├─ doc_id_name_mapping/entity_a.json
  ├─ doc_id_name_mapping/entity_b.json
  └─ doc_id_name_mapping/entity_c.json
```

---

## Configuration Tuning

### FastAPI Workers

```python
# In api/main.py or via command line
uvicorn.run(
    "main:app",
    host="0.0.0.0",
    port=8000,
    workers=16  # Increase for high concurrency
)
```

### EntityRAG Thread Pool

```python
# In src/core/entity_scoped_rag.py:569
self._thread_pool = ThreadPoolExecutor(
    max_workers=os.cpu_count() or 4  # Default
)

# Increase for more parallelism
self._thread_pool = ThreadPoolExecutor(max_workers=16)
```

### Optimal Settings

| Workload | FastAPI Workers | ThreadPool Workers |
|----------|----------------|-------------------|
| Low (1-5 concurrent) | 4 | 4 |
| Medium (5-20 concurrent) | 8 | 8 |
| High (20-50 concurrent) | 16 | 16 |
| Very High (50+) | 32 | 32 |

---

## Summary

✅ **What Enables Parallelization:**
1. Per-entity vector stores with independent locks
2. Sharded storage with per-entity files
3. Reduced lock scope (only critical sections)
4. CPU-intensive operations outside locks

✅ **Bottlenecks Eliminated:**
1. ~~Global storage lock~~ → Per-entity shard locks
2. ~~Full-operation entity lock~~ → Critical-section-only lock
3. ~~Sequential processing~~ → Parallel document processing

✅ **Expected Performance:**
- 3-10x speedup for multi-entity uploads
- Near-linear scaling up to CPU core count
- ~50ms lock time vs ~1000ms total processing time

The system now achieves **true parallelization** for multi-entity document uploads! 🚀
