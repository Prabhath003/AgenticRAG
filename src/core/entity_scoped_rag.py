# -----------------------------------------------------------------------------
# Copyright (c) 2025 Backend
# All rights reserved.
#
# Developed by:
# Author: Prabhath Chellingi
# GitHub: https://github.com/Prabhath003
# Contact: prabhathchellingi2003@gmail.com
#
# This source code is licensed under the MIT License found in the LICENSE file
# in the root directory of this source tree.
# -----------------------------------------------------------------------------

"""
Entity-Scoped RAG System with Parallel Processing

This module provides isolated RAG indexes per entity for faster access and
parallel processing capabilities across multiple entities.
"""

import os
import threading
from typing import List, Optional, Dict, Any, Set, Tuple
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
from uuid import uuid4
import tiktoken
import json
import time
import numpy as np

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.schema import Document

# from ..infrastructure import chunk_file
from ..config import Config
from ..log_creator import get_file_logger
from ..infrastructure.storage import get_storage_session
from ..infrastructure.clients import FileProcessorClient
from ..infrastructure.metrics import Service, ServiceType
from .models import File
from ..infrastructure.storage.json_storage import JSONStorage

# Initialize tokenizer for cost calculation
try:
    tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
except Exception:
    tiktoken_encoding = None

logger = get_file_logger()

