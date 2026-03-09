from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ...infrastructure.ids import generate_kb_id, generate_conv_tree_id
from .agent import Settings
from .agent.message_models import DUMMY_MESSAGE_ID
from .operation_audit import TaskStatus
from ...infrastructure.operation_logging import get_operation_user_id


class DatabaseBaseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)
    user_id: Optional[str] = Field(default_factory=lambda: get_operation_user_id())


class Doc(BaseModel):
    doc_name: str
    content_type: str
    content: bytes
    source: Optional[str] = None


class KnowledgeBase(DatabaseBaseModel):
    kb_id: str = Field(default_factory=lambda: generate_kb_id(), alias="_id")
    title: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    index_build_at: Optional[datetime] = Field(default=None)
    updated_at: Optional[datetime] = Field(default=None)
    last_uploaded_at: Optional[datetime] = Field(default=None)
    doc_ids: List[str] = Field(default_factory=list)
    size_mb: float = Field(default=0.0)
    estimated_cost_usd: float = Field(default=0.0)
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    error: Optional[str] = Field(default=None)
    auto_generated: bool = Field(default=False)
    processing_started_at: Optional[datetime] = Field(default=None)
    processing_completed_at: Optional[datetime] = Field(default=None)
    index_build_on_doc_ids: List[str] = Field(
        default_factory=list,
        description="Doc IDs used in the last successful index build",
    )


class Chunk(DatabaseBaseModel):
    chunk_id: str = Field(..., alias="_id")
    doc_id: str
    content: Dict[str, Any]
    metadata: Dict[str, Any] = Field(default_factory=lambda: {})
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # deleted: bool = Field(default=False)
    # deleted_at: Optional[datetime] = Field(default=None)
    # original_chunk_id: Optional[str] = Field(default=None)


class Document(DatabaseBaseModel):
    doc_id: str = Field(..., alias="_id")
    content_id: str
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    doc_name: str
    content_type: str = Field(default="application/octet-stream")
    doc_size: int = Field(default=0)
    source: Optional[str] = Field(default="upload")
    chunked: bool = Field(default=False)
    estimated_cost_usd: float = Field(default=0)
    presigned_url: Optional[str] = Field(
        default=None, description="Cached S3 presigned URL for direct access"
    )
    presigned_url_expires_at: Optional[datetime] = Field(
        default=None, description="Expiration time for the cached presigned URL"
    )
    # deleted: bool = Field(default=False)
    # deleted_at: Optional[datetime] = Field(default=None)
    # original_doc_id: Optional[str] = Field(default=None)


class Content(DatabaseBaseModel):
    content_id: str = Field(..., alias="_id", description="UUID4-based unique identifier")
    content_hash: str = Field(..., description="SHA-256 hash for deduplication within user scope")
    variant_id: int = Field(
        default=0,
        description="Collision variant ID: 0 for normal, 1+ for SHA-256 collisions",
    )
    content_path: str = Field(
        ..., description="Hierarchical storage path: ab/cd/hash or ab/cd/hash_vN"
    )
    storage_hash: str = Field(default="", description="Full SHA-256 hash (64 chars) for reference")
    mime_type: str = Field(default="application/octet-stream", description="MIME type of content")
    ref_count: int = Field(default=1, description="Reference count for garbage collection")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_size: int = Field(default=0, description="File size in bytes")
    # deleted: bool = Field(default=False)
    # deleted_at: Optional[datetime] = Field(default=None)
    # original_content_id: Optional[str] = Field(default=None)


# class ContextUsage(DatabaseBaseModel):
#     system_prompt: int = Field(default=0)
#     system_tools: int = Field(default=0)
#     messages: int = Field(default=0)


class Conversation(DatabaseBaseModel):
    conversation_id: str = Field(..., alias="_id")
    kb_ids: List[str] = Field(default_factory=lambda: [])
    name: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = Field(default=None)
    last_accessed_at: Optional[datetime] = Field(default=None)
    # deleted: bool = Field(default=False)
    user_instructions: Optional[str] = Field(default=None)
    estimated_cost_usd: float = Field(default=0)
    tree_id: str = Field(default_factory=lambda: generate_conv_tree_id())
    leaf_message_ids: List[str] = Field(default_factory=lambda: [DUMMY_MESSAGE_ID])
    current_leaf_message_id: str = DUMMY_MESSAGE_ID
    settings: Settings = Settings()
    # deleted_at: Optional[datetime] = Field(default=None)
    # original_conversation_id: Optional[str] = Field(default=None)
