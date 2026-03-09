from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union, cast, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator

from .agent import ConversationFileMetadata
from .agent.message_models import Message
from .core_models import (
    Chunk,
    Conversation,
    Document,
    KnowledgeBase,
)
from .operation_audit import Operation, Service, TaskStatus
from ...infrastructure.operation_logging import get_operation_id


class BaseResponse(BaseModel):
    """Base response model for all API responses.

    for API responses, never for database operations.
    """

    success: bool = Field(default=True)
    operation_id: Optional[str] = Field(
        default_factory=lambda: get_operation_id()
    )  # Lazy import avoids circular dependency
    message: str = "Successful!"
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HealthResponse(BaseResponse):
    """Health check response"""

    status: Optional[str] = Field(default=None)
    timestamp: Optional[datetime] = Field(default=None)
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Performance stats of the server"
    )


class CreateKnowledgeBaseResponse(BaseResponse):
    kb_id: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)
    created_at: Optional[datetime] = Field(default=None)
    status: Optional[TaskStatus] = Field(default=None)


class GetKnowledgeBaseResponse(BaseResponse):
    knowledge_base: Optional[KnowledgeBase] = Field(default=None)


class ListKnowledgeBasesResponse(BaseResponse):
    """Response for listing knowledge bases. Supports both full Pydantic models and raw dict projections."""

    knowledge_bases: List[Union[KnowledgeBase, Dict[str, Any]]] = Field(default_factory=lambda: [])
    count: int = Field(default=0)
    # include_deleted: bool = Field(default=False)
    filters: Dict[str, Any] = Field(default_factory=lambda: {})

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=False,
    )

    @field_validator("knowledge_bases", mode="before")
    @classmethod
    def validate_knowledge_bases(cls, v: Any) -> List[Union[KnowledgeBase, Dict[str, Any]]]:
        """Accept both KnowledgeBase models and raw dicts without auto-coercion."""
        if not isinstance(v, list):
            return v
        # Keep dicts and models as-is without auto-coercion
        return [item for item in v if isinstance(item, (dict, KnowledgeBase))]  # type: ignore


class UploadDocumentsToKnowledgeBaseResponse(BaseResponse):
    """Response for document upload with background indexing.

    Returns immediately with operation status while indexing happens in background.
    """

    kb_id: Optional[str] = Field(default=None)
    docs_count: int = Field(default=0)
    task_status: TaskStatus = Field(default=TaskStatus.PENDING)
    submission_time: Optional[datetime] = Field(default=None)


class UploadChunksToKnowledgeBaseResponse(BaseResponse):
    """Response for chunk upload with background indexing.

    Returns immediately with operation status while indexing happens in background.
    """

    kb_id: Optional[str] = Field(default=None)
    chunks_count: int = Field(default=0)
    task_status: TaskStatus = Field(default=TaskStatus.PENDING)
    submission_time: Optional[datetime] = Field(default=None)


class ModifyKnowledgeBaseResponse(BaseResponse):
    kb_id: Optional[str] = Field(default=None)
    status: Optional[TaskStatus] = Field(default=None)
    modified_fields: List[str] = Field(default_factory=lambda: [])
    updated_at: Optional[datetime] = Field(default=None)


class DeleteKnowledgeBaseResponse(BaseResponse):
    kb_id: Optional[str] = Field(default=None)
    deleted_docs_count: int = Field(default=0)
    deleted_at: Optional[datetime] = Field(default=None)


class DeleteDocumentsFromKnowledgeBaseResponse(BaseResponse):
    kb_id: Optional[str] = Field(default=None)
    deleted_docs_count: int = Field(default=0)
    updated_at: Optional[datetime] = Field(default=None)


class GetDocumentResponse(BaseResponse):
    document: Optional[Document] = Field(default=None)


class GetDocumentsResponse(BaseResponse):
    documents: List[Union[Document, Dict[str, Any]]] = Field(default_factory=lambda: [])
    found_count: int = Field(default=0)
    missing_count: int = Field(default=0)
    missing_doc_ids: List[str] = Field(default_factory=lambda: [])

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=False,
    )

    @field_validator("documents", mode="before")
    @classmethod
    def validate_documents(cls, v: Any) -> List[Union[Document, Dict[str, Any]]]:
        """Auto-coerce complete dicts to models (no projections). Keep partial dicts as-is (with projections)."""
        if not isinstance(v, list):
            return v

        items_list = cast(List[Any], v)
        result: List[Union[Document, Dict[str, Any]]] = []
        for item in items_list:
            if isinstance(item, dict):
                try:
                    # Complete dict has required fields - convert to model
                    result.append(Document.model_validate(item))
                except Exception:
                    # Partial dict - keep as-is
                    result.append(cast(Dict[str, Any], item))
            else:
                result.append(cast(Document, item))
        return result


