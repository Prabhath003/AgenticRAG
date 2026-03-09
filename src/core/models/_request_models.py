# api/models.py
from typing import Any, Dict, List, Optional, Literal, Union, Annotated
from pydantic import BaseModel, Field, Discriminator, Tag

from .agent import ConverseFile, Settings, Style
from .core_models import Chunk


# Discriminator function for conversation request types
def get_request_type(v: Union[Dict[str, Any], "ConverseRequest", "StopConverseRequest"]) -> str:
    """Extract request_type field from conversation request object for discriminator."""
    if isinstance(v, dict):
        return v.get("request_type", "converse")
    return v.request_type


class CreateKnowledgeBaseRequest(BaseModel):
    """Request model for creating a knowledge base"""

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "My Knowledge Base",
                "metadata": {"source": "internal", "version": "1.0"},
            }
        }
    }

    title: Optional[str] = Field(
        default=None,
        title="Knowledge Base Title",
        description=(
            "The title/name of your knowledge base. "
            "This is displayed in the UI and used for identification."
        ),
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        title="Metadata",
        description=(
            "Optional key-value pairs for storing additional information "
            "about the knowledge base (e.g., version, source, department)."
        ),
    )

    description: Optional[str] = Field(default=None)


class ListKnowledgeBasesRequest(BaseModel):
    """Request model for listing knowledge bases"""

    # include_deleted: bool = Field(
    #     default=False,
    #     title="Include Deleted",
    #     description="If true, includes deleted KBs in the response (default: false)",
    # )
    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Filters",
        description=(
            "MongoDB-style filters to apply to knowledge bases "
            "(e.g., {'title': 'My KB', 'status': 'active'})"
        ),
    )
    projections: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Projections",
        description=(
            "MongoDB-style projections to specify which fields to "
            "include/exclude (e.g., {'title': 1, 'created_at': 1})"
        ),
    )


class ListConversationsRequest(BaseModel):
    """Request model for listing conversations"""

    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Filters",
        description=(
            "MongoDB-style filters to apply to conversations "
            "(e.g., {'kb_id': ['kb_123']} to filter by knowledge base)"
        ),
    )
    projections: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Projections",
        description=("MongoDB-style projections to specify which fields to " "include/exclude"),
    )


class GetDocumentsBatchRequest(BaseModel):
    """Request model for retrieving documents with filters and projections"""

    filters: Dict[str, Any] = Field(
        ...,
        title="Filters",
        description=(
            "MongoDB-style filters to retrieve documents "
            "(e.g., {'_id': {'$in': ['doc_1', 'doc_2']}} "
            "or {'content_type': 'pdf'})"
        ),
    )
    projections: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Projections",
        description=("MongoDB-style projections to specify which fields to " "include/exclude"),
    )


class DownloadDocumentsBatchRequest(BaseModel):
    """Request model for downloading documents with filters"""

    filters: Dict[str, Any] = Field(
        ...,
        title="Filters",
        description=(
            "MongoDB-style filters to select documents to download "
            "(e.g., {'_id': {'$in': ['doc_1', 'doc_2']}} "
            "or {'content_type': 'pdf'})"
        ),
    )


class GetChunksByDocRequest(BaseModel):
    """Request model for getting chunks by document with filters and projections.

    Default projections (when not specified): _id, doc_id, chunk_index, content, metadata, created_at
    """

    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Filters",
        description=(
            "MongoDB-style filters to further filter chunks " "(e.g., {'tags': 'important'})"
        ),
    )
    projections: Optional[Dict[str, Any]] = Field(
        default={
            "_id": 1,
            "doc_id": 1,
            "chunk_index": 1,
            "content": 1,
            "metadata": 1,
            "created_at": 1,
        },
        title="Projections",
        description=(
            "MongoDB-style projections to specify which fields to include/exclude. "
            "Defaults to: _id, doc_id, chunk_index, content, metadata, created_at"
        ),
    )


class GetChunksByKBRequest(BaseModel):
    """Request model for getting chunks by knowledge base with filters and projections.

    Default projections (when not specified): _id, doc_id, chunk_index, content, metadata, created_at
    """

    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Filters",
        description="MongoDB-style filters to further filter chunks",
    )
    projections: Optional[Dict[str, Any]] = Field(
        default={
            "_id": 1,
            "doc_id": 1,
            "chunk_index": 1,
            "content": 1,
            "metadata": 1,
            "created_at": 1,
        },
        title="Projections",
        description=(
            "MongoDB-style projections to specify which fields to include/exclude. "
            "Defaults to: _id, doc_id, chunk_index, content, metadata, created_at"
        ),
    )


class GetChunksBatchRequest(BaseModel):
    """Request model for getting chunks with filters and projections.

    Default projections (when not specified): _id, doc_id, chunk_index, content, metadata, created_at
    """

    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Filters",
        description=(
            "MongoDB-style filters to retrieve chunks "
            "(e.g., {'_id': {'$in': ['chunk_1', 'chunk_2']}} "
            "or {'doc_id': 'doc_123'})"
        ),
    )
    projections: Optional[Dict[str, Any]] = Field(
        default={
            "_id": 1,
            "doc_id": 1,
            "chunk_index": 1,
            "content": 1,
            "metadata": 1,
            "created_at": 1,
        },
        title="Projections",
        description=(
            "MongoDB-style projections to specify which fields to include/exclude. "
            "Defaults to: _id, doc_id, chunk_index, content, metadata, created_at"
        ),
    )


