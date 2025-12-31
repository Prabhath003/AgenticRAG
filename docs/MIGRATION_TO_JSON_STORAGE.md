# Migration from MongoDB to JSON Storage

## Overview

This project has been migrated from MongoDB to a JSON-based storage system with atomic writes and file locking for thread-safe operations.

## Changes Made

### 1. New JSON Storage System

Created a new storage module at `src/infrastructure/storage/json_storage.py` with the following features:

- **Atomic Writes**: Uses temporary files and atomic rename operations to prevent data corruption
- **File Locking**: Thread-safe operations using file-specific locks
- **MongoDB-like API**: Provides a familiar interface similar to MongoDB for easy migration
- **Cross-platform Support**: Handles Windows and POSIX systems differently for atomic operations

Key classes:
- `JSONStorage`: Core storage engine with atomic write operations
- `JSONStorageSession`: Context manager for storage operations
- `JSONCollection`: MongoDB-like collection interface

### 2. Updated RAG System

Modified `src/core/rag_system.py` to use JSON storage:

- Replaced all `get_db_session()` calls with `get_storage_session()`
- Updated imports to use the new storage module
- All database operations now use JSON files instead of MongoDB

### 3. Configuration Updates

Updated `src/config/settings.py`:

```python
# Storage Collections
DOC_ID_NAME_MAPPING_COLLECTION = "doc_id_name_mapping"
ENTITY_MAPPINGS_COLLECTION = "entity_mappings"
CHUNKS_COLLECTION = "chunks"
```

### 4. Infrastructure Updates

Modified `src/infrastructure/__init__.py`:
- Removed MongoDB dependencies
- Kept only file processing functions

## Storage Structure

All data is stored in JSON files under `data/storage/`:

```
data/
└── storage/
    ├── doc_id_name_mapping.json
    ├── entity_mappings.json
    └── chunks.json
```

## Atomic Write Technique

The atomic write implementation prevents data loss during write operations:

```python
def _atomic_write_json(self, data: Any, filename: str) -> None:
    """
    Atomically write JSON data to file using temporary file and rename.
    This prevents data loss if the process is killed mid-write.
    """
    # 1. Create temp file in same directory (same filesystem)
    fd, temp_path = tempfile.mkstemp(...)

    # 2. Write data to temp file with fsync
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, ...)
        f.flush()
        os.fsync(f.fileno())  # Force write to disk

    # 3. Atomic rename (key operation)
    os.replace(temp_path, filename)  # Atomic on POSIX
```

### Windows Support

On Windows, where `os.replace()` requires special handling:

```python
# Create backup first
backup_path = filename + '.bak'
shutil.copy2(filename, backup_path)

try:
    os.replace(temp_path, filename)
    os.remove(backup_path)  # Remove backup on success
except Exception:
    shutil.copy2(backup_path, filename)  # Restore on failure
    raise
```

## Thread Safety

File locks ensure thread-safe operations:

```python
@classmethod
def _get_file_lock(cls, file_path: str) -> threading.Lock:
    """Get or create a lock for a specific file path"""
    with cls._file_locks_lock:
        if file_path not in cls._file_locks:
            cls._file_locks[file_path] = threading.Lock()
        return cls._file_locks[file_path]
```

## API Compatibility

The new storage system maintains MongoDB-like API:

```python
# Before (MongoDB)
with get_db_session() as db:
    db["collection"].update_one(
        {"_id": "doc1"},
        {"$set": {"field": "value"}},
        upsert=True
    )

# After (JSON Storage)
with get_storage_session() as db:
    db["collection"].update_one(
        {"_id": "doc1"},
        {"$set": {"field": "value"}},
        upsert=True
    )
```

## Supported MongoDB Operations

The JSON storage system supports common MongoDB operations:

- **Queries**: `find()`, `find_one()`
- **Updates**: `update_one()`, `update_many()`
- **Deletes**: `delete_one()`, `delete_many()`
- **Aggregation**: `aggregate()` (basic support)

### Query Operators

- `$exists`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`
- `$or`, `$and`
- Nested field queries with dot notation

### Update Operators

- `$set` - Set field values
- `$unset` - Remove fields
- `$addToSet` - Add to array (no duplicates)
- `$setOnInsert` - Set values only on insert (upsert)

## Testing

Run the test suite to verify functionality:

```bash
python test_json_storage.py
```

Tests cover:
- Insert operations
- Find operations with queries
- Update operations
- Delete operations
- Atomic writes
- Thread safety
- Session context managers

## Performance Considerations

### Pros:
- No database server required
- Simple deployment
- Easy backup (just copy JSON files)
- Version control friendly
- Atomic operations prevent corruption

### Cons:
- Full file read/write for each operation
- Not suitable for very large datasets (>10MB per collection)
- No built-in indexing (linear search)
- No distributed operations

### Recommendations:
- Use for small to medium datasets (< 10,000 documents per collection)
- Consider MongoDB for production with large datasets
- Implement caching for frequently accessed data
- Monitor file sizes and optimize if needed

## Migration Path

If you need to migrate back to MongoDB or to another database:

1. The storage interface is abstracted - just change `get_storage_session()` implementation
2. Keep the same MongoDB-like API
3. Data can be easily exported/imported from JSON files

## Future Enhancements

Potential improvements:

- [ ] Add indexing support for faster queries
- [ ] Implement query caching
- [ ] Add compression for large files
- [ ] Support for sharding/partitioning large collections
- [ ] Add transaction support
- [ ] Implement write-ahead logging (WAL)

## Troubleshooting

### Issue: File permission errors

**Solution**: Ensure the `data/storage/` directory has write permissions:
```bash
chmod -R 755 data/storage/
```

### Issue: Corrupted JSON files

**Solution**: The atomic write mechanism should prevent this, but if it happens:
1. Check for `.bak` files in the storage directory
2. Restore from backup if available
3. Check system logs for disk issues

### Issue: Performance degradation

**Solution**:
1. Check file sizes - optimize or split large collections
2. Reduce concurrent access if possible
3. Consider migrating to MongoDB for better performance

## Contact

For questions or issues, contact:
- Author: Prabhath Chellingi
- Email: prabhathchellingi2003@gmail.com
- GitHub: https://github.com/Prabhath003