class GetDocumentPresignedUrlResponse(BaseResponse):
    doc_id: Optional[str] = Field(default=None)
    presigned_url: Optional[str] = Field(default=None)
    expires_in_seconds: int = Field(default=600)


class GetChunksByDocResponse(BaseResponse):
    chunks: List[Union[Chunk, Dict[str, Any]]] = Field(default_factory=lambda: [])
    count: int = Field(default=0)
    doc_id: Optional[str] = Field(default=None)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=False,
    )

    @field_validator("chunks", mode="before")
    @classmethod
    def validate_chunks(cls, v: Any) -> List[Union[Chunk, Dict[str, Any]]]:
        """Auto-coerce complete dicts to models (no projections). Keep partial dicts as-is (with projections)."""
        if not isinstance(v, list):
            return v

        items_list = cast(List[Any], v)
        result: List[Union[Chunk, Dict[str, Any]]] = []
        for item in items_list:
            if isinstance(item, dict):
                try:
                    # Complete dict has required fields - convert to model
                    result.append(Chunk.model_validate(item))
                except Exception:
                    # Partial dict - keep as-is
                    result.append(cast(Dict[str, Any], item))
            else:
                result.append(cast(Chunk, item))
        return result


class GetChunksByKBResponse(BaseResponse):
    chunks: List[Union[Chunk, Dict[str, Any]]] = Field(default_factory=lambda: [])
    count: int = Field(default=0)
    kb_id: Optional[str] = Field(default=None)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=False,
    )

    @field_validator("chunks", mode="before")
    @classmethod
    def validate_chunks(cls, v: Any) -> List[Union[Chunk, Dict[str, Any]]]:
        """Auto-coerce complete dicts to models (no projections). Keep partial dicts as-is (with projections)."""
        if not isinstance(v, list):
            return v

        items_list = cast(List[Any], v)
        result: List[Union[Chunk, Dict[str, Any]]] = []
        for item in items_list:
            if isinstance(item, dict):
                try:
                    # Complete dict has required fields - convert to model
                    result.append(Chunk.model_validate(item))
                except Exception:
                    # Partial dict - keep as-is
                    result.append(cast(Dict[str, Any], item))
            else:
                result.append(cast(Chunk, item))
        return result


class GetChunkBatchResponse(BaseResponse):
    total_requested: int = Field(default=0)
    found_count: int = Field(default=0)
    missing_count: int = Field(default=0)
    chunks: List[Union[Chunk, Dict[str, Any]]] = Field(default_factory=lambda: [])
    missing_chunk_ids: List[str] = Field(default_factory=lambda: [])

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=False,
    )

    @field_validator("chunks", mode="before")
    @classmethod
    def validate_chunks(cls, v: Any) -> List[Union[Chunk, Dict[str, Any]]]:
        """Auto-coerce complete dicts to models (no projections). Keep partial dicts as-is (with projections)."""
        if not isinstance(v, list):
            return v

        items_list = cast(List[Any], v)
        result: List[Union[Chunk, Dict[str, Any]]] = []
        for item in items_list:
            if isinstance(item, dict):
                try:
                    # Complete dict has required fields - convert to model
                    result.append(Chunk.model_validate(item))
                except Exception:
                    # Partial dict - keep as-is
                    result.append(cast(Dict[str, Any], item))
            else:
                result.append(cast(Chunk, item))
        return result


class ListConversationsResponse(BaseResponse):
    conversations: List[Union[Conversation, Dict[str, Any]]] = Field(default_factory=lambda: [])
    count: int = Field(default=0)
    filters: Dict[str, Any] = Field(default_factory=lambda: {})

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=False,
    )

    @field_validator("conversations", mode="before")
    @classmethod
    def validate_conversations(cls, v: Any) -> List[Union[Conversation, Dict[str, Any]]]:
        """Accept both Conversation models and raw dicts without auto-coercion."""
        if not isinstance(v, list):
            return v
        # Keep dicts and models as-is without auto-coercion
        return [item for item in v if isinstance(item, (dict, Conversation))]  # type: ignore


class GetConversationResponse(BaseResponse):
    conversation: Optional[Conversation] = Field(default=None)


class BranchConversationResponse(BaseResponse):
    new_conversation_id: Optional[str] = Field(
        default=None, description="ID of the new branched conversation"
    )
    parent_conversation_id: Optional[str] = Field(
        default=None, description="ID of the parent conversation"
    )
    message_id: Optional[str] = Field(default=None, description="ID of the branch point message")
    tree_id: Optional[str] = Field(
        default=None, description="Tree ID linking parent and child conversations"
    )
    conversation_history: OrderedDict[str, Message] = Field(
        default_factory=lambda: OrderedDict(),
        description="Initial conversation history for the branched conversation",
    )


class ListConversationFilesResponse(BaseResponse):
    conversation_id: Optional[str] = Field(default=None)
    files: List[str] = Field(default_factory=lambda: [])
    files_metadata: List[ConversationFileMetadata] = Field(default_factory=lambda: [])


