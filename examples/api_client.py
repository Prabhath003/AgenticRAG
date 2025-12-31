"""
Simple API Client for Entity-Scoped RAG System
"""

import requests
import time
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from dataclasses import dataclass

@dataclass
class File:
    filename: str
    content: bytes

class RAGClient:
    """Simple client for Entity-Scoped RAG API"""
    
    def __init__(self, base_url: str = "http://localhost:8002", poll_interval: float = 2.0):
        """
        Initialize RAG client

        Args:
            base_url: Base URL of the API server
            poll_interval: How often to poll for task status in seconds (default: 2.0)
        """
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.poll_interval = poll_interval
    
    # Entity Management
    def create_entity(self, entity_id: str, entity_name: str, 
                     description: Optional[str] = None, 
                     metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a new entity"""
        response = self.session.post(
            f"{self.base_url}/api/entities",
            json={
                "entity_id": entity_id,
                "entity_name": entity_name,
                "description": description,
                "metadata": metadata
            }
        )
        response.raise_for_status()
        return response.json()
    
    def get_entity(self, entity_id: str) -> Dict[str, Any]:
        """Get entity details"""
        response = self.session.get(f"{self.base_url}/api/entities/{entity_id}")
        response.raise_for_status()
        return response.json()
    
    def list_entities(self) -> List[Dict[str, Any]]:
        """List all entities"""
        response = self.session.get(f"{self.base_url}/api/entities")
        response.raise_for_status()
        return response.json()["entities"]
    
    def delete_entity(self, entity_id: str) -> Dict[str, Any]:
        """Delete an entity"""
        response = self.session.delete(f"{self.base_url}/api/entities/{entity_id}")
        response.raise_for_status()
        return response.json()
    
    # File Management
    def upload_file_async(
        self,
        entity_id: str,
        file_path: str,
        description: Optional[str] = None,
        source: Optional[str] = None
    ) -> str:
        """
        Upload a file asynchronously (returns task_id immediately).

        Args:
            entity_id: Entity identifier
            file_path: Path to the file to upload
            description: Optional description
            source: Optional source identifier

        Returns:
            Task ID string

        Raises:
            FileNotFoundError: If file doesn't exist
            requests.exceptions.RequestException: If API request fails
        """
        file_path_obj = Path(file_path)

        if not file_path_obj.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path_obj, 'rb') as f:
            files = {'file': (file_path_obj.name, f.read())}
            data: Dict[str, Any] = {}
            if description:
                data['description'] = description
            if source:
                data['source'] = source

            response = self.session.post(
                f"{self.base_url}/api/entities/{entity_id}/files",
                files=files,
                data=data
            )

        response.raise_for_status()
        result = response.json()
        return result['task_id']

    def upload_file(
        self,
        entity_id: str,
        file_path: str,
        description: Optional[str] = None,
        source: Optional[str] = None,
        wait: bool = True,
        poll_interval: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Upload a file to an entity.

        Args:
            entity_id: Entity identifier
            file_path: Path to the file to upload
            description: Optional description
            source: Optional source identifier
            wait: If True, wait for upload to complete (default: True)
            poll_interval: Override default poll interval

        Returns:
            If wait=True: Dictionary with upload result and task status
            If wait=False: Dictionary with task_id and status URL

        Raises:
            FileNotFoundError: If file doesn't exist
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> client = RAGClient()
            >>> result = client.upload_file("entity_123", "document.pdf", source="docs/2024")
            >>> print(result['task_id'])
        """
        task_id = self.upload_file_async(entity_id, file_path, description, source)

        if not wait:
            return {
                "task_id": task_id,
                "status_url": f"/tasks/{task_id}"
            }

        return self.wait_for_upload(task_id, poll_interval=poll_interval)

    def upload_file_from_bytes(
        self,
        entity_id: str,
        file_content: bytes,
        filename: str,
        description: Optional[str] = None,
        source: Optional[str] = None,
        wait: bool = True,
        poll_interval: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Upload file content from bytes (useful when you already have file in memory).

        Args:
            entity_id: Entity identifier
            file_content: File content as bytes
            filename: Original filename
            description: Optional description
            source: Optional source identifier
            wait: If True, wait for upload to complete (default: True)
            poll_interval: Override default poll interval

        Returns:
            If wait=True: Dictionary with upload result and task status
            If wait=False: Dictionary with task_id and status URL

        Raises:
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> with open('document.pdf', 'rb') as f:
            ...     content = f.read()
            >>> result = client.upload_file_from_bytes("entity_123", content, 'document.pdf')
        """
        files = {'file': (filename, file_content)}
        data: Dict[str, Any] = {}
        if description:
            data['description'] = description
        if source:
            data['source'] = source

        response = self.session.post(
            f"{self.base_url}/api/entities/{entity_id}/files",
            files=files,
            data=data
        )

        response.raise_for_status()
        result = response.json()
        task_id = result['task_id']

        if not wait:
            return {
                "task_id": task_id,
                "status_url": f"/tasks/{task_id}"
            }

        return self.wait_for_upload(task_id, poll_interval=poll_interval)

    def wait_for_upload(
        self,
        task_id: str,
        poll_interval: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Wait for an upload task to complete and return the result.

        Args:
            task_id: The task ID to wait for
            poll_interval: Override default poll interval

        Returns:
            Task result when completed

        Raises:
            requests.exceptions.RequestException: If API request fails
        """
        poll_interval = poll_interval or self.poll_interval

        while True:
            try:
                # Check task status
                status = self.get_task_status(task_id)
                task_status = status.get("status")

                # Check if task is complete
                if task_status in ["completed", "COMPLETED"]:
                    return status

                if task_status in ["failed", "FAILED"]:
                    raise Exception(f"File upload failed: {status.get('error', 'Unknown error')}")

                # Task still processing
                time.sleep(poll_interval)

            except requests.exceptions.RequestException as e:
                # Only re-raise connection/network errors
                if isinstance(e, (requests.exceptions.ConnectionError,
                                  requests.exceptions.Timeout)):
                    raise
                # For other HTTP errors, retry after waiting
                time.sleep(poll_interval)
                continue

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get upload task status"""
        response = self.session.get(f"{self.base_url}/api/tasks/{task_id}")
        response.raise_for_status()
        return response.json()
    
    def list_files(self, entity_id: str) -> List[Dict[str, Any]]:
        """List all files for an entity"""
        response = self.session.get(f"{self.base_url}/api/entities/{entity_id}/files")
        response.raise_for_status()
        return response.json()

    # def delete_file(self, entity_id: str, doc_id: str) -> Dict[str, Any]:
    #     """Delete a file"""
    #     response = self.session.delete(
    #         f"{self.base_url}/api/entities/{entity_id}/files/{doc_id}"
    #     )
    #     response.raise_for_status()
    #     return response.json()

    # Chunk Management (Direct Chunk Ingestion API)

    def ingest_chunk(
        self,
        entity_id: str,
        chunk_id: str,
        text: str,
        doc_id: str,
        chunk_order_index: int = 0,
        tokens: int = 0,
        source: Optional[str] = None,
        filename: Optional[str] = None,
        pages: Optional[List[int]] = None,
        processed_by: str = "API"
    ) -> Dict[str, Any]:
        """
        Ingest a single chunk with automatic duplicate detection.

        Args:
            entity_id: Entity identifier
            chunk_id: Unique chunk identifier (managed by client)
            text: Markdown text content of the chunk
            doc_id: Document ID this chunk belongs to
            chunk_order_index: Order index of this chunk in the document
            tokens: Token count of the chunk
            source: Source identifier (default: entity_id)
            filename: Original filename (default: 'chunk.txt')
            pages: List of page numbers (default: [])
            processed_by: Processor name (default: 'API')

        Returns:
            Dictionary with chunk_id, doc_id, indexed (bool), and message

        Example:
            >>> client = RAGClient()
            >>> result = client.ingest_chunk(
            ...     entity_id="company_123",
            ...     chunk_id="chunk_001",
            ...     text="Company financial overview...",
            ...     doc_id="doc_abc123"
            ... )
            >>> print(f"Indexed: {result['indexed']}")
        """
        chunk_data = {
            "chunk_id": chunk_id,
            "markdown": {
                "text": text,
                "chunk_order_index": chunk_order_index,
                "source": source or entity_id,
                "filename": filename or "chunk.txt",
                "pages": pages or []
            },
            "metadata": {
                "chunk_index": chunk_order_index,
                "tokens": tokens,
                "processed_by": processed_by,
                "doc_id": doc_id,
                "entity_id": entity_id
            }
        }

        response = self.session.post(
            f"{self.base_url}/api/entities/{entity_id}/chunks",
            json=chunk_data
        )
        response.raise_for_status()
        return response.json()

    def ingest_chunks_batch(
        self,
        entity_id: str,
        chunks: List[Dict[str, Any]],
        doc_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Batch ingest multiple chunks with automatic duplicate detection.

        All chunks must belong to the same document (same doc_id).

        Args:
            entity_id: Entity identifier
            chunks: List of chunk dictionaries, each containing:
                - chunk_id: Unique chunk identifier
                - text: Markdown text content
                - chunk_order_index: Order in document
                - tokens: Token count
                - source: (optional) Source identifier
                - filename: (optional) Original filename
                - pages: (optional) Page numbers
                - processed_by: (optional) Processor name
            doc_id: Document ID (required if not in chunk metadata)

        Returns:
            Dictionary with total_chunks, indexed_chunks, duplicate_chunks, and message

        Example:
            >>> chunks = [
            ...     {
            ...         "chunk_id": "chunk_001",
            ...         "text": "First chunk...",
            ...         "chunk_order_index": 0,
            ...         "tokens": 50
            ...     },
            ...     {
            ...         "chunk_id": "chunk_002",
            ...         "text": "Second chunk...",
            ...         "chunk_order_index": 1,
            ...         "tokens": 60
            ...     }
            ... ]
            >>> result = client.ingest_chunks_batch("company_123", chunks, "doc_abc123")
            >>> print(f"Indexed: {result['indexed_chunks']}, Duplicates: {result['duplicate_chunks']}")
        """
        # Format chunks
        formatted_chunks = []
        for chunk in chunks:
            chunk_id = chunk.get("chunk_id")
            text = chunk.get("text", "")
            chunk_order_index = chunk.get("chunk_order_index", 0)
            tokens = chunk.get("tokens", 0)
            source = chunk.get("source", entity_id)
            filename = chunk.get("filename", "chunk.txt")
            pages = chunk.get("pages", [])
            processed_by = chunk.get("processed_by", "API")
            chunk_doc_id = chunk.get("doc_id", doc_id)

            if not chunk_id or not chunk_doc_id:
                raise ValueError("chunk_id and doc_id are required for all chunks")

            formatted_chunk = {
                "chunk_id": chunk_id,
                "markdown": {
                    "text": text,
                    "chunk_order_index": chunk_order_index,
                    "source": source,
                    "filename": filename,
                    "pages": pages
                },
                "metadata": {
                    "chunk_index": chunk_order_index,
                    "tokens": tokens,
                    "processed_by": processed_by,
                    "doc_id": chunk_doc_id,
                    "entity_id": entity_id
                }
            }
            formatted_chunks.append(formatted_chunk)

        request_data = {"chunks": formatted_chunks}

        response = self.session.post(
            f"{self.base_url}/api/entities/{entity_id}/chunks/batch",
            json=request_data
        )
        response.raise_for_status()
        return response.json()

    # Chat Management
    
    def create_session(self, entity_id: str, session_name: Optional[str] = None,
                      metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a chat session"""
        response = self.session.post(
            f"{self.base_url}/api/chat/sessions",
            json={
                "entity_id": entity_id,
                "session_name": session_name,
                "metadata": metadata
            }
        )
        response.raise_for_status()
        return response.json()
    
    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get session details"""
        response = self.session.get(f"{self.base_url}/api/chat/sessions/{session_id}")
        response.raise_for_status()
        return response.json()
    
    def list_sessions(self, entity_id: str) -> List[Dict[str, Any]]:
        """List all sessions for an entity"""
        response = self.session.get(f"{self.base_url}/api/entities/{entity_id}/sessions")
        response.raise_for_status()
        return response.json()
    
    def delete_session(self, session_id: str) -> Dict[str, Any]:
        """Delete a session"""
        response = self.session.delete(f"{self.base_url}/api/chat/sessions/{session_id}")
        response.raise_for_status()
        return response.json()
    
    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Get chat history"""
        response = self.session.get(
            f"{self.base_url}/api/chat/sessions/{session_id}/messages"
        )
        response.raise_for_status()
        return response.json()
    
    def chat(self, session_id: str, message: str, stream: bool = False) -> Any:
        """
        Send a chat message

        Returns:
            For non-streaming: Dict with 'message' (ChatMessage), 'node_ids', 'relationship_ids', 'cited_node_ids', 'citations'
            For streaming: Iterator of content
        """
        response = self.session.post(
            f"{self.base_url}/api/chat",
            json={
                "session_id": session_id,
                "message": message,
                "stream": stream
            },
            stream=stream
        )
        response.raise_for_status()

        if stream:
            return response.iter_content(decode_unicode=True)

        # Parse the response to extract tracking information
        response_data = response.json()
        return response_data
    
    # Search
    # Note: Search endpoint is disabled in the API
    # def search(self, entity_id: str, query: str, k: int = 5,
    #           doc_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    #     """Search entity documents"""
    #     response = self.session.post(
    #         f"{self.base_url}/api/search",
    #         json={
    #             "entity_id": entity_id,
    #             "query": query,
    #             "k": k,
    #             "doc_ids": doc_ids
    #         }
    #     )
    #     response.raise_for_status()
    #     return response.json()
    
    def get_knowledge_graph(self, entity_ids: List[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        response = self.session.get(
            f"{self.base_url}/api/knowledge-graph",
            json={
                "entity_ids": entity_ids
            }
        )
        response.raise_for_status()
        return response.json().get("nodes", []), response.json().get("relationships", [])
    
    # System
    def health(self) -> Dict[str, Any]:
        """Check API health"""
        response = self.session.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()
    
    # def worker_status(self) -> Dict[str, Any]:
    #     """Get worker status"""
    #     response = self.session.get(f"{self.base_url}/api/status/workers")
    #     response.raise_for_status()
    #     return response.json()


# Usage Examples
if __name__ == "__main__":
    import uuid
    import time

    # Initialize client
    client = RAGClient(base_url="http://localhost:8002")

    # Generate unique entity ID for this run
    unique_suffix = uuid.uuid4().hex[:8]
    entity_id = f"company_{unique_suffix}"

    # ============================================
    # Example 1: Single Chunk Ingestion
    # ============================================
    print("\n=== Single Chunk Ingestion ===")
    try:
        # Create an entity first
        entity = client.create_entity(entity_id, "TechCorp Inc")
        print(f"Created entity: {entity['entity_id']}")

        # Ingest a single chunk
        chunk_result = client.ingest_chunk(
            entity_id=entity_id,
            chunk_id="chunk_001",
            text="TechCorp Inc is a leading technology company founded in 2020...",
            doc_id="doc_financial_2024",
            chunk_order_index=0,
            tokens=45,
            pages=[1]
        )
        print(f"Chunk ingestion result: {chunk_result}")
        print(f"  - Indexed: {chunk_result['indexed']}")
        print(f"  - Message: {chunk_result['message']}")

        # Try ingesting the same chunk again (should detect as duplicate)
        dup_result = client.ingest_chunk(
            entity_id=entity_id,
            chunk_id="chunk_001",  # Same chunk_id
            text="TechCorp Inc is a leading technology company founded in 2020...",
            doc_id="doc_financial_2024",
            chunk_order_index=0,
            tokens=45
        )
        print(f"Duplicate detection: {dup_result}")
        print(f"  - Indexed: {dup_result['indexed']} (should be False)")

    except Exception as e:
        print(f"Error in single chunk example: {e}")

    # ============================================
    # Example 2: Batch Chunk Ingestion
    # ============================================
    print("\n=== Batch Chunk Ingestion ===")
    try:
        # Prepare multiple chunks for batch ingestion
        chunks = [
            {
                "chunk_id": "batch_chunk_001",
                "text": "Financial Overview: Total revenue for Q1 2024 was $5.2M...",
                "chunk_order_index": 1,
                "tokens": 52,
                "pages": [2]
            },
            {
                "chunk_id": "batch_chunk_002",
                "text": "Product Portfolio: Our main products include...",
                "chunk_order_index": 2,
                "tokens": 48,
                "pages": [3]
            },
            {
                "chunk_id": "batch_chunk_003",
                "text": "Market Position: We maintain leadership in enterprise solutions...",
                "chunk_order_index": 3,
                "tokens": 51,
                "pages": [4]
            }
        ]

        # Ingest batch of chunks
        batch_result = client.ingest_chunks_batch(
            entity_id=entity_id,
            chunks=chunks,
            doc_id="doc_financial_2024"
        )
        print(f"Batch ingestion result:")
        print(f"  - Total chunks: {batch_result['total_chunks']}")
        print(f"  - Indexed: {batch_result['indexed_chunks']}")
        print(f"  - Duplicates: {batch_result['duplicate_chunks']}")
        print(f"  - Message: {batch_result['message']}")

        # Ingest batch with mix of duplicates and new chunks
        mixed_batch = chunks[:2] + [
            {
                "chunk_id": "batch_chunk_004",
                "text": "Future Plans: We plan to expand into emerging markets...",
                "chunk_order_index": 4,
                "tokens": 44,
                "pages": [5]
            }
        ]

        mixed_result = client.ingest_chunks_batch(
            entity_id=entity_id,
            chunks=mixed_batch,
            doc_id="doc_financial_2024"
        )
        print(f"\nMixed batch result (2 duplicates + 1 new):")
        print(f"  - Total chunks: {mixed_result['total_chunks']}")
        print(f"  - Indexed: {mixed_result['indexed_chunks']}")
        print(f"  - Duplicates: {mixed_result['duplicate_chunks']}")

    except Exception as e:
        print(f"Error in batch chunk example: {e}")

    # ============================================
    # Example 3: Check Health
    # ============================================
    print("\n=== Health Check ===")
    try:
        health = client.health()
        print(f"API Status: {health['status']}")
        print(f"Entities loaded: {health['entities_loaded']}")
        print(f"Total documents: {health['total_documents']}")
    except Exception as e:
        print(f"Error in health check: {e}")