# Agent MCP Client - Quick Reference

## Initialization

```python
from src.core._agents import AgentMCPClient

# Unscoped (all user's KBs)
client = AgentMCPClient()

# Scoped to specific KBs
client = AgentMCPClient(kb_ids=["kb_001", "kb_002"])

# Custom collection
client = AgentMCPClient(collection_name="my_collection")
```

## Knowledge Bases

| Operation | Code |
|-----------|------|
| List all KBs | `client.list_knowledge_bases()` |
| List KBs with filter | `client.list_knowledge_bases({'title': 'My KB'})` |
| Get specific KB | `client.get_knowledge_base('kb_001')` |

## Documents

| Operation | Code |
|-----------|------|
| List docs in KB | `client.list_documents_in_kb('kb_001')` |
| Get all chunks from doc | `client.get_document_chunks('doc_001')` |
| Get doc chunks (ordered) | `client.get_document_chunks('doc_001', ordered=True)` |

## Chunk Queries

| Operation | Code |
|-----------|------|
| Vector search | `client.query_chunks('search text', n_results=10)` |
| Search in specific KB | `client.query_chunks('text', kb_ids=['kb_001'])` |
| Search in specific doc | `client.query_chunks('text', doc_ids=['doc_001'])` |
| Get specific chunk | `client.get_chunk_by_id('chunk_001')` |
| List all KB chunks | `client.list_chunks_in_kb('kb_001', limit=100)` |
| List doc chunks in KB | `client.list_chunks_in_kb('kb_001', doc_id='doc_001')` |
| Batch queries | `client.batch_query_chunks(['q1', 'q2', 'q3'], n_results=5)` |

## Chunk Navigation

| Operation | Code |
|-----------|------|
| Get chunk context | `client.get_chunk_context('chunk_001', context_size=2)` |
| Get previous chunk | `client.get_previous_chunk('chunk_001')` |
| Get next chunk | `client.get_next_chunk('chunk_001')` |

## Scope Management

| Operation | Code |
|-----------|------|
| Update KB scope | `client.set_kb_scope(['kb_001', 'kb_002'])` |
| Update collection | `client.set_collection('new_collection')` |
| Get scope info | `client.get_client_scope_info()` |
| Validate KB in scope | `client.validate_scope('kb_001')` |

## Chunk Info Structure

```python
{
    'chunk_id': str,
    'doc_id': str,
    'content': dict,              # Full content
    'metadata': dict,
    'created_at': str,            # ISO format
    'user_id': str,
    'chunk_order_index': float,   # Position in doc
    'text_preview': str           # 100-char preview
}
```

## KB Info Structure

```python
{
    'kb_id': str,
    'title': str,
    'description': str,
    'doc_ids': list[str],
    'doc_count': int,
    'status': str,                # PENDING, PROCESSING, READY, FAILED
    'size_mb': float,
    'estimated_cost_usd': float,
    'created_at': str,            # ISO format
    'updated_at': str,            # ISO format or None
}
```

## Document Info Structure

```python
{
    'doc_id': str,
    'doc_name': str,
    'content_type': str,          # MIME type
    'doc_size': int,              # Bytes
    'chunk_count': int,
    'uploaded_at': str,           # ISO format
    'chunked': bool,
    'source': str                 # 'upload' or other
}
```

## Chunk Context Response

```python
{
    'current': {...},             # Chunk info dict
    'before': [...],              # List of previous chunks
    'after': [...],               # List of next chunks
    'total_neighbors': int        # Total including current
}
```

## Common Patterns

### RAG with Context
```python
chunk = client.query_chunks(query, n_results=1)[0]
context = client.get_chunk_context(chunk['chunk_id'], context_size=2)
rag_docs = context['before'] + [context['current']] + context['after']
```

### Explore Document
```python
docs = client.list_documents_in_kb('kb_001')
for doc in docs:
    chunks = client.get_document_chunks(doc['doc_id'])
    for chunk in chunks:
        print(f"{chunk['chunk_order_index']}: {chunk['text_preview']}")
```

### Multi-Query Search
```python
queries = ['q1', 'q2', 'q3']
results = client.batch_query_chunks(queries, n_results=5)
all_chunks = [chunk for result in results for chunk in result]
unique_chunks = {c['chunk_id']: c for c in all_chunks}.values()
```

### Iterate Through Document
```python
chunk = client.get_chunk_by_id('chunk_001')
while chunk:
    print(f"Index {chunk['chunk_order_index']}: {chunk['text_preview']}")
    chunk = client.get_next_chunk(chunk['chunk_id'])
```

### Search with Filters
```python
# Single KB
chunks = client.query_chunks(
    'search term',
    kb_ids=['kb_001'],
    n_results=10
)

# Multiple KBs
chunks = client.query_chunks(
    'search term',
    kb_ids=['kb_001', 'kb_002'],
    n_results=10
)

# Specific document
chunks = client.query_chunks(
    'search term',
    doc_ids=['doc_001'],
    n_results=10
)
```

## Return Value Handling

```python
# These return lists (empty if error/not found)
kbs = client.list_knowledge_bases()              # [] on error
docs = client.list_documents_in_kb('kb_001')    # [] on error
chunks = client.query_chunks('text')            # [] on error
chunks = client.get_document_chunks('doc_001')  # [] on error

# These return dict or None
chunk = client.get_chunk_by_id('chunk_001')     # None if not found
chunk = client.get_previous_chunk('chunk_001')  # None if none exists
chunk = client.get_next_chunk('chunk_001')      # None if none exists
context = client.get_chunk_context('chunk_001') # None if not found
kb = client.get_knowledge_base('kb_001')        # None if not found
```

## Error Checking

```python
# Check if results exist
chunks = client.query_chunks('text')
if chunks:
    top = chunks[0]
    print(f"Found: {top['chunk_id']}")
else:
    print("No results")

# Check for None
chunk = client.get_next_chunk('chunk_id')
if chunk:
    print(f"Next: {chunk['chunk_id']}")
else:
    print("This is the last chunk")
```

## Tips

1. **Use context_size > 1** for RAG: `context = client.get_chunk_context(id, context_size=2)`
2. **Batch multiple queries** instead of loops: `batch_query_chunks([q1, q2, q3])`
3. **Scope to specific KBs** for better isolation: `AgentMCPClient(kb_ids=['kb_001'])`
4. **Check chunk_order_index** to understand document position
5. **Use text_preview** for UI display (shortened text)
6. **Handle None returns** when navigating edges (first/last chunk)

## See Also

- Full guide: [AGENT_MCP_CLIENT_GUIDE.md](./AGENT_MCP_CLIENT_GUIDE.md)
- Examples: [agent_mcp_client_example.py](../examples/agent_mcp_client_example.py)
- Source: [src/core/_agents/_mcp_client.py](../src/core/_agents/_mcp_client.py)
