# Entity-Scoped RAG System Guide

## Overview

The Entity-Scoped RAG System provides **isolated vector stores per entity** with **parallel processing** capabilities for significantly faster access and better scalability compared to a single global FAISS index.

## Key Benefits

### 🚀 Performance

- **10-100x Faster Searches**: Each entity has a small, focused FAISS index
- **Parallel Processing**: Search multiple entities concurrently
- **Memory Efficient**: Only load needed entity indexes
- **Reduced Latency**: No need to search through irrelevant data

### 🔒 Isolation

- **Complete Separation**: Each entity's data is isolated
- **Security**: Entity A cannot access Entity B's documents
- **Independent Scaling**: Add/remove entities without affecting others

### 📈 Scalability

- **Horizontal Scaling**: Add unlimited entities
- **No Performance Degradation**: Adding entities doesn't slow down existing ones
- **Distributed Ready**: Easy to distribute entities across servers

## Architecture

```
data/entity_scoped/
├── entities/
│   ├── company_123/
│   │   ├── vector_store/       # FAISS index for company_123
│   │   │   ├── index.faiss
│   │   │   └── index.pkl
│   │   └── metadata.json
│   ├── company_456/
│   │   ├── vector_store/       # FAISS index for company_456
│   │   │   ├── index.faiss
│   │   │   └── index.pkl
│   │   └── metadata.json
│   └── company_789/
│       ├── vector_store/       # FAISS index for company_789
│       │   ├── index.faiss
│       │   └── index.pkl
│       └── metadata.json
└── storage/                    # Shared JSON storage
    ├── doc_id_name_mapping.json
    ├── entity_mappings.json
    └── chunks.json
```

### Components

1. **EntityVectorStore**: Manages a single entity's isolated FAISS index
2. **EntityRAGManager**: Coordinates multiple entity stores with parallel processing
3. **Thread Pool**: Handles concurrent operations across entities

## Usage

### Basic Operations

#### 1. Index a Document to an Entity

```python
from src.core.rag_system import index_document_entity_scoped

result = index_document_entity_scoped(
    entity_id="company_123",
    file_path="/path/to/annual_report.pdf",
    metadata={"year": 2024, "type": "financial"}
)

print(f"Indexed: {result['doc_id']}")
print(f"Chunks: {result['chunks_count']}")
```

#### 2. Index Multiple Documents in Parallel

```python
from src.core.rag_system import index_documents_parallel

# Index documents for multiple entities at once
results = index_documents_parallel({
    "company_123": [
        "/path/to/doc1.pdf",
        "/path/to/doc2.pdf"
    ],
    "company_456": [
        "/path/to/doc3.pdf"
    ],
    "company_789": [
        "/path/to/doc4.pdf",
        "/path/to/doc5.pdf"
    ]
})

# Process results
for entity_id, docs in results.items():
    print(f"{entity_id}: {len(docs)} documents indexed")
```

#### 3. Search Within an Entity

```python
from src.core.rag_system import search_entity_scoped

# Fast search within a single entity's index
results = search_entity_scoped(
    entity_id="company_123",
    query="financial performance Q4 2024",
    k=5
)

for doc in results:
    print(doc.page_content[:200])
```

#### 4. Search Multiple Entities in Parallel

```python
from src.core.rag_system import search_multiple_entities_parallel

# Search across multiple entities concurrently
results = search_multiple_entities_parallel(
    entity_ids=["company_123", "company_456", "company_789"],
    query="What are the key risks?",
    k=3
)

# Process results per entity
for entity_id, docs in results.items():
    print(f"\n{entity_id}:")
    for doc in docs:
        print(f"  - {doc.page_content[:100]}...")
```

#### 5. Get Entity Statistics

```python
from src.core.rag_system import get_entity_stats, get_all_entity_stats

# Single entity stats
stats = get_entity_stats("company_123")
print(f"Documents: {stats['total_documents']}")
print(f"Chunks: {stats['total_chunks']}")

# All entities stats
all_stats = get_all_entity_stats()
for entity_id, stats in all_stats.items():
    print(f"{entity_id}: {stats['total_documents']} docs")
```

#### 6. Delete a Document from Entity

```python
from src.core.rag_system import delete_document_entity_scoped

success = delete_document_entity_scoped(
    entity_id="company_123",
    doc_id="doc_12345"
)

if success:
    print("Document deleted and vector store rebuilt")
```

## Use Cases

### 1. Multi-Tenant RAG System

Each customer gets their own isolated vector store:

```python
# Index customer documents
index_document_entity_scoped(
    entity_id=f"customer_{customer_id}",
    file_path=uploaded_file_path
)

# Search only within customer's documents
results = search_entity_scoped(
    entity_id=f"customer_{customer_id}",
    query=user_query
)
```

