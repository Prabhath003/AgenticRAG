#!/usr/bin/env python3
"""Test $inc operator for cost accumulation and counter increments"""

import os
import sys
import shutil
import traceback

from src.infrastructure.storage import JSONStorage


def test_inc_operator():
    """Test $inc operator for atomic counter increments"""
    print("Testing $inc Operator Implementation...")

    # Create temporary storage directory
    test_dir = "tests/test_data/test_inc"
    storage_dir = os.path.join(test_dir, "test_storage")

    # Ensure clean test environment

    if os.path.exists(storage_dir):
        shutil.rmtree(storage_dir)

    # Create storage instance

    storage = JSONStorage(storage_dir, enable_sharding=False)

    print(f"✓ Created storage at: {storage_dir}")

    # Test 1: Basic $inc operation on new document
    print("\n1. Testing $inc on new document...")
    _ = storage.update_one(
        "entities",
        {"_id": "entity1"},
        {"$set": {"name": "Entity 1"}, "$inc": {"documents_count": 1, "chunk_count": 10}},
        upsert=True,
    )
    doc = storage.find_one("entities", {"_id": "entity1"})
    print(f"   Document: {doc}")
    assert doc["documents_count"] == 1 if doc else False
    assert doc["chunk_count"] == 10 if doc else False
    assert doc["name"] == "Entity 1" if doc else False
    print("   ✓ $inc on new document successful")

    # Test 2: $inc on existing document
    print("\n2. Testing $inc on existing document...")
    _ = storage.update_one(
        "entities", {"_id": "entity1"}, {"$inc": {"documents_count": 1, "chunk_count": 5}}
    )
    doc = storage.find_one("entities", {"_id": "entity1"})
    print(f"   Document after increment: {doc}")
    assert doc["documents_count"] == 2 if doc else False
    assert doc["chunk_count"] == 15 if doc else False
    print("   ✓ $inc on existing document successful")

    # Test 3: Multiple increments (simulating multiple file uploads)
    print("\n3. Testing multiple increments...")
    for i in range(5):
        storage.update_one(
            "entities", {"_id": "entity1"}, {"$inc": {"documents_count": 1, "chunk_count": 3}}
        )
    doc = storage.find_one("entities", {"_id": "entity1"})
    print(f"   Document after 5 increments: {doc}")
    assert doc["documents_count"] == 7 if doc else False  # 2 + 5
    assert doc["chunk_count"] == 30 if doc else False  # 15 + (5 * 3)
    print("   ✓ Multiple increments successful")

    # Test 4: Negative $inc (decrement)
    print("\n4. Testing negative $inc (decrement)...")
    _ = storage.update_one(
        "entities", {"_id": "entity1"}, {"$inc": {"documents_count": -1, "chunk_count": -5}}
    )
    doc = storage.find_one("entities", {"_id": "entity1"})
    print(f"   Document after decrement: {doc}")
    assert doc["documents_count"] == 6 if doc else False  # 7 - 1
    assert doc["chunk_count"] == 25 if doc else False  # 30 - 5
    print("   ✓ Negative $inc (decrement) successful")

    # Test 5: $inc with $set in single operation
    print("\n5. Testing $inc combined with $set...")
    _ = storage.update_one(
        "entities",
        {"_id": "entity1"},
        {
            "$set": {"last_updated": "2025-11-25", "estimated_cost_usd": 5.50},
            "$inc": {"documents_count": 1, "chunk_count": 10},
        },
    )
    doc = storage.find_one("entities", {"_id": "entity1"})
    print(f"   Document after $set + $inc: {doc}")
    assert doc["documents_count"] == 7 if doc else False  # 6 + 1
    assert doc["chunk_count"] == 35 if doc else False  # 25 + 10
    assert doc["last_updated"] == "2025-11-25" if doc else False
    assert doc["estimated_cost_usd"] == 5.50 if doc else False
    print("   ✓ $inc combined with $set successful")

    # Test 6: Float $inc for cost tracking
    print("\n6. Testing float $inc for cost tracking...")
    storage.update_one(
        "sessions",
        {"_id": "session1"},
        {"$set": {"entity_id": "entity1"}, "$inc": {"estimated_cost_usd": 1.25}},
        upsert=True,
    )
    doc = storage.find_one("sessions", {"_id": "session1"})
    assert doc["estimated_cost_usd"] == 1.25 if doc else False

    # Increment cost again
    storage.update_one("sessions", {"_id": "session1"}, {"$inc": {"estimated_cost_usd": 0.75}})
    doc = storage.find_one("sessions", {"_id": "session1"})
    print(f"   Session cost after increments: {doc['estimated_cost_usd']}") if doc else False
    assert (
        abs(doc["estimated_cost_usd"] - 2.0) < 0.001 if doc else False
    )  # Account for floating point precision
    print("   ✓ Float $inc for cost tracking successful")

    # Test 7: $inc initializes non-existent field to increment value
    print("\n7. Testing $inc initializes non-existent field...")
    storage.update_one(
        "tasks",
        {"_id": "task1"},
        {"$set": {"name": "Task 1"}, "$inc": {"retry_count": 1}},  # Field doesn't exist yet
        upsert=True,
    )
    doc = storage.find_one("tasks", {"_id": "task1"})
    print(f"   Task document: {doc}")
    assert doc["retry_count"] == 1 if doc else False
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
            "$inc": {"estimated_cost_usd": 0.05},
        },
        upsert=True,
    )

    # Add multiple messages with costs
    for i in range(3):
        storage.update_one(
            "chat_sessions",
            {"_id": session_id},
            {
                "$set": {"last_updated": f"update_{i}"},
                "$inc": {"estimated_cost_usd": 0.03 + (i * 0.01), "message_count": 1},
            },
        )

    doc = storage.find_one("chat_sessions", {"_id": session_id})
    print(f"   Session after 3 messages: {doc}")
    expected_cost = 0.05 + 0.03 + 0.04 + 0.05  # 0.05 + (0.03 + 0.04 + 0.05)
    assert abs(doc["estimated_cost_usd"] - expected_cost) < 0.001 if doc else False
    assert doc["message_count"] == 3 if doc else False
    print("   ✓ Cost accumulation successful")

    print("\n" + "=" * 50)
    print("All $inc operator tests passed! ✓")
    print("=" * 50)


if __name__ == "__main__":
    try:
        test_inc_operator()
    except Exception as e:
        print(f"\n✗ Test failed: {e}")

        traceback.print_exc()
        sys.exit(1)
