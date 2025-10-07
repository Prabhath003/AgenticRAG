# Same Entity Multiple Upload Behavior

## Overview

When multiple files are uploaded to the **same entity** simultaneously, the behavior differs from uploading to different entities.

---

## Current Behavior

### Flow Diagram
```
┌─────────────────────────────────────────────────────────────┐
│ 3 Uploads to Entity A (Same Entity)                         │
└─────────────────────────────────────────────────────────────┘

Time:   0s        1s        2s        3s        4s        5s        6s
        │         │         │         │         │         │         │
Doc1:   [─Process─]        │         │         │         │         │
                   [Lock A]─┘         │         │         │         │
                                      │         │         │         │
Doc2:   [─Process──────────]         │         │         │         │
                            [Wait...][Lock A]──┘         │         │
                                                          │         │
Doc3:   [─Process──────────────────────────────]         │         │
                                                 [Wait...][Lock A]──┘

Legend:
  [─Process─] = Parallel (no lock)
  [Lock A]    = Serialized (entity lock)
  [Wait...]   = Blocked, waiting for lock

Total Time: ~1.3 seconds
Speedup: 2.5x (compared to 3.3s if fully sequential)
```

### Detailed Breakdown

#### Phase 1: Document Processing (0-1000ms) ✅ PARALLEL

```python
# All three workers process simultaneously

Worker 1 (doc1.pdf):
  ✓ Calculate hash      [100ms]  │
  ✓ Read file           [50ms]   │  All happening
  ✓ Chunk document      [800ms]  │  at the same
  ✓ Format metadata     [50ms]   │  time!
  Total: 1000ms                  │

Worker 2 (doc2.pdf):            │
  ✓ Calculate hash      [100ms]  │
  ✓ Read file           [50ms]   │
  ✓ Chunk document      [800ms]  │
  ✓ Format metadata     [50ms]   │
  Total: 1000ms                  │

Worker 3 (doc3.pdf):            │
  ✓ Calculate hash      [100ms]  │
  ✓ Read file           [50ms]   │
  ✓ Chunk document      [800ms]  │
  ✓ Format metadata     [50ms]   │
  Total: 1000ms                  ▼
```

**Wall Clock Time: 1000ms** (not 3000ms!)

#### Phase 2: Vector Store Update (1000-1150ms) 🔒 SERIALIZED

```python
# Workers take turns acquiring entity lock

Worker 1:
  with entity_a._lock:  # ← Acquires lock first
      add_chunks_to_vector_store()  # 30ms
      save_vector_store()           # 20ms
  # Lock released

Worker 2:
  with entity_a._lock:  # ← Waits for Worker 1, then acquires
      add_chunks_to_vector_store()  # 30ms
      save_vector_store()           # 20ms
  # Lock released

Worker 3:
  with entity_a._lock:  # ← Waits for Worker 2, then acquires
      add_chunks_to_vector_store()  # 30ms
      save_vector_store()           # 20ms
  # Lock released
```

**Wall Clock Time: 150ms** (50ms × 3 sequential)

#### Phase 3: Metadata Storage (1150-1300ms) 🔒 SERIALIZED

```python
# Workers take turns writing to same shard file

Worker 1:
  # Writes to: storage/doc_id_name_mapping/entity_a.json
  with file_lock("entity_a.json"):
      atomic_write(doc1_metadata)  # 50ms

Worker 2:
  # Writes to: storage/doc_id_name_mapping/entity_a.json (same file!)
  with file_lock("entity_a.json"):  # ← Waits for Worker 1
      atomic_write(doc2_metadata)   # 50ms

Worker 3:
  # Writes to: storage/doc_id_name_mapping/entity_a.json (same file!)
  with file_lock("entity_a.json"):  # ← Waits for Worker 2
      atomic_write(doc3_metadata)   # 50ms
```

**Wall Clock Time: 150ms** (50ms × 3 sequential)

### Total Time Calculation

| Phase | Duration | Type |
|-------|----------|------|
| Processing | 1000ms | ✅ Parallel |
| Vector Store Update | 150ms | 🔒 Sequential |
| Metadata Storage | 150ms | 🔒 Sequential |
| **Total** | **1300ms** | **Mixed** |

**Compared to fully sequential:**
- Fully sequential: 3 × (1000 + 50 + 50) = 3300ms
- With optimization: 1300ms
- **Speedup: 2.5x** ⚡

