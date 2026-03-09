#!/usr/bin/env python3
"""
ChromaDB Local Development Example

This example demonstrates:
1. Local ChromaDB store initialization
2. Adding documents to collections
3. Querying documents
4. Collection management
"""

import sys
from datetime import datetime, timezone

from src.core.models.core_models import Chunk
from src.infrastructure.storage import get_chromadb_store
from src.log_creator import get_file_logger

logger = get_file_logger()


def example_basic_operations():
    """Example: Basic add and query operations with chunks."""
    print("\n" + "=" * 60)
    print("EXAMPLE 1: Basic Operations with Chunks")
    print("=" * 60)

    # Get singleton store
    store = get_chromadb_store(
        persist_dir="./data/chromadb_example",
        mode="development",
    )

    # Create sample chunks
    chunk_texts = [
        "ChromaDB is a vector database for building AI applications",
        "Vector databases enable semantic search with embeddings",
        "Retrieval Augmented Generation (RAG) combines search with generation",
        "Large Language Models can be enhanced with external knowledge",
        "Embeddings convert text to high-dimensional vectors",
    ]

    kb_id = "kb_example_001"
    doc_id = "doc_example_001"
    chunks = [
        Chunk(
            _id=f"chunk_{i:03d}",
            doc_id=doc_id,
            content={"text": text},
            metadata={"kb_id": kb_id, "source": "ai_concepts"},
            created_at=datetime.now(timezone.utc),
        )
        for i, text in enumerate(chunk_texts)
    ]

    # Add chunks to collection
    print("\n📝 Adding chunks...")
    added_ids = store.add_chunks(
        collection_name="ai_docs",
        chunks=chunks,
    )
    print(f"✓ Added {len(added_ids)} chunks")

    # Get stats
    stats = store.get_collection_stats("ai_docs")
    print(f"✓ Collection stats: {stats}")

    # Query with no filters
    print("\n🔍 Querying all chunks...")
    results = store.query(
        collection_name="ai_docs",
        query_texts=["What is vector search?"],
        n_results=3,
    )

    print("Top results:")
    for i, (chunk, distance) in enumerate(results, 1):
        print(f"  {i}. Chunk ID: {chunk.chunk_id}, Distance: {distance:.4f}")

    # Query filtered by kb_id
    print("\n🔍 Querying filtered by kb_id...")
    results = store.query(
        collection_name="ai_docs",
        query_texts=["embeddings"],
        n_results=2,
        kb_ids=[kb_id],
    )
    print(f"✓ Found {len(results)} results for kb_id={kb_id}")

    # Query filtered by doc_id
    print("\n🔍 Querying filtered by doc_id...")
    results = store.query(
        collection_name="ai_docs",
        query_texts=["database"],
        n_results=2,
        doc_ids=[doc_id],
    )
    print(f"✓ Found {len(results)} results for doc_id={doc_id}")

    return store


