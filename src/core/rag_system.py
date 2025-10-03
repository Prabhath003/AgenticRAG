# -----------------------------------------------------------------------------
# Copyright (c) 2025 Backend
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

# src/rag_system.py
import os
import json
from typing import List, Optional, Dict, Any
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
import glob

from .parsers.file_parser import convert_to_md
from .utils.chunker import chunk_by_MarkDownSplitter
from ..config import Config
from ..log_creator import get_file_logger

logger = get_file_logger()

# JSON Storage Helper Functions
class JSONStorage:
    """Helper class for JSON file storage operations"""

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or Config.DATA_DIR
        self.storage_dir = os.path.join(self.data_dir, "json_storage")
        os.makedirs(self.storage_dir, exist_ok=True)

        # File paths for different collections
        self.chunks_file = os.path.join(self.storage_dir, "chunks.json")
        self.doc_mappings_file = os.path.join(self.storage_dir, "doc_id_name_mappings.json")
        self.entity_mappings_file = os.path.join(self.storage_dir, "entity_mappings.json")

        # Initialize files if they don't exist
        self._ensure_file_exists(self.chunks_file, {})
        self._ensure_file_exists(self.doc_mappings_file, {})
        self._ensure_file_exists(self.entity_mappings_file, {})

    def _ensure_file_exists(self, file_path: str, default_content: Dict[str, Any]):
        """Ensure a JSON file exists with default content"""
        if not os.path.exists(file_path):
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(default_content, f, indent=2, ensure_ascii=False, default=str)

    def _read_json_file(self, file_path: str) -> Dict[str, Any]:
        """Read JSON file safely"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to read {file_path}: {e}, returning empty dict")
            return {}

    def _write_json_file(self, file_path: str, data: Dict[str, Any]):
        """Write JSON file safely"""
        try:
            # Create backup
            if os.path.exists(file_path):
                backup_path = f"{file_path}.backup"
                os.rename(file_path, backup_path)

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)

            # Remove backup if write was successful
            backup_path = f"{file_path}.backup"
            if os.path.exists(backup_path):
                os.remove(backup_path)
        except Exception as e:
            logger.error(f"Failed to write {file_path}: {e}")
            # Restore backup if it exists
            backup_path = f"{file_path}.backup"
            if os.path.exists(backup_path):
                os.rename(backup_path, file_path)
            raise

    def find_chunks(self, query: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Find chunks matching query"""
        data = self._read_json_file(self.chunks_file)
        results = []

        for chunk_id, chunk_data in data.items():
            if query:
                match = True
                for key, value in query.items():
                    if key == "_id":
                        if chunk_id != value:
                            match = False
                            break
                    elif key.startswith("metadata."):
                        metadata_key = key.replace("metadata.", "")
                        chunk_metadata = chunk_data.get("metadata", {})
                        if chunk_metadata.get(metadata_key) != value:
                            match = False
                            break
                    elif chunk_data.get(key) != value:
                        match = False
                        break

                if match:
                    chunk_copy = chunk_data.copy()
                    chunk_copy["_id"] = chunk_id
                    results.append(chunk_copy)
            else:
                chunk_copy = chunk_data.copy()
                chunk_copy["_id"] = chunk_id
                results.append(chunk_copy)

        return results

    def find_one_chunk(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find single chunk matching query"""
        results = self.find_chunks(query)
        return results[0] if results else None

    def update_chunk(self, chunk_id: str, data: Dict[str, Any], upsert: bool = False):
        """Update or insert chunk"""
        chunks_data = self._read_json_file(self.chunks_file)

        if chunk_id in chunks_data or upsert:
            # Remove _id from data if present to avoid duplication
            if "_id" in data:
                del data["_id"]
            chunks_data[chunk_id] = data
            self._write_json_file(self.chunks_file, chunks_data)
        elif not upsert:
            logger.warning(f"Chunk {chunk_id} not found and upsert=False")

    def find_doc_mappings(self, query: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Find document mappings matching query"""
        data = self._read_json_file(self.doc_mappings_file)
        results = []

        for doc_id, doc_data in data.items():
            if query:
                match = True
                for key, value in query.items():
                    if key == "doc_id":
                        if doc_id != value:
                            match = False
                            break
                    elif key == "entity_ids":
                        if isinstance(value, str):
                            if value not in doc_data.get("entity_ids", []):
                                match = False
                                break
                        elif isinstance(value, dict) and "$exists" in value:
                            exists = "entity_ids" in doc_data
                            if exists != value["$exists"]:
                                match = False
                                break
                    elif doc_data.get(key) != value:
                        match = False
                        break

                if match:
                    doc_copy = doc_data.copy()
                    doc_copy["doc_id"] = doc_id
                    results.append(doc_copy)
            else:
                doc_copy = doc_data.copy()
                doc_copy["doc_id"] = doc_id
                results.append(doc_copy)

        return results

    def find_one_doc_mapping(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Find single document mapping matching query"""
        results = self.find_doc_mappings(query)
        return results[0] if results else None

    def update_doc_mapping(self, doc_id: str, data: Dict[str, Any], upsert: bool = False):
        """Update or insert document mapping"""
        doc_mappings = self._read_json_file(self.doc_mappings_file)

        if doc_id in doc_mappings:
            # Handle $set and $addToSet operations
            if "$set" in data:
                doc_mappings[doc_id].update(data["$set"])
            if "$addToSet" in data:
                for key, value in data["$addToSet"].items():
                    if key not in doc_mappings[doc_id]:
                        doc_mappings[doc_id][key] = []
                    if value not in doc_mappings[doc_id][key]:
                        doc_mappings[doc_id][key].append(value)
            if "$setOnInsert" in data and doc_id not in doc_mappings:
                doc_mappings[doc_id] = data["$setOnInsert"]
        elif upsert:
            new_doc = {}
            if "$set" in data:
                new_doc.update(data["$set"])
            if "$setOnInsert" in data:
                new_doc.update(data["$setOnInsert"])
            if "$addToSet" in data:
                for key, value in data["$addToSet"].items():
                    new_doc[key] = [value]
            doc_mappings[doc_id] = new_doc

        self._write_json_file(self.doc_mappings_file, doc_mappings)

    def delete_doc_mapping(self, doc_id: str):
        """Delete document mapping"""
        doc_mappings = self._read_json_file(self.doc_mappings_file)
        if doc_id in doc_mappings:
            del doc_mappings[doc_id]
            self._write_json_file(self.doc_mappings_file, doc_mappings)

    def find_entity_mappings(self, query: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Find entity mappings matching query"""
        data = self._read_json_file(self.entity_mappings_file)
        results = []

        for mapping_id, mapping_data in data.items():
            if query:
                match = True
                for key, value in query.items():
                    if key == "_id":
                        if mapping_id != value:
                            match = False
                            break
                    elif key == "$or":
                        # Handle $or queries
                        or_match = False
                        for or_condition in value:
                            condition_match = True
                            for cond_key, cond_value in or_condition.items():
                                if mapping_data.get(cond_key) != cond_value:
                                    condition_match = False
                                    break
                            if condition_match:
                                or_match = True
                                break
                        if not or_match:
                            match = False
                            break
                    elif mapping_data.get(key) != value:
                        match = False
                        break

                if match:
                    mapping_copy = mapping_data.copy()
                    mapping_copy["_id"] = mapping_id
                    results.append(mapping_copy)
            else:
                mapping_copy = mapping_data.copy()
                mapping_copy["_id"] = mapping_id
                results.append(mapping_copy)

        return results

    def update_entity_mapping(self, mapping_id: str, data: Dict[str, Any], upsert: bool = False):
        """Update or insert entity mapping"""
        entity_mappings = self._read_json_file(self.entity_mappings_file)

        if mapping_id in entity_mappings:
            # Handle $addToSet operations
            if "$addToSet" in data:
                for key, value in data["$addToSet"].items():
                    if key not in entity_mappings[mapping_id]:
                        entity_mappings[mapping_id][key] = []
                    if value not in entity_mappings[mapping_id][key]:
                        entity_mappings[mapping_id][key].append(value)
            if "$setOnInsert" in data and mapping_id not in entity_mappings:
                entity_mappings[mapping_id] = data["$setOnInsert"]
        elif upsert:
            new_mapping = {}
            if "$setOnInsert" in data:
                new_mapping.update(data["$setOnInsert"])
            if "$addToSet" in data:
                for key, value in data["$addToSet"].items():
                    new_mapping[key] = [value]
            entity_mappings[mapping_id] = new_mapping

        self._write_json_file(self.entity_mappings_file, entity_mappings)

    def aggregate_doc_mappings(self, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Simple aggregation for document mappings (limited MongoDB aggregation emulation)"""
        data = self._read_json_file(self.doc_mappings_file)
        results = []

        # Convert to list format for processing
        docs = []
        for doc_id, doc_data in data.items():
            doc_copy = doc_data.copy()
            doc_copy["doc_id"] = doc_id
            docs.append(doc_copy)

        # Process pipeline stages
        current_docs = docs
        for stage in pipeline:
            if "$match" in stage:
                # Simple match stage
                match_criteria = stage["$match"]
                filtered_docs = []
                for doc in current_docs:
                    match = True
                    for key, value in match_criteria.items():
                        if key == "content_hash" and "$exists" in value:
                            exists = "content_hash" in doc
                            if exists != value["$exists"]:
                                match = False
                                break
                        elif doc.get(key) != value:
                            match = False
                            break
                    if match:
                        filtered_docs.append(doc)
                current_docs = filtered_docs

            elif "$group" in stage:
                # Simple group stage for duplicate detection
                group_criteria = stage["$group"]
                if "_id" in group_criteria and "docs" in group_criteria and "count" in group_criteria:
                    group_key = group_criteria["_id"]
                    groups = {}

                    for doc in current_docs:
                        key_value = doc.get(group_key.replace("$", ""))
                        if key_value not in groups:
                            groups[key_value] = []
                        groups[key_value].append(doc)

                    grouped_results = []
                    for key_value, group_docs in groups.items():
                        grouped_results.append({
                            "_id": key_value,
                            "docs": [{"doc_id": doc["doc_id"], "entity_ids": doc.get("entity_ids", [])} for doc in group_docs],
                            "count": len(group_docs)
                        })

                    current_docs = grouped_results

            elif "$match" in stage and "count" in stage["$match"]:
                # Filter by count (for duplicate detection)
                count_filter = stage["$match"]["count"]
                if "$gt" in count_filter:
                    min_count = count_filter["$gt"]
                    current_docs = [doc for doc in current_docs if doc.get("count", 0) > min_count]

        return current_docs

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
    def __init__(self):
        logger.info("Initializing RAG System")

        # Initialize JSON storage
        self.storage = JSONStorage()

        # Initialize all attributes first to prevent AttributeError
        # Initialize loaders dictionary first (prevents the AttributeError)
        logger.debug("Document loaders initialized")

        # Initialize embeddings
        self.embeddings = HuggingFaceEmbeddings(
            model_name=Config.EMBEDDINGS_MODEL
        )
        logger.info(f"Loaded embeddings model: {Config.EMBEDDINGS_MODEL}")

        # Set vector store path
        self.vector_store_path = os.path.join(Config.DATA_DIR, "vector_stores")

        # Entity-specific vector stores
        self.entity_vector_stores: Dict[str, FAISS] = {}  # entity_id -> FAISS store
        self.global_vector_store: Optional[FAISS] = None  # For global search across all entities

        self.document_hashes: Dict[str, str] = {}
        self.doc_id_to_hash: Dict[str, str] = {}

        # Index mappings for efficient filtering - keeping for backward compatibility
        self.entity_to_chunks: Dict[str, set] = {}  # entity_id -> set of chunk indices
        self.doc_to_chunks: Dict[str, set] = {}     # doc_id -> set of chunk indices
        self.chunk_metadata: Dict[int, Dict[str, Any]] = {}  # chunk index -> metadata

        # Entity-specific metadata tracking
        self.entity_chunk_metadata: Dict[str, Dict[int, Dict[str, Any]]] = {}  # entity_id -> chunk_index -> metadata
        self.entity_doc_mapping: Dict[str, Dict[str, set]] = {}  # entity_id -> doc_id -> chunk_indices

        try:
            # Load existing vector stores (this should be done last)
            self.load_vector_stores(self.vector_store_path)

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
            # Find documents with same content hash using JSON storage
            pipeline: List[Dict[str, Dict[str, Any]]] = [
                {"$match": {"content_hash": {"$exists": True}}},
                {"$group": {
                    "_id": "$content_hash",
                    "docs": {"$push": {"doc_id": "$doc_id", "entity_ids": "$entity_ids"}},
                    "count": {"$sum": 1}
                }},
                {"$match": {"count": {"$gt": 1}}}
            ]

            duplicates = self.storage.aggregate_doc_mappings(pipeline)
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
                self.storage.update_doc_mapping(
                    primary_doc['doc_id'],
                    {"$set": {"entity_ids": list(all_entity_ids)}}
                )

            if stats['duplicates_removed'] > 0:
                # Rebuild vector stores
                self._rebuild_vector_stores_without_document(None)  # Full rebuild
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
        """Load existing document hashes from JSON storage"""
        try:
            existing_docs = self.storage.find_doc_mappings({"content_hash": {"$exists": True}})

            for doc in existing_docs:
                doc_id = doc["doc_id"]
                content_hash = doc.get("content_hash")
                if content_hash:
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
                existing_doc = self.storage.find_one_doc_mapping(
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
            
            for chunk in split_docs:
                chunk_id = f"chunk_{chunk['metadata']['doc_id']}_{chunk['chunk']['chunk_order_index']}"
                update_doc = {key: value for key, value in chunk.items() if key != "_id"}

                self.storage.update_chunk(chunk_id, update_doc, upsert=True)
            
            # Add to entity-specific vector stores
            entity_ids = split_docs[0]['metadata'].get('entity_ids', [])
            self._create_or_update_entity_vector_stores(split_docs, entity_ids)

            # Also add to global vector store for global search
            self._create_or_update_global_vector_store(split_docs)
            
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
            self.save_vector_stores()
        
    def _add_entity_to_existing_document(self, doc_id: str, entity_id: str):
        """Add entity association to an existing document"""
        try:
            # Update document mapping
            self.storage.update_doc_mapping(
                doc_id,
                {"$addToSet": {"entity_ids": entity_id}}
            )
            logger.debug(f"Added entity {entity_id} to existing document {doc_id}")

        except Exception as e:
            logger.error(f"Failed to add entity to existing document: {e}")
    
    def update_entity_with_document(self, entity_id: str, doc_id: str, doc_name: str, description: str = "Related document"):
        """Update entity document with reference to indexed document (simplified for JSON storage)"""
        logger.debug(f"Updating entity {entity_id} with document {doc_id}")

        try:
            entity_mapping_id = f"{entity_id}_{doc_id}"

            # Check if the entity mapping already exists
            existing_mapping = self.storage.find_entity_mappings({"_id": entity_mapping_id})

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
                        "relations": [["document", "more details"]],
                        "doc_name": doc_name,
                        "description": description,
                        "indexed_at": datetime.now(timezone.utc).isoformat()
                    }
                }

            self.storage.update_entity_mapping(entity_mapping_id, update_data, upsert=True)

            logger.debug(f"Entity mapping updated for entity {entity_id} and document {doc_id}")

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
            
            # Get document info from JSON storage
            relation_docs = self.storage.find_entity_mappings({
                "$or": [
                    {"target_id": doc_id},
                    {"source_id": doc_id}
                ]
            })

            if relation_docs:
                raise RuntimeError("Cannot delete a document when attached to entities, detach the document before")

            doc_info = self.storage.find_one_doc_mapping({"doc_id": doc_id})

            if not doc_info:
                logger.warning(f"Document not found in storage: {doc_id}")
                return False

            # Remove from document mappings
            self.storage.delete_doc_mapping(doc_id)
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
            
            # Remove from vector stores (this requires rebuilding)
            if self.global_vector_store or self.entity_vector_stores:
                self._rebuild_vector_stores_without_document(doc_id)
            
            logger.info(f"Successfully deleted document: {doc_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete document {doc_id}: {e}")
            return False
        
    def _rebuild_vector_stores_without_document(self, doc_id_to_remove: Optional[str]):
        """
        Rebuild both global and entity-specific vector stores excluding a specific document
        This is expensive but necessary for FAISS
        """
        logger.info(f"Rebuilding vector stores without document: {doc_id_to_remove}")

        try:
            # Get document info to find which entities it belonged to
            doc_info = self.storage.find_one_doc_mapping({"doc_id": doc_id_to_remove})

            affected_entities = []
            if doc_info and "entity_ids" in doc_info:
                affected_entities = doc_info["entity_ids"]

            # Rebuild global vector store
            self._rebuild_global_vector_store_without_document(doc_id_to_remove)

            # Rebuild affected entity vector stores
            for entity_id in affected_entities:
                if entity_id in self.entity_vector_stores:
                    self._rebuild_entity_vector_store_without_document(entity_id, doc_id_to_remove)

        except Exception as e:
            logger.error(f"Failed to rebuild vector stores: {e}")
            raise

    def _rebuild_global_vector_store_without_document(self, doc_id_to_remove: Optional[str]):
        """
        Rebuild global vector store excluding a specific document
        This is expensive but necessary for FAISS
        """
        logger.info(f"Rebuilding global vector store without document: {doc_id_to_remove}")
        
        try:
            # Get all remaining documents from JSON storage
            all_docs = self.storage.find_doc_mappings()
            remaining_docs = [doc for doc in all_docs if doc.get("doc_id") != doc_id_to_remove]
            
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
            
            # Rebuild global vector store
            if all_documents:
                if self.embeddings:
                    self.global_vector_store = FAISS.from_documents(
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

                logger.info(f"Rebuilt global vector store with {len(all_documents)} document chunks")

                # Save the rebuilt vector stores
                self.save_vector_stores()
            else:
                logger.warning("No valid documents found for global vector store rebuild")
                # Clear indices if no documents
                self.entity_to_chunks.clear()
                self.doc_to_chunks.clear()
                self.chunk_metadata.clear()

        except Exception as e:
            logger.error(f"Failed to rebuild global vector store: {e}")
            raise

    def _rebuild_entity_vector_store_without_document(self, entity_id: str, doc_id_to_remove: Optional[str]):
        """
        Rebuild entity-specific vector store excluding a specific document
        """
        logger.info(f"Rebuilding entity vector store for {entity_id} without document: {doc_id_to_remove}")

        try:
            # Get all remaining documents for this entity from JSON storage
            all_docs = self.storage.find_doc_mappings({"entity_ids": entity_id})
            remaining_docs = [doc for doc in all_docs if doc.get("doc_id") != doc_id_to_remove]

            if not remaining_docs:
                logger.info(f"No documents remaining for entity {entity_id}, removing vector store")
                del self.entity_vector_stores[entity_id]
                if entity_id in self.entity_chunk_metadata:
                    del self.entity_chunk_metadata[entity_id]
                if entity_id in self.entity_doc_mapping:
                    del self.entity_doc_mapping[entity_id]
                return

            # Reload and reprocess all remaining documents for this entity
            all_documents: List[Dict[str, Dict[str, Any]]] = []

            for doc_info in remaining_docs:
                doc_path = doc_info.get("doc_path")

                if doc_path and os.path.exists(doc_path):
                    try:
                        split_docs = self._load_document(doc_path, entity_id)

                        if split_docs:
                            all_documents.extend(split_docs)
                            logger.debug(f"Reloaded document for entity {entity_id}: {doc_path}")
                    except Exception as e:
                        logger.error(f"Failed to reload document {doc_path} for entity {entity_id}: {e}")

            # Rebuild entity vector store
            if all_documents:
                if self.embeddings:
                    self.entity_vector_stores[entity_id] = FAISS.from_documents(
                        [
                            Document(
                                page_content=doc['chunk']['content'],
                                metadata=doc
                            )
                            for doc in all_documents
                        ],
                        self.embeddings
                    )

                # Rebuild entity-specific indices
                self.entity_chunk_metadata[entity_id] = {}
                self.entity_doc_mapping[entity_id] = {}
                self._update_entity_chunk_indices(entity_id, all_documents, 0)

                logger.info(f"Rebuilt entity vector store for {entity_id} with {len(all_documents)} document chunks")
            else:
                logger.warning(f"No valid documents found for entity {entity_id} vector store rebuild")
                # Remove empty entity store
                if entity_id in self.entity_vector_stores:
                    del self.entity_vector_stores[entity_id]
                if entity_id in self.entity_chunk_metadata:
                    del self.entity_chunk_metadata[entity_id]
                if entity_id in self.entity_doc_mapping:
                    del self.entity_doc_mapping[entity_id]

        except Exception as e:
            logger.error(f"Failed to rebuild entity vector store for {entity_id}: {e}")
            raise
                
        except Exception as e:
            logger.error(f"Failed to rebuild vector store: {e}")
            raise
    
    def _load_document(self, file_path: str, entity_id: Optional[str], metadata: Optional[Dict[str, Any]]=None) -> List[Dict[str, Dict[str, Any]]]:
        """Load and process a single document file"""
        logger.debug(f"Loading document from: {file_path}")
        
        try:
            # Get file extension
            file_ext = Path(file_path).suffix.lower()
            
            # Convert file to markdown
            markdown_content = convert_to_md(file_path)
            if markdown_content is None:
                logger.error(f"Failed to convert file to markdown: {file_path}")
                return []
            
            # You can implement chunking logic here, e.g.:
            chunks = chunk_by_MarkDownSplitter(
                [markdown_content],
                "Qwen/Qwen2.5-32B-Instruct",
                sources=[file_path]
            )
            
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
            
            self.storage.update_doc_mapping(doc_id, update_data, upsert=True)
            logger.debug(f"Document mapping updated for doc_id: {doc_id}")
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
        """Rebuild indices from existing global vector store"""
        if not self.global_vector_store:
            return

        logger.info("Rebuilding chunk indices from global vector store")

        # Clear existing indices
        self.entity_to_chunks.clear()
        self.doc_to_chunks.clear()
        self.chunk_metadata.clear()

        # Get all documents from vector store
        try:
            # Access the docstore to get all documents
            docstore = self.global_vector_store.docstore
            index_to_docstore_id = self.global_vector_store.index_to_docstore_id

            for i in range(len(index_to_docstore_id)):
                docstore_id = index_to_docstore_id[i]
                doc = docstore.search(docstore_id)

                if doc and hasattr(doc, 'metadata') and isinstance(getattr(doc, 'metadata', None), dict):
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
        """Fallback method to rebuild indices by querying JSON storage"""
        logger.info("Using fallback method to rebuild indices")

        try:
            # Get all chunks from JSON storage
            chunks = self.storage.find_chunks()

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

        if self.global_vector_store is None:
            logger.warning("No global vector store loaded - cannot perform search")
            return []

        try:
            results = self.global_vector_store.similarity_search(query, k=k)
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

        try:
            # If single entity_id specified, use entity-specific index for better performance
            if entity_ids and len(entity_ids) == 1 and not doc_ids:
                return self.search_entity_documents(entity_ids[0], query, k)

            # If multiple entities specified, search each entity index and combine results
            if entity_ids and not doc_ids:
                return self._search_multiple_entities(query, k, entity_ids)

            # Fallback to global search with filtering
            if self.global_vector_store is None:
                logger.warning("No global vector store loaded - cannot perform search")
                return []

            # Use optimized pre-filtering if filters are specified
            if doc_ids or entity_ids:
                return self._search_with_prefiltering(query, k, doc_ids, entity_ids)
            else:
                # Fallback to global search
                results = self.global_vector_store.similarity_search(query, k=k)
                logger.info(f"Global search returned {len(results)} results for query: '{query}'")
                return results

        except Exception as e:
            logger.error(f"Error during similarity search with filters: {e}")
            return []

    def search_entity_documents(self, entity_id: str, query: str, k: int = 5) -> List[Document]:
        """Search documents within a specific entity using its dedicated vector store"""
        logger.debug(f"Performing entity-specific search for entity: '{entity_id}' with query: '{query}', k={k}")

        if entity_id not in self.entity_vector_stores:
            logger.warning(f"No vector store found for entity: {entity_id}")
            return []

        try:
            entity_store = self.entity_vector_stores[entity_id]
            results = entity_store.similarity_search(query, k=k)
            logger.info(f"Entity search returned {len(results)} results for entity '{entity_id}' with query: '{query}'")
            return results
        except Exception as e:
            logger.error(f"Error during entity-specific search for {entity_id}: {e}")
            return []

    def _search_multiple_entities(self, query: str, k: int, entity_ids: List[str]) -> List[Document]:
        """Search across multiple entity-specific indexes and combine results"""
        logger.debug(f"Performing multi-entity search for entities: {entity_ids} with query: '{query}', k={k}")

        all_results = []
        k_per_entity = max(1, k // len(entity_ids))  # Distribute k across entities
        remainder = k % len(entity_ids)

        for i, entity_id in enumerate(entity_ids):
            # Give some entities one extra result if k doesn't divide evenly
            entity_k = k_per_entity + (1 if i < remainder else 0)
            entity_results = self.search_entity_documents(entity_id, query, entity_k)
            all_results.extend(entity_results)

        # Sort combined results by relevance (if similarity scores are available)
        # For now, just return first k results
        return all_results[:k]

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
        if hasattr(self.global_vector_store, 'similarity_search_by_vector'):
            # Use FAISS search with index filtering
            return self._search_with_index_filtering(query, k, relevant_indices)
        else:
            # Fallback to metadata filtering
            return self._search_with_metadata_filtering(query, k, doc_ids, entity_ids)

    def _search_with_index_filtering(self, query: str, k: int, relevant_indices: List[int]) -> List[Document]:
        """Search using FAISS index filtering for better performance"""
        try:
            if not self.embeddings or not self.global_vector_store:
                return []

            # Get query embedding
            query_embedding = self.embeddings.embed_query(query)

            # Search all vectors and get similarities with indices
            all_scores, all_indices = self.global_vector_store.index.search(
                np.array([query_embedding], dtype=np.float32),
                len(self.chunk_metadata)  # Get all results
            )

            # Filter results to only include relevant indices
            filtered_results = []
            relevant_indices_set = set(relevant_indices)  # Convert to set for faster lookup

            for idx in all_indices[0]:
                if idx in relevant_indices_set and len(filtered_results) < k:
                    # Get document from docstore
                    docstore_id = self.global_vector_store.index_to_docstore_id[idx]
                    doc = self.global_vector_store.docstore.search(docstore_id)
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

        if not self.global_vector_store:
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
        results = self.global_vector_store.similarity_search(query, k=k, filter=filter_func, fetch_k=10000)
        logger.debug(f"Metadata filtering returned {len(results)} results")
        return results

    # ============================================================================
    # AGENTIC RAG FUNCTIONS - CHUNK NAVIGATION & ENTITY-SCOPED SEARCH
    # ============================================================================

    def get_chunk_by_id(self, doc_id: str, chunk_order_index: int) -> Optional[Dict[str, Any]]:
        """Get a specific chunk by doc_id and chunk_order_index"""
        try:
            chunk_id = f"chunk_{doc_id}_{chunk_order_index}"
            chunk = self.storage.find_one_chunk({"_id": chunk_id})

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

        # Use entity-specific vector store for better performance
        results = self.search_entity_documents(entity_id, query, k)

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
            chunks = self.storage.find_chunks({"metadata.doc_id": doc_id})

            # Sort by chunk order index
            chunks.sort(key=lambda x: x.get("chunk", {}).get("chunk_order_index", 0))

            logger.info(f"Found {len(chunks)} chunks for document {doc_id}")
            return chunks

        except Exception as e:
            logger.error(f"Error getting document chunks {doc_id}: {e}")
            return []

    def get_entity_documents(self, entity_id: str) -> List[Dict[str, Any]]:
        """Get all documents associated with an entity"""
        logger.debug(f"Getting documents for entity: {entity_id}")

        try:
            docs = self.storage.find_doc_mappings({"entity_ids": entity_id})

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
    
    def _create_or_update_entity_vector_stores(self, documents: List[Dict[str, Dict[str, Any]]], entity_ids: List[str]):
        """Create or update entity-specific vector stores with documents"""
        logger.debug(f"Creating/updating entity vector stores for entities: {entity_ids} with {len(documents)} documents")

        try:
            for entity_id in entity_ids:
                if not entity_id:
                    continue

                # Initialize entity tracking if not exists
                if entity_id not in self.entity_chunk_metadata:
                    self.entity_chunk_metadata[entity_id] = {}
                if entity_id not in self.entity_doc_mapping:
                    self.entity_doc_mapping[entity_id] = {}

                # Create FAISS documents
                faiss_documents = [
                    Document(
                        page_content=doc['chunk']['content'],
                        metadata=doc
                    )
                    for doc in documents
                ]

                start_idx = 0  # Initialize default value

                if entity_id in self.entity_vector_stores:
                    # Add to existing entity vector store
                    start_idx = len(self.entity_chunk_metadata[entity_id])
                    self.entity_vector_stores[entity_id].add_documents(faiss_documents)
                    logger.debug(f"Added {len(documents)} documents to existing entity store: {entity_id}")
                else:
                    # Create new entity vector store
                    if self.embeddings:
                        self.entity_vector_stores[entity_id] = FAISS.from_documents(
                            faiss_documents,
                            self.embeddings
                        )
                        start_idx = 0
                        logger.info(f"Created new entity vector store: {entity_id} with {len(documents)} documents")

                # Update entity-specific metadata
                self._update_entity_chunk_indices(entity_id, documents, start_idx)

        except Exception as e:
            logger.error(f"Failed to create/update entity vector stores: {e}")
            raise

    def _create_or_update_global_vector_store(self, documents: List[Dict[str, Dict[str, Any]]]):
        """Create or update the global vector store for cross-entity search"""
        logger.debug(f"Creating/updating global vector store with {len(documents)} documents")

        try:
            faiss_documents = [
                Document(
                    page_content=doc['chunk']['content'],
                    metadata=doc
                )
                for doc in documents
            ]

            if self.global_vector_store:
                start_idx = len(self.chunk_metadata)
                self.global_vector_store.add_documents(faiss_documents)
                # Update global indices for backward compatibility
                self._update_chunk_indices(documents, start_idx)
                logger.info(f"Added {len(documents)} documents to existing global vector store")
            else:
                if self.embeddings:
                    self.global_vector_store = FAISS.from_documents(
                        faiss_documents,
                        self.embeddings
                    )
                # Build global indices for backward compatibility
                self._update_chunk_indices(documents, 0)
                logger.info(f"Created new global vector store with {len(documents)} documents")
        except Exception as e:
            logger.error(f"Failed to create/update global vector store: {e}")
            raise

    def _update_entity_chunk_indices(self, entity_id: str, documents: List[Dict[str, Dict[str, Any]]], start_idx: int):
        """Update chunk indices for specific entity"""
        logger.debug(f"Updating entity chunk indices for {entity_id} starting from index {start_idx}")

        for i, doc in enumerate(documents):
            chunk_idx = start_idx + i
            metadata = doc['metadata']

            # Store chunk metadata for this entity
            self.entity_chunk_metadata[entity_id][chunk_idx] = metadata

            # Update entity doc mapping
            doc_id = metadata.get('doc_id')
            if doc_id:
                if doc_id not in self.entity_doc_mapping[entity_id]:
                    self.entity_doc_mapping[entity_id][doc_id] = set()
                self.entity_doc_mapping[entity_id][doc_id].add(chunk_idx)

        logger.debug(f"Updated entity indices for {len(documents)} chunks in entity {entity_id}")

    def get_entity_vector_store(self, entity_id: str) -> Optional[FAISS]:
        """Get vector store for specific entity"""
        return self.entity_vector_stores.get(entity_id)

    def get_available_entities(self) -> List[str]:
        """Get list of all entities with vector stores"""
        return list(self.entity_vector_stores.keys())

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

    def save_vector_stores(self, save_path: Optional[str]=None):
        """Save all entity-specific vector stores and global vector store"""
        if not save_path:
            save_path = self.vector_store_path
        logger.info(f"Attempting to save vector stores to: {save_path}")

        try:
            os.makedirs(save_path, exist_ok=True)

            # Save global vector store
            if self.global_vector_store:
                global_path = os.path.join(save_path, "global")
                os.makedirs(global_path, exist_ok=True)
                self.global_vector_store.save_local(global_path)
                logger.info(f"Saved global vector store at {global_path}")

            # Save entity-specific vector stores
            entities_path = os.path.join(save_path, "entities")
            os.makedirs(entities_path, exist_ok=True)

            for entity_id, vector_store in self.entity_vector_stores.items():
                entity_path = os.path.join(entities_path, entity_id.replace("/", "_"))
                os.makedirs(entity_path, exist_ok=True)
                vector_store.save_local(entity_path)
                logger.debug(f"Saved entity vector store for {entity_id} at {entity_path}")

            logger.info(f"Successfully saved {len(self.entity_vector_stores)} entity vector stores")

        except Exception as e:
            logger.error(f"Failed to save vector stores: {e}")

    def load_vector_stores(self, load_path: Optional[str]=None):
        """Load all entity-specific vector stores and global vector store"""
        if load_path is None:
            load_path = self.vector_store_path

        logger.info(f"Attempting to load vector stores from: {load_path}")

        if not os.path.exists(load_path):
            logger.warning(f"Vector stores path does not exist: {load_path}")
            return

        try:
            # Load global vector store
            global_path = os.path.join(load_path, "global")
            if os.path.exists(global_path) and self.embeddings:
                self.global_vector_store = FAISS.load_local(
                    global_path,
                    self.embeddings,
                    allow_dangerous_deserialization=True
                )
                logger.info(f"Successfully loaded global vector store from {global_path}")
                # Rebuild global indices for backward compatibility
                self._rebuild_indices_from_vector_store()

            # Load entity-specific vector stores
            entities_path = os.path.join(load_path, "entities")
            if os.path.exists(entities_path):
                for entity_dir in os.listdir(entities_path):
                    entity_path = os.path.join(entities_path, entity_dir)
                    if os.path.isdir(entity_path):
                        entity_id = entity_dir.replace("_", "/")
                        try:
                            if self.embeddings:
                                self.entity_vector_stores[entity_id] = FAISS.load_local(
                                    entity_path,
                                    self.embeddings,
                                    allow_dangerous_deserialization=True
                                )
                                logger.debug(f"Loaded entity vector store for {entity_id}")
                        except Exception as e:
                            logger.error(f"Failed to load entity vector store for {entity_id}: {e}")

                logger.info(f"Successfully loaded {len(self.entity_vector_stores)} entity vector stores")

            # Rebuild entity-specific indices
            self._rebuild_entity_indices()

        except Exception as e:
            logger.error(f"Failed to load vector stores: {e}")

    def _rebuild_entity_indices(self):
        """Rebuild entity-specific indices from loaded vector stores"""
        logger.info("Rebuilding entity-specific indices from vector stores")

        # Clear existing entity indices
        self.entity_chunk_metadata.clear()
        self.entity_doc_mapping.clear()

        for entity_id, vector_store in self.entity_vector_stores.items():
            try:
                self.entity_chunk_metadata[entity_id] = {}
                self.entity_doc_mapping[entity_id] = {}

                # Access the docstore to get all documents
                docstore = vector_store.docstore
                index_to_docstore_id = vector_store.index_to_docstore_id

                for i in range(len(index_to_docstore_id)):
                    docstore_id = index_to_docstore_id[i]
                    doc = docstore.search(docstore_id)

                    if doc and hasattr(doc, 'metadata') and isinstance(getattr(doc, 'metadata', None), dict):
                        doc_metadata = doc.metadata
                        self.entity_chunk_metadata[entity_id][i] = doc_metadata

                        # Handle nested metadata structure
                        if 'metadata' in doc_metadata and isinstance(doc_metadata['metadata'], dict):
                            working_metadata = doc_metadata['metadata']
                        else:
                            working_metadata = doc_metadata

                        # Update doc mapping for this entity
                        doc_id = working_metadata.get('doc_id')
                        if doc_id:
                            if doc_id not in self.entity_doc_mapping[entity_id]:
                                self.entity_doc_mapping[entity_id][doc_id] = set()
                            self.entity_doc_mapping[entity_id][doc_id].add(i)

                logger.debug(f"Rebuilt indices for entity {entity_id} with {len(self.entity_chunk_metadata[entity_id])} chunks")

            except Exception as e:
                logger.error(f"Failed to rebuild indices for entity {entity_id}: {e}")

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