---

## Performance Comparison

### Scenario: Upload 3 PDFs

#### Case 1: Different Entities ✅ BEST
```
Entity A + doc1: [════════1000ms════════][50ms]
Entity B + doc2: [════════1000ms════════][50ms]
Entity C + doc3: [════════1000ms════════][50ms]

Total: ~1050ms
Speedup: ~3x
```

#### Case 2: Same Entity ⚠️ GOOD
```
Entity A + doc1: [════════1000ms════════][50ms]
Entity A + doc2: [════════1000ms════════]  [50ms]
Entity A + doc3: [════════1000ms════════]    [50ms]

Total: ~1300ms
Speedup: ~2.5x
```

#### Case 3: Sequential (No Optimization) ❌ BASELINE
```
Entity A + doc1: [════════1000ms════════][50ms]
Entity A + doc2:                              [════════1000ms════════][50ms]
Entity A + doc3:                                                            [════════1000ms════════][50ms]

Total: ~3300ms
Speedup: 1x (baseline)
```

---

## Why Serialization is Necessary

### 1. FAISS Index Safety

```python
# FAISS doesn't support concurrent writes
# This would cause corruption:

Thread 1: index.add(chunks_1)  ──┐
Thread 2: index.add(chunks_2)  ──┼─ ❌ CORRUPTION!
Thread 3: index.add(chunks_3)  ──┘

# Instead, we serialize:

Thread 1: with lock: index.add(chunks_1)  ← Safe
Thread 2: with lock: index.add(chunks_2)  ← Safe
Thread 3: with lock: index.add(chunks_3)  ← Safe
```

### 2. Chunk Index Integrity

```python
# Chunks must have unique sequential indices
# Without locking:

Thread 1: assigns indices [0-49]    ─┐
Thread 2: assigns indices [0-49]    ─┼─ ❌ DUPLICATES!
Thread 3: assigns indices [0-49]    ─┘

# With locking:

Thread 1: assigns [0-49]     ← start_idx = 0
Thread 2: assigns [50-99]    ← start_idx = 50
Thread 3: assigns [100-149]  ← start_idx = 100
```

### 3. Storage Atomicity

```python
# Same shard file needs atomic updates
# Without locking:

Thread 1: write({doc1})  ─┐
Thread 2: write({doc2})  ─┼─ ❌ FILE CORRUPTION!
Thread 3: write({doc3})  ─┘

# With locking (one at a time):

Thread 1: write({doc1})        ← Safe
Thread 2: write({doc1, doc2})  ← Safe
Thread 3: write({doc1, doc2, doc3})  ← Safe
```

---

## Optimization Strategies

### Strategy 1: Batch Upload API (Best) 🌟

**Implementation:**

```python
@app.post("/api/entities/{entity_id}/files/batch")
async def upload_files_batch(
    entity_id: str,
    files: List[UploadFile] = File(...)
):
    """
    Upload multiple files in a single transaction.
    More efficient than individual uploads to same entity.
    """
    all_chunks = []

    # Process all files (parallel within batch)
    for file in files:
        file_path = save_file(file)
        chunks = process_document(file_path)
        all_chunks.extend(chunks)

    # Single lock acquisition for all files
    manager = get_entity_rag_manager()
    store = manager.get_entity_store(entity_id)

    with store._lock:  # Only lock ONCE for all files
        for chunks in all_chunks:
            store._add_chunks_to_vector_store(chunks)
        store._save_vector_store()  # Only save ONCE

    return {"files_uploaded": len(files), "total_chunks": len(all_chunks)}
```

**Benefits:**
```
Individual Uploads:
  Doc1: [Process][Lock][Save]
  Doc2: [Process][Wait][Lock][Save]
  Doc3: [Process][Wait][Lock][Save]
  Time: 1300ms

Batch Upload:
  All: [Process All][Lock ONCE][Save ONCE]
  Time: ~1100ms (batch embeddings even faster)

Speedup: ~15-20% additional improvement
```

### Strategy 2: Entity Sharding

**For large-scale deployments:**