def example_multiple_collections():
    """Example: Multiple collections for different data types."""
    print("\n" + "=" * 60)
    print("EXAMPLE 2: Multiple Collections with Chunks")
    print("=" * 60)

    store = get_chromadb_store(
        persist_dir="./data/chromadb_example",
        mode="development",
    )

    kb_id = "kb_multi_collections"

    # Create collections for different purposes
    print("\n📚 Creating collections...")

    # Documents collection
    store.get_or_create_collection(
        "documents",
        metadata={"type": "raw_text", "purpose": "primary_retrieval"},
    )

    # Summaries collection
    store.get_or_create_collection(
        "summaries",
        metadata={"type": "summary", "purpose": "quick_overview"},
    )

    # Entities collection
    store.get_or_create_collection(
        "entities",
        metadata={"type": "entity", "purpose": "knowledge_base"},
    )

    # Add data to documents collection
    doc_chunks = [
        Chunk(
            _id="doc_chunk_001",
            doc_id="doc_rag_001",
            content={"text": "This is a detailed document about RAG systems"},
            metadata={"kb_id": kb_id},
        ),
        Chunk(
            _id="doc_chunk_002",
            doc_id="doc_chroma_001",
            content={"text": "ChromaDB provides persistent vector storage"},
            metadata={"kb_id": kb_id},
        ),
    ]
    store.add_chunks("documents", doc_chunks)

    # Add data to summaries collection
    summary_chunks = [
        Chunk(
            _id="sum_chunk_001",
            doc_id="doc_rag_001",
            content={"text": "RAG enhances LLMs with retrieval"},
            metadata={"kb_id": kb_id},
        ),
        Chunk(
            _id="sum_chunk_002",
            doc_id="doc_chroma_001",
            content={"text": "ChromaDB = Vector DB"},
            metadata={"kb_id": kb_id},
        ),
    ]
    store.add_chunks("summaries", summary_chunks)

    # Add data to entities collection
    entity_chunks = [
        Chunk(
            _id="ent_chunk_001",
            doc_id="doc_entities_001",
            content={"text": "RAG"},
            metadata={"kb_id": kb_id},
        ),
        Chunk(
            _id="ent_chunk_002",
            doc_id="doc_entities_001",
            content={"text": "ChromaDB"},
            metadata={"kb_id": kb_id},
        ),
        Chunk(
            _id="ent_chunk_003",
            doc_id="doc_entities_001",
            content={"text": "Vector DB"},
            metadata={"kb_id": kb_id},
        ),
    ]
    store.add_chunks("entities", entity_chunks)

    # List and show stats
    print("\n📊 Collections and stats:")
    for coll_name in store.list_collections():
        stats = store.get_collection_stats(coll_name)
        print(f"  {coll_name}: {stats['document_count']} documents")

    return store


def example_metadata_filtering():
    """Example: Adding and querying chunks with metadata filtering."""
    print("\n" + "=" * 60)
    print("EXAMPLE 3: Metadata Filtering")
    print("=" * 60)

    store = get_chromadb_store(
        persist_dir="./data/chromadb_example",
        mode="development",
    )

    kb_id = "kb_technical_docs"

    # Create chunks with rich metadata
    chunks = [
        Chunk(
            _id="tech_chunk_001",
            doc_id="doc_chroma_001",
            content={"text": "ChromaDB Documentation"},
            metadata={
                "kb_id": kb_id,
                "source": "docs.trychroma.com",
                "category": "chromadb",
                "year": 2024,
            },
        ),
        Chunk(
            _id="tech_chunk_002",
            doc_id="doc_postgres_001",
            content={"text": "PostgreSQL Vector Extensions"},
            metadata={
                "kb_id": kb_id,
                "source": "postgresql.org",
                "category": "database",
                "year": 2024,
            },
        ),
        Chunk(
            _id="tech_chunk_003",
            doc_id="doc_opensearch_001",
            content={"text": "OpenSearch Vector Search"},
            metadata={
                "kb_id": kb_id,
                "source": "opensearch.org",
                "category": "search",
                "year": 2024,
            },
        ),
    ]

    print("\n📝 Adding chunks with metadata...")
    store.add_chunks("technical_docs", chunks)

    # Query with custom where filter
    print("\n🔍 Querying with category filter...")
    results = store.query(
        "technical_docs",
        query_texts=["vector database"],
        n_results=5,
        where={"category": "chromadb"},
    )

    print(f"Results filtered by category='chromadb':")
    for i, (chunk, distance) in enumerate(results, 1):
        print(f"  {i}. {chunk.chunk_id} (distance: {distance:.4f})")

    # Query with kb_id filter
    print("\n🔍 Querying with kb_id filter...")
    results = store.query(
        "technical_docs",
        query_texts=["vector"],
        n_results=5,
        kb_ids=[kb_id],
    )

    print(f"Results filtered by kb_id={kb_id}:")
    for i, (chunk, distance) in enumerate(results, 1):
        print(f"  {i}. {chunk.chunk_id} (distance: {distance:.4f})")


