from datetime import datetime, timezone
from enum import Enum, StrEnum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    """Task status enum"""

    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class OperationType(StrEnum):
    """Enum for different types of operations that incur costs"""

    CREATE_KNOWLEDGE_BASE = "create_knowledge_base"
    GET_KNOWLEDGE_BASE = "get_knowledge_base"
    LIST_KNOWLEDGE_BASE = "list_knowledge_base"
    UPLOAD_DOCS_TO_KNOWLEDGE_BASE = "upload_docs_to_knowledge_base"
    UPLOAD_CHUNKS_TO_KNOWLEDGE_BASE = "upload_chunks_to_knowledge_base"
    DELETE_KNOWLEDGE_BASE = "delete_knowledge_base"
    UPDATE_KNOWLEDGE_BASE = "update_knowledge_base"
    GET_KNOWLEDGE_BASE_STATUS = "get_knowledge_base_status"
    DELETE_KNOWLEDGE_BASE_DOCS = "delete_knowledge_base_docs"

    GET_DOCUMENT = "get_document"
    DOWNLOAD_DOCUMENT = "download_document"

    GET_DOCUMENTS = "get_documents"
    DOWNLOAD_DOCUMENTS = "download_documents"

    CREATE_CONVERSATION_SESSION = "create_conversation_session"
    BRANCH_CONVERSATION_SESSION = "branch_conversation_session"

    LIST_CONVERSATIONS = "list_conversations"

    GET_CONVERSATION = "get_conversation"
    CONVERSE = "converse"
    CONVERSE_WEBSOCKET = "converse_websocket"
    SUBMIT_CONVERSE_MESSAGE = "submit_converse_message"
    PROCESS_CONVERSE_MESSAGES = "process_converse_messages"
    UPDATE_CONVERSATION = "update_conversation"
    LIST_CONVERSATION_FILES = "list_conversation_files"
    DOWNLOAD_CONVERSATION_FILE = "download_conversation_file"
    PREVIEW_CONVERSATION_FILE = "preview_conversation_file"
    EDIT_FILE = "edit_file"
    GET_CONVERSATION_HISTORY = "get_conversation_history"
    DELETE_CONVERSATION = "delete_conversation"

    GET_TOOLS = "get_tools"
    EXECUTE_TOOL = "execute_tool"
    SEARCH_TOOLS = "search_tools"

    LIST_OPERATIONS = "list_operations"
    LIST_SERVICES = "list_services"


class Operation(BaseModel):
    """Model for tracking operations and associated costs - BSON optimized"""

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        use_enum_values=True,  # Serialize enums as values (strings)
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()),
        alias="_id",
        description="Unique MongoDB operation identifier",
    )
    user_id: str
    operation_type: OperationType = Field(..., description="Type of operation performed")
    estimated_cost_usd: float = Field(default=0, ge=0, description="Estimated cost in USD")
    actual_cost_usd: float = Field(
        default=0, ge=0, description="Actual cost in USD after completion"
    )
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="Operation status")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when operation was initiated",
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="Timestamp when operation completed"
    )
    description: Optional[str] = Field(
        default=None, description="Additional details about the operation"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional metadata for the operation"
    )

    def mark_completed(self, actual_cost_usd: Optional[float] = None) -> None:
        """Mark operation as completed"""
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)
        if actual_cost_usd is not None:
            self.actual_cost_usd = actual_cost_usd

    def mark_failed(self, error_description: str) -> None:
        """Mark operation as failed"""
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now(timezone.utc)
        if not self.description:
            self.description = error_description
        else:
            self.description = f"{self.description}; Error: {error_description}"

    def get_duration_seconds(self) -> Optional[float]:
        """Get operation duration in seconds"""
        if self.completed_at:
            return (self.completed_at - self.created_at).total_seconds()
        return None

    def get_id(self) -> str:
        return self.id


class ServiceType(str, Enum):
    MONGODB = "mongodb"
    S3_STORAGE = "s3_storage"
    OPENAI = "openai"
    TRANSFORMER = "transformer"  # local gpu
    FILE_PROCESSOR = "file_processor"
    RAG_INDEX_BUILD = "rag_index_build"
    RAG_INDEX_QUERY = "rag_index_query"
    OXYLABS = "oxylabs"


class Service(BaseModel):
    """Model for tracking service usage - BSON optimized"""

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both field name and alias
        use_enum_values=True,  # Serialize enums as values (strings)
    )

    id: str = Field(
        default_factory=lambda: str(uuid4()),
        alias="_id",
        description="Unique MongoDB service identifier",
    )
    operation_id: str = Field(..., description="ID of the operation this service belongs to")
    user_id: str
    service_type: ServiceType = Field(..., description="Type of Service used")
    breakdown: Dict[str, Any] = Field(
        default_factory=dict, description="Breakdown of cost with per unit costs"
    )
    estimated_cost_usd: float = Field(default=0, ge=0, description="Estimated cost in USD")
    actual_cost_usd: float = Field(
        default=0, ge=0, description="Actual cost in USD after completion"
    )
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="Service status")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when service was used",
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="Timestamp when service completed"
    )
    description: Optional[str] = Field(
        default=None, description="Additional details about the service"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional metadata for the service"
    )

    def get_id(self) -> str:
        return self.id
