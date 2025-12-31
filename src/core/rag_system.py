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

# src/rag_system.py
import os
from typing import List, Optional, Dict, Any, Tuple
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain.schema import Document
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
import threading
import time
import hashlib
import numpy as np

from ..infrastructure.clients import chunk_file
from ..config import Config
from ..log_creator import get_file_logger
from ..infrastructure.storage import get_storage_session
from .entity_scoped_rag import get_entity_rag_manager, entity_rag_context
from ..infrastructure.metrics import Service

logger = get_file_logger()

class RAGSystemPool:
    """
    Singleton RAG system pool with thread safety
    """
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self._rag_system: Optional['RAGSystem'] = None
        self._operation_lock = threading.RLock()  # For thread-safe operations
        self._last_used = time.time()
        self._initialization_failed = False
        
        # Initialize RAG system in a separate thread to avoid blocking
        self._init_thread = threading.Thread(target=self._initialize_rag_system_async, daemon=True)
        self._init_thread.start()
        
    def _initialize_rag_system_async(self):
        """Initialize RAG system asynchronously"""
        try:
            logger.info("Initializing RAG System in singleton pool (async)")
            self._rag_system = RAGSystem()
            logger.info("RAG system initialized successfully in pool")
        except ImportError as e:
            logger.info(f"RAG system not available due to missing dependencies: {e}")
            self._rag_system = None
            self._initialization_failed = True
        except Exception as e:
            logger.error(f"Failed to initialize RAG system: {e}")
            self._rag_system = None
            self._initialization_failed = True
    
    def get_rag_system(self) -> Optional['RAGSystem']:
        """Get RAG system instance, with lazy initialization if needed"""
        self._last_used = time.time()
        
        # If initialization is still in progress, wait for it
        if hasattr(self, '_init_thread') and self._init_thread.is_alive():
            logger.info("Waiting for RAG system initialization to complete...")
            self._init_thread.join(timeout=30)  # Wait up to 30 seconds
            
        # If initialization failed and we haven't tried again recently, retry
        if self._rag_system is None and not self._initialization_failed:
            try:
                logger.info("Attempting to initialize RAG system on demand")
                self._rag_system = RAGSystem()
                logger.info("RAG system initialized successfully on demand")
            except Exception as e:
                logger.error(f"Failed to initialize RAG system on demand: {e}")
                self._initialization_failed = True
                
        return self._rag_system
    
    def is_available(self) -> bool:
        """Check if RAG system is available"""
        return self._rag_system is not None
    
    @contextmanager
    def get_rag_context(self):
        """Context manager for RAG operations with thread safety"""
        if not self._rag_system:
            raise RuntimeError("RAG system not available")
        
        with self._operation_lock:
            try:
                yield self._rag_system
            except Exception as e:
                logger.error(f"RAG operation error: {e}")
                raise
            finally:
                self._last_used = time.time()

