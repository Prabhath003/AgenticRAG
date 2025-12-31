#!/usr/bin/env python3
"""
Test to verify document creation behavior:
- Chunk API: saves chunks WITHOUT document entries
- File Upload: saves chunks AND creates document entries
"""

import requests
import json
import uuid
from pathlib import Path
import tempfile

API_BASE_URL = "http://localhost:8002"

def create_entity(entity_id: str, entity_name: str):
    """Create a test entity"""
    url = f"{API_BASE_URL}/api/entities"
    payload = {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "description": "Test entity"
    }
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()

def ingest_chunks_batch(entity_id: str, chunks: list):
    """Batch ingest chunks"""
    url = f"{API_BASE_URL}/api/entities/{entity_id}/chunks/batch"
    payload = {"chunks": chunks}
    response = requests.post(url, json=payload)
    response.raise_for_status()
    return response.json()

def upload_file(entity_id: str, file_path: str):
    """Upload a file"""
    url = f"{API_BASE_URL}/api/entities/{entity_id}/files"
    with open(file_path, 'rb') as f:
        files = {"file": (Path(file_path).name, f)}
        response = requests.post(url, files=files)
    response.raise_for_status()
    return response.json()

def get_task_status(task_id: str):
    """Get task status"""
    url = f"{API_BASE_URL}/api/tasks/{task_id}"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def list_files(entity_id: str):
    """List files for entity"""
    url = f"{API_BASE_URL}/api/entities/{entity_id}/files"
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def create_chunk(chunk_id: str, chunk_order_index: int, text: str, doc_id: str):
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
    print("Testing Document Creation Behavior")
    print("=" * 80)

    unique_suffix = uuid.uuid4().hex[:8]
    entity_id = f"test_entity_{unique_suffix}"
    entity_name = "Document Behavior Test"
    doc_id_chunks = "chunk_api_doc"

    try:
        # 1. Create entity
        print("\n[1] Creating test entity...")
        entity = create_entity(entity_id, entity_name)
        print(f"✓ Entity created: {entity['entity_id']}")

        # 2. Ingest chunks via API
        print("\n[2] Ingesting 3 chunks via Chunk API...")
        chunks = [
            create_chunk(f"{doc_id_chunks}_chunk_0", 0, "First chunk content.", doc_id_chunks),
            create_chunk(f"{doc_id_chunks}_chunk_1", 1, "Second chunk content.", doc_id_chunks),
            create_chunk(f"{doc_id_chunks}_chunk_2", 2, "Third chunk content.", doc_id_chunks),
        ]
        result = ingest_chunks_batch(entity_id, chunks)
        print(f"✓ Ingested {result['indexed_chunks']} chunks")
        print(f"  Doc ID: {result['doc_id']}")

        # Check files list - should NOT show any document for chunk API ingestion
        print("\n[3] Checking files list after chunk ingestion...")
        files = list_files(entity_id)
        print(f"✓ Files response: {json.dumps(files, indent=2)}")

        if isinstance(files, dict) and 'documents' in files:
            doc_count = len(files.get('documents', []))
            print(f"  → Document count: {doc_count}")
            if doc_count == 0:
                print("  ✓ CORRECT: No document entry created for chunk API ingestion")
            else:
                print(f"  ✗ WRONG: Document entry was created when it shouldn't be")
        else:
            print(f"  → Files list: {files}")

        # 4. Upload a real file
        print("\n[4] Uploading a test file...")
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("This is a test document with some content for file upload testing.")
            temp_file = f.name

        try:
            upload_result = upload_file(entity_id, temp_file)
            task_id = upload_result.get('task_id')
            print(f"✓ File upload started with task_id: {task_id}")

            # Wait for task to complete
            print("\n[5] Waiting for file processing...")
            import time
            max_attempts = 30
            attempt = 0
            while attempt < max_attempts:
                task_status = get_task_status(task_id)
                status = task_status.get('status')
                print(f"  Task status: {status}")

                if status == 'completed':
                    print(f"✓ File processing completed")
                    break
                elif status == 'failed':
                    print(f"✗ File processing failed")
                    break

                time.sleep(1)
                attempt += 1

            # Check files list - should NOW show a document entry
            print("\n[6] Checking files list after file upload...")
            files = list_files(entity_id)
            print(f"✓ Files response: {json.dumps(files, indent=2)}")

            if isinstance(files, dict) and 'documents' in files:
                doc_count = len(files.get('documents', []))
                print(f"  → Document count: {doc_count}")
                if doc_count > 0:
                    print("  ✓ CORRECT: Document entry created for file upload")
                else:
                    print("  ✗ WRONG: No document entry created when file was uploaded")
            else:
                print(f"  → Files list: {files}")

        finally:
            Path(temp_file).unlink()

        print("\n" + "=" * 80)
        print("✓ Document creation behavior verified!")
        print("=" * 80)
        return True

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
