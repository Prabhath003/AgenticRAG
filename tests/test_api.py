#!/usr/bin/env python3
"""Test client for Entity-Scoped RAG API"""

import requests
import json
import time
from pathlib import Path

BASE_URL = "http://localhost:8002"


def print_section(title):
    """Print a section header"""
    print("\n" + "="*60)
    print(f"  {title}")
    print("="*60)


def test_api():
    """Test the complete API workflow"""
    print_section("Entity-Scoped RAG API Test")

    # Test 1: Health Check
    print_section("1. Health Check")
    response = requests.get(f"{BASE_URL}/health")
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")

    # Test 2: Create Entity
    print_section("2. Create Entity")
    entity_data = {
        "entity_id": "company_techcorp",
        "entity_name": "TechCorp Industries",
        "description": "AI-powered analytics company",
        "metadata": {
            "industry": "Technology",
            "founded": 2020
        }
    }
    response = requests.post(f"{BASE_URL}/api/entities", json=entity_data)
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")

    # Test 3: List Entities
    print_section("3. List Entities")
    response = requests.get(f"{BASE_URL}/api/entities")
    print(f"Status: {response.status_code}")
    print(f"Entities: {json.dumps(response.json(), indent=2)}")

    # Test 4: Upload File
    print_section("4. Upload File")

    # Create a test file
    test_file_content = """
    TechCorp Industries - Q4 2024 Financial Report

    Financial Highlights:
    - Q4 Revenue: $145 million (up 35% YoY)
    - Annual Revenue 2024: $520 million
    - Net Income Q4: $28 million
    - Operating Margin: 19.3%

    Key Achievements:
    - Launched AI-powered analytics platform
    - Signed 50+ new enterprise customers
    - Expanded to European markets

    Product Performance:
    - AI Analytics Suite: $65M in Q4 revenue
    - Enterprise Cloud Platform: $48M in Q4 revenue
    """

    test_file_path = Path("/tmp/techcorp_q4_report.txt")
    test_file_path.write_text(test_file_content)

    with open(test_file_path, "rb") as f:
        files = {"file": ("techcorp_q4_report.txt", f, "text/plain")}
        data = {"description": "Q4 2024 Financial Report"}
        response = requests.post(
            f"{BASE_URL}/api/entities/company_techcorp/files",
            files=files,
            data=data
        )

    print(f"Status: {response.status_code}")
    upload_response = response.json()
    print(f"Response: {json.dumps(upload_response, indent=2)}")
    doc_id = upload_response.get("doc_id")
    print(f"\n✓ Document ID: {doc_id}")

    # Wait for indexing
    time.sleep(2)

    # Test 5: List Files
    print_section("5. List Files")
    response = requests.get(f"{BASE_URL}/api/entities/company_techcorp/files")
    print(f"Status: {response.status_code}")
    print(f"Files: {json.dumps(response.json(), indent=2)}")

    # Test 6: Search
    print_section("6. Search Documents")
    search_data = {
        "entity_id": "company_techcorp",
        "query": "Q4 revenue financial performance",
        "k": 3
    }
    response = requests.post(f"{BASE_URL}/api/search", json=search_data)
    print(f"Status: {response.status_code}")
    search_results = response.json()
    print(f"Found {search_results['total']} results")
    for i, result in enumerate(search_results['results'], 1):
        print(f"\nResult {i}:")
        print(f"  Doc ID: {result['doc_id']}")
        print(f"  Content: {result['content'][:200]}...")

    # Test 7: Create Chat Session
    print_section("7. Create Chat Session")
    session_data = {
        "entity_id": "company_techcorp",
        "session_name": "Financial Analysis Session",
        "metadata": {"purpose": "Q4 analysis"}
    }
    response = requests.post(f"{BASE_URL}/api/chat/sessions", json=session_data)
    print(f"Status: {response.status_code}")
    session_response = response.json()
    print(f"Response: {json.dumps(session_response, indent=2)}")
    session_id = session_response["session_id"]
    print(f"\n✓ Session ID: {session_id}")

    # Test 8: Send Chat Message (Non-Streaming)
    print_section("8. Send Chat Message")
    chat_data = {
        "session_id": session_id,
        "message": "What was the Q4 revenue and how did it grow?",
        "stream": False
    }
    print(f"Question: {chat_data['message']}")
    print("\nResponse: ", end="", flush=True)

    response = requests.post(f"{BASE_URL}/api/chat", json=chat_data)
    if response.status_code == 200:
        chat_response = response.json()
        print(chat_response["message"]["content"])
    else:
        print(f"Error: {response.status_code}")

    # Test 9: Get Chat History
    print_section("9. Get Chat History")
    response = requests.get(f"{BASE_URL}/api/chat/sessions/{session_id}/messages")
    print(f"Status: {response.status_code}")
    messages = response.json()
    print(f"Total messages: {len(messages)}")
    for msg in messages:
        print(f"\n{msg['role'].upper()}: {msg['content'][:100]}...")

    # Test 10: List Sessions
    print_section("10. List Chat Sessions")
    response = requests.get(f"{BASE_URL}/api/entities/company_techcorp/sessions")
    print(f"Status: {response.status_code}")
    print(f"Sessions: {json.dumps(response.json(), indent=2)}")

    # Test 11: Get Entity Details (Updated)
    print_section("11. Get Updated Entity Details")
    response = requests.get(f"{BASE_URL}/api/entities/company_techcorp")
    print(f"Status: {response.status_code}")
    print(f"Entity: {json.dumps(response.json(), indent=2)}")

    # Optional: Delete Operations (Commented out to preserve data)
    print_section("Cleanup (Optional)")
    print("Skipping cleanup to preserve test data")
    print("To clean up manually:")
    print(f"  - Delete session: DELETE {BASE_URL}/api/chat/sessions/{session_id}")
    print(f"  - Delete file: DELETE {BASE_URL}/api/entities/company_techcorp/files/{doc_id}")
    print(f"  - Delete entity: DELETE {BASE_URL}/api/entities/company_techcorp")

    '''
    # Uncomment to actually delete

    # Delete Session
    print_section("Delete Session")
    response = requests.delete(f"{BASE_URL}/api/chat/sessions/{session_id}")
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")

    # Delete File
    print_section("Delete File")
    response = requests.delete(f"{BASE_URL}/api/entities/company_techcorp/files/{doc_id}")
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")

    # Delete Entity
    print_section("Delete Entity")
    response = requests.delete(f"{BASE_URL}/api/entities/company_techcorp")
    print(f"Status: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    '''

    print_section("All Tests Completed Successfully! ✓")


if __name__ == "__main__":
    try:
        test_api()
    except requests.exceptions.ConnectionError:
        print("\n✗ Error: Could not connect to API server")
        print("Make sure the API server is running:")
        print("  cd api && python main.py")
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
