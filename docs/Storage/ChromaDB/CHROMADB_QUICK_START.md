# ChromaDB Quick Start

**For Development**: 5 minutes to get started

## Installation

```bash
pip install chromadb
```

## Basic Usage

```python
from src.infrastructure.storage import ChromaDBStore

# Initialize
store = ChromaDBStore(persist_dir="./data/chromadb", mode="development")

# Add documents
store.add_documents(
    "documents",
    documents=["text 1", "text 2", "text 3"]
)

# Query
results = store.query(
    "documents",
    query_texts=["search query"],
    n_results=5
)
```

## Common Operations

### Collections
```python
store.list_collections()                    # List all
store.get_collection_stats("documents")     # Get stats
store.delete_collection("documents")        # Delete
```

### Adding Documents
```python
# Simple
store.add_documents("docs", ["text"])

# With metadata
store.add_documents(
    "docs",
    documents=["text"],
    ids=["id1"],
    metadatas=[{"key": "value"}]
)
```

### Querying
```python
# Simple
store.query("docs", query_texts=["query"])

# With filters
store.query(
    "docs",
    query_texts=["query"],
    n_results=10,
    where={"category": "important"}
)
```

### Batch Operations
```python
with store.transaction():
    for batch in batches:
        store.add_documents("docs", batch)
```

## File Locations

| What | Where |
|------|-------|
| Code | `src/infrastructure/storage/_chromadb_store.py` |
| Full Docs | `docs/CHROMADB_DEVELOPMENT_GUIDE.md` |
| Production | `docs/CHROMADB_S3_ARCHITECTURE.md` |
| Examples | `examples/chromadb_example.py` |
| Data | `./data/chromadb/` |

## Test It

```bash
python examples/chromadb_example.py
```

## Production Setup

When ready for production:

```python
store = ChromaDBStore(
    persist_dir="/data/chromadb",
    mode="production",
    s3_enabled=True,
    s3_bucket="chromadb-prod",
)

# Backup to S3
store.backup_to_s3()
```

See `CHROMADB_S3_ARCHITECTURE.md` for full production guide.

## Integration

Use in your RAG system:

```python
from src.infrastructure.storage import ChromaDBStore

store = ChromaDBStore()
store.add_documents("documents", your_documents)
results = store.query("documents", your_query)
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Import error | `pip install chromadb` |
| Collection not found | Check `store.list_collections()` |
| Slow queries | Use metadata filters with `where` |
| Storage full | Archive to S3 (production mode) |

## Next

- Read `CHROMADB_DEVELOPMENT_GUIDE.md` for details
- Check `examples/chromadb_example.py` for working code
- Review `CHROMADB_S3_ARCHITECTURE.md` for production setup