class RAGSystem:
    vector_store: Optional[FAISS] = None

    def __init__(self):
        logger.info("Initializing RAG System")

        # Initialize all attributes first to prevent AttributeError
        # Initialize loaders dictionary first (prevents the AttributeError)
        logger.debug("Document loaders initialized")

        # Initialize embeddings
        self.embeddings = HuggingFaceEmbeddings(
            model_name=Config.EMBEDDINGS_MODEL
        )
        logger.info(f"Loaded embeddings model: {Config.EMBEDDINGS_MODEL}")

        # Set vector store path
        self.vector_store_path = os.path.join(Config.DATA_DIR, "vector_store")

        self.document_hashes: Dict[str, str] = {}
        self.doc_id_to_hash: Dict[str, str] = {}

        # Index mappings for efficient filtering
        self.entity_to_chunks: Dict[str, set] = {}  # entity_id -> set of chunk indices
        self.doc_to_chunks: Dict[str, set] = {}     # doc_id -> set of chunk indices
        self.chunk_metadata: Dict[int, Dict[str, Any]] = {}  # chunk index -> metadata
        
        try:            
            # Load existing vector store (this should be done last)
            self.load_vector_store(self.vector_store_path)
            
            self._load_existing_document_hashes()
            
            logger.info("RAG System initialization completed successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize RAG System: {e}")
            # Clean up any partially initialized state
            self._cleanup_partial_init()
            raise
        
    def cleanup_duplicate_documents(self) -> Dict[str, int]:
        """Find and clean up duplicate documents"""
        logger.info("Starting duplicate document cleanup")
        
        stats: Dict[str, int|bool] = {
            'duplicates_found': 0,
            'duplicates_removed': 0,
            'vector_store_rebuilt': False
        }
        
        try:
            with get_storage_session() as db:
                # Find documents with same content hash
                pipeline: List[Dict[str, Dict[str, Any]]] = [
                    {"$match": {"content_hash": {"$exists": True}}},
                    {"$group": {
                        "_id": "$content_hash",
                        "docs": {"$push": {"doc_id": "$doc_id", "entity_ids": "$entity_ids"}},
                        "count": {"$sum": 1}
                    }},
                    {"$match": {"count": {"$gt": 1}}}
                ]

                duplicates = list(db[Config.DOC_ID_NAME_MAPPING_COLLECTION].aggregate(pipeline))
                stats['duplicates_found'] = len(duplicates)
                
                for dup_group in duplicates:
                    docs = dup_group['docs']
                    # Keep the first document, merge others into it
                    primary_doc = docs[0]
                    
                    all_entity_ids = set(primary_doc.get('entity_ids', []))
                    
                    # Collect all entity IDs from duplicates
                    for doc in docs[1:]:
                        all_entity_ids.update(doc.get('entity_ids', []))
                        
                        # Delete duplicate document
                        self.delete_document(doc['doc_id'])
                        stats['duplicates_removed'] += 1
                    
                    # Update primary document with all entity IDs
                    db[Config.DOC_ID_NAME_MAPPING_COLLECTION].update_one(
                        {"doc_id": primary_doc['doc_id']},
                        {"$set": {"entity_ids": list(all_entity_ids)}}
                    )
                
                if stats['duplicates_removed'] > 0:
                    # Rebuild vector store
                    self._rebuild_vector_store_without_document(None)  # Full rebuild
                    stats['vector_store_rebuilt'] = True
                    
            logger.info(f"Duplicate cleanup completed: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"Failed to cleanup duplicates: {e}")
            return stats
        
    def _calculate_file_hash(self, file_path: str) -> Optional[str]:
        """Calculate SHA-256 hash of fie content"""
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
                return hashlib.sha256(content).hexdigest()
        except Exception as e:
            logger.error(f"Failed to calculate hash for {file_path}: {e}")
            return None
        
    def _load_existing_document_hashes(self):
        """Load existing document hashed from storage"""
        try:
            with get_storage_session() as db:
                existing_docs = db[Config.DOC_ID_NAME_MAPPING_COLLECTION].find(
                    {"content_hash": {"$exists": True}},
                    {"doc_id": 1, "content_hash": 1}
                )

                for doc in existing_docs:
                    doc_id = doc["doc_id"]
                    content_hash = doc["content_hash"]
                    self.document_hashes[content_hash] = doc_id
                    self.doc_id_to_hash[doc_id] = content_hash

                logger.info(f"Loaded {len(self.document_hashes)} existing document hashes")

        except Exception as e:
            logger.error(f"Failed to load existing document hashes: {e}")
            
    def _check_duplicate_by_hash(self, file_path: str) -> Optional[str]:
        """Check if document is a duplicate based on content hash"""
        content_hash = self._calculate_file_hash(file_path)
        if not content_hash:
            return None
        
        return self.document_hashes.get(content_hash)
        
    def _cleanup_partial_init(self):
        """Clean up partially initialized state"""
        logger.debug("Cleaning up partial initialization")
        self.embeddings = None
        
    def index_single_document(self, file_path: str, entity_id: Optional[str], metadata: Optional[Dict[str, Any]]=None) -> Optional[Dict[str, Any]]:
        """
        Index a single document and return document information
        
        Args:
            file_path: Path to the document file
            entity_id: entity this document belongs to
            metadat: Additional metadata to attach to the document
        
        Returns:
            Dictionary with doc_id and other document info, or None if failed
        """
        logger.info(f"Indexing single document: {file_path} for entity: {entity_id}")
        
        if not os.path.exists(file_path):
            logger.error(f"File does not exist: {file_path}")
            return None
        
        try:
            # Check for duplicate by content hash
            existing_doc_id = self._check_duplicate_by_hash(file_path)
            
            if existing_doc_id:
                logger.info(f"Document with same content already exists: {existing_doc_id}")
                
                if entity_id:
                    # Update entity association for existing documnet
                    self._add_entity_to_existing_document(existing_doc_id, entity_id)
                
                # Get existing document info
                with get_storage_session() as db:
                    existing_doc = db[Config.DOC_ID_NAME_MAPPING_COLLECTION].find_one(
                        {"doc_id": existing_doc_id}
                    )

                if existing_doc:
                    return {
                        "doc_id": existing_doc_id,
                        'doc_name': existing_doc.get('doc_name', ''),
                        'file_path': existing_doc.get('doc_path', ''),
                        'entity_id': entity_id,
                        'is_duplicate': True,
                        'chunks_count': 0
                    }
                    
            # Proceed with normal indexing for new documents
            content_hash = self._calculate_file_hash(file_path)
                         
            # Load and process the document
            split_docs =  self._load_document(file_path, entity_id, metadata)
            
            if not split_docs:
                logger.warning(f"No documents loaded from: {file_path}")
                return None
            
            doc_id: str = str(split_docs[0]['metadata']['doc_id'])
            doc_name: str = str(split_docs[0]['metadata']['doc_name'])
            
            with get_storage_session() as db:
                for chunk in split_docs:
                    chunk_id = f"chunk_{chunk['metadata']['doc_id']}_{chunk['chunk']['chunk_order_index']}"
                    update_doc = {key: value for key, value in chunk.items() if key != "_id"}

                    db[Config.CHUNKS_COLLECTION].update_one(
                        {"_id": chunk_id},
                        {"$set": update_doc},
                        upsert=True
                    )
            
            # Add to vector store
            self._create_or_update_vector_store(split_docs)
            
            # Update document mappings
            self._update_docid_name_mapping(doc_id, file_path, entity_id, content_hash, metadata)
            
            if content_hash:
                self.document_hashes[content_hash] = doc_id
                self.doc_id_to_hash[doc_id] = content_hash
                
            logger.info(f"Successfully indexed document: {doc_name} with doc_id: {doc_id}")
            
            return {
                'doc_id': doc_id,
                'doc_name': doc_name, 
                'file_path': file_path,
                'entity_id': entity_id,
                'chunks_count': len(split_docs),
                'is_duplicate': False
            }
            
        except Exception as e:
            logger.error(f"Failed to index document {file_path}: {e}", exc_info=True)
            return None
        
        finally:
            self.save_vector_store()
        
    def _add_entity_to_existing_document(self, doc_id: str, entity_id: str):
        """Add entity association to an existing document"""
        try:
            with get_storage_session() as db:
                # Update document mapping
                db[Config.DOC_ID_NAME_MAPPING_COLLECTION].update_one(
                    {'doc_id': doc_id},
                    {"$addToSet": {"entity_ids": entity_id}}
                )
                logger.debug(f"Added entity {entity_id} to existing document {doc_id}")

        except Exception as e:
            logger.error(f"Failed to add entity to existing document: {e}")
    
    def update_entity_with_document(self, entity_id: str, doc_id: str, doc_name: str, description: str = "Related document"):
        """Update entity document with reference to indexed document"""
        logger.debug(f"Updating entity {entity_id} with document {doc_id}")
        
        try:
            doc_reference: Dict[str, Any] = {
                "doc_id": doc_id,
                "filename": doc_name,
                "description": description,
                "indexed_at": datetime.now(timezone.utc)
            }
            entity_type = entity_id.split("_")[0]
            with get_storage_session() as db:
                result = db[entity_type].update_one(
                    {"_id": entity_id},
                    {
                        "$set": {
                            f"related_doc_ids.{doc_id}": doc_reference
                        }
                    }
                )
                logger.debug(f"Entity update result - matched: {result.matched_count}, modified: {result.modified_count}")
                
                if result.modified_count:
                    entity_mapping_id = f"{entity_id}_{doc_id}"
                    
                    # Check if the entity mapping already exists
                    existing_mapping = db["entity_mappings"].find_one({"_id": entity_mapping_id})
                    
                    if existing_mapping:
                        # Document exists, just add to relations
                        update_data = {
                            "$addToSet": {
                                "relations": ["document", "more details"]
                            }
                        }
                    else:
                        # Document doesn't exist, create it with initial relations
                        update_data: Dict[str, Dict[str, Any]] = {
                            "$setOnInsert": {
                                "_id": entity_mapping_id,
                                "source_id": entity_id,
                                "target_id": doc_id,
                                "relations": [["document", "more details"]]  
                            }
                        }
                    
                    result = db["entity_mappings"].update_one(
                        {"_id": entity_mapping_id},
                        update_data,
                        upsert=True
                    )
                    
                    logger.debug(f"Entity mapping update result - matched: {result.matched_count}, modified: {result.modified_count}")

        except Exception as e:
            logger.error(f"Failed to update entity with document reference: {e}")
        
    def load_and_process_documents(self, documents_dir: str):
        """Load and process all entity documents (legacy method for compatibility)"""
        logger.info(f"Starting document processing from directory: {documents_dir}")
        
        docs_dir = os.path.join(documents_dir, "docs")
        if not os.path.exists(docs_dir):
            logger.error(f"Documents directory does not exist: {documents_dir}")
            return
        
        try:
            for filename in os.listdir(docs_dir):
                file_path = os.path.join(docs_dir, filename)

                if not os.path.isfile(file_path):
                    continue
                
                self.index_single_document(file_path, None, None)
                
        except Exception as e:
            logger.error(f"Failed to load documents from {documents_dir}: {e}")
            return
    
    def delete_document(self, doc_id: str) -> bool:
        """
        Delete a document from the system including vector store
        
        Args:
            doc_id: Document ID to delete
            
        Returns:
            bool: True if document was deleted successfully
        """
        logger.info(f"Deleting document: {doc_id}")
        
        try:
            # Clean up hash tracking
            if doc_id in self.doc_id_to_hash:
                content_hash = self.doc_id_to_hash[doc_id]
                del self.doc_id_to_hash[doc_id]
                if content_hash in self.document_hashes:
                    del self.document_hashes[content_hash]
            
            # Get document info from storage
            with get_storage_session() as db:

                relation_docs = list(db[Config.ENTITY_MAPPINGS_COLLECTION].find({
                    "$or": [
                        {"target_id": doc_id},
                        {"source_id": doc_id}
                    ]
                }))
                
                if relation_docs:
                    raise RuntimeError("Cannot delete a document when attached to entities, detach the document before")
                
                
                doc_info = db[Config.DOC_ID_NAME_MAPPING_COLLECTION].find_one(
                    {"doc_id": doc_id}
                )
                
                if not doc_info:
                    logger.warning(f"Document not found in database: {doc_id}")
                    return False
                
                # Remove from document mappings
                db[Config.DOC_ID_NAME_MAPPING_COLLECTION].delete_one(
                    {"doc_id": doc_id}
                )
                logger.debug(f"Removed document from mappings: {doc_id}")
                
                # Remove from entities that reference this document
                # TODO: Find all the entities connected to this document and delete them we can find that in entity_mappings collection
                # relation_docs = list(db[Config.ENTITY_MAPPINGS_COLLECTION].find({
                #     "$or": [
                #         {"target_id": doc_id},
                #         {"source_id": doc_id}
                #     ]
                # }))
                
                # if relation_docs
                # for relation_doc in relation_docs:
                #     if relation_doc["target_id"] == doc_id and relation_doc["source_id"] == doc_id:
                #         continue
                #     elif relation_doc["source_id"] == doc_id:
                #         entity_type = relation_doc["target_id"].split("_")[0]
                #     else:
                #         entity_type = relation_doc["source_id"].split("_")[0]
                #     entity_update_result = db[entity_type].update_many(
                #         {f"related_doc_ids.{doc_id}": {"$exists": True}},
                #         {
                #             "$unset": {f"related_doc_ids.{doc_id}": ""}
                #         }
                #     )
                #     logger.debug(f"Updated {entity_update_result.modified_count} entities to remove document reference in {entity_type}")
                
                # # Remove entity mappings
                # entity_delete_result = db["entity_mappings"].delete_many(
                #     {"target": doc_id}
                # )
                # logger.debug(f"Removed {entity_delete_result.deleted_count} entity mappings")
            
            # Remove from vector store (this requires rebuilding)
            if self.vector_store:
                self._rebuild_vector_store_without_document(doc_id)
            
            logger.info(f"Successfully deleted document: {doc_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete document {doc_id}: {e}")
            return False
        
    def _rebuild_vector_store_without_document(self, doc_id_to_remove: Optional[str]):
        """
        Rebuild vector store excluding a specific document
        This is expensive but necessary for FAISS
        """
        logger.info(f"Rebuilding vector store without document: {doc_id_to_remove}")
        
        try:
            # Get all remaining documents from storage
            with get_storage_session() as db:
                remaining_docs = list(db[Config.DOC_ID_NAME_MAPPING_COLLECTION].find(
                    {"doc_id": {"$ne": doc_id_to_remove}}
                ))
            
            if not remaining_docs:
                logger.info("No documents remaining, clearing vector store")
                return
            
            # Reload and reprocess all remaining documents
            all_documents: List[Dict[str, Dict[str, Any]]] = []
            
            for doc_info in remaining_docs:
                doc_path = doc_info.get("doc_path")
                entity_ids = doc_info.get("entity_ids", [])
                
                if doc_path and os.path.exists(doc_path):
                    try:
                        # Use first entity_id or None
                        entity_id = entity_ids[0] if entity_ids else None
                        split_docs = self._load_document(doc_path, entity_id)
                        
                        if split_docs:
                            all_documents.extend(split_docs)
                            logger.debug(f"Reloaded document: {doc_path}")
                    except Exception as e:
                        logger.error(f"Failed to reload document {doc_path}: {e}")
            
            # Rebuild vector store
            if all_documents:
                if self.embeddings:
                    self.vector_store = FAISS.from_documents(
                                            [
                                                Document(
                                                    page_content=doc['chunk']['content'],
                                                    metadata=doc
                                                )
                                                for doc in all_documents
                                            ],
                                            self.embeddings
                                        )

                # Rebuild indices for efficient filtering
                self._update_chunk_indices(all_documents, 0)

                logger.info(f"Rebuilt vector store with {len(all_documents)} document chunks")

                # Save the rebuilt vector store
                self.save_vector_store()
            else:
                logger.warning("No valid documents found for vector store rebuild")
                # Clear indices if no documents
                self.entity_to_chunks.clear()
                self.doc_to_chunks.clear()
                self.chunk_metadata.clear()
                
        except Exception as e:
            logger.error(f"Failed to rebuild vector store: {e}")
            raise
    
    def _load_document(self, file_path: str, entity_id: Optional[str], metadata: Optional[Dict[str, Any]]=None) -> List[Dict[str, Dict[str, Any]]]:
        """Load and process a single document file"""
        logger.debug(f"Loading document from: {file_path}")
        
        try:
            # Get file extension
            file_ext = Path(file_path).suffix.lower()

            # Use file processing API to chunk the file
            chunk_result = chunk_file(file_path, source=file_path)

            if not chunk_result.get('success', False):
                logger.error(f"Failed to chunk file: {file_path}")
                return []

            chunks = chunk_result.get('chunks', [])

            logger.debug(f"Loaded {len(chunks)} raw chunks from: {file_path}")
            
            if not chunks:
                logger.warning(f"No content loaded from: {file_path}")
                return []
            
            # Generate unique document ID
            doc_id = f"doc_{str(uuid4())}"
            doc_name = os.path.basename(file_path)
            logger.debug(f"Assigned doc_id: {doc_id}, doc_name: {doc_name}")

            # Prepare metadata
            doc_metadata: Dict[str, Any] = {
                "entity_ids": [entity_id] if entity_id else [],
                "doc_id": doc_id,
                "doc_name": doc_name,
                "source": file_path,
                "file_type": file_ext,
                "indexed_at": datetime.now(timezone.utc)
            }
            
            if metadata:
                doc_metadata.update(metadata)
                
            # Add metadata to all document chunks
            for doc in chunks:
                if 'metadata' in doc:
                    doc['metadata'].update(doc_metadata)
                else:
                    doc['metadata'] = doc_metadata.copy()
                
            return chunks
        
        except Exception as e:
            logger.error(f"Error loading document {file_path}: {e}")
            return []
        
    def _update_docid_name_mapping(self, doc_id: str, file_path: str, entity_id: Optional[str],
                                 content_hash: Optional[str], metadata: Optional[Dict[str, Any]] = None):
        """Update document ID to name mapping with hash"""
        logger.debug(f"Updating doc-id mapping: doc_id={doc_id}, content_hash={content_hash}")
        
        try:
            mapping_data: Dict[str, Any] = {
                "doc_path": file_path,
                "doc_name": os.path.basename(file_path),
                "file_size": os.path.getsize(file_path) if os.path.exists(file_path) else 0,
                "indexed_at": datetime.now(timezone.utc)
            }
            
            if content_hash:
                mapping_data["content_hash"] = content_hash
            
            if metadata:
                mapping_data["metadata"] = metadata
            
            update_data: Dict[str, Dict[str, Any]] = {
                "$set": mapping_data,
                "$setOnInsert": {"doc_id": doc_id}
            }
            if entity_id:
                update_data["$addToSet"] = {"entity_ids": entity_id}

            with get_storage_session() as db:
                result = db[Config.DOC_ID_NAME_MAPPING_COLLECTION].update_one(
                    {"doc_id": doc_id},
                    update_data,
                    upsert=True
                )
                logger.debug(f"Document mapping update result - matched: {result.matched_count}, modified: {result.modified_count}")
        except Exception as e:
            logger.error(f"Failed to update doc-id mapping: {e}")
    
    def _create_or_update_vector_store(self, documents: List[Dict[str, Dict[str, Any]]]):
        """Create or update the vector store with new documents"""
        logger.debug(f"Creating/updating vector store with {len(documents)} documents")

        try:
            if self.vector_store:
                start_idx = len(self.chunk_metadata)
                self.vector_store.add_documents([
                    Document(
                        page_content=doc['chunk']['content'],
                        metadata=doc
                    )
                    for doc in documents
                ])
                # Update indices for new documents
                self._update_chunk_indices(documents, start_idx)
                logger.info(f"Added {len(documents)} documents to existing vector store")
            else:
                if self.embeddings:
                    self.vector_store = FAISS.from_documents(
                                            [
                                                Document(
                                                    page_content=doc['chunk']['content'],
                                                    metadata=doc
                                                )
                                                for doc in documents
                                            ],
                                            self.embeddings
                                        )
                # Build indices for all documents
                self._update_chunk_indices(documents, 0)
                logger.info(f"Created new vector store with {len(documents)} documents")
        except Exception as e:
            logger.error(f"Failed to create/update vector store: {e}")
            raise

    def _update_chunk_indices(self, documents: List[Dict[str, Dict[str, Any]]], start_idx: int):
        """Update chunk indices for efficient filtering"""
        logger.debug(f"Updating chunk indices starting from index {start_idx}")

        for i, doc in enumerate(documents):
            chunk_idx = start_idx + i
            metadata = doc['metadata']

            # Store chunk metadata
            self.chunk_metadata[chunk_idx] = metadata

            # Update doc_id mapping
            doc_id = metadata.get('doc_id')
            if doc_id:
                if doc_id not in self.doc_to_chunks:
                    self.doc_to_chunks[doc_id] = set()
                self.doc_to_chunks[doc_id].add(chunk_idx)

            # Update entity_id mappings
            entity_ids = metadata.get('entity_ids', [])
            for entity_id in entity_ids:
                if entity_id:
                    if entity_id not in self.entity_to_chunks:
                        self.entity_to_chunks[entity_id] = set()
                    self.entity_to_chunks[entity_id].add(chunk_idx)

        logger.debug(f"Updated indices for {len(documents)} chunks")

    def _rebuild_indices_from_vector_store(self):
        """Rebuild indices from existing vector store"""
        if not self.vector_store:
            return

        logger.info("Rebuilding chunk indices from vector store")

        # Clear existing indices
        self.entity_to_chunks.clear()
        self.doc_to_chunks.clear()
        self.chunk_metadata.clear()

        # Get all documents from vector store
        try:
            # Access the docstore to get all documents
            docstore = self.vector_store.docstore
            index_to_docstore_id = self.vector_store.index_to_docstore_id

            for i in range(len(index_to_docstore_id)):
                docstore_id = index_to_docstore_id[i]
                doc = docstore.search(docstore_id)

                if doc and hasattr(doc, 'metadata'):
                    doc_metadata = doc.metadata
                    self.chunk_metadata[i] = doc_metadata

                    # The metadata might be nested - check both structures
                    # Case 1: Direct metadata structure
                    if 'entity_ids' in doc_metadata:
                        working_metadata = doc_metadata
                    # Case 2: Nested metadata structure (metadata.metadata)
                    elif 'metadata' in doc_metadata and isinstance(doc_metadata['metadata'], dict):
                        working_metadata = doc_metadata['metadata']
                    else:
                        working_metadata = doc_metadata

                    # Update doc_id mapping
                    doc_id = working_metadata.get('doc_id')
                    if doc_id:
                        if doc_id not in self.doc_to_chunks:
                            self.doc_to_chunks[doc_id] = set()
                        self.doc_to_chunks[doc_id].add(i)

                    # Update entity_id mappings
                    entity_ids = working_metadata.get('entity_ids', [])
                    if entity_ids and isinstance(entity_ids, list):
                        for entity_id in entity_ids:
                            if entity_id:
                                if entity_id not in self.entity_to_chunks:
                                    self.entity_to_chunks[entity_id] = set()
                                self.entity_to_chunks[entity_id].add(i)
                                logger.debug(f"Added chunk {i} to entity {entity_id}")

            logger.info(f"Rebuilt indices for {len(self.chunk_metadata)} chunks")
            logger.debug(f"Entity mappings built: {len(self.entity_to_chunks)} unique entities")
            if self.entity_to_chunks:
                logger.debug(f"Sample entities: {list(self.entity_to_chunks.keys())[:5]}")
            else:
                logger.warning("No entity mappings were built during index rebuilding")

        except Exception as e:
            logger.error(f"Failed to rebuild indices: {e}")

    def debug_entity_mappings(self, target_entity_id: Optional[str] = None):
        """Debug function to inspect entity mappings"""
        logger.info(f"=== ENTITY MAPPINGS DEBUG ===")
        logger.info(f"Total entities in mapping: {len(self.entity_to_chunks)}")
        logger.info(f"Total chunks indexed: {len(self.chunk_metadata)}")

        if target_entity_id:
            if target_entity_id in self.entity_to_chunks:
                chunks = self.entity_to_chunks[target_entity_id]
                logger.info(f"Target entity '{target_entity_id}' found with {len(chunks)} chunks")
                logger.info(f"Chunk indices: {sorted(list(chunks))[:10]}...")
            else:
                logger.warning(f"Target entity '{target_entity_id}' NOT FOUND in mapping")

                # Check if we can find similar entity IDs
                similar_entities = [eid for eid in self.entity_to_chunks.keys()
                                  if target_entity_id.lower() in eid.lower() or eid.lower() in target_entity_id.lower()]
                if similar_entities:
                    logger.info(f"Similar entities found: {similar_entities[:5]}")

        # Show sample of all entities
        logger.info(f"Sample entities: {list(self.entity_to_chunks.keys())[:10]}")

        # Check a sample chunk's metadata
        if self.chunk_metadata:
            sample_chunk_idx = list(self.chunk_metadata.keys())[0]
            sample_metadata = self.chunk_metadata[sample_chunk_idx]
            logger.info(f"Sample chunk metadata: {sample_metadata}")

    def _rebuild_indices_fallback(self):
        """Fallback method to rebuild indices by querying database"""
        logger.info("Using fallback method to rebuild indices")

        try:
            with get_storage_session() as db:
                # Get all chunks from storage
                chunks = list(db[Config.CHUNKS_COLLECTION].find({}))

                for i, chunk in enumerate(chunks):
                    metadata = chunk.get('metadata', {})
                    self.chunk_metadata[i] = metadata

                    # Update doc_id mapping
                    doc_id = metadata.get('doc_id')
                    if doc_id:
                        if doc_id not in self.doc_to_chunks:
                            self.doc_to_chunks[doc_id] = set()
                        self.doc_to_chunks[doc_id].add(i)

                    # Update entity_id mappings
                    entity_ids = metadata.get('entity_ids', [])
                    for entity_id in entity_ids:
                        if entity_id:
                            if entity_id not in self.entity_to_chunks:
                                self.entity_to_chunks[entity_id] = set()
                            self.entity_to_chunks[entity_id].add(i)

            logger.info(f"Rebuilt indices using fallback for {len(chunks)} chunks")

        except Exception as e:
            logger.error(f"Fallback index rebuild failed: {e}")
    
    def search_all_documents(self, query: str, k: int = 5) -> List[Document]:
        logger.debug(f"Performing global search with query: '{query}', k={k}")
        
        if self.vector_store is None:
            logger.warning("No vector store loaded - cannot perform search")
            return []

        try:
            results = self.vector_store.similarity_search(query, k=k)
            logger.info(f"Global search returned {len(results)} results for query: '{query}'")
            return results
        except Exception as e:
            logger.error(f"Error during global search: {e}")
            return []
    
    def search_documents(
        self,
        query: str,
        k: int = 5,
        doc_ids: Optional[List[str]] = None,
        entity_ids: Optional[List[str]] = None
    ) -> List[Document]:
        logger.debug(f"Performing filtered search with query: '{query}', k={k}, doc_ids={doc_ids}, entity_ids={entity_ids}")

        if self.vector_store is None:
            logger.warning("No vector store loaded - cannot perform search")
            return []

        try:
            # Use optimized pre-filtering if filters are specified
            if doc_ids or entity_ids:
                return self._search_with_prefiltering(query, k, doc_ids, entity_ids)
            else:
                # Fallback to global search
                results = self.vector_store.similarity_search(query, k=k)
                logger.info(f"Global search returned {len(results)} results for query: '{query}'")
                return results

        except Exception as e:
            logger.error(f"Error during similarity search with filters: {e}")
            return []

    def _search_with_prefiltering(
        self,
        query: str,
        k: int,
        doc_ids: Optional[List[str]] = None,
        entity_ids: Optional[List[str]] = None
    ) -> List[Document]:
        """Perform search with pre-filtering using chunk indices"""
        logger.debug("Using pre-filtering approach for search")

        # Get relevant chunk indices based on filters
        relevant_chunk_indices = set()

        if doc_ids:
            for doc_id in doc_ids:
                if doc_id in self.doc_to_chunks:
                    relevant_chunk_indices.update(self.doc_to_chunks[doc_id])

        if entity_ids:
            entity_chunk_indices = set()
            logger.debug(f"Filtering by entity_ids: {entity_ids}")
            logger.debug(f"Available entity_ids in mapping: {list(self.entity_to_chunks.keys())[:10]}...")  # Show first 10

            for entity_id in entity_ids:
                if entity_id in self.entity_to_chunks:
                    chunk_count = len(self.entity_to_chunks[entity_id])
                    entity_chunk_indices.update(self.entity_to_chunks[entity_id])
                    logger.debug(f"Found {chunk_count} chunks for entity_id: {entity_id}")
                else:
                    logger.debug(f"Entity_id {entity_id} not found in mapping")

            logger.debug(f"Total entity_chunk_indices found: {len(entity_chunk_indices)}")

            if doc_ids:
                # Intersection: chunks that match both doc_ids AND entity_ids
                relevant_chunk_indices = relevant_chunk_indices.intersection(entity_chunk_indices)
                logger.debug(f"After doc+entity intersection: {len(relevant_chunk_indices)} chunks")
            else:
                # Only entity_ids filter
                relevant_chunk_indices = entity_chunk_indices
                logger.debug(f"Using entity filter only: {len(relevant_chunk_indices)} chunks")

        if not relevant_chunk_indices:
            logger.debug("No chunks match the filtering criteria")
            return []

        # Convert to sorted list for consistent ordering
        relevant_indices = sorted(list(relevant_chunk_indices))
        logger.debug(f"Pre-filtering found {len(relevant_indices)} relevant chunks")

        # Create a subset vector store or search with index filtering
        if hasattr(self.vector_store, 'similarity_search_by_vector'):
            # Use FAISS search with index filtering
            return self._search_with_index_filtering(query, k, relevant_indices)
        else:
            # Fallback to metadata filtering
            return self._search_with_metadata_filtering(query, k, doc_ids, entity_ids)

    def _search_with_index_filtering(self, query: str, k: int, relevant_indices: List[int]) -> List[Document]:
        """Search using FAISS index filtering for better performance"""
        try:
            if not self.embeddings or not self.vector_store:
                return []

            # Get query embedding
            query_embedding = self.embeddings.embed_query(query)

            # Search all vectors and get similarities with indices
            all_scores, all_indices = self.vector_store.index.search(
                np.array([query_embedding], dtype=np.float32),
                len(self.chunk_metadata)  # Get all results
            )

            # Filter results to only include relevant indices
            filtered_results = []
            relevant_indices_set = set(relevant_indices)  # Convert to set for faster lookup

            for idx in all_indices[0]:
                if idx in relevant_indices_set and len(filtered_results) < k:
                    # Get document from docstore
                    docstore_id = self.vector_store.index_to_docstore_id[idx]
                    doc = self.vector_store.docstore.search(docstore_id)
                    if doc:
                        filtered_results.append(doc)

            logger.debug(f"Index filtering returned {len(filtered_results)} results")
            return filtered_results

        except Exception as e:
            logger.error(f"Index filtering failed: {e}")
            # Fallback to metadata filtering
            return self._search_with_metadata_filtering(query, k, None, None)

    def _search_with_metadata_filtering(
        self,
        query: str,
        k: int,
        doc_ids: Optional[List[str]],
        entity_ids: Optional[List[str]]
    ) -> List[Document]:
        """Fallback search using metadata filtering"""
        logger.debug("Using metadata filtering fallback")

        if not self.vector_store:
            return []

        def create_filter_func():
            if not (doc_ids or entity_ids):
                return None

            def filter_func(metadata_dict):
                # Check doc_id filter
                if doc_ids:
                    doc_id = metadata_dict.get('metadata', {}).get('doc_id')
                    if doc_id not in doc_ids:
                        return False

                # Check entity_ids filter
                if entity_ids:
                    doc_entity_ids = metadata_dict.get('metadata', {}).get('entity_ids', [])
                    if not any(entity_id in doc_entity_ids for entity_id in entity_ids):
                        return False

                return True

            return filter_func

        filter_func = create_filter_func()
        results = self.vector_store.similarity_search(query, k=k, filter=filter_func, fetch_k=10000)
        logger.debug(f"Metadata filtering returned {len(results)} results")
        return results

    # ============================================================================
    # AGENTIC RAG FUNCTIONS - CHUNK NAVIGATION & ENTITY-SCOPED SEARCH
    # ============================================================================

    def get_chunk_by_id(self, doc_id: str, chunk_order_index: int) -> Optional[Dict[str, Any]]:
        """Get a specific chunk by doc_id and chunk_order_index"""
        try:
            with get_storage_session() as db:
                chunk_id = f"chunk_{doc_id}_{chunk_order_index}"
                chunk = db[Config.CHUNKS_COLLECTION].find_one({"_id": chunk_id})

                if chunk:
                    logger.debug(f"Found chunk: {chunk_id}")
                    return chunk
                else:
                    logger.debug(f"Chunk not found: {chunk_id}")
                    return None

        except Exception as e:
            logger.error(f"Error getting chunk {doc_id}:{chunk_order_index}: {e}")
            return None

    def get_previous_chunk(self, doc_id: str, current_chunk_index: int) -> Optional[Dict[str, Any]]:
        """Get the previous chunk in the same document"""
        if current_chunk_index <= 0:
            logger.debug(f"No previous chunk for {doc_id}:{current_chunk_index}")
            return None

        chunk = self.get_chunk_by_id(doc_id, current_chunk_index - 1)
        if chunk:
            return {
                'content': chunk['chunk']['content'],
                'doc_id': chunk['metadata']['doc_id'],
                'chunk_order_index': chunk["chunk"]['chunk_order_index'],
                'source': chunk['chunk']['source'],
                'can_navigate': True
            }
        else:
            return None

    def get_next_chunk(self, doc_id: str, current_chunk_index: int) -> Optional[Dict[str, Any]]:
        """Get the next chunk in the same document"""
        next_chunk = self.get_chunk_by_id(doc_id, current_chunk_index + 1)
        if next_chunk:
            logger.debug(f"Found next chunk: {doc_id}:{current_chunk_index + 1}")
            return {
                'content': next_chunk['chunk']['content'],
                'doc_id': next_chunk['metadata']['doc_id'],
                'chunk_order_index': next_chunk["chunk"]['chunk_order_index'],
                'source': next_chunk['chunk']['source'],
                'can_navigate': True
            }
        logger.debug(f"No next chunk for {doc_id}:{current_chunk_index}")
        return None

    def get_chunk_context(self, doc_id: str, chunk_order_index: int, context_size: int = 1) -> Dict[str, Any]:
        """Get chunk with surrounding context chunks"""
        current = self.get_chunk_by_id(doc_id, chunk_order_index)
        if not current:
            return {"current": None, "previous": [], "next": []}

        previous_chunks = []
        next_chunks = []

        # Get previous chunks
        for i in range(1, context_size + 1):
            prev_chunk = self.get_previous_chunk(doc_id, chunk_order_index - i + 1)
            if prev_chunk:
                previous_chunks.append(prev_chunk)
            else:
                break

        # Get next chunks
        for i in range(1, context_size + 1):
            next_chunk = self.get_next_chunk(doc_id, chunk_order_index + i - 1)
            if next_chunk:
                next_chunks.append(next_chunk)
            else:
                break

        return {
            "current": {
                'content': current['chunk']['content'],
                'doc_id': current['metadata']['doc_id'],
                'chunk_order_index': current["chunk"]['chunk_order_index'],
                'source': current['chunk']['source'],
                'can_navigate': True
            },
            "previous": list(reversed(previous_chunks)),  # Order from oldest to newest
            "next": next_chunks
        }

    def semantic_search_within_entity(self, query: str, entity_id: str, k: int = 5) -> List[Dict[str, Any]]:
        """Semantic search scoped to a specific entity with chunk metadata"""
        logger.debug(f"Semantic search within entity {entity_id}: {query}")

        # Use existing filtered search
        results = self.search_documents(query, k=k, entity_ids=[entity_id])

        # Convert to format with chunk navigation info
        enhanced_results = []
        for doc in results:
            if hasattr(doc, 'metadata'):
                metadata = doc.metadata
                doc_id = metadata.get('metadata', {}).get('doc_id')
                source = metadata.get('chunk', {}).get('source')
                chunk_index = metadata.get('chunk', {}).get('chunk_order_index')

                if doc_id is not None and chunk_index is not None:
                    enhanced_results.append({
                        'content': doc.page_content,
                        'doc_id': doc_id,
                        'chunk_order_index': chunk_index,
                        'source': source,
                        'can_navigate': True
                    })
                else:
                    enhanced_results.append({
                        'content': doc.page_content,
                        'doc_id': doc_id,
                        'chunk_order_index': None,
                        'source': source,
                        'can_navigate': False
                    })

        logger.info(f"Found {len(enhanced_results)} chunks for entity {entity_id}")
        return enhanced_results

    def semantic_search_within_document(self, query: str, doc_id: str, k: int = 5) -> List[Dict[str, Any]]:
        """Semantic search scoped to a specific document"""
        logger.debug(f"Semantic search within document {doc_id}: {query}")

        results = self.search_documents(query, k=k, doc_ids=[doc_id])

        # Convert to format with chunk navigation info
        enhanced_results = []
        for doc in results:
            if hasattr(doc, 'metadata'):
                metadata = doc.metadata
                chunk_index = metadata.get('chunk', {}).get('chunk_order_index')

                enhanced_results.append({
                    'content': doc.page_content,
                    'doc_id': doc_id,
                    'chunk_order_index': chunk_index,
                    'metadata': metadata,
                    'can_navigate': chunk_index is not None
                })

        logger.info(f"Found {len(enhanced_results)} chunks for document {doc_id}")
        return enhanced_results

    def get_document_chunks_in_order(self, doc_id: str) -> List[Dict[str, Any]]:
        """Get all chunks of a document in order"""
        logger.debug(f"Getting all chunks for document: {doc_id}")

        try:
            with get_storage_session() as db:
                # Get chunks and sort them manually (JSONStorage doesn't support chained sort)
                chunks = db[Config.CHUNKS_COLLECTION].find(
                    {"metadata.doc_id": doc_id}
                )

                # Sort by chunk_order_index
                if chunks:
                    chunks = sorted(
                        chunks,
                        key=lambda x: x.get('chunk', {}).get('chunk_order_index', 0)
                    )

                logger.info(f"Found {len(chunks)} chunks for document {doc_id}")
                return chunks

        except Exception as e:
            logger.error(f"Error getting document chunks {doc_id}: {e}")
            return []

    def get_entity_documents(self, entity_id: str) -> List[Dict[str, Any]]:
        """Get all documents associated with an entity"""
        logger.debug(f"Getting documents for entity: {entity_id}")

        try:
            with get_storage_session() as db:
                docs = list(db[Config.DOC_ID_NAME_MAPPING_COLLECTION].find(
                    {"entity_ids": entity_id}
                ))

                # Ensure each document has doc_id field for API and agent compatibility
                # If doc_id is missing, use _id as fallback
                for doc in docs:
                    if "doc_id" not in doc and "_id" in doc:
                        doc["doc_id"] = doc["_id"]

                logger.info(f"Found {len(docs)} documents for entity {entity_id}")
                return docs

        except Exception as e:
            logger.error(f"Error getting entity documents {entity_id}: {e}")
            return []

    def get_chunk_neighbors(self, doc_id: str, chunk_order_index: int, window_size: int = 2) -> List[Dict[str, Any]]:
        """Get neighboring chunks within a window"""
        logger.debug(f"Getting neighbors for {doc_id}:{chunk_order_index} with window {window_size}")

        neighbors = []
        start_idx = max(0, chunk_order_index - window_size)
        end_idx = chunk_order_index + window_size + 1

        for idx in range(start_idx, end_idx):
            chunk = self.get_chunk_by_id(doc_id, idx)
            if chunk:
                chunk['is_current'] = (idx == chunk_order_index)
                neighbors.append(chunk)

        logger.debug(f"Found {len(neighbors)} neighbor chunks")
        return neighbors

    def search_and_navigate(self, query: str, entity_id: str, k: int = 3) -> Dict[str, Any]:
        """Search within entity and return results with navigation capabilities"""
        search_results = self.semantic_search_within_entity(query, entity_id, k)

        # Add navigation info to each result
        for result in search_results:
            if result['can_navigate']:
                doc_id = result['doc_id']
                chunk_idx = result['chunk_order_index']

                result['navigation'] = {
                    'has_previous': chunk_idx > 0,
                    'has_next': self.get_next_chunk(doc_id, chunk_idx) is not None,
                    'document_total_chunks': len(self.get_document_chunks_in_order(doc_id))
                }

        return {
            'query': query,
            'entity_id': entity_id,
            'results': search_results,
            'total_found': len(search_results)
        }
    
    def save_vector_store(self, save_path: Optional[str]=None):
        if not save_path:
            save_path = self.vector_store_path
        logger.info(f"Attempting to save vector store to: {save_path}")
        
        if self.vector_store:
            try:
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                self.vector_store.save_local(save_path)
                logger.info(f"Successfully saved vector store at {save_path}")
            except Exception as e:
                logger.error(f"Failed to save vector store: {e}")
        else:
            logger.warning("No vector store to save")

    def load_vector_store(self, load_path: Optional[str]=None):
        """Load vector store from disk"""
        if load_path is None:
            load_path = self.vector_store_path

        logger.info(f"Attempting to load vector store from: {load_path}")

        if os.path.exists(load_path):
            try:
                if self.embeddings:
                    self.vector_store = FAISS.load_local(
                        load_path,
                        self.embeddings,
                        allow_dangerous_deserialization=True
                    )
                logger.info(f"Successfully loaded vector store from {load_path}")

                # Rebuild indices for efficient filtering
                self._rebuild_indices_from_vector_store()

            except Exception as e:
                logger.error(f"Failed to load vector store: {e}")
        else:
            logger.warning(f"Vector store path does not exist: {load_path}")
            self.load_and_process_documents(Config.DATA_DIR)
            self.save_vector_store(load_path)

# Create global singleton instance
rag_pool = RAGSystemPool()

# Helper functions for easy access
def get_rag_system() -> Optional[RAGSystem]:
    """Get RAG system instance"""
    return rag_pool.get_rag_system()

def is_rag_available() -> bool:
    """Check if RAG system is available"""
    return rag_pool.is_available()

@contextmanager
def get_rag_context():
    """Context manager for RAG operations"""
    with rag_pool.get_rag_context() as rag:
        yield rag

def index_document_safe(file_path: str, entity_id: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Thread-safe document indexing"""
    if not is_rag_available():
        logger.warning("RAG system not available for indexing")
        return None

    try:
        with get_rag_context() as rag:
            return rag.index_single_document(file_path, entity_id, metadata)
    except Exception as e:
        logger.error(f"Failed to index document safely: {e}")
        return None

def debug_entity_search(entity_id: str):
    """Debug function to check entity mapping issues"""
    if not is_rag_available():
        logger.warning("RAG system not available for debugging")
        return

    try:
        with get_rag_context() as rag:
            rag.debug_entity_mappings(entity_id)
    except Exception as e:
        logger.error(f"Failed to debug entity mappings: {e}")


# ============================================================================
# ENTITY-SCOPED RAG FUNCTIONS - Parallel Processing with Isolated Indexes
# ============================================================================

from .manager import File

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


def index_documents_parallel(entity_documents: Dict[str, List[str]],
                            metadata: Optional[Dict[str, Any]] = None) -> Dict[str, List[Dict[str, Any]]]:
    """
    Index documents for multiple entities in parallel

    Args:
        entity_documents: Dict mapping entity_id -> list of file paths
        metadata: Optional metadata for all documents

    Returns:
        Dict mapping entity_id -> list of document info dicts

    Example:
        >>> results = index_documents_parallel({
        ...     "company_123": ["/path/to/doc1.pdf", "/path/to/doc2.pdf"],
        ...     "company_456": ["/path/to/doc3.pdf"]
        ... })
    """
    try:
        manager = get_entity_rag_manager()
        return manager.add_documents_parallel(entity_documents, metadata)
    except Exception as e:
        logger.error(f"Failed to index documents in parallel: {e}")
        return {}


def search_entity_scoped(entity_id: str, query: str, k: int = 5,
                        doc_ids: Optional[List[str]] = None) -> List[Document]:
    """
    Search within a specific entity's isolated vector store (faster than global search)

    Args:
        entity_id: Entity identifier
        query: Search query
        k: Number of results
        doc_ids: Optional document filter

    Returns:
        List of matching documents
    """
    try:
        manager = get_entity_rag_manager()
        return manager.search(entity_id, query, k, doc_ids)
    except Exception as e:
        logger.error(f"Failed to search entity {entity_id}: {e}")
        return []


def search_multiple_entities_parallel(entity_ids: List[str], query: str,
                                      k: int = 5) -> Dict[str, List[Document]]:
    """
    Search across multiple entities in parallel

    Args:
        entity_ids: List of entity identifiers
        query: Search query
        k: Number of results per entity

    Returns:
        Dict mapping entity_id -> list of documents

    Example:
        >>> results = search_multiple_entities_parallel(
        ...     ["company_123", "company_456"],
        ...     "What are the key findings?",
        ...     k=3
        ... )
        >>> for entity_id, docs in results.items():
        ...     print(f"{entity_id}: {len(docs)} results")
    """
    try:
        manager = get_entity_rag_manager()
        return manager.search_multiple_entities(entity_ids, query, k)
    except Exception as e:
        logger.error(f"Failed to search multiple entities: {e}")
        return {}


def delete_document_entity_scoped(entity_id: str, doc_id: str) -> bool:
    """
    Delete a document from an entity's vector store

    Args:
        entity_id: Entity identifier
        doc_id: Document identifier

    Returns:
        True if successful
    """
    try:
        manager = get_entity_rag_manager()
        return manager.delete_document(entity_id, doc_id)
    except Exception as e:
        logger.error(f"Failed to delete document {doc_id} from entity {entity_id}: {e}")
        return False


def get_entity_stats(entity_id: str) -> Dict[str, Any]:
    """
    Get statistics for an entity's vector store

    Args:
        entity_id: Entity identifier

    Returns:
        Statistics dict
    """
    try:
        manager = get_entity_rag_manager()
        return manager.get_entity_stats(entity_id)
    except Exception as e:
        logger.error(f"Failed to get stats for entity {entity_id}: {e}")
        return {}


def get_all_entity_stats() -> Dict[str, Dict[str, Any]]:
    """
    Get statistics for all entities

    Returns:
        Dict mapping entity_id -> statistics
    """
    try:
        manager = get_entity_rag_manager()
        return manager.get_all_entity_stats()
    except Exception as e:
        logger.error(f"Failed to get all entity stats: {e}")
        return {}