### 2. Company Research Platform

Separate indexes per company:

```python
# Add company filings
companies = ["AAPL", "MSFT", "GOOGL", "AMZN"]
documents = {
    f"company_{ticker}": get_company_filings(ticker)
    for ticker in companies
}

# Index all in parallel
index_documents_parallel(documents)

# Comparative search
results = search_multiple_entities_parallel(
    entity_ids=[f"company_{t}" for t in companies],
    query="AI strategy and investments"
)
```

### 3. Department-Based Document Management

Each department has isolated access:

```python
departments = ["engineering", "sales", "finance", "legal"]

# Index department documents
for dept in departments:
    index_documents_parallel({
        f"dept_{dept}": get_dept_documents(dept)
    })

# Search within department
results = search_entity_scoped(
    entity_id="dept_engineering",
    query="API documentation"
)
```

### 4. Project-Based Knowledge Base

Separate knowledge base per project:

```python
# Index project documents
projects = ["project_alpha", "project_beta", "project_gamma"]

for project_id in projects:
    index_document_entity_scoped(
        entity_id=project_id,
        file_path=f"/projects/{project_id}/specs.pdf"
    )

# Search across all projects
all_results = search_multiple_entities_parallel(
    entity_ids=projects,
    query="deployment requirements"
)
```

## Performance Comparison

### Global Index vs Entity-Scoped

| Metric | Global Index | Entity-Scoped |
|--------|-------------|---------------|
| **Search Time (single entity)** | O(n) where n = all docs | O(m) where m = entity docs |
| **Search Time (10 entities)** | Sequential: 10 * O(n) | Parallel: ~O(m) |
| **Memory Usage** | Always loads all | Loads only needed |
| **Scalability** | Degrades with size | Constant per entity |
| **Isolation** | None | Complete |

### Example Performance

With 100 entities, 1000 documents each:

- **Global Index**:
  - Index size: 100,000 docs
  - Search time: ~500ms
  - Memory: ~10GB

- **Entity-Scoped**:
  - Index size per entity: 1,000 docs
  - Search time per entity: ~5ms (100x faster!)
  - Memory: ~100MB per entity (load on demand)
  - Parallel search 10 entities: ~20ms (vs 5000ms sequential)

## Advanced Features

### 1. Lazy Loading

Entity indexes are loaded on-demand:

```python
# First access loads the index
results1 = search_entity_scoped("company_123", "query")  # Loads index

# Subsequent searches reuse cached index
results2 = search_entity_scoped("company_123", "query2")  # Fast!
```

### 2. Memory Management

Free memory by cleaning up unused entities:

```python
from src.core.entity_scoped_rag import get_entity_rag_manager

manager = get_entity_rag_manager()

# Remove entity from cache (saves memory)
manager.cleanup_entity("company_123")

# Next access will reload from disk
```

### 3. Thread-Safe Operations

All operations are thread-safe:

```python
import threading

def index_for_entity(entity_id, files):
    for file in files:
        index_document_entity_scoped(entity_id, file)

# Multiple threads can work on different entities
threads = [
    threading.Thread(target=index_for_entity, args=(f"entity_{i}", files))
    for i in range(10)
]

for t in threads:
    t.start()
for t in threads:
    t.join()
```

### 4. Document Filtering

Filter by document IDs within an entity:

```python
# Search only specific documents
results = search_entity_scoped(
    entity_id="company_123",
    query="financial data",
    k=5,
    doc_ids=["doc_001", "doc_002", "doc_003"]
)
```

## API Reference

### Core Functions

#### `index_document_entity_scoped(entity_id, file_path, metadata=None)`

Index a single document to an entity's vector store.

**Parameters:**
- `entity_id` (str): Entity identifier
- `file_path` (str): Path to document file
- `metadata` (dict, optional): Additional metadata

**Returns:**
- dict: `{"doc_id": str, "entity_id": str, "chunks_count": int, "is_duplicate": bool}`

---

#### `index_documents_parallel(entity_documents, metadata=None)`

Index multiple documents across entities in parallel.

**Parameters:**
- `entity_documents` (dict): Mapping of `entity_id -> list[file_paths]`
- `metadata` (dict, optional): Metadata for all documents

**Returns:**
- dict: Mapping of `entity_id -> list[doc_info]`

---

#### `search_entity_scoped(entity_id, query, k=5, doc_ids=None)`

Search within a specific entity's vector store.

**Parameters:**
- `entity_id` (str): Entity identifier
- `query` (str): Search query
- `k` (int): Number of results
- `doc_ids` (list[str], optional): Filter by document IDs

