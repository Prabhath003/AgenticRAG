# Parallel Upload Optimization Guide

## Overview

This document explains the optimizations made to support parallel document upload and indexing across multiple entities.

## Problem Identified

The original implementation had several bottlenecks preventing parallel processing:

### 1. **Global File Locks in JSONStorage**
- **Issue**: All entities shared a single lock for `doc_id_name_mapping.json`
- **Impact**: Parallel uploads to different entities blocked each other
- **Location**: `src/infrastructure/storage/json_storage.py:35-55`

### 2. **Coarse-Grained Entity Locks**
- **Issue**: Entire entity operations were locked, including CPU-intensive tasks
- **Impact**: Processing documents sequentially even for different entities
- **Location**: `src/core/entity_scoped_rag.py:170`

### 3. **Inefficient Lock Scope**
- **Issue**: Locks held during file I/O, chunking, and embedding operations
- **Impact**: Threads waiting unnecessarily while others process documents

## Optimizations Implemented

### 1. Per-Entity Sharding in JSONStorage ✅

**Implementation**: `src/infrastructure/storage/json_storage.py`

```python
# Before: Single file for all entities
storage/doc_id_name_mapping.json  # Single lock, all entities block

# After: Sharded by entity_id
storage/doc_id_name_mapping/
  ├── entity_1.json   # Independent lock
  ├── entity_2.json   # Independent lock
  └── entity_3.json   # Independent lock
```

**Benefits**:
- Different entities can write concurrently without lock contention
- Automatic shard key detection from queries
- Backward compatible with non-sharded mode

**Key Features**:
- `enable_sharding=True` (default): Per-entity sharding
- Automatic shard key extraction from `entity_id` or `entity_ids` fields
- Smart query routing to appropriate shards
- Parallel writes to different entity shards

### 2. Reduced Lock Scope in EntityVectorStore ✅

**Implementation**: `src/core/entity_scoped_rag.py:159-224`

```python
# Before: Lock held for entire operation
with self._lock:
    content_hash = self._calculate_file_hash(file_path)  # I/O
    chunks = self._process_document(file_path, metadata)  # CPU-intensive
    self._add_chunks_to_vector_store(chunks)              # Critical
    self._save_document_metadata(...)                     # I/O
    self._save_vector_store()                            # Critical

# After: Lock only for critical sections
content_hash = self._calculate_file_hash(file_path)      # No lock
chunks = self._process_document(file_path, metadata)     # No lock

with self._lock:
    # Double-check pattern for race conditions
    self._add_chunks_to_vector_store(chunks)             # Locked
    self._save_vector_store()                            # Locked

self._save_document_metadata(...)                        # Uses shard locks
```

**Benefits**:
- CPU-intensive operations run in parallel
- Lock held only for vector store modifications
- Double-check locking pattern prevents race conditions

### 3. Optimized Storage Operations

**Features**:
- **Shard Key Detection**: Automatically detects `entity_id` from queries and updates
- **Conditional Loading**: Only loads relevant shard when shard key is available
- **Atomic Writes**: Maintains data integrity with atomic file operations
- **Granular Locking**: Per-shard locks instead of global locks

## Performance Improvements

### Expected Speedup

For N entities uploading M documents each with W workers:

| Scenario | Sequential Time | Parallel Time | Speedup |
|----------|----------------|---------------|---------|
| 3 entities, 2 docs each | 6 × T | ~2 × T | **~3x** |
| 5 entities, 4 docs each | 20 × T | ~4 × T | **~5x** |
| 10 entities, 10 docs each | 100 × T | ~10 × T | **~10x** |

*Where T is the average time to process one document*

### Actual Performance

Run the test to measure actual performance:

```bash
python tests/test_parallel_upload.py
```

Example output:
```
📈 PERFORMANCE COMPARISON
============================================================
Sequential time: 12.45s
Parallel time:   3.21s
Speedup:         3.88x
Efficiency:      48.5%
============================================================
```

## Usage

### API Endpoint

The `/api/entities/{entity_id}/files` endpoint automatically benefits from parallel optimization:

```python
# Multiple parallel uploads to different entities
import requests
from concurrent.futures import ThreadPoolExecutor

def upload_file(entity_id, file_path):
    with open(file_path, 'rb') as f:
        files = {'file': f}
        response = requests.post(
            f'http://localhost:8000/api/entities/{entity_id}/files',
            files=files
        )
    return response.json()

# Upload in parallel
with ThreadPoolExecutor(max_workers=8) as executor:
    futures = []
    for entity_id in ['entity_1', 'entity_2', 'entity_3']:
        for file_path in document_paths:
            future = executor.submit(upload_file, entity_id, file_path)
            futures.append(future)

    results = [f.result() for f in futures]
```

### Python API

