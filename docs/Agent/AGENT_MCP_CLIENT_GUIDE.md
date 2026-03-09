# Agent MCP Client Guide

## Overview

The `AgentMCPClient` provides a scoped interface for agents to interact with ChromaDB chunk storage and knowledge base management. It handles:

- **Knowledge Base Operations**: List, search, and manage KBs
- **Document Management**: Access documents within KBs
- **Chunk Navigation**: Query, navigate, and get context around chunks
- **Batch Operations**: Efficient multi-query processing
- **Scope Management**: KB-scoped access control

## Quick Start

### Basic Initialization

```python
from src.core._agents import AgentMCPClient

# Unscoped client - can access all user's KBs
client = AgentMCPClient()

# Scoped client - limited to specific KBs
client = AgentMCPClient(kb_ids=["kb_001", "kb_002"])
```

### List Knowledge Bases

```python
# Get all KBs for the user
kbs = client.list_knowledge_bases()

for kb in kbs:
    print(f"{kb['kb_id']}: {kb['title']} ({kb['doc_count']} docs)")
```

### List Documents in KB

```python
# List all documents in a KB
docs = client.list_documents_in_kb("kb_001")

for doc in docs:
    print(f"{doc['doc_name']}: {doc['chunk_count']} chunks")
```

### Query Chunks

```python
# Vector similarity search
chunks = client.query_chunks(
    "machine learning algorithms",
    n_results=10
)

for chunk in chunks:
    print(f"{chunk['chunk_id']}: {chunk['text_preview']}")
```

### Navigate Between Chunks

```python
# Get a chunk
chunk = client.get_chunk_by_id("chunk_123")

# Get surrounding context
context = client.get_chunk_context("chunk_123", context_size=2)
# Returns: {
#   'current': {...},
#   'before': [...],
#   'after': [...]
# }

# Navigate to previous/next
prev = client.get_previous_chunk("chunk_123")
next_chunk = client.get_next_chunk("chunk_123")
```

## Complete API Reference

### Initialization

```python
AgentMCPClient(
    collection_name: str = Config.CHUNKS_COLLECTION,
    kb_ids: Optional[List[str]] = None,
    user_id: Optional[str] = None
)
```

