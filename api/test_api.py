#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# Copyright (c) 2025 Edureka Backend
# All rights reserved.
#
# Developed by: GiKA AI Team
# Author: Prabhath Chellingi
# GitHub: https://github.com/Prabhath003
# Contact: prabhath@gikagraph.ai
#
# This source code is licensed under the MIT License found in the LICENSE file
# in the root directory of this source tree.
# -----------------------------------------------------------------------------

"""
Test client for Entity-Scoped RAG API

This script demonstrates the complete workflow:
1. Create entity
2. Upload file (get doc_id)
3. Create chat session
4. Send messages
5. Search documents
"""

import requests
import time
from pathlib import Path
import tempfile

BASE_URL = "http://localhost:8000"

def print_separator(title: str = ""):
    """Print a separator line"""
    print("\n" + "=" * 80)
    if title:
        print(f" {title}")
        print("=" * 80)
    print()

def test_health_check():
    """Test health check endpoint"""
    print_separator("1. Health Check")

    response = requests.get(f"{BASE_URL}/health")
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")

    assert response.status_code == 200, "Health check failed"
    assert response.json()["status"] == "healthy", "API not healthy"

    print("✅ Health check passed")

def test_create_entity():
    """Test entity creation"""
    print_separator("2. Create Entity")

    entity_data = {
        "entity_id": "test_company_001",
        "entity_name": "Test Corporation",
        "description": "A test company for API testing",
        "metadata": {
            "industry": "Technology",
            "founded": 2024
        }
    }

    response = requests.post(f"{BASE_URL}/api/entities", json=entity_data)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")

    if response.status_code == 400:
        print("⚠️  Entity already exists, continuing with existing entity")
    else:
        assert response.status_code == 200, f"Failed to create entity: {response.text}"
        print("✅ Entity created successfully")

    return entity_data["entity_id"]

def test_get_entity(entity_id: str):
    """Test get entity details"""
    print_separator("3. Get Entity Details")

    response = requests.get(f"{BASE_URL}/api/entities/{entity_id}")
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")

    assert response.status_code == 200, "Failed to get entity"
    assert response.json()["entity_id"] == entity_id, "Entity ID mismatch"

    print("✅ Entity retrieved successfully")

def test_list_entities():
    """Test list all entities"""
    print_separator("4. List All Entities")

    response = requests.get(f"{BASE_URL}/api/entities")
    print(f"Status Code: {response.status_code}")
    data = response.json()
    print(f"Total Entities: {data['total']}")
    print(f"Entities: {[e['entity_name'] for e in data['entities']]}")

    assert response.status_code == 200, "Failed to list entities"
    assert data["total"] > 0, "No entities found"

    print("✅ Entities listed successfully")

def test_upload_file(entity_id: str):
    """Test file upload (returns doc_id)"""
    print_separator("5. Upload File (Returns doc_id)")

    # Create a temporary test file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write("# Test Document\n\n")
        f.write("This is a test document for the Entity-Scoped RAG API.\n\n")
        f.write("## Revenue Analysis\n")
        f.write("The company achieved $145 million in Q4 revenue.\n\n")
        f.write("## Growth Metrics\n")
        f.write("Year-over-year growth was 35%.\n")
        temp_file_path = f.name

    try:
        # Upload the file
        with open(temp_file_path, 'rb') as f:
            files = {"file": ("test_report.txt", f, "text/plain")}
            data = {"description": "Test financial report"}

            response = requests.post(
                f"{BASE_URL}/api/entities/{entity_id}/files",
                files=files,
                data=data
            )

        print(f"Status Code: {response.status_code}")
        result = response.json()
        print(f"Response: {result}")

        assert response.status_code == 200, f"Failed to upload file: {response.text}"
        assert "doc_id" in result, "doc_id not returned in response"

        doc_id = result["doc_id"]
        print(f"\n📄 File uploaded successfully!")
        print(f"   doc_id: {doc_id}")
        print(f"   filename: {result['filename']}")
        print(f"   chunks_count: {result['chunks_count']}")

        print("✅ File upload test passed (doc_id returned)")

        return doc_id

    finally:
        # Cleanup temp file
        Path(temp_file_path).unlink(missing_ok=True)

def test_list_files(entity_id: str):
    """Test list files for entity"""
    print_separator("6. List Files")

    response = requests.get(f"{BASE_URL}/api/entities/{entity_id}/files")
    print(f"Status Code: {response.status_code}")
    files = response.json()
    print(f"Total Files: {len(files)}")
    for file_info in files:
        print(f"  - {file_info['doc_name']} (doc_id: {file_info['doc_id']})")

    assert response.status_code == 200, "Failed to list files"
    assert len(files) > 0, "No files found"

    print("✅ Files listed successfully")

def test_search(entity_id: str):
    """Test entity-scoped search"""
    print_separator("7. Search Documents")

    # Wait a moment for indexing to complete
    time.sleep(2)

    search_data = {
        "entity_id": entity_id,
        "query": "revenue growth",
        "k": 3
    }

    response = requests.post(f"{BASE_URL}/api/search", json=search_data)
    print(f"Status Code: {response.status_code}")
    result = response.json()
    print(f"Query: {result['query']}")
    print(f"Total Results: {result['total']}")

    if result['results']:
        print("\nTop Result:")
        print(f"  Content: {result['results'][0]['content'][:200]}...")
        print(f"  Source: {result['results'][0]['source']}")

    assert response.status_code == 200, "Search failed"

    print("✅ Search completed successfully")

