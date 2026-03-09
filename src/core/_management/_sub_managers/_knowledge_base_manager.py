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
from typing import Optional, Dict, Any, List, Set, Union, cast
from datetime import datetime, timezone, timedelta
import os
import threading
import zipfile
import io
from concurrent.futures import as_completed, Future
from pymongo.errors import DuplicateKeyError
from contextvars import copy_context

from ....log_creator import get_file_logger
from ...models.core_models import (
    Doc,
    KnowledgeBase,
    Chunk,
    Document,
    Content,
)
from ....infrastructure.database import get_db_session
from ....config import Config
from ....infrastructure.ids import (
    generate_kb_id,
    generate_document_id,
    generate_content_id,
    sha256_hex,
)
from ....infrastructure.dynamic_thread_pool import executor
from ....infrastructure.operation_logging import (
    update_operation_metadata,
    update_operation_status,
)
from ...models.operation_audit import TaskStatus
from ..._data_indexer import RAGIndexManager
from ...models.response_models import (
    CreateKnowledgeBaseResponse,
    GetKnowledgeBaseResponse,
    ListKnowledgeBasesResponse,
    DeleteKnowledgeBaseResponse,
    ModifyKnowledgeBaseResponse,
    DeleteDocumentsFromKnowledgeBaseResponse,
    UploadDocumentsToKnowledgeBaseResponse,
    UploadChunksToKnowledgeBaseResponse,
    GetDocumentResponse,
    GetDocumentsResponse,
)
from ....infrastructure.operation_logging import (
    get_operation_user_id,
)
from ....infrastructure.storage import S3Service

logger = get_file_logger()


