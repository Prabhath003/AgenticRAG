#!/usr/bin/env python3
"""Test script for JSON storage implementation"""

import os
import sys
import tempfile
# from pathlib import Path

# Add src to path
# sys.path.insert(0, str(Path(__file__).parent / "src"))

# from src.infrastructure.storage import get_storage, get_storage_session

def test_json_storage():
    """Test basic JSON storage operations"""
    print("Testing JSON Storage Implementation...")

    # Create temporary storage directory
    test_dir = "tests/test_data/test_json_storage"
    storage_dir = os.path.join(test_dir, "test_storage")

    # Create storage instance
    from src.infrastructure.storage.json_storage import JSONStorage
    storage = JSONStorage(storage_dir, enable_sharding=False)

    print(f"✓ Created storage at: {storage_dir}")

    # Test 1: Insert a document
    print("\n1. Testing insert operation...")
    result = storage.update_one(
        "test_collection",
        {"_id": "doc1"},
        {
            "$set": {"name": "Test Document", "value": 42},
            "$setOnInsert": {"_id": "doc1"}
        },
        upsert=True
    )
    print(f"   Inserted: matched={result['matched_count']}, modified={result['modified_count']}")
    assert result['modified_count'] == 1
    print("   ✓ Insert successful")

    # Test 2: Find the document
    print("\n2. Testing find operation...")
    doc = storage.find_one("test_collection", {"_id": "doc1"})
    print(f"   Found: {doc}")
    assert doc is not None
    assert doc['name'] == "Test Document"
    assert doc['value'] == 42
    print("   ✓ Find successful")

    # Test 3: Update the document
    print("\n3. Testing update operation...")
    result = storage.update_one(
        "test_collection",
        {"_id": "doc1"},
        {"$set": {"value": 100}}
    )
    print(f"   Updated: matched={result['matched_count']}, modified={result['modified_count']}")
    assert result['matched_count'] == 1
    assert result['modified_count'] == 1

    # Verify update
    doc = storage.find_one("test_collection", {"_id": "doc1"})
    assert doc['value'] == 100
    print("   ✓ Update successful")

    # Test 4: Add to set
    print("\n4. Testing $addToSet operation...")
    result = storage.update_one(
        "test_collection",
        {"_id": "doc1"},
        {"$addToSet": {"entity_ids": "entity_1"}}
    )
    doc = storage.find_one("test_collection", {"_id": "doc1"})
    print(f"   Document after addToSet: {doc}")
    assert "entity_ids" in doc
    assert "entity_1" in doc['entity_ids']
    print("   ✓ AddToSet successful")

    # Test 5: Find with query operators
    print("\n5. Testing query operators...")
    docs = storage.find("test_collection", {"value": {"$gte": 50}})
    print(f"   Found {len(docs)} docs with value >= 50")
    assert len(docs) == 1
    print("   ✓ Query operators successful")

    # Test 6: Delete operation
    print("\n6. Testing delete operation...")
    result = storage.delete_one("test_collection", {"_id": "doc1"})
    print(f"   Deleted: {result['deleted_count']} documents")
    assert result['deleted_count'] == 1

    # Verify deletion
    doc = storage.find_one("test_collection", {"_id": "doc1"})
    assert doc is None
    print("   ✓ Delete successful")

    # Test 7: Test session context manager
    print("\n7. Testing session context manager...")
    from src.infrastructure.storage.json_storage import JSONStorageSession
    session = JSONStorageSession(storage)

    with session as db:
        db["users"].update_one(
            {"_id": "user1"},
            {"$set": {"name": "Alice", "age": 30}, "$setOnInsert": {"_id": "user1"}},
            upsert=True
        )

        user = db["users"].find_one({"_id": "user1"})
        print(f"   User: {user}")
        assert user['name'] == "Alice"
        assert user['age'] == 30
    print("   ✓ Session context manager successful")

    # Test 8: Test atomic writes
    print("\n8. Testing atomic writes...")
    collection_file = storage._get_collection_path("atomic_test")

    # Write multiple times quickly
    for i in range(5):
        storage.update_one(
            "atomic_test",
            {"_id": f"doc{i}"},
            {"$set": {"index": i}, "$setOnInsert": {"_id": f"doc{i}"}},
            upsert=True
        )

    # Verify all documents exist
    docs = storage.find("atomic_test", {})
    print(f"   Created {len(docs)} documents")
    assert len(docs) == 5

    # Verify file exists and is valid JSON
    import json
    with open(collection_file, 'r') as f:
        data = json.load(f)
        print(f"   File contains {len(data)} entries")
        assert len(data) == 5
    print("   ✓ Atomic writes successful")

    print("\n" + "="*50)
    print("All tests passed! ✓")
    print("="*50)

if __name__ == "__main__":
    try:
        test_json_storage()
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
