# -----------------------------------------------------------------------------
# Copyright (c) 2025 Backend
# All rights reserved.
#
# Developed by: 
# Author: Prabhath Chellingi
# GitHub: https://github.com/Prabhath003
# Contact: prabhathchellingi2003@gmail.com
#
# This source code is licensed under the MIT License found in the LICENSE file
# in the root directory of this source tree.
# -----------------------------------------------------------------------------

"""
Tests for chunk traversal functions in EntityVectorStore and EntityRAGManager
"""

import os
import tempfile
import pytest
from pathlib import Path

from src.core.entity_scoped_rag import EntityRAGManager, get_entity_rag_manager
from src.infrastructure.storage import get_storage_session
from src.config import Config


@pytest.fixture
def temp_storage_dir():
    """Create a temporary storage directory"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_document():
    """Create a sample text document"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        # Create a document with enough content to generate multiple chunks
        content = "\n\n".join([
            f"This is paragraph {i}. " * 50  # Make each paragraph substantial
            for i in range(10)
        ])
        f.write(content)
        temp_path = f.name

    yield temp_path

    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def entity_rag_manager(temp_storage_dir, monkeypatch):
    """Create an EntityRAGManager with temporary storage"""
    monkeypatch.setattr(Config, 'DATA_DIR', temp_storage_dir)
    manager = EntityRAGManager()
    yield manager
    manager.shutdown()


@pytest.fixture
def setup_test_data(entity_rag_manager, sample_document):
    """Setup test data with a document and chunks"""
    entity_id = "test_entity_123"

    # Add document
    result = entity_rag_manager.add_document(
        entity_id=entity_id,
        file_path=sample_document,
        metadata={"test": "metadata"}
    )

    assert result is not None
    assert result["chunks_count"] > 0

    doc_id = result["doc_id"]

    return {
        "entity_id": entity_id,
        "doc_id": doc_id,
        "chunks_count": result["chunks_count"]
    }


