# ChromaDB Singleton Pattern Usage

Both `ChromaDBStore` and `S3Service` now use the **singleton pattern**, meaning only one instance exists across your entire application.

## Why Singleton?

- **Efficiency**: Reuses database connections
- **Consistency**: Single source of truth for vector data
- **Thread-safety**: Built-in locking for concurrent access
- **Simplicity**: Get instance anywhere in your code

## Usage

### Basic Usage (Recommended)

```python
from src.infrastructure.storage import get_chromadb_store

# Get singleton instance (created on first call, reused thereafter)
store = get_chromadb_store(
    persist_dir="./data/chromadb",
    mode="development",
)

# Add documents
store.add_documents("documents", ["text 1", "text 2"])

# Query
results = store.query("documents", query_texts=["search query"])
```

### Subsequent Calls

Once initialized, you can call `get_chromadb_store()` without arguments:

```python
# In a different module/function
from src.infrastructure.storage import get_chromadb_store

# Returns the SAME instance created earlier
store = get_chromadb_store()

results = store.query("documents", query_texts=["another query"])
```

### Production Mode with S3

Initialize once at application startup:

```python
from src.infrastructure.storage import get_chromadb_store

# Initialize for production (S3 enabled)
store = get_chromadb_store(
    persist_dir="/data/chromadb",
    mode="production",
    s3_enabled=True,
)

# Now use S3 operations
store.backup_to_s3("documents")
store.restore_from_s3("documents")
```

## Singleton Pattern Details

### Thread Safety

Both singletons use double-checked locking for thread-safe initialization:

```python
# Both safe to call from multiple threads
store1 = get_chromadb_store()  # Thread 1
store2 = get_chromadb_store()  # Thread 2

assert store1 is store2  # Same instance!
```

### Initialization (First Call Only)

Configuration is only applied on first call:

```python
# First call - configuration applied
store = get_chromadb_store(
    persist_dir="./data/chromadb",
    mode="development",
    s3_enabled=False,
)

# Subsequent calls - arguments ignored, returns existing instance
store2 = get_chromadb_store(
    persist_dir="/other/path",  # Ignored
    mode="production",           # Ignored
)

assert store is store2
assert store.persist_dir == Path("./data/chromadb")  # Original config
```

## S3Service Singleton

Similar pattern for S3:

```python
from src.infrastructure.storage import get_s3_service

# Get singleton S3 service (uses AWS credentials from Config)
s3_service = get_s3_service()

# Operations
s3_service.upload_file_from_path("local.txt", "s3_key")
s3_service.download_file_to_path("s3_key", "local.txt")
objects = s3_service.list_objects(prefix="documents/")
```

## Application Startup Pattern

Recommended pattern for production applications:

```python
# app.py or main.py
from src.infrastructure.storage import get_chromadb_store, get_s3_service

def initialize_storage():
    """Initialize storage singletons at app startup."""
    # Initialize ChromaDB store
    store = get_chromadb_store(
        persist_dir="/data/chromadb",
        mode="production",
        s3_enabled=True,
    )
    logger.info(f"ChromaDB initialized: {store.list_collections()}")

    # Initialize S3 service
    s3_service = get_s3_service()
    logger.info(f"S3 service initialized: {s3_service.bucket_name}")

    return store, s3_service

if __name__ == "__main__":
    store, s3 = initialize_storage()
    # Now use throughout app
```

Then in other modules:

```python
# other_module.py
from src.infrastructure.storage import get_chromadb_store

def process_documents(docs):
    store = get_chromadb_store()  # Gets existing instance
    store.add_documents("documents", docs)
    return store.query("documents", query_texts=["search"])
```

## Benefits

✅ **No multiple instances** - Memory efficient
✅ **Thread-safe** - Safe for concurrent operations
✅ **Clean API** - Simple `get_chromadb_store()` everywhere
✅ **Lazy initialization** - Created on first use
✅ **Reusable connections** - No connection overhead

## Testing

For testing, you can reinitialize with test settings:

```python
# conftest.py (pytest)
import pytest
from src.infrastructure.storage import _chromadb_store_instance, ChromaDBStore

@pytest.fixture
def test_store():
    """Create isolated test store."""
    global _chromadb_store_instance
    original = _chromadb_store_instance

    # Create new instance for testing
    _chromadb_store_instance = ChromaDBStore(
        persist_dir="./test_data/chromadb",
        mode="development",
    )

    yield _chromadb_store_instance

    # Restore original
    _chromadb_store_instance = original
```

## Troubleshooting

**Q: Why am I getting different instances?**
A: You're not - you're getting the same instance. Check that you're calling `get_chromadb_store()` not `ChromaDBStore()` directly.

**Q: How do I reset the singleton?**
A: Reset the module-level variable:
```python
from src.infrastructure.storage import _chromadb_store
_chromadb_store_instance = None  # Resets singleton
```

**Q: Can I have multiple ChromaDB stores?**
A: The singleton pattern enforces one instance per process. If you need multiple isolated stores, use `ChromaDBStore()` directly (not recommended for most cases).
