# ChromaDB Development & Local Implementation Guide

**Status**: Development Implementation
**Mode**: Local development with production path

## Quick Start

### Installation

```bash
# Install ChromaDB
pip install chromadb

# For S3 support (optional, production)
pip install boto3
```

### Basic Usage

```python
from src.infrastructure.storage import ChromaDBStore

# Initialize (development mode, local only)
store = ChromaDBStore(
    persist_dir="./data/chromadb",
    mode="development",
)

# Add documents to a collection
doc_ids = store.add_documents(
    collection_name="documents",
    documents=[
        "This is a sample document about AI",
        "Another document about machine learning",
        "Document about vector databases",
    ],
)

# Query
results = store.query(
    collection_name="documents",
    query_texts=["What is AI?"],
    n_results=5,
)

print(results)
```

## Development Setup

### Project Structure

```
src/infrastructure/storage/
├── __init__.py                    # Export ChromaDBStore
├── _chromadb_store.py            # Main implementation
└── ...existing code...

data/
└── chromadb/                      # Local storage (development)
    ├── documents/
    │   ├── data/
    │   └── index/
    ├── summaries/
    └── entities/
```

### Configuration

Create `.env` file:

```env
# Development
CHROMADB_MODE=development
CHROMADB_PERSIST_DIR=./data/chromadb
CHROMADB_ENABLE_S3=false

# Production (for later)
# CHROMADB_MODE=production
# CHROMADB_S3_BUCKET=chromadb-prod
# CHROMADB_S3_REGION=us-east-1
# CHROMADB_S3_ENABLE=true
```

Load in your code:

```python
import os
from pathlib import Path

# Load environment
from dotenv import load_dotenv
load_dotenv()

# Configure store
store = ChromaDBStore(
    persist_dir=os.getenv(
        "CHROMADB_PERSIST_DIR",
        "./data/chromadb"
    ),
    mode=os.getenv("CHROMADB_MODE", "development"),
)
```

## Core Operations

### 1. Collection Management

```python
# Create/get collection
docs_collection = store.get_or_create_collection("documents")

# List all collections
collections = store.list_collections()
# Output: ['documents', 'summaries', 'entities']

# Get collection statistics
stats = store.get_collection_stats("documents")
print(stats)
# Output:
# {
#     'name': 'documents',
#     'document_count': 1250,
#     'mode': 'development',
#     'timestamp': '2026-03-05T10:30:00'
# }

# Delete collection
store.delete_collection("documents")
```

### 2. Adding Documents

```python
# Simple add with auto-generated IDs
ids = store.add_documents(
    collection_name="documents",
    documents=[
        "Document 1 text...",
        "Document 2 text...",
    ],
)

# Add with custom IDs and metadata
ids = store.add_documents(
    collection_name="documents",
    documents=[
        "Document 1 text...",
        "Document 2 text...",
    ],
    ids=["doc_001", "doc_002"],
    metadatas=[
        {"source": "file_a.txt", "date": "2026-03-01"},
        {"source": "file_b.txt", "date": "2026-03-02"},
    ],
)

# Bulk insert with transaction
with store.transaction():
    for batch in document_batches:
        store.add_documents("documents", batch)
```

### 3. Querying

```python
# Simple query
results = store.query(
    collection_name="documents",
    query_texts=["What is RAG?"],
    n_results=5,
)

# Access results
for i, (doc_id, distance) in enumerate(zip(
    results['ids'][0],
    results['distances'][0]
)):
    print(f"{i+1}. {doc_id} (distance: {distance:.4f})")

# Query with metadata filtering
results = store.query(
    collection_name="documents",
    query_texts=["vector search"],
    n_results=10,
    where={"source": "file_a.txt"},  # Filter by metadata
)

# Multiple queries at once
results = store.query(
    collection_name="documents",
    query_texts=[
        "What is machine learning?",
        "What is deep learning?",
    ],
    n_results=5,
)
# Results for both queries returned
```

## Data Organization Patterns

### Pattern 1: Semantic Collections (Recommended for Development)

```python
# Separate by data type
store.get_or_create_collection("documents", metadata={
    "type": "raw_text",
    "purpose": "primary_retrieval",
})

store.get_or_create_collection("summaries", metadata={
    "type": "summary",
    "purpose": "quick_overview",
})

store.get_or_create_collection("entities", metadata={
    "type": "entity",
    "purpose": "knowledge_base,
})
```

Usage:
```python
# For detailed retrieval
docs = store.query("documents", ["complex query"], n_results=10)

# For quick overview
summary = store.query("summaries", ["topic overview"], n_results=3)
```

### Pattern 2: Metadata-Rich Storage

```python
# Store rich metadata with documents
documents = [
    "Text about RAG systems",
    "Text about vector databases",
]

metadatas = [
    {
        "source": "arxiv_2023_001",
        "category": "rag",
        "date": "2023-01-15",
        "author": "Team A",
        "version": "1.0",
    },
    {
        "source": "doc_2024_045",
        "category": "database",
        "date": "2024-02-20",
        "author": "Team B",
        "version": "2.1",
    },
]

store.add_documents(
    "documents",
    documents=documents,
    metadatas=metadatas,
)

# Query with filtering
results = store.query(
    "documents",
    query_texts=["vector search"],
    n_results=5,
    where={"category": "rag"},  # Only RAG documents
)
```

## Testing & Development

### Unit Test Example