class KnowledgeBaseManager:
    """Manages knowledge base operations: creation, document upload, processing, and status tracking."""

    def __init__(self):
        self.docs_dir = os.path.join(Config.DATA_DIR, "docs")
        os.makedirs(self.docs_dir, exist_ok=True)

        self.use_s3 = Config.USE_AWS_S3_STORAGE
        if self.use_s3:
            logger.info("KnowledgeBaseManager initialized with S3 storage")
        else:
            logger.info("KnowledgeBaseManager initialized with local storage")

        self._upload_lock = threading.RLock()
        self._file_write_lock = threading.Lock()
        self._db_lock = threading.RLock()

        # Initialize RAG index manager for chunk indexing
        self.rag_index_manager = RAGIndexManager(db_lock=self._db_lock, docs_dir=self.docs_dir)

        # Auto-create indexes for collision-safe deduplication
        self._create_database_indexes()

    def create_knowledge_base(
        self,
        title: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Dict[str, Any] = {},
    ) -> CreateKnowledgeBaseResponse:
        """
        Create a new knowledge base with comprehensive validation and error handling.

        Args:
            user_id_or_api_key: User identifier or API key for audit trail
            title: Optional title for the knowledge base
            metadata: Optional metadata dictionary for additional context

        Returns:
            Dictionary containing created knowledge base details

        Raises:
            ValueError: If user_id_or_api_key is missing are invalid
            Exception: If database operation fails
        """
        created_at = datetime.now(timezone.utc)
        update_operation_status(TaskStatus.PROCESSING)
        # ============================================
        # KB CREATION
        # ============================================
        new_kb = KnowledgeBase(title=title, metadata=metadata, description=description)

        try:
            with self._db_lock:
                with get_db_session() as db:
                    # Check for existing KB (race condition protection)
                    existing_kb = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                        {"_id": new_kb.kb_id, "user_id": get_operation_user_id()}
                    )
                    while existing_kb:
                        logger.warning(f"KB {new_kb.kb_id} already exists, regenerating ID")
                        new_kb.kb_id = generate_kb_id()
                        existing_kb = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                            {"_id": new_kb.kb_id, "user_id": get_operation_user_id()}
                        )

                    # Insert KB entry
                    try:
                        db[Config.KNOWLEDGE_BASES_COLLECTION].insert_one(
                            new_kb.model_dump(by_alias=True, exclude_none=True)
                        )
                        logger.info(f"Created knowledge base {new_kb.kb_id}")
                    except DuplicateKeyError:
                        error_msg = f"Duplicate KB creation: {new_kb.kb_id} already exists"
                        logger.error(error_msg)
                        return CreateKnowledgeBaseResponse(success=False, message=error_msg)

        except ValueError:
            return CreateKnowledgeBaseResponse(
                success=False,
                message=f"Duplicate KB creation!",
                created_at=created_at,
                status=TaskStatus.FAILED,
            )
        except Exception as e:
            logger.error(f"Critical error creating KB {new_kb.kb_id}: {str(e)}", exc_info=True)
            # Log failed operation for unexpected errors
            return CreateKnowledgeBaseResponse(
                success=False,
                message=f"Critical error creating KB {new_kb.kb_id}",
                created_at=created_at,
                status=TaskStatus.FAILED,
            )

        logger.info(f"Knowledge base creation successful: {new_kb.kb_id}")

        # Update operation metadata with created KB
        update_operation_metadata(
            {
                "$addToSet": {"kb_ids": [new_kb.kb_id]},
            }
        )

        return CreateKnowledgeBaseResponse(
            message="Knowledge base created successfully!",
            kb_id=new_kb.kb_id,
            title=new_kb.title,
            created_at=new_kb.created_at,
            status=new_kb.status,
        )

    def get_knowledge_base(self, kb_id: str) -> GetKnowledgeBaseResponse:
        """
        Get knowledge base information by ID with comprehensive validation and error handling.

        Excludes deleted knowledge bases. Logs all operations (success and failure) for audit trail.

        Args:
            user_id_or_api_key: User identifier or API key for audit trail
            kb_id: Knowledge base ID to retrieve

        Returns:
            Dictionary containing knowledge base details

        Raises:
            ValueError: If user_id_or_api_key or kb_id is invalid, or KB not found
        """
        # ============================================
        # KB RETRIEVAL
        # ============================================
        kb_entry = None
        update_operation_status(TaskStatus.PROCESSING)

        try:
            with self._db_lock:
                with get_db_session() as db:
                    kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                        {
                            "_id": kb_id,
                            # "deleted": {"$ne": True},
                            "user_id": get_operation_user_id(),
                        }
                    )

            # Check if KB was found
            if not kb_entry:
                error_msg = f"Knowledge base {kb_id} not found"
                logger.warning(error_msg)
                return GetKnowledgeBaseResponse(success=False, message=error_msg)

            kb_model = KnowledgeBase(**kb_entry)

            logger.info(f"Retrieved knowledge base: {kb_id}")

            # Update operation metadata with accessed KB and its contents
            update_operation_metadata(
                {
                    "$addToSet": {
                        "kb_ids": [kb_id],
                        "doc_ids": kb_model.doc_ids,
                    }
                }
            )

        except ValueError as ve:
            logger.warning(f"{ve}")
            return GetKnowledgeBaseResponse(success=False, message="Value Error!")
        except Exception as e:
            logger.error(f"Critical error retrieving KB {kb_id}: {str(e)}", exc_info=True)
            return GetKnowledgeBaseResponse(
                success=False, message=f"Critical error retrieving KB {kb_id}"
            )

        return GetKnowledgeBaseResponse(knowledge_base=kb_model)

    def list_knowledge_bases(
        self,
        filters: Optional[Dict[str, Any]] = None,
        projections: Optional[Dict[str, Any]] = None,
    ) -> ListKnowledgeBasesResponse:
        """List knowledge bases with optional filtering and projections.

        When projections are used, returns raw dictionary data. Otherwise returns Pydantic models.

        Args:
            filters: MongoDB-style filters to apply (e.g., {'title': 'My KB'})
            projections: MongoDB-style projections to specify which fields to return

        Returns:
            List of knowledge bases (as models or raw data depending on projections)
        """
        update_operation_status(TaskStatus.PROCESSING)

        # Initialize filters if not provided
        filters = filters or {}
        projections = projections or {}

        # Merge user filters with base filter
        merged_filter: Dict[str, Any] = {**filters}

        merged_filter["user_id"] = get_operation_user_id()
        with get_db_session() as db:
            # Note: find(query, {}) and find(query) are identical, so always pass projections
            kb_entries = (
                db[Config.KNOWLEDGE_BASES_COLLECTION].find(merged_filter, projections).to_list()
            )
        merged_filter.pop("user_id", None)

        # If projections are used, return raw data; otherwise convert to Pydantic models
        if projections:
            kb_data: List[Union[KnowledgeBase, Dict[str, Any]]] = cast(
                List[Union[KnowledgeBase, Dict[str, Any]]], kb_entries
            )
            kb_ids = [kb.get("kb_id") for kb in kb_entries if "kb_id" in kb]
        else:
            kb_models = [KnowledgeBase(**kb_entry) for kb_entry in kb_entries]
            kb_data = cast(List[Union[KnowledgeBase, Dict[str, Any]]], kb_models)
            kb_ids = [kb.kb_id for kb in kb_models]

        # Update operation metadata with listed KBs
        update_operation_metadata(
            {
                "$addToSet": {"kb_ids": kb_ids},
                "$set": {
                    "kb_count": len(kb_ids),
                    "filters_applied": bool(filters),
                    "projections_applied": bool(projections),
                },
            }
        )

        return ListKnowledgeBasesResponse(
            knowledge_bases=kb_data, count=len(kb_data), filters=merged_filter
        )

    def delete_knowledge_base(self, kb_id: str) -> DeleteKnowledgeBaseResponse:
        """Soft delete a knowledge base and all associated resources.

        Cascade soft delete flow:
        1.
        2. Soft delete all knowledge bases with [DELETED] prefix and deleted=true
        3. Delete all documents using delete_docs_from_knowledge_base (soft deletes with [DELETED] prefix)
        4. Soft delete KB entry with [DELETED] prefix and deleted=true
        """
        deleted_at = datetime.now(timezone.utc)
        deleted_docs_count = 0

        with self._db_lock:
            with get_db_session() as db:
                # Get KB entry to retrieve associated data
                kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                    {"_id": kb_id, "user_id": get_operation_user_id()}
                )

                if not kb_entry:
                    return DeleteKnowledgeBaseResponse(
                        success=False, message=f"Knowledge base {kb_id} not found"
                    )

                kb_model = KnowledgeBase(**kb_entry)

                # Prevent deletion during processing to avoid race conditions
                if kb_model.status in [TaskStatus.PROCESSING, TaskStatus.QUEUED]:
                    return DeleteKnowledgeBaseResponse(
                        success=False,
                        message=(
                            f"Cannot delete knowledge base {kb_id} while it is being processed. "
                            f"Current status: {kb_model.status}. Please wait for processing to complete."
                        ),
                    )

        # Delete all documents from KB using delete_docs_from_knowledge_base
        # This function handles soft delete with [DELETED] prefix for docs and chunks
        if kb_model.doc_ids:
            deleted_docs_count = self.delete_docs_from_knowledge_base(
                kb_id, kb_model.doc_ids
            ).deleted_docs_count

        # Soft delete the KB entry with [DELETED] prefix
        with self._db_lock:
            with get_db_session() as db:
                kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                    {"_id": kb_id, "user_id": get_operation_user_id()}
                )
                if kb_entry:
                    # deleted_kb_model = KnowledgeBase(
                    #     **kb_entry
                    # )
                    # deleted_kb_model.original_kb_id = kb_id
                    # deleted_kb_model.kb_id = f"[DELETED]{kb_id}_{deleted_at}"
                    # deleted_kb_model.deleted = True
                    # deleted_kb_model.deleted_at = deleted_at

                    # Delete the original KB entry and insert the deleted version
                    db[Config.KNOWLEDGE_BASES_COLLECTION].delete_one(
                        {"_id": kb_id, "user_id": get_operation_user_id()}
                    )
                    # db[Config.KNOWLEDGE_BASES_COLLECTION].insert_one(deleted_kb_model.model_dump(by_alias=True, exclude_none=True))

        logger.info(
            f"Soft deleted knowledge base {kb_id}: docs={deleted_docs_count} "
            # f"with [DELETED] prefix and deleted=true"
        )

        # Update operation metadata with deleted data
        update_operation_metadata(
            {
                "$addToSet": {
                    "kb_ids": [kb_id],
                    "doc_ids": kb_model.doc_ids,
                },
                "$inc": {
                    "deleted_docs_count": deleted_docs_count,
                },
            }
        )

        return DeleteKnowledgeBaseResponse(
            kb_id=kb_id,
            deleted_at=deleted_at,
            deleted_docs_count=deleted_docs_count,
            # message="Knowledge base and all associated resources soft deleted with [DELETED] prefix for audit purposes"
            message="Knowledge base and all associated resources deleted",
        )

    def modify_knowledge_base(self, kb_id: str, **kwargs: Any) -> ModifyKnowledgeBaseResponse:
        """
        Modify knowledge base metadata. Cannot modify deleted knowledge bases.

        Args:
            kb_id: Knowledge base ID
            **kwargs: Fields to update:
                - title: str - Update knowledge base title
                - metadata: Dict[str, Any] - Replace entire metadata object
                - metadata_updates: Dict[str, Any] - Update individual metadata sub-keys (merged with existing)

        Returns:
            Updated knowledge base entry
        """
        updated_at = datetime.now(timezone.utc)
        if not kb_id:
            return ModifyKnowledgeBaseResponse(
                success=False, message="Knowledge base ID is required"
            )

        # Extract special handling fields
        metadata_updates = kwargs.pop("metadata_updates", None)

        # Only allow modification of specific fields
        allowed_fields = {"title", "metadata"}
        update_data = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not update_data and not metadata_updates:
            return ModifyKnowledgeBaseResponse(
                success=False, message="No valid fields provided for update"
            )

        # Add updated_at timestamp
        update_data["updated_at"] = updated_at

        with self._db_lock:
            with get_db_session() as db:
                # Get the current KB to handle metadata_updates
                # kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                #     {"_id": kb_id, "deleted": {"$ne": True}, "user_id": get_operation_user_id()}
                # )
                kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                    {"_id": kb_id, "user_id": get_operation_user_id()}
                )

                if not kb_entry:
                    return ModifyKnowledgeBaseResponse(
                        success=False, message=f"Knowledge base {kb_id} not found"
                    )

                # Use regular constructor for proper nested object construction
                kb_model = KnowledgeBase(**kb_entry)

                # Handle metadata_updates - merge with existing metadata
                if metadata_updates:
                    current_metadata = kb_model.metadata
                    current_metadata.update(metadata_updates)
                    update_data["metadata"] = current_metadata

                    # Also check if doc_ids differ (set comparison - order independent)
                    doc_ids_differ = set(kb_model.doc_ids) != set(kb_model.index_build_on_doc_ids)

                    if doc_ids_differ:
                        # doc_ids changed - need to rebuild
                        update_data["status"] = TaskStatus.PENDING.value
                        update_data["processing_started_at"] = None
                        update_data["processing_completed_at"] = None
                        update_data["error"] = None

                # Perform the update
                result = db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                    {
                        "_id": kb_id,
                        "deleted": {"$ne": True},
                        "user_id": get_operation_user_id(),
                    },
                    {"$set": update_data},
                )

        if result.matched_count == 0:
            return ModifyKnowledgeBaseResponse(
                success=False, message=f"Knowledge base {kb_id} not found"
            )

        logger.info(f"Modified knowledge base {kb_id} with fields: {list(update_data.keys())}")

        # Update operation metadata with modified KB
        update_operation_metadata(
            {
                "$addToSet": {"kb_ids": [kb_id]},
                "$set": {"modified_fields": list(update_data.keys())},
            }
        )

        update_data.pop("updated_at", None)

        return ModifyKnowledgeBaseResponse(
            message="Knowledge base modified successfully",
            kb_id=kb_id,
            modified_fields=list(update_data.keys()),
            updated_at=updated_at,
        )

    def delete_docs_from_knowledge_base(
        self, kb_id: str, doc_ids: List[str]
    ) -> DeleteDocumentsFromKnowledgeBaseResponse:
        """
        Delete documents from a knowledge base. Soft deletes docs only if no other KB uses them.

        Args:
            kb_id: Knowledge base ID
            file_ids: List of document IDs to delete

        Returns:
            Deletion confirmation with count
        """
        if not doc_ids:
            return DeleteDocumentsFromKnowledgeBaseResponse(
                success=False, message="No file IDs provided for deletion"
            )

        updated_at = datetime.now(timezone.utc)
        deleted_chunk_ids: List[str] = []
        deleted_content_ids: List[str] = []

        with self._db_lock:
            with get_db_session() as db:
                # Verify KB exists and is not deleted
                kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                    {
                        "_id": kb_id,
                        # "deleted": {"$ne": True},
                        "user_id": get_operation_user_id(),
                    }
                )
                if not kb_entry:
                    return DeleteDocumentsFromKnowledgeBaseResponse(
                        success=False, message=f"Knowledge base {kb_id} not found"
                    )

                # Use regular constructor for proper nested object construction
                kb_model = KnowledgeBase(**kb_entry)

                # Prevent deletion during processing to avoid race conditions with base building
                if kb_model.status in [TaskStatus.PROCESSING, TaskStatus.QUEUED]:
                    return DeleteDocumentsFromKnowledgeBaseResponse(
                        success=False,
                        message=(
                            f"Cannot delete documents from knowledge base {kb_id} while it is being processed. "
                            f"Current status: {kb_model.status}. Please wait for processing to complete."
                        ),
                    )

                # Fetch documents being deleted to calculate size
                docs_being_deleted = list(
                    db[Config.DOCUMENTS_COLLECTION].find(
                        {
                            "_id": {"$in": doc_ids},
                            "user_id": get_operation_user_id(),
                        },
                        {"_id": 1, "doc_size": 1},
                    )
                )

                # Calculate size_mb of documents being deleted
                deleted_docs_size_bytes = sum(doc.get("doc_size", 0) for doc in docs_being_deleted)
                deleted_docs_size_mb = deleted_docs_size_bytes / (1024 * 1024)

                # Remove docs from KB's doc_ids and conditionally set status for base rebuild
                # Get new doc_ids after removal for comparison (set comparison - order independent)
                new_doc_ids = [doc_id for doc_id in kb_model.doc_ids if doc_id not in doc_ids]
                doc_ids_differ = set(new_doc_ids) != set(kb_model.index_build_on_doc_ids)

                # Build the update query
                update_query: Dict[str, Any] = {
                    "$pull": {"doc_ids": {"$in": doc_ids}},
                    "$set": {"updated_at": updated_at},
                    "$inc": {
                        "size_mb": -deleted_docs_size_mb,
                    },
                }

                # Set status based on whether doc_ids differ from last build
                if doc_ids_differ:
                    # Doc_ids changed - need rebuild
                    update_query["$set"].update(
                        {
                            "status": TaskStatus.PENDING.value,
                            "processing_started_at": None,
                            "processing_completed_at": None,
                            "error": None,
                        }
                    )

                db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                    {"_id": kb_id, "user_id": get_operation_user_id()}, update_query
                )

                # Check if any other active KB is using these documents
                other_kbs_using_docs = db[Config.KNOWLEDGE_BASES_COLLECTION].count_documents(
                    {
                        "_id": {"$ne": kb_id},
                        "user_id": get_operation_user_id(),
                        "doc_ids": {"$in": doc_ids},
                    }
                )

                if other_kbs_using_docs == 0:
                    # No other KB is using these docs, delete them
                    # Collect content_ids from documents being deleted
                    docs_to_delete = list(
                        db[Config.DOCUMENTS_COLLECTION].find(
                            {
                                "_id": {"$in": doc_ids},
                                "user_id": get_operation_user_id(),
                            },
                        )
                    )
                    # Use model_construct for DB-sourced data (skips validation)
                    docs_to_delete_models = [Document(**doc) for doc in docs_to_delete]
                    content_ids_to_check = [doc.content_id for doc in docs_to_delete_models]

                    # Update documents with deleted flag and new IDs
                    for doc_id in doc_ids:
                        doc_entry = db[Config.DOCUMENTS_COLLECTION].find_one(
                            {"_id": doc_id, "user_id": get_operation_user_id()}
                        )
                        if doc_entry:

                            db[Config.DOCUMENTS_COLLECTION].delete_one(
                                {"_id": doc_id, "user_id": get_operation_user_id()}
                            )

                    # Delete chunks from ChromaDB for these documents
                    user_id = get_operation_user_id()
                    collection_name = f"chunks_{user_id}"
                    deleted_chunk_count = self.rag_index_manager.chroma_store.delete_chunks(
                        collection_name=collection_name, doc_ids=doc_ids
                    )
                    logger.info(
                        f"Deleted {deleted_chunk_count} chunks from ChromaDB for {len(doc_ids)} documents from KB {kb_id}"
                    )

                    # Check and soft delete content if no other active documents use them
                    if content_ids_to_check:
                        content_deleted_count = 0
                        for content_id in content_ids_to_check:
                            # Check if any other active (non-deleted) document is using this content
                            other_docs_using_content = db[
                                Config.DOCUMENTS_COLLECTION
                            ].count_documents(
                                {
                                    "content_id": content_id,
                                    # "deleted": {"$ne": True},
                                    "user_id": get_operation_user_id(),
                                }
                            )

                            if other_docs_using_content == 0:
                                # No other active document is using this content, soft delete it
                                content_entry = db[Config.DOCUMENT_CONTENTS_COLLECTION].find_one(
                                    {
                                        "_id": content_id,
                                        "user_id": get_operation_user_id(),
                                    }
                                )
                                if content_entry:
                                    deleted_content_ids.append(content_id)

                                    db[Config.DOCUMENT_CONTENTS_COLLECTION].delete_one(
                                        {
                                            "_id": content_id,
                                            "user_id": get_operation_user_id(),
                                        }
                                    )
                                    content_deleted_count += 1

                        if content_deleted_count > 0:
                            logger.info(
                                f"Deleted {content_deleted_count} content entries for KB {kb_id}"
                            )

                    delete_count = len(doc_ids)
                else:
                    # Other KBs are using these docs, just remove from current KB (chunks remain active for other KBs)
                    logger.info(
                        f"Documents {doc_ids} are used by other KBs. Chunks not deleted to preserve data for other knowledge bases."
                    )
                    delete_count = len(doc_ids)

        logger.info(
            f"Deleted {delete_count} documents from KB {kb_id}. Status set to pending for base rebuild"
        )

        # Update operation metadata with deleted data
        update_operation_metadata(
            {
                "$addToSet": {
                    "kb_ids": [kb_id],
                    "doc_ids": doc_ids,
                    "chunk_ids": deleted_chunk_ids,
                    "content_ids": deleted_content_ids,
                },
                "$inc": {"deleted_count": delete_count},
            }
        )

        return DeleteDocumentsFromKnowledgeBaseResponse(
            message=("Documents deleted successfully" "KB status set to pending."),
            kb_id=kb_id,
            deleted_docs_count=delete_count,
            updated_at=updated_at,
        )

    def upload_docs_to_knowledge_base(
        self, kb_id: str, docs: List[Doc]
    ) -> UploadDocumentsToKnowledgeBaseResponse:
        """
        Thread-safe document upload that returns immediately.
        Submits chunking and indexing as background task via thread pool.
        Returns operation status with task_id for progress tracking.
        """
        submission_time = datetime.now(timezone.utc)

        with self._upload_lock:
            if not docs:
                return UploadDocumentsToKnowledgeBaseResponse(
                    success=False, message="No documents provided for upload"
                )

            # Verify KB exists and is not deleted, and is not currently being processed
            with self._db_lock:
                with get_db_session() as db:
                    kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                        {
                            "_id": kb_id,
                            # "deleted": {"$ne": True},
                            "user_id": get_operation_user_id(),
                        }
                    )
                    if not kb_entry:
                        return UploadDocumentsToKnowledgeBaseResponse(
                            success=False, message=f"Knowledge base {kb_id} not found"
                        )

                    kb_model = KnowledgeBase(**kb_entry)

                    # Prevent upload during processing to avoid race condition
                    # (newly uploaded docs would not be included in current base build)
                    if kb_model.status in [TaskStatus.PROCESSING, TaskStatus.QUEUED]:
                        return UploadDocumentsToKnowledgeBaseResponse(
                            success=False,
                            message=(
                                f"Cannot upload documents to knowledge base {kb_id} while it is being processed. "
                                f"Current status: {kb_model.status}. Please wait for processing to complete."
                            ),
                        )

            created_at = datetime.now(timezone.utc)
            new_content_to_write: List[Dict[str, Any]] = []
            content_ids: List[str] = []
            doc_ids: List[str] = []
            content_entries: List[Content] = []
            doc_entries: List[Document] = []

            # Wrap ALL operations in try-except for comprehensive rollback on any failure
            try:
                # Step 1: Process documents directly - generate IDs and resolve deduplication
                new_content_to_write, content_entries, doc_entries, content_ids, doc_ids = (
                    self._process_documents_for_upload(docs, created_at)
                )

                if not doc_ids:
                    return UploadDocumentsToKnowledgeBaseResponse(
                        success=False,
                        message="All documents were invalid or could not be processed",
                    )

                # Step 2: Get existing docs and current KB doc_ids
                _, _, current_kb_doc_ids = self._fetch_existing_documents(
                    kb_id, content_ids, doc_ids
                )

                # Step 3: Parallel file I/O (can fail - files written to disk)
                self._write_files_in_parallel(new_content_to_write)

                # Step 4: Batch database inserts (can fail - DB partially updated)
                self._insert_documents_to_db(content_entries, doc_entries)

                # Step 5: Update KB only if new docs added
                new_doc_ids_for_kb = [
                    doc_id for doc_id in doc_ids if doc_id not in current_kb_doc_ids
                ]

                # Step 6: KB update
                if new_doc_ids_for_kb:
                    updated_at = datetime.now(timezone.utc)

                    # Calculate size_mb of new documents
                    new_doc_entries = [
                        doc for doc in doc_entries if doc.doc_id in new_doc_ids_for_kb
                    ]
                    new_docs_size_bytes = sum(doc.doc_size for doc in new_doc_entries)
                    new_docs_size_mb = new_docs_size_bytes / (1024 * 1024)

                    with self._db_lock:
                        with get_db_session() as db:
                            # Get updated KB to check if new doc_ids differ from index_build_on_doc_ids
                            updated_kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                                {
                                    "_id": kb_id,
                                    "user_id": get_operation_user_id(),
                                }
                            )
                            if updated_kb_entry:
                                # Use regular constructor for proper nested object construction
                                kb_after_update = KnowledgeBase(**updated_kb_entry)
                                updated_doc_ids = kb_after_update.doc_ids + new_doc_ids_for_kb
                                # Set comparison - order independent
                                doc_ids_differ = set(updated_doc_ids) != set(
                                    kb_after_update.index_build_on_doc_ids
                                )

                                # Build update query
                                update_query: Dict[str, Any] = {
                                    "$addToSet": {"doc_ids": {"$each": new_doc_ids_for_kb}},
                                    "$set": {
                                        "updated_at": updated_at,
                                        "last_uploaded_at": updated_at,
                                    },
                                    "$inc": {
                                        "size_mb": new_docs_size_mb,
                                    },
                                }

                                # Set status based on whether doc_ids differ from last build
                                if doc_ids_differ:
                                    update_query["$set"].update(
                                        {
                                            "status": TaskStatus.PENDING.value,
                                            "processing_started_at": None,
                                            "processing_completed_at": None,
                                            "error": None,
                                        }
                                    )
                                    log_msg = "Status set to pending for base rebuild"
                                else:
                                    log_msg = "No rebuild needed (no bases exist yet)"

                                db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                                    {"_id": kb_id, "user_id": get_operation_user_id()},
                                    update_query,
                                )
                            else:
                                # Fallback: always set to PENDING if we can't read updated KB
                                db[Config.KNOWLEDGE_BASES_COLLECTION].update_one(
                                    {"_id": kb_id, "user_id": get_operation_user_id()},
                                    {
                                        "$addToSet": {"doc_ids": {"$each": new_doc_ids_for_kb}},
                                        "$set": {
                                            "updated_at": updated_at,
                                            "last_uploaded_at": updated_at,
                                            "status": TaskStatus.PENDING.value,
                                            "processing_started_at": None,
                                            "processing_completed_at": None,
                                            "error": None,
                                        },
                                        "$inc": {
                                            "size_mb": new_docs_size_mb,
                                        },
                                    },
                                )
                                log_msg = "Status set to pending for base rebuild"

                    logger.info(
                        f"Successfully uploaded {len(new_doc_ids_for_kb)} new documents to KB {kb_id}. {log_msg}"
                    )

                    # Update operation metadata
                    update_operation_metadata(
                        {
                            "$addToSet": {
                                "kb_ids": [kb_id],
                                "doc_ids": new_doc_ids_for_kb,
                                "content_ids": content_ids,
                            },
                            "$inc": {
                                "new_doc_count": len(new_doc_ids_for_kb),
                                "total_doc_count": len(new_doc_ids_for_kb),
                            },
                        }
                    )

                    # Submit background indexing task
                    context = copy_context()
                    executor.submit(
                        context.run,
                        self._index_documents_background,
                        kb_id,
                        new_doc_ids_for_kb,
                    )
                    logger.info(
                        f"Submitted background indexing task for {len(new_doc_ids_for_kb)} documents in KB {kb_id}"
                    )

                    # Return immediately with operation status
                    return UploadDocumentsToKnowledgeBaseResponse(
                        success=True,
                        message=f"Uploaded {len(new_doc_ids_for_kb)} new documents. Indexing started.",
                        kb_id=kb_id,
                        docs_count=len(new_doc_ids_for_kb),
                        task_status=TaskStatus.PENDING,
                        submission_time=submission_time,
                    )
                else:
                    logger.info(
                        f"No new documents to add to KB {kb_id} (all documents already associated)"
                    )
                    new_doc_ids_to_index: List[str] = []

                    # Update operation metadata
                    update_operation_metadata(
                        {
                            "$addToSet": {
                                "kb_ids": [kb_id],
                                "doc_ids": doc_ids,
                                "content_ids": content_ids,
                            },
                            "$inc": {
                                "new_doc_count": len(new_doc_ids_to_index),
                                "total_doc_count": len(doc_ids),
                            },
                        }
                    )

                    # Submit background indexing task
                    context = copy_context()
                    if new_doc_ids_to_index:
                        executor.submit(
                            context.run,
                            self._index_documents_background,
                            kb_id,
                            new_doc_ids_to_index,
                        )
                        logger.info(
                            f"Submitted background indexing task for {len(new_doc_ids_to_index)} documents in KB {kb_id}"
                        )

                    # Return immediately with operation status
                    return UploadDocumentsToKnowledgeBaseResponse(
                        success=True,
                        message=f"Uploaded {len(doc_ids)} documents. Indexing started.",
                        kb_id=kb_id,
                        docs_count=len(doc_ids),
                        task_status=TaskStatus.PENDING,
                        submission_time=submission_time,
                    )
            except Exception as e:
                logger.error(f"Error during document upload for {kb_id}: {str(e)}", exc_info=True)

                # Rollback Step 1: Delete written files from disk
                if new_content_to_write:
                    for item in new_content_to_write:
                        file_path = item.get("storage_path")
                        if file_path:
                            try:
                                if os.path.exists(file_path):
                                    os.remove(file_path)
                                    logger.info(f"Rolled back file from disk: {file_path}")
                            except Exception as file_error:
                                logger.error(
                                    f"Failed to delete file {file_path} during rollback: {str(file_error)}"
                                )

                # Rollback Step 2: Delete ONLY NEWLY CREATED documents and content from MongoDB
                # (NOT existing documents that were deduplicated)
                newly_created_doc_ids = [doc.doc_id for doc in doc_entries]
                newly_created_content_ids = [content.content_id for content in content_entries]

                if newly_created_doc_ids or newly_created_content_ids:
                    try:
                        with self._db_lock:
                            with get_db_session() as db:
                                if newly_created_doc_ids:
                                    db[Config.DOCUMENTS_COLLECTION].delete_many(
                                        {
                                            "_id": {"$in": newly_created_doc_ids},
                                            "user_id": get_operation_user_id(),
                                        }
                                    )
                                    logger.info(
                                        f"Rolled back {len(newly_created_doc_ids)} newly created document entries from MongoDB"
                                    )

                                if newly_created_content_ids:
                                    db[Config.DOCUMENT_CONTENTS_COLLECTION].delete_many(
                                        {
                                            "_id": {"$in": newly_created_content_ids},
                                            "user_id": get_operation_user_id(),
                                        }
                                    )
                                    logger.info(
                                        f"Rolled back {len(newly_created_content_ids)} newly created content entries from MongoDB"
                                    )
                    except Exception as rollback_error:
                        logger.error(
                            f"Failed to rollback database entries: {str(rollback_error)}",
                            exc_info=True,
                        )

                raise  # Re-raise to propagate error to caller

    def upload_chunks_to_knowledge_base(
        self, kb_id: str, chunks: List[Chunk]
    ) -> UploadChunksToKnowledgeBaseResponse:
        """
        Thread-safe chunk upload that returns immediately.
        Submits chunk indexing as background task via thread pool.
        Returns operation status with task_id for progress tracking.

        Args:
            kb_id: Knowledge base ID
            chunks: List of pre-chunked Chunk objects

        Returns:
            Response with operation status and task_id
        """
        submission_time = datetime.now(timezone.utc)

        with self._upload_lock:
            if not chunks:
                return UploadChunksToKnowledgeBaseResponse(
                    success=False, message="No chunks provided for upload"
                )

            # Verify KB exists and is not deleted
            with self._db_lock:
                with get_db_session() as db:
                    kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                        {
                            "_id": kb_id,
                            "user_id": get_operation_user_id(),
                        }
                    )
                    if not kb_entry:
                        return UploadChunksToKnowledgeBaseResponse(
                            success=False, message=f"Knowledge base {kb_id} not found"
                        )

                    kb_model = KnowledgeBase(**kb_entry)

                    # Prevent upload during processing
                    if kb_model.status in [TaskStatus.PROCESSING, TaskStatus.QUEUED]:
                        return UploadChunksToKnowledgeBaseResponse(
                            success=False,
                            message=(
                                f"Cannot upload chunks to knowledge base {kb_id} while it is being processed. "
                                f"Current status: {kb_model.status}. Please wait for processing to complete."
                            ),
                        )

            created_at = datetime.now(timezone.utc)
            chunk_ids = [chunk.chunk_id for chunk in chunks]
            backfilled_doc_ids: List[str] = []

            # Wrap backfill document creation in try-except to ensure proper rollback if it fails
            try:
                # Extract unique doc_ids from chunks and create missing document entries
                with self._db_lock:
                    with get_db_session() as db:
                        # Get unique doc_ids from chunks
                        chunk_doc_ids: Set[str] = set()
                        for chunk in chunks:
                            if hasattr(chunk, "doc_id") and chunk.doc_id:
                                chunk_doc_ids.add(chunk.doc_id)

                        if chunk_doc_ids:
                            # Check which doc_ids already exist
                            existing_docs = db[Config.DOCUMENTS_COLLECTION].find(
                                {
                                    "_id": {"$in": list(chunk_doc_ids)},
                                    "user_id": get_operation_user_id(),
                                }
                            )
                            existing_doc_ids = {doc["_id"] for doc in existing_docs}

                            # Create missing document entries with backfilled data
                            missing_doc_ids = chunk_doc_ids - existing_doc_ids
                            if missing_doc_ids:
                                documents_to_create: List[Dict[str, Any]] = []
                                for doc_id in missing_doc_ids:
                                    # Generate content_id for the backfilled document
                                    content_id = generate_content_id()

                                    # Create backfilled document entry using Document model
                                    doc_model = Document(
                                        _id=doc_id,
                                        content_id=content_id,
                                        uploaded_at=created_at,
                                        doc_name=f"Imported chunks - {doc_id[:12]}",
                                        content_type="application/octet-stream",
                                        doc_size=0,
                                        source="imported_chunks",
                                        chunked=True,
                                        estimated_cost_usd=0.0,
                                    )
                                    # Convert to dict for insertion, with user_id and kb_id added
                                    doc_dict = doc_model.model_dump(
                                        by_alias=True, exclude_none=True
                                    )
                                    doc_dict["user_id"] = get_operation_user_id()
                                    doc_dict["kb_id"] = kb_id
                                    documents_to_create.append(doc_dict)
                                    backfilled_doc_ids.append(doc_id)

                                # Batch insert missing documents (CAN FAIL - need rollback)
                                if documents_to_create:
                                    db[Config.DOCUMENTS_COLLECTION].insert_many(documents_to_create)
                                    logger.info(
                                        f"Created {len(documents_to_create)} backfilled document entries for missing doc_ids"
                                    )
            except Exception as backfill_error:
                logger.error(
                    f"Failed to create backfilled document entries for {kb_id}: {str(backfill_error)}",
                    exc_info=True,
                )

                # Rollback: Delete any backfilled documents that were inserted
                if backfilled_doc_ids:
                    try:
                        with self._db_lock:
                            with get_db_session() as db:
                                db[Config.DOCUMENTS_COLLECTION].delete_many(
                                    {
                                        "_id": {"$in": backfilled_doc_ids},
                                        "user_id": get_operation_user_id(),
                                    }
                                )
                                logger.info(
                                    f"Rolled back {len(backfilled_doc_ids)} backfilled document entries from MongoDB"
                                )
                    except Exception as rollback_error:
                        logger.error(
                            f"Failed to rollback backfilled document entries: {str(rollback_error)}",
                            exc_info=True,
                        )

                raise  # Re-raise the original error

            # Wrap metadata update and task submission in try-except for rollback if needed
            try:
                # Update operation metadata
                update_operation_metadata(
                    {
                        "$addToSet": {
                            "kb_ids": [kb_id],
                        },
                        "$inc": {
                            "new_chunk_count": len(chunk_ids),
                        },
                    }
                )

                # Submit background chunk indexing task
                context = copy_context()
                executor.submit(
                    context.run,
                    self._index_chunks_background,
                    kb_id,
                    chunks,
                )
                logger.info(
                    f"Submitted background indexing task for {len(chunk_ids)} chunks in KB {kb_id}"
                )

                # Return immediately with operation status
                return UploadChunksToKnowledgeBaseResponse(
                    success=True,
                    message=f"Uploaded {len(chunk_ids)} chunks. Indexing started.",
                    kb_id=kb_id,
                    chunks_count=len(chunk_ids),
                    task_status=TaskStatus.PENDING,
                    submission_time=submission_time,
                )
            except Exception as e:
                logger.error(
                    f"Error during chunk submission or metadata update for {kb_id}: {str(e)}",
                    exc_info=True,
                )

                # Rollback: Delete backfilled document entries from MongoDB
                if backfilled_doc_ids:
                    try:
                        with self._db_lock:
                            with get_db_session() as db:
                                db[Config.DOCUMENTS_COLLECTION].delete_many(
                                    {
                                        "_id": {"$in": backfilled_doc_ids},
                                        "user_id": get_operation_user_id(),
                                    }
                                )
                                logger.info(
                                    f"Rolled back {len(backfilled_doc_ids)} backfilled document entries from MongoDB"
                                )
                    except Exception as rollback_error:
                        logger.error(
                            f"Failed to rollback backfilled document entries: {str(rollback_error)}",
                            exc_info=True,
                        )

                raise  # Re-raise to propagate error to caller

    def get_document(self, doc_id: str) -> GetDocumentResponse:
        """
        Retrieve document metadata by ID.

        Args:
            doc_id: Document ID

        Returns:
            Document entry with metadata (id, name, type, size, source, upload date, etc.)

        Raises:
            ValueError: If document not found
        """
        with self._db_lock:
            with get_db_session() as db:
                doc_entry = db[Config.DOCUMENTS_COLLECTION].find_one(
                    {
                        "_id": doc_id,
                        # "deleted": {"$ne": True},
                        "user_id": get_operation_user_id(),
                    }
                )

        if not doc_entry:
            return GetDocumentResponse(success=False, message=f"Document {doc_id} not found")

        doc_model = Document(**doc_entry)

        logger.info(f"Retrieved document metadata for {doc_id}")
        return GetDocumentResponse(document=doc_model)

    def download_document(self, doc_id: str) -> tuple[bytes, str, str]:
        """
        Download document file content.

        Args:
            doc_id: Document ID

        Returns:
            Tuple of (file_content: bytes, filename: str, content_type: str)

        Raises:
            ValueError: If document not found
            IOError: If file cannot be read from disk
        """
        with self._db_lock:
            with get_db_session() as db:
                # Get document metadata
                doc_entry = db[Config.DOCUMENTS_COLLECTION].find_one(
                    {
                        "_id": doc_id,
                        # "deleted": {"$ne": True},
                        "user_id": get_operation_user_id(),
                    }
                )

                if not doc_entry:
                    raise ValueError(f"Document {doc_id} not found")

                doc_model = Document(**doc_entry)

                if not doc_model.content_id:
                    raise ValueError(f"Document {doc_id} has no content_id")

                # Get content metadata to find the file path
                content_entry = db[Config.DOCUMENT_CONTENTS_COLLECTION].find_one(
                    {"_id": doc_model.content_id, "user_id": get_operation_user_id()}
                )

                if not content_entry:
                    raise ValueError(
                        f"Content {doc_model.content_id} not found for document {doc_id}"
                    )

                content_model = Content(**content_entry)

                # Read file from storage (S3 or disk)
                content_path = content_model.content_path

                try:
                    if self.use_s3:
                        # Download from S3 using context manager for proper resource cleanup
                        s3_key = f"documents/{content_path}"
                        file_content = None
                        with S3Service() as s3_service:
                            file_content = s3_service.download_file(s3_key)

                        if file_content is None:
                            raise IOError(f"File not found in S3: {s3_key}")

                        logger.info(
                            f"Downloaded document {doc_id} from S3 ({len(file_content)} bytes)"
                        )
                        return file_content, doc_model.doc_name, doc_model.content_type
                    else:
                        # Read from local disk (backward compatibility)
                        # - Old format: absolute path (stored directly)
                        # - New format: relative hierarchical path (ab/cd/hash)
                        if os.path.isabs(content_path):
                            file_path = content_path
                        else:
                            file_path = os.path.join(self.docs_dir, content_path)

                        if not os.path.exists(file_path):
                            raise IOError(f"File not found at {file_path}")

                        with open(file_path, "rb") as f:
                            file_content = f.read()

                        logger.info(
                            f"Downloaded document {doc_id} from disk ({len(file_content)} bytes)"
                        )
                        return file_content, doc_model.doc_name, doc_model.content_type

                except IOError as e:
                    logger.error(f"Failed to read document file: {str(e)}")
                    raise
                except Exception as e:
                    logger.error(f"Unexpected error downloading document: {str(e)}")
                    raise IOError(f"Failed to download document: {str(e)}")

    def get_document_presigned_url(
        self, doc_id: str, expiration: int = 3600, inline: bool = False
    ) -> tuple[Optional[str], bool]:
        """
        Get or generate a presigned URL for accessing a document in S3.
        Caches the URL in database to avoid regenerating on repeated requests.

        Args:
            doc_id: Document ID
            expiration: URL expiration time in seconds (default: 1 hour)
            inline: If True, URL renders content in browser; if False, forces download

        Returns:
            Tuple of (presigned_url: str, is_s3: bool)

        Raises:
            ValueError: If document or content not found
            IOError: If S3 storage not enabled or URL generation fails
        """
        if not Config.USE_AWS_S3_STORAGE:
            raise IOError("S3 storage is not enabled for this deployment")

        # Read from DB while holding lock
        presigned_url_cached = None
        presigned_url_expires_at = None
        content_path = None
        doc_name = None

        with self._db_lock:
            with get_db_session() as db:
                doc_entry = db[Config.DOCUMENTS_COLLECTION].find_one(
                    {"_id": doc_id, "user_id": get_operation_user_id()}
                )
                if not doc_entry:
                    raise ValueError(f"Document {doc_id} not found")

                doc_model = Document(**doc_entry)
                if not doc_model.content_id:
                    raise ValueError(f"Document {doc_id} has no content_id")

                # Cache values for use outside lock
                presigned_url_cached = doc_model.presigned_url
                presigned_url_expires_at = doc_model.presigned_url_expires_at
                doc_name = doc_model.doc_name

                # Get content path (needed for regeneration if cache misses)
                content_entry = db[Config.DOCUMENT_CONTENTS_COLLECTION].find_one(
                    {"_id": doc_model.content_id, "user_id": get_operation_user_id()}
                )
                if not content_entry:
                    raise ValueError(
                        f"Content {doc_model.content_id} not found for document {doc_id}"
                    )

                content_model = Content(**content_entry)
                content_path = content_model.content_path

        # Check cache validity (outside lock to avoid contention)
        now = datetime.now(timezone.utc)

        # Ensure timezone-aware comparison (MongoDB stores timezone-naive datetimes)
        expires_at = presigned_url_expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        cache_is_valid = presigned_url_cached and expires_at and expires_at > now

        if cache_is_valid:
            return presigned_url_cached, True

        # Cache miss or expired - generate new URL
        try:
            s3_key = f"documents/{content_path}"
            presigned_url = None
            with S3Service() as s3_service:
                presigned_url = s3_service.get_presigned_url(
                    s3_key, expiration, filename=doc_name, inline=inline
                )

            if not presigned_url:
                raise IOError(f"Failed to generate presigned URL for {doc_id}")

            # Cache the new URL
            url_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expiration)
            with self._db_lock:
                with get_db_session() as db:
                    db[Config.DOCUMENTS_COLLECTION].update_one(
                        {"_id": doc_id, "user_id": get_operation_user_id()},
                        {
                            "$set": {
                                "presigned_url": presigned_url,
                                "presigned_url_expires_at": url_expires_at,
                            }
                        },
                    )

            return presigned_url, True

        except IOError:
            raise
        except Exception as e:
            logger.error(f"Error generating presigned URL for {doc_id}: {str(e)}", exc_info=True)
            raise IOError(f"Failed to generate presigned URL: {str(e)}")

    def cleanup_expired_presigned_urls(self) -> int:
        """
        Clean up expired presigned URLs from all documents (database maintenance).
        Should be called periodically via background tasks.

        Returns:
            Number of documents with expired presigned URLs cleared
        """
        with self._db_lock:
            try:
                with get_db_session() as db:
                    now = datetime.now(timezone.utc)
                    result = db[Config.DOCUMENTS_COLLECTION].update_many(
                        {
                            "presigned_url_expires_at": {"$lt": now},
                            "user_id": get_operation_user_id(),
                        },
                        {
                            "$set": {
                                "presigned_url": None,
                                "presigned_url_expires_at": None,
                            }
                        },
                    )
                    cleared_count = result.modified_count
                    if cleared_count > 0:
                        logger.info(f"Cleaned up {cleared_count} expired presigned URLs")
                    return cleared_count
            except Exception as e:
                logger.error(f"Error cleaning up expired presigned URLs: {str(e)}")
                raise

    def get_documents_batch(
        self, filters: Dict[str, Any], projections: Optional[Dict[str, Any]] = None
    ) -> GetDocumentsResponse:
        """
        Search documents with MongoDB-style filters and projections.

        Args:
            filters: MongoDB-style filters to search documents (required)
                    Examples:
                    - Batch retrieval: {'_id': {'$in': ['doc_1', 'doc_2']}}
                    - Filter by type: {'content_type': 'pdf'}
                    - Complex: {'kb_id': 'kb_123', 'content_type': {'$in': ['pdf', 'txt']}}
            projections: MongoDB-style projections to specify which fields to return (optional)

        Returns:
            GetDocumentsResponse with matching documents

        Raises:
            ValueError: If filters is empty
        """
        if not filters:
            return GetDocumentsResponse(
                success=False, message="Filters are required to search documents"
            )

        # Initialize projections
        projections = projections or {}

        # Build the base query to exclude deleted documents
        # base_query = {"deleted": {"$ne": True}}

        # Merge user filters with base query
        merged_query: Dict[str, Any] = {**filters}

        merged_query["user_id"] = get_operation_user_id()
        with self._db_lock:
            with get_db_session() as db:
                # Search documents with merged filters
                # Note: find(query, {}) and find(query) are identical, so always pass projections
                documents = list(db[Config.DOCUMENTS_COLLECTION].find(merged_query, projections))
        merged_query.pop("user_id", None)

        # If projections are used, return raw data; otherwise convert to Pydantic models
        if projections:
            documents_data: List[Union[Document, Dict[str, Any]]] = cast(
                List[Union[Document, Dict[str, Any]]], documents
            )
        else:
            documents_data = cast(
                List[Union[Document, Dict[str, Any]]],
                [Document(**document) for document in documents],
            )

        logger.info(f"Found {len(documents_data)} documents matching the search criteria")

        return GetDocumentsResponse(
            documents=documents_data,
            found_count=len(documents_data),
            missing_count=0,
            missing_doc_ids=[],
        )

    def download_documents_batch(self, filters: Dict[str, Any]) -> tuple[bytes, str]:
        """
        Download multiple documents as a zip file with MongoDB-style filters.

        Args:
            filters: MongoDB-style filters to select documents (required)
                     Examples:
                     - Batch retrieval: {'_id': {'$in': ['doc_1', 'doc_2']}}
                     - Filter by type: {'content_type': 'pdf'}
                     - Complex: {'kb_id': 'kb_123', 'content_type': {'$in': ['pdf', 'txt']}}

        Returns:
            Tuple of (zip_file_content: bytes, filename: str)

        Raises:
            ValueError: If filters are empty or no documents found
            IOError: If file cannot be read from disk
        """
        if not filters:
            raise ValueError("Filters are required to download documents")

        # Build the base query to exclude deleted documents
        # base_query = {"deleted": {"$ne": True}}

        # Merge user filters with base query
        merged_query: Dict[str, Any] = {**filters}

        merged_query["user_id"] = get_operation_user_id()
        with self._db_lock:
            with get_db_session() as db:
                # Get all documents matching the filters
                documents = list(db[Config.DOCUMENTS_COLLECTION].find(merged_query))
        merged_query.pop("user_id", None)

        if not documents:
            raise ValueError("No documents found matching the provided filters")

        # Use model_construct for DB-sourced data (skips validation)
        doc_models = [Document(**doc) for doc in documents]

        # Create a zip file in memory
        zip_buffer = io.BytesIO()
        missing_docs: List[str] = []

        try:
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                with self._db_lock:
                    with get_db_session() as db:
                        for doc in doc_models:
                            if not doc.content_id:
                                logger.warning(f"Document {doc.doc_id} has no content_id, skipping")
                                missing_docs.append(doc.doc_id)
                                continue

                            # Get content metadata to find the file path
                            content_entry = db[Config.DOCUMENT_CONTENTS_COLLECTION].find_one(
                                {
                                    "_id": doc.content_id,
                                    "user_id": get_operation_user_id(),
                                }
                            )

                            if not content_entry:
                                logger.warning(
                                    f"Content {doc.content_id} not found for document {doc.doc_id}, skipping"
                                )
                                missing_docs.append(doc.doc_id)
                                continue

                            content_model = Content(**content_entry)
                            content_path = content_model.content_path

                            try:
                                if self.use_s3:
                                    # Download from S3 using context manager for proper resource cleanup
                                    s3_key = f"documents/{content_path}"
                                    file_content = None
                                    with S3Service() as s3_service:
                                        file_content = s3_service.download_file(s3_key)

                                    if file_content is None:
                                        logger.warning(f"File not found in S3: {s3_key}, skipping")
                                        missing_docs.append(doc.doc_id)
                                        continue

                                    # Add file to zip from memory
                                    zip_file.writestr(doc.doc_name, file_content)
                                    logger.debug(f"Added {doc.doc_name} to zip from S3")
                                else:
                                    # Read from local disk (backward compatibility)
                                    if os.path.isabs(content_path):
                                        file_path = content_path
                                    else:
                                        file_path = os.path.join(self.docs_dir, content_path)

                                    if not os.path.exists(file_path):
                                        logger.warning(f"File not found at {file_path}, skipping")
                                        missing_docs.append(doc.doc_id)
                                        continue

                                    # Add file to zip from disk
                                    zip_file.write(file_path, arcname=doc.doc_name)
                                    logger.debug(f"Added {doc.doc_name} to zip from disk")

                            except IOError as e:
                                logger.warning(f"Failed to read document {doc.doc_id}: {str(e)}")
                                missing_docs.append(doc.doc_id)
                                continue

            zip_buffer.seek(0)
            zip_content = zip_buffer.getvalue()

            if len(doc_models) - len(missing_docs) == 0:
                raise ValueError("Failed to download any documents")

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"documents_{timestamp}.zip"

            logger.info(
                f"Created zip with {len(doc_models) - len(missing_docs)} documents, "
                f"{len(missing_docs)} failed ({zip_content.__sizeof__()} bytes)"
            )

            return zip_content, filename

        except Exception as e:
            logger.error(f"Error creating zip file: {str(e)}")
            raise

    def _create_database_indexes(self) -> None:
        """Create MongoDB indexes for collision-safe deduplication."""
        try:
            with get_db_session() as db:
                # Unique constraint on (user_id, content_hash, variant_id)
                # Ensures each (user, hash, variant) combination is unique
                db[Config.DOCUMENT_CONTENTS_COLLECTION].create_index(
                    [("user_id", 1), ("content_hash", 1), ("variant_id", 1)],
                    unique=True,
                    name="unique_user_hash_variant",
                    background=True,
                )

                # Lookup index on (user_id, content_hash)
                # Used for fast collision detection queries
                db[Config.DOCUMENT_CONTENTS_COLLECTION].create_index(
                    [("user_id", 1), ("content_hash", 1)],
                    name="lookup_user_hash",
                    background=True,
                )

                logger.debug("Database indexes created for collision-safe deduplication")
        except Exception as e:
            # Index creation failures are non-fatal (may already exist)
            logger.warning(f"Could not create database indexes: {str(e)}")

    def _get_extension_from_mime(self, mime_type: str) -> str:
        """
        Get file extension from MIME type.

        Args:
            mime_type: MIME type (e.g., "application/pdf", "application/msword")

        Returns:
            Extension with dot (e.g., ".pdf", ".docx")
        """
        mime_to_ext = {
            "application/pdf": ".pdf",
            "text/plain": ".txt",
            "text/html": ".html",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "application/vnd.ms-excel": ".xls",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
            "application/vnd.ms-powerpoint": ".ppt",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
            "application/json": ".json",
            "application/xml": ".xml",
            "text/csv": ".csv",
            "text/markdown": ".md",
        }
        return mime_to_ext.get(mime_type, "")

    def _get_storage_path(self, content_hash: str, variant_id: int = 0, extension: str = "") -> str:
        """
        Generate hierarchical storage path for content file WITH extension.

        Args:
            content_hash: SHA-256 hash (64 chars)
            variant_id: 0 for normal, 1+ for collision variants
            extension: File extension (e.g., ".pdf", ".docx")

        Returns:
            Hierarchical path: ab/cd/hash.ext or ab/cd/hash_vN.ext
        """
        # Extract first 2 chars and next 2 chars from hash for directory structure
        prefix1 = content_hash[:2]
        prefix2 = content_hash[2:4]

        # Create filename: hash.ext (v0) or hash_vN.ext (collision)
        if variant_id == 0:
            filename = f"{content_hash}{extension}"
        else:
            filename = f"{content_hash}_v{variant_id}{extension}"

        storage_path = os.path.join(prefix1, prefix2, filename)
        logger.debug(
            f"Generated storage path with extension: {storage_path} (hash={content_hash[:8]}..., variant={variant_id}, ext='{extension}')"
        )
        return storage_path

    def _verify_content_match(
        self, existing_path: str, new_content: bytes, existing_size: int
    ) -> bool:
        """
        Verify if two files are byte-identical (collision detection).

        Args:
            existing_path: Storage path of existing file (relative to docs_dir)
            new_content: New content bytes
            existing_size: Size of existing file

        Returns:
            True if files are identical, False if collision detected
        """
        # Quick size check (O(1))
        if existing_size != len(new_content):
            return False

        # Byte-level streaming comparison (O(n), O(1) memory)
        full_path = os.path.join(self.docs_dir, existing_path)

        # Verify file exists
        if not os.path.exists(full_path):
            logger.warning(f"Expected file not found: {full_path}")
            return False

        CHUNK_SIZE = 8 * 1024 * 1024  # 8MB chunks for memory efficiency

        try:
            with open(full_path, "rb") as existing_file:
                new_offset = 0
                while True:
                    existing_chunk = existing_file.read(CHUNK_SIZE)
                    new_chunk = new_content[new_offset : new_offset + CHUNK_SIZE]

                    # Compare chunks
                    if existing_chunk != new_chunk:
                        logger.debug(f"Content mismatch detected at offset {new_offset}")
                        return False  # Collision detected

                    # End of file check
                    if not existing_chunk:
                        break

                    new_offset += CHUNK_SIZE

            return True  # Files are identical

        except IOError as e:
            logger.error(f"Error verifying content match: {str(e)}")
            return False

    def _find_existing_document(
        self, content_hash: str, doc_name: str, source: Optional[str]
    ) -> Optional[str]:
        """
        Find existing document with matching content_hash + doc_name + source (document deduplication).

        Returns: doc_id if found, None if new document

        Logic: If doc_name AND source match an existing document with same content,
        it's the same document - reuse its ID.
        """
        with self._db_lock:
            with get_db_session() as db:
                # Query: Find document with same name, source, and content_hash
                existing_doc = db[Config.DOCUMENTS_COLLECTION].find_one(
                    {
                        "user_id": get_operation_user_id(),
                        "doc_name": doc_name,
                        "source": source,
                        # "deleted": {"$ne": True}
                    },
                    projection={"content_id": 1, "_id": 1},
                )

                if existing_doc:
                    # Verify the content_id has matching hash
                    content_entry = db[Config.DOCUMENT_CONTENTS_COLLECTION].find_one(
                        {
                            "_id": existing_doc.get("content_id"),
                            "user_id": get_operation_user_id(),
                            "content_hash": content_hash,
                        }
                    )
                    if content_entry:
                        logger.info(
                            f"Document deduplicated: name={doc_name}, source={source}, "
                            f"existing_id={existing_doc['_id']}"
                        )
                        return existing_doc["_id"]

        return None

    def _find_or_create_content(
        self,
        content_hash: str,
        content: bytes,
        created_at: datetime,
        mime_type: Optional[str] = None,
        doc_name: Optional[str] = None,
    ) -> tuple[str, str, int, bool]:
        """
        Find existing content or create new one with collision handling.

        Implements paranoid-correct deduplication:
        1. SHA-256 is primary key for deduplication
        2. On hash collision, verify byte-level equality before deduplication
        3. If true collision, assign new variant_id

        Args:
            content_hash: SHA-256 hash of content
            content: Content bytes
            created_at: Creation timestamp
            mime_type: MIME type (e.g., "application/pdf")
            doc_name: Original filename (used to extract extension)

        Returns:
            Tuple of (content_id, storage_path, variant_id, is_new_content)
        """
        # Extract file extension - prefer filename over MIME type
        extension = ""
        if doc_name:
            _, ext = os.path.splitext(doc_name)
            extension = ext.lower() if ext else ""
            logger.debug(f"Extracted extension from filename '{doc_name}': '{extension}'")

        # Fallback to MIME type if filename has no extension
        if not extension and mime_type:
            extension = self._get_extension_from_mime(mime_type)
            logger.debug(f"Using MIME type extension for mime_type '{mime_type}': '{extension}'")

        user_id = get_operation_user_id()

        with self._db_lock:
            with get_db_session() as db:
                # Step 1: Find existing content with same hash (user-scoped query)
                # NOTE: Include deleted entries too - we can't reuse a deleted content's variant_id
                # because the unique index (user_id, content_hash, variant_id) prevents duplicate keys
                existing = list(
                    db[Config.DOCUMENT_CONTENTS_COLLECTION].find(
                        {"user_id": user_id, "content_hash": content_hash}
                    )
                )

                # Step 2: No existing content - create new with variant_id=0
                if not existing:
                    content_id = generate_content_id()
                    variant_id = 0
                    storage_path = self._get_storage_path(content_hash, variant_id, extension)
                    logger.debug(
                        f"New content: hash={content_hash}, id={content_id}, variant={variant_id}"
                    )
                    return (content_id, storage_path, variant_id, True)  # is_new=True

                # Step 3: Check each existing variant for byte-level match (only active entries)
                for existing_entry in existing:
                    existing_model = Content(**existing_entry)

                    # Skip soft-deleted entries - create new variant instead
                    # if existing_model.deleted:
                    #     continue

                    # Quick size check (reject obvious mismatches)
                    if existing_model.content_size != len(content):
                        continue

                    # Byte-level verification
                    if self._verify_content_match(
                        existing_model.content_path,
                        content,
                        existing_model.content_size,
                    ):
                        # MATCH FOUND - deduplicate with active entry
                        logger.info(
                            f"Content deduplicated: hash={content_hash}, "
                            f"existing_id={existing_model.content_id}, variant={existing_model.variant_id}"
                        )
                        return (
                            existing_model.content_id,
                            existing_model.content_path,
                            existing_model.variant_id,
                            False,
                        )  # is_new=False

                # Step 4: COLLISION DETECTED - no byte-match found for this hash
                # Assign new variant_id and create separate storage
                # Count ALL entries (including soft-deleted) to never reuse a variant_id
                max_variant = max((e.get("variant_id", 0) for e in existing), default=0)
                new_variant_id = max_variant + 1

                # Log collision event (security audit trail)
                logger.warning(
                    f"SHA-256 collision detected: hash={content_hash}, "
                    f"variant={new_variant_id}, user={user_id}"
                )

                # Create new content entry with incremented variant
                content_id = generate_content_id()
                storage_path = self._get_storage_path(content_hash, new_variant_id, extension)

                return (
                    content_id,
                    storage_path,
                    new_variant_id,
                    True,
                )  # is_new=True (collision variant)

    def _process_documents_for_upload(
        self, docs: List[Doc], created_at: datetime
    ) -> tuple[List[Dict[str, Any]], List[Content], List[Document], List[str], List[str]]:
        """
        Process documents in single pass: generate IDs, resolve deduplication, create models.

        Eliminates dict intermediary - goes directly from input docs to Pydantic models.

        Returns: (new_content_to_write, content_entries, doc_entries, content_ids, doc_ids)
        """
        new_content_to_write: List[Dict[str, Any]] = []
        content_entries: List[Content] = []
        doc_entries: List[Document] = []
        content_ids: List[str] = []
        doc_ids: List[str] = []
        processed_hashes: Dict[str, tuple[str, str, int, bool]] = (
            {}
        )  # hash -> (content_id, storage_path, variant_id)

        for doc in docs:
            if not doc.doc_name or not doc.content:
                logger.warning(f"Skipping invalid document: {getattr(doc, 'doc_name', 'unknown')}")
                continue

            # Generate IDs
            doc_id = generate_document_id()
            content_bytes = doc.content
            content_hash = sha256_hex(content_bytes)

            # Resolve content with collision-safe deduplication
            if content_hash not in processed_hashes:
                resolved_content_id, storage_path, variant_id, is_new = (
                    self._find_or_create_content(
                        content_hash, content_bytes, created_at, doc.content_type, doc.doc_name
                    )
                )
                processed_hashes[content_hash] = (
                    resolved_content_id,
                    storage_path,
                    variant_id,
                    is_new,
                )
            else:
                resolved_content_id, storage_path, variant_id, is_new = processed_hashes[
                    content_hash
                ]

            # Track content ID (always)
            content_ids.append(resolved_content_id)

            # Only write file and create Content entry if it's NEW content (not deduplicated)
            if is_new:
                new_content_to_write.append(
                    {
                        "content_id": resolved_content_id,
                        "storage_path": storage_path,
                        "content": content_bytes,
                        "content_type": doc.content_type,
                    }
                )

                content_entries.append(
                    Content(
                        _id=resolved_content_id,
                        content_hash=content_hash,
                        variant_id=variant_id,
                        content_path=storage_path,
                        storage_hash=content_hash,
                        mime_type=doc.content_type,
                        content_size=len(content_bytes),
                        ref_count=1,
                        created_at=created_at,
                    )
                )

            # Document-level deduplication: Check if same doc (name + source + content) exists
            existing_doc_id = self._find_existing_document(content_hash, doc.doc_name, doc.source)
            if existing_doc_id:
                # Reuse existing document ID
                doc_id = existing_doc_id
                is_new_doc = False
            else:
                # New document - use generated UUID4
                is_new_doc = True

            # Track the finalized doc_id (either new or existing)
            doc_ids.append(doc_id)

            # Only create Document entry if it's NEW (not already in DB)
            if is_new_doc:
                doc_entries.append(
                    Document(
                        _id=doc_id,
                        content_id=resolved_content_id,
                        uploaded_at=created_at,
                        doc_name=doc.doc_name,
                        content_type=doc.content_type,
                        doc_size=len(content_bytes),
                        source=doc.source,
                    )
                )

        return new_content_to_write, content_entries, doc_entries, content_ids, doc_ids

    def _fetch_existing_documents(self, kb_id: str, content_ids: List[str], doc_ids: List[str]):
        """Fetch existing non-deleted documents and current KB doc_ids."""
        with self._db_lock:
            with get_db_session() as db:
                kb_entry = db[Config.KNOWLEDGE_BASES_COLLECTION].find_one(
                    {"_id": kb_id, "user_id": get_operation_user_id()}
                )
                current_kb_doc_ids: Set[str] = (
                    set(kb_entry.get("doc_ids", [])) if kb_entry else set()
                )

                existing_content_ids = set(
                    doc["_id"]
                    for doc in db[Config.DOCUMENT_CONTENTS_COLLECTION].find(
                        {
                            "_id": {"$in": content_ids},
                            "user_id": get_operation_user_id(),
                        },
                        {"_id": 1},
                    )
                )
                existing_doc_ids = set(
                    # doc["_id"] for doc in db[Config.DOCUMENTS_COLLECTION].find(
                    #     {"_id": {"$in": doc_ids}, "deleted": {"$ne": True}, "user_id": get_operation_user_id()},
                    #     {"_id": 1}
                    # )
                    doc["_id"]
                    for doc in db[Config.DOCUMENTS_COLLECTION].find(
                        {"_id": {"$in": doc_ids}, "user_id": get_operation_user_id()},
                        {"_id": 1},
                    )
                )
        return existing_content_ids, existing_doc_ids, current_kb_doc_ids

    def _write_files_in_parallel(self, new_content_to_write: List[Dict[str, Any]]):
        """Write files in parallel with thread-safe operations using hierarchical storage."""
        file_write_futures: List[Future[None]] = []
        if new_content_to_write:
            for item in new_content_to_write:
                future = executor.submit(
                    self._write_document_file_thread_safe,
                    item["storage_path"],
                    item["content"],
                    item.get("content_type", "application/octet-stream"),
                )
                file_write_futures.append(future)

            for future in as_completed(file_write_futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"File write failed: {str(e)}")
                    raise

    def _insert_documents_to_db(self, content_models: List[Content], doc_models: List[Document]):
        """Insert documents to database with error handling."""
        with self._db_lock:
            try:
                with get_db_session() as db:
                    if content_models:
                        try:
                            db[Config.DOCUMENT_CONTENTS_COLLECTION].insert_many(
                                [
                                    content.model_dump(by_alias=True, exclude_none=True)
                                    for content in content_models
                                ],
                                ordered=False,
                            )
                        except DuplicateKeyError:
                            logger.warning("Some content entries already exist (race condition)")

                    if doc_models:
                        try:
                            db[Config.DOCUMENTS_COLLECTION].insert_many(
                                [
                                    doc.model_dump(by_alias=True, exclude_none=True)
                                    for doc in doc_models
                                ],
                                ordered=False,
                            )
                        except DuplicateKeyError:
                            logger.warning("Some document entries already exist (race condition)")
            except Exception as e:
                logger.error(f"Error inserting documents: {str(e)}")
                raise

    def _write_document_file_thread_safe(
        self, storage_path: str, content: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        """
        Thread-safe helper method to write document file to storage (S3 or local).

        Args:
            storage_path: Relative path from docs_dir (e.g., "ab/cd/hash.pdf" or "ab/cd/hash_v1.pdf")
            content: File content bytes
            content_type: MIME type of the document (e.g., "application/pdf", "text/plain")
        """
        logger.debug(
            f"Writing document to storage: {storage_path} (size={len(content)} bytes, type={content_type})"
        )
        with self._file_write_lock:
            try:
                if self.use_s3:
                    # Upload to S3 using context manager for proper resource cleanup
                    with S3Service() as s3_service:
                        s3_url = s3_service.upload_file(
                            file_content=content,
                            s3_key=f"documents/{storage_path}",
                            content_type=content_type,
                        )
                        if not s3_url:
                            raise IOError(f"Failed to upload document to S3: {storage_path}")
                        logger.debug(
                            f"Successfully wrote document to S3: {storage_path} (type: {content_type})"
                        )
                else:
                    # Write to local disk (fallback)
                    full_path = os.path.join(self.docs_dir, storage_path)
                    dir_path = os.path.dirname(full_path)
                    os.makedirs(dir_path, exist_ok=True)

                    with open(full_path, "wb") as f:
                        f.write(content)
                    logger.debug(f"Successfully wrote document to local disk: {storage_path}")

            except IOError as e:
                logger.error(f"Failed to write document to {storage_path}: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error writing document to {storage_path}: {str(e)}")
                raise IOError(f"Failed to write document: {str(e)}")

    def _index_documents_background(self, kb_id: str, doc_ids: List[str]) -> None:
        """
        Background task to index documents for a knowledge base.
        Called via executor.submit() for non-blocking operation.

        Performs:
        1. Chunking of documents if not already chunked
        2. Storing chunks in ChromaDB
        3. Updating KB status to COMPLETED or FAILED

        Args:
            kb_id: Knowledge base ID
            doc_ids: List of document IDs to index
            task_id: Task ID for tracking
        """
        update_operation_status(TaskStatus.PROCESSING)
        try:
            logger.info(
                f"Starting background indexing task for {len(doc_ids)} documents in KB {kb_id}"
            )

            # Call the RAG index manager to perform chunking and indexing
            # This handles all the heavy lifting: chunking, storage, and status updates
            self.rag_index_manager.build_index(kb_id)

            logger.info(f"Successfully completed background indexing task for KB {kb_id}")

            update_operation_status(TaskStatus.COMPLETED)

        except Exception as e:
            logger.error(
                f"Error in background indexing task for KB {kb_id}: {str(e)}",
                exc_info=True,
            )
            # Status is already updated to FAILED by rag_index_manager.build_index() error handling
            try:
                update_operation_metadata(
                    {
                        "$set": {
                            "error": f"Indexing failed: {str(e)}",
                        }
                    }
                )
            except Exception as meta_error:
                logger.error(f"Failed to update operation metadata: {str(meta_error)}")

            update_operation_status(TaskStatus.FAILED)

    def _index_chunks_background(self, kb_id: str, chunks: List[Chunk]) -> None:
        """
        Background task to index pre-chunked data for a knowledge base.
        Called via executor.submit() for non-blocking operation.

        Performs:
        1. Storing chunks in ChromaDB
        2. Building knowledge base index from chunks
        3. Updating KB status to COMPLETED or FAILED

        Args:
            kb_id: Knowledge base ID
            chunks: List of pre-chunked Chunk objects
            task_id: Task ID for tracking
        """
        update_operation_status(TaskStatus.PROCESSING)
        try:
            logger.info(
                f"Starting background chunk indexing task for {len(chunks)} chunks in KB {kb_id}"
            )

            # Call the RAG index manager to store chunks and build index
            # This handles chunk storage in ChromaDB and knowledge base generation
            self.rag_index_manager.build_index_from_chunks(kb_id, chunks)

            logger.info(
                f"Successfully completed background chunk indexing task for KB {kb_id}. "
                f"Knowledge base has been built."
            )

            update_operation_status(TaskStatus.COMPLETED)

        except Exception as e:
            logger.error(
                f"Error in background chunk indexing task for KB {kb_id}: {str(e)}",
                exc_info=True,
            )
            # Update KB status to FAILED
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

            # Update operation metadata with error
            try:
                update_operation_metadata(
                    {
                        "$set": {
                            "error": f"Chunk indexing failed: {str(e)}",
                        }
                    }
                )
            except Exception as meta_error:
                logger.error(f"Failed to update operation metadata: {str(meta_error)}")

            update_operation_status(TaskStatus.FAILED)
