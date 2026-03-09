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
from typing import Dict, List, AsyncGenerator, Union, Optional, Any, cast
import time
import threading
from datetime import datetime, timezone, timedelta
import base64

from ....infrastructure.database import get_db_session
from ....infrastructure.utils import (
    detect_file_type,
    EXTENSION_TO_LANGUAGE,
    text_to_markdown,
    office_document_to_pdf,
)
from ....config import Config
from ..._agents import MainAgent
from ...models.agent.message_models import (
    MessageStart,
    MessageStop,
    MessageDelta,
)
from ...models.agent import Settings
from ....infrastructure.operation_logging import (
    update_operation_status,
    update_operation_metadata,
    get_operation_user_id,
)
from ...models.operation_audit import TaskStatus
from ...models.agent.delta_models import (
    ContentBlockStart,
    ContentBlockDelta,
    ContentBlockStop,
)
from ...models.response_models import (
    CreateConversationResponse,
    ListConversationsResponse,
    EditConversationFileResponse,
    ListConversationFilesResponse,
    GetConversationHistoryResponse,
    GetConversationResponse,
    DeleteConversationResponse,
    ModifyConversationSettingsResponse,
    PreviewFileResponse,
    BranchConversationResponse,
)
from ...models.core_models import Conversation
from ...models._request_models import ConversationRequest
from ....infrastructure.ids import generate_chat_id
from ....log_creator import get_file_logger

logger = get_file_logger()

# Session cleanup configuration
SESSION_CLEANUP_INTERVAL = timedelta(minutes=5)  # Check every 5 minutes (300 seconds)
SESSION_INACTIVITY_TIMEOUT = timedelta(
    hours=1
)  # Offload conversations inactive for 1 hour (3600 seconds)


