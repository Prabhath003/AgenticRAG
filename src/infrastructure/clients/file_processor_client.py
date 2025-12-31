"""
API Client Helper for File Processing Service

This module provides helper functions to interact with the file processing API
from external applications or services.

The API v2.0 uses an async task-based architecture with cost tracking:
- POST /parse or /chunk returns task_id immediately
- GET /status/{task_id} to check task status (with progress and estimated costs)
- GET /result/{task_id} to retrieve results when complete (includes cost metrics)
- POST /batch/parse or /batch/chunk for batch processing (async, returns task_id)
- GET /metrics/{file_hash} to get processing metrics for a file
- GET /tasks/{file_hash} to get all tasks that processed a file
- GET /cost-report to get aggregated cost report

Usage:
    from examples.api_client import FileProcessorClient

    # Initialize client
    client = FileProcessorClient(base_url="http://localhost:8003")

    # Parse a file (waits for completion by default)
    result = client.parse_file("path/to/document.pdf", source="my-app/docs")
    print(f"Cost: ${result['estimated_cost_usd']}")

    # Chunk a file (waits for completion by default)
    result = client.chunk_file("path/to/document.md", source="my-app/content")

    # Async usage (don't wait for completion)
    task_id = client.parse_file_async("document.pdf", source="my-app")
    # ... do other work ...
    result = client.wait_for_task(task_id)

    # Get cost report
    report = client.get_cost_report()
    print(f"Total cost: ${report['total_estimated_cost']}")
"""

import requests
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, ByteString
import mimetypes