```python
import pytest
from src.infrastructure.storage import ChromaDBStore

@pytest.fixture
def store():
    """Create test store."""
    store = ChromaDBStore(
        persist_dir="./test_data/chromadb",
        mode="development",
    )
    yield store
    # Cleanup
    import shutil
    shutil.rmtree("./test_data/chromadb", ignore_errors=True)


def test_add_and_query(store):
    """Test adding and querying documents."""
    # Add
    ids = store.add_documents(
        "test_collection",
        documents=["Document 1", "Document 2"],
    )
    assert len(ids) == 2

    # Query
    results = store.query(
        "test_collection",
        query_texts=["Document"],
        n_results=2,
    )
    assert len(results['ids'][0]) == 2


def test_collection_stats(store):
    """Test collection statistics."""
    store.add_documents(
        "test_collection",
        documents=["Doc 1", "Doc 2", "Doc 3"],
    )

    stats = store.get_collection_stats("test_collection")
    assert stats['document_count'] == 3
```

Run tests:
```bash
pytest tests/ -v
```

## Local Data Management

### Directory Structure

```
data/chromadb/
├── documents/
│   ├── 0/
│   │   ├── f1 (parquet file)
│   │   └── f2
│   └── metadata.parquet
├── summaries/
│   └── ...
└── .chroma/
    └── chroma.sqlite
```

### Manual Operations

```python
# Check data directory
from pathlib import Path
chromadb_dir = Path("./data/chromadb")
print(f"Total size: {sum(f.stat().st_size for f in chromadb_dir.rglob('*') if f.is_file()) / 1e9:.2f} GB")

# Backup locally
import shutil
shutil.copytree("./data/chromadb", "./backup/chromadb_2026-03-05")

# Restore from backup
shutil.rmtree("./data/chromadb")
shutil.copytree("./backup/chromadb_2026-03-05", "./data/chromadb")
```

### Size Estimation

For development:
```
Documents     Vectors    Size
100           1.5K       ~50MB
1,000         15K        ~500MB
10,000        150K       ~5GB
100,000       1.5M       ~50GB
```

## Integration with RAG System

### Example: Integration with Existing RAGSystemPool

```python
from src.infrastructure.storage import ChromaDBStore
from src.core._rag_system import RAGSystemPool

class EnhancedRAGSystemPool:
    def __init__(self):
        self.rag_pool = RAGSystemPool()
        self.chroma_store = ChromaDBStore(
            persist_dir="./data/chromadb",
            mode="development",
        )

    def index_documents(self, documents: List[str]):
        """Index documents in ChromaDB."""
        self.chroma_store.add_documents(
            "documents",
            documents=documents,
        )

    def retrieve(self, query: str, top_k: int = 5):
        """Retrieve relevant documents."""
        results = self.chroma_store.query(
            "documents",
            query_texts=[query],
            n_results=top_k,
        )
        return results
```

## Monitoring & Debugging

### Enable Logging

```python
import logging

# Enable ChromaDB debug logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("chromadb")
logger.setLevel(logging.DEBUG)

# Your store will now log operations
store = ChromaDBStore()
```

### Health Check

```python
def health_check(store: ChromaDBStore) -> Dict[str, Any]:
    """Check store health."""
    try:
        collections = store.list_collections()
        stats = {}

        for coll_name in collections:
            stats[coll_name] = store.get_collection_stats(coll_name)

        return {
            "status": "healthy",
            "collections": stats,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }

# Usage
print(health_check(store))
```

## Migration Path to Production

### Step 1: Verify Development Works
```bash
# Run local tests
pytest tests/test_chromadb_store.py -v
```

### Step 2: Configure for S3 (Production)
```python
# In production environment
store = ChromaDBStore(
    persist_dir="/data/chromadb",
    mode="production",
    s3_enabled=True,
    s3_bucket="chromadb-prod",
    s3_region="us-east-1",
)

# Backup to S3
store.backup_to_s3("documents")  # Single collection
store.backup_to_s3()  # All collections
```

### Step 3: Automated Backups
```python
# In scheduled task (cron/Lambda)
import schedule
import time

def scheduled_backup():
    store = ChromaDBStore(
        mode="production",
        s3_enabled=True,
        s3_bucket="chromadb-prod",
    )
    store.backup_to_s3()

schedule.every().day.at("02:00").do(scheduled_backup)

while True:
    schedule.run_pending()
    time.sleep(60)
```

## Performance Tips (Development)

1. **Batch Operations**: Use transactions for bulk adds
   ```python
   with store.transaction():
       for batch in batches:
           store.add_documents("docs", batch)
   ```

2. **Collection Selectivity**: Only load/query needed collections

3. **Metadata Filtering**: Use where clause to reduce vector search scope

4. **Cache Results**: Cache frequently queried results locally

## Common Issues & Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| "Collection not found" | Typo in name | Check `store.list_collections()` |
| Slow queries | Large dataset | Add metadata filters with `where` |
| Memory issues | Too many collections | Archive old collections to S3 |
| S3 auth errors | Missing IAM role | Verify AWS credentials/IAM permissions |

## Next Steps

1. ✅ Local development implementation complete
2. ⏳ Add GPU embedding acceleration
3. ⏳ Integrate with RAG pipeline
4. ⏳ Add monitoring/alerting
5. ⏳ Set up S3 backup automation
6. ⏳ Deploy to production

## References

- [Local Development Guide](./CHROMADB_S3_ARCHITECTURE.md)
- [ChromaDB API Reference](https://docs.trychroma.com/reference)
- [RAG System Integration](./ENTITY_SCOPED_RAG_GUIDE.md)