class TestEntityVectorStoreChunkTraversal:
    """Tests for EntityVectorStore chunk traversal methods"""

    def test_get_chunk_by_id(self, entity_rag_manager, setup_test_data):
        """Test retrieving a specific chunk by ID"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]

        store = entity_rag_manager.get_entity_store(entity_id)

        # Get first chunk
        chunk = store.get_chunk_by_id(doc_id, 0)
        assert chunk is not None
        assert chunk["metadata"]["doc_id"] == doc_id
        assert chunk["chunk"]["chunk_order_index"] == 0

        # Get second chunk
        chunk = store.get_chunk_by_id(doc_id, 1)
        assert chunk is not None
        assert chunk["chunk"]["chunk_order_index"] == 1

    def test_get_chunk_by_id_not_found(self, entity_rag_manager, setup_test_data):
        """Test retrieving a non-existent chunk"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]

        store = entity_rag_manager.get_entity_store(entity_id)

        # Try to get a chunk with very high index
        chunk = store.get_chunk_by_id(doc_id, 99999)
        assert chunk is None

    def test_get_previous_chunk(self, entity_rag_manager, setup_test_data):
        """Test getting the previous chunk"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]

        store = entity_rag_manager.get_entity_store(entity_id)

        # Get previous of chunk 2
        prev_chunk = store.get_previous_chunk(doc_id, 2)
        assert prev_chunk is not None
        assert prev_chunk["chunk"]["chunk_order_index"] == 1

        # Get previous of chunk 0 (should be None)
        prev_chunk = store.get_previous_chunk(doc_id, 0)
        assert prev_chunk is None

    def test_get_next_chunk(self, entity_rag_manager, setup_test_data):
        """Test getting the next chunk"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]
        chunks_count = data["chunks_count"]

        store = entity_rag_manager.get_entity_store(entity_id)

        # Get next of first chunk
        next_chunk = store.get_next_chunk(doc_id, 0)
        assert next_chunk is not None
        assert next_chunk["chunk"]["chunk_order_index"] == 1

        # Get next of last chunk (should be None)
        next_chunk = store.get_next_chunk(doc_id, chunks_count - 1)
        assert next_chunk is None

    def test_get_chunk_context(self, entity_rag_manager, setup_test_data):
        """Test getting chunk with context"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]

        store = entity_rag_manager.get_entity_store(entity_id)

        # Get context for chunk 2 with context_size=1
        context = store.get_chunk_context(doc_id, 2, context_size=1)

        assert context["current"] is not None
        assert context["current"]["chunk"]["chunk_order_index"] == 2

        assert len(context["before"]) == 1
        assert context["before"][0]["chunk"]["chunk_order_index"] == 1

        assert len(context["after"]) == 1
        assert context["after"][0]["chunk"]["chunk_order_index"] == 3

    def test_get_chunk_context_larger_window(self, entity_rag_manager, setup_test_data):
        """Test getting chunk with larger context window"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]

        store = entity_rag_manager.get_entity_store(entity_id)

        # Get context for chunk 3 with context_size=2
        context = store.get_chunk_context(doc_id, 3, context_size=2)

        assert context["current"] is not None
        assert len(context["before"]) == 2
        assert len(context["after"]) >= 1  # At least one after

        # Verify order
        assert context["before"][0]["chunk"]["chunk_order_index"] == 1
        assert context["before"][1]["chunk"]["chunk_order_index"] == 2

    def test_get_chunk_context_at_boundaries(self, entity_rag_manager, setup_test_data):
        """Test chunk context at document boundaries"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]

        store = entity_rag_manager.get_entity_store(entity_id)

        # Context at beginning
        context = store.get_chunk_context(doc_id, 0, context_size=2)
        assert context["current"] is not None
        assert len(context["before"]) == 0  # No chunks before
        assert len(context["after"]) >= 1

    def test_get_document_chunks_in_order(self, entity_rag_manager, setup_test_data):
        """Test retrieving all chunks in order"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]
        chunks_count = data["chunks_count"]

        store = entity_rag_manager.get_entity_store(entity_id)

        chunks = store.get_document_chunks_in_order(doc_id)

        assert len(chunks) == chunks_count

        # Verify chunks are in order
        for i, chunk in enumerate(chunks):
            assert chunk["chunk"]["chunk_order_index"] == i

    def test_get_chunk_neighbors(self, entity_rag_manager, setup_test_data):
        """Test getting neighboring chunks"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]

        store = entity_rag_manager.get_entity_store(entity_id)

        # Get neighbors of chunk 3 with window_size=2
        neighbors = store.get_chunk_neighbors(doc_id, 3, window_size=2)

        # Should include chunks 1, 2, 3, 4, 5
        assert len(neighbors) == 5

        # Verify the chunks
        indices = [chunk["chunk"]["chunk_order_index"] for chunk in neighbors]
        assert indices == [1, 2, 3, 4, 5]

    def test_get_chunk_neighbors_at_boundary(self, entity_rag_manager, setup_test_data):
        """Test getting neighbors at document boundary"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]

        store = entity_rag_manager.get_entity_store(entity_id)

        # Get neighbors of chunk 0 with window_size=2
        neighbors = store.get_chunk_neighbors(doc_id, 0, window_size=2)

        # Should include chunks 0, 1, 2 (no negative indices)
        assert len(neighbors) >= 3
        indices = [chunk["chunk"]["chunk_order_index"] for chunk in neighbors]
        assert 0 in indices
        assert all(i >= 0 for i in indices)