def test_create_chat_session(entity_id: str):
    """Test create chat session"""
    print_separator("8. Create Chat Session")

    session_data = {
        "entity_id": entity_id,
        "session_name": "Financial Analysis Session",
        "metadata": {"purpose": "testing"}
    }

    response = requests.post(f"{BASE_URL}/api/chat/sessions", json=session_data)
    print(f"Status Code: {response.status_code}")
    result = response.json()
    print(f"Response: {result}")

    assert response.status_code == 200, "Failed to create chat session"
    assert "session_id" in result, "session_id not returned"

    session_id = result["session_id"]
    print(f"\n💬 Chat session created!")
    print(f"   session_id: {session_id}")
    print(f"   session_name: {result['session_name']}")

    print("✅ Chat session created successfully")

    return session_id

def test_list_sessions(entity_id: str):
    """Test list chat sessions for entity"""
    print_separator("9. List Chat Sessions")

    response = requests.get(f"{BASE_URL}/api/entities/{entity_id}/sessions")
    print(f"Status Code: {response.status_code}")
    sessions = response.json()
    print(f"Total Sessions: {len(sessions)}")
    for session in sessions:
        print(f"  - {session['session_name']} (session_id: {session['session_id']})")
        print(f"    Messages: {session['message_count']}")

    assert response.status_code == 200, "Failed to list sessions"

    print("✅ Sessions listed successfully")

def test_chat(session_id: str):
    """Test send chat message (non-streaming)"""
    print_separator("10. Send Chat Message")

    chat_data = {
        "session_id": session_id,
        "message": "What was the Q4 revenue mentioned in the document?",
        "stream": False
    }

    print(f"User: {chat_data['message']}")
    print("\nWaiting for response...")

    response = requests.post(f"{BASE_URL}/api/chat", json=chat_data)
    print(f"\nStatus Code: {response.status_code}")

    if response.status_code == 200:
        result = response.json()
        assistant_message = result["message"]["content"]
        print(f"\nAssistant: {assistant_message[:500]}...")

        print("✅ Chat message processed successfully")
    else:
        print(f"⚠️  Chat request returned status {response.status_code}: {response.text}")

def test_chat_history(session_id: str):
    """Test get chat history"""
    print_separator("11. Get Chat History")

    response = requests.get(f"{BASE_URL}/api/chat/sessions/{session_id}/messages")
    print(f"Status Code: {response.status_code}")

    if response.status_code == 200:
        messages = response.json()
        print(f"Total Messages: {len(messages)}")

        for msg in messages:
            print(f"\n{msg['role'].upper()}: {msg['content'][:200]}...")

        print("\n✅ Chat history retrieved successfully")
    else:
        print(f"⚠️  Failed to get chat history: {response.text}")

def test_delete_file(entity_id: str, doc_id: str):
    """Test delete file by doc_id"""
    print_separator("12. Delete File")

    print(f"Deleting file with doc_id: {doc_id}")

    response = requests.delete(f"{BASE_URL}/api/entities/{entity_id}/files/{doc_id}")
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")

    assert response.status_code == 200, "Failed to delete file"

    print("✅ File deleted successfully")

def test_delete_session(session_id: str):
    """Test delete chat session"""
    print_separator("13. Delete Chat Session")

    print(f"Deleting session: {session_id}")

    response = requests.delete(f"{BASE_URL}/api/chat/sessions/{session_id}")
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.json()}")

    assert response.status_code == 200, "Failed to delete session"

    print("✅ Chat session deleted successfully")

def main():
    """Run all tests"""
    print("\n" + "=" * 80)
    print(" Entity-Scoped RAG API - Test Suite")
    print("=" * 80)
    print(f"\nTesting API at: {BASE_URL}")
    print("Make sure the API server is running (python main.py)")

    input("\nPress Enter to start tests...")

    try:
        # 1. Health check
        test_health_check()

        # 2. Entity management
        entity_id = test_create_entity()
        test_get_entity(entity_id)
        test_list_entities()

        # 3. File management
        doc_id = test_upload_file(entity_id)
        test_list_files(entity_id)

        # 4. Search
        test_search(entity_id)

        # 5. Chat sessions
        session_id = test_create_chat_session(entity_id)
        test_list_sessions(entity_id)

        # 6. Chat
        test_chat(session_id)
        test_chat_history(session_id)

        # 7. Cleanup (optional - uncomment to test delete)
        # test_delete_file(entity_id, doc_id)
        # test_delete_session(session_id)

        # Final summary
        print_separator("Test Summary")
        print("✅ All tests completed successfully!")
        print(f"\nEntity ID: {entity_id}")
        print(f"Doc ID: {doc_id}")
        print(f"Session ID: {session_id}")
        print(f"\nYou can now:")
        print(f"  - View API docs: {BASE_URL}/docs")
        print(f"  - Check entity: GET {BASE_URL}/api/entities/{entity_id}")
        print(f"  - List sessions: GET {BASE_URL}/api/entities/{entity_id}/sessions")

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1
    except requests.exceptions.ConnectionError:
        print(f"\n❌ Connection error: Is the API server running at {BASE_URL}?")
        print("   Start the server with: cd api && python main.py")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0

if __name__ == "__main__":
    exit(main())