def example_transaction():
    """Example: Batch operations with transactions."""
    print("\n" + "=" * 60)
    print("EXAMPLE 4: Batch Operations with Transactions")
    print("=" * 60)

    store = get_chromadb_store(
        persist_dir="./data/chromadb_example",
        mode="development",
    )

    kb_id = "kb_batch_example"

    print("\n📝 Adding chunks in batches with transaction...")
    with store.transaction():
        for batch_num in range(1, 4):
            # Create batch of chunks
            batch_chunks = [
                Chunk(
                    _id=f"batch_{batch_num}_chunk_{i}",
                    doc_id=f"doc_batch_{batch_num}",
                    content={"text": f"Document {chr(64+batch_num)}{i}"},
                    metadata={"kb_id": kb_id, "batch": batch_num},
                )
                for i in range(1, 4)
            ]
            store.add_chunks("batch_data", batch_chunks)
            print(f"  ✓ Batch {batch_num} added (3 chunks)")

    stats = store.get_collection_stats("batch_data")
    print(f"✓ Total chunks: {stats['document_count']}")


def example_delete_chunks():
    """Example: Selective chunk deletion."""
    print("\n" + "=" * 60)
    print("EXAMPLE 5: Selective Chunk Deletion")
    print("=" * 60)

    store = get_chromadb_store(
        persist_dir="./data/chromadb_example",
        mode="development",
    )

    kb_id = "kb_deletion_example"
    doc_id_1 = "doc_001"
    doc_id_2 = "doc_002"

    # Create chunks in a test collection
    chunks = [
        Chunk(
            _id=f"chunk_{i}",
            doc_id=doc_id_1,
            content={"text": f"Content from document 1, chunk {i}"},
            metadata={"kb_id": kb_id},
        )
        for i in range(1, 4)
    ]
    chunks.extend(
        [
            Chunk(
                _id=f"chunk_{i}",
                doc_id=doc_id_2,
                content={"text": f"Content from document 2, chunk {i}"},
                metadata={"kb_id": kb_id},
            )
            for i in range(4, 6)
        ]
    )

    print("\n📝 Adding test chunks...")
    store.add_chunks("deletion_test", chunks)
    stats = store.get_collection_stats("deletion_test")
    print(f"✓ Added {stats['document_count']} chunks")

    # Delete specific chunks by chunk_id
    print("\n🗑️ Deleting by chunk_id...")
    deleted = store.delete_chunks("deletion_test", chunk_ids=["chunk_1", "chunk_2"])
    print(f"✓ Deleted {deleted} chunks by chunk_id")
    stats = store.get_collection_stats("deletion_test")
    print(f"  Remaining: {stats['document_count']} chunks")

    # Delete all chunks from specific document
    print("\n🗑️ Deleting by doc_id...")
    deleted = store.delete_chunks("deletion_test", doc_ids=[doc_id_2])
    print(f"✓ Deleted {deleted} chunks from doc_id={doc_id_2}")
    stats = store.get_collection_stats("deletion_test")
    print(f"  Remaining: {stats['document_count']} chunks")

    # Delete remaining by kb_id
    print("\n🗑️ Deleting by kb_id...")
    deleted = store.delete_chunks("deletion_test", kb_ids=[kb_id])
    print(f"✓ Deleted {deleted} chunks with kb_id={kb_id}")
    stats = store.get_collection_stats("deletion_test")
    print(f"  Remaining: {stats['document_count']} chunks")


