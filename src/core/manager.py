import os
import time
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, AsyncGenerator
import uuid
import asyncio

from ..config import Config
from ..log_creator import get_file_logger
from ..infrastructure.storage.json_storage import JSONStorage, get_storage
from ..infrastructure.dynamic_thread_pool import executor
from .entity_scoped_rag import index_document_entity_scoped, get_entity_rag_manager
from .models import File, TaskStatus, KnowledgeGraph, KnowledgeGraphNode, KnowledgeGraphRelationship
from .agents.research_agent import ResearchAgent
from .agents.custom_types import ResponseRequiredRequest

logger = get_file_logger()

# Session cleanup configuration
SESSION_CLEANUP_INTERVAL = 300  # Check every 5 minutes (300 seconds)
SESSION_INACTIVITY_TIMEOUT = 3600  # Offload sessions inactive for 1 hour (3600 seconds)

# Configuration for different operation types
CHAT_MAX_WORKERS = 20      # High throughput for user-facing chat
# SEARCH_MAX_WORKERS = 10    # Moderate for search operations

# Semaphores for chat and search operations
chat_semaphore = asyncio.Semaphore(CHAT_MAX_WORKERS)
# search_semaphore = asyncio.Semaphore(SEARCH_MAX_WORKERS)

class Manager:
    def __init__(self):
        self.data_dir = Config.DATA_DIR
        os.makedirs(self.data_dir, exist_ok=True)
        self.entities_dir = os.path.join(self.data_dir, "entities")
        os.makedirs(self.entities_dir, exist_ok=True)

        self.storage: JSONStorage = get_storage()

        self.chat_sessions: Dict[str, Dict[str, Any]] = {}
        self.session_cleanup_lock = threading.Lock()

        # Per-session locks to serialize conversation operations and prevent concurrent message interleaving
        # CONCURRENT REQUEST FIX: Ensures only one request processes per session at a time
        self.session_locks: Dict[str, threading.Lock] = {}

        # Entity creation lock to prevent concurrent entity creation race
        # THREAD SAFETY FIX: Serializes entity creation to prevent TOCTOU vulnerability
        self.entity_creation_lock = threading.Lock()

        self.cleanup_thread = None
        self.cleanup_shutdown = threading.Event()

        # Start session cleanup background task
        self._start_session_cleanup()

        logger.info("Initialized Manager with session cleanup enabled")

    def _start_session_cleanup(self):
        """Start the background thread for session cleanup"""
        def cleanup_worker():
            logger.info(f"[Session Cleanup] Started background cleanup thread (interval: {SESSION_CLEANUP_INTERVAL}s, timeout: {SESSION_INACTIVITY_TIMEOUT}s)")
            while not self.cleanup_shutdown.is_set():
                try:
                    time.sleep(SESSION_CLEANUP_INTERVAL)
                    if not self.cleanup_shutdown.is_set():
                        self._cleanup_inactive_sessions()
                except Exception as e:
                    logger.error(f"[Session Cleanup] Error in cleanup loop: {e}")

        self.cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
        self.cleanup_thread.start()

    def _cleanup_inactive_sessions(self):
        """Offload sessions that have been inactive for more than 1 hour"""
        try:
            current_time = time.time()
            sessions_to_offload: List[tuple[str, float]] = []

            with self.session_cleanup_lock:
                for session_id, session_data in list(self.chat_sessions.items()):
                    # Get last accessed timestamp (stored as ISO string)
                    last_accessed_str = session_data.get("last_accessed")
                    if not last_accessed_str:
                        continue

                    try:
                        # Parse ISO format timestamp to datetime
                        last_accessed = datetime.fromisoformat(last_accessed_str.replace('Z', '+00:00'))
                        last_accessed_timestamp = last_accessed.timestamp()
                    except (ValueError, AttributeError):
                        continue

                    # Calculate inactivity duration
                    inactivity_duration = current_time - last_accessed_timestamp

                    # Mark session for offloading if inactive for more than 1 hour
                    if inactivity_duration > SESSION_INACTIVITY_TIMEOUT:
                        sessions_to_offload.append((session_id, inactivity_duration))

            # Offload inactive sessions
            for session_id, inactivity_duration in sessions_to_offload:
                self._offload_session(session_id, inactivity_duration)

        except Exception as e:
            logger.error(f"[Session Cleanup] Error during cleanup: {e}")

    def _offload_session(self, session_id: str, inactivity_duration: float):
        """Remove session from memory cache, keeping agent for quick reload"""
        try:
            with self.session_cleanup_lock:
                if session_id in self.chat_sessions:
                    logger.info(
                        f"[Session Cleanup] Offloaded session {session_id} (inactive for {inactivity_duration/3600:.1f}h) - "
                        f"Will reload from storage on next access"
                    )
                    # Remove the session from memory to free resources
                    # Agent will be recreated on next access if needed
                    del self.chat_sessions[session_id]
        except Exception as e:
            logger.error(f"[Session Cleanup] Error offloading session {session_id}: {e}")

    def shutdown_session_cleanup(self):
        """Shutdown the session cleanup thread gracefully"""
        if self.cleanup_shutdown and self.cleanup_thread:
            self.cleanup_shutdown.set()
            self.cleanup_thread.join(timeout=5)
            logger.info("[Session Cleanup] Cleanup thread stopped")

    def _get_session_lock(self, session_id: str) -> threading.Lock:
        """
        Get or create a per-session lock for serializing conversation operations.

        CONCURRENT REQUEST FIX: This ensures that only one request to the same session
        can be processed at a time, preventing out-of-order message interleaving in
        conversation history.

        Args:
            session_id: Session identifier

        Returns:
            threading.Lock for this session
        """
        with self.session_cleanup_lock:
            if session_id not in self.session_locks:
                self.session_locks[session_id] = threading.Lock()
            return self.session_locks[session_id]

    def _cleanup_session_lock(self, session_id: str):
        """Remove session lock when session is deleted to prevent memory leak"""
        with self.session_cleanup_lock:
            self.session_locks.pop(session_id, None)

    def create_entity(self, entity_id: str, entity_name: str, description: Optional[str]=None, metadata: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
        # THREAD SAFETY FIX: Serialize entity creation with lock to prevent concurrent creation race
        with self.entity_creation_lock:
            # Check existence while holding lock - prevents TOCTOU (Time-of-check-time-of-use) race
            if self.storage.find_one("entities", {"_id": entity_id}):
                raise ValueError(f"Entity with ID {entity_id} already exists")

            created_at = datetime.now(timezone.utc).isoformat()

            # Create entity directory with timestamp
            entity_dir = os.path.join(self.entities_dir, f"{entity_id}_{created_at}")
            os.makedirs(entity_dir, exist_ok=True)

            entity_data: Dict[str, Any] = {
                "_id": entity_id,
                "entity_name": entity_name,
                "entity_dir": entity_dir,
                "created_at": created_at,
                "documents_count": 0,
                "chunk_count": 0,
                "sessions_count": 0,
                "estimated_cost_usd": 0.0,
                "last_accessed": created_at,
            }
            if description:
                entity_data["description"] = description
            if metadata:
                entity_data["metadata"] = metadata

            self.storage.update_one(
                "entities",
                {"_id": entity_id},
                {"$set": entity_data},
                upsert=True
            )
            return {
                "entity_id": entity_id,
                "entity_name": entity_name,
                "created_at": created_at,
                "documents_count": 0,
                "chunk_count": 0,
                "estimated_cost_usd": 0.0,
            }

    def get_entity(self, entity_id: str, include_deleted: bool = False):
        """
        Get entity by ID.

        Args:
            entity_id: Entity identifier (with or without [DELETED] prefix)
            include_deleted: If True, include deleted entities (with [DELETED] prefix). Default: False

        Raises:
            ValueError: If entity not found
        """
        # Try to get the entity by ID directly
        entity = self.storage.find_one("entities", {"_id": entity_id})

        # If not found and ID doesn't have [DELETED] prefix, try to find deleted entity with regex
        # (deleted IDs have format [DELETED]entity_id_timestamp)
        if not entity and not entity_id.startswith("[DELETED]"):
            # Search for deleted entity using regex pattern
            deleted_entities = self.storage.find("entities", {
                "_id": {"$regex": f"^\\[DELETED\\]{entity_id}_"}
            })
            if deleted_entities:
                # Get the most recently deleted one (last one in the list)
                entity = deleted_entities[-1]

        # Check if deleted and handle based on include_deleted flag
        if entity and not include_deleted and entity.get("_id", "").startswith("[DELETED]"):
            entity = None

        if not entity:
            raise ValueError(f"Entity with ID {entity_id} does not exist")
        return entity
        
    def modify_entity(self, entity_id: str, metadata: Optional[Dict[str, Any]]):
        if not self.storage.find_one("entities", {"_id": entity_id}):
            raise ValueError(f"Entity with ID {entity_id} does not exist")

        updated_at = datetime.now(timezone.utc).isoformat()
            
        self.storage.update_one(
            "entities",
            {"_id": entity_id},
            {"$set": {
                "metadata": metadata,
                "updated_at": updated_at
            }},
            upsert=True
        )
    
    def delete_entity(self, entity_id: str) -> Dict[str, Any]:
        # THREAD SAFETY FIX: Serialize entity deletion to prevent concurrent delete races
        with self.entity_creation_lock:
            # Check entity exists while holding lock
            entity_data = self.get_entity(entity_id)
            sessions = self.list_entity_chat_sessions(entity_id)

            self.storage.delete_one(
                "entities",
                {"_id": entity_id}
            )
            deleted_at = datetime.now(timezone.utc).isoformat()

            # Create unique deleted ID with timestamp to allow ID reuse
            deleted_entity_id = f"[DELETED]{entity_id}_{deleted_at}"

            entity_dir = entity_data.get("entity_dir")
            new_entity_dir = os.path.join(self.entities_dir, f'[DELETED]{entity_id}_{deleted_at}')
            entity_data["_id"] = deleted_entity_id
            entity_data["deleted_at"] = deleted_at
            entity_data["entity_dir"] = new_entity_dir
            self.storage.update_one(
                "entities",
                {"_id": deleted_entity_id},
                {"$set": entity_data},
                upsert=True
            )

        # Delete sessions and rename directory OUTSIDE lock to avoid blocking other operations
        for session_entry in sessions:
            self.delete_chat_session(session_entry.get("session_id", ""))
        if entity_dir and new_entity_dir:
            try:
                os.rename(entity_dir, new_entity_dir)
            except OSError as e:
                logger.warning(f"Failed to rename entity directory {entity_dir} to {new_entity_dir}: {e}")

        return {
            "entity_id": entity_id,
            "deleted_at": deleted_at,
            "session_deleted": len(sessions),
            "note": "Entity data is retained with [DELETED] prefix for audit purposes"
        }
        
    def list_entities(self, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """
        List all entities.

        Args:
            include_deleted: If True, include deleted entities (with [DELETED] prefix). Default: False

        Returns:
            List of entity documents
        """
        if include_deleted:
            return self.storage.find("entities")
        else:
            # Fetch all entities and filter out deleted ones
            all_entities = self.storage.find("entities")
            return [entity for entity in all_entities if not entity.get("_id", "").startswith("[DELETED]")]
    
    def _process_file_upload(
        self,
        task_id: str,
        entity_id: str,
        file: File,
        description: Optional[str],
        source: Optional[str]
    ):
        """
        Background worker function to process file upload
        Runs in ThreadPoolExecutor
        """
        processing_started_at = datetime.now(timezone.utc).isoformat()
        try:
            # Update task status to processing
            self.storage.update_one(
                "tasks",
                {"_id": task_id},
                {"$set": {
                    "status": TaskStatus.PROCESSING.value,
                    "processing_started_at": processing_started_at
                }}
            )
            logger.info(f"Processing file upload for task {task_id}")

            # Index the file
            metadata: Dict[str, Optional[str]] = {
                "description": description,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "source": source
            }

            if source:
                metadata["source"] = source

            # RACE CONDITION FIX: Get entity data and validate it exists before processing upload
            # This prevents race with entity deletion
            entity_data = self.storage.find_one("entities", {"_id": entity_id})
            if not entity_data or entity_data.get("_id", "").startswith("[DELETED]"):
                raise ValueError(f"Entity {entity_id} was deleted during file upload processing")

            entity_dir = entity_data.get("entity_dir")
            if not entity_dir or not os.path.isdir(entity_dir):
                raise ValueError(f"Entity directory for {entity_id} does not exist or was removed")

            result = index_document_entity_scoped(
                entity_id=entity_id,
                file=file,
                metadata=metadata,
                entity_dir=entity_dir
            )
            completed_at = datetime.now(timezone.utc).isoformat()

            if not result:
                raise Exception("Failed to index document")
            
            if result.get("is_duplicate"):
                logger.info(f"Uploaded a duplicate file {file.filename} to entity {entity_id}, doc_id: {result['doc_id']}")

                # Update task with estimated cost
                self.storage.update_one(
                    "tasks",
                    {"_id": task_id},
                    {"$set": {
                        "status": TaskStatus.COMPLETED.value,
                        "estimated_cost_usd": 0.0,
                        "doc_id": result["doc_id"],
                        "completed_at": completed_at,
                        "is_duplicate": True
                    }}
                )
            else:

                # Extract estimated cost from result
                estimated_cost_usd = result.get("estimated_cost_usd", 0.0)
                chunks_count = result.get("chunks_count", 0)

                logger.info(f"Uploaded file {file.filename} to entity {entity_id}, doc_id: {result['doc_id']}, "
                        f"cost: ${estimated_cost_usd:.6f}, chunks: {chunks_count}, source: {source or 'default'}")

                # Update task with estimated cost
                self.storage.update_one(
                    "tasks",
                    {"_id": task_id},
                    {"$set": {
                        "status": TaskStatus.COMPLETED.value,
                        "estimated_cost_usd": round(estimated_cost_usd, 6),
                        "doc_id": result["doc_id"],
                        "chunks_count": chunks_count,
                        "completed_at": completed_at
                    }}
                )

                # Update entity's total cost and document count using atomic operations
                # Use $inc for cost to ensure atomic accumulation (prevents race conditions)
                self.storage.update_one(
                    "entities",
                    {"_id": entity_id},
                    {
                        "$set": {
                            "last_updated_at": completed_at
                        },
                        "$inc": {
                            "estimated_cost_usd": round(estimated_cost_usd, 6),
                            "documents_count": 1,
                            "chunk_count": chunks_count
                        }
                    }
                )

            logger.info(f"Completed file upload for task {task_id}")

        except Exception as e:
            completed_at = datetime.now(timezone.utc).isoformat()
            logger.error(f"Error processing file upload for task {task_id}: {e}")
            self.storage.update_one(
                "tasks",
                {"_id": task_id},
                {"$set": {
                    "status": TaskStatus.FAILED.value,
                    "error_message": str(e),
                    "completed_at": completed_at
                }}
            )
    
    def upload_file(self, entity_id: str, file: File, description: Optional[str]=None, source: Optional[str]=None) -> Dict[str, Any]:
        _ = self.get_entity(entity_id)
        uploaded_at = datetime.now(timezone.utc).isoformat()
        task_id = f"upload_{uuid.uuid4().hex[:12]}"
        
        self.storage.update_one(
            "tasks",
            {"_id": task_id},
            {"$set": {
                "_id": task_id,
                "task_type": "upload",
                "entity_id": entity_id,
                "uploaded_at": uploaded_at,
                "description": description,
                "source": source,
                "status": TaskStatus.PENDING.value
            }},
            upsert=True
        )
        
        executor.submit(
            self._process_file_upload,
            task_id,
            entity_id,
            file,
            description,
            source
        )
        
        return {
            "task_id": task_id,
            "entity_id": entity_id,
            "task_type": "upload",
            "filename": file.filename,
            "description": description,
            "source": source,
            "uploaded_at": uploaded_at,
            "status": TaskStatus.PENDING.value
        }
        
    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """
        Get task status and enrich with services_used from document metadata

        Args:
            task_id: Task identifier

        Returns:
            Task dict enriched with services_used from the associated document
        """
        task = self.storage.find_one("tasks", {"_id": task_id})
        if not task:
            raise ValueError(f"Task with ID {task_id} does not exist")

        # Enrich task with services_used from document metadata if available
        doc_id = task.get("doc_id")
        entity_id = task.get("entity_id")
        if task.get("task_type", "") == "upload" and task.get("status") == TaskStatus.COMPLETED.value and doc_id and entity_id:
            try:
                # Get entity directory
                entity_data = self.storage.find_one("entities", {"_id": entity_id})
                if entity_data:
                    entity_dir = entity_data.get("entity_dir")
                    if entity_dir:
                        entity_storage = JSONStorage(entity_dir)

                        # Fetch document metadata with services_used
                        doc = entity_storage.find_one("documents", {"_id": doc_id})
                        if doc:
                            task["services_used"] = doc.get("services_used", [])

            except Exception as e:
                logger.error(f"Failed to fetch services_used for task {task_id}: {e}")
                # Continue without services_used if fetching fails

        return task
    
    def list_files(self, entity_id: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """
        List all files for an entity.

        Args:
            entity_id: Entity identifier
            include_deleted: If True, include deleted files (with [DELETED] prefix). Default: False

        Returns:
            List of document records
        """
        entity_data = self.get_entity(entity_id)
        entity_storage = JSONStorage(entity_data['entity_dir'])
        documents = entity_storage.find("documents")

        if include_deleted:
            docs_result = documents
        else:
            # Filter out deleted documents
            docs_result = [doc for doc in documents if not doc.get("_id", "").startswith("[DELETED]")]

        # Ensure each document has doc_id field for API compatibility
        for doc in docs_result:
            if "doc_id" not in doc and "_id" in doc:
                doc["doc_id"] = doc["_id"]

        return docs_result
    
    # def delete_file(self, entity_id: str, doc_id: str) -> Dict[str, Any]:
    #     """
    #     Soft delete a file from an entity (preserves audit trail and removes from vector store)

    #     Args:
    #         entity_id: Entity ID
    #         doc_id: Document ID to delete

    #     Returns:
    #         Deletion response with metadata

    #     Raises:
    #         ValueError: If entity or document not found
    #     """
    #     # Get entity data
    #     entity_data = self.get_entity(entity_id)
    #     entity_storage = JSONStorage(entity_data['entity_dir'])

    #     # Find the document
    #     doc = entity_storage.find_one("documents", {"_id": doc_id})
    #     if not doc:
    #         raise ValueError(f"Document with ID {doc_id} not found in entity {entity_id}")

    #     deleted_at = datetime.now(timezone.utc).isoformat()

    #     # Get document details for response
    #     filename = doc.get("doc_name", doc.get("filename", "unknown"))
    #     chunks_count = doc.get("chunks_count", 0)

    #     # Delete from vector store (removes embeddings so document won't be retrieved in chat)
    #     try:
    #         rag_manager = get_entity_rag_manager()
    #         result = rag_manager.delete_document(entity_id, doc_id, entity_dir=entity_data.get("entity_dir"))
    #         logger.info(f"Removed document {doc_id} from vector store for entity {entity_id}")

    #         # Invalidate active sessions from cache for this entity
    #         # They will reload from storage with updated vector store on next get_chat_session() call
    #         invalidated_sessions = []
    #         for session_id, session_data in list(self.chat_sessions.items()):
    #             if session_data.get("entity_id") == entity_id:
    #                 del self.chat_sessions[session_id]
    #                 invalidated_sessions.append(session_id)

    #         if invalidated_sessions:
    #             logger.info(f"Invalidated {len(invalidated_sessions)} active session(s) for entity {entity_id}. "
    #                        f"Sessions will reload from storage with updated vector store on next access.")
    #     except Exception as e:
    #         logger.error(f"Failed to remove document {doc_id} from vector store: {e}")
    #         # Fail the deletion to maintain data consistency
    #         raise ValueError(
    #             f"Cannot delete document: failed to remove from vector store. "
    #             f"Vector store must be cleaned before document is deleted from database. "
    #             f"Please retry. Error: {e}"
    #         )

    #     # Delete from documents collection
    #     entity_storage.delete_one("documents", {"_id": doc_id})

    #     # Create a task entry for the deletion (for audit trail)
    #     deletion_task_id = f"doc_delete_{str(uuid.uuid4())[:13]}"
    #     deletion_task: Dict[str, Any] = {
    #         "_id": deletion_task_id,
    #         "task_type": "delete",
    #         "doc_id": doc_id,
    #         "entity_id": entity_id,
    #         "doc_name": filename,
    #         "created_at": deleted_at,
    #         "status": "completed",
    #         "estimated_cost_usd": result.get("estimated_cost_usd", 0)
    #     }

    #     # Store the deletion task in global storage
    #     self.storage.update_one(
    #         "tasks",
    #         {"_id": deletion_task_id},
    #         {"$set": deletion_task},
    #         upsert=True
    #     )

    #     # Store as archived/deleted document with [DELETED] prefix and timestamp
    #     deleted_doc = doc.copy()
    #     deleted_doc_id = f"[DELETED]{doc_id}_{deleted_at}"
    #     deleted_doc["_id"] = deleted_doc_id
    #     deleted_doc["doc_id"] = deleted_doc_id
    #     deleted_doc["deleted_at"] = deleted_at
    #     deleted_doc["deletion_task_id"] = deletion_task_id
    #     deleted_doc["deletion_services_used"] = [result.get("services_used", [])],
    #     deleted_doc["deletion_cost_usd"] = result.get("estimated_cost_usd", 0)
        
    #     entity_storage.update_one(
    #         "documents",
    #         {"_id": deleted_doc_id},
    #         {"$set": deleted_doc},
    #         upsert=True
    #     )

    #     # Update entity's document and chunk counts using atomic operations
    #     self.storage.update_one(
    #         "entities",
    #         {"_id": entity_id},
    #         {
    #             "$set": {
    #                 "last_modified": deleted_at
    #             },
    #             "$inc": {
    #                 "documents_count": -1,
    #                 "chunk_count": -chunks_count
    #                 ""
    #             }
    #         }
    #     )

    #     # Also update in entity's storage for consistency (best-effort, non-critical)
    #     # Entity-scoped storage update is a replica; global storage is the authoritative source
    #     try:
    #         entity_data_updated = entity_data.copy()
    #         entity_data_updated["last_modified"] = deleted_at

    #         entity_storage.update_one(
    #             "entities",
    #             {"_id": entity_id},
    #             {
    #                 "$set": entity_data_updated,
    #                 "$inc": {
    #                     "documents_count": -1,
    #                     "chunk_count": -chunks_count
    #                 }
    #             },
    #             upsert=True
    #         )
    #     except Exception as e:
    #         # Log error but don't fail - global storage is authoritative
    #         logger.warning(
    #             f"Failed to update entity counts in entity-scoped storage for {entity_id}: {e}. "
    #             f"Global storage updated successfully. Consistency may be temporarily inconsistent."
    #         )

    #     logger.info(f"Deleted document {doc_id} from entity {entity_id} (removed {chunks_count} chunks)")

    #     return {
    #         "success": True,
    #         "doc_id": doc_id,
    #         "entity_id": entity_id,
    #         "filename": filename,
    #         "deleted_at": deleted_at,
    #         "chunks_removed": chunks_count,
    #         "deletion_task_id": deletion_task_id,
    #         "services_used": [deletion_service.to_dict()],
    #         "estimated_cost_usd": deletion_service.estimated_cost_usd,
    #         "message": f"Document '{filename}' has been deleted. Data is retained with [DELETED] prefix for audit purposes"
    #     }
    
    def create_chat_session(self, entity_id: str, session_name: Optional[str]=None, metadata: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
        entity_data = self.get_entity(entity_id)

        # Generate session ID
        session_id = f"session_{uuid.uuid4().hex[:12]}"

        entity_storage = JSONStorage(entity_data['entity_dir'])

        # Create session data
        session_data: Dict[str, Any] = {
            "session_id": session_id,
            "entity_id": entity_id,
            "session_name": session_name,
            "entity_name": entity_data['entity_name'],
            "entity_dir": entity_data['entity_dir'],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_accessed": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
            "message_count": 0,
            "estimated_cost_usd": 0.0
        }

        self.storage.update_one(
            "sessions",
            {"_id": session_id},
            {"$set": session_data},
            upsert=True
        )

        entity_storage.update_one(
            "sessions",
            {"_id": session_id},
            {"$set": session_data},
            upsert=True
        )

        # Increment sessions_count in entity
        self.storage.update_one(
            "entities",
            {"_id": entity_id},
            {"$inc": {"sessions_count": 1}}
        )

        # Store agent in memory cache (not in response) - RACE CONDITION FIX: Acquire lock before accessing chat_sessions
        cache_entry = session_data.copy()
        cache_entry["agent"] = ResearchAgent(
            session_data["entity_id"],
            entity_data["entity_name"],
            entity_dir=entity_data.get("entity_dir")
        )
        cache_entry["conversation_history"] = []

        with self.session_cleanup_lock:
            self.chat_sessions[session_id] = cache_entry

        # Return only serializable data
        return session_data
    
    def get_chat_session(self, session_id: str, include_deleted: bool = False) -> Dict[str, Any]:
        """
        Get chat session by ID.

        Args:
            session_id: Session identifier
            include_deleted: If True, include deleted sessions (with [DELETED] prefix). Default: False

        Returns:
            Session data dict

        Raises:
            ValueError: If session not found
        """
        last_accessed = datetime.now(timezone.utc).isoformat()

        # RACE CONDITION FIX: Use lock to protect cache access and updates
        with self.session_cleanup_lock:
            session_entry = self.chat_sessions.get(session_id)

        entity_storage = None
        if not session_entry:
            session_entry = self.storage.find_one("sessions", {"_id": session_id})

            # If not found and ID doesn't have [DELETED] prefix, try to find deleted session with regex
            # (deleted IDs have format [DELETED]session_id_timestamp)
            if not session_entry and not session_id.startswith("[DELETED]"):
                deleted_sessions = self.storage.find("sessions", {
                    "_id": {"$regex": f"^\\[DELETED\\]{session_id}_"}
                })
                if deleted_sessions:
                    # Get the most recently deleted one (last one in the list)
                    session_entry = deleted_sessions[-1]

            if not session_entry:
                raise ValueError(f"Chat session with ID {session_id} does not exist")

            # Check if deleted and handle based on include_deleted flag
            if not include_deleted and session_entry.get("_id", "").startswith("[DELETED]"):
                raise ValueError(f"Chat session with ID {session_id} does not exist")

            # Use the actual session ID from storage (may have [DELETED] prefix)
            actual_session_id = session_entry.get("_id")
            entity_storage = JSONStorage(session_entry['entity_dir'])
            session_data = entity_storage.find_one("sessions", {"_id": actual_session_id})
            if not session_data:
                raise ValueError(f"Chat session with ID {session_id} does not exist")

            # Store in cache with agent (for internal use)
            cache_entry = session_data.copy()
            agent = ResearchAgent(
                session_data["entity_id"],
                session_data["entity_name"],
                entity_dir=session_entry.get("entity_dir")
            )
            agent.conversation_history = [{"role": message["role"],"content": message["content"]} for message in session_data.get("conversation_history", [])]
            cache_entry["agent"] = agent

            # RACE CONDITION FIX: Protect cache write
            with self.session_cleanup_lock:
                self.chat_sessions[session_id] = cache_entry

        if not session_entry:
            raise ValueError(f"Chat session with ID {session_id} does not exist")

        # Get the actual session ID from storage (may have [DELETED] prefix)
        actual_session_id = session_entry.get("_id")

        # RACE CONDITION FIX: Protect cache update
        with self.session_cleanup_lock:
            if session_id in self.chat_sessions:
                self.chat_sessions[session_id]["last_accessed"] = last_accessed

        self.storage.update_one(
            "sessions",
            {"_id": actual_session_id},
            {"$set": {
                "last_accessed": last_accessed
            }}
        )
        if not entity_storage:
            entity_storage = JSONStorage(session_entry['entity_dir'])
        entity_storage.update_one(
            "sessions",
            {"_id": actual_session_id},
            {"$set": {
                "last_accessed": last_accessed
            }}
        )
        # Return copy without agent for serialization
        response_data = session_entry.copy()
        response_data.pop("agent", None)
        return response_data
    
    def list_entity_chat_sessions(self, entity_id: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """
        List all chat sessions for an entity.

        Args:
            entity_id: Entity identifier
            include_deleted: If True, include deleted sessions (with [DELETED] prefix). Default: False

        Returns:
            List of session data dicts
        """
        _ = self.get_entity(entity_id)
        sessions = self.storage.find(
            "sessions",
            {"entity_id": entity_id}
        )
        if include_deleted:
            return [session for session in sessions]
        else:
            # Filter out deleted sessions
            return [session for session in sessions if not session.get("_id", "").startswith("[DELETED]")]
        
    def delete_chat_session(self, session_id: str):
        deleted_at = datetime.now(timezone.utc).isoformat()

        # RACE CONDITION FIX: Acquire lock before modifying chat_sessions cache
        with self.session_cleanup_lock:
            self.chat_sessions.pop(session_id, None)

        # CONCURRENT REQUEST FIX: Clean up per-session lock to prevent memory leak
        self._cleanup_session_lock(session_id)

        session_entry = self.storage.find_one("sessions", {"_id": session_id})
        if not session_entry:
            raise ValueError(f"Chat session with ID {session_id} does not exist")
        self.storage.delete_one("sessions", {"_id": session_id})

        # Create unique deleted ID with timestamp to allow ID reuse
        deleted_session_id = f"[DELETED]{session_id}_{deleted_at}"

        session_entry["_id"] = deleted_session_id
        session_entry["deleted_at"] = deleted_at
        self.storage.update_one(
            "sessions",
            {"_id": deleted_session_id},
            {"$set": session_entry},
            upsert=True
        )
        entity_storage = JSONStorage(session_entry['entity_dir'])
        session_data = entity_storage.find_one("sessions", {"_id": session_id})
        if not session_data:
            raise ValueError(f"Chat session with ID {session_id} does not exist")
        entity_storage.delete_one("sessions", {"_id": session_id})
        session_data["_id"] = deleted_session_id
        session_data["deleted_at"] = deleted_at
        entity_storage.update_one(
            "sessions",
            {"_id": deleted_session_id},
            {"$set": session_data},
            upsert=True
        )

        # Decrement sessions_count in entity
        self.storage.update_one(
            "entities",
            {"_id": session_entry.get("entity_id")},
            {"$inc": {"sessions_count": -1}}
        )

        return {
            "session_id": session_id,
            "deleted_at": deleted_at,
            "note": "Session data is retained with [DELETED] prefix for audit purposes"
        }
        
    def get_chat_session_conversations(self, session_id: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """
        Get conversation history for a chat session.

        Args:
            session_id: Session identifier
            include_deleted: If True, include deleted sessions (with [DELETED] prefix). Default: False

        Returns:
            List of conversation messages
        """
        self.get_chat_session(session_id, include_deleted=include_deleted)
        # RACE CONDITION FIX: Protect cache access with lock
        with self.session_cleanup_lock:
            if session_id in self.chat_sessions:
                return self.chat_sessions[session_id].get("conversation_history", [])
            else:
                raise ValueError(f"Chat session with ID {session_id} was offloaded or deleted")
      
    def chat_session_converse(
        self,
        session_id: str,
        user_message: str,
        stream: bool = False
    ):
        """
        Process a user message and generate a response using the session's agent.
        Handles both streaming and non-streaming responses.

        CONCURRENT REQUEST FIX: Uses per-session lock to serialize all conversation
        operations for this session. This prevents out-of-order message interleaving
        when multiple requests arrive concurrently.

        Args:
            session_id: Session identifier
            user_message: User's message
            stream: Whether to return streaming response

        Returns:
            Async generator for streaming, or dict with response for non-streaming
        """
        # CONCURRENT REQUEST FIX: Acquire per-session lock to serialize all operations
        session_lock = self._get_session_lock(session_id)

        with session_lock:  # Only one request to this session at a time
            session_entry = self.get_chat_session(session_id)
            task_id = f"chat_{str(uuid.uuid4())[:13]}"
            created_at = datetime.now(timezone.utc).isoformat()
            self.storage.update_one(
                "tasks",
                {"_id": task_id},
                {
                    "$set": {
                        "_id": task_id,
                        "created_at": created_at,
                        "estimated_cost_usd": 0.0,
                        "task_type": "chat",
                        "stream": stream,
                        "user_message": user_message,
                        "session_id": session_id,
                        "entity_id": session_entry.get("entity_id")
                    }
                },
                upsert=True
            )

            # RACE CONDITION FIX: Get agent and initialize history within lock, then prepare transcript
            agent: ResearchAgent
            transcript: List[Dict[str, str]]
            if session_id not in self.chat_sessions:
                raise ValueError(f"Chat session with ID {session_id} was offloaded or deleted during initialization")

            agent = self.chat_sessions[session_id]["agent"]

            # Initialize conversation history if not present
            if "conversation_history" not in self.chat_sessions[session_id]:
                self.chat_sessions[session_id]["conversation_history"] = []

            user_message_dict: Dict[str, Any] = {
                "role": "user",
                "content": user_message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id
            }

            self.chat_sessions[session_id]["conversation_history"].append(user_message_dict)

            # Convert to transcript format (convert role enum to string)
            transcript = [
                {
                    "role": msg["role"] if isinstance(msg["role"], str) else msg["role"].value,
                    "content": msg["content"]
                }
                for msg in self.chat_sessions[session_id]["conversation_history"]
            ]

        if stream:
            async def generate() -> AsyncGenerator[Dict[str, Any], None]:
                full_response = ""
                try:
                    async for response in agent.research_question(
                        ResponseRequiredRequest(
                            interaction_type="response_required",
                            response_id=len(transcript),
                            transcript=transcript
                        ),
                        None
                    ):
                        if response:
                            # Create streaming response object with metadata
                            if response.content and response.response_type == "response":
                                full_response += response.content

                            services_used = response.services_used
                            estimated_cost_usd = response.estimated_cost_usd

                            response_obj = response.model_dump()
                            response_obj["task_id"] = task_id
                            response_obj["session_id"] = session_id
                            yield response_obj

                            if response.end_call or response.content_complete:
                                # Save assistant message
                                completed_at = datetime.now(timezone.utc).isoformat()
                                assistant_message_dict: Dict[str, Any] = {
                                    "role": "assistant",
                                    "content": full_response,
                                    "node_ids": response.node_ids,
                                    "relationship_ids": response.relationship_ids,
                                    "cited_node_ids": response.cited_node_ids,
                                    "timestamp": completed_at,
                                    "transfer_number": response.transfer_number,
                                    "task_id": task_id,
                                    "estimated_cost_usd": estimated_cost_usd,
                                    "services_used": services_used
                                }

                                # CONCURRENT REQUEST FIX: Use per-session lock to serialize response save
                                with session_lock:
                                    if session_id in self.chat_sessions:
                                        self.chat_sessions[session_id]["conversation_history"].append(assistant_message_dict)
                                        # Update session last_accessed
                                        self.chat_sessions[session_id]["last_accessed"] = completed_at
                                        conversation_history = self.chat_sessions[session_id]["conversation_history"]
                                    else:
                                        logger.warning(f"Session {session_id} was offloaded during streaming - saving to storage only")
                                        conversation_history = None

                                self.storage.update_one(
                                    "sessions",
                                    {"_id": session_id},
                                    {"$set": {"last_accessed": completed_at}},
                                    upsert=True
                                )
                                self.storage.update_one(
                                    "tasks",
                                    {"_id": task_id},
                                    {"$set": {
                                        "completed_at": completed_at
                                    },
                                    "$inc": {
                                        "estimated_cost_usd": estimated_cost_usd
                                    }}
                                )
                                entity_storage = JSONStorage(session_entry['entity_dir'])
                                entity_storage.update_one(
                                    "sessions",
                                    {"_id": session_id},
                                    {"$set": {"conversation_history": conversation_history if conversation_history else [assistant_message_dict],
                                              "last_accessed": completed_at},
                                     "$inc": {"estimated_cost_usd": estimated_cost_usd}},
                                    upsert=True
                                )
                                self.storage.update_one(
                                    "entities",
                                    {"_id": session_entry.get("entity_id")},
                                    {
                                        "$inc": {
                                            "estimated_cost_usd": estimated_cost_usd
                                        }
                                    }
                                )
                except Exception as e:
                    logger.error(f"Error in stream generation for session {session_id}: {e}")
                    raise

            return generate()
        else:
            # Non-streaming response
            async def get_non_streaming_response() -> Dict[str, Any]:
                full_response = ""
                final_node_ids = []
                final_relationship_ids = []
                final_cited_node_ids = []
                transfer_number = None
                services_used = []
                estimated_cost_usd = 0.0

                async for response in agent.research_question(
                    ResponseRequiredRequest(
                        interaction_type="response_required",
                        response_id=len(transcript),
                        transcript=transcript
                    ),
                    None
                ):
                    if response:
                        final_node_ids = response.node_ids if hasattr(response, 'node_ids') else []
                        final_relationship_ids = response.relationship_ids if hasattr(response, 'relationship_ids') else []
                        final_cited_node_ids = response.cited_node_ids if hasattr(response, 'cited_node_ids') else []
                        transfer_number = response.transfer_number
                        services_used = response.services_used
                        estimated_cost_usd = response.estimated_cost_usd

                    if response.content and response.response_type == "response":
                        full_response += response.content

                completed_at = datetime.now(timezone.utc).isoformat()
                assistant_message_dict: Dict[str, Any] = {
                    "role": "assistant",
                    "content": full_response,
                    "node_ids": final_node_ids,
                    "relationship_ids": final_relationship_ids,
                    "cited_node_ids": final_cited_node_ids,
                    "timestamp": completed_at,
                    "transfer_number": transfer_number,
                    "task_id": task_id,
                    "estimated_cost_usd": estimated_cost_usd,
                    "services_used": services_used
                }

                # CONCURRENT REQUEST FIX: Use per-session lock to serialize response save
                session_cost = 0.0
                conversation_history = None
                with session_lock:
                    if session_id in self.chat_sessions:
                        self.chat_sessions[session_id]["conversation_history"].append(assistant_message_dict)
                        # Update session last_accessed
                        self.chat_sessions[session_id]["last_accessed"] = completed_at
                        self.chat_sessions[session_id]["estimated_cost_usd"] = estimated_cost_usd + self.chat_sessions[session_id].get("estimated_cost_usd", 0.0)
                        session_cost = self.chat_sessions[session_id]["estimated_cost_usd"]
                        conversation_history = self.chat_sessions[session_id]["conversation_history"]
                    else:
                        logger.warning(f"Session {session_id} was offloaded during non-streaming response - saving to storage only")
                        session_cost = estimated_cost_usd
                        conversation_history = [assistant_message_dict]

                self.storage.update_one(
                    "sessions",
                    {"_id": session_id},
                    {
                        "$set": {"last_accessed": completed_at},
                        "$inc": {"estimated_cost_usd": estimated_cost_usd}
                    },
                    upsert=True
                )
                self.storage.update_one(
                    "tasks",
                    {"_id": task_id},
                    {"$set": {
                        "completed_at": completed_at,
                        "estimated_cost_usd": estimated_cost_usd
                    }},
                    upsert=True
                )
                entity_storage = JSONStorage(session_entry['entity_dir'])
                entity_storage.update_one(
                    "sessions",
                    {"_id": session_id},
                    {"$set": {"conversation_history": conversation_history,
                                "last_accessed": completed_at,
                                "estimated_cost_usd": session_cost}},
                )
                self.storage.update_one(
                    "entities",
                    {"_id": session_entry.get("entity_id")},
                    {
                        "$inc": {
                            "estimated_cost_usd": estimated_cost_usd
                        }
                    }
                )

                return {
                    "session_id": session_id,
                    "task_id": task_id,
                    "content": full_response,
                    "node_ids": final_node_ids,
                    "relationship_ids": final_relationship_ids,
                    "cited_node_ids": final_cited_node_ids,
                    "estimated_cost_usd": estimated_cost_usd,
                    "services_used": services_used
                }

            return get_non_streaming_response()

    def ingest_chunks(self, entity_id: str, chunks_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Ingest multiple pre-chunked items with duplicate detection and proper indexing

        Args:
            entity_id: Entity identifier
            chunks_data: List of chunk data containing chunk_id, markdown, and metadata

        Returns:
            Response indicating success, counts, and message
        """
        try:
            # Validate entity exists
            entity_data = self.get_entity(entity_id)
            entity_dir = entity_data.get("entity_dir")
            entity_storage = JSONStorage(entity_dir)

            if not chunks_data:
                raise ValueError("At least one chunk is required")

            # Get doc_id from first chunk (all should have same doc_id)
            doc_id = chunks_data[0].get("metadata", {}).get("doc_id")
            if not doc_id:
                raise ValueError("metadata.doc_id is required for all chunks")

            # Verify all chunks belong to the same document
            for chunk_data in chunks_data:
                if chunk_data.get("metadata", {}).get("doc_id") != doc_id:
                    raise ValueError("All chunks must belong to the same document (same doc_id)")

            # Check for duplicate chunks and collect new ones
            new_chunks_data = []
            duplicate_count = 0

            for chunk_data in chunks_data:
                chunk_id = chunk_data.get("chunk_id")
                if not chunk_id:
                    raise ValueError("chunk_id is required for all chunks")

                # Check if chunk_id already exists
                existing_chunk = entity_storage.find_one("chunks", {"_id": chunk_id})
                if existing_chunk:
                    logger.info(f"Chunk {chunk_id} already exists, skipping")
                    duplicate_count += 1
                else:
                    new_chunks_data.append(chunk_data)

            # If all chunks are duplicates, return early
            if not new_chunks_data:
                logger.info(f"All {duplicate_count} chunks already exist for doc {doc_id}")
                return {
                    "success": True,
                    "entity_id": entity_id,
                    "doc_id": doc_id,
                    "total_chunks": len(chunks_data),
                    "indexed_chunks": 0,
                    "duplicate_chunks": duplicate_count,
                    "message": f"All {duplicate_count} chunks already exist, no indexing performed"
                }

            # Format chunks for vector store (same format as _process_document returns)
            formatted_chunks = []
            for chunk_data in new_chunks_data:
                markdown_content = chunk_data.get("content", {})
                chunk_metadata_obj = chunk_data.get("metadata", {})

                formatted_chunk = {
                    "chunk": {
                        "text": markdown_content.get("text", ""),
                        "chunk_order_index": markdown_content.get("chunk_order_index", 0)
                    },
                    "metadata": {
                        "chunk_id": chunk_data.get("chunk_id"),
                        "doc_id": doc_id,
                        "chunk_order_index": markdown_content.get("chunk_order_index", 0),
                        "source": markdown_content.get("source", entity_id),
                        "pages": markdown_content.get("pages", [markdown_content.get("page", [0])]),
                        "tokens": chunk_metadata_obj.get("tokens", 0),
                        "processed_by": chunk_metadata_obj.get("processed_by", "ChunkAPI"),
                        "indexed_at": datetime.now(timezone.utc).isoformat()
                    }
                }
                formatted_chunks.append(formatted_chunk)

            # Get the RAG manager and add chunks to vector store
            from .entity_scoped_rag import get_entity_rag_manager
            rag_manager = get_entity_rag_manager()

            # Add chunks using the standard batch method
            result = rag_manager.add_chunks_batch(
                entity_id=entity_id,
                chunks=formatted_chunks,
                doc_id=doc_id,
                entity_dir=entity_dir,
                new_chunks_data=new_chunks_data
            )

            if not result:
                raise ValueError("Failed to index chunks")

            indexed_count = len(new_chunks_data)
            logger.info(f"Successfully indexed {indexed_count} chunks for doc {doc_id} in entity {entity_id}")

            return {
                "success": True,
                "entity_id": entity_id,
                "doc_id": doc_id,
                "total_chunks": len(chunks_data),
                "indexed_chunks": indexed_count,
                "duplicate_chunks": duplicate_count,
                "message": f"Ingested {indexed_count} chunks ({duplicate_count} duplicates skipped) for doc {doc_id}"
            }

        except ValueError as ve:
            logger.error(f"Validation error in chunk batch ingestion: {ve}")
            raise
        except Exception as e:
            logger.error(f"Error ingesting chunks for entity {entity_id}: {e}")
            raise

    def ingest_chunk(self, entity_id: str, chunk_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ingest a single pre-chunked data with duplicate detection (wrapper for batch)

        Args:
            entity_id: Entity identifier
            chunk_data: Chunk data containing chunk_id, markdown, and metadata

        Returns:
            Response indicating success, whether indexed, and message
        """
        try:
            batch_result = self.ingest_chunks(entity_id, [chunk_data])

            # Convert batch result to single chunk response format
            return {
                "success": batch_result["success"],
                "chunk_id": chunk_data.get("chunk_id"),
                "entity_id": entity_id,
                "doc_id": batch_result["doc_id"],
                "indexed": batch_result["indexed_chunks"] > 0,
                "message": batch_result["message"]
            }

        except Exception as e:
            logger.error(f"Error ingesting chunk for entity {entity_id}: {e}")
            raise

    def get_knowledge_graph(self, entity_ids: List[str]) -> KnowledgeGraph:
        nodes: List[KnowledgeGraphNode] = []
        relationships: List[KnowledgeGraphRelationship] = []
        node_id_set: set[str] = set()
        relationship_id_set: set[str] = set()

        for entity_id in entity_ids:
            entity_data = self.get_entity(entity_id)

            entity_storage = JSONStorage(entity_data.get("entity_dir", ""))

            # Get all chunks for this entity
            chunks = entity_storage.find("chunks")

            # Group chunks by doc_id from metadata
            chunks_by_doc: dict[str, list] = {}
            for chunk_data in chunks:
                doc_id = chunk_data.get('metadata', {}).get('doc_id')
                if not doc_id:
                    continue

                if doc_id not in chunks_by_doc:
                    chunks_by_doc[doc_id] = []
                chunks_by_doc[doc_id].append(chunk_data)

            # Process each document's chunks
            for doc_id, doc_chunks in chunks_by_doc.items():
                # Sort chunks by chunk_order_index
                sorted_chunks = sorted(
                    doc_chunks,
                    key=lambda x: x.get('chunk', {}).get('chunk_order_index', 0)
                )

                previous_node_id = None
                for chunk_data in sorted_chunks:
                    chunk = chunk_data.get('chunk', {})
                    metadata_dict = chunk_data.get('metadata', {})

                    chunk_order_index = chunk.get('chunk_order_index')
                    content = chunk.get('text', '')
                    source = chunk.get('source', '')

                    if chunk_order_index is None:
                        continue

                    # Create node_id
                    node_id = f"{entity_id}_{doc_id}_{chunk_order_index}"

                    # Add node if not already added
                    if node_id not in node_id_set:
                        node_id_set.add(node_id)

                        nodes.append(KnowledgeGraphNode(
                            id=node_id,
                            nodeLabel=content[:10],
                            properties={
                                "entity_id": entity_id,
                                "doc_id": doc_id,
                                "chunk_order_index": chunk_order_index,
                                "content": content,
                                "source": source,
                                "metadata": metadata_dict
                            }
                        ))

                    # Create sequential relationship with previous chunk
                    if previous_node_id:
                        relationship_id = f"{previous_node_id}:{node_id}"
                        if relationship_id not in relationship_id_set:
                            relationship_id_set.add(relationship_id)
                            relationships.append(KnowledgeGraphRelationship(
                                id=relationship_id,
                                source=previous_node_id,
                                target=node_id,
                                label="sequential",
                                properties={
                                    "doc_id": doc_id,
                                    "entity_id": entity_id
                                }
                            ))

                    previous_node_id = node_id

        logger.info(f"Generated knowledge graph with {len(nodes)} nodes and {len(relationships)} relationships for entities: {entity_ids}")

        return KnowledgeGraph(
            nodes=nodes,
            relationships=relationships,
            total_nodes=len(nodes),
            total_relationships=len(relationships),
            entity_ids=entity_ids
        )
                    
        
        
        