class ConversationManager:
    """Manages conversation and chat conversation operations."""

    def __init__(self):
        self.conversation_instances: Dict[str, MainAgent] = {}
        # Use RLock for safety - allows same thread to reacquire without deadlock
        self.conversation_cleanup_lock = threading.RLock()

        # Per-conversation locks to serialize conversation operations and prevent concurrent message interleaving
        # CONCURRENT REQUEST FIX: Ensures only one request processes per conversation at a time
        # Use RLock for defensive programming - prevents deadlock if internal code reacquires lock
        self.conversation_locks: Dict[str, threading.RLock] = {}

        self.cleanup_thread = None
        self.cleanup_shutdown = threading.Event()

        # Start conversation cleanup background task
        self._start_conversation_cleanup()

        logger.info("Initialized Conversation Session Manager with conversation cleanup enabled")

    def _start_conversation_cleanup(self):
        """Start the background thread for conversation cleanup"""

        def cleanup_worker():
            logger.info(
                f"[Session Cleanup] Started background cleanup thread (interval: {SESSION_CLEANUP_INTERVAL}s, timeout: {SESSION_INACTIVITY_TIMEOUT}s)"
            )
            while not self.cleanup_shutdown.is_set():
                try:
                    time.sleep(SESSION_CLEANUP_INTERVAL.seconds)
                    if not self.cleanup_shutdown.is_set():
                        self._cleanup_inactive_conversations()
                except Exception as e:
                    logger.error(f"[Session Cleanup] Error in cleanup loop: {e}")

        self.cleanup_thread = threading.Thread(target=cleanup_worker, daemon=True)
        self.cleanup_thread.start()

    def _cleanup_inactive_conversations(self):
        """Offload conversations that have been inactive for more than 1 hour"""
        try:
            current_time = datetime.now(timezone.utc)
            conversations_to_offload: List[tuple[str, timedelta]] = []

            with self.conversation_cleanup_lock:
                for conversation_id, conversation_agent in list(
                    self.conversation_instances.items()
                ):
                    try:
                        # Get last accessed timestamp (stored as ISO string)
                        last_accessed_at = conversation_agent.conversation_model.last_accessed_at

                        # Ensure timezone-aware comparison (MongoDB stores naive datetimes)
                        if last_accessed_at:
                            # Handle naive datetime from MongoDB
                            if (
                                hasattr(last_accessed_at, "tzinfo")
                                and last_accessed_at.tzinfo is None
                            ):
                                last_accessed_at = last_accessed_at.replace(tzinfo=timezone.utc)

                            # Calculate inactivity duration
                            inactivity_duration = current_time - last_accessed_at
                        else:
                            inactivity_duration = SESSION_INACTIVITY_TIMEOUT

                        # Mark conversation for offloading if inactive for more than 1 hour
                        if inactivity_duration >= SESSION_INACTIVITY_TIMEOUT:
                            conversations_to_offload.append((conversation_id, inactivity_duration))
                    except Exception as conv_error:
                        logger.warning(
                            f"[Session Cleanup] Error checking conversation {conversation_id}: {conv_error}"
                        )

            # Offload inactive conversations
            for conversation_id, inactivity_duration in conversations_to_offload:
                self._offload_conversation(conversation_id, inactivity_duration)

        except Exception as e:
            logger.error(f"[Session Cleanup] Error during cleanup: {e}")

    def _offload_conversation(self, conversation_id: str, inactivity_duration: timedelta):
        """Remove conversation from memory cache, keeping agent for quick reload"""
        try:
            with self.conversation_cleanup_lock:
                if conversation_id in self.conversation_instances:
                    logger.info(
                        f"[Session Cleanup] Offloaded conversation {conversation_id} (inactive for {inactivity_duration.seconds/3600:.1f}h) - "
                        f"Will reload from storage on next access"
                    )
                    # Remove the conversation from memory to free resources
                    # Agent will be recreated on next access if needed
                    del self.conversation_instances[conversation_id]
        except Exception as e:
            logger.error(f"[Session Cleanup] Error offloading conversation {conversation_id}: {e}")

    def shutdown_conversation_cleanup(self):
        """Shutdown the conversation cleanup thread gracefully"""
        if self.cleanup_shutdown and self.cleanup_thread:
            self.cleanup_shutdown.set()
            self.cleanup_thread.join(timeout=5)
            logger.info("[Session Cleanup] Cleanup thread stopped")

    def _get_conversation_lock(self, conversation_id: str) -> threading.RLock:
        """
        Get or create a per-conversation lock for serializing conversation operations.

        CONCURRENT REQUEST FIX: This ensures that only one request to the same conversation
        can be processed at a time, preventing out-of-order message interleaving in
        conversation history.

        Args:
            conversation_id: Session identifier

        Returns:
            threading.RLock for this conversation (reentrant lock for safety)
        """
        with self.conversation_cleanup_lock:
            if conversation_id not in self.conversation_locks:
                self.conversation_locks[conversation_id] = threading.RLock()
            return self.conversation_locks[conversation_id]

    def _cleanup_conversation_lock(self, conversation_id: str):
        """Remove conversation lock when conversation is deleted to prevent memory leak"""
        with self.conversation_cleanup_lock:
            self.conversation_locks.pop(conversation_id, None)

    def create_conversation_session(
        self,
        name: Optional[str] = None,
        kb_ids: List[str] = [],
        user_instructions: Optional[str] = None,
        settings: Settings = Settings(),
    ) -> CreateConversationResponse:
        """
        Create a conversation session.

        Args:
            kb_ids:
            name: Optional name for the conversation session
            user_instructions: Optional instructions/context for the conversation
            settings: Optional conversation settings

        Returns:
            Conversation session entry with conversation_id

        Raises:
        """
        update_operation_status(TaskStatus.PROCESSING)

        # Generate session ID and timestamps
        conversation_id = generate_chat_id()

        conversation_model = Conversation(
            _id=conversation_id,
            name=name,
            kb_ids=kb_ids,
            user_instructions=user_instructions,
            settings=settings,
        )

        # Store session in database
        with get_db_session() as db:
            db[Config.CONVERSATIONS_COLLECTION].insert_one(
                conversation_model.model_dump(by_alias=True, exclude_none=True)
            )

        logger.info(f"Created conversation session {conversation_id}")

        update_operation_metadata(
            {
                "$addToSet": {
                    "kb_ids": kb_ids,
                    "conversation_id": conversation_id,
                }
            }
        )

        return CreateConversationResponse(
            message="Conversation session created successfully",
            conversation_id=conversation_id,
            kb_ids=kb_ids,
            created_at=conversation_model.created_at,
        )

    def list_conversations(
        self,
        filters: Optional[Dict[str, Any]] = None,
        projections: Optional[Dict[str, Any]] = None,
    ) -> ListConversationsResponse:
        """List conversation sessions with MongoDB-style filters and projections.

        When projections are used, returns raw dictionary data. Otherwise returns Pydantic models.

        Args:
            filters: MongoDB-style filters to apply (e.g., {'kb_ids': ['kb_123']} to filter)
            projections: MongoDB-style projections to specify which fields to return

        Returns:
            List of conversation session entries (as models or raw data depending on projections)
        """
        update_operation_status(TaskStatus.PROCESSING)

        # Build base filter
        base_filter: Dict[str, Any] = {}

        # Merge user filters with base filter
        filters = filters or {}
        merged_filter = {**base_filter, **filters}

        projections = projections or {}
        merged_filter["user_id"] = get_operation_user_id()

        with get_db_session() as db:
            if projections:
                conversations = list(
                    db[Config.CONVERSATIONS_COLLECTION].find(merged_filter, projections)
                )
            else:
                conversations = list(db[Config.CONVERSATIONS_COLLECTION].find(merged_filter))

        # If projections are used, return raw data; otherwise convert to Pydantic models
        if projections:
            conversation_data: List[Union[Conversation, Dict[str, Any]]] = cast(
                List[Union[Conversation, Dict[str, Any]]], conversations
            )
        else:
            # Use regular constructor for proper nested Settings object construction
            conv_models = [Conversation(**conv) for conv in conversations]
            conversation_data = cast(List[Union[Conversation, Dict[str, Any]]], conv_models)

        logger.info(f"Listed {len(conversations)} conversations")

        # Update operation metadata if kb_ids is in filters
        if filters and "kb_ids" in filters:
            update_operation_metadata({"$addToSet": {"kb_ids": filters["kb_ids"]}})

        merged_filter.pop("user_id", None)

        return ListConversationsResponse(
            conversations=conversation_data,
            count=len(conversation_data),
            filters=merged_filter,
        )

    def get_conversation_history(
        self, conversation_id: str, offset: int = 0, limit: int = 100
    ) -> GetConversationHistoryResponse:
        """
        Get conversation history.

        Args:
            chat_id: Conversation/chat ID
            limit: Maximum number of messages to retrieve

        Returns:
            List of conversation messages
        """
        update_operation_status(TaskStatus.PROCESSING)
        result = self.get_conversation(conversation_id)
        if not result.success or not result.conversation:
            return GetConversationHistoryResponse(success=False, message=result.message)
        update_operation_metadata({"$addToSet": {"conversation_ids": [conversation_id]}})
        return GetConversationHistoryResponse(
            conversation=result.conversation,
            conversation_history=self.conversation_instances[
                conversation_id
            ].conversation_manager.get_user_history(offset, limit),
        )

    def get_conversation(self, conversation_id: str) -> GetConversationResponse:
        update_operation_status(TaskStatus.PROCESSING)

        # if conversation_id.startswith("[DELETED]"):
        #     with get_db_session() as db:
        #         conversation_entry = db[Config.CONVERSATIONS_COLLECTION].find_one(
        #             {"_id": conversation_id, "user_id": get_operation_user_id()}
        #         )

        #     if not conversation_entry:
        #         return GetConversationResponse(
        #             success=False,
        #             message=f"Conversation with ID {conversation_id} does not exist"
        #         )

        #     return GetConversationResponse(
        #         conversation=Conversation(**conversation_entry)
        #     )

        last_accessed_at = datetime.now(timezone.utc)

        # RACE CONDITION FIX: Use lock to protect cache access and updates
        with self.conversation_cleanup_lock:
            conversation_instance = self.conversation_instances.get(conversation_id)
            if conversation_instance:
                self.conversation_instances[conversation_id].conversation_model.last_accessed_at = (
                    last_accessed_at
                )

            if not conversation_instance:
                with get_db_session() as db:
                    # conversation_entry = db[Config.CONVERSATIONS_COLLECTION].find_one(
                    #     {"_id": conversation_id, "deleted": {"$ne": True}, "user_id": get_operation_user_id()}
                    # )
                    conversation_entry = db[Config.CONVERSATIONS_COLLECTION].find_one(
                        {"_id": conversation_id, "user_id": get_operation_user_id()}
                    )

                if not conversation_entry:
                    return GetConversationResponse(
                        success=False,
                        message=f"Conversation with ID {conversation_id} does not exist",
                    )

                conversation_model = Conversation(**conversation_entry)
                conversation_model.last_accessed_at = last_accessed_at
                self.conversation_instances[conversation_id] = MainAgent.load(
                    **conversation_model.model_dump(exclude_none=True)
                )

            with get_db_session() as db:
                db[Config.CONVERSATIONS_COLLECTION].update_one(
                    {"_id": conversation_id, "user_id": get_operation_user_id()},
                    {"$set": {"last_accessed_at": last_accessed_at}},
                )

        update_operation_metadata({"$addToSet": {"conversation_ids": [conversation_id]}})

        return GetConversationResponse(
            conversation=self.conversation_instances[conversation_id].conversation_model
        )

    def submit_converse_message(self, conversation_id: str, request: ConversationRequest) -> None:
        """
        Submit a message to a conversation's processing queue.

        This method validates the conversation exists and queues the message for processing.
        Use start_processing_messages() to begin processing queued messages.

        Args:
            conversation_id: Conversation ID
            request: ConverseRequest object containing conversation details
                - prompt: User message/question
                - parent_message_uuid: Parent message UUID for thread
                - attachments: Optional file attachments
                - personalized_styles: Optional styling preferences
            stop_request: If True, signals to stop processing after this message is handled

        Raises:
            Exception: If conversation not found or invalid
        """
        update_operation_status(TaskStatus.PROCESSING)
        result = self.get_conversation(conversation_id)
        if not result.success or not result.conversation:
            raise Exception(result.message)

        logger.info(f"Queued message for conversation {conversation_id}...")
        self.conversation_instances[conversation_id].submit_message(request)

    async def start_processing_messages(self, conversation_id: str) -> AsyncGenerator[
        Union[
            MessageStart,
            MessageDelta,
            MessageStop,
            ContentBlockStart,
            ContentBlockDelta,
            ContentBlockStop,
        ],
        None,
    ]:
        """
        Start processing queued messages for a conversation.

        Call submit_converse_message() one or more times before calling this method.
        This processes all queued messages and streams responses from the agent.

        Args:
            conversation_id: Conversation ID

        Yields:
            Response chunks from the agent as they stream
        """
        update_operation_status(TaskStatus.PROCESSING)
        result = self.get_conversation(conversation_id)
        if not result.success or not result.conversation:
            raise Exception(result.message)

        conversation_lock = self._get_conversation_lock(conversation_id)
        with conversation_lock:
            logger.info(f"Starting message processing for conversation {conversation_id}...")

            # Stream responses from agent
            async for response in self.conversation_instances[
                conversation_id
            ].process_user_messages():
                yield response

        update_operation_metadata({"$addToSet": {"conversation_ids": [conversation_id]}})

    async def converse(self, conversation_id: str, request: ConversationRequest) -> AsyncGenerator[
        Union[
            MessageStart,
            MessageDelta,
            MessageStop,
            ContentBlockStart,
            ContentBlockDelta,
            ContentBlockStop,
        ],
        None,
    ]:
        """
        DEPRECATED: Use submit_converse_message() + start_processing_messages() instead.

        Handle conversation/chat request using MainAgent (convenience method that combines
        submit and process operations).

        This method is maintained for backwards compatibility. New code should use:
        1. submit_converse_message(conversation_id, request)
        2. start_processing_messages(conversation_id)

        Args:
            request: ConverseRequest object containing conversation details
                - prompt: User message/question
                - parent_message_uuid: Parent message UUID for thread
                - attachments: Optional file attachments
                - personalized_styles: Optional styling preferences

        Yields:
            Response chunks from the agent as they stream
        """
        self.submit_converse_message(conversation_id, request)
        async for response in self.start_processing_messages(conversation_id):
            yield response

    def list_conversation_files(self, conversation_id: str) -> ListConversationFilesResponse:
        """
        List files associated with a conversation.

        Args:
            chat_id: Conversation/chat ID

        Returns:
            List of files in the conversation
        """
        update_operation_status(TaskStatus.PROCESSING)
        result = self.get_conversation(conversation_id)
        if not result.success or not result.conversation:
            return ListConversationFilesResponse(success=False, message=result.message)

        response_dict = self.conversation_instances[conversation_id].tool_manager.get_files()
        logger.info(
            f"Found {len(response_dict.get('files', []))} files in conversation {conversation_id}"
        )

        update_operation_metadata(
            {
                "$addToSet": {
                    "conversation_ids": [conversation_id],
                    "files": response_dict.get("files", []),
                },
                "$inc": {"files_count": len(response_dict.get("files", []))},
            }
        )

        return ListConversationFilesResponse(
            conversation_id=conversation_id,
            files=response_dict.get("files", []),
            files_metadata=response_dict.get("files_metadata", []),
        )

    def preview_conversation_file(
        self, conversation_id: str, file_path: str, version: Optional[int] = None
    ) -> PreviewFileResponse:

        file_content, filename, content_type = self.download_conversation_file(
            conversation_id, file_path, version
        )

        try:
            # Detect original file type using python-magic with file content
            file_type = detect_file_type(
                filename, content_type, file_content, EXTENSION_TO_LANGUAGE
            )

            # Process based on file type
            if file_type == "image":
                # Images: Return as base64
                preview_content_type = "image/base64"
                content = base64.b64encode(file_content).decode("utf-8")

            elif file_type == "pdf":
                # PDFs: Return as base64
                preview_content_type = "application/pdf"
                content = base64.b64encode(file_content).decode("utf-8")

            elif file_type in ["code", "text", "markdown"]:
                # Code, text, and markdown files: Convert to markdown format
                try:
                    text_content = file_content.decode("utf-8")

                    if file_type == "code":
                        # Code files: Wrap in code block with language
                        markdown_content = text_to_markdown(text_content, filename, is_code=True)
                    else:
                        # Text and markdown files: Return as-is
                        markdown_content = text_content

                    preview_content_type = "text/markdown"
                    content = markdown_content

                except UnicodeDecodeError:
                    # If UTF-8 decoding fails, return as base64
                    logger.warning(f"Failed to decode {filename} as UTF-8, returning as base64")
                    preview_content_type = "application/octet-stream"
                    content = base64.b64encode(file_content).decode("utf-8")

            elif file_type == "docx":
                # DOCX: Convert to PDF
                pdf_content = office_document_to_pdf(file_content, filename, "docx")
                preview_content_type = "application/pdf"
                content = base64.b64encode(pdf_content).decode("utf-8")

            elif file_type == "xlsx":
                # XLSX: Convert to PDF
                pdf_content = office_document_to_pdf(file_content, filename, "xlsx")
                preview_content_type = "application/pdf"
                content = base64.b64encode(pdf_content).decode("utf-8")

            elif file_type == "ppt":
                # PPTX: Convert to PDF
                pdf_content = office_document_to_pdf(file_content, filename, "ppt")
                preview_content_type = "application/pdf"
                content = base64.b64encode(pdf_content).decode("utf-8")

            else:
                # Unknown file type: Return as base64
                logger.warning(f"Unknown file type for {filename}, returning as base64")
                preview_content_type = "application/octet-stream"
                content = base64.b64encode(file_content).decode("utf-8")

            return PreviewFileResponse(
                filename=filename, preview_content_type=preview_content_type, content=content
            )

        except ValueError as e:
            return PreviewFileResponse(success=False, message="Unable to get the file!")
        except Exception as e:
            logger.error(f"Error previewing conversation file: {str(e)}", exc_info=True)
            return PreviewFileResponse(success=False, message=f"Error previewing conversation file")

    def download_conversation_file(
        self, conversation_id: str, file_path: str, version: Optional[int] = None
    ):
        """
        Download a file from a conversation.

        Args:
            conversation_id: Conversation/chat ID
            file_path: Path to the file within the conversation
            version: Optional file version to download. If None, downloads the latest version.

        Returns:
            File content as bytes
        """
        update_operation_status(TaskStatus.PROCESSING)
        result = self.get_conversation(conversation_id)
        if not result.success or not result.conversation:
            raise Exception(result.message)

        update_operation_metadata(
            {
                "$addToSet": {
                    "conversation_ids": [conversation_id],
                },
                "$set": {"file_path": file_path, "version": version},
            }
        )

        return self.conversation_instances[conversation_id].tool_manager.download_content(
            file_path, version=version
        )

        # # Build absolute file path
        # abs_file_path = os.path.join(self.docs_dir, chat_id, file_path)

        # # Security check: ensure file is within conversation directory
        # real_path = os.path.realpath(abs_file_path)
        # conv_dir = os.path.realpath(os.path.join(self.docs_dir, chat_id))
        # if not real_path.startswith(conv_dir):
        #     raise ValueError(f"Invalid file path: {file_path}")

        # if not os.path.exists(abs_file_path):
        #     raise ValueError(f"File not found: {file_path}")

        # # Read and return file content
        # with open(abs_file_path, "rb") as f:
        #     content = f.read()

        # logger.info(f"Downloaded file {file_path} from conversation {chat_id} ({len(content)} bytes)")
        # return content

    def modify_conversation_settings(
        self,
        conversation_id: str,
        name: Optional[str] = None,
        user_instructions: Optional[str] = None,
        settings_updates: Optional[Dict[str, Union[bool, str, int, float]]] = None,
    ) -> ModifyConversationSettingsResponse:
        """
        Modify conversation settings including name, user instructions, and individual settings keys.

        Args:
            conversation_id: Conversation ID to update
            name: Optional new conversation name
            user_instructions: Optional new user instructions
            settings_updates: Optional dict with individual settings keys to update
                            (e.g., {"enabled_extended_thinking": True})

        Returns:
            Response dict with success status and updated conversation data
        """
        update_operation_status(TaskStatus.PROCESSING)

        # Get or load conversation
        result = self.get_conversation(conversation_id)
        if not result.success or not result.conversation:
            return ModifyConversationSettingsResponse(
                success=False,
                message=result.message or f"Conversation {conversation_id} not found",
            )

        # Get conversation lock to prevent concurrent modifications
        conversation_lock = self._get_conversation_lock(conversation_id)

        with conversation_lock:
            try:
                conversation = self.conversation_instances[conversation_id].conversation_model

                # Update name if provided
                if name is not None:
                    conversation.name = name

                # Update user_instructions if provided
                if user_instructions is not None:
                    conversation.user_instructions = user_instructions

                # Update individual settings keys if provided
                if settings_updates:
                    settings_model_class = type(conversation.settings)
                    model_fields = settings_model_class.model_fields

                    for key, value in settings_updates.items():
                        if key not in model_fields:
                            valid_keys = list(model_fields.keys())
                            return ModifyConversationSettingsResponse(
                                success=False,
                                message=f"Invalid settings key: {key}. Valid keys are: {valid_keys}",
                            )

                        # Validate value type against field type
                        field_info = model_fields[key]
                        expected_type = field_info.annotation

                        if not isinstance(value, expected_type):  # type: ignore
                            return ModifyConversationSettingsResponse(
                                success=False,
                                message=f"Invalid type for '{key}'. Expected {expected_type.__name__}, got {type(value).__name__}",  # type: ignore
                            )

                        setattr(conversation.settings, key, value)

                # Update timestamp
                conversation.updated_at = datetime.now(timezone.utc)

                # Persist to database
                update_data: Dict[str, Any] = {}
                if name is not None:
                    update_data["name"] = name
                if user_instructions is not None:
                    update_data["user_instructions"] = user_instructions
                if settings_updates:
                    # Update each settings key individually in the database
                    for key, value in settings_updates.items():
                        update_data[f"settings.{key}"] = value

                update_data["updated_at"] = conversation.updated_at

                with get_db_session() as db:
                    db[Config.CONVERSATIONS_COLLECTION].update_one(
                        {"_id": conversation_id, "user_id": get_operation_user_id()},
                        {"$set": update_data},
                    )

                logger.info(f"Modified conversation {conversation_id} settings: {update_data}")

                update_operation_metadata(
                    {
                        "$addToSet": {"conversation_ids": [conversation_id]},
                        "$set": {"modified_settings": update_data},
                    }
                )

                return ModifyConversationSettingsResponse(
                    success=True,
                    message="Conversation settings modified successfully",
                    conversation_id=conversation_id,
                    conversation=conversation,
                    modified_fields=update_data,
                    updated_at=conversation.updated_at,
                )

            except Exception as e:
                logger.error(f"Error modifying conversation {conversation_id} settings: {e}")
                return ModifyConversationSettingsResponse(
                    success=False, message=f"Error modifying settings: {str(e)}"
                )

    async def delete_conversation(self, conversation_id: str) -> DeleteConversationResponse:
        """
        Delete a conversation and all associated messages asynchronously.

        Handles both database-persisted and local-only (branched) conversations.
        For local-only conversations, only removes from memory cache.
        For database conversations, deletes messages, database record, and cache.

        Thread-safe deletion without race conditions or deadlock.
        Uses non-blocking lock acquisition to fail fast if conversation is in use.

        Args:
            conversation_id: Conversation/chat ID

        Returns:
            Deletion confirmation

        Raises:
            ValueError: If conversation is currently processing or not found
        """
        update_operation_status(TaskStatus.PROCESSING)

        # NON-BLOCKING LOCK: Fail fast if conversation is in use
        # This prevents hanging delete requests when a conversation is being processed
        conversation_lock = self._get_conversation_lock(conversation_id)

        if not conversation_lock.acquire(blocking=False):
            return DeleteConversationResponse(
                success=False,
                message=f"Conversation {conversation_id} is currently processing. Cannot delete while in use.",
            )

        deleted_at = datetime.now(timezone.utc)
        is_local_only = False

        try:
            # Check if conversation exists in database
            conversation_in_db = False
            with get_db_session() as db:
                conversation_entry = db[Config.CONVERSATIONS_COLLECTION].find_one(
                    {"_id": conversation_id, "user_id": get_operation_user_id()}
                )
                conversation_in_db = conversation_entry is not None

            # RACE CONDITION PREVENTION: Load conversation if not in memory
            with self.conversation_cleanup_lock:
                if conversation_id not in self.conversation_instances:
                    # If not in memory and not in database, it's a local-only conversation
                    if not conversation_in_db:
                        return DeleteConversationResponse(
                            success=False,
                            message=f"Conversation {conversation_id} not found",
                        )

                    # Load from database if not in memory
                    conversation_model = Conversation(**conversation_entry)  # type: ignore
                    conversation_model.last_accessed_at = datetime.now(timezone.utc)
                    self.conversation_instances[conversation_id] = MainAgent.load(
                        **conversation_model.model_dump(exclude_none=True)
                    )

                # Check if this is a local-only (branched) conversation
                is_local_only = not conversation_in_db

            # For database conversations: delete messages and database record
            if conversation_in_db:
                # Delete messages (deletes from database and clears agent state)
                await self.conversation_instances[conversation_id].delete_messages()

                # Delete conversation record from database
                with get_db_session() as db:
                    db[Config.CONVERSATIONS_COLLECTION].delete_one(
                        {"_id": conversation_id, "user_id": get_operation_user_id()}
                    )

            # Remove from memory cache
            self._offload_conversation(conversation_id, timedelta(seconds=1))

        finally:
            # Always release the lock
            conversation_lock.release()

        # Clean up the per-conversation lock AFTER releasing it to prevent deadlock
        self._cleanup_conversation_lock(conversation_id)

        logger.info(
            f"Deleted conversation {conversation_id}" + (f" (local-only)" if is_local_only else "")
        )

        update_operation_metadata(
            {
                "$addToSet": {"conversation_ids": [conversation_id]},
                "$set": {"deleted": True},
            }
        )

        return DeleteConversationResponse(
            message="Conversation deleted successfully",
            conversation_id=conversation_id,
            deleted_at=deleted_at,
        )

    def edit_conversation_file(
        self, conversation_id: str, file_path: str, new_content: str
    ) -> EditConversationFileResponse:
        """
        Edit a file in a conversation's working directory.

        Args:
            conversation_id: Conversation/chat ID
            file_path: Path to the file to edit (relative to conversation working directory)
            new_content: New content for the file

        Returns:
            ToolResultContent with edit operation result

        Raises:
            Exception: If conversation not found or file edit fails
        """
        update_operation_status(TaskStatus.PROCESSING)
        result = self.get_conversation(conversation_id)
        if not result.success or not result.conversation:
            raise Exception(result.message)

        conversation_lock = self._get_conversation_lock(conversation_id)
        with conversation_lock:
            logger.info(f"Editing file: {file_path} in conversation {conversation_id}")

            # Get the tool manager from the conversation agent
            agent_instance = self.conversation_instances[conversation_id]

            # Call the edit_file method on the tool manager
            result = agent_instance.tool_manager.edit_file(
                file_path=file_path, new_content=new_content
            )

            logger.info(f"File edit completed for {file_path}")

        if result.is_error:
            return EditConversationFileResponse(
                success=False,
                message=result.message if result.message else "Edit failed!",
            )

        update_operation_metadata(
            {
                "$addToSet": {
                    "file_paths": [file_path],
                    "conversation_ids": [conversation_id],
                }
            }
        )

        if result.files:
            return EditConversationFileResponse(file_metadata=result.files[0])
        else:
            return EditConversationFileResponse(
                success=False,
                message=result.message if result.message else "Edit failed!",
            )

    def branch_conversation(
        self, conversation_id: str, message_id: str
    ) -> BranchConversationResponse:
        """
        Create a new conversation branched from a specific message in the original conversation.

        Args:
            conversation_id: ID of the conversation to branch from
            message_id: ID of the message to branch from

        Returns:
            BranchConversationResponse with new_conversation_id and branch metadata
        """

        update_operation_status(TaskStatus.PROCESSING)

        # Get original conversation
        result = self.get_conversation(conversation_id)
        if not result.success or not result.conversation:
            return BranchConversationResponse(
                success=False,
                message=result.message or f"Conversation {conversation_id} not found",
            )

        original_conv = result.conversation

        # Validate that the message_id is part of the original conversation
        # by checking if the message exists and has the same tree_id
        tree_id = original_conv.tree_id
        with get_db_session() as db:
            messages_coll = db[Config.MESSAGES]
            message_doc = messages_coll.find_one(
                {
                    "_id": message_id,
                    "tree_id": tree_id,
                }
            )

            if not message_doc:
                return BranchConversationResponse(
                    success=False,
                    message=f"Message {message_id} does not exist in this conversation tree (tree_id: {tree_id})",
                )

        # Create new conversation with same settings
        new_conv_result = Conversation(
            user_id=original_conv.user_id,
            _id=generate_chat_id(),
            kb_ids=original_conv.kb_ids,
            name=f"Branched - {original_conv.name}",
            summary=original_conv.summary,
            user_instructions=original_conv.user_instructions,
            tree_id=tree_id,
            leaf_message_ids=[message_id],
            current_leaf_message_id=message_id,
            settings=original_conv.settings,
        )

        if not new_conv_result:
            return BranchConversationResponse(
                success=False,
                message=f"Failed to create branched conversation",
            )

        new_conversation_id = new_conv_result.conversation_id

        # Store new conversation in local instance cache
        with self.conversation_cleanup_lock:
            self.conversation_instances[new_conversation_id] = MainAgent.load(
                **new_conv_result.model_dump(exclude_none=True)
            )

        logger.info(
            f"Created branched conversation {new_conversation_id} from {conversation_id} "
            f"at message {message_id} with tree_id {tree_id}"
        )

        return BranchConversationResponse(
            success=True,
            message="Conversation branched successfully",
            new_conversation_id=new_conversation_id,
            parent_conversation_id=conversation_id,
            message_id=message_id,
            tree_id=tree_id,
            conversation_history=self.conversation_instances[
                new_conversation_id
            ].conversation_manager.get_user_history(),
        )
