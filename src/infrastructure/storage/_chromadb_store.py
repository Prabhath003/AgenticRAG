# src/infrastructure/storage/_chromadb_store.py
"""
ChromaDB vector store implementation with S3 support (production-ready).

This module provides:
- Local ChromaDB for development/testing
- S3 sync capabilities for production backup
- Multiple collection management
- Cache invalidation strategies
"""

from pathlib import Path
from typing import List, Optional, Dict, Any, Set, cast, Literal, Tuple
from datetime import datetime
from contextlib import contextmanager
import threading
import chromadb
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings, Embeddable, Where
from chromadb import Collection, Metadata
import json

from ..operation_logging import get_operation_user_id
from ..clients import ModelServerClient
from ...log_creator import get_file_logger
from ._s3_service import get_s3_service
from ...core.models.core_models import Chunk

logger = get_file_logger()


class ModelServerEmbeddingFunction(EmbeddingFunction[Embeddable]):
    """Custom embedding function for ChromaDB using ModelServerClient"""

    def __init__(self, model_name: str = "text-embedding-3-small", batch_size: int = 512):
        """
        Initialize embedding function with ModelServerClient.

        Args:
            model_name: OpenAI embedding model name (default: text-embedding-3-small)
        """
        super().__init__()
        self.model_client = ModelServerClient(timeout=0)
        self.model_name = model_name
        self.batch_size = 512

    def __call__(self, input: Embeddable) -> Embeddings:
        """
        Generate embeddings for input documents using ModelServerClient.

        Args:
            input: List of document strings to embed

        Returns:
            List of embedding vectors (each vector is a list of floats)
        """
        # Cast to Documents (List[str]) since ModelServerEmbeddingFunction only supports text
        input_texts = cast(Documents, input)

        embeddings: List[List[float]] = []
        batch_size = 512

        for batch_idx in range(0, len(input_texts), batch_size):
            batch = input_texts[batch_idx : batch_idx + batch_size]

            attempt = 0
            while True:
                try:
                    response = self.model_client.create_openai_embeddings(
                        batch, model=self.model_name
                    )
                    batch_embeddings = response
                    embeddings.extend(batch_embeddings)
                    break  # Successfully processed batch, move to next
                except Exception as e:
                    logger.error(f"Error in generation embeddigns: {e}")
                    if "429" in str(e):
                        attempt += 1
                        continue
                    else:
                        raise
        return cast(Embeddings, embeddings)


