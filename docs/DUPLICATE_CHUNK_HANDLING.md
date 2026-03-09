# Duplicate Chunk Handling in ChromaDBStore

## Overview

ChromaDBStore now includes comprehensive duplicate detection and handling for chunk operations. This prevents data inconsistency and provides control over how duplicates are managed.

## Changes Made

### 1. Enhanced `add_chunks()` Method

**Signature**:
```python
def add_chunks(
    self,
    collection_name: str,
    chunks: List[Chunk],
    skip_duplicates: bool = True,
) -> List[str]:
```

**Behavior**:
- Automatically checks for existing chunk IDs before adding
- Returns only the IDs of chunks that were actually added
- Two modes for handling duplicates:

#### Mode 1: Skip Duplicates (Default)
```python
added_ids = store.add_chunks(collection_name, chunks, skip_duplicates=True)
# Only new chunks are added
# Existing chunks are logged and skipped
# Returns IDs of newly added chunks only
```

**When to use**: Production environments to prevent accidental overwrites

#### Mode 2: Replace Duplicates
```python
added_ids = store.add_chunks(collection_name, chunks, skip_duplicates=False)
# Existing chunks are replaced with new data
# Useful for updating chunk content
# Returns IDs of all added chunks (including replacements)
```

**When to use**: Re-indexing or updating documents

### 2. New `check_duplicate_chunks()` Method

**Signature**:
```python
def check_duplicate_chunks(
    self,
    collection_name: str,
    chunks: List[Chunk],
) -> Dict[str, List[str]]:
```

**Returns**:
```python
{
    "id_duplicates": ["chunk_001", "chunk_002"],      # Existing chunk IDs
    "content_duplicates": ["chunk_003"],               # Same content in same doc
    "duplicate_count": 3                               # Total duplicates
}
```

**Use Cases**:
- Audit duplicate chunks before adding
- Identify problematic content duplicates
- Monitor data quality

**Example**:
```python
chunks_to_add = [chunk1, chunk2, chunk3]
dup_info = store.check_duplicate_chunks("my_collection", chunks_to_add)

if dup_info["duplicate_count"] > 0:
    print(f"Found {dup_info['duplicate_count']} duplicates")
    print(f"ID duplicates: {dup_info['id_duplicates']}")
    print(f"Content duplicates: {dup_info['content_duplicates']}")
```

## Duplicate Detection Types

### 1. ID-Based Duplicates
Chunks with the same `chunk_id` already exist in the collection.

```
Scenario:
- Collection has: chunk_001, chunk_002, chunk_003
- Adding: chunk_002 (duplicate), chunk_004 (new)
- Result (skip_duplicates=True): Only chunk_004 added
- Result (skip_duplicates=False): chunk_002 updated, chunk_004 added
```

### 2. Content-Based Duplicates
Same document has chunks with identical content (even if chunk_id differs).

```
Scenario:
- Document has chunk_A with content "Introduction..."
- Adding chunk_B with content "Introduction..." (same)
- Detection: Content duplicate detected
- Use case: Prevent duplicate sections in same document
```

## Logging

All duplicate operations are logged with details:

```
WARNING: Found 3 duplicate chunk IDs in 'my_collection': ['chunk_001', 'chunk_002', 'chunk_003']...
INFO: Will replace 5 duplicate chunks in 'my_collection'
INFO: Successfully added 10 chunks to 'my_collection' (duplicate handling: skip_duplicates=True)
```

## Usage Examples

### Example 1: Safe Batch Upload
```python
from src.infrastructure.storage import get_chromadb_store

store = get_chromadb_store()
chunks = load_chunks_from_document("document.pdf")

# Safe: Skip duplicates, only add new chunks
added_ids = store.add_chunks("documents", chunks, skip_duplicates=True)
print(f"Added {len(added_ids)} new chunks")
```

### Example 2: Update Existing Document
```python
# Re-index document with updated content
updated_chunks = reprocess_document("document.pdf")

# Replace old chunks with new ones
added_ids = store.add_chunks("documents", updated_chunks, skip_duplicates=False)
print(f"Updated {len(added_ids)} chunks")
```

### Example 3: Pre-Add Validation
```python
# Check what will happen before adding
chunks = prepare_chunks()
dup_info = store.check_duplicate_chunks("documents", chunks)

if dup_info["id_duplicates"]:
    print(f"⚠️  {len(dup_info['id_duplicates'])} chunks will be skipped")

if dup_info["content_duplicates"]:
    print(f"⚠️  {len(dup_info['content_duplicates'])} content duplicates detected")

if dup_info["duplicate_count"] == 0:
    print("✓ All chunks are new")
    store.add_chunks("documents", chunks)
```

### Example 4: Batch Processing with Reporting
```python
documents = load_multiple_documents()

total_duplicates = 0
for doc in documents:
    chunks = chunk_document(doc)

    # Check for duplicates
    dup_info = store.check_duplicate_chunks("docs", chunks)
    if dup_info["duplicate_count"] > 0:
        total_duplicates += dup_info["duplicate_count"]
        print(f"{doc.name}: {dup_info['duplicate_count']} duplicates found")

    # Add with safe mode
    store.add_chunks("docs", chunks, skip_duplicates=True)

print(f"Total duplicates skipped: {total_duplicates}")
```

## Migration from Old Code

If you have code using the old `add_chunks()` without the `skip_duplicates` parameter:

```python
# Old code (still works with default skip_duplicates=True)
store.add_chunks("collection", chunks)

# New code (explicit)
store.add_chunks("collection", chunks, skip_duplicates=True)
```

**Default behavior is backward compatible**: Missing duplicates are skipped by default, which is safer than silent overwrites.

## Performance Considerations

- **Duplicate checking**: O(n) where n is number of chunks to add
- **ID lookup**: Fast (uses ChromaDB's native get operation)
- **Content comparison**: O(n*m) for content duplicates (where m is chunks in document)
- **Impact**: Minimal overhead for typical batch sizes

### Optimization Tips

1. **For large batches**: Enable duplicate checking first
   ```python
   dup_info = store.check_duplicate_chunks(collection, large_batch)
   if dup_info["duplicate_count"] > 0:
       filtered_batch = [c for c in large_batch if c.chunk_id not in dup_info["id_duplicates"]]
       store.add_chunks(collection, filtered_batch)
   ```

2. **For frequent re-indexing**: Use `skip_duplicates=False` to replace in bulk
   ```python
   store.add_chunks(collection, updated_chunks, skip_duplicates=False)
   ```

## Test Coverage

Comprehensive tests in `tests/test_duplicate_detection.py`:

1. ✓ Add new chunks (no duplicates)
2. ✓ Detect duplicates with skip_duplicates=True
3. ✓ Replace duplicates with skip_duplicates=False
4. ✓ Check duplicate chunks method
5. ✓ Content duplicate detection
6. ✓ Batch with mixed new/duplicate chunks
7. ✓ Empty input handling
8. ✓ All-duplicates batch handling

Run tests:
```bash
python tests/test_duplicate_detection.py
```

## Related Files

- **Implementation**: `src/infrastructure/storage/_chromadb_store.py`
  - `add_chunks()`: Lines 277-330
  - `check_duplicate_chunks()`: Lines 213-275

- **Tests**: `tests/test_duplicate_detection.py`

- **Chunk Model**: `src/core/models/core_models.py`

## Future Enhancements

Potential improvements:
1. Content hash-based deduplication for faster comparison
2. Configurable duplicate strategies (merge, update metadata, etc.)
3. Duplicate statistics/reporting dashboard
4. Soft delete + duplicate cleanup background task