class EntityVectorStore:
    """
    Isolated vector store for a single entity with its own FAISS index.
    Thread-safe operations for concurrent access.
    """

    def __init__(self, entity_id: str, embeddings: HuggingFaceEmbeddings, storage_path: str):
        """
        Initialize entity-scoped vector store

        Args:
            entity_id: Unique entity identifier
            embeddings: Shared embeddings model
            storage_path: Base path for storing entity data. If it's an entity directory
                         (contains {entity_id}_*), use it directly. Otherwise, create
                         subdirectories under storage_path/entities/
        """
        self.entity_id = entity_id
        self.embeddings = embeddings

        # Determine entity directory path
        storage_path_obj = Path(storage_path)

        # Check if storage_path is already an entity directory
        # (either ends with entity_id or is in the format {entity_id}_timestamp)
        if storage_path_obj.name == entity_id or storage_path_obj.name.startswith(f"{entity_id}_"):
            # Use the provided path directly as entity directory
            self.entity_dir = storage_path_obj
        else:
            # Use default structure: storage_path/entities/{entity_id}
            self.entity_dir = storage_path_obj / "entities" / entity_id

        self.entity_dir.mkdir(parents=True, exist_ok=True)

        self.vector_store_path = str(self.entity_dir / "vector_store")
        self.metadata_path = str(self.entity_dir / "metadata.json")

        # Entity-scoped collection name for chunks
        self.chunks_collection = f"{Config.CHUNKS_COLLECTION}_{entity_id}"

        # Entity-scoped data
        self.vector_store: Optional[FAISS] = None
        self.doc_to_chunks: Dict[str, Set[int]] = {}  # doc_id -> chunk indices
        self.chunk_metadata: Dict[int, Dict[str, Any]] = {}  # chunk index -> metadata
        self.document_hashes: Dict[str, str] = {}  # content_hash -> doc_id

        # Thread safety
        self._lock = threading.RLock()

        # Load existing data
        self._load_vector_store()
        self._load_metadata()

        logger.info(f"Initialized EntityVectorStore for entity: {entity_id}")

    def _load_vector_store(self):
        """Load entity-specific vector store"""
        if os.path.exists(self.vector_store_path):
            try:
                with self._lock:
                    self.vector_store = FAISS.load_local(
                        self.vector_store_path,
                        self.embeddings,
                        allow_dangerous_deserialization=True
                    )
                logger.info(f"Loaded vector store for entity {self.entity_id}")
            except Exception as e:
                logger.error(f"Failed to load vector store for {self.entity_id}: {e}")

    def _save_vector_store(self):
        """Save entity-specific vector store"""
        if self.vector_store:
            try:
                with self._lock:
                    self.vector_store.save_local(self.vector_store_path)
                logger.debug(f"Saved vector store for entity {self.entity_id}")
            except Exception as e:
                logger.error(f"Failed to save vector store for {self.entity_id}: {e}")

    def _load_metadata(self):
        """Load entity metadata from entity directory storage"""
        try:

            # Use entity-specific JSON storage
            entity_storage = JSONStorage(str(self.entity_dir))

            # Load documents from entity directory to get content hashes (excluding deleted docs)
            docs = entity_storage.find("documents", {"_id": {"$not": {"$regex": "^\\[DELETED\\]"}}})

            for doc in docs:
                content_hash = doc.get("content_hash")
                doc_id = doc.get("_id")  # Document ID is stored as _id in JSON storage
                if content_hash and doc_id:
                    self.document_hashes[content_hash] = doc_id

            logger.debug(f"Loaded {len(self.document_hashes)} document hashes for {self.entity_id} from entity directory")
        except Exception as e:
            logger.error(f"Failed to load metadata for {self.entity_id}: {e}")

    def add_document(self, file: File, metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Add a document to this entity's vector store

        Args:
            file_path: Path to document file
            metadata: Additional metadata

        Returns:
            Document info dict or None on failure
        """
        all_services_used: List[Service] = []
        try:

            # Quick duplicate check without lock (read-only)
            content_hash = self._calculate_file_hash_from_content(file.content)
            if content_hash:
                # First check in-memory cache
                if content_hash in self.document_hashes:
                    existing_doc_id = self.document_hashes[content_hash]
                    logger.info(f"Document already exists for entity {self.entity_id}: {existing_doc_id}")
                    return {
                        "doc_id": existing_doc_id,
                        "entity_id": self.entity_id,
                        "is_duplicate": True
                    }

                # Also check in entity storage directly (for race condition safety)
                try:
                    entity_storage = JSONStorage(str(self.entity_dir))
                    # Only find non-deleted documents (exclude [DELETED] prefixed docs)
                    existing_doc = entity_storage.find_one("documents", {"content_hash": content_hash, "_id": {"$not": {"$regex": "^\\[DELETED\\]"}}})
                    if existing_doc:
                        existing_doc_id = existing_doc.get("_id")
                        # Update cache
                        self.document_hashes[content_hash] = existing_doc_id
                        logger.info(f"Document already exists for entity {self.entity_id}: {existing_doc_id} (found in storage)")
                        return {
                            "doc_id": existing_doc_id,
                            "entity_id": self.entity_id,
                            "is_duplicate": True
                        }
                except Exception as e:
                    logger.debug(f"Could not check entity storage for duplicates: {e}")

            # Process document outside of lock (CPU-intensive)
            chunks, services_used = self._process_document(file, metadata)
            all_services_used.extend(services_used)
            if not chunks:
                return None

            doc_id = chunks[0]['metadata']['doc_id']

            # Only lock for vector store updates (critical section)
            with self._lock:
                # Double-check for duplicates after acquiring lock (race condition safety)
                if content_hash:
                    # Check cache first
                    if content_hash in self.document_hashes:
                        existing_doc_id = self.document_hashes[content_hash]
                        logger.info(f"Document already exists (race condition detected): {existing_doc_id}")
                        return {
                            "doc_id": existing_doc_id,
                            "entity_id": self.entity_id,
                            "is_duplicate": True
                        }

                    # Check storage again for safety
                    try:
                        entity_storage = JSONStorage(str(self.entity_dir))
                        # Only find non-deleted documents (exclude [DELETED] prefixed docs)
                        existing_docs = entity_storage.find("documents", {"content_hash": content_hash, "_id": {"$not": {"$regex": "^\\[DELETED\\]"}}})
                        if existing_docs:
                            existing_doc_id = existing_docs[0].get("_id")
                            self.document_hashes[content_hash] = existing_doc_id
                            logger.info(f"Document already exists (race condition detected in storage): {existing_doc_id}")
                            return {
                                "doc_id": existing_doc_id,
                                "entity_id": self.entity_id,
                                "is_duplicate": True
                            }
                    except Exception as e:
                        logger.debug(f"Could not verify duplicates in storage during lock: {e}")

                # Add to vector store
                services_used = self._add_chunks_to_vector_store(chunks)
                all_services_used.extend(services_used)

                if content_hash:
                    self.document_hashes[content_hash] = doc_id

                # Save vector store
                self._save_vector_store()

            # Save metadata and chunks to storage outside of lock (uses its own locks)
            self._save_document_metadata(doc_id, file, content_hash, metadata, chunks, all_services_used)

            logger.info(f"Added document {doc_id} to entity {self.entity_id}")

            # Calculate total cost from all services
            total_cost_usd = sum(service.estimated_cost_usd for service in all_services_used)

            return {
                "doc_id": doc_id,
                "entity_id": self.entity_id,
                "chunks_count": len(chunks),
                "is_duplicate": False,
                "services_used": [service.to_dict() for service in all_services_used],
                "estimated_cost_usd": round(total_cost_usd, 6)
            }

        except Exception as e:
            logger.error(f"Failed to add document to entity {self.entity_id}: {e}")
            return None

    def _calculate_file_hash_from_file_path(self, file_path: str) -> Optional[str]:
        """Calculate SHA-256 hash of file content"""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception as e:
            logger.error(f"Failed to calculate hash: {e}")
            return None

    def _calculate_file_hash_from_content(self, content: bytes) -> Optional[str]:
        """Calculate SHA-256 hash of file content"""
        try:
            return hashlib.sha256(content).hexdigest()
        except Exception as e:
            logger.error(f"Failed to calculate hash: {e}")
            return None

    def add_chunk(self, chunk_text: str, chunk_id: str, doc_id: str,
                  metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Add a single pre-chunked item directly to the vector store

        Args:
            chunk_text: The text content of the chunk
            chunk_id: Unique identifier for the chunk
            doc_id: Document ID this chunk belongs to
            metadata: Metadata for the chunk

        Returns:
            True if successful, False otherwise
        """
        try:
            # Format the chunk for vector store
            chunk_dict = {
                "chunk": {"text": chunk_text},
                "metadata": {
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    **(metadata or {})
                }
            }

            # Add to vector store using existing method
            services = self._add_chunks_to_vector_store([chunk_dict])
            logger.info(f"Successfully added chunk {chunk_id} to vector store")
            return True

        except Exception as e:
            logger.error(f"Error adding chunk {chunk_id}: {e}")
            return False

    def add_chunks_batch(self, chunks: List[Dict[str, Any]], doc_id: str,
                        new_chunks_data: List[Dict[str, Any]]) -> bool:
        """
        Add multiple pre-chunked items to the vector store with full metadata persistence

        This follows the same process as add_document but skips file processing.

        Args:
            chunks: List of formatted chunks with 'chunk' and 'metadata' keys
            doc_id: Document ID for all chunks
            new_chunks_data: Original chunk data for cross-referencing

        Returns:
            True if successful, False otherwise
        """
        try:
            if not chunks:
                return True

            # Add chunks to vector store
            with self._lock:
                services_used = self._add_chunks_to_vector_store(chunks)
                self._save_vector_store()

            # Save document and chunk metadata to storage (outside lock to avoid deadlock)
            self._save_chunks_metadata(doc_id, chunks)

            logger.info(f"Successfully added {len(chunks)} chunks to vector store and saved metadata")
            return True

        except Exception as e:
            logger.error(f"Error adding chunks batch: {e}")
            return False

    def _save_chunks_metadata(self, doc_id: str, chunks: List[Dict[str, Any]]) -> None:
        """
        Save chunk metadata to entity storage (without creating document entry)

        Only saves chunks themselves. Document entries are created during normal file uploads,
        not during chunk API ingestion.

        Args:
            doc_id: Document ID
            chunks: List of chunks with metadata
        """
        try:
            entity_storage = JSONStorage(str(self.entity_dir))

            # Save each chunk to chunks collection
            for chunk in chunks:
                chunk_id = chunk.get("metadata", {}).get("chunk_id")
                if chunk_id:
                    chunk_doc = {
                        "_id": chunk_id,
                        "entity_id": self.entity_id,
                        **chunk
                    }
                    entity_storage.update_one(
                        "chunks",
                        {"_id": chunk_id},
                        {"$set": chunk_doc},
                        upsert=True
                    )

            logger.debug(f"Saved {len(chunks)} chunks metadata for doc {doc_id} to entity directory {self.entity_dir}")

        except Exception as e:
            logger.error(f"Failed to save chunks metadata: {e}")

    def _process_document(self, file: File, metadata: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Service]]:
        """Process document into chunks"""
        all_services_used: List[Service] = []
        try:
            # Extract source from metadata, fallback to file_path
            source = metadata.get('source', file.filename) if metadata else file.filename
            
            file_processing_client = FileProcessorClient()
            response = file_processing_client.chunk_file_from_bytes(file.content, file.filename, source)

            task_id = response.get("task_id")
            if not response.get("results", {}).values():
                raise Exception("File processing failed")
            chunks = list(response.get("results", {}).values())[0]
            all_services_used.append(Service(
                service_type=ServiceType.FILE_PROCESSOR,
                breakdown=list(response.get("metrics", {}).values())[0] if response.get("metrics", {}).values() else {},
                estimated_cost_usd=response.get("estimated_cost_usd", 0)
            ))

            # Generate document ID
            doc_id = f"doc_{str(uuid4())[:13]}"

            # Add metadata to chunks
            doc_metadata: Dict[str, Any] = {
                "entity_ids": [self.entity_id],
                "doc_id": doc_id,
                "doc_name": file.filename,
                "source": source,
                "file_type": Path(file.filename).suffix.lower(),
                "indexed_at": datetime.now(timezone.utc),
                "file_processor_task_id": task_id
            }

            if metadata:
                doc_metadata.update(metadata)

            # Convert API chunks to our format
            formatted_chunks: List[Dict[str, Any]] = []
            for chunk in chunks:
                # API chunks format: {content, chunk_order_index, source, metadata}
                formatted_chunk: Dict[str, Dict[str, Any]] = {
                    'chunk': chunk.get("content", {}),
                    'metadata': doc_metadata.copy()
                }

                # Merge any existing metadata from API
                if 'metadata' in chunk:
                    formatted_chunk['metadata'].update(chunk['metadata'])

                formatted_chunks.append(formatted_chunk)

            return formatted_chunks, all_services_used

        except Exception as e:
            logger.error(f"Error processing document {file.filename}: {e}")
            import traceback
            traceback.print_exc()
            return [], all_services_used

    def _simple_chunk_file(self, file_path: str) -> List[Dict[str, Any]]:
        """Simple fallback chunking when API is not available"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Simple chunking - split by size
            chunk_size = 1000
            chunks = []

            for i in range(0, len(content), chunk_size):
                chunk_content = content[i:i + chunk_size]
                chunks.append({
                    'content': chunk_content,
                    'chunk_order_index': len(chunks),
                    'source': file_path,
                    'metadata': {
                        'tokens': len(chunk_content.split())
                    }
                })

            return chunks if chunks else [{'content': content, 'chunk_order_index': 0, 'source': file_path}]

        except Exception as e:
            logger.error(f"Simple chunking failed: {e}")
            return []

    def _add_chunks_to_vector_store(self, chunks: List[Dict[str, Any]]) -> List[Service]:
        """
        Add chunks to the vector store and return service usage metrics.

        Args:
            chunks: List of chunk dictionaries to add to vector store

        Returns:
            List of Service objects tracking embedding operation costs and metrics
        """

        start_time = time.time()
        all_services_used: List[Service] = []

        try:
            documents = [
                Document(
                    page_content=json.dumps(chunk['chunk'], indent=1),
                    metadata={
                        'metadata': chunk.get("metadata"),
                        'chunk': chunk.get('chunk')
                    }
                )
                for chunk in chunks
            ]

            if self.vector_store:
                start_idx = len(self.chunk_metadata)
                self.vector_store.add_documents(documents)
            else:
                start_idx = 0
                self.vector_store = FAISS.from_documents(documents, self.embeddings)

            # Update chunk indices
            self._update_chunk_indices(chunks, start_idx)

            # Calculate metrics from chunk metadata
            elapsed_time = time.time() - start_time
            num_chunks = len(chunks)

            # Extract token count from chunk metadata (already calculated during file processing)
            total_tokens = 0
            for chunk in chunks:
                metadata = chunk.get("metadata", {})
                # Check for tokens in metadata from file processor
                if isinstance(metadata, dict):
                    total_tokens += metadata.get("tokens", 0)

            # Create service tracking for embedding operation (GPU-intensive)
            # Estimate cost based on tokens processed through embedding model
            # Typical embedding costs: ~$0.02 per 1M tokens for local GPU processing
            estimated_cost = (total_tokens / 1_000_000) * 0.02

            embedding_service = Service(
                service_type=ServiceType.TRANSFORMER,
                breakdown={
                    "operation": "vector_indexing",
                    "chunks_processed": num_chunks,
                    "total_tokens": total_tokens,
                    "processing_time_seconds": round(elapsed_time, 4),
                    "embeddings_model": Config.EMBEDDINGS_MODEL,
                    "is_local_model": True,
                    "gpu_accelerated": True
                },
                estimated_cost_usd=round(estimated_cost, 6)
            )

            all_services_used.append(embedding_service)
            logger.debug(
                f"Vector store indexing: {num_chunks} chunks, "
                f"{total_tokens} tokens, ${estimated_cost:.6f} cost in {elapsed_time:.2f}s"
            )

        except Exception as e:
            logger.error(f"Error adding chunks to vector store: {e}")
            raise

        return all_services_used

    def _update_chunk_indices(self, chunks: List[Dict[str, Any]], start_idx: int):
        """Update chunk metadata indices"""
        for i, chunk in enumerate(chunks):
            chunk_idx = start_idx + i
            metadata = chunk['metadata']

            self.chunk_metadata[chunk_idx] = metadata

            doc_id = metadata.get('doc_id')
            if doc_id:
                if doc_id not in self.doc_to_chunks:
                    self.doc_to_chunks[doc_id] = set()
                self.doc_to_chunks[doc_id].add(chunk_idx)

    def _save_document_metadata(self, doc_id: str, file: File, content_hash: Optional[str],
                                metadata: Optional[Dict[str, Any]], chunks: List[Dict[str, Any]],
                                services_used: Optional[List[Service]] = None):
        """Save document metadata and chunks to storage (JSON storage in entity directory)"""
        try:
            # Use entity-specific JSON storage
            entity_storage = JSONStorage(str(self.entity_dir))

            # Calculate total estimated cost
            total_cost_usd = sum(service.estimated_cost_usd for service in (services_used or []))

            mapping_data = {
                "doc_name": os.path.basename(file.filename),
                "file_size": len(file.content) if file.content else 0,
                "indexed_at": datetime.now(timezone.utc),
                "estimated_cost_usd": round(total_cost_usd, 6),
                "services_used": [service.to_dict() for service in (services_used or [])]
            }

            if content_hash:
                mapping_data["content_hash"] = content_hash

            if metadata:
                mapping_data["metadata"] = metadata

            # Save document metadata to entity-specific storage
            # Explicitly set _id to doc_id for consistent querying by _id
            mapping_data["_id"] = doc_id
            mapping_data["doc_id"] = doc_id
            entity_storage.update_one(
                "documents",
                {"_id": doc_id},
                {"$set": mapping_data},
                upsert=True
            )

            # Save chunks to entity-specific storage (chunks collection)
            for chunk in chunks:
                chunk_id = f"chunk_{chunk['metadata']['doc_id']}_{chunk['chunk']['chunk_order_index']}"
                # Prepare chunk document
                chunk_doc = {key: value for key, value in chunk.items() if key != "_id"}
                chunk_doc['entity_id'] = self.entity_id

                entity_storage.update_one(
                    "chunks",
                    {"_id": chunk_id},
                    {"$set": chunk_doc},
                    upsert=True
                )

            logger.debug(f"Saved document {doc_id} with {len(chunks)} chunks to entity directory {self.entity_dir}")

        except Exception as e:
            logger.error(f"Failed to save document metadata: {e}")

    def search(self, query: str, k: int = 5, doc_ids: Optional[List[str]] = None) -> Tuple[List[Document], List[Service]]:
        """
        Search within this entity's vector store with service tracking

        Args:
            query: Search query
            k: Number of results
            doc_ids: Optional filter by document IDs

        Returns:
            Tuple of (List of matching documents, List of services used for embedding)
        """
        with self._lock:
            if not self.vector_store:
                logger.warning(f"No vector store for entity {self.entity_id}")
                return [], []

            try:
                # Calculate exact transformer cost for query embedding
                query_tokens = 0
                if tiktoken_encoding:
                    query_tokens = len(tiktoken_encoding.encode(query))
                else:
                    # Fallback to approximate tokenization if tiktoken fails
                    query_tokens = len(query.split()) + 10

                # Transformer cost: $0.02 per 1M tokens
                embedding_cost = (query_tokens / 1_000_000) * 0.02
                services_used = [Service(
                    ServiceType.TRANSFORMER,
                    {
                        "operation": "query_embedding",
                        "model": Config.EMBEDDINGS_MODEL,
                        "query_tokens": query_tokens,
                        "is_local_model": True
                    },
                    embedding_cost
                )]

                if doc_ids:
                    # Filter by document IDs
                    results = self._search_with_doc_filter(query, k, doc_ids)
                else:
                    # Search all documents in this entity
                    results = self.vector_store.similarity_search(query, k=k)

                return results, services_used

            except Exception as e:
                logger.error(f"Search failed for entity {self.entity_id}: {e}")
                return [], []

    def _search_with_doc_filter(self, query: str, k: int, doc_ids: List[str]) -> List[Document]:
        """Search with document ID filtering"""
        # Get relevant chunk indices
        relevant_indices = set()
        for doc_id in doc_ids:
            if doc_id in self.doc_to_chunks:
                relevant_indices.update(self.doc_to_chunks[doc_id])

        if not relevant_indices:
            return []

        # Filter search results
        all_results = self.vector_store.similarity_search(query, k=k*3)  # Get more to filter
        filtered_results = []

        for doc in all_results:
            doc_id = doc.metadata.get('metadata', {}).get('doc_id')
            if doc_id in doc_ids:
                filtered_results.append(doc)
                if len(filtered_results) >= k:
                    break

        return filtered_results

    def delete_document(self, doc_id: str) -> bool:
        """Delete document chunks from entity vector store

        Removes:
        - Chunk entries from JSONStorage (NO document metadata deletion - handled by manager.delete_file)
        - Deleted chunks from in-memory FAISS index (via rebuild)

        Note: No cost operation - only I/O and FAISS rebuild from stored chunks, no re-embedding
        """
        with self._lock:
            try:
                entity_storage = JSONStorage(str(self.entity_dir))

                # Delete ALL chunks associated with this document from JSONStorage
                deleted_chunk_count = 0
                all_chunks = entity_storage.find("chunks", {})
                for chunk in all_chunks:
                    chunk_metadata = chunk.get("metadata", {})
                    if chunk_metadata.get("doc_id") == doc_id:
                        entity_storage.delete_one("chunks", {"_id": chunk["_id"]})
                        deleted_chunk_count += 1

                # Delete specific indices from FAISS (no rebuild, no re-embedding)
                if self.vector_store and doc_id in self.doc_to_chunks:
                    chunk_indices = list(self.doc_to_chunks[doc_id])
                    # Delete indices from FAISS in descending order to avoid index shifting
                    for idx in sorted(chunk_indices, reverse=True):
                        try:
                            # FAISS's remove_ids removes entries by their internal ID
                            self.vector_store.index.remove_ids(np.array([idx], dtype=np.int64))
                        except Exception as e:
                            logger.debug(f"Could not remove FAISS index {idx}: {e}")

                # Remove from local metadata cache
                if doc_id in self.doc_to_chunks:
                    del self.doc_to_chunks[doc_id]

                logger.info(f"Deleted {deleted_chunk_count} chunks for document {doc_id} from vector store "
                           f"(removed {len(chunk_indices) if doc_id in self.doc_to_chunks or not self.vector_store else 0} FAISS indices)")
                return True

            except Exception as e:
                logger.error(f"Failed to delete chunks for document {doc_id}: {e}")
                return False

    def _rebuild_vector_store(self):
        """Rebuild the vector store from JSONStorage chunks (no re-embedding, just I/O)

        This is used to drop deleted chunks from the FAISS index by excluding them
        when rebuilding. Chunks are already processed and stored, so this just
        reconstructs the in-memory FAISS index without re-embedding.
        """
        try:
            entity_storage = JSONStorage(str(self.entity_dir))

            # Load remaining chunks from JSONStorage (already processed, no re-chunking/embedding)
            all_chunks: List[Dict[str, Any]] = []
            stored_chunks = entity_storage.find("chunks", {})

            for chunk_doc in stored_chunks:
                # Skip [DELETED] chunks - they should not be in the index
                if not chunk_doc.get("_id", "").startswith("[DELETED]"):
                    all_chunks.append(chunk_doc)

            # Rebuild FAISS index with remaining chunks (zero embedding cost, just I/O)
            if all_chunks:
                documents = [
                    Document(
                        page_content=json.dumps(chunk.get('chunk', {}), indent=1),
                        metadata={
                            'metadata': chunk.get("metadata"),
                            'chunk': chunk.get('chunk')
                        }
                    )
                    for chunk in all_chunks
                ]
                self.vector_store = FAISS.from_documents(documents, self.embeddings)
                self._update_chunk_indices(all_chunks, 0)
                self._save_vector_store()
            else:
                self.vector_store = None

            logger.info(f"Rebuilt vector store for entity {self.entity_id} with {len(all_chunks)} chunks")

        except Exception as e:
            logger.error(f"Failed to rebuild vector store: {e}")

    def get_chunk_by_id(self, doc_id: str, chunk_order_index: int) -> Optional[Dict[str, Any]]:
        """
        Retrieve a specific chunk by document ID and chunk order index

        Args:
            doc_id: Document identifier
            chunk_order_index: Chunk order index

        Returns:
            Chunk data or None if not found
        """
        try:
            # Use entity-scoped storage
            entity_storage = JSONStorage(str(self.entity_dir))
            chunk_id = f"chunk_{doc_id}_{chunk_order_index}"
            chunk = entity_storage.find_one("chunks", {"_id": chunk_id})

            if chunk:
                logger.debug(f"Retrieved chunk {chunk_id} from entity {self.entity_id}")
                return chunk
            else:
                logger.warning(f"Chunk {chunk_id} not found in entity {self.entity_id}")
                return None
        except Exception as e:
            logger.error(f"Failed to get chunk {doc_id}:{chunk_order_index}: {e}")
            return None

    def get_previous_chunk(self, doc_id: str, current_chunk_index: int) -> Optional[Dict[str, Any]]:
        """
        Get the previous chunk in document order

        Args:
            doc_id: Document identifier
            current_chunk_index: Current chunk order index

        Returns:
            Previous chunk or None if at beginning
        """
        if current_chunk_index <= 0:
            return None
        return self.get_chunk_by_id(doc_id, current_chunk_index - 1)

    def get_next_chunk(self, doc_id: str, current_chunk_index: int) -> Optional[Dict[str, Any]]:
        """
        Get the next chunk in document order

        Args:
            doc_id: Document identifier
            current_chunk_index: Current chunk order index

        Returns:
            Next chunk or None if at end
        """
        return self.get_chunk_by_id(doc_id, current_chunk_index + 1)

    def get_chunk_context(self, doc_id: str, chunk_order_index: int, context_size: int = 1) -> Dict[str, Any]:
        """
        Get a chunk with surrounding context chunks

        Args:
            doc_id: Document identifier
            chunk_order_index: Target chunk order index
            context_size: Number of chunks before and after to include

        Returns:
            Dict with 'current', 'before', and 'after' chunks
        """
        current = self.get_chunk_by_id(doc_id, chunk_order_index)

        if not current:
            return {"current": None, "before": [], "after": []}

        before_chunks = []
        for i in range(context_size, 0, -1):
            prev_chunk = self.get_chunk_by_id(doc_id, chunk_order_index - i)
            if prev_chunk:
                before_chunks.append(prev_chunk)

        after_chunks = []
        for i in range(1, context_size + 1):
            next_chunk = self.get_chunk_by_id(doc_id, chunk_order_index + i)
            if next_chunk:
                after_chunks.append(next_chunk)

        return {
            "current": current,
            "before": before_chunks,
            "after": after_chunks
        }

    def get_document_chunks_in_order(self, doc_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve all chunks for a document in order

        Args:
            doc_id: Document identifier

        Returns:
            List of chunks sorted by chunk_order_index
        """
        try:
            # Use entity-scoped storage
            entity_storage = JSONStorage(str(self.entity_dir))
            chunks = list(entity_storage.find("chunks",
                {"metadata.doc_id": doc_id}
            ))

            logger.debug(f"Retrieved {len(chunks)} chunks for document {doc_id} from entity {self.entity_id}")
            return chunks
        except Exception as e:
            logger.error(f"Failed to get chunks for document {doc_id}: {e}")
            return []

    def get_chunk_neighbors(self, doc_id: str, chunk_order_index: int, window_size: int = 2) -> List[Dict[str, Any]]:
        """
        Get neighboring chunks within a window around the target chunk

        Args:
            doc_id: Document identifier
            chunk_order_index: Target chunk order index
            window_size: Number of chunks on each side (default 2)

        Returns:
            List of chunks including the target and neighbors
        """
        chunks = []
        start_idx = max(0, chunk_order_index - window_size)
        end_idx = chunk_order_index + window_size

        for i in range(start_idx, end_idx + 1):
            chunk = self.get_chunk_by_id(doc_id, i)
            if chunk:
                chunks.append(chunk)

        return chunks
    
    def get_entity_documents(self) -> List[Dict[str, Any]]:
        """Get all documents associated with an entity"""
        logger.debug(f"Getting documents for entity: {self.entity_id}")

        try:
            # Use entity-scoped storage instead of global storage
            entity_storage = JSONStorage(str(self.entity_dir))

            # Get all documents from entity-scoped storage (excluding deleted ones)
            docs = entity_storage.find("documents",
                {"_id": {"$not": {"$regex": "^\\[DELETED\\]"}}}
            )

            # Ensure each document has doc_id field for API and agent compatibility
            # If doc_id is missing, use _id as fallback
            for doc in docs:
                if "doc_id" not in doc and "_id" in doc:
                    doc["doc_id"] = doc["_id"]

            logger.info(f"Found {len(docs)} documents for entity {self.entity_id}")
            return docs

        except Exception as e:
            logger.error(f"Error getting entity documents {self.entity_id}: {e}")
            return []

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for this entity's vector store"""
        with self._lock:
            return {
                "entity_id": self.entity_id,
                "total_documents": len(self.doc_to_chunks),
                "total_chunks": len(self.chunk_metadata),
                "has_vector_store": self.vector_store is not None
            }