class DeleteDocumentsFromKBRequest(BaseModel):
    """Request model for deleting documents from a knowledge base"""

    doc_ids: List[str] = Field(
        ...,
        title="Document IDs",
        description="List of document IDs to delete from the knowledge base",
    )


class CreateConversationSessionRequest(BaseModel):
    """Request model for creating a conversation session"""

    name: Optional[str] = Field(
        default=None,
        title="Conversation Name",
        description="Optional name for the conversation session",
    )
    kb_ids: List[str] = Field(default_factory=lambda: [])
    user_instructions: Optional[str] = Field(
        default=None,
        title="User Instructions",
        description="Optional instructions/context for the conversation",
    )
    settings: Settings = Field(
        default_factory=lambda: Settings(),
        title="Settings",
        description=("Optional conversation settings " "(enabled_extended_thinking)"),
    )


class ConverseRequest(BaseModel):
    request_type: Literal["converse"] = Field(
        default="converse", description="Request type identifier"
    )
    prompt: str
    parent_message_uuid: Optional[str] = Field(default=None)
    attachments: List[Dict[str, Any]] = Field(default_factory=lambda: [])
    files: List[ConverseFile] = Field(default_factory=lambda: [])
    personalized_styles: List[Style] = [Style()]


class StopConverseRequest(BaseModel):
    """Request to stop queue processing after current messages"""

    request_type: Literal["stop"] = Field(default="stop", description="Request type identifier")
    reason: Optional[str] = Field(
        default=None, description="Optional reason for stopping the queue processor"
    )


# Discriminated union for type-safe request handling with custom discriminator function
ConversationRequest = Annotated[
    Union[
        Annotated[ConverseRequest, Tag("converse")],
        Annotated[StopConverseRequest, Tag("stop")],
    ],
    Discriminator(get_request_type),
]


class ModifyConversationSettingsRequest(BaseModel):
    name: Optional[str] = None
    user_instructions: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class BranchConversationRequest(BaseModel):
    conversation_id: str = Field(..., description="ID of the conversation to branch from")
    message_id: str = Field(..., description="ID of the message to branch from")


class GetConversationHistoryRequest(BaseModel):
    offset: int = 0
    limit: int = 100


class DownloadConversationFileRequest(BaseModel):
    file_path: str
    version: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional file version to download. If None, downloads the latest version. Must be >= 1.",
    )


class EditConversationFileRequest(BaseModel):
    file_path: str
    new_content: str


class PreviewConversationFileRequest(BaseModel):
    """Request model for previewing a conversation file"""

    file_path: str
    version: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional file version to preview. If None, previews the latest version. Must be >= 1.",
    )


class ModifyKnowledgeBaseRequest(BaseModel):
    title: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    metadata_updates: Optional[Dict[str, Any]] = None


class GetDocumentPresignedUrlRequest(BaseModel):
    """Request model for getting a presigned URL for document access"""

    expiration: int = Field(
        default=3600, description="URL expiration time in seconds (default: 3600, max: 3600)"
    )
    inline: bool = Field(
        default=False,
        description="If true, URL renders content in browser (e.g., PDFs, images); if false, forces download",
    )


class ListOperationsRequest(BaseModel):
    """Request model for listing operations"""

    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Filters",
        description=(
            "MongoDB-style filters to apply to operations "
            "(e.g., {'status': 'completed', 'operation_type': 'create_knowledge_base'})"
        ),
    )
    projections: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Projections",
        description=("MongoDB-style projections to specify which fields to " "include/exclude"),
    )


class ListServicesRequest(BaseModel):
    """Request model for listing services"""

    filters: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Filters",
        description=(
            "MongoDB-style filters to apply to services "
            "(e.g., {'service_type': 'mongodb', 'status': 'completed'})"
        ),
    )
    projections: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Projections",
        description=("MongoDB-style projections to specify which fields to " "include/exclude"),
    )


# WebSocket Concurrent Operation Models
class ConversationWebSocketRequest(BaseModel):
    """Request model for concurrent WebSocket operations"""

    task_id: str = Field(..., description="Unique identifier for tracking this operation")
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
    request: Optional[Dict[str, Any]] = Field(
        default=None, description="Operation-specific request data"
    )


class UploadChunksRequest(BaseModel):
    """Request model for uploading pre-chunked data to a knowledge base"""

    chunks: List[Chunk] = Field(
        ...,
        description="List of pre-chunked Chunk objects to upload",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "chunks": [
                    {
                        "_id": "chunk_001",
                        "doc_id": "doc_001",
                        "content": {"text": "Sample chunk content"},
                        "metadata": {"page": 1, "section": "introduction"},
                    }
                ]
            }
        }
    }


class ExecuteToolRequest(BaseModel):
    """Request model for executing MCP tools"""

    tool_name: str = Field(
        ...,
        title="Tool Name",
        description="Name of the tool to execute (e.g., 'user_kbs-list_all_kbs')",
    )
    arguments: Optional[Dict[str, Any]] = Field(
        default=None,
        title="Tool Arguments",
        description="Dictionary of arguments to pass to the tool",
    )


class SearchToolsRequest(BaseModel):
    """Request model for searching MCP tools"""

    query: str = Field(
        ...,
        title="Search Query",
        description="Search query to find tools by name or description",
    )