**Returns:**
- list[Document]: Matching documents

---

#### `search_multiple_entities_parallel(entity_ids, query, k=5)`

Search across multiple entities in parallel.

**Parameters:**
- `entity_ids` (list[str]): List of entity identifiers
- `query` (str): Search query
- `k` (int): Results per entity

**Returns:**
- dict: Mapping of `entity_id -> list[Document]`

---

#### `get_entity_stats(entity_id)`

Get statistics for an entity's vector store.

**Parameters:**
- `entity_id` (str): Entity identifier

**Returns:**
- dict: `{"entity_id": str, "total_documents": int, "total_chunks": int, "has_vector_store": bool}`

---

#### `get_all_entity_stats()`

Get statistics for all entities.

**Returns:**
- dict: Mapping of `entity_id -> stats_dict`

---

#### `delete_document_entity_scoped(entity_id, doc_id)`

Delete a document from an entity's vector store.

**Parameters:**
- `entity_id` (str): Entity identifier
- `doc_id` (str): Document identifier

**Returns:**
- bool: True if successful

## Best Practices

### 1. Entity Naming

Use clear, consistent entity IDs:

```python
# Good
"company_AAPL"
"customer_12345"
"dept_engineering"
"project_alpha"

# Avoid
"123"
"entity1"
"temp"
```

### 2. Batch Indexing

Use parallel indexing for multiple documents:

```python
# Good - parallel
index_documents_parallel({
    "entity_1": [file1, file2, file3],
    "entity_2": [file4, file5]
})

# Less optimal - sequential
for entity, files in entity_files.items():
    for file in files:
        index_document_entity_scoped(entity, file)
```

### 3. Search Strategy

Choose the right search function:

```python
# Single entity - use entity-scoped
results = search_entity_scoped("company_123", query)

# Multiple entities - use parallel
results = search_multiple_entities_parallel(
    ["company_123", "company_456"],
    query
)

# All entities - consider if you really need this
all_stats = get_all_entity_stats()
all_entities = list(all_stats.keys())
results = search_multiple_entities_parallel(all_entities, query)
```

### 4. Memory Management

For systems with many entities:

```python
from src.core.entity_scoped_rag import get_entity_rag_manager

manager = get_entity_rag_manager()

# After processing an entity, free memory
for entity_id in large_entity_list:
    # Process entity
    search_entity_scoped(entity_id, query)

    # Free memory if not needed soon
    manager.cleanup_entity(entity_id)
```

## Migration Guide

### From Global RAG to Entity-Scoped

```python
# Old: Global RAG
from src.core.rag_system import index_single_document, search_documents

# Index
result = rag.index_single_document(file_path, entity_id, metadata)

# Search
results = rag.search_documents(query, k=5, entity_ids=[entity_id])

# ----------------------------------------

# New: Entity-Scoped RAG
from src.core.rag_system import (
    index_document_entity_scoped,
    search_entity_scoped
)

# Index (now entity-first)
result = index_document_entity_scoped(entity_id, file_path, metadata)

# Search (much faster!)
results = search_entity_scoped(entity_id, query, k=5)
```

## Troubleshooting

### Issue: Entity index not found

**Solution**: The entity hasn't been initialized. Index at least one document first.

```python
# Initialize entity by indexing a document
index_document_entity_scoped(entity_id, file_path)
```

### Issue: Slow parallel searches

**Solution**: Check thread pool size and system resources.

```python
# Check system CPU count
import os
print(f"CPU cores: {os.cpu_count()}")

# Thread pool is auto-configured to CPU count
```

### Issue: Memory usage high

**Solution**: Clean up unused entity stores.

```python
from src.core.entity_scoped_rag import get_entity_rag_manager

manager = get_entity_rag_manager()

# Get all loaded entities
loaded = list(manager._entity_stores.keys())

# Cleanup entities not in active use
for entity_id in loaded:
    if entity_id not in active_entities:
        manager.cleanup_entity(entity_id)
```

## Testing

Run the test suite:

```bash
python test_entity_scoped_rag.py
```

This will test:
- Single document indexing
- Parallel document indexing
- Entity-scoped search
- Parallel multi-entity search
- Entity statistics
- Performance comparisons
- Entity isolation

## Conclusion

The Entity-Scoped RAG System provides significant performance improvements and better isolation compared to a global FAISS index. Use it when:

- You have multiple tenants/customers
- You need fast search within specific entities
- You want complete data isolation
- You need to scale to many entities

For questions or issues, contact:
- Author: Prabhath Chellingi
- Email: prabhathchellingi2003@gmail.com
- GitHub: https://github.com/Prabhath003
