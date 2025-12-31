#!/usr/bin/env python3
"""
Test script for chunk ingestion endpoint
Tests:
1. Creating an entity
2. Submitting a chunk
3. Submitting the same chunk (duplicate detection)
4. Verifying responses
"""

import requests
import json
import time
from typing import Dict, Any

API_BASE_URL = "http://localhost:8002"

def create_entity(entity_id: str, entity_name: str) -> Dict[str, Any]:
    """Create a test entity"""
    url = f"{API_BASE_URL}/api/entities"
    payload = {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "description": "Test entity for chunk ingestion"
    }
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()

def ingest_chunk(entity_id: str, chunk_data: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest a chunk"""
    url = f"{API_BASE_URL}/api/entities/{entity_id}/chunks"
    response = requests.post(url, json=chunk_data)
    response.raise_for_status()
    return response.json()

def main():
    print("=" * 80)
    print("Testing Chunk Ingestion Endpoint")
    print("=" * 80)

    import uuid
    unique_suffix = uuid.uuid4().hex[:8]
    entity_id = f"test_entity_{unique_suffix}"
    entity_name = "Test Company"

    try:
        # 1. Create entity
        print("\n[1] Creating test entity...")
        entity = create_entity(entity_id, entity_name)
        print(f"✓ Entity created: {entity['entity_id']}")

        # 2. Prepare chunk data (using the structure provided)
        chunk_data = {
            "chunk_id": "692457c5993bfb88f1cea68f_doc_f7d8b75f_0",
            "markdown": {
                "text": "**BIZZTM TECHNOLOGY PRIVATE LIMITED**\n\n*Comprehensive Company Analysis Report*\n\nReport Generated: October 28, 2025\n\n### Company Information\n\n| Field | Value |\n| --- | --- |\n| CIN: | U51100HR2020PTC088956 |\n| Industry: | E-commerce |\n| Stage: | Seed |\n| Founded: | 2020 |",
                "chunk_order_index": 0,
                "source": "entity_692457c5993bfb88f1cea68f",
                "filename": "fac9512a-ef91-425f-9e56-66541ef3807a.pdf",
                "pages": [1, 2, 3, 4, 5, 6]
            },
            "metadata": {
                "chunk_index": 0,
                "tokens": 769,
                "processed_by": "FileProcessor",
                "doc_id": "692457c5993bfb88f1cea68f_doc_f7d8b75f",
                "entity_id": entity_id
            }
        }

        # 3. Submit chunk first time (should be indexed)
        print("\n[2] Ingesting chunk (first time)...")
        response1 = ingest_chunk(entity_id, chunk_data)
        print(f"✓ Response: {json.dumps(response1, indent=2)}")

        if response1.get("indexed"):
            print("  → Chunk was indexed (as expected)")
        else:
            print("  ✗ ERROR: Chunk should have been indexed on first submission")
            return False

        # 4. Submit same chunk again (should detect as duplicate)
        print("\n[3] Ingesting same chunk (duplicate detection)...")
        time.sleep(1)  # Small delay to ensure different timestamps
        response2 = ingest_chunk(entity_id, chunk_data)
        print(f"✓ Response: {json.dumps(response2, indent=2)}")

        if not response2.get("indexed"):
            print("  → Chunk was correctly detected as duplicate (indexed=False)")
        else:
            print("  ✗ ERROR: Chunk should have been detected as duplicate")
            return False

        # 5. Submit different chunk (should be indexed)
        print("\n[4] Ingesting different chunk...")
        chunk_data2 = chunk_data.copy()
        chunk_data2["chunk_id"] = "different_chunk_id_001"
        chunk_data2["markdown"] = chunk_data["markdown"].copy()
        chunk_data2["markdown"]["chunk_order_index"] = 1
        chunk_data2["metadata"] = chunk_data["metadata"].copy()
        chunk_data2["metadata"]["chunk_index"] = 1

        response3 = ingest_chunk(entity_id, chunk_data2)
        print(f"✓ Response: {json.dumps(response3, indent=2)}")

        if response3.get("indexed"):
            print("  → New chunk was indexed (as expected)")
        else:
            print("  ✗ ERROR: New chunk should have been indexed")
            return False

        print("\n" + "=" * 80)
        print("✓ All tests passed!")
        print("=" * 80)
        return True

    except requests.exceptions.ConnectionError:
        print("\n✗ ERROR: Could not connect to API server")
        print(f"  Make sure the server is running at {API_BASE_URL}")
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