class FileProcessorClient:
    """Client for interacting with the file processing API v2.0."""

    def __init__(
        self,
        base_url: str = "http://localhost:8003",
        poll_interval: float = 5.0
    ):
        """
        Initialize the file processor client.

        Args:
            base_url: Base URL of the API server (default: http://localhost:8003)
            poll_interval: How often to poll for task status in seconds (default: 5.0)
        """
        self.base_url = base_url.rstrip('/')
        self.poll_interval = poll_interval

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
            '.doc': 'application/msword',
            '.ppt': 'application/vnd.ms-powerpoint',
        }
        return mime_map.get(ext, 'application/octet-stream')

    def health_check(self) -> Dict[str, Any]:
        """
        Check if the API server is healthy.

        Returns:
            Health status response with CPU and worker stats

        Raises:
            requests.exceptions.RequestException: If request fails
        """
        response = requests.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """
        Get the status of a task.

        Args:
            task_id: The task ID returned from parse/chunk endpoints

        Returns:
            Task status information including state and progress

        Raises:
            requests.exceptions.RequestException: If request fails
        """
        response = requests.get(f"{self.base_url}/status/{task_id}")
        response.raise_for_status()
        return response.json()

    def get_task_result(self, task_id: str) -> Dict[str, Any]:
        """
        Get the result of a completed task.

        Args:
            task_id: The task ID

        Returns:
            Task result (parsed/chunked content)

        Raises:
            requests.exceptions.RequestException: If request fails or task not complete
        """
        response = requests.get(f"{self.base_url}/result/{task_id}")
        response.raise_for_status()
        return response.json()

    def wait_for_task(
        self,
        task_id: str,
        poll_interval: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Wait for a task to complete and return the result.

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
                # Check task status first
                status = self.get_task_status(task_id)
                task_status = status.get("status")

                # Check if task is complete
                if task_status in ["completed", "COMPLETED"]:
                    # Task is complete, get the full result
                    return self.get_task_result(task_id)

                if task_status in ["failed", "FAILED"]:
                    raise Exception("File processing failed")

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

    def parse_file_async(
        self,
        file_path: str,
        source: Optional[str] = None
    ) -> str:
        """
        Parse a file asynchronously (returns task_id immediately).

        Args:
            file_path: Path to the file to parse
            source: Source identifier (e.g., 'my-app/documents/123')

        Returns:
            Task ID string

        Raises:
            FileNotFoundError: If file doesn't exist
            requests.exceptions.RequestException: If API request fails
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
        params: Dict[str, Any] = {}
        if source:
            params['source'] = source

        # Make request
        response = requests.post(
            f"{self.base_url}/parse",
            files=files,
            params=params,
        )

        response.raise_for_status()
        result = response.json()
        return result['task_id']

    def parse_file(
        self,
        file_path: str,
        source: Optional[str] = None,
        wait: bool = True,
        poll_interval: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Parse a file and return markdown content.

        Args:
            file_path: Path to the file to parse
            source: Source identifier (e.g., 'my-app/documents/123')
            wait: If True, wait for task to complete (default: True)
            poll_interval: Override default poll interval

        Returns:
            If wait=True: Dictionary containing parse result with markdown content
            If wait=False: Dictionary with task_id and status URLs

        Raises:
            FileNotFoundError: If file doesn't exist
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> client = FileProcessorClient()
            >>> result = client.parse_file("document.pdf", source="docs/2024")
            >>> print(result['markdown'])
        """
        task_id = self.parse_file_async(file_path, source=source)

        if not wait:
            return {
                "task_id": task_id,
                "status_url": f"/status/{task_id}",
                "result_url": f"/result/{task_id}"
            }

        return self.wait_for_task(task_id, poll_interval=poll_interval)

    def chunk_file_async(
        self,
        file_path: str,
        source: Optional[str] = None
    ) -> str:
        """
        Chunk a file asynchronously (returns task_id immediately).

        Args:
            file_path: Path to the file to chunk
            source: Source identifier (e.g., 'my-app/documents/123')

        Returns:
            Task ID string

        Raises:
            FileNotFoundError: If file doesn't exist
            requests.exceptions.RequestException: If API request fails
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
        params: Dict[str, Any] = {}
        if source:
            params['source'] = source

        # Make request
        response = requests.post(
            f"{self.base_url}/chunk",
            files=files,
            params=params
        )

        response.raise_for_status()
        result = response.json()
        return result['task_id']

    def chunk_file(
        self,
        file_path: str,
        source: Optional[str] = None,
        wait: bool = True,
        poll_interval: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Parse and chunk a file into smaller segments.

        Args:
            file_path: Path to the file to chunk
            source: Source identifier (e.g., 'my-app/documents/123')
            wait: If True, wait for task to complete (default: True)
            poll_interval: Override default poll interval

        Returns:
            If wait=True: Dictionary containing chunk results
            If wait=False: Dictionary with task_id and status URLs

        Raises:
            FileNotFoundError: If file doesn't exist
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> client = FileProcessorClient()
            >>> result = client.chunk_file("large_doc.md", source="docs/2024")
            >>> for chunk in result['chunks']:
            ...     print(f"Chunk {chunk['chunk_index']}: {chunk['content'][:100]}")
        """
        task_id = self.chunk_file_async(file_path, source=source)

        if not wait:
            return {
                "task_id": task_id,
                "status_url": f"/status/{task_id}",
                "result_url": f"/result/{task_id}"
            }

        return self.wait_for_task(task_id, poll_interval=poll_interval)

    def parse_file_from_bytes(
        self,
        file_content: bytes,
        filename: str,
        source: Optional[str] = None,
        mime_type: Optional[str] = None,
        wait: bool = True,
        poll_interval: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Parse file content from bytes (useful when you already have file in memory).

        Args:
            file_content: File content as bytes
            filename: Original filename (used for extension detection)
            source: Source identifier
            mime_type: Optional MIME type (auto-detected if not provided)
            wait: If True, wait for task to complete (default: True)
            poll_interval: Override default poll interval

        Returns:
            If wait=True: Dictionary with parse results
            If wait=False: Dictionary with task_id and status URLs

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

        params: Dict[str, Any] = {}
        if source:
            params['source'] = source

        response = requests.post(
            f"{self.base_url}/parse",
            files=files,
            params=params
        )

        response.raise_for_status()
        result = response.json()
        task_id = result['task_id']

        if not wait:
            return {
                "task_id": task_id,
                "status_url": f"/status/{task_id}",
                "result_url": f"/result/{task_id}"
            }

        return self.wait_for_task(task_id, poll_interval=poll_interval)

    def chunk_file_from_bytes(
        self,
        file_content: bytes,
        filename: str,
        source: Optional[str] = None,
        mime_type: Optional[str] = None,
        wait: bool = True,
        poll_interval: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Chunk file content from bytes.

        Args:
            file_content: File content as bytes
            filename: Original filename (used for extension detection)
            source: Source identifier
            mime_type: Optional MIME type (auto-detected if not provided)
            wait: If True, wait for task to complete (default: True)
            poll_interval: Override default poll interval

        Returns:
            If wait=True: Dictionary with chunk results
            If wait=False: Dictionary with task_id and status URLs

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

        params: Dict[str, Any] = {}
        if source:
            params['source'] = source

        response = requests.post(
            f"{self.base_url}/chunk",
            files=files,
            params=params
        )

        response.raise_for_status()
        result = response.json()
        task_id = result['task_id']

        if not wait:
            return {
                "task_id": task_id,
                "status_url": f"/status/{task_id}",
                "result_url": f"/result/{task_id}"
            }

        return self.wait_for_task(task_id, poll_interval=poll_interval)

    def batch_parse_files_async(
        self,
        file_paths: List[str],
        source: Optional[str] = None
    ) -> str:
        """
        Parse multiple files asynchronously (returns task_id immediately).

        Args:
            file_paths: List of file paths to parse
            source: Optional source identifier applied to all files

        Returns:
            Task ID string

        Raises:
            FileNotFoundError: If any file doesn't exist
            requests.exceptions.RequestException: If API request fails
        """
        files: List[Tuple[str, Tuple[str, ByteString, str]]] = []
        for file_path in file_paths:
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                raise FileNotFoundError(f"File not found: {file_path}")

            with open(file_path_obj, 'rb') as f:
                file_content = f.read()

            mime_type = self._get_mime_type(str(file_path_obj))
            files.append(('files', (file_path_obj.name, file_content, mime_type)))

        params: Dict[str, Any] = {}
        if source:
            params['source'] = source

        response = requests.post(
            f"{self.base_url}/batch/parse",
            files=files,
            params=params
        )

        response.raise_for_status()
        result = response.json()
        return result['task_id']

    def batch_parse_files(
        self,
        file_paths: List[str],
        source: Optional[str] = None,
        wait: bool = True,
        poll_interval: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Parse multiple files in parallel (async batch endpoint).

        Args:
            file_paths: List of file paths to parse
            source: Optional source identifier applied to all files
            wait: If True, wait for batch to complete (default: True)
            poll_interval: Override default poll interval

        Returns:
            If wait=True: Dictionary with batch results containing:
                - task_id: str
                - total_files: int
                - successful: int
                - failed: int
                - files: List[Dict] with per-file info
                - estimated_cost_usd: float
            If wait=False: Dictionary with task_id and status URLs

        Raises:
            FileNotFoundError: If any file doesn't exist
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> client = FileProcessorClient()
            >>> results = client.batch_parse_files(["doc1.pdf", "doc2.md"], source="batch-2024")
            >>> print(f"Processed {results['successful']} files for ${results['estimated_cost_usd']}")
        """
        task_id = self.batch_parse_files_async(file_paths, source=source)

        if not wait:
            return {
                "task_id": task_id,
                "status_url": f"/batch/status/{task_id}",
                "result_url": f"/batch/result/{task_id}"
            }

        return self.wait_for_task(task_id, poll_interval=poll_interval)

    def batch_chunk_files_async(
        self,
        file_paths: List[str],
        source: Optional[str] = None
    ) -> str:
        """
        Chunk multiple files asynchronously (returns task_id immediately).

        Args:
            file_paths: List of file paths to chunk
            source: Optional source identifier applied to all files

        Returns:
            Task ID string

        Raises:
            FileNotFoundError: If any file doesn't exist
            requests.exceptions.RequestException: If API request fails
        """
        files: List[Tuple[str, Tuple[str, ByteString, str]]] = []
        for file_path in file_paths:
            file_path_obj = Path(file_path)
            if not file_path_obj.exists():
                raise FileNotFoundError(f"File not found: {file_path}")

            with open(file_path_obj, 'rb') as f:
                file_content = f.read()

            mime_type = self._get_mime_type(str(file_path_obj))
            files.append(('files', (file_path_obj.name, file_content, mime_type)))

        params: Dict[str, Any] = {}
        if source:
            params['source'] = source

        response = requests.post(
            f"{self.base_url}/batch/chunk",
            files=files,
            params=params
        )

        response.raise_for_status()
        result = response.json()
        return result['task_id']

    def batch_chunk_files(
        self,
        file_paths: List[str],
        source: Optional[str] = None,
        wait: bool = True,
        poll_interval: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Chunk multiple files in parallel (async batch endpoint).

        Args:
            file_paths: List of file paths to chunk
            source: Optional source identifier applied to all files
            wait: If True, wait for batch to complete (default: True)
            poll_interval: Override default poll interval

        Returns:
            If wait=True: Dictionary with batch results containing:
                - task_id: str
                - total_files: int
                - successful: int
                - failed: int
                - files: List[Dict] with per-file chunk info
                - estimated_cost_usd: float
            If wait=False: Dictionary with task_id and status URLs

        Raises:
            FileNotFoundError: If any file doesn't exist
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> client = FileProcessorClient()
            >>> results = client.batch_chunk_files(["doc1.md", "doc2.txt"], source="batch-2024")
            >>> print(f"Processed {results['successful']} files for ${results['estimated_cost_usd']}")
        """
        task_id = self.batch_chunk_files_async(file_paths, source=source)

        if not wait:
            return {
                "task_id": task_id,
                "status_url": f"/batch/status/{task_id}",
                "result_url": f"/batch/result/{task_id}"
            }

        return self.wait_for_task(task_id, poll_interval=poll_interval)

    def get_metrics(self, file_hash: str) -> Dict[str, Any]:
        """
        Get processing metrics for a specific file.

        Args:
            file_hash: Hash of the file

        Returns:
            Dictionary with metrics including pages, tokens, services used, and costs

        Raises:
            requests.exceptions.RequestException: If API request fails
        """
        response = requests.get(f"{self.base_url}/metrics/{file_hash}")
        response.raise_for_status()
        return response.json()

    def get_file_tasks(self, file_hash: str) -> Dict[str, Any]:
        """
        Get all task requests that processed a specific file.

        Args:
            file_hash: Hash of the file

        Returns:
            Dictionary with file hash, total cost, and task count

        Raises:
            requests.exceptions.RequestException: If API request fails
        """
        response = requests.get(f"{self.base_url}/tasks/{file_hash}")
        response.raise_for_status()
        return response.json()

    def get_cost_report(self) -> Dict[str, Any]:
        """
        Get cost report across all processed files.

        Returns:
            Dictionary with aggregated costs and breakdown by operation/service

        Raises:
            requests.exceptions.RequestException: If API request fails

        Example:
            >>> client = FileProcessorClient()
            >>> report = client.get_cost_report()
            >>> print(f"Total cost: ${report['total_estimated_cost']}")
            >>> print(f"Files processed: {report['total_files']}")
        """
        response = requests.get(f"{self.base_url}/cost-report")
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
        >>> from examples.api_client import parse_file
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
        >>> from examples.api_client import chunk_file
        >>> result = chunk_file("large_doc.md", source="my-content")
        >>> chunks = result['chunks']
    """
    client = FileProcessorClient(base_url=base_url)
    return client.chunk_file(file_path, source=source)


def batch_parse_files(
    file_paths: List[str],
    source: Optional[str] = None,
    base_url: str = "http://localhost:8003"
) -> Dict[str, Any]:
    """
    Quick helper to batch parse multiple files without instantiating a client.

    Args:
        file_paths: List of file paths
        source: Source identifier applied to all files
        base_url: API server URL

    Returns:
        Batch result dictionary with aggregated results

    Example:
        >>> from examples.api_client import batch_parse_files
        >>> result = batch_parse_files(["doc1.pdf", "doc2.md"], source="batch")
    """
    client = FileProcessorClient(base_url=base_url)
    return client.batch_parse_files(file_paths, source=source)


def batch_chunk_files(
    file_paths: List[str],
    source: Optional[str] = None,
    base_url: str = "http://localhost:8003"
) -> Dict[str, Any]:
    """
    Quick helper to batch chunk multiple files without instantiating a client.

    Args:
        file_paths: List of file paths
        source: Source identifier applied to all files
        base_url: API server URL

    Returns:
        Batch result dictionary with aggregated results

    Example:
        >>> from examples.api_client import batch_chunk_files
        >>> result = batch_chunk_files(["doc1.pdf", "doc2.md"], source="batch")
    """
    client = FileProcessorClient(base_url=base_url)
    return client.batch_chunk_files(file_paths, source=source)


# Example usage
if __name__ == "__main__":
    import sys
    import json

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
        print(f"   CPU: {health.get('cpu_utilization', 'N/A')}")
        print(f"   Workers: {health.get('current_workers', 'N/A')}/{health.get('max_workers', 'N/A')}")
        print()

        # Parse file (async task-based)
        print("📝 Submitting parse task...")
        task_id = client.parse_file_async(file_path, source=source)
        print(f"   Task ID: {task_id}")
        print(f"   Status URL: /status/{task_id}")
        print()

        # Wait for completion
        print("⏳ Waiting for task to complete...")
        result = client.wait_for_task(task_id)

        # Check if task was successful
        status = result.get('status', '')
        print(f"   Task Status: {status}")
        print(f"   Filename: {result.get('filename', 'N/A')}")
        print(f"   File Type: {result.get('file_type', 'N/A')}")

        # Show cost information
        cost = result.get('estimated_cost_usd', 0)
        print(f"   💰 Estimated Cost: ${cost:.6f}")
        print()

        # Show files processed
        files_info = result.get('files', [])
        if files_info:
            print(f"📦 Files Processed: {len(files_info)}")
            for file_info in files_info:
                print(f"   - {file_info.get('filename', 'unknown')}")
                print(f"     Status: {file_info.get('status', 'unknown')}")
                print(f"     Cost: ${file_info.get('estimated_cost_usd', 0):.6f}")
        print()

        # Show results if available
        results = result.get('results', {})
        if results:
            print("✅ Parsed Content Available:")
            for file_hash, content in results.items():
                if isinstance(content, str):
                    print(f"   File Hash: {file_hash}")
                    preview = content[:300] if len(content) > 300 else content
                    print(f"   Content Preview: {preview}")
                    if len(content) > 300:
                        print("   ...")
        print()

        # Show metrics if available
        metrics = result.get('metrics', {})
        if metrics:
            print("📊 Processing Metrics:")
            for file_hash, metric_info in metrics.items():
                if metric_info:
                    print(f"   File Hash: {file_hash}")
                    print(f"   Metrics: {json.dumps(metric_info, indent=6)}")

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
    except TimeoutError as e:
        print(f"❌ Timeout Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