class EditConversationFileResponse(BaseResponse):
    file_metadata: Optional[ConversationFileMetadata] = Field(default=None)


class PreviewFileResponse(BaseResponse):
    filename: Optional[str] = Field(default=None)
    preview_content_type: Optional[str] = Field(default=None)
    content: Optional[str] = Field(default=None, description="Base64 encoded file content")


class GetConversationHistoryResponse(BaseResponse):
    conversation: Optional[Conversation] = Field(default=None)
    conversation_history: OrderedDict[str, Message] = Field(default_factory=lambda: OrderedDict())
    offset: int = Field(default=0)
    limit: int = Field(default=0)


class ModifyConversationSettingsResponse(BaseResponse):
    conversation_id: Optional[str] = Field(default=None)
    conversation: Optional[Conversation] = Field(default=None)
    modified_fields: Dict[str, Any] = Field(default_factory=lambda: {})
    updated_at: Optional[datetime] = Field(default=None)


class DeleteConversationResponse(BaseResponse):
    conversation_id: Optional[str] = Field(default=None)
    deleted_at: Optional[datetime] = Field(default=None)


class GetOperationStatsResponse(BaseResponse):
    operation: Optional[Operation] = Field(default=None)
    services_used: List[Service] = Field(default_factory=lambda: [])


class ListOperationsResponse(BaseResponse):
    """Response for listing operations. Supports both full Pydantic models and raw dict projections."""

    operations: List[Union[Operation, Dict[str, Any]]] = Field(default_factory=lambda: [])
    count: int = Field(default=0)
    filters: Dict[str, Any] = Field(default_factory=lambda: {})

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=False,  # Prevent auto-coercion on assignment
    )

    @field_validator("operations", mode="before")
    @classmethod
    def validate_operations(cls, v: Any) -> List[Union[Operation, Dict[str, Any]]]:
        """Accept both Operation models and raw dicts without auto-coercion.

        When projections are used, dicts are partial and shouldn't be coerced to models.
        """
        if not isinstance(v, list):
            return v

        result: List[Union[Operation, Dict[str, Any]]] = []
        for item in v:  # type: ignore
            # If it's already a dict or Operation instance, keep as-is
            # Don't try to auto-coerce partial dicts to Operation models
            if isinstance(item, (dict, Operation)):
                result.append(item)  # type: ignore
        return result


class ListServicesResponse(BaseResponse):
    """Response for listing services. Supports both full Pydantic models and raw dict projections."""

    services: List[Union[Service, Dict[str, Any]]] = Field(default_factory=lambda: [])
    count: int = Field(default=0)
    filters: Dict[str, Any] = Field(default_factory=lambda: {})

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=False,  # Prevent auto-coercion on assignment
    )

    @field_validator("services", mode="before")
    @classmethod
    def validate_services(cls, v: Any) -> List[Union[Service, Dict[str, Any]]]:
        """Accept both Service models and raw dicts without auto-coercion.

        When projections are used, dicts are partial and shouldn't be coerced to models.
        """
        if not isinstance(v, list):
            return v

        result: List[Union[Dict[str, Any], Service]] = []
        for item in v:  # type: ignore
            # If it's already a dict or Service instance, keep as-is
            # Don't try to auto-coerce partial dicts to Service models
            if isinstance(item, (dict, Service)):
                result.append(item)  # type: ignore
        return result


class ConversationWebSocketTaskResponse(BaseResponse):
    """Response model for concurrent WebSocket operations"""

    task_id: str = Field(..., description="Matches operation_id from request for correlation")
    type: Literal[
        "ping-pong",
        "submit",
        "get_conversation",
        "get_history",
        "list_files",
        "modify_settings",
        "edit_file",
        "preview_file",
    ] = Field(
        ...,
        description="Type of operation: submit, get_conversation, get_history, list_files, download_file, preview_file, edit_file, modify_settings, ping-pong",
    )
    data: Optional[Dict[str, Any]] = Field(default=None, description="Operation response data")


class CreateConversationResponse(BaseResponse):
    conversation_id: Optional[str] = Field(default=None)
    kb_ids: Optional[List[str]] = Field(default_factory=lambda: [])
    created_at: Optional[datetime] = Field(default=None)


class GetToolsResponse(BaseResponse):
    """Response model for listing available MCP tools"""

    tools: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        title="Tools",
        description="List of available tools with their schemas (name, description, parameters)",
    )


class ExecuteToolResponse(BaseResponse):
    """Response model for tool execution result"""

    tool_name: Optional[str] = Field(
        default=None,
        title="Tool Name",
        description="Name of the executed tool",
    )
    result: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Result",
        description="Tool execution result containing content and metadata",
    )


class SearchToolsResponse(BaseResponse):
    """Response model for tool search results"""

    tools: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        title="Matching Tools",
        description="List of tools matching the search query with their schemas",
    )
