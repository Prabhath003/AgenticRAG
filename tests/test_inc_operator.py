#!/usr/bin/env python3
"""Test $inc operator for cost accumulation and counter increments"""

import os
import sys
import tempfile

def test_inc_operator():
    """Test $inc operator for atomic counter increments"""
    print("Testing $inc Operator Implementation...")

    # Create temporary storage directory
    test_dir = "tests/test_data/test_inc"
    storage_dir = os.path.join(test_dir, "test_storage")

    # Ensure clean test environment
    import shutil
    if os.path.exists(storage_dir):
        shutil.rmtree(storage_dir)

    # Create storage instance
    from src.infrastructure.storage.json_storage import JSONStorage
    storage = JSONStorage(storage_dir, enable_sharding=False)

    print(f"✓ Created storage at: {storage_dir}")

    # Test 1: Basic $inc operation on new document
    print("\n1. Testing $inc on new document...")
    result = storage.update_one(
        "entities",
        {"_id": "entity1"},
        {
            "$set": {"name": "Entity 1"},
            "$inc": {"documents_count": 1, "chunk_count": 10}
        },
        upsert=True
    )
    doc = storage.find_one("entities", {"_id": "entity1"})
    print(f"   Document: {doc}")
    assert doc['documents_count'] == 1
    assert doc['chunk_count'] == 10
    assert doc['name'] == "Entity 1"
    print("   ✓ $inc on new document successful")

    # Test 2: $inc on existing document
    print("\n2. Testing $inc on existing document...")
    result = storage.update_one(
        "entities",
        {"_id": "entity1"},
        {"$inc": {"documents_count": 1, "chunk_count": 5}}
    )
    doc = storage.find_one("entities", {"_id": "entity1"})
    print(f"   Document after increment: {doc}")
    assert doc['documents_count'] == 2
    assert doc['chunk_count'] == 15
    print("   ✓ $inc on existing document successful")

    # Test 3: Multiple increments (simulating multiple file uploads)
    print("\n3. Testing multiple increments...")
    for i in range(5):
        storage.update_one(
            "entities",
            {"_id": "entity1"},
            {"$inc": {"documents_count": 1, "chunk_count": 3}}
        )
    doc = storage.find_one("entities", {"_id": "entity1"})
    print(f"   Document after 5 increments: {doc}")
    assert doc['documents_count'] == 7  # 2 + 5
    assert doc['chunk_count'] == 30  # 15 + (5 * 3)
    print("   ✓ Multiple increments successful")

    # Test 4: Negative $inc (decrement)
    print("\n4. Testing negative $inc (decrement)...")
    result = storage.update_one(
        "entities",
        {"_id": "entity1"},
        {"$inc": {"documents_count": -1, "chunk_count": -5}}
    )
    doc = storage.find_one("entities", {"_id": "entity1"})
    print(f"   Document after decrement: {doc}")
    assert doc['documents_count'] == 6  # 7 - 1
    assert doc['chunk_count'] == 25  # 30 - 5
    print("   ✓ Negative $inc (decrement) successful")

    # Test 5: $inc with $set in single operation
    print("\n5. Testing $inc combined with $set...")
    result = storage.update_one(
        "entities",
        {"_id": "entity1"},
        {
            "$set": {"last_updated": "2025-11-25", "estimated_cost_usd": 5.50},
            "$inc": {"documents_count": 1, "chunk_count": 10}
        }
    )
    doc = storage.find_one("entities", {"_id": "entity1"})
    print(f"   Document after $set + $inc: {doc}")
    assert doc['documents_count'] == 7  # 6 + 1
    assert doc['chunk_count'] == 35  # 25 + 10
    assert doc['last_updated'] == "2025-11-25"
    assert doc['estimated_cost_usd'] == 5.50
    print("   ✓ $inc combined with $set successful")

    # Test 6: Float $inc for cost tracking
    print("\n6. Testing float $inc for cost tracking...")
    storage.update_one(
        "sessions",
        {"_id": "session1"},
        {
            "$set": {"entity_id": "entity1"},
            "$inc": {"estimated_cost_usd": 1.25}
        },
        upsert=True
    )
    doc = storage.find_one("sessions", {"_id": "session1"})
    assert doc['estimated_cost_usd'] == 1.25

    # Increment cost again
    storage.update_one(
        "sessions",
        {"_id": "session1"},
        {"$inc": {"estimated_cost_usd": 0.75}}
    )
    doc = storage.find_one("sessions", {"_id": "session1"})
    print(f"   Session cost after increments: {doc['estimated_cost_usd']}")
    assert abs(doc['estimated_cost_usd'] - 2.0) < 0.001  # Account for floating point precision
    print("   ✓ Float $inc for cost tracking successful")

    # Test 7: $inc initializes non-existent field to increment value
    print("\n7. Testing $inc initializes non-existent field...")
    storage.update_one(
        "tasks",
        {"_id": "task1"},
        {
            "$set": {"name": "Task 1"},
            "$inc": {"retry_count": 1}  # Field doesn't exist yet
        },
        upsert=True
    )
    doc = storage.find_one("tasks", {"_id": "task1"})
    print(f"   Task document: {doc}")
    assert doc['retry_count'] == 1
    print("   ✓ $inc initialization successful")

    # Test 8: Simulate cost accumulation in chat session
    print("\n8. Simulating chat session cost accumulation...")
    session_id = "chat_session_123"

    # Initial session with first message cost
    storage.update_one(
        "chat_sessions",
        {"_id": session_id},
        {
            "$set": {"entity_id": "entity1", "message_count": 0},
            "$inc": {"estimated_cost_usd": 0.05}
        },
        upsert=True
    )

    # Add multiple messages with costs
    for i in range(3):
        storage.update_one(
            "chat_sessions",
            {"_id": session_id},
            {
                "$set": {"last_updated": f"update_{i}"},
                "$inc": {
                    "estimated_cost_usd": 0.03 + (i * 0.01),
                    "message_count": 1
                }
            }
        )

    doc = storage.find_one("chat_sessions", {"_id": session_id})
    print(f"   Session after 3 messages: {doc}")
    expected_cost = 0.05 + 0.03 + 0.04 + 0.05  # 0.05 + (0.03 + 0.04 + 0.05)
    assert abs(doc['estimated_cost_usd'] - expected_cost) < 0.001
    assert doc['message_count'] == 3
    print("   ✓ Cost accumulation successful")

    print("\n" + "="*50)
    print("All $inc operator tests passed! ✓")
    print("="*50)

if __name__ == "__main__":
    try:
        test_inc_operator()
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