def example_chunk_navigation():
    """Example: Navigate between chunks in a document."""
    print("\n" + "=" * 60)
    print("EXAMPLE 6: Chunk Navigation (Previous/Next)")
    print("=" * 60)

    store = get_chromadb_store(
        persist_dir="./data/chromadb_example",
        mode="development",
    )

    kb_id = "kb_navigation_example"
    doc_id = "doc_narrative_001"

    # Create a sequence of chunks (like chapters in a document)
    chunks = [
        Chunk(
            _id=f"chapter_{i}",
            doc_id=doc_id,
            content={"text": f"Chapter {i}: Introduction to topic {i}"},
            metadata={"kb_id": kb_id, "chapter": i},
            created_at=datetime.now(timezone.utc),
        )
        for i in range(1, 6)  # 5 chapters
    ]

    print("\n📚 Adding sequential chunks (chapters)...")
    store.add_chunks("narrative_collection", chunks)
    print(f"✓ Added {len(chunks)} chunks")

    # Navigate through chapters
    print("\n🔄 Navigating through chunks...")
    current_chunk_id = "chapter_3"
    print(f"\n📖 Current: {current_chunk_id}")

    # Get previous chunk
    prev = store.get_previous_chunk("narrative_collection", current_chunk_id)
    if prev:
        print(f"  ⬅️ Previous: {prev.chunk_id}")
        print(f"     Content: {prev.content["text"][:50]}...")
    else:
        print(f"  ⬅️ Previous: None (first chunk)")

    # Get next chunk
    next_chunk = store.get_next_chunk("narrative_collection", current_chunk_id)
    if next_chunk:
        print(f"  ➡️ Next: {next_chunk.chunk_id}")
        print(f"     Content: {next_chunk.content["text"][:50]}...")
    else:
        print(f"  ➡️ Next: None (last chunk)")

    # Navigate from first chapter
    print(f"\n📖 From first chapter...")
    first_prev = store.get_previous_chunk("narrative_collection", "chapter_1")
    first_next = store.get_next_chunk("narrative_collection", "chapter_1")
    print(f"  ⬅️ Previous: {first_prev.chunk_id if first_prev else 'None'}")
    print(f"  ➡️ Next: {first_next.chunk_id if first_next else 'None'}")

    # Navigate from last chapter
    print(f"\n📖 From last chapter...")
    last_prev = store.get_previous_chunk("narrative_collection", "chapter_5")
    last_next = store.get_next_chunk("narrative_collection", "chapter_5")
    print(f"  ⬅️ Previous: {last_prev.chunk_id if last_prev else 'None'}")
    print(f"  ➡️ Next: {last_next.chunk_id if last_next else 'None'}")

    # Cleanup
    store.delete_collection("narrative_collection")
    print("\n✓ Navigation example complete")


def example_cleanup():
    """Example: Cleanup collections."""
    print("\n" + "=" * 60)
    print("EXAMPLE 8: Collection Management")
    print("=" * 60)

    store = get_chromadb_store(
        persist_dir="./data/chromadb_example",
        mode="development",
    )

    # List all collections
    print("\n📋 All collections:")
    collections = store.list_collections()
    for coll in collections:
        print(f"  - {coll}")

    # Clean up
    print("\n🧹 Cleaning up example collections...")
    for coll in [
        "ai_docs",
        "documents",
        "summaries",
        "entities",
        "technical_docs",
        "batch_data",
        "deletion_test",
        "narrative_collection",
        "context_collection",
    ]:
        try:
            store.delete_collection(coll)
            print(f"  ✓ Deleted {coll}")
        except Exception as e:
            print(f"  ℹ {coll} not found: {e}")

    print("\n✓ Cleanup complete")


def main():
    """Run all examples."""
    print("\n" + "=" * 60)
    print("ChromaDB Development Examples")
    print("=" * 60)

    try:
        # Run examples
        _ = example_basic_operations()
        example_multiple_collections()
        example_metadata_filtering()
        example_transaction()
        example_delete_chunks()
        example_chunk_navigation()
        example_cleanup()

        print("\n" + "=" * 60)
        print("✓ All examples completed successfully!")
        print("=" * 60)
        print("\nNext steps:")
        print("  1. Check data/chromadb_example/ for stored data")
        print("  2. Read CHROMADB_DEVELOPMENT_GUIDE.md for more details")
        print("  3. Check CHROMADB_S3_ARCHITECTURE.md for production setup")

    except Exception as e:
        logger.error(f"Example failed: {e}", exc_info=True)
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
