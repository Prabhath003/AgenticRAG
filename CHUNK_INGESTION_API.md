# Chunk Ingestion API Documentation

## Overview

The Chunk Ingestion API allows clients to directly submit pre-chunked data to the system with automatic duplicate detection. This bypasses the file processor service and indexes chunks directly into the entity's vector store, following the exact same indexing process as regular document uploads.

## Features

- **Duplicate Detection**: Uses `chunk_id` to detect and skip already-indexed chunks
- **Batch Ingestion**: Submit multiple chunks in a single request
- **Single Chunk Ingestion**: Submit individual chunks
- **Full Indexing**: Chunks are indexed into FAISS vector store with proper metadata persistence
- **Document Tracking**: Automatically creates document entries and tracks chunk counts

## API Endpoints

### 1. Single Chunk Ingestion

**Endpoint**: `POST /api/entities/{entity_id}/chunks`

**Request Body**:
```json
{
  "chunk_id": "unique_chunk_identifier",
  "markdown": {
    "text": "Markdown content of the chunk",
    "chunk_order_index": 0,
    "source": "source_identifier",
    "filename": "original_filename.pdf",
    "pages": [1, 2, 3]
  },
  "metadata": {
    "chunk_index": 0,
    "tokens": 100,
    "processed_by": "FileProcessor",
    "doc_id": "document_id",
    "entity_id": "entity_id"
  }
}
```

**Response**:
```json
{
  "success": true,
  "chunk_id": "unique_chunk_identifier",
  "entity_id": "entity_id",
  "doc_id": "document_id",
  "indexed": true,
  "message": "Chunk successfully indexed"
}
```

### 2. Batch Chunk Ingestion

**Endpoint**: `POST /api/entities/{entity_id}/chunks/batch`

**Request Body**:
```json
{
  "chunks": [
    {
      "chunk_id": "chunk_1",
      "markdown": {...},
      "metadata": {...}
    },
    {
      "chunk_id": "chunk_2",
      "markdown": {...},
      "metadata": {...}
    }
  ]
}
```

**Response**:
```json
{
  "success": true,
  "entity_id": "entity_id",
  "doc_id": "document_id",
  "total_chunks": 10,
  "indexed_chunks": 8,
  "duplicate_chunks": 2,
  "message": "Ingested 8 chunks (2 duplicates skipped) for doc document_id"
}
```

## Implementation Details

### Indexing Process

1. **Duplicate Detection**: Check if `chunk_id` exists in entity storage
2. **Vector Store**: Add chunks to FAISS vector store using sentence-transformers embeddings
3. **Vector Store Save**: Persist vector store to disk
4. **Metadata Storage**: Save document entry and chunk metadata to entity-specific JSON storage

### Key Classes

- **ChunkCreate**: Pydantic model for single chunk validation
- **ChunkBatchIngestRequest**: Pydantic model for batch chunk validation
- **Manager.ingest_chunk()**: Single chunk ingestion wrapper
- **Manager.ingest_chunks()**: Batch chunk ingestion with duplicate detection
- **EntityVectorStore.add_chunks_batch()**: Vector store indexing
- **EntityVectorStore._save_chunks_metadata()**: Metadata persistence

### Storage Structure

Chunks are stored in entity-specific directories with the following structure:

```
data/entities/{entity_id}/
├── vector_store.faiss          # FAISS vector store index
├── vector_store_metadata.pkl   # Vector store embeddings
└── JSONStorage (collections)
    ├── documents/              # Document entries
    ├── chunks/                 # Chunk metadata and content
    └── ...
```

## Usage Examples

### Python Example

```python
import requests

entity_id = "company_123"
chunks = [
    {
        "chunk_id": "chunk_1",
        "markdown": {
            "text": "Company information...",
            "chunk_order_index": 0,
            "source": "company_123",
            "filename": "report.pdf",
            "pages": [1]
        },
        "metadata": {
            "chunk_index": 0,
            "tokens": 150,
            "processed_by": "ChunkAPI",
            "doc_id": "doc_123",
            "entity_id": entity_id
        }
    }
]

# Single chunk
response = requests.post(
    f"http://localhost:8002/api/entities/{entity_id}/chunks",
    json=chunks[0]
)

# Batch chunks
response = requests.post(
    f"http://localhost:8002/api/entities/{entity_id}/chunks/batch",
    json={"chunks": chunks}
)
```

## Important Notes

1. **Client Manages chunk_id**: The client is responsible for ensuring `chunk_id` uniqueness
2. **Same doc_id**: All chunks in a batch must have the same `doc_id`
3. **Duplicate Handling**: Duplicate chunks are silently skipped (not an error)
4. **No Re-processing**: Unlike file uploads, chunks bypass the file processor service
5. **Immediate Indexing**: Chunks are indexed synchronously (no background processing)

## Performance Characteristics

- **Single Chunk**: ~50-200ms (includes embeddings generation)
- **Batch Chunks**: Linear scaling with chunk count (~10-50ms per chunk)
- **Duplicate Detection**: O(1) lookup time

## Testing

Run the included test scripts:

```bash
# Test single chunk endpoint
python test_chunk_endpoint.py

# Test batch chunk endpoint
python test_chunk_batch_endpoint.py
```