**Parameters:**
- `collection_name`: ChromaDB collection to use (default: CHUNKS_COLLECTION)
- `kb_ids`: List of KB IDs to scope access to (if None, all user's KBs)
- `user_id`: User ID for scoped access (default: current operation user)

### Scope Management

```python
# Update KB scope
client.set_kb_scope(["kb_001", "kb_002"])

# Update collection
client.set_collection("my_collection")

# Get scope info
info = client.get_client_scope_info()
# Returns: {
#   'collection_name': 'chunks',
#   'kb_ids': ['kb_001'],
#   'kb_ids_count': 1,
#   'user_id': 'user_123'
# }

# Validate if KB is in scope
is_in_scope = client.validate_scope("kb_001")
```

### Knowledge Base Operations

#### list_knowledge_bases(filters=None)
Get all knowledge bases.

**Parameters:**
- `filters`: Optional MongoDB-style filters (e.g., `{'title': 'My KB'}`)

**Returns:** List of KB info dicts

**Example:**
```python
kbs = client.list_knowledge_bases()
# [{
#   'kb_id': 'kb_001',
#   'title': 'My Knowledge Base',
#   'description': '...',
#   'doc_count': 5,
#   'doc_ids': [...],
#   'status': 'READY',
#   'created_at': '2024-01-01T...',
#   'updated_at': None,
# }, ...]
```

#### get_knowledge_base(kb_id)
Get details for a specific KB.

**Parameters:**
- `kb_id`: Knowledge base ID

**Returns:** KB info dict or None

### Document Operations

#### list_documents_in_kb(kb_id)
List all documents in a KB.

**Parameters:**
- `kb_id`: Knowledge base ID

**Returns:** List of document info dicts

**Example:**
```python
docs = client.list_documents_in_kb("kb_001")
# [{
#   'doc_id': 'doc_001',
#   'doc_name': 'research.pdf',
#   'content_type': 'application/pdf',
#   'doc_size': 2048000,
#   'chunk_count': 42,
#   'uploaded_at': '2024-01-01T...',
#   'chunked': True,
#   'source': 'upload'
# }, ...]
```

### Chunk Query Operations

#### query_chunks(query_text, kb_ids=None, doc_ids=None, n_results=10)
Search chunks using vector similarity.

**Parameters:**
- `query_text`: Search query string
- `kb_ids`: KB IDs to search (default: client's kb_ids)
- `doc_ids`: Optional document IDs to limit search
- `n_results`: Number of results (default: 10)

**Returns:** List of chunk info dicts sorted by relevance

**Example:**
```python
chunks = client.query_chunks(
    "neural network training",
    kb_ids=["kb_001"],
    n_results=5
)
```

#### list_chunks_in_kb(kb_id, doc_id=None, limit=100)
List chunks in a KB with optional document filter.

**Parameters:**
- `kb_id`: Knowledge base ID
- `doc_id`: Optional document ID for specific document
- `limit`: Maximum chunks to return (default: 100)

**Returns:** List of chunk info dicts

#### get_chunk_by_id(chunk_id)
Get a specific chunk by ID.

**Parameters:**
- `chunk_id`: Chunk ID

**Returns:** Chunk info dict or None

### Chunk Navigation

#### get_chunk_context(chunk_id, context_size=1)
Get a chunk with surrounding context (previous/next chunks).

**Parameters:**
- `chunk_id`: Chunk ID
- `context_size`: Number of chunks before/after (default: 1)

**Returns:** Dict with structure:
```python
{
  'current': {...},           # Current chunk
  'before': [...],            # List of previous chunks
  'after': [...],             # List of next chunks
  'total_neighbors': 3        # Total including current
}
```

#### get_previous_chunk(chunk_id)
Get the previous chunk in document sequence.

**Parameters:**
- `chunk_id`: Chunk ID

**Returns:** Chunk info dict or None

#### get_next_chunk(chunk_id)
Get the next chunk in document sequence.

**Parameters:**
- `chunk_id`: Chunk ID

**Returns:** Chunk info dict or None

#### get_document_chunks(doc_id, ordered=True)
Get all chunks for a document.

**Parameters:**
- `doc_id`: Document ID
- `ordered`: Whether to return in sequence order (default: True)

**Returns:** List of chunk info dicts

### Batch Operations

#### batch_query_chunks(queries, kb_ids=None, n_results=5)
Run multiple chunk queries efficiently.

**Parameters:**
- `queries`: List of search query strings
- `kb_ids`: KB IDs to search (default: client's kb_ids)
- `n_results`: Results per query (default: 5)

**Returns:** List of result lists (one per query)

**Example:**
```python
results = client.batch_query_chunks([
    "machine learning",
    "neural networks",
    "deep learning"
], n_results=3)

# results[0] = chunks for "machine learning"
# results[1] = chunks for "neural networks"
# results[2] = chunks for "deep learning"
```

## Integration with Main Agent

### Example: Agent with RAG Context

```python
from src.core._agents import AgentMCPClient

class MyAgent:
    def __init__(self, kb_ids: List[str]):
        # Initialize MCP client scoped to specific KBs
        self.mcp_client = AgentMCPClient(kb_ids=kb_ids)

    def process_query(self, query: str):
        # Search for relevant chunks
        chunks = self.mcp_client.query_chunks(query, n_results=5)

        # Get context around top chunk
        if chunks:
            top_chunk = chunks[0]
            context = self.mcp_client.get_chunk_context(
                top_chunk['chunk_id'],
                context_size=2
            )

            # Build RAG context
            rag_context = {
                'query': query,
                'current_chunk': context['current'],
                'previous_chunk': context['before'][0] if context['before'] else None,
                'next_chunk': context['after'][0] if context['after'] else None,
                'all_results': chunks
            }

            return rag_context
```

### Example: Multi-KB Agent

```python
# Create client that can access multiple KBs
client = AgentMCPClient(kb_ids=["kb_001", "kb_002", "kb_003"])

# Switch KB scope dynamically
client.set_kb_scope(["kb_002"])

# All subsequent queries use new scope
chunks = client.query_chunks("search term")
```

### Example: KB and Document Explorer

```python
def explore_kb(kb_id: str):
    client = AgentMCPClient()

    # Get KB details
    kb = client.get_knowledge_base(kb_id)
    print(f"KB: {kb['title']}")

    # List all documents
    docs = client.list_documents_in_kb(kb_id)
    for doc in docs:
        print(f"  - {doc['doc_name']}: {doc['chunk_count']} chunks")

        # Get chunks from each document
        chunks = client.get_document_chunks(doc['doc_id'])
        for chunk in chunks[:3]:
            print(f"    - {chunk['chunk_id']}: {chunk['text_preview']}")
```

## Chunk Info Structure

All chunk operations return chunk info dicts with:

```python
{
    'chunk_id': 'chunk_abc123',
    'doc_id': 'doc_xyz',
    'content': {...},              # Full chunk content
    'metadata': {...},             # Chunk metadata
    'created_at': '2024-01-01T...',
    'user_id': 'user_123',
    'chunk_order_index': 0,        # Sequential position in document
    'text_preview': 'First 100 chars...'  # Text preview for UI
}
```

## Best Practices

### 1. Use Scoped Clients
```python
# Good: Scoped to specific KBs
client = AgentMCPClient(kb_ids=["kb_001"])

# Less ideal: Unscoped access to all KBs
client = AgentMCPClient()
```

### 2. Validate Scope
```python
if client.validate_scope("kb_001"):
    chunks = client.query_chunks("search term")
```

### 3. Use Context for RAG
```python
# Instead of just returning the chunk:
# Get surrounding context for better RAG
context = client.get_chunk_context(chunk_id, context_size=2)
rag_context = context['before'] + [context['current']] + context['after']
```

### 4. Batch Multiple Queries
```python
# Better performance than individual queries
results = client.batch_query_chunks([
    "query1",
    "query2",
    "query3"
])
```

### 5. Handle None Returns
```python
# Check for None before using
next_chunk = client.get_next_chunk(chunk_id)
if next_chunk:
    print(f"Next: {next_chunk['chunk_id']}")
else:
    print("No next chunk")
```

## Configuration

The client uses default configuration from `src/config/Config`:

```python
Config.CHUNKS_COLLECTION  # Default collection name
Config.KNOWLEDGE_BASES_COLLECTION
Config.DOCUMENTS_COLLECTION
Config.DATA_DIR
```

Override during initialization if needed:

```python
client = AgentMCPClient(collection_name="custom_collection")
```

## Error Handling

The client logs errors but returns empty lists or None on failure:

```python
chunks = client.query_chunks("query")
# Returns [] if error

chunk = client.get_chunk_by_id("chunk_id")
# Returns None if error or not found
```

Check logs for detailed error information:

```python
from src.log_creator import get_file_logger
logger = get_file_logger()
# Check logs for "AgentMCPClient" entries
```

## Performance Considerations

- **Vector Search**: Typically 50-200ms per query depending on index size
- **Chunk Navigation**: Usually <10ms per operation (local lookups)
- **Batch Queries**: More efficient than individual queries (amortized cost)
- **Large Result Sets**: Use `limit` parameter to avoid excessive data transfer

## Thread Safety

The client is thread-safe:

```python
from concurrent.futures import ThreadPoolExecutor

client = AgentMCPClient(kb_ids=["kb_001"])

def query_worker(query):
    return client.query_chunks(query)

with ThreadPoolExecutor(max_workers=4) as executor:
    results = executor.map(query_worker, queries)
```

## See Also

- [ChromaDB Store Documentation](./CHROMADB_DEVELOPMENT_GUIDE.md)
- [Knowledge Base Manager](../src/core/_management/)
- [Examples](../examples/agent_mcp_client_example.py)
