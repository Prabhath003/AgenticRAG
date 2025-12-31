#!/usr/bin/env python3
"""
Test script for batch chunk ingestion endpoint
Tests:
1. Creating an entity
2. Batch submitting multiple chunks
3. Batch submitting with some duplicates
4. Verifying counts and responses
"""

import requests
import json
import time
from typing import Dict, Any
import uuid

API_BASE_URL = "http://localhost:8002"

def create_entity(entity_id: str, entity_name: str) -> Dict[str, Any]:
    """Create a test entity"""
    url = f"{API_BASE_URL}/api/entities"
    payload = {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "description": "Test entity for chunk batch ingestion"
    }
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()

def ingest_chunks_batch(entity_id: str, chunks: list) -> Dict[str, Any]:
    """Batch ingest chunks"""
    url = f"{API_BASE_URL}/api/entities/{entity_id}/chunks/batch"
    payload = {"chunks": chunks}
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()

def create_chunk(chunk_id: str, chunk_order_index: int, text: str, doc_id: str) -> Dict[str, Any]:
    """Helper to create a chunk object"""
    return {
        "chunk_id": chunk_id,
        "markdown": {
            "text": text,
            "chunk_order_index": chunk_order_index,
            "source": f"entity_{doc_id}",
            "filename": "test.pdf",
            "pages": [chunk_order_index + 1]
        },
        "metadata": {
            "chunk_index": chunk_order_index,
            "tokens": len(text.split()),
            "processed_by": "TestBatch",
            "doc_id": doc_id,
            "entity_id": "test_entity"
        }
    }

def main():
    print("=" * 80)
    print("Testing Batch Chunk Ingestion Endpoint")
    print("=" * 80)

    unique_suffix = uuid.uuid4().hex[:8]
    entity_id = f"test_entity_{unique_suffix}"
    entity_name = "Batch Test Company"
    doc_id = "batch_doc_123"

    try:
        # 1. Create entity
        print("\n[1] Creating test entity...")
        entity = create_entity(entity_id, entity_name)
        print(f"✓ Entity created: {entity['entity_id']}")

        # 2. Create a batch of 5 chunks
        print("\n[2] Creating batch of 5 chunks...")
        batch1_chunks = [
            create_chunk(f"{doc_id}_chunk_0", 0, "This is the first chunk of the document.", doc_id),
            create_chunk(f"{doc_id}_chunk_1", 1, "This is the second chunk with more content.", doc_id),
            create_chunk(f"{doc_id}_chunk_2", 2, "The third chunk continues the narrative.", doc_id),
            create_chunk(f"{doc_id}_chunk_3", 3, "Fourth chunk adds additional information.", doc_id),
            create_chunk(f"{doc_id}_chunk_4", 4, "The fifth and final chunk concludes.", doc_id),
        ]
        print(f"✓ Created {len(batch1_chunks)} chunks")

        # 3. Submit first batch
        print("\n[3] Submitting batch (first time)...")
        response1 = ingest_chunks_batch(entity_id, batch1_chunks)
        print(f"✓ Response: {json.dumps(response1, indent=2)}")

        assert response1["indexed_chunks"] == 5, f"Expected 5 indexed chunks, got {response1['indexed_chunks']}"
        assert response1["duplicate_chunks"] == 0, f"Expected 0 duplicates, got {response1['duplicate_chunks']}"
        print("  → All 5 chunks were indexed (as expected)")

        # 4. Create batch with mix of duplicates and new chunks
        print("\n[4] Submitting batch with 3 duplicates and 2 new chunks...")
        batch2_chunks = [
            batch1_chunks[0],  # Duplicate
            batch1_chunks[1],  # Duplicate
            batch1_chunks[2],  # Duplicate
            create_chunk(f"{doc_id}_chunk_5", 5, "The sixth chunk is new.", doc_id),
            create_chunk(f"{doc_id}_chunk_6", 6, "The seventh chunk is also new.", doc_id),
        ]

        time.sleep(1)  # Small delay
        response2 = ingest_chunks_batch(entity_id, batch2_chunks)
        print(f"✓ Response: {json.dumps(response2, indent=2)}")

        assert response2["indexed_chunks"] == 2, f"Expected 2 indexed chunks, got {response2['indexed_chunks']}"
        assert response2["duplicate_chunks"] == 3, f"Expected 3 duplicates, got {response2['duplicate_chunks']}"
        assert response2["total_chunks"] == 5, f"Expected 5 total chunks, got {response2['total_chunks']}"
        print("  → Correctly identified 3 duplicates and indexed 2 new chunks")

        # 5. Submit batch with all duplicates
        print("\n[5] Submitting batch with all duplicates...")
        batch3_chunks = [
            batch1_chunks[0],  # Duplicate
            batch1_chunks[1],  # Duplicate
        ]

        time.sleep(1)  # Small delay
        response3 = ingest_chunks_batch(entity_id, batch3_chunks)
        print(f"✓ Response: {json.dumps(response3, indent=2)}")

        assert response3["indexed_chunks"] == 0, f"Expected 0 indexed chunks, got {response3['indexed_chunks']}"
        assert response3["duplicate_chunks"] == 2, f"Expected 2 duplicates, got {response3['duplicate_chunks']}"
        print("  → Correctly identified all chunks as duplicates")

        print("\n" + "=" * 80)
        print("✓ All batch tests passed!")
        print("=" * 80)
        return True

    except requests.exceptions.ConnectionError:
        print("\n✗ ERROR: Could not connect to API server")
        print(f"  Make sure the server is running at {API_BASE_URL}")
        return False
    except AssertionError as ae:
        print(f"\n✗ Assertion Error: {ae}")
        return False
    except requests.exceptions.HTTPError as e:
        print(f"\n✗ HTTP Error: {e}")
        print(f"  Status: {e.response.status_code}")
        print(f"  Response: {e.response.text}")
        return False
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
