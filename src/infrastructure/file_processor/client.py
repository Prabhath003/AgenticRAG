"""
API Client Helper for File Processing Service

This module provides helper functions to interact with the file processing API
from external applications or services.

Usage:
    from tests.api_client import FileProcessorClient

    # Initialize client
    client = FileProcessorClient(base_url="http://localhost:8003")

    # Parse a file
    result = client.parse_file("path/to/document.pdf", source="my-app/docs")

    # Chunk a file
    result = client.chunk_file("path/to/document.md", source="my-app/content")
"""

import requests
from pathlib import Path
from typing import Dict, Any, Optional
import mimetypes


class FileProcessorClient:
    """Client for interacting with the file processing API."""

    def __init__(self, base_url: str = "http://localhost:8003", timeout: int = 300):
        """
        Initialize the file processor client.

        Args:
            base_url: Base URL of the API server (default: http://localhost:8003)
            timeout: Request timeout in seconds (default: 300)
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

    def _get_mime_type(self, file_path: str) -> str:
        """
        Detect MIME type from file extension.

        Args:
            file_path: Path to the file

        Returns:
            MIME type string
        """
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type:
            return mime_type

        # Fallback mappings for common types
        ext = Path(file_path).suffix.lower()
        mime_map = {
            '.json': 'application/json',
            '.md': 'text/markdown',
            '.txt': 'text/plain',
            '.pdf': 'application/pdf',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.xls': 'application/vnd.ms-excel',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        }
        return mime_map.get(ext, 'application/octet-stream')

    def health_check(self) -> Dict[str, Any]:
        """
        Check if the API server is healthy.

        Returns:
            Health status response

        Raises:
            requests.exceptions.RequestException: If request fails
        """
        response = requests.get(f"{self.base_url}/health", timeout=10)
        response.raise_for_status()
        return response.json()

    def parse_file(
        self,
        file_path: str,
        source: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Parse a file and return markdown content.

        Args:
            file_path: Path to the file to parse
            source: Source identifier (e.g., 'my-app/documents/123')

        Returns:
            Dictionary containing:
                - success: bool
                - filename: str
                - file_type: str
                - file_hash: str
                - markdown: str
                - cache_status: dict

        Raises:
            FileNotFoundError: If file doesn't exist
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> client = FileProcessorClient()
            >>> result = client.parse_file("document.pdf", source="docs/2024")
            >>> print(result['markdown'])
        """
        file_path_obj = Path(file_path)

        if not file_path_obj.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Read file content
        with open(file_path_obj, 'rb') as f:
            file_content = f.read()

        # Detect MIME type
        mime_type = self._get_mime_type(str(file_path_obj))

        # Prepare files dict
        files = {
            'file': (file_path_obj.name, file_content, mime_type)
        }

        # Prepare params
        params = {}
        if source:
            params['source'] = source

        # Make request
        response = requests.post(
            f"{self.base_url}/parse",
            files=files,
            params=params,
            timeout=self.timeout
        )

        response.raise_for_status()
        return response.json()

    def chunk_file(
        self,
        file_path: str,
        source: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Parse and chunk a file into smaller segments.

        Args:
            file_path: Path to the file to chunk
            source: Source identifier (e.g., 'my-app/documents/123')

        Returns:
            Dictionary containing:
                - success: bool
                - filename: str
                - file_type: str
                - file_hash: str
                - chunk_count: int
                - chunks: List[Dict] - List of chunk objects
                - cache_status: dict

        Raises:
            FileNotFoundError: If file doesn't exist
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> client = FileProcessorClient()
            >>> result = client.chunk_file("large_doc.md", source="docs/2024")
            >>> for chunk in result['chunks']:
            ...     print(f"Chunk {chunk['chunk_index']}: {chunk['chunk']['content'][:100]}")
        """
        file_path_obj = Path(file_path)

        if not file_path_obj.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Read file content
        with open(file_path_obj, 'rb') as f:
            file_content = f.read()

        # Detect MIME type
        mime_type = self._get_mime_type(str(file_path_obj))

        # Prepare files dict
        files = {
            'file': (file_path_obj.name, file_content, mime_type)
        }

        # Prepare params
        params = {}
        if source:
            params['source'] = source

        # Make request
        response = requests.post(
            f"{self.base_url}/chunk",
            files=files,
            params=params,
            timeout=self.timeout
        )

        response.raise_for_status()
        return response.json()

    def parse_file_from_bytes(
        self,
        file_content: bytes,
        filename: str,
        source: Optional[str] = None,
        mime_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Parse file content from bytes (useful when you already have file in memory).

        Args:
            file_content: File content as bytes
            filename: Original filename (used for extension detection)
            source: Source identifier
            mime_type: Optional MIME type (auto-detected if not provided)

        Returns:
            Dictionary with parse results (same as parse_file)

        Raises:
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> with open('document.pdf', 'rb') as f:
            ...     content = f.read()
            >>> result = client.parse_file_from_bytes(content, 'document.pdf', source='app/docs')
        """
        if not mime_type:
            mime_type = self._get_mime_type(filename)

        files = {
            'file': (filename, file_content, mime_type)
        }

        params = {}
        if source:
            params['source'] = source

        response = requests.post(
            f"{self.base_url}/parse",
            files=files,
            params=params,
            timeout=self.timeout
        )

        response.raise_for_status()
        return response.json()

    def chunk_file_from_bytes(
        self,
        file_content: bytes,
        filename: str,
        source: Optional[str] = None,
        mime_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Chunk file content from bytes.

        Args:
            file_content: File content as bytes
            filename: Original filename (used for extension detection)
            source: Source identifier
            mime_type: Optional MIME type (auto-detected if not provided)

        Returns:
            Dictionary with chunk results (same as chunk_file)

        Raises:
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> with open('document.md', 'rb') as f:
            ...     content = f.read()
            >>> result = client.chunk_file_from_bytes(content, 'document.md', source='app/content')
        """
        if not mime_type:
            mime_type = self._get_mime_type(filename)

        files = {
            'file': (filename, file_content, mime_type)
        }

        params = {}
        if source:
            params['source'] = source

        response = requests.post(
            f"{self.base_url}/chunk",
            files=files,
            params=params,
            timeout=self.timeout
        )

        response.raise_for_status()
        return response.json()


# Convenience functions for quick usage
def parse_file(
    file_path: str,
    source: Optional[str] = None,
    base_url: str = "http://localhost:8003"
) -> Dict[str, Any]:
    """
    Quick helper to parse a file without instantiating a client.

    Args:
        file_path: Path to file
        source: Source identifier
        base_url: API server URL

    Returns:
        Parse result dictionary

    Example:
        >>> from tests.api_client import parse_file
        >>> result = parse_file("document.pdf", source="my-docs")
        >>> markdown = result['markdown']
    """
    client = FileProcessorClient(base_url=base_url)
    return client.parse_file(file_path, source=source)


def chunk_file(
    file_path: str,
    source: Optional[str] = None,
    base_url: str = "http://localhost:8003"
) -> Dict[str, Any]:
    """
    Quick helper to chunk a file without instantiating a client.

    Args:
        file_path: Path to file
        source: Source identifier
        base_url: API server URL

    Returns:
        Chunk result dictionary

    Example:
        >>> from tests.api_client import chunk_file
        >>> result = chunk_file("large_doc.md", source="my-content")
        >>> chunks = result['chunks']
    """
    client = FileProcessorClient(base_url=base_url)
    return client.chunk_file(file_path, source=source)


# Example usage
if __name__ == "__main__":
    import sys

    # Example: Parse a file
    if len(sys.argv) < 2:
        print("Usage: python api_client.py <file_path> [source]")
        print("\nExample:")
        print("  python api_client.py document.pdf my-app/docs")
        sys.exit(1)

    file_path = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"📄 Processing file: {file_path}")
    print(f"🏷️  Source: {source or 'Not specified'}")
    print()

    try:
        # Initialize client
        client = FileProcessorClient()

        # Check health
        print("🏥 Checking API health...")
        health = client.health_check()
        print(f"   Status: {health['status']}")
        print()

        # Parse file
        print("📝 Parsing file...")
        result = client.parse_file(file_path, source=source)

        print(f"   ✅ Success: {result['success']}")
        print(f"   📁 File Type: {result['file_type']}")
        print(f"   🔑 File Hash: {result['file_hash']}")
        print(f"   📊 Markdown Length: {len(result['markdown'])} chars")
        print(f"   💾 Cache Status: {result['cache_status']}")
        print()

        # Preview markdown
        print("📄 Markdown Preview (first 300 chars):")
        print("-" * 60)
        print(result['markdown'][:300])
        if len(result['markdown']) > 300:
            print("...")
        print("-" * 60)

    except FileNotFoundError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("❌ Error: Could not connect to API server")
        print("   Make sure the server is running on http://localhost:8003")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP Error: {e}")
        print(f"   Response: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")
        sys.exit(1)
