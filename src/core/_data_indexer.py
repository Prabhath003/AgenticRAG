# ============================================================================
# RAG Builder
# Handles chunk storage, retrieval, and relationship building using ChromaDB
# ============================================================================

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import os
import threading
from concurrent.futures import as_completed, Future
from contextvars import copy_context
import tiktoken

from ..infrastructure.database import get_db_session
from ..config import Config
from ..infrastructure.storage import S3Service, get_chromadb_store
from ..infrastructure.ids import generate_chunk_id
from ..infrastructure.dynamic_thread_pool import chunk_executor
from ..infrastructure.clients import FileProcessorClient
from .models.operation_audit import TaskStatus, ServiceType
from ..infrastructure.operation_logging import (
    get_operation_user_id,
    log_service,
    update_operation_status,
    mark_operation_complete,
    mark_operation_failed,
    update_operation_metadata,
)
from ..log_creator import get_file_logger
from .models.core_models import (
    Chunk,
    Document,
    KnowledgeBase,
    Content,
)

logger = get_file_logger()


class RAGIndexManager:
    """Handles RAG pipeline: chunk storage, retrieval, and relationship building using ChromaDB."""

    def __init__(self, db_lock: threading.RLock, docs_dir: str):
        """Initialize the RAG builder.

        Args:
            db_lock: Thread lock for database operations
            docs_dir: Directory where documents are stored
        """
        self._db_lock = db_lock
        self.docs_dir = docs_dir
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        self.chroma_store = get_chromadb_store()

    def _update_kb_status(
        self, kb_id: str, status: TaskStatus, processing_started_at: datetime
    ) -> None:
        """Update KB status to PROCESSING."""
        with self._db_lock:
            with get_db_session() as db:
                db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                    {"_id": kb_id, "user_id": get_operation_user_id()},
                    {
                        "$set": {
                            "status": status.value,
                            "processing_start_at": processing_started_at,
                        }
                    },
                )

    def _finalize_index_build(
        self, kb_id: str, chunks: List[Chunk], processing_completed_at: datetime
    ) -> None:
        """Finalize index build: update KB status and mark operation."""
        kb_status = TaskStatus.COMPLETED if chunks else TaskStatus.FAILED
        should_update_index_build_at = bool(chunks)

        with self._db_lock:
            with get_db_session() as db:
                update_data: Dict[str, Any] = {
                    "$set": {
                        "processing_completed_at": processing_completed_at,
                        "status": kb_status.value,
                        "error": None,
                    },
                }

                if should_update_index_build_at:
                    update_data["$set"]["index_build_at"] = processing_completed_at
                    # Extract doc_ids from chunks that were actually indexed
                    indexed_doc_ids = list(set(chunk.doc_id for chunk in chunks if chunk.doc_id))
                    # Add indexed docs to both doc_ids and index_build_on_doc_ids (without losing newly added ones)
                    update_data["$addToSet"] = {
                        "doc_ids": {"$each": indexed_doc_ids},
                        "index_build_on_doc_ids": {"$each": indexed_doc_ids},
                    }
                    logger.info(
                        f"Indexed {len(indexed_doc_ids)} documents for KB {kb_id}: {indexed_doc_ids}"
                    )

                db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                    {"_id": kb_id, "user_id": get_operation_user_id()}, update_data
                )

        if chunks:
            logger.info(f"KB {kb_id} index build complete")
            mark_operation_complete()
        else:
            logger.warning(f"KB {kb_id} index build failed. No chunks available.")
            mark_operation_failed("No chunks available.")

    def build_index(self, kb_id: str) -> None:
        """
        Build index for a knowledge base:
        1. Chunk documents if not already chunked
        2. Store chunks in ChromaDB with user-scoped collection
        4. Update KB status

        Args:
            kb_id: Knowledge base ID
        """
        processing_started_at = datetime.now(timezone.utc)
        update_operation_status(TaskStatus.PROCESSING)

        try:
            logger.info(f"Starting RAG processing for KB: {kb_id}")

            # Update KB status to PROCESSING
            self._update_kb_status(kb_id, TaskStatus.PROCESSING, processing_started_at)
            logger.info(f"KB {kb_id} status updated to PROCESSING")

            # Step 1: Get knowledge base and document information
            with self._db_lock:
                with get_db_session() as db:
                    kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                        {
                            "_id": kb_id,
                            "user_id": get_operation_user_id(),
                        }
                    )
                    if not kb_entry:
                        raise ValueError(f"Knowledge base {kb_id} not found")

                    kb_model = KnowledgeBase(**kb_entry)

                    if not kb_model.doc_ids:
                        logger.warning(f"No documents in KB {kb_id}")
                        return

                    docs = list(
                        db[Config.DOCUMENTS_COLLECTION].find(
                            {
                                "_id": {"$in": kb_model.doc_ids},
                                "user_id": get_operation_user_id(),
                            },
                        )
                    )
                    doc_models = [Document(**doc) for doc in docs]

            logger.info(f"Found {len(doc_models)} documents to process for KB {kb_id}")

            # Step 2: Identify documents that need chunking
            docs_to_chunk = [d for d in doc_models if not d.chunked]
            docs_by_source: Dict[str, List[Document]] = {}

            if docs_to_chunk:
                logger.info(f"Found {len(docs_to_chunk)} documents that need chunking")

                # Group documents by source
                for doc in docs_to_chunk:
                    source = doc.source or "default"
                    if source not in docs_by_source:
                        docs_by_source[source] = []
                    docs_by_source[source].append(doc)

                with self._db_lock:
                    with get_db_session() as db:
                        content_ids = [d.content_id for d in docs_to_chunk]
                        contents = list(
                            db[Config.DOCUMENT_CONTENTS_COLLECTION].find(
                                {
                                    "_id": {"$in": content_ids},
                                    "user_id": get_operation_user_id(),
                                },
                            )
                        )
                        content_models = [Content(**con) for con in contents]
                content_map = {c.content_id: c.content_path for c in content_models}

                # Process each source group in parallel
                chunk_futures: List[Future[float]] = []
                context = copy_context()
                for source, source_docs in docs_by_source.items():
                    logger.info(
                        f"Queuing chunk processing for {len(source_docs)} documents from source: {source}"
                    )
                    future = chunk_executor.submit(
                        context.run,
                        self._chunk_documents_by_source,
                        source,
                        source_docs,
                        content_map,
                        kb_id,
                    )
                    chunk_futures.append(future)

                # Wait for all chunk processing to complete
                total_chunking_cost: float = 0.0
                for future in as_completed(chunk_futures):
                    try:
                        source_cost = future.result()
                        total_chunking_cost += source_cost
                    except Exception as e:
                        logger.error(f"Error in chunk processing: {str(e)}")

                # Update KB with total chunking cost
                if total_chunking_cost > 0:
                    with self._db_lock:
                        with get_db_session() as db:
                            db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                                {"_id": kb_id, "user_id": get_operation_user_id()},
                                {"$inc": {"estimated_cost_usd": total_chunking_cost}},
                            )
                    logger.info(f"Added ${total_chunking_cost:.6f} chunking cost to KB {kb_id}")

            # Step 3: Get all chunks from ChromaDB
            all_chunks = self.get_all_chunks_for_kb(kb_id)
            logger.info(f"Total chunks retrieved: {len(all_chunks)}")

            # Step 4: Finalize index build
            processing_completed_at = datetime.now(timezone.utc)
            self._finalize_index_build(kb_id, all_chunks, processing_completed_at)

        except Exception as e:
            logger.error(f"Critical error processing KB {kb_id}: {str(e)}", exc_info=True)
            try:
                error_time = datetime.now(timezone.utc).isoformat()
                with self._db_lock:
                    with get_db_session() as db:
                        db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                            {"_id": kb_id, "user_id": get_operation_user_id()},
                            {
                                "$set": {
                                    "status": TaskStatus.FAILED.value,
                                    "processing_completed_at": error_time,
                                    "error": str(e),
                                }
                            },
                        )
            except Exception as db_error:
                logger.error(f"Failed to update KB status on error: {str(db_error)}")

            mark_operation_failed(str(e))
            raise  # Re-raise to propagate error to caller (CRITICAL: was missing!)

    def build_index_from_chunks(self, kb_id: str, chunks: List[Chunk]) -> None:
        """
        Build index from pre-chunked content.
        Skips document chunking and directly stores chunks and builds knowledge base.

        Args:
            kb_id: Knowledge base ID
            chunks: List of Chunk objects to index
        """
        if not chunks:
            logger.warning(f"No chunks provided for KB {kb_id}")
            return

        processing_started_at = datetime.now(timezone.utc)
        update_operation_status(TaskStatus.PROCESSING)

        added_chunk_ids: List[str] = []
        indexed_doc_ids: List[str] = []
        try:
            logger.info(
                f"Starting index build from {len(chunks)} pre-chunked chunks for KB: {kb_id}"
            )

            # Update KB status to PROCESSING
            self._update_kb_status(kb_id, TaskStatus.PROCESSING, processing_started_at)

            # Extract doc_ids from chunks for tracking and potential rollback
            indexed_doc_ids = list(set(chunk.doc_id for chunk in chunks if chunk.doc_id))

            # Step 1: Store chunks in ChromaDB
            user_id = get_operation_user_id()
            collection_name = f"chunks_{user_id}"
            # Add kb_id and user_id to each chunk's metadata for filtering/querying
            for chunk in chunks:
                chunk.metadata["kb_id"] = kb_id
                chunk.metadata["user_id"] = user_id
            added_chunk_ids = self.chroma_store.add_chunks(collection_name, chunks)
            logger.info(f"Stored {len(chunks)} chunks in ChromaDB for KB {kb_id}")

            # Step 2: Finalize index build
            processing_completed_at = datetime.now(timezone.utc)
            self._finalize_index_build(kb_id, chunks, processing_completed_at)

        except Exception as e:
            logger.error(f"Critical error building index for KB {kb_id}: {str(e)}", exc_info=True)

            # Rollback: Delete chunks from ChromaDB if they were added
            if added_chunk_ids:
                try:
                    user_id = get_operation_user_id()
                    collection_name = f"chunks_{user_id}"
                    self.chroma_store.delete_chunks(collection_name, added_chunk_ids)
                    logger.info(f"Rolled back {len(added_chunk_ids)} chunks from ChromaDB")
                except Exception as rollback_error:
                    logger.error(
                        f"Failed to rollback chunks from ChromaDB: {str(rollback_error)}",
                        exc_info=True,
                    )

            # Rollback: Remove doc_ids and index_build_on_doc_ids that may have been added
            if indexed_doc_ids:
                try:
                    with self._db_lock:
                        with get_db_session() as db:
                            db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                                {"_id": kb_id, "user_id": get_operation_user_id()},
                                {
                                    "$pull": {
                                        "doc_ids": {"$in": indexed_doc_ids},
                                        "index_build_on_doc_ids": {"$in": indexed_doc_ids},
                                    }
                                },
                            )
                    logger.info(f"Rolled back doc_ids and index_build_on_doc_ids for KB {kb_id}")
                except Exception as rollback_error:
                    logger.error(
                        f"Failed to rollback KB doc_ids: {str(rollback_error)}", exc_info=True
                    )
            try:
                error_time = datetime.now(timezone.utc).isoformat()
                with self._db_lock:
                    with get_db_session() as db:
                        db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                            {"_id": kb_id, "user_id": get_operation_user_id()},
                            {
                                "$set": {
                                    "status": TaskStatus.FAILED.value,
                                    "processing_completed_at": error_time,
                                    "error": str(e),
                                }
                            },
                        )
            except Exception as db_error:
                logger.error(f"Failed to update KB status on error: {str(db_error)}")

            mark_operation_failed(str(e))
            raise  # Re-raise to propagate error to caller (CRITICAL: was missing!)

    def _chunk_documents_by_source(
        self, source: str, source_docs: List[Document], content_map: Dict[str, str], kb_id: str
    ) -> float:
        """Chunk documents and store in ChromaDB. Returns estimated cost in USD."""
        file_contents: List[Dict[str, Any]] = []
        doc_id_map: Dict[str, str] = {}  # Maps filename -> doc_id
        filename_to_content_path_map: Dict[str, str] = (
            {}
        )  # Maps filename -> content_path (for storage rollback)
        total_cost: float = 0.0
        content_ids: List[str] = []
        doc_ids: List[str] = []
        all_chunk_ids: List[str] = []  # Track all chunks for rollback

        for doc in source_docs:
            try:
                content_path = content_map.get(doc.content_id)
                content_ids.append(doc.content_id)
                if not content_path:
                    logger.error(f"Content path not found for doc {doc.doc_id}")
                    continue

                # Read content from local disk or S3
                content = None
                full_path = os.path.join(self.docs_dir, content_path)

                if os.path.exists(full_path):
                    try:
                        with open(full_path, "rb") as f:
                            content = f.read()
                        logger.debug(f"Loaded document content from disk for doc {doc.doc_id}")
                    except Exception as e:
                        logger.warning(f"Failed to read local file for doc {doc.doc_id}: {str(e)}")
                else:
                    logger.info(f"Local file not found at {full_path}, attempting S3")
                    try:
                        s3_key = f"documents/{content_path}"
                        with S3Service() as s3_service:
                            content = s3_service.download_file(s3_key)
                        if content:
                            logger.info(f"Downloaded document from S3 for doc {doc.doc_id}")
                    except Exception as e:
                        logger.error(f"Failed to fetch from S3 for doc {doc.doc_id}: {str(e)}")

                if not content:
                    logger.error(f"Could not retrieve content for doc {doc.doc_id}")
                    continue

                file_contents.append(
                    {
                        "content": content,
                        "filename": doc.doc_name,
                        "source": source,
                        "mime_type": None,
                    }
                )
                doc_id_map[doc.doc_name] = doc.doc_id
                filename_to_content_path_map[doc.doc_name] = (
                    content_path  # Track storage path for rollback
                )
                doc_ids.append(doc.doc_id)
            except Exception as e:
                logger.error(f"Failed to read content for doc {doc.doc_id}: {str(e)}")

        update_operation_metadata(
            {
                "$addToSet": {"doc_ids": doc_ids, "content_ids": content_ids},
                "$inc": {"docs_count": len(doc_ids)},
            }
        )

        if file_contents:
            file_processor = FileProcessorClient()
            try:
                chunk_result = file_processor.batch_chunk_bytes(
                    file_contents, wait=True, poll_interval=5.0
                )
            except Exception as fp_error:
                logger.error(f"FileProcessor service failed: {str(fp_error)}", exc_info=True)
                # Rollback all uploaded files since FileProcessor failed
                for content_path in [
                    c.get("content_path") for c in file_contents if c.get("content_path")
                ]:
                    try:
                        full_path = os.path.join(self.docs_dir, str(content_path))
                        if os.path.exists(full_path):
                            os.remove(full_path)
                            logger.info(f"Rolled back file: {full_path}")
                    except Exception as cleanup_error:
                        logger.error(f"Failed to cleanup file {content_path}: {str(cleanup_error)}")

                # Rollback document records
                with self._db_lock:
                    with get_db_session() as db:
                        for doc_id in doc_ids:
                            db[Config.DOCUMENTS_COLLECTION].delete_one(
                                {"_id": doc_id, "user_id": get_operation_user_id()}
                            )
                            db[Config.DOCUMENT_CONTENTS_COLLECTION].delete_many(
                                {"doc_id": doc_id, "user_id": get_operation_user_id()}
                            )
                            logger.info(f"Rolled back document record for {doc_id}")

                raise

            files_info = chunk_result.get("files", [])
            results = chunk_result.get("results", {})
            metrics = chunk_result.get("metrics", {})
            file_processor_task_id = chunk_result.get("task_id")

            for file_info in files_info:
                filename = file_info.get("filename")
                mapped_doc_id: Optional[str] = doc_id_map.get(filename)
                file_hash = file_info.get("file_hash")
                chunk_ids: List[str] = []

                if not mapped_doc_id or not file_hash:
                    logger.error(f"Missing doc_id or file_hash for {filename} - Rolling back")
                    # Rollback stored file from storage
                    if filename in filename_to_content_path_map:
                        content_path = filename_to_content_path_map[filename]
                        try:
                            full_path = os.path.join(self.docs_dir, content_path)
                            if os.path.exists(full_path):
                                os.remove(full_path)
                                logger.info(f"Rolled back file from storage: {full_path}")
                        except Exception as cleanup_error:
                            logger.error(
                                f"Failed to cleanup file from storage: {str(cleanup_error)}"
                            )

                    # Rollback document record, content record, and remove from KB
                    if mapped_doc_id:
                        with self._db_lock:
                            with get_db_session() as db:
                                db[Config.DOCUMENTS_COLLECTION].delete_one(
                                    {"_id": mapped_doc_id, "user_id": get_operation_user_id()}
                                )
                                db[Config.DOCUMENT_CONTENTS_COLLECTION].delete_many(
                                    {"doc_id": mapped_doc_id, "user_id": get_operation_user_id()}
                                )
                                db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                                    {"_id": kb_id, "user_id": get_operation_user_id()},
                                    {"$pull": {"doc_ids": mapped_doc_id}},
                                )
                                logger.info(
                                    f"Rolled back document {mapped_doc_id} and removed from KB {kb_id} due to missing metadata"
                                )
                    continue

                file_status = file_info.get("status")

                # ================================================================
                # Check file processing status - CRITICAL!
                # ================================================================
                if file_status != TaskStatus.COMPLETED.value:
                    logger.error(
                        f"FileProcessor failed for {filename}: status={file_status}, "
                        f"doc_id={mapped_doc_id}"
                    )
                    # Rollback stored file from storage
                    if filename in filename_to_content_path_map:
                        content_path = filename_to_content_path_map[filename]
                        try:
                            full_path = os.path.join(self.docs_dir, content_path)
                            if os.path.exists(full_path):
                                os.remove(full_path)
                                logger.info(
                                    f"Rolled back file from storage due to processing failure: {full_path}"
                                )
                        except Exception as cleanup_error:
                            logger.error(
                                f"Failed to cleanup file from storage: {str(cleanup_error)}"
                            )

                    # Rollback document record, content record, and remove from KB
                    with self._db_lock:
                        with get_db_session() as db:
                            db[Config.DOCUMENTS_COLLECTION].delete_one(
                                {"_id": mapped_doc_id, "user_id": get_operation_user_id()}
                            )
                            db[Config.DOCUMENT_CONTENTS_COLLECTION].delete_many(
                                {"doc_id": mapped_doc_id, "user_id": get_operation_user_id()}
                            )
                            db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                                {"_id": kb_id, "user_id": get_operation_user_id()},
                                {"$pull": {"doc_ids": mapped_doc_id}},
                            )
                            logger.info(
                                f"Rolled back document {mapped_doc_id} and removed from KB {kb_id} due to processing failure"
                            )
                    continue

                if file_hash not in results:
                    logger.error(
                        f"No results returned for {filename} (file_hash={file_hash}) - "
                        f"FileProcessor may have failed silently, doc_id={mapped_doc_id}"
                    )
                    # Rollback stored file from storage
                    if filename in filename_to_content_path_map:
                        content_path = filename_to_content_path_map[filename]
                        try:
                            full_path = os.path.join(self.docs_dir, content_path)
                            if os.path.exists(full_path):
                                os.remove(full_path)
                                logger.info(
                                    f"Rolled back file from storage due to missing results: {full_path}"
                                )
                        except Exception as cleanup_error:
                            logger.error(
                                f"Failed to cleanup file from storage: {str(cleanup_error)}"
                            )

                    # Rollback document record, content record, and remove from KB
                    with self._db_lock:
                        with get_db_session() as db:
                            db[Config.DOCUMENTS_COLLECTION].delete_one(
                                {"_id": mapped_doc_id, "user_id": get_operation_user_id()}
                            )
                            db[Config.DOCUMENT_CONTENTS_COLLECTION].delete_many(
                                {"doc_id": mapped_doc_id, "user_id": get_operation_user_id()}
                            )
                            db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                                {"_id": kb_id, "user_id": get_operation_user_id()},
                                {"$pull": {"doc_ids": mapped_doc_id}},
                            )
                            logger.info(
                                f"Rolled back document {mapped_doc_id} and removed from KB {kb_id} due to missing results"
                            )
                    continue

                chunks = results.get(file_hash, [])

                # ================================================================
                # CRITICAL: Check if FileProcessor returned 0 chunks
                # ================================================================
                if not chunks:
                    logger.error(
                        f"FileProcessor returned 0 chunks for {filename} (doc_id={mapped_doc_id}, "
                        f"file_hash={file_hash}). This indicates: "
                        f"1) File is empty/corrupted, 2) FileProcessor service error, "
                        f"or 3) Unsupported file format. "
                        f"Estimated cost: ${file_info.get('estimated_cost_usd', 0)}"
                    )
                    # Rollback stored file from storage
                    if filename in filename_to_content_path_map:
                        content_path = filename_to_content_path_map[filename]
                        try:
                            full_path = os.path.join(self.docs_dir, content_path)
                            if os.path.exists(full_path):
                                os.remove(full_path)
                                logger.info(
                                    f"Rolled back file from storage due to 0 chunks: {full_path}"
                                )
                        except Exception as cleanup_error:
                            logger.error(
                                f"Failed to cleanup file from storage: {str(cleanup_error)}"
                            )

                    # Rollback document record, content record, and remove from KB
                    with self._db_lock:
                        with get_db_session() as db:
                            # Delete document record
                            db[Config.DOCUMENTS_COLLECTION].delete_one(
                                {"_id": mapped_doc_id, "user_id": get_operation_user_id()}
                            )
                            # Delete content record
                            db[Config.DOCUMENT_CONTENTS_COLLECTION].delete_many(
                                {"doc_id": mapped_doc_id, "user_id": get_operation_user_id()}
                            )
                            # Remove doc_id from KB's doc_ids list
                            db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                                {"_id": kb_id, "user_id": get_operation_user_id()},
                                {"$pull": {"doc_ids": mapped_doc_id}},
                            )
                            logger.info(
                                f"Rolled back document {mapped_doc_id} and removed from KB {kb_id} due to 0 chunks"
                            )
                    continue

                logger.info(f"Processing {len(chunks)} chunks for doc {mapped_doc_id}")

                # Create Chunk models
                chunk_models: List[Chunk] = []
                for chunk in chunks:
                    chunk_content_obj = chunk.get("content", {})
                    text_content = chunk_content_obj.get("text", "")

                    chunk_id = generate_chunk_id(
                        mapped_doc_id,
                        text_content,
                        chunk_content_obj.get("chunk_order_index"),
                    )
                    chunk_ids.append(chunk_id)

                    chunk_model = Chunk(
                        _id=chunk_id,
                        doc_id=mapped_doc_id,
                        content=chunk_content_obj,
                        metadata=chunk.get("metadata", {}),
                    )
                    chunk_models.append(chunk_model)

                # Mark document as chunked
                with self._db_lock:
                    with get_db_session() as db:
                        doc_cost = file_info.get("estimated_cost_usd", 0.0)
                        db[Config.DOCUMENTS_COLLECTION].update_one(
                            {"_id": mapped_doc_id, "user_id": get_operation_user_id()},
                            {
                                "$set": {
                                    "chunked": True,
                                    "estimated_cost_usd": doc_cost,
                                }
                            },
                        )

                # Store chunks in ChromaDB
                user_id = get_operation_user_id()
                collection_name = f"chunks_{user_id}"
                # Add kb_id to each chunk's metadata for filtering/querying
                for chunk in chunk_models:
                    chunk.metadata["kb_id"] = kb_id
                try:
                    added_ids = self.chroma_store.add_chunks(collection_name, chunk_models)
                    all_chunk_ids.extend(added_ids)
                    logger.info(
                        f"Stored {len(chunk_models)} chunks in ChromaDB for doc {mapped_doc_id}"
                    )
                except Exception as chroma_error:
                    logger.error(
                        f"Failed to store chunks in ChromaDB for doc {mapped_doc_id}: {str(chroma_error)}",
                        exc_info=True,
                    )
                    # Rollback all chunks added so far
                    if all_chunk_ids:
                        try:
                            self.chroma_store.delete_chunks(collection_name, all_chunk_ids)
                            logger.info(f"Rolled back {len(all_chunk_ids)} chunks from ChromaDB")
                        except Exception as rollback_error:
                            logger.error(
                                f"Failed to rollback chunks: {str(rollback_error)}", exc_info=True
                            )
                    raise  # Re-raise to propagate the error

                total_cost += file_info.get("estimated_cost_usd", 0.0)

                # Log service usage
                if file_hash in metrics:
                    file_metrics = metrics[file_hash]
                    log_service(
                        ServiceType.FILE_PROCESSOR,
                        file_metrics.get("estimated_cost_usd", 0),
                        breakdown=file_metrics,
                        metadata={
                            "file_processor_task_id": file_processor_task_id,
                            "doc_ids": [mapped_doc_id],
                        },
                    )

                update_operation_metadata(
                    {
                        "$addToSet": {"chunk_ids": chunk_ids},
                        "$inc": {"new_chunks_count": len(chunk_ids)},
                    }
                )

        logger.info(f"Completed chunking for source '{source}' with cost: ${total_cost:.6f}")
        return total_cost

    def get_all_chunks_for_kb(self, kb_id: str) -> List[Chunk]:
        """Retrieve all chunks for a knowledge base from ChromaDB."""
        try:
            # Get all doc_ids for the KB
            with self._db_lock:
                with get_db_session() as db:
                    kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                        {
                            "_id": kb_id,
                            "user_id": get_operation_user_id(),
                        }
                    )
                    if not kb_entry:
                        return []

                    doc_ids = kb_entry.get("doc_ids", [])
                    if not doc_ids:
                        return []

            # Get chunks for each document in order
            if doc_ids:
                user_id = get_operation_user_id()
                collection_name = f"chunks_{user_id}"
                all_chunks: List[Chunk] = []

                for doc_id in doc_ids:
                    doc_chunks = self.chroma_store.get_document_chunks_in_order(
                        collection_name=collection_name, doc_id=doc_id
                    )
                    all_chunks.extend(doc_chunks)

                return all_chunks
            return []
        except Exception as e:
            logger.error(f"Failed to retrieve chunks for KB {kb_id}: {str(e)}")
            return []