class EntityRAGManager:
    """
    Manager for entity-scoped RAG systems with parallel processing support.
    Maintains isolated vector stores per entity.
    """

    def __init__(self):
        """Initialize the entity RAG manager"""
        logger.info("Initializing EntityRAGManager")

        # Shared embeddings model (thread-safe)
        self.embeddings = HuggingFaceEmbeddings(
            model_name=Config.EMBEDDINGS_MODEL
        )

        # Storage path
        self.storage_path = os.path.join(Config.DATA_DIR, "entity_scoped")
        os.makedirs(self.storage_path, exist_ok=True)

        # Entity stores cache
        self._entity_stores: Dict[str, EntityVectorStore] = {}
        self._stores_lock = threading.RLock()

        # Thread pool for parallel operations
        self._thread_pool = ThreadPoolExecutor(max_workers=os.cpu_count() or 4)

        logger.info("EntityRAGManager initialized successfully")

    def get_entity_store(self, entity_id: str, entity_dir: Optional[str] = None) -> EntityVectorStore:
        """
        Get or create an entity-scoped vector store

        Args:
            entity_id: Entity identifier
            entity_dir: Optional entity directory path (overrides default storage_path)

        Returns:
            EntityVectorStore instance
        """
        with self._stores_lock:
            if entity_id not in self._entity_stores:
                # Use provided entity_dir if available, otherwise use default storage path
                storage_path = entity_dir if entity_dir else self.storage_path
                self._entity_stores[entity_id] = EntityVectorStore(
                    entity_id=entity_id,
                    embeddings=self.embeddings,
                    storage_path=storage_path
                )
            else:
                # If entity already cached but entity_dir was provided and is different,
                # we may need to recreate the store with the correct path
                # This handles cases where the same entity is accessed with different directories
                existing_store = self._entity_stores[entity_id]
                if entity_dir and str(entity_dir) != str(existing_store.entity_dir):
                    logger.warning(f"Entity {entity_id} already cached with different directory. "
                                   f"Existing: {existing_store.entity_dir}, New: {entity_dir}. "
                                   f"Using existing cache.")
            return self._entity_stores[entity_id]

    def add_document(self, entity_id: str, file: File,
                    metadata: Optional[Dict[str, Any]] = None,
                    entity_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Add a document to an entity's vector store

        Args:
            entity_id: Entity identifier
            file: File object to index
            metadata: Additional metadata
            entity_dir: Entity directory path for storing vector store and metadata

        Returns:
            Document info dict with doc_id, chunks_count, services_used, etc.
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.add_document(file, metadata)

    def add_chunk(self, entity_id: str, chunk_text: str, chunk_id: str, doc_id: str,
                  metadata: Optional[Dict[str, Any]] = None,
                  entity_dir: Optional[str] = None) -> bool:
        """
        Add a single pre-chunked item directly to an entity's vector store

        Args:
            entity_id: Entity identifier
            chunk_text: The text content of the chunk
            chunk_id: Unique identifier for the chunk
            doc_id: Document ID this chunk belongs to
            metadata: Additional metadata
            entity_dir: Entity directory path for storing vector store and metadata

        Returns:
            True if successful, False otherwise
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.add_chunk(chunk_text, chunk_id, doc_id, metadata)

    def add_chunks_batch(self, entity_id: str, chunks: List[Dict[str, Any]], doc_id: str,
                        entity_dir: Optional[str] = None,
                        new_chunks_data: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        Add multiple pre-chunked items to an entity's vector store with full metadata persistence

        Args:
            entity_id: Entity identifier
            chunks: List of formatted chunks with 'chunk' and 'metadata' keys
            doc_id: Document ID for all chunks
            entity_dir: Entity directory path for storing vector store and metadata
            new_chunks_data: Original chunk data for cross-referencing

        Returns:
            True if successful, False otherwise
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.add_chunks_batch(chunks, doc_id, new_chunks_data or [])

    def add_documents_parallel(self, entity_documents: Dict[str, List[str]],
                              metadata: Optional[Dict[str, Any]] = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        Add documents to multiple entities in parallel

        Args:
            entity_documents: Dict mapping entity_id -> list of file paths
            metadata: Optional metadata to add to all documents

        Returns:
            Dict mapping entity_id -> list of document info dicts
        """
        results = {}
        futures = {}

        # Submit all tasks
        for entity_id, file_paths in entity_documents.items():
            entity_futures = []
            for file_path in file_paths:
                future = self._thread_pool.submit(
                    self.add_document,
                    entity_id,
                    file_path,
                    metadata
                )
                entity_futures.append((file_path, future))
            futures[entity_id] = entity_futures

        # Collect results
        for entity_id, entity_futures in futures.items():
            results[entity_id] = []
            for file_path, future in entity_futures:
                try:
                    result = future.result(timeout=300)  # 5 minute timeout per document
                    if result:
                        results[entity_id].append(result)
                except Exception as e:
                    logger.error(f"Failed to process {file_path} for entity {entity_id}: {e}")

        return results

    def search(self, entity_id: str, query: str, k: int = 5,
              doc_ids: Optional[List[str]] = None, entity_dir: Optional[str] = None) -> List[Document]:
        """
        Search within a specific entity's documents

        Args:
            entity_id: Entity identifier
            query: Search query
            k: Number of results
            doc_ids: Optional document ID filter
            entity_dir: Optional entity directory path (required if entity data is in temp/isolated location)

        Returns:
            List of matching documents
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.search(query, k, doc_ids)

    def search_multiple_entities(self, entity_ids: List[str], query: str,
                                 k: int = 5) -> Dict[str, List[Document]]:
        """
        Search across multiple entities in parallel

        Args:
            entity_ids: List of entity identifiers
            query: Search query
            k: Number of results per entity

        Returns:
            Dict mapping entity_id -> list of documents
        """
        results = {}
        futures = {}

        # Submit all search tasks
        for entity_id in entity_ids:
            future = self._thread_pool.submit(
                self.search,
                entity_id,
                query,
                k
            )
            futures[entity_id] = future

        # Collect results
        for entity_id, future in futures.items():
            try:
                results[entity_id] = future.result(timeout=30)  # 30 second timeout
            except Exception as e:
                logger.error(f"Search failed for entity {entity_id}: {e}")
                results[entity_id] = []

        return results

    def delete_document(self, entity_id: str, doc_id: str, entity_dir: Optional[str] = None) -> bool:
        """Delete a document from an entity's store

        Args:
            entity_id: Entity identifier
            doc_id: Document ID to delete
            entity_dir: Optional entity directory path (required if entity data is in temp/isolated location)

        Returns:
            True if deletion successful, False otherwise
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.delete_document(doc_id)

    def get_entity_stats(self, entity_id: str, entity_dir: Optional[str] = None) -> Dict[str, Any]:
        """Get statistics for an entity

        Args:
            entity_id: Entity identifier
            entity_dir: Optional entity directory path (required if entity data is in temp/isolated location)

        Returns:
            Dictionary with entity statistics
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.get_stats()

    def get_chunk_by_id(self, entity_id: str, doc_id: str, chunk_order_index: int, entity_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get a specific chunk by ID for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            chunk_order_index: Chunk order index
            entity_dir: Optional entity directory path (required if entity data is in temp/isolated location)

        Returns:
            Chunk data or None
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.get_chunk_by_id(doc_id, chunk_order_index)

    def get_previous_chunk(self, entity_id: str, doc_id: str, current_chunk_index: int, entity_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get the previous chunk for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            current_chunk_index: Current chunk order index
            entity_dir: Optional entity directory path (required if entity data is in temp/isolated location)

        Returns:
            Previous chunk or None
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.get_previous_chunk(doc_id, current_chunk_index)

    def get_next_chunk(self, entity_id: str, doc_id: str, current_chunk_index: int, entity_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get the next chunk for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            current_chunk_index: Current chunk order index
            entity_dir: Optional entity directory path (required if entity data is in temp/isolated location)

        Returns:
            Next chunk or None
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.get_next_chunk(doc_id, current_chunk_index)

    def get_chunk_context(self, entity_id: str, doc_id: str, chunk_order_index: int,
                         context_size: int = 1, entity_dir: Optional[str] = None) -> Dict[str, Any]:
        """
        Get chunk with context for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            chunk_order_index: Target chunk order index
            context_size: Number of chunks before/after
            entity_dir: Optional entity directory path (required if entity data is in temp/isolated location)

        Returns:
            Dict with current, before, and after chunks
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.get_chunk_context(doc_id, chunk_order_index, context_size)

    def get_document_chunks_in_order(self, entity_id: str, doc_id: str, entity_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all chunks for a document in order for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            entity_dir: Optional entity directory path (required if entity data is in temp/isolated location)

        Returns:
            List of chunks in order
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.get_document_chunks_in_order(doc_id)

    def get_chunk_neighbors(self, entity_id: str, doc_id: str, chunk_order_index: int,
                           window_size: int = 2, entity_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get neighboring chunks for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            chunk_order_index: Target chunk order index
            window_size: Window size on each side
            entity_dir: Optional entity directory path (required if entity data is in temp/isolated location)

        Returns:
            List of neighboring chunks
        """
        store = self.get_entity_store(entity_id, entity_dir)
        return store.get_chunk_neighbors(doc_id, chunk_order_index, window_size)

    def get_all_entity_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all entities"""
        with self._stores_lock:
            return {
                entity_id: store.get_stats()
                for entity_id, store in self._entity_stores.items()
            }

    def cleanup_entity(self, entity_id: str):
        """Remove an entity's store from cache (saves memory)"""
        with self._stores_lock:
            if entity_id in self._entity_stores:
                del self._entity_stores[entity_id]
                logger.info(f"Cleaned up entity store: {entity_id}")

    def shutdown(self):
        """Shutdown the manager and thread pool"""
        self._thread_pool.shutdown(wait=True)
        logger.info("EntityRAGManager shutdown complete")


# Global manager instance
_entity_rag_manager: Optional[EntityRAGManager] = None
_manager_lock = threading.Lock()


def get_entity_rag_manager() -> EntityRAGManager:
    """Get or create the global entity RAG manager"""
    global _entity_rag_manager

    if _entity_rag_manager is None:
        with _manager_lock:
            if _entity_rag_manager is None:
                _entity_rag_manager = EntityRAGManager()

    return _entity_rag_manager

def index_document_entity_scoped(entity_id: str, file: File,
                                metadata: Optional[Dict[str, Any]] = None,
                                entity_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Index a document to an entity-scoped vector store (faster, isolated access)

    Args:
        entity_id: Entity identifier
        file: File object to index
        metadata: Optional metadata
        entity_dir: Entity directory path for storing vector store and metadata

    Returns:
        Document info dict with doc_id, chunks_count, services_used, etc.
    """
    try:
        manager = get_entity_rag_manager()
        result = manager.add_document(entity_id, file, metadata, entity_dir)
        return result
    except Exception as e:
        logger.error(f"Failed to index document for entity {entity_id}: {e}")
        return None

@contextmanager
def entity_rag_context():
    """Context manager for entity RAG operations"""
    manager = get_entity_rag_manager()
    try:
        yield manager
    finally:
        pass  # Cleanup if needed
