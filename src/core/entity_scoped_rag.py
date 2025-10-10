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
Entity-Scoped RAG System with Parallel Processing

This module provides isolated RAG indexes per entity for faster access and
parallel processing capabilities across multiple entities.
"""

import os
import threading
from typing import List, Optional, Dict, Any, Set
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import hashlib
from uuid import uuid4
import mimetypes
import time
import requests

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.schema import Document

# from ..infrastructure import chunk_file
from ..config import Config
from ..log_creator import get_file_logger
from ..infrastructure.storage import get_storage_session

logger = get_file_logger()

def _get_mime_type(file_path: str) -> str:
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
            storage_path: Base path for storing entity data
        """
        self.entity_id = entity_id
        self.embeddings = embeddings

        # Entity-specific storage paths
        self.entity_dir = Path(storage_path) / "entities" / entity_id
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
        """Load entity metadata from storage"""
        try:
            with get_storage_session() as db:
                # Load entity-specific documents
                docs = db[Config.DOC_ID_NAME_MAPPING_COLLECTION].find(
                    {"entity_ids": self.entity_id}
                )

                for doc in docs:
                    content_hash = doc.get("content_hash")
                    doc_id = doc.get("doc_id")
                    if content_hash and doc_id:
                        self.document_hashes[content_hash] = doc_id

                logger.debug(f"Loaded {len(self.document_hashes)} document hashes for {self.entity_id}")
        except Exception as e:
            logger.error(f"Failed to load metadata for {self.entity_id}: {e}")

    def add_document(self, file_path: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Add a document to this entity's vector store

        Args:
            file_path: Path to document file
            metadata: Additional metadata

        Returns:
            Document info dict or None on failure
        """
        try:
            # Quick duplicate check without lock (read-only)
            content_hash = self._calculate_file_hash(file_path)
            if content_hash and content_hash in self.document_hashes:
                existing_doc_id = self.document_hashes[content_hash]
                logger.info(f"Document already exists for entity {self.entity_id}: {existing_doc_id}")
                return {
                    "doc_id": existing_doc_id,
                    "entity_id": self.entity_id,
                    "is_duplicate": True
                }

            # Process document outside of lock (CPU-intensive)
            chunks = self._process_document(file_path, metadata)
            if not chunks:
                return None

            doc_id = chunks[0]['metadata']['doc_id']

            # Only lock for vector store updates (critical section)
            with self._lock:
                # Double-check for duplicates after acquiring lock
                if content_hash and content_hash in self.document_hashes:
                    existing_doc_id = self.document_hashes[content_hash]
                    logger.info(f"Document already exists (race condition detected): {existing_doc_id}")
                    return {
                        "doc_id": existing_doc_id,
                        "entity_id": self.entity_id,
                        "is_duplicate": True
                    }

                # Add to vector store
                self._add_chunks_to_vector_store(chunks)

                if content_hash:
                    self.document_hashes[content_hash] = doc_id

                # Save vector store
                self._save_vector_store()

            # Save metadata and chunks to storage outside of lock (uses its own locks)
            self._save_document_metadata(doc_id, file_path, content_hash, metadata, chunks)

            logger.info(f"Added document {doc_id} to entity {self.entity_id}")

            return {
                "doc_id": doc_id,
                "entity_id": self.entity_id,
                "chunks_count": len(chunks),
                "is_duplicate": False
            }

        except Exception as e:
            logger.error(f"Failed to add document to entity {self.entity_id}: {e}")
            return None

    def _calculate_file_hash(self, file_path: str) -> Optional[str]:
        """Calculate SHA-256 hash of file content"""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception as e:
            logger.error(f"Failed to calculate hash: {e}")
            return None

    def _process_document(self, file_path: str, metadata: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process document into chunks"""
        try:
            # Extract source from metadata, fallback to file_path
            source = metadata.get('source', file_path) if metadata else file_path

            # Try to use file processing API first
            try:
                # Detect MIME type
                mime_type = _get_mime_type(file_path)
                with open(file_path, 'rb') as f:
                    file_content = f.read()

                # Prepare files dict
                files = {
                    'file': (Path(file_path).name, file_content, mime_type)
                }

                # Prepare params
                params = {}
                if source:
                    params['source'] = source

                # Make request
                response = requests.post(
                    f"http://localhost:8003/chunk",
                    files=files,
                    params=params
                )
                response.raise_for_status()
                task_info = response.json()
                task_id = task_info['task_id']
                
                poll_interval = 5  # Start with 5 seconds

                while True:
                    status_response = requests.get(
                        f"http://localhost:8003/status/{task_id}"
                    )
                    status_response.raise_for_status()
                    status = status_response.json()
                    
                    if status['status'] == 'completed':
                        break
                    elif status['status'] == 'failed':
                        raise Exception(f"Task failed: {status.get('error')}")
                    
                    # Exponential backoff for polling
                    time.sleep(poll_interval)
                    poll_interval = min(poll_interval * 1.5, 5)  # Max 5 seconds
                    
                # Get the result
                result_response = requests.get(
                    f"http://localhost:8003/result/{task_id}"
                )
                result_response.raise_for_status()
                chunk_result = result_response.json()

                if not chunk_result.get('success', False):
                    raise Exception("File processing API returned unsuccessful")

                api_chunks = chunk_result.get('chunks', [])
            except Exception as api_error:
                logger.debug(f"File processing API not available, using simple chunking: {api_error}")
                # Fallback to simple chunking
                api_chunks = self._simple_chunk_file(file_path)

            # Generate document ID
            doc_id = f"doc_{str(uuid4())}"
            doc_name = os.path.basename(file_path)

            # Add metadata to chunks
            doc_metadata = {
                "entity_ids": [self.entity_id],
                "doc_id": doc_id,
                "doc_name": doc_name,
                "source": file_path,
                "file_type": Path(file_path).suffix.lower(),
                "indexed_at": datetime.now(timezone.utc)
            }

            if metadata:
                doc_metadata.update(metadata)

            # Convert API chunks to our format
            formatted_chunks = []
            for api_chunk in api_chunks:
                # API chunks format: {content, chunk_order_index, source, metadata}
                formatted_chunk = {
                    'chunk': {
                        'content': api_chunk.get('content', ''),
                        'chunk_order_index': api_chunk.get('chunk_order_index', len(formatted_chunks)),
                        'source': api_chunk.get('source', file_path)
                    },
                    'metadata': doc_metadata.copy()
                }

                # Merge any existing metadata from API
                if 'metadata' in api_chunk:
                    formatted_chunk['metadata'].update(api_chunk['metadata'])

                formatted_chunks.append(formatted_chunk)

            return formatted_chunks

        except Exception as e:
            logger.error(f"Error processing document {file_path}: {e}")
            import traceback
            traceback.print_exc()
            return []

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

    def _add_chunks_to_vector_store(self, chunks: List[Dict[str, Any]]):
        """Add chunks to the vector store"""
        documents = [
            Document(
                page_content=chunk['chunk']['content'],
                metadata=chunk
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

    def _save_document_metadata(self, doc_id: str, file_path: str, content_hash: Optional[str],
                                metadata: Optional[Dict[str, Any]], chunks: List[Dict[str, Any]]):
        """Save document metadata and chunks to storage"""
        try:
            with get_storage_session() as db:
                mapping_data = {
                    "doc_path": file_path,
                    "doc_name": os.path.basename(file_path),
                    "file_size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                    "indexed_at": datetime.now(timezone.utc)
                }

                if content_hash:
                    mapping_data["content_hash"] = content_hash

                if metadata:
                    mapping_data["metadata"] = metadata

                update_data = {
                    "$set": mapping_data,
                    "$setOnInsert": {"doc_id": doc_id},
                    "$addToSet": {"entity_ids": self.entity_id}
                }

                db[Config.DOC_ID_NAME_MAPPING_COLLECTION].update_one(
                    {"doc_id": doc_id},
                    update_data,
                    upsert=True
                )

                # Save chunks to entity-scoped collection
                for chunk in chunks:
                    chunk_id = f"chunk_{chunk['metadata']['doc_id']}_{chunk['chunk']['chunk_order_index']}"
                    # Prepare chunk document without _id for $set
                    chunk_doc = {key: value for key, value in chunk.items() if key != "_id"}
                    chunk_doc['entity_id'] = self.entity_id

                    db[self.chunks_collection].update_one(
                        {"_id": chunk_id},
                        {"$set": chunk_doc},
                        upsert=True
                    )

                logger.debug(f"Saved {len(chunks)} chunks for document {doc_id} to collection {self.chunks_collection}")

        except Exception as e:
            logger.error(f"Failed to save document metadata: {e}")

    def search(self, query: str, k: int = 5, doc_ids: Optional[List[str]] = None) -> List[Document]:
        """
        Search within this entity's vector store

        Args:
            query: Search query
            k: Number of results
            doc_ids: Optional filter by document IDs

        Returns:
            List of matching documents
        """
        with self._lock:
            if not self.vector_store:
                logger.warning(f"No vector store for entity {self.entity_id}")
                return []

            try:
                if doc_ids:
                    # Filter by document IDs
                    return self._search_with_doc_filter(query, k, doc_ids)
                else:
                    # Search all documents in this entity
                    return self.vector_store.similarity_search(query, k=k)

            except Exception as e:
                logger.error(f"Search failed for entity {self.entity_id}: {e}")
                return []

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
        """Delete a document from this entity's vector store"""
        with self._lock:
            try:
                # Remove from storage
                with get_storage_session() as db:
                    # Remove entity_id from document
                    result = db[Config.DOC_ID_NAME_MAPPING_COLLECTION].update_one(
                        {"doc_id": doc_id},
                        {"$pull": {"entity_ids": self.entity_id}}
                    )

                    # Delete chunks from entity-scoped collection
                    db[self.chunks_collection].delete_many({"metadata.doc_id": doc_id})

                    # Check if document has no more entities
                    doc = db[Config.DOC_ID_NAME_MAPPING_COLLECTION].find_one({"doc_id": doc_id})
                    if doc and (not doc.get("entity_ids") or len(doc.get("entity_ids", [])) == 0):
                        # Delete document completely
                        db[Config.DOC_ID_NAME_MAPPING_COLLECTION].delete_one({"doc_id": doc_id})

                # Remove from local metadata
                if doc_id in self.doc_to_chunks:
                    del self.doc_to_chunks[doc_id]

                # Rebuild vector store without this document
                self._rebuild_vector_store()

                logger.info(f"Deleted document {doc_id} from entity {self.entity_id}")
                return True

            except Exception as e:
                logger.error(f"Failed to delete document {doc_id}: {e}")
                return False

    def _rebuild_vector_store(self):
        """Rebuild the vector store from storage"""
        try:
            with get_storage_session() as db:
                # Get all documents for this entity
                docs = list(db[Config.DOC_ID_NAME_MAPPING_COLLECTION].find(
                    {"entity_ids": self.entity_id}
                ))

                all_chunks = []
                for doc in docs:
                    doc_path = doc.get("doc_path")
                    if doc_path and os.path.exists(doc_path):
                        chunks = self._process_document(doc_path, doc.get("metadata"))
                        if chunks:
                            all_chunks.extend(chunks)

                # Rebuild vector store
                if all_chunks:
                    documents = [
                        Document(
                            page_content=chunk['chunk']['content'],
                            metadata=chunk
                        )
                        for chunk in all_chunks
                    ]
                    self.vector_store = FAISS.from_documents(documents, self.embeddings)
                    self._update_chunk_indices(all_chunks, 0)
                    self._save_vector_store()
                else:
                    self.vector_store = None

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
            with get_storage_session() as db:
                chunk_id = f"chunk_{doc_id}_{chunk_order_index}"
                chunk = db[self.chunks_collection].find_one({"_id": chunk_id})

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
            with get_storage_session() as db:
                chunks = list(db[self.chunks_collection].find(
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
            with get_storage_session() as db:
                docs = list(db[Config.DOC_ID_NAME_MAPPING_COLLECTION].find(
                    {"entity_ids": self.entity_id}
                ))

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

    def get_entity_store(self, entity_id: str) -> EntityVectorStore:
        """
        Get or create an entity-scoped vector store

        Args:
            entity_id: Entity identifier

        Returns:
            EntityVectorStore instance
        """
        with self._stores_lock:
            if entity_id not in self._entity_stores:
                self._entity_stores[entity_id] = EntityVectorStore(
                    entity_id=entity_id,
                    embeddings=self.embeddings,
                    storage_path=self.storage_path
                )
            return self._entity_stores[entity_id]

    def add_document(self, entity_id: str, file_path: str,
                    metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Add a document to an entity's vector store

        Args:
            entity_id: Entity identifier
            file_path: Path to document
            metadata: Additional metadata

        Returns:
            Document info dict
        """
        store = self.get_entity_store(entity_id)
        return store.add_document(file_path, metadata)

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
              doc_ids: Optional[List[str]] = None) -> List[Document]:
        """
        Search within a specific entity's documents

        Args:
            entity_id: Entity identifier
            query: Search query
            k: Number of results
            doc_ids: Optional document ID filter

        Returns:
            List of matching documents
        """
        store = self.get_entity_store(entity_id)
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

    def delete_document(self, entity_id: str, doc_id: str) -> bool:
        """Delete a document from an entity's store"""
        store = self.get_entity_store(entity_id)
        return store.delete_document(doc_id)

    def get_entity_stats(self, entity_id: str) -> Dict[str, Any]:
        """Get statistics for an entity"""
        store = self.get_entity_store(entity_id)
        return store.get_stats()

    def get_chunk_by_id(self, entity_id: str, doc_id: str, chunk_order_index: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific chunk by ID for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            chunk_order_index: Chunk order index

        Returns:
            Chunk data or None
        """
        store = self.get_entity_store(entity_id)
        return store.get_chunk_by_id(doc_id, chunk_order_index)

    def get_previous_chunk(self, entity_id: str, doc_id: str, current_chunk_index: int) -> Optional[Dict[str, Any]]:
        """
        Get the previous chunk for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            current_chunk_index: Current chunk order index

        Returns:
            Previous chunk or None
        """
        store = self.get_entity_store(entity_id)
        return store.get_previous_chunk(doc_id, current_chunk_index)

    def get_next_chunk(self, entity_id: str, doc_id: str, current_chunk_index: int) -> Optional[Dict[str, Any]]:
        """
        Get the next chunk for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            current_chunk_index: Current chunk order index

        Returns:
            Next chunk or None
        """
        store = self.get_entity_store(entity_id)
        return store.get_next_chunk(doc_id, current_chunk_index)

    def get_chunk_context(self, entity_id: str, doc_id: str, chunk_order_index: int,
                         context_size: int = 1) -> Dict[str, Any]:
        """
        Get chunk with context for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            chunk_order_index: Target chunk order index
            context_size: Number of chunks before/after

        Returns:
            Dict with current, before, and after chunks
        """
        store = self.get_entity_store(entity_id)
        return store.get_chunk_context(doc_id, chunk_order_index, context_size)

    def get_document_chunks_in_order(self, entity_id: str, doc_id: str) -> List[Dict[str, Any]]:
        """
        Get all chunks for a document in order for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier

        Returns:
            List of chunks in order
        """
        store = self.get_entity_store(entity_id)
        return store.get_document_chunks_in_order(doc_id)

    def get_chunk_neighbors(self, entity_id: str, doc_id: str, chunk_order_index: int,
                           window_size: int = 2) -> List[Dict[str, Any]]:
        """
        Get neighboring chunks for an entity

        Args:
            entity_id: Entity identifier
            doc_id: Document identifier
            chunk_order_index: Target chunk order index
            window_size: Window size on each side

        Returns:
            List of neighboring chunks
        """
        store = self.get_entity_store(entity_id)
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


@contextmanager
def entity_rag_context():
    """Context manager for entity RAG operations"""
    manager = get_entity_rag_manager()
    try:
        yield manager
    finally:
        pass  # Cleanup if needed