class ChromaDBStore:
    """
    ChromaDB vector store with S3 backup support.

    Development mode: Local storage only
    Production mode: Includes S3 sync capabilities
    """

    def __init__(
        self,
        index_type: Literal["flat", "hnsw"] = "hnsw",
        persist_dir: str = "./data/chromadb",
        mode: str = "development",
        s3_enabled: bool = False,
        model_name: str = "text-embedding-3-small",
    ):
        """
        Initialize ChromaDB store.

        Args:
            index_type: Index type for collections ("flat" for exact search, "hnsw" for approximate)
            persist_dir: Local persistence directory
            mode: "development" or "production"
            s3_enabled: Enable S3 backup (production only)
            model_name: Embedding model to use
        """
        if chromadb is None:
            raise ImportError("chromadb not installed. Install with: pip install chromadb")

        if index_type not in ["flat", "hnsw"]:
            raise ValueError("index_type must be 'flat' or 'hnsw'")

        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.index_type = index_type

        self.mode = mode
        self.s3_enabled = s3_enabled and mode == "production"

        # Create embedding function for loading existing collections
        self.embedding_function = ModelServerEmbeddingFunction(model_name=model_name)

        # Initialize ChromaDB (using new PersistentClient API)
        self.client = chromadb.PersistentClient(path=str(self.persist_dir))

        # Thread safety
        self._lock = threading.RLock()
        self._collection_cache: Dict[str, Any] = {}

        logger.info(f"ChromaDBStore initialized ({mode} mode)")

    def get_or_create_collection(
        self, name: str, metadata: Optional[Dict[str, Any]] = None
    ) -> Collection:
        """Get or create a collection with ModelServer embeddings and index type configuration."""
        with self._lock:
            if name in self._collection_cache:
                return self._collection_cache[name]

            # Build collection kwargs with index type metadata
            collection_kwargs: Dict[str, Any] = {
                "name": name,
                "embedding_function": self.embedding_function,
            }

            # Prepare metadata with index type configuration
            collection_metadata = metadata or {}

            # Only add HNSW configuration for HNSW index type
            if self.index_type == "hnsw":
                collection_metadata["hnsw:space"] = "cosine"

            collection_kwargs["metadata"] = collection_metadata

            collection = self.client.get_or_create_collection(**collection_kwargs)
            self._collection_cache[name] = collection
            logger.info(
                f"Collection '{name}' created/retrieved with {self.index_type} index and ModelServer embeddings"
            )
            return collection

    def _build_chunk_from_retrieval(
        self,
        chunk_id: str,
        document: str,
        metadata: Dict[str, Any],
    ) -> Chunk:
        """
        Build a Chunk object from retrieved data, preserving user_id and created_at.

        Args:
            chunk_id: The chunk ID
            document: The document text
            doc_id: The document ID
            metadata: The metadata dict containing user_id and created_at

        Returns:
            Chunk object with preserved user_id and created_at
        """
        # Extract user_id and created_at from metadata
        user_id = get_operation_user_id()
        created_at_str = metadata.get("created_at")

        # Parse created_at timestamp
        if created_at_str:
            try:
                if isinstance(created_at_str, str):
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                else:
                    created_at = cast(datetime, created_at_str)
            except (ValueError, AttributeError, TypeError):
                created_at = datetime.now()
        else:
            created_at = datetime.now()

        # Extract user metadata (exclude system fields added during storage)
        # System fields: doc_id, chunk_id, created_at, user_id, chunk_order_index, chunk_metadata, kb_id
        system_fields = {
            "doc_id",
            "chunk_id",
            "created_at",
            "user_id",
            "chunk_order_index",
            "chunk_metadata",
            "kb_id",
        }
        chunk_metadata = {k: v for k, v in metadata.items() if k not in system_fields}

        return Chunk(
            _id=chunk_id,
            doc_id=metadata.get("doc_id", ""),
            content=json.loads(document),
            metadata=chunk_metadata,
            created_at=created_at,
            user_id=user_id,
        )

    def check_duplicate_chunks(
        self,
        collection_name: str,
        chunks: List[Chunk],
    ) -> Dict[str, Any]:
        """
        Check for duplicate chunks in the collection.

        Returns information about:
        - Exact ID duplicates (same chunk_id already exists)
        - Content duplicates (same document has identical content)

        Args:
            collection_name: Target collection
            chunks: Chunks to check for duplicates

        Returns:
            Dict with keys:
            - 'id_duplicates': List of chunk IDs that already exist
            - 'content_duplicates': List of chunk IDs with duplicate content in same doc
            - 'duplicate_count': Total number of duplicates found
        """
        result: Dict[str, Any] = {
            "id_duplicates": [],
            "content_duplicates": [],
            "duplicate_count": 0,
        }

        try:
            collection = self.get_or_create_collection(collection_name)
            chunk_ids = [chunk.chunk_id for chunk in chunks]

            # Check for ID duplicates
            if chunk_ids:
                existing = collection.get(ids=chunk_ids)
                id_duplicates = existing.get("ids", [])
                if id_duplicates:
                    result["id_duplicates"] = id_duplicates
                    result["duplicate_count"] += len(id_duplicates)

            # Check for content duplicates within same document
            for chunk in chunks:
                # Get all chunks from the same document
                same_doc_chunks = collection.get(where=cast(Where, {"doc_id": chunk.doc_id}))

                if same_doc_chunks and same_doc_chunks.get("ids"):
                    # Compare content (JSON serialized)
                    current_content = json.dumps(chunk.content, sort_keys=True)
                    existing_docs = same_doc_chunks.get("documents", [])

                    if existing_docs:
                        for existing_doc in existing_docs:
                            try:
                                existing_content = json.dumps(
                                    json.loads(existing_doc), sort_keys=True
                                )
                                if current_content == existing_content:
                                    result["content_duplicates"].append(chunk.chunk_id)
                                    result["duplicate_count"] += 1
                                    break
                            except (json.JSONDecodeError, TypeError):
                                pass

        except Exception as e:
            logger.debug(f"Error checking duplicates: {e}")

        return result

    @staticmethod
    def _sanitize_metadata_value(value: Any, key: str = "unknown") -> Any:
        """
        Sanitize a single metadata value to be ChromaDB-compatible.

        ChromaDB only accepts: str, int, float, bool, None, list of primitives
        This function converts any non-primitive to a safe type.

        Args:
            value: The metadata value to sanitize
            key: The metadata key (for logging context)

        Returns:
            A ChromaDB-compatible value
        """
        # Already safe types
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            # Check for special float values
            if isinstance(value, float):
                if value != value or value == float("inf") or value == float("-inf"):
                    logger.debug(f"Metadata key '{key}': Skipping NaN/Infinity value")
                    return None
            return value

        # Handle lists: recursively sanitize items
        if isinstance(value, list):
            if not value:
                return None  # Skip empty lists
            sanitized_items: List[Any] = []
            for item in value:  # type: ignore
                if isinstance(item, (str, int, float, bool, type(None))):
                    sanitized_items.append(item)
                else:
                    # Convert non-primitive list items to string
                    sanitized_items.append(str(item))  # type: ignore
            # Only keep list if it has items after sanitization
            return sanitized_items if sanitized_items else None

        # Handle datetime objects by converting to ISO string
        if hasattr(value, "isoformat"):  # datetime, date, time
            return value.isoformat()

        # Handle numpy types
        try:
            import numpy as np

            if isinstance(value, (np.integer, np.floating)):
                return float(value) if isinstance(value, np.floating) else int(value)  # type: ignore
            if isinstance(value, np.bool_):
                return bool(value)  # type: ignore
        except ImportError:
            pass

        # Handle UUID and other types with string representation
        if hasattr(value, "__str__") and not isinstance(value, type):  # type: ignore
            return str(value)  # type: ignore

        # Fallback: convert to string representation
        logger.debug(f"Metadata key '{key}': Converting {type(value).__name__} to string")  # type: ignore
        return str(value)  # type: ignore

    def add_chunks(
        self,
        collection_name: str,
        chunks: List[Chunk],
        skip_duplicates: bool = True,
    ) -> List[str]:
        """
        Add chunks to collection with structured metadata and duplicate detection.

        Args:
            collection_name: Target collection
            chunks: List of Chunk objects with chunk_id, doc_id, content, metadata, created_at
            skip_duplicates: If True, skip chunks with existing IDs; if False, replace them

        Returns:
            List of actually added chunk IDs (excluding duplicates if skip_duplicates=True)
        """
        # Handle empty input early to prevent ChromaDB "non-empty lists required" error
        if not chunks:
            logger.info(f"No chunks to add to '{collection_name}'")
            return []

        with self._lock:
            collection = self.get_or_create_collection(collection_name)

            chunk_ids = [chunk.chunk_id for chunk in chunks]

            # ================================================================
            # Check for duplicate chunk IDs
            # ================================================================
            if chunk_ids:
                try:
                    existing_result = collection.get(ids=chunk_ids)
                    existing_ids: Set[str] = set(existing_result.get("ids", []))
                except Exception as e:
                    logger.warning(f"Failed to check for existing chunks: {e}")
                    existing_ids = set()

                if existing_ids:
                    if skip_duplicates:
                        # Filter out duplicates
                        logger.warning(
                            f"Found {len(existing_ids)} duplicate chunk IDs in '{collection_name}': "
                            f"{sorted(existing_ids)[:10]}{'...' if len(existing_ids) > 10 else ''}"
                        )
                        chunks = [c for c in chunks if c.chunk_id not in existing_ids]
                        chunk_ids = [c.chunk_id for c in chunks]

                        if not chunks:
                            logger.info("All chunks are duplicates, skipping add")
                            return []
                    else:
                        # Will replace duplicates
                        logger.info(
                            f"Will replace {len(existing_ids)} duplicate chunks in '{collection_name}'"
                        )

            # Extract text content from chunks for embedding
            # Content can be a dict with text field or a simple string representation
            texts: List[str] = []
            for chunk in chunks:
                logger.debug(f"Chunk {chunk.chunk_id} content type: {type(chunk.content).__name__}")
                try:
                    text = json.dumps(chunk.content, indent=1)
                except TypeError as json_error:
                    logger.warning(
                        f"Could not JSON serialize chunk.content for {chunk.chunk_id}: {str(json_error)}"
                    )
                    text = str(chunk.content)
                texts.append(text)

            # Build metadata for each chunk, preserving kb_id and other metadata
            metadatas: List[Metadata] = []
            for chunk in chunks:
                # Safely extract chunk_order_index, ensuring it's an integer
                chunk_order_index = (
                    chunk.content.get("chunk_order_index", 0) if chunk.content else 0
                )
                if not isinstance(chunk_order_index, int):
                    logger.warning(
                        f"Chunk {chunk.chunk_id}: chunk_order_index is {type(chunk_order_index).__name__}, "
                        f"value={repr(chunk_order_index)[:100]}, converting to 0"
                    )
                    chunk_order_index = 0

                # Debug: Log incoming metadata from chunk before processing
                logger.debug(
                    f"Chunk {chunk.chunk_id} incoming metadata types: {[(k, type(v).__name__) for k, v in chunk.metadata.items()]}"
                )

                # Sanitize initial metadata values
                logger.debug(
                    f"Chunk {chunk.chunk_id} initial values - "
                    f"doc_id type: {type(chunk.doc_id).__name__}, "
                    f"chunk_id type: {type(chunk.chunk_id).__name__}, "
                    f"user_id type: {type(chunk.user_id).__name__}, "
                    f"chunk_order_index type: {type(chunk_order_index).__name__}"
                )
                chunk_metadata: Dict[str, Any] = {
                    "doc_id": self._sanitize_metadata_value(chunk.doc_id, "doc_id") or "unknown",
                    "chunk_id": self._sanitize_metadata_value(chunk.chunk_id, "chunk_id")
                    or "unknown",
                    "chunk_order_index": self._sanitize_metadata_value(
                        chunk_order_index, "chunk_order_index"
                    )
                    or 0,
                }
                # Only add user_id if it's not None (ChromaDB doesn't accept None values)
                user_id_sanitized = self._sanitize_metadata_value(chunk.user_id, "user_id")
                if user_id_sanitized is not None:
                    chunk_metadata["user_id"] = user_id_sanitized
                logger.debug(
                    f"Chunk {chunk.chunk_id} sanitized metadata initial dict - "
                    f"doc_id: {type(chunk_metadata['doc_id']).__name__}, "
                    f"chunk_id: {type(chunk_metadata['chunk_id']).__name__}, "
                    f"user_id: {type(chunk_metadata.get('user_id')).__name__}, "
                    f"chunk_order_index: {type(chunk_metadata['chunk_order_index']).__name__}"
                )
                # Flatten and sanitize chunk.metadata
                for key, value in chunk.metadata.items():
                    try:
                        sanitized = self._sanitize_metadata_value(value, key)
                        if sanitized is not None:  # Skip None values from sanitization
                            chunk_metadata[key] = sanitized
                    except Exception as metadata_error:
                        logger.warning(
                            f"Chunk {chunk.chunk_id}: Error sanitizing metadata key '{key}': {str(metadata_error)}"
                        )
                        continue

                metadatas.append(chunk_metadata)

            # Final safety check: Clean and validate all metadata before adding to ChromaDB
            cleaned_metadatas: List[Metadata] = []
            for _, metadata in enumerate(metadatas):
                cleaned_metadata: Dict[str, Any] = {}
                for key, value in metadata.items():
                    # Skip None values - ChromaDB doesn't accept them
                    if value is None:
                        continue
                    # Validate and clean each value
                    if isinstance(value, (str, int, float, bool)):
                        # Already safe
                        cleaned_metadata[key] = value
                    elif isinstance(value, list):
                        # Validate list items and remove non-primitives
                        cleaned_items: List[Any] = []
                        for item in value:
                            if isinstance(item, (str, int, float, bool, type(None))):  # type: ignore
                                cleaned_items.append(item)
                            else:
                                # Skip non-primitive list items
                                logger.debug(
                                    f"Removing non-primitive list item from metadata key '{key}': {type(item).__name__}"
                                )
                        if cleaned_items:
                            cleaned_metadata[key] = cleaned_items
                        # Skip empty lists
                    else:
                        # Should not happen if _sanitize_metadata_value works correctly
                        # But as final safety, convert to string
                        logger.warning(
                            f"Final cleanup: Converting unexpected type {type(value).__name__} to string for key '{key}'"
                        )
                        cleaned_metadata[key] = str(value)
                cleaned_metadatas.append(cleaned_metadata)

            # Debug: Log every metadata value right before add
            logger.debug(f"Total chunks to add: {len(chunk_ids)}")
            for chunk_idx, (chunk_id, metadata) in enumerate(zip(chunk_ids, cleaned_metadatas)):
                logger.debug(
                    f"Chunk {chunk_idx}: ID={chunk_id}, metadata_keys={list(metadata.keys())}"
                )
                for key, value in metadata.items():
                    actual_type = type(value).__name__
                    logger.debug(f"  {key}: {actual_type} = {repr(value)[:80]}")
                    if not isinstance(value, (str, int, float, bool, type(None), list)):
                        logger.error(
                            f"FAILED: Chunk {chunk_id} key '{key}' is {actual_type}: {repr(value)[:100]}"
                        )
                    elif isinstance(value, list):
                        for item_idx, item in enumerate(value):
                            if not isinstance(item, (str, int, float, bool, type(None))):  # type: ignore
                                logger.error(
                                    f"FAILED: List item {chunk_id}[{key}][{item_idx}] is {type(item).__name__}"
                                )

            logger.info(
                f"About to add {len(chunk_ids)} chunks to ChromaDB collection '{collection_name}'"
            )
            collection.add(
                ids=chunk_ids,
                documents=texts,
                metadatas=cleaned_metadatas,
            )

            logger.info(
                f"Successfully added {len(chunks)} chunks to '{collection_name}' "
                f"(duplicate handling: skip_duplicates={skip_duplicates})"
            )
            return chunk_ids

    def query(
        self,
        collection_name: str,
        query_texts: List[str],
        n_results: int = 10,
        where: Optional[Dict[str, Any]] = None,
        doc_ids: Optional[List[str]] = None,
        kb_ids: Optional[List[str]] = None,
        chunk_ids: Optional[List[str]] = None,
        min_chunk_order_index: Optional[float] = None,
        max_chunk_order_index: Optional[float] = None,
    ) -> List[Tuple[Chunk, float]]:
        """
        Query documents from collection with optional filtering by chunk metadata.

        Args:
            collection_name: Source collection
            query_texts: Query text(s) to search
            n_results: Number of results to return
            where: Optional custom metadata filter (merged with other conditions)
            doc_ids: Optional list of document IDs to filter by
            kb_ids: Optional list of knowledge base IDs to filter by
            chunk_ids: Optional list of chunk IDs to filter by
            min_chunk_order_index: Optional minimum chunk order index (inclusive)
            max_chunk_order_index: Optional maximum chunk order index (inclusive)

        Returns:
            List of tuples (Chunk, distance) where distance is the similarity score
        """
        collection = self.get_or_create_collection(collection_name)

        # Build filter conditions from chunk-specific parameters
        filter_conditions: Dict[str, Any] = where.copy() if where else {}

        # Add filters for doc_ids, kb_ids, chunk_ids (support multiple values with $in)
        if doc_ids:
            if len(doc_ids) == 1:
                filter_conditions["doc_id"] = doc_ids[0]
            else:
                filter_conditions["doc_id"] = {"$in": doc_ids}

        if kb_ids:
            if len(kb_ids) == 1:
                filter_conditions["kb_id"] = kb_ids[0]
            else:
                filter_conditions["kb_id"] = {"$in": kb_ids}

        if chunk_ids:
            if len(chunk_ids) == 1:
                filter_conditions["chunk_id"] = chunk_ids[0]
            else:
                filter_conditions["chunk_id"] = {"$in": chunk_ids}

        # Add range-based filter for chunk_order_index
        if min_chunk_order_index is not None or max_chunk_order_index is not None:
            order_index_filter: Dict[str, Any] = {}
            if min_chunk_order_index is not None:
                order_index_filter["$gte"] = min_chunk_order_index
            if max_chunk_order_index is not None:
                order_index_filter["$lte"] = max_chunk_order_index

            if order_index_filter:
                filter_conditions["chunk_order_index"] = order_index_filter

        # Use filter if any conditions exist
        final_where = filter_conditions if filter_conditions else None

        results = collection.query(
            query_texts=query_texts,
            n_results=n_results,
            where=final_where,
        )

        # Convert query results to List[Tuple[Chunk, float]]
        chunks: List[Tuple[Chunk, float]] = []
        if results and results.get("ids") and len(results["ids"]) > 0:
            result_ids = results["ids"][0]
            if results["documents"]:
                result_documents = results["documents"][0]
            else:
                result_documents = []
            if results["metadatas"]:
                result_metadatas = results["metadatas"][0]
            else:
                result_metadatas = []
            if results["distances"]:
                result_distances = results["distances"][0]
            else:
                result_distances = [0.0] * len(result_ids)

            for chunk_id, document, metadata, distance in zip(
                result_ids, result_documents, result_metadatas, result_distances
            ):
                chunk = self._build_chunk_from_retrieval(
                    chunk_id=chunk_id,
                    document=document,
                    metadata=dict(metadata),
                )
                chunks.append((chunk, distance))

        logger.debug(f"Query on '{collection_name}' returned {len(chunks)} results")
        return chunks

    def delete_chunks(
        self,
        collection_name: str,
        chunk_ids: Optional[List[str]] = None,
        doc_ids: Optional[List[str]] = None,
        kb_ids: Optional[List[str]] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Delete chunks from collection by various filters.

        Args:
            collection_name: Target collection
            chunk_ids: Optional list of chunk IDs to delete
            doc_ids: Optional list of document IDs to delete
            kb_ids: Optional list of knowledge base IDs to delete
            where: Optional custom metadata filter (merged with other conditions)

        Returns:
            Number of chunks deleted
        """
        with self._lock:
            collection = self.get_or_create_collection(collection_name)

            # Build filter conditions
            filter_conditions: Dict[str, Any] = where.copy() if where else {}

            # Add filters (support multiple values with $in)
            if chunk_ids:
                if len(chunk_ids) == 1:
                    filter_conditions["chunk_id"] = chunk_ids[0]
                else:
                    filter_conditions["chunk_id"] = {"$in": chunk_ids}

            if doc_ids:
                if len(doc_ids) == 1:
                    filter_conditions["doc_id"] = doc_ids[0]
                else:
                    filter_conditions["doc_id"] = {"$in": doc_ids}

            if kb_ids:
                if len(kb_ids) == 1:
                    filter_conditions["kb_id"] = kb_ids[0]
                else:
                    filter_conditions["kb_id"] = {"$in": kb_ids}

            # Use filter if any conditions exist
            final_where = filter_conditions if filter_conditions else None

            if not final_where:
                logger.warning(
                    f"No delete filter provided for '{collection_name}', skipping delete"
                )
                return 0

            try:
                # First, query to get the chunk IDs to delete (for logging)
                results = collection.get(where=cast(Where, final_where))
                deleted_ids = results.get("ids", []) if results else []
                deleted_count = len(deleted_ids)

                # Perform deletion
                if deleted_count > 0:
                    collection.delete(where=cast(Where, final_where))
                    logger.info(f"Deleted {deleted_count} chunks from '{collection_name}'")
                else:
                    logger.debug(f"No chunks matched deletion criteria in '{collection_name}'")

                return deleted_count

            except Exception as e:
                logger.error(f"Failed to delete chunks from '{collection_name}': {e}")
                return 0

    def get_previous_chunk(
        self,
        collection_name: str,
        chunk_id: str,
    ) -> Optional[Chunk]:
        """
        Get the previous chunk in the same document.

        Chunks are ordered by chunk_order_index. Returns the chunk with
        chunk_order_index = current_index - 1 in the same document.

        Args:
            collection_name: Target collection
            chunk_id: Chunk ID to find previous for

        Returns:
            Previous Chunk object or None if no previous chunk exists
        """
        try:
            collection = self.get_or_create_collection(collection_name)

            # Get the current chunk to find its doc_id and chunk_order_index
            current = collection.get(ids=[chunk_id])
            if not current or not current.get("ids"):
                logger.warning(f"Chunk '{chunk_id}' not found in '{collection_name}'")
                return None
            if current["metadatas"] and len(current["metadatas"]) > 0:
                current_metadatas: Metadata = current["metadatas"][0]
            else:
                current_metadatas = {}
            doc_id = current_metadatas.get("doc_id", "")
            order_index_value = current_metadatas.get("chunk_order_index", 0)

            if not doc_id:
                logger.warning(f"Chunk '{chunk_id}' has no doc_id in metadata")
                return None

            # Convert to float for comparison
            try:
                if isinstance(order_index_value, (int, float, str)):
                    current_order_index = float(order_index_value)
                else:
                    current_order_index = 0.0
            except (ValueError, TypeError):
                current_order_index = 0.0

            # If current chunk is first (index 0 or less), no previous chunk
            if current_order_index <= 0:
                return None

            # Query for previous chunk directly (order_index = current - 1)
            prev_order_index = current_order_index - 1
            prev_chunks = collection.get(
                where=cast(
                    Where,
                    {
                        "$and": [
                            {"doc_id": doc_id},
                            {"chunk_order_index": prev_order_index},
                        ]
                    },
                )
            )

            if not prev_chunks or not prev_chunks.get("ids"):
                logger.debug(f"No previous chunk found for '{chunk_id}'")
                return None

            # Return the previous chunk
            if prev_chunks["metadatas"]:
                metadata: Metadata = prev_chunks["metadatas"][0]
            else:
                metadata = {}
            if prev_chunks["documents"]:
                document = prev_chunks["documents"][0]
            else:
                document = ""
            prev_chunk_id = prev_chunks.get("ids", [""])[0]

            logger.debug(f"Previous chunk for '{chunk_id}': {prev_chunk_id}")
            return self._build_chunk_from_retrieval(
                chunk_id=prev_chunk_id,
                document=document,
                metadata=dict(metadata),
            )

        except Exception as e:
            logger.error(f"Failed to get previous chunk for '{chunk_id}': {e}")
            return None

    def get_next_chunk(
        self,
        collection_name: str,
        chunk_id: str,
    ) -> Optional[Chunk]:
        """
        Get the next chunk in the same document.

        Chunks are ordered by chunk_order_index. Returns the chunk with
        chunk_order_index = current_index + 1 in the same document.

        Args:
            collection_name: Target collection
            chunk_id: Chunk ID to find next for

        Returns:
            Next Chunk object or None if no next chunk exists
        """
        try:
            collection = self.get_or_create_collection(collection_name)

            # Get the current chunk to find its doc_id and chunk_order_index
            current = collection.get(ids=[chunk_id])
            if not current or not current.get("ids"):
                logger.warning(f"Chunk '{chunk_id}' not found in '{collection_name}'")
                return None

            if current["metadatas"]:
                current_metadatas: Metadata = current["metadatas"][0]
            else:
                current_metadatas = {}
            doc_id = current_metadatas.get("doc_id")

            # Convert to float for comparison
            try:
                order_index_value = dict(current_metadatas).get("chunk_order_index", 0)
                if isinstance(order_index_value, (int, float, str)):
                    current_order_index = float(order_index_value)
                else:
                    current_order_index = 0.0
            except (ValueError, TypeError):
                current_order_index = 0.0

            if not doc_id:
                logger.warning(f"Chunk '{chunk_id}' has no doc_id in metadata")
                return None

            # Query for next chunk directly (order_index = current + 1)
            next_order_index = current_order_index + 1
            next_chunks = collection.get(
                where=cast(
                    Where,
                    {
                        "$and": [
                            {"doc_id": doc_id},
                            {"chunk_order_index": next_order_index},
                        ]
                    },
                )
            )

            if not next_chunks or not next_chunks.get("ids"):
                logger.debug(f"No next chunk found for '{chunk_id}'")
                return None

            # Return the next chunk
            if next_chunks["metadatas"]:
                metadata: Metadata = next_chunks["metadatas"][0]
            else:
                metadata = {}
            if next_chunks["documents"]:
                document = next_chunks["documents"][0]
            else:
                document = ""
            next_chunk_id = next_chunks.get("ids", [""])[0]

            logger.debug(f"Next chunk for '{chunk_id}': {next_chunk_id}")
            return self._build_chunk_from_retrieval(
                chunk_id=next_chunk_id,
                document=document,
                metadata=dict(metadata),
            )

        except Exception as e:
            logger.error(f"Failed to get next chunk for '{chunk_id}': {e}")
            return None

    def get_chunk_by_id(
        self,
        collection_name: str,
        chunk_id: str,
    ) -> Optional[Chunk]:
        """
        Get a specific chunk by chunk_id.

        Args:
            collection_name: Target collection
            chunk_id: Chunk ID to retrieve

        Returns:
            Chunk object or None if not found
        """
        try:
            collection = self.get_or_create_collection(collection_name)
            result = collection.get(ids=[chunk_id])

            if not result or not result.get("ids"):
                logger.debug(f"Chunk '{chunk_id}' not found in '{collection_name}'")
                return None

            if result["metadatas"]:
                metadata: Metadata = result["metadatas"][0]
            else:
                metadata = {}
            if result["documents"]:
                document = result["documents"][0]
            else:
                document = ""

            return self._build_chunk_from_retrieval(
                chunk_id=result["ids"][0],
                document=document,
                metadata=dict(metadata),
            )
        except Exception as e:
            logger.error(f"Failed to get chunk '{chunk_id}': {e}")
            return None

    def get_document_chunks_in_order(
        self,
        collection_name: str,
        doc_id: str,
    ) -> List[Chunk]:
        """
        Get all chunks for a document in chronological order.

        Chunks are ordered by chunk_order_index (from metadata).

        Args:
            collection_name: Target collection
            doc_id: Document ID

        Returns:
            List of Chunk objects ordered by chunk_order_index
        """
        try:
            collection = self.get_or_create_collection(collection_name)

            # Get all chunks from document
            result = collection.get(where=cast(Where, {"doc_id": doc_id}))
            if not result or not result.get("ids"):
                logger.debug(f"No chunks found for doc_id '{doc_id}'")
                return []

            # Build chunk list with timestamps
            chunk_list: List[Dict[str, Any]] = []
            ids = result.get("ids", [])
            metadatas = cast(List[Dict[str, Any]], result.get("metadatas", []))
            documents = cast(List[str], result.get("documents", []))

            for chunk_id, metadata, document in zip(ids, metadatas, documents):
                # Try to get chunk_order_index, default to 0
                chunk_order_index = metadata.get("chunk_order_index")
                if chunk_order_index is None:
                    sort_key = 0.0
                elif isinstance(chunk_order_index, (int, float, str)):
                    try:
                        sort_key = float(chunk_order_index)
                    except (ValueError, TypeError):
                        sort_key = 0.0
                else:
                    sort_key = 0.0

                chunk_list.append(
                    {
                        "chunk_id": chunk_id,
                        "document": document,
                        "metadata": metadata,
                        "sort_key": sort_key,
                        "doc_id": doc_id,
                    }
                )

            # Sort by chunk_order_index
            chunk_list.sort(key=lambda x: x["sort_key"])

            # Convert to Chunk objects
            result_list: List[Chunk] = [
                self._build_chunk_from_retrieval(
                    chunk_id=c["chunk_id"],
                    document=c["document"],
                    metadata=c["metadata"],
                )
                for c in chunk_list
            ]

            logger.debug(f"Got {len(result_list)} chunks for doc_id '{doc_id}'")
            return result_list

        except Exception as e:
            logger.error(f"Failed to get document chunks for '{doc_id}': {e}")
            return []

    def get_chunk_neighbors(
        self,
        collection_name: str,
        chunk_id: str,
        window_size: int = 2,
    ) -> List[Chunk]:
        """
        Get neighboring chunks in a window around the target chunk.

        Returns chunks within window_size distance (before and after).
        The target chunk is included in the results.

        Args:
            collection_name: Target collection
            chunk_id: Center chunk ID
            window_size: Number of chunks on each side to include

        Returns:
            List of neighboring Chunk objects (including the target chunk)
        """
        try:
            current_chunk = self.get_chunk_by_id(collection_name, chunk_id)
            if not current_chunk:
                return []

            neighbors: List[Chunk] = []

            # Get before chunks
            prev_chunk = current_chunk
            before_chunks: List[Chunk] = []
            for _ in range(window_size):
                prev = self.get_previous_chunk(collection_name, prev_chunk.chunk_id)
                if prev:
                    before_chunks.insert(0, prev)  # Insert at beginning for ordering
                    prev_chunk = prev
                else:
                    break

            # Get after chunks
            next_chunk = current_chunk
            after_chunks: List[Chunk] = []
            for _ in range(window_size):
                nxt = self.get_next_chunk(collection_name, next_chunk.chunk_id)
                if nxt:
                    after_chunks.append(nxt)
                    next_chunk = nxt
                else:
                    break

            # Combine: before + current + after
            neighbors.extend(before_chunks)
            neighbors.append(current_chunk)
            neighbors.extend(after_chunks)

            logger.debug(
                f"Got {len(neighbors)} neighbors for chunk '{chunk_id}' with window_size={window_size}"
            )
            return neighbors

        except Exception as e:
            logger.error(f"Failed to get neighbors for chunk '{chunk_id}': {e}")
            return []

    def get_collection_stats(self, collection_name: str) -> Dict[str, Any]:
        """Get statistics for a collection."""
        try:
            collection = self.get_or_create_collection(collection_name)
            count = collection.count()

            return {
                "name": collection_name,
                "document_count": count,
                "mode": self.mode,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to get stats for '{collection_name}': {e}")
            return {}

    def list_collections(self) -> List[str]:
        """List all available collections."""
        collections = self.client.list_collections()
        return [c.name for c in collections]

    def delete_collection(self, name: str) -> None:
        """Delete a collection."""
        with self._lock:
            self.client.delete_collection(name)
            self._collection_cache.pop(name, None)
            logger.info(f"Collection '{name}' deleted")

    # ========== S3 Operations (Production) ==========

    def backup_to_s3(self, collection_name: Optional[str] = None) -> bool:
        """
        Backup collection(s) to S3.

        Args:
            collection_name: Specific collection to backup, or None for all

        Returns:
            True if successful
        """
        if not self.s3_enabled:
            logger.warning("S3 is not enabled. Set s3_enabled=True and provide s3_bucket.")
            return False

        # Get collection list inside lock
        with self._lock:
            collections = [collection_name] if collection_name else self.list_collections()
            timestamp = datetime.now().isoformat().replace(":", "-")

        # Do S3 upload outside lock
        try:
            for coll_name in collections:
                self._backup_collection_to_s3(coll_name, timestamp)

            logger.info(f"Backup completed for {len(collections)} collection(s)")
            return True
        except Exception as e:
            logger.error(f"S3 backup failed: {e}")
            return False

    def _backup_collection_to_s3(self, collection_name: str, timestamp: str) -> None:
        """Backup a single collection to S3."""
        # Create collection directory in S3
        collection_path = self.persist_dir / collection_name
        if not collection_path.exists():
            logger.warning(f"Collection path not found: {collection_path}")
            return

        # Upload files
        s3_prefix = f"{collection_name}/{timestamp}/"
        s3_service = get_s3_service()

        for file_path in collection_path.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(self.persist_dir)
                s3_key = f"{s3_prefix}{relative_path}"

                try:
                    success = s3_service.upload_file_from_path(
                        str(file_path),
                        s3_key,
                    )
                    if success:
                        logger.debug(f"Uploaded {s3_key}")
                    else:
                        logger.error(f"Failed to upload {s3_key}")
                except Exception as e:
                    logger.error(f"Failed to upload {s3_key}: {e}")

    def restore_from_s3(self, collection_name: str, timestamp: Optional[str] = None) -> bool:
        """
        Restore collection from S3.

        Args:
            collection_name: Collection to restore
            timestamp: Specific backup timestamp (latest if not specified)

        Returns:
            True if successful
        """
        if not self.s3_enabled:
            logger.warning("S3 is not enabled.")
            return False

        # Get latest timestamp if needed (no lock needed for S3 query)
        if timestamp is None:
            timestamp = self._get_latest_s3_timestamp(collection_name)

        if not timestamp:
            logger.error(f"No backups found for '{collection_name}'")
            return False

        # Do S3 download outside lock
        try:
            self._restore_collection_from_s3(collection_name, timestamp)
            logger.info(f"Restored '{collection_name}' from {timestamp}")
            return True
        except Exception as e:
            logger.error(f"S3 restore failed: {e}")
            return False

    def _restore_collection_from_s3(self, collection_name: str, timestamp: str) -> None:
        """Restore a single collection from S3."""
        # Delete existing collection
        collection_path = self.persist_dir / collection_name
        if collection_path.exists():
            import shutil

            shutil.rmtree(collection_path)

        # Download files
        s3_prefix = f"{collection_name}/{timestamp}/"
        s3_service = get_s3_service()
        objects = s3_service.list_objects(prefix=s3_prefix)

        for obj in objects:
            s3_key = obj["Key"]
            relative_key = s3_key[len(s3_prefix) :]
            local_path = self.persist_dir / relative_key

            local_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                success = s3_service.download_file_to_path(
                    s3_key,
                    str(local_path),
                )
                if success:
                    logger.debug(f"Downloaded {s3_key}")
                else:
                    logger.error(f"Failed to download {s3_key}")
            except Exception as e:
                logger.error(f"Failed to download {s3_key}: {e}")

        # Invalidate cache (inside lock)
        with self._lock:
            self._collection_cache.pop(collection_name, None)

    def _get_latest_s3_timestamp(self, collection_name: str) -> Optional[str]:
        """Get the latest backup timestamp for a collection."""
        try:
            s3_prefix = f"{collection_name}/"
            s3_service = get_s3_service()
            objects = s3_service.list_objects(prefix=s3_prefix)

            timestamps: Set[str] = set()
            for obj in objects:
                # Extract timestamp from key: collection_name/2026-03-05T12-00-00/...
                parts = obj["Key"].split("/")
                if len(parts) >= 2:
                    timestamps.add(parts[1])

            if timestamps:
                return sorted(timestamps)[-1]  # Latest
            return None
        except Exception as e:
            logger.error(f"Failed to get S3 timestamps: {e}")
            return None

    def get_s3_backups(self, collection_name: str) -> List[Dict[str, Any]]:
        """List all S3 backups for a collection."""
        if not self.s3_enabled:
            return []

        try:
            # Do S3 list operation outside lock
            s3_prefix = f"{collection_name}/"
            s3_service = get_s3_service()
            objects = s3_service.list_objects(prefix=s3_prefix)

            # Aggregate results (minimal lock needed)
            with self._lock:
                backups: Dict[str, Dict[str, Any]] = {}
                for obj in objects:
                    parts = obj["Key"].split("/")
                    if len(parts) >= 2:
                        timestamp = parts[1]
                        if timestamp not in backups:
                            backups[timestamp] = {
                                "collection": collection_name,
                                "timestamp": timestamp,
                                "size_bytes": 0,
                                "file_count": 0,
                            }
                        backups[timestamp]["size_bytes"] += obj["Size"]
                        backups[timestamp]["file_count"] += 1

                return sorted(backups.values(), key=lambda x: x["timestamp"])
        except Exception as e:
            logger.error(f"Failed to list S3 backups: {e}")
            return []

    @contextmanager
    def transaction(self):
        """
        Context manager for transactional operations.
        Useful for batch operations.
        """
        self._lock.acquire()
        try:
            yield self
        finally:
            self._lock.release()


# Singleton instance
_chromadb_store_instance: Optional[ChromaDBStore] = None
_chromadb_store_lock = threading.Lock()


def get_chromadb_store(
    mode: str = "development",
    persist_dir: str = "./data/chromadb",
    s3_enabled: bool = False,
) -> ChromaDBStore:
    """
    Get or create the singleton ChromaDB store instance.

    Args:
        mode: "development" or "production" (only used on first call)
        persist_dir: Local persistence directory (only used on first call)
        s3_enabled: Enable S3 backup (only used on first call)

    Returns:
        Singleton ChromaDBStore instance
    """
    global _chromadb_store_instance

    # Fast path for subsequent calls
    if _chromadb_store_instance is not None:
        return _chromadb_store_instance

    # Double-checked locking for thread safety
    with _chromadb_store_lock:
        if _chromadb_store_instance is None:
            _chromadb_store_instance = ChromaDBStore(
                persist_dir=persist_dir,
                mode=mode,
                s3_enabled=s3_enabled,
            )
            logger.debug("ChromaDB store singleton instance created")

    return _chromadb_store_instance
