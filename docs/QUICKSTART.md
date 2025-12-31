# Quick Start Guide: Entity-Scoped RAG

## 🚀 Get Started in 5 Minutes

### 1. Import the Functions

```python
from src.core.rag_system import (
    index_document_entity_scoped,
    search_entity_scoped,
    index_documents_parallel,
    search_multiple_entities_parallel
)
```

### 2. Index a Document

```python
# Index a document for a specific entity
result = index_document_entity_scoped(
    entity_id="company_123",
    file_path="/path/to/annual_report.pdf"
)

print(f"✓ Indexed: {result['doc_id']}")
print(f"  Chunks: {result['chunks_count']}")
```

### 3. Search

```python
# Fast search within the entity's isolated index
results = search_entity_scoped(
    entity_id="company_123",
    query="What was the revenue in Q4?",
    k=5
)

for doc in results:
    print(doc.page_content)
```

## 🔥 Power Features

### Parallel Indexing

```python
# Index documents for multiple entities at once
results = index_documents_parallel({
    "company_123": ["/docs/report1.pdf", "/docs/report2.pdf"],
    "company_456": ["/docs/report3.pdf"],
    "company_789": ["/docs/report4.pdf"]
})

# Check results
for entity_id, docs in results.items():
    print(f"{entity_id}: {len(docs)} documents indexed")
```

### Parallel Search

```python
# Search across multiple entities concurrently
results = search_multiple_entities_parallel(
    entity_ids=["company_123", "company_456", "company_789"],
    query="What are the key risks?",
    k=3
)

# Process results
for entity_id, docs in results.items():
    print(f"\n{entity_id}:")
    for doc in docs:
        print(f"  - {doc.page_content[:100]}...")
```

## 📊 Get Statistics

```python
from src.core.rag_system import get_entity_stats, get_all_entity_stats

# Single entity
stats = get_entity_stats("company_123")
print(f"Documents: {stats['total_documents']}")
print(f"Chunks: {stats['total_chunks']}")

# All entities
all_stats = get_all_entity_stats()
for entity_id, stats in all_stats.items():
    print(f"{entity_id}: {stats['total_documents']} docs")
```

## 🎯 Common Use Cases

### Multi-Tenant SaaS

```python
# Each customer gets isolated storage
customer_id = "customer_12345"

# Index customer's documents
index_document_entity_scoped(
    entity_id=f"customer_{customer_id}",
    file_path=uploaded_file_path
)

# Search only within customer's data
results = search_entity_scoped(
    entity_id=f"customer_{customer_id}",
    query=user_question
)
```

### Company Research

```python
# Index multiple companies in parallel
companies = ["AAPL", "MSFT", "GOOGL"]
docs = {
    f"company_{ticker}": get_filings(ticker)
    for ticker in companies
}
index_documents_parallel(docs)

# Compare across companies
results = search_multiple_entities_parallel(
    entity_ids=[f"company_{t}" for t in companies],
    query="AI investments"
)
```

### Department Documents

```python
# Separate index per department
departments = ["engineering", "sales", "finance"]

for dept in departments:
    index_document_entity_scoped(
        entity_id=f"dept_{dept}",
        file_path=f"/departments/{dept}/handbook.pdf"
    )

# Search within department
results = search_entity_scoped(
    entity_id="dept_engineering",
    query="deployment process"
)
```

## ⚡ Performance Tips

### 1. Batch Operations

```python
# ✅ Good - parallel
index_documents_parallel({
    "entity_1": [file1, file2, file3],
    "entity_2": [file4, file5]
})

# ❌ Slower - sequential
for entity_id, files in entities.items():
    for file in files:
        index_document_entity_scoped(entity_id, file)
```

### 2. Entity-Scoped Search

```python
# ✅ Fast - searches only entity's small index
search_entity_scoped("company_123", query)

# ❌ Slower - searches global index with filter
rag.search_documents(query, entity_ids=["company_123"])
```

### 3. Parallel Multi-Entity Search

```python
# ✅ Fast - parallel across entities
search_multiple_entities_parallel(
    ["entity_1", "entity_2", "entity_3"],
    query
)

# ❌ Slower - sequential
results = []
for entity in entities:
    results.extend(search_entity_scoped(entity, query))
```

## 🧪 Run Tests

```bash
# Test JSON storage
python test_json_storage.py

# Test entity-scoped RAG
python test_entity_scoped_rag.py
```

## 📚 Full Documentation

- **[ENTITY_SCOPED_RAG_GUIDE.md](ENTITY_SCOPED_RAG_GUIDE.md)** - Complete guide with examples
- **[MIGRATION_TO_JSON_STORAGE.md](MIGRATION_TO_JSON_STORAGE.md)** - JSON storage details
- **[IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)** - Technical overview

## 🆘 Troubleshooting

### Issue: No results found

```python
# Check if entity has documents
stats = get_entity_stats("my_entity")
print(stats)

# Make sure you indexed documents first
index_document_entity_scoped("my_entity", "/path/to/doc.pdf")
```

### Issue: Slow performance

```python
# Use entity-scoped search (much faster)
search_entity_scoped("entity_id", query)  # Fast!

# Use parallel for multiple entities
search_multiple_entities_parallel(entity_ids, query)  # Even faster!
```

## 💡 Key Benefits

| Feature | Benefit |
|---------|---------|
| **Isolated Indexes** | 10-100x faster search per entity |
| **Parallel Processing** | Process multiple entities concurrently |
| **Complete Isolation** | No cross-entity data leakage |
| **Scalable** | Add entities without performance impact |
| **Memory Efficient** | Load only needed indexes |
| **No Database** | JSON storage with atomic writes |

## 🎓 Next Steps

1. Index your first documents using `index_document_entity_scoped`
2. Try parallel indexing with `index_documents_parallel`
3. Search with `search_entity_scoped` for fast results
4. Compare across entities with `search_multiple_entities_parallel`
5. Check stats with `get_entity_stats`

---

**Happy RAG-ing! 🚀**

Contact: prabhathchellingi2003@gmail.com
