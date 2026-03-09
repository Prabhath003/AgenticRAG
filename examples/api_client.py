"""
Comprehensive Python client for AgenticRAG API.

Supports all endpoints including:
- Health checks
- Admin operations (users, API keys)
- Knowledge base management
- Document operations
- Conversation management
- MCP server operations
- Operation tracking
"""

import requests
import json
from typing import Optional, List, Dict, Any, Generator
from pathlib import Path
import asyncio
import websockets
from urllib.parse import urljoin


class APIClientError(Exception):
    """Base exception for API client errors."""

    pass


class APIClient:
    """Comprehensive client for AgenticRAG API."""

    def __init__(self, base_url: str = "http://localhost:8000", api_key: str = ""):
        """
        Initialize API client.

        Args:
            base_url: Base URL of the API server
            api_key: API key for authentication
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self._setup_headers()

    def _setup_headers(self):
        """Setup default headers with API key."""
        self.session.headers.update(
            {
                "X-API-Key": self.api_key,
                "Content-Type": "application/json",
            }
        )

    def set_api_key(self, api_key: str):
        """Update API key."""
        self.api_key = api_key
        self.session.headers["X-API-Key"] = api_key

    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make HTTP request to API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint path
            data: Form data
            json_data: JSON body data
            files: Files to upload
            params: Query parameters

        Returns:
            Response data as dictionary

        Raises:
            APIClientError: If request fails
        """
        url = urljoin(self.base_url, endpoint)

        try:
            if files:
                # Don't set Content-Type when uploading files (let requests handle it)
                headers = dict(self.session.headers)
                headers.pop("Content-Type", None)
                response = self.session.request(
                    method, url, files=files, data=data, params=params, headers=headers
                )
            else:
                response = self.session.request(
                    method,
                    url,
                    json=json_data,
                    data=data,
                    params=params,
                )

            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise APIClientError(f"Request failed: {str(e)}")

    def _make_streaming_request(
        self, method: str, endpoint: str, json_data: Optional[Dict[str, Any]] = None
    ) -> Generator[str, None, None]:
        """
        Make streaming HTTP request (SSE).

        Args:
            method: HTTP method
            endpoint: API endpoint path
            json_data: JSON body data

        Yields:
            Streaming response lines
        """
        url = urljoin(self.base_url, endpoint)

        try:
            response = self.session.request(method, url, json=json_data, stream=True)
            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    yield line.decode("utf-8")
        except requests.exceptions.RequestException as e:
            raise APIClientError(f"Streaming request failed: {str(e)}")

    # =========================================================================
    # HEALTH ENDPOINTS
    # =========================================================================

    def health_check(self) -> Dict[str, Any]:
        """
        Health check endpoint.

        Returns:
            Health status response
        """
        return self._make_request("GET", "/health")

    # =========================================================================
    # ADMIN ENDPOINTS
    # =========================================================================

    def generate_api_key(self, user_id: str, role: str = "user") -> Dict[str, Any]:
        """
        Generate a new API key (requires admin role).

        Args:
            user_id: User ID to generate key for
            role: Role for the key ("user" or "admin")

        Returns:
            Response with generated API key
        """
        return self._make_request(
            "POST",
            "/admin/api_keys/generate",
            json_data={"user_id": user_id, "role": role},
        )

    def list_api_keys(
        self,
        filters: Optional[Dict[str, Any]] = None,
        projections: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        List API keys (requires admin role).

        Args:
            filters: MongoDB-style filters
            projections: MongoDB-style projections

        Returns:
            List of API keys
        """
        return self._make_request(
            "POST",
            "/admin/api_keys/list",
            json_data={
                "filters": filters or {},
                "projections": projections or {},
            },
        )

    def delete_api_keys(self, api_keys: List[str]) -> Dict[str, Any]:
        """
        Delete API keys (requires admin role).

        Args:
            api_keys: List of API keys to delete

        Returns:
            Deletion response
        """
        return self._make_request(
            "POST",
            "/admin/api_keys/delete",
            json_data={"api_keys": api_keys},
        )

    def create_user(
        self, name: Optional[str] = None, email: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a new user (requires admin role).

        Args:
            name: User's full name
            email: User's email address

        Returns:
            Created user response
        """
        return self._make_request(
            "POST",
            "/admin/users/create",
            json_data={
                "name": name,
                "email": email,
            },
        )

    def list_users(
        self,
        filters: Optional[Dict[str, Any]] = None,
        projections: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        List users (requires admin role).

        Args:
            filters: MongoDB-style filters
            projections: MongoDB-style projections

        Returns:
            List of users
        """
        return self._make_request(
            "POST",
            "/admin/users/list",
            json_data={
                "filters": filters or {},
                "projections": projections or {},
            },
        )

    def update_user(
        self, user_id: str, name: Optional[str] = None, email: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update a user (requires admin role).

        Args:
            user_id: User ID to update
            name: New name (optional)
            email: New email (optional)

        Returns:
            Updated user response
        """
        return self._make_request(
            "PUT",
            f"/admin/users/{user_id}",
            json_data={
                "name": name,
                "email": email,
            },
        )

    def delete_user(self, user_id: str) -> Dict[str, Any]:
        """
        Delete a user (requires admin role).

        Args:
            user_id: User ID to delete

        Returns:
            Deletion response
        """
        return self._make_request("DELETE", f"/admin/users/{user_id}")

    # =========================================================================
    # OPERATION ENDPOINTS
    # =========================================================================

    def get_operation_status(self, operation_id: str) -> Dict[str, Any]:
        """
        Get status of an async operation.

        Args:
            operation_id: Operation ID

        Returns:
            Operation status
        """
        return self._make_request("GET", f"/operation/{operation_id}")

    def list_operations(
        self,
        filters: Optional[Dict[str, Any]] = None,
        projections: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        List operations for the authenticated user.

        Args:
            filters: MongoDB-style filters
            projections: MongoDB-style projections

        Returns:
            List of operations
        """
        return self._make_request(
            "POST",
            "/operation/list",
            json_data={
                "filters": filters or {},
                "projections": projections or {},
            },
        )

    def list_services(
        self,
        filters: Optional[Dict[str, Any]] = None,
        projections: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        List services for the authenticated user.

        Args:
            filters: MongoDB-style filters
            projections: MongoDB-style projections

        Returns:
            List of services
        """
        return self._make_request(
            "POST",
            "/operation/services/list",
            json_data={
                "filters": filters or {},
                "projections": projections or {},
            },
        )

    # =========================================================================
    # KNOWLEDGE BASE ENDPOINTS
    # =========================================================================

    def create_knowledge_base(
        self,
        title: str,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new knowledge base.

        Args:
            title: Knowledge base title
            description: Knowledge base description
            metadata: Additional metadata

        Returns:
            Created knowledge base response
        """
        return self._make_request(
            "POST",
            "/knowledge-base/create",
            json_data={
                "title": title,
                "description": description,
                "metadata": metadata or {},
            },
        )

    def get_knowledge_base(self, kb_id: str) -> Dict[str, Any]:
        """
        Get knowledge base information.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Knowledge base details
        """
        return self._make_request("GET", f"/knowledge-base/{kb_id}")

    def list_knowledge_bases(
        self,
        filters: Optional[Dict[str, Any]] = None,
        projections: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        List knowledge bases.

        Args:
            filters: MongoDB-style filters
            projections: MongoDB-style projections

        Returns:
            List of knowledge bases
        """
        return self._make_request(
            "POST",
            "/knowledge-bases/list",
            json_data={
                "filters": filters or {},
                "projections": projections or {},
            },
        )

    def modify_knowledge_base(
        self,
        kb_id: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        metadata_updates: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Modify a knowledge base.

        Args:
            kb_id: Knowledge base ID
            title: New title
            metadata: New metadata (replaces existing)
            metadata_updates: Partial metadata updates

        Returns:
            Updated knowledge base response
        """
        data: Dict[str, Any] = {}
        if title is not None:
            data["title"] = title
        if metadata is not None:
            data["metadata"] = metadata
        if metadata_updates is not None:
            data["metadata_updates"] = metadata_updates

        return self._make_request("PUT", f"/knowledge-base/{kb_id}", json_data=data)

    def delete_knowledge_base(self, kb_id: str) -> Dict[str, Any]:
        """
        Delete a knowledge base.

        Args:
            kb_id: Knowledge base ID

        Returns:
            Deletion response
        """
        return self._make_request("DELETE", f"/knowledge-base/{kb_id}")

    def upload_documents(self, kb_id: str, file_paths: List[str]) -> Dict[str, Any]:
        """
        Upload documents to a knowledge base.

        Args:
            kb_id: Knowledge base ID
            file_paths: List of file paths to upload

        Returns:
            Upload response
        """
        files: Dict[str, Any] = {}
        for file_path in file_paths:
            path = Path(file_path)
            if not path.exists():
                raise APIClientError(f"File not found: {file_path}")
            files[f"files"] = (path.name, open(file_path, "rb"))

        try:
            return self._make_request(
                "POST",
                f"/knowledge-base/{kb_id}/upload",
                files=files if len(files) > 0 else None,
            )
        finally:
            for file_tuple in files.values():
                file_tuple[1].close()

    def delete_documents_from_kb(self, kb_id: str, doc_ids: List[str]) -> Dict[str, Any]:
        """
        Delete documents from a knowledge base.

        Args:
            kb_id: Knowledge base ID
            doc_ids: List of document IDs to delete

        Returns:
            Deletion response
        """
        return self._make_request(
            "DELETE",
            f"/knowledge-base/{kb_id}/documents",
            json_data={"doc_ids": doc_ids},
        )

    def upload_chunks(self, kb_id: str, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Upload pre-chunked data to a knowledge base.

        Args:
            kb_id: Knowledge base ID
            chunks: List of chunk dictionaries

        Returns:
            Upload response
        """
        return self._make_request(
            "POST",
            f"/knowledge-base/{kb_id}/upload-chunks",
            json_data={"chunks": chunks},
        )

    # =========================================================================
    # DOCUMENT ENDPOINTS
    # =========================================================================

    def get_document(self, doc_id: str) -> Dict[str, Any]:
        """
        Get document metadata.

        Args:
            doc_id: Document ID

        Returns:
            Document metadata
        """
        return self._make_request("GET", f"/documents/{doc_id}")

    def download_document(self, doc_id: str, output_path: Optional[str] = None) -> bytes:
        """
        Download a document file.

        Args:
            doc_id: Document ID
            output_path: Optional file path to save to

        Returns:
            Document file content
        """
        url = urljoin(self.base_url, f"/documents/{doc_id}/download")

        try:
            response = self.session.get(url)
            response.raise_for_status()

            if output_path:
                with open(output_path, "wb") as f:
                    f.write(response.content)

            return response.content
        except requests.exceptions.RequestException as e:
            raise APIClientError(f"Download failed: {str(e)}")

    def get_document_presigned_url(
        self, doc_id: str, expiration: int = 3600, inline: bool = False
    ) -> Dict[str, Any]:
        """
        Get a presigned URL for document download.

        Args:
            doc_id: Document ID
            expiration: URL expiration in seconds (max 3600)
            inline: Whether to serve inline (True) or as attachment (False)

        Returns:
            Presigned URL response
        """
        return self._make_request(
            "POST",
            f"/documents/{doc_id}/presigned-url",
            json_data={
                "expiration": min(expiration, 3600),
                "inline": inline,
            },
        )

    def search_documents(
        self,
        filters: Dict[str, Any],
        projections: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Search documents with filters.

        Args:
            filters: MongoDB-style filters (required)
            projections: MongoDB-style projections

        Returns:
            Search results
        """
        return self._make_request(
            "POST",
            "/documents/search",
            json_data={
                "filters": filters,
                "projections": projections or {},
            },
        )

    def download_documents_batch(self, filters: Dict[str, Any]) -> bytes:
        """
        Download multiple documents as zip.

        Args:
            filters: MongoDB-style filters to select documents

        Returns:
            Zip file content
        """
        url = urljoin(self.base_url, "/documents/batch/download")

        try:
            response = self.session.post(url, json={"filters": filters})
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            raise APIClientError(f"Batch download failed: {str(e)}")

    # =========================================================================
    # CONVERSATION ENDPOINTS
    # =========================================================================

    def create_conversation(
        self,
        name: Optional[str] = None,
        kb_ids: Optional[List[str]] = None,
        user_instructions: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a conversation session.

        Args:
            name: Conversation name
            kb_ids: List of knowledge base IDs
            user_instructions: User instructions/context
            settings: Conversation settings

        Returns:
            Created conversation response
        """
        return self._make_request(
            "POST",
            "/conversation/create",
            json_data={
                "name": name,
                "kb_ids": kb_ids or [],
                "user_instructions": user_instructions,
                "settings": settings or {},
            },
        )

    def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        """
        Get conversation details.

        Args:
            conversation_id: Conversation ID

        Returns:
            Conversation details
        """
        return self._make_request("GET", f"/conversation/{conversation_id}")

    def list_conversations(
        self,
        filters: Optional[Dict[str, Any]] = None,
        projections: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        List conversations.

        Args:
            filters: MongoDB-style filters
            projections: MongoDB-style projections

        Returns:
            List of conversations
        """
        return self._make_request(
            "POST",
            "/conversation/list",
            json_data={
                "filters": filters or {},
                "projections": projections or {},
            },
        )

    def submit_message(
        self,
        conversation_id: str,
        prompt: str,
        parent_message_uuid: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Submit a message to conversation queue.

        Args:
            conversation_id: Conversation ID
            prompt: User message
            parent_message_uuid: Parent message UUID for threading
            attachments: File attachments

        Returns:
            Submission response
        """
        return self._make_request(
            "POST",
            f"/conversation/{conversation_id}/submit-message",
            json_data={
                "prompt": prompt,
                "parent_message_uuid": parent_message_uuid,
                "attachments": attachments or [],
            },
        )

    def converse_stream(
        self, conversation_id: str, prompt: str
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Start conversation with streaming response (SSE).

        Args:
            conversation_id: Conversation ID
            prompt: User message

        Yields:
            Streaming response events as dictionaries
        """
        for line in self._make_streaming_request(
            "POST",
            f"/conversation/{conversation_id}/converse",
            json_data={"prompt": prompt},
        ):
            if line.startswith("event:"):
                continue
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                    yield data
                except json.JSONDecodeError:
                    continue

    def process_messages_stream(
        self, conversation_id: str
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Process queued messages with streaming response (SSE).

        Args:
            conversation_id: Conversation ID

        Yields:
            Streaming response events as dictionaries
        """
        for line in self._make_streaming_request(
            "POST",
            f"/conversation/{conversation_id}/process",
        ):
            if line.startswith("event:"):
                continue
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                    yield data
                except json.JSONDecodeError:
                    continue

    async def converse_websocket(self, conversation_id: str) -> None:
        """
        WebSocket conversation endpoint for concurrent operations.

        Args:
            conversation_id: Conversation ID
        """
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        uri = f"{ws_url}/conversation/ws/{conversation_id}"
        headers = [("X-API-Key", self.api_key)]

        try:
            async with websockets.connect(uri, extra_headers=headers) as websocket:
                # Receive messages from the server
                while True:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=30.0)
                        data = json.loads(message)
                        print(f"Received: {data}")
                    except asyncio.TimeoutError:
                        print("Connection timeout")
                        break
        except Exception as e:
            raise APIClientError(f"WebSocket error: {str(e)}")

    # =========================================================================
    # MCP SERVER ENDPOINTS
    # =========================================================================

    def get_tools(self) -> Dict[str, Any]:
        """
        Get available MCP tools.

        Returns:
            List of available tools
        """
        return self._make_request("GET", "/mcp-server/get-tools")

    def execute_tool(
        self, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute an MCP tool.

        Args:
            tool_name: Tool name to execute
            arguments: Tool arguments

        Returns:
            Tool execution response
        """
        return self._make_request(
            "POST",
            "/mcp-server/execute-tool",
            json_data={
                "tool_name": tool_name,
                "arguments": arguments or {},
            },
        )

    def search_tools(self, query: str) -> Dict[str, Any]:
        """
        Search MCP tools.

        Args:
            query: Search query

        Returns:
            Search results
        """
        return self._make_request(
            "POST",
            "/mcp-server/search-tools",
            json_data={"query": query},
        )


# ============================================================================
# EXAMPLE USAGE
# ============================================================================


if __name__ == "__main__":
    # Initialize client
    client = APIClient(base_url="http://localhost:8000", api_key="your-api-key")

    # Example: Health check
    try:
        health = client.health_check()
        print(f"Health: {health}")
    except APIClientError as e:
        print(f"Error: {e}")

    # Example: List knowledge bases
    try:
        kbs = client.list_knowledge_bases()
        print(f"Knowledge bases: {kbs}")
    except APIClientError as e:
        print(f"Error: {e}")

    # Example: Create knowledge base
    try:
        kb = client.create_knowledge_base(
            title="My Knowledge Base",
            description="Test KB",
        )
        print(f"Created KB: {kb}")
    except APIClientError as e:
        print(f"Error: {e}")

    # Example: Upload documents
    try:
        result = client.upload_documents("kb_id", ["./test.pdf"])
        print(f"Upload result: {result}")
    except APIClientError as e:
        print(f"Error: {e}")

    # Example: Streaming conversation
    try:
        for response in client.converse_stream("conv_id", "Hello"):
            print(f"Response: {response}")
    except APIClientError as e:
        print(f"Error: {e}")