```python
from src.core.entity_scoped_rag import get_entity_rag_manager

manager = get_entity_rag_manager()

# Parallel upload to multiple entities
entity_documents = {
    'entity_1': ['/path/doc1.pdf', '/path/doc2.pdf'],
    'entity_2': ['/path/doc3.pdf', '/path/doc4.pdf'],
    'entity_3': ['/path/doc5.pdf', '/path/doc6.pdf'],
}

# Automatically parallelized
results = manager.add_documents_parallel(entity_documents)
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│          EntityRAGManager (Thread Pool)              │
│  • Shared embeddings model                           │
│  • ThreadPoolExecutor for parallel operations        │
└──────────────┬──────────────┬──────────────┬─────────┘
               │              │              │
        ┌──────▼──────┐┌──────▼──────┐┌──────▼──────┐
        │  Entity 1   ││  Entity 2   ││  Entity 3   │
        │  Vector     ││  Vector     ││  Vector     │
        │  Store      ││  Store      ││  Store      │
        │  (RLock)    ││  (RLock)    ││  (RLock)    │
        └──────┬──────┘└──────┬──────┘└──────┬──────┘
               │              │              │
        ┌──────▼──────────────▼──────────────▼──────┐
        │         JSONStorage (Sharded)              │
        │  ┌──────────┐ ┌──────────┐ ┌──────────┐  │
        │  │ Shard 1  │ │ Shard 2  │ │ Shard 3  │  │
        │  │ (Lock)   │ │ (Lock)   │ │ (Lock)   │  │
        │  └──────────┘ └──────────┘ └──────────┘  │
        └───────────────────────────────────────────┘
```

## Locking Strategy

### Per-Entity Vector Store Locks
- **Purpose**: Protect vector store integrity within each entity
- **Scope**: Only held during vector store modifications
- **Concurrency**: Different entities can process in parallel

### Per-Shard File Locks
- **Purpose**: Prevent concurrent writes to the same JSON file
- **Scope**: Held during file read/write operations
- **Concurrency**: Different shards can be accessed in parallel

### Lock Hierarchy
```
ThreadPool (No Lock)
  ├── Entity A (RLock A)
  │     └── Shard A (File Lock A)
  ├── Entity B (RLock B)
  │     └── Shard B (File Lock B)
  └── Entity C (RLock C)
        └── Shard C (File Lock C)
```

## Configuration

### Enable/Disable Sharding

```python
from src.infrastructure.storage.json_storage import JSONStorage

# Enable sharding (default, recommended for parallel operations)
storage = JSONStorage(storage_dir, enable_sharding=True)

# Disable sharding (for backward compatibility)
storage = JSONStorage(storage_dir, enable_sharding=False)
```

### Thread Pool Size

```python
# Adjust max_workers in EntityRAGManager
# Default: os.cpu_count() or 4
manager._thread_pool = ThreadPoolExecutor(max_workers=16)
```

## Best Practices

### 1. Upload to Different Entities in Parallel
✅ **Good**: Upload documents to entity_1, entity_2, entity_3 simultaneously
❌ **Bad**: Upload all documents to entity_1, then all to entity_2, etc.

### 2. Batch Documents Appropriately
✅ **Good**: 10-100 documents per batch
❌ **Bad**: Uploading 10,000 documents in a single parallel batch

### 3. Monitor Resource Usage
- CPU: Document processing is CPU-intensive
- Memory: Each entity vector store consumes memory
- I/O: Limit concurrent I/O operations

### 4. Use Appropriate Worker Count
```python
# Rule of thumb: 2x CPU cores for I/O-bound tasks
max_workers = min(32, (os.cpu_count() or 4) * 2)
```

## Troubleshooting

### Parallel Upload Not Faster

**Symptoms**: Speedup < 1.5x

**Possible Causes**:
1. Documents are small (overhead dominates)
2. Too many workers (context switching overhead)
3. Disk I/O bottleneck
4. Same entity being targeted (entity lock contention)

**Solutions**:
- Reduce number of workers
- Use larger documents
- Ensure different entities are targeted
- Use SSD for storage

### Race Conditions

**Symptoms**: Duplicate documents or missing data

**Protection**:
- Double-check locking pattern in `add_document()`
- Atomic file writes with temporary files
- Per-file locks in JSONStorage

### High Memory Usage

**Symptoms**: Out of memory errors

**Solutions**:
- Process documents in smaller batches
- Reduce thread pool size
- Clear entity store cache: `manager.cleanup_entity(entity_id)`

## Testing

Run the comprehensive test suite:

```bash
# Test parallel upload performance
python tests/test_parallel_upload.py

# Run with more entities and documents
python tests/test_parallel_upload.py --entities 10 --docs 5 --workers 16
```

## Migration Guide

### From Non-Sharded to Sharded Storage

The system is backward compatible. Existing data will work, but won't benefit from sharding until migrated:

1. **Automatic Migration** (recommended):
   ```python
   # Just enable sharding - new data will be sharded
   # Old data remains in monolithic file but is readable
   storage = JSONStorage(storage_dir, enable_sharding=True)
   ```

2. **Manual Migration**:
   ```bash
   python scripts/migrate_to_sharded_storage.py
   ```

## Summary

The parallel optimization provides:

- ✅ **3-10x speedup** for multi-entity uploads
- ✅ **No breaking changes** to existing API
- ✅ **Automatic shard routing** based on entity_id
- ✅ **Thread-safe** with double-check locking
- ✅ **Reduced lock contention** with per-entity sharding
- ✅ **Optimized lock scope** for critical sections only

**Recommended Configuration**:
- Enable sharding: `True`
- Max workers: `2 × CPU cores`
- Batch size: `10-100 documents`