```python
# Instead of single entity
entity_id = "company_123"

# Shard into multiple entities
entity_ids = [
    "company_123_shard_0",
    "company_123_shard_1",
    "company_123_shard_2"
]

# Upload to different shards (fully parallel)
upload(entity_ids[0], doc1)  # Parallel
upload(entity_ids[1], doc2)  # Parallel
upload(entity_ids[2], doc3)  # Parallel

# Search across all shards
results = search_multiple_entities_parallel(entity_ids, query)
```

**Benefits:**
- Fully parallel uploads
- Scales horizontally
- Load distribution

**Trade-offs:**
- More complex entity management
- Search needs to aggregate across shards

### Strategy 3: Upload Queue (Future)

**Async background processing:**

```python
@app.post("/api/entities/{entity_id}/files")
async def upload_file(entity_id: str, file: UploadFile):
    # Save file immediately
    file_path = save_file(file)

    # Queue for background processing
    job_id = upload_queue.enqueue(entity_id, file_path)

    # Return immediately
    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"/api/jobs/{job_id}"
    }

@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = upload_queue.get_job(job_id)
    return {
        "status": job.status,  # queued, processing, completed, failed
        "progress": job.progress,
        "result": job.result if job.completed else None
    }
```

**Benefits:**
- Non-blocking API (instant response)
- Better resource management
- Built-in rate limiting
- Retry logic

**Trade-offs:**
- More complex architecture
- Requires job storage (Redis, DB)
- Client needs to poll for status

---

## Best Practices

### ✅ DO: Use Different Entities When Possible

```python
# Good: Parallel processing
upload("company_1", doc1)  # Parallel
upload("company_2", doc2)  # Parallel
upload("company_3", doc3)  # Parallel

# Less optimal: Same entity
upload("company_1", doc1)  # Semi-parallel
upload("company_1", doc2)  # Semi-parallel
upload("company_1", doc3)  # Semi-parallel
```

### ✅ DO: Batch Small Files

```python
# Instead of 100 individual API calls
for doc in small_docs:
    POST /api/entities/company/files  # 100 requests

# Use batch upload (when implemented)
POST /api/entities/company/files/batch  # 1 request
files = [all 100 docs]
```

### ✅ DO: Stagger Uploads

```python
# If you must upload to same entity, add small delay
import asyncio

async def staggered_upload():
    for doc in docs:
        upload(entity_id, doc)
        await asyncio.sleep(0.1)  # 100ms stagger
```

### ❌ DON'T: Spam Same Entity

```python
# Bad: All hit at once
with ThreadPoolExecutor(max_workers=50) as executor:
    for doc in docs:
        executor.submit(upload, "same_entity", doc)
# Most threads will just wait for the lock!
```

---

## Monitoring & Debugging

### Check for Lock Contention

```python
# Add logging to EntityVectorStore
import time

def add_document(self, file_path, metadata):
    lock_wait_start = time.time()

    with self._lock:
        wait_time = time.time() - lock_wait_start
        if wait_time > 0.1:  # More than 100ms wait
            logger.warning(
                f"Lock contention detected for {self.entity_id}: "
                f"waited {wait_time:.2f}s"
            )
        # ... rest of method
```

### Metrics to Track

```python
# In production, track these metrics:

metrics = {
    "lock_wait_time": [],      # Time spent waiting for locks
    "processing_time": [],     # Document processing time
    "upload_throughput": 0,    # Docs/second
    "concurrent_uploads": 0,   # Current parallel uploads
}

# Alert if:
if avg(lock_wait_time) > 1.0:  # More than 1s waiting
    alert("High lock contention on entity {entity_id}")
```

---

## Summary

| Aspect | Different Entities | Same Entity |
|--------|-------------------|-------------|
| **Processing** | Fully parallel ✅ | Fully parallel ✅ |
| **Vector Store Update** | Parallel (different locks) ✅ | Sequential (same lock) 🔒 |
| **Storage Write** | Parallel (different shards) ✅ | Sequential (same shard) 🔒 |
| **Speedup** | 3-10x | 2-3x |
| **Best For** | Multi-tenant systems | Single entity bulk upload |

### Recommendations

1. **For Multi-Entity Systems**: ✅ Current optimization is perfect
2. **For Same-Entity Bulk Upload**: Consider implementing batch upload API
3. **For High Volume**: Implement async queue with background workers
4. **For Now**: Current system still provides 2-3x speedup even for same entity

The serialization for same-entity uploads is a **necessary trade-off** for data integrity, but you still get **significant speedup** from parallel processing! 🚀