class TestEntityRAGManagerChunkTraversal:
    """Tests for EntityRAGManager chunk traversal methods"""

    def test_manager_get_chunk_by_id(self, entity_rag_manager, setup_test_data):
        """Test manager's get_chunk_by_id method"""
        data = setup_test_data

        chunk = entity_rag_manager.get_chunk_by_id(
            data["entity_id"],
            data["doc_id"],
            0
        )

        assert chunk is not None
        assert chunk["chunk"]["chunk_order_index"] == 0

    def test_manager_get_previous_chunk(self, entity_rag_manager, setup_test_data):
        """Test manager's get_previous_chunk method"""
        data = setup_test_data

        prev_chunk = entity_rag_manager.get_previous_chunk(
            data["entity_id"],
            data["doc_id"],
            2
        )

        assert prev_chunk is not None
        assert prev_chunk["chunk"]["chunk_order_index"] == 1

    def test_manager_get_next_chunk(self, entity_rag_manager, setup_test_data):
        """Test manager's get_next_chunk method"""
        data = setup_test_data

        next_chunk = entity_rag_manager.get_next_chunk(
            data["entity_id"],
            data["doc_id"],
            0
        )

        assert next_chunk is not None
        assert next_chunk["chunk"]["chunk_order_index"] == 1

    def test_manager_get_chunk_context(self, entity_rag_manager, setup_test_data):
        """Test manager's get_chunk_context method"""
        data = setup_test_data

        context = entity_rag_manager.get_chunk_context(
            data["entity_id"],
            data["doc_id"],
            2,
            context_size=1
        )

        assert context["current"] is not None
        assert len(context["before"]) == 1
        assert len(context["after"]) >= 1

    def test_manager_get_document_chunks_in_order(self, entity_rag_manager, setup_test_data):
        """Test manager's get_document_chunks_in_order method"""
        data = setup_test_data

        chunks = entity_rag_manager.get_document_chunks_in_order(
            data["entity_id"],
            data["doc_id"]
        )

        assert len(chunks) == data["chunks_count"]
        assert all(
            chunks[i]["chunk"]["chunk_order_index"] == i
            for i in range(len(chunks))
        )

    def test_manager_get_chunk_neighbors(self, entity_rag_manager, setup_test_data):
        """Test manager's get_chunk_neighbors method"""
        data = setup_test_data

        neighbors = entity_rag_manager.get_chunk_neighbors(
            data["entity_id"],
            data["doc_id"],
            2,
            window_size=1
        )

        assert len(neighbors) >= 2  # At least chunk 1, 2, 3
        indices = [chunk["chunk"]["chunk_order_index"] for chunk in neighbors]
        assert 2 in indices  # Target chunk should be included


class TestEntityScopedChunkStorage:
    """Tests for entity-scoped chunk storage"""

    def test_chunks_stored_in_entity_collection(self, entity_rag_manager, setup_test_data):
        """Test that chunks are stored in entity-scoped collections"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]

        store = entity_rag_manager.get_entity_store(entity_id)
        expected_collection = f"{Config.CHUNKS_COLLECTION}_{entity_id}"

        # Verify collection name
        assert store.chunks_collection == expected_collection

        # Verify chunks exist in the collection
        with get_storage_session() as db:
            chunk_count = db[expected_collection].count_documents(
                {"metadata.doc_id": doc_id}
            )
            assert chunk_count == data["chunks_count"]

    def test_multiple_entities_separate_collections(self, entity_rag_manager, sample_document):
        """Test that different entities have separate chunk collections"""
        entity1 = "entity_001"
        entity2 = "entity_002"

        # Add same document to two entities
        result1 = entity_rag_manager.add_document(entity1, sample_document)
        result2 = entity_rag_manager.add_document(entity2, sample_document)

        assert result1 is not None
        assert result2 is not None

        # Verify chunks in separate collections
        with get_storage_session() as db:
            collection1 = f"{Config.CHUNKS_COLLECTION}_{entity1}"
            collection2 = f"{Config.CHUNKS_COLLECTION}_{entity2}"

            count1 = db[collection1].count_documents({})
            count2 = db[collection2].count_documents({})

            assert count1 > 0
            assert count2 > 0

            # Both should have the same number of chunks
            assert count1 == result1["chunks_count"]
            assert count2 == result2["chunks_count"]

    def test_chunk_deletion_entity_scoped(self, entity_rag_manager, setup_test_data):
        """Test that chunk deletion is entity-scoped"""
        data = setup_test_data
        entity_id = data["entity_id"]
        doc_id = data["doc_id"]

        store = entity_rag_manager.get_entity_store(entity_id)

        # Delete the document
        success = store.delete_document(doc_id)
        assert success

        # Verify chunks are deleted from entity collection
        with get_storage_session() as db:
            collection = f"{Config.CHUNKS_COLLECTION}_{entity_id}"
            count = db[collection].count_documents({"metadata.doc_id": doc_id})
            assert count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
