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

"""Pydantic models for FastAPI endpoints"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class EntityCreate(BaseModel):
    """Request model for creating an entity"""
    entity_id: str = Field(..., description="Unique entity identifier", example="company_123")
    entity_name: str = Field(..., description="Display name for the entity", example="TechCorp Industries")
    description: Optional[str] = Field(None, description="Optional description", example="AI-powered analytics company")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class EntityResponse(BaseModel):
    """Response model for entity operations"""
    entity_id: str
    entity_name: str
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    total_documents: int = 0
    total_chunks: int = 0
    has_vector_store: bool = False


class EntityListResponse(BaseModel):
    """Response model for listing entities"""
    entities: List[EntityResponse]
    total: int


class FileUploadResponse(BaseModel):
    """Response model for file upload"""
    success: bool
    doc_id: str
    entity_id: str
    filename: str
    chunks_count: int
    is_duplicate: bool = False
    message: str


class FileDeleteResponse(BaseModel):
    """Response model for file deletion"""
    success: bool
    doc_id: str
    entity_id: str
    message: str


class DocumentInfo(BaseModel):
    """Document information"""
    doc_id: str
    doc_name: str
    file_path: Optional[str] = None
    indexed_at: Optional[datetime] = None
    file_size: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class ChatSessionCreate(BaseModel):
    """Request model for creating a chat session"""
    entity_id: str = Field(..., description="Entity to chat with")
    session_name: Optional[str] = Field(None, description="Optional session name")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class ChatSessionResponse(BaseModel):
    """Response model for chat session"""
    session_id: str
    entity_id: str
    entity_name: str
    session_name: Optional[str] = None
    created_at: datetime
    last_activity: datetime
    message_count: int = 0
    metadata: Optional[Dict[str, Any]] = None


class ChatMessageRole(str, Enum):
    """Chat message roles"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatMessage(BaseModel):
    """Chat message"""
    role: ChatMessageRole
    content: str
    timestamp: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


class ChatRequest(BaseModel):
    """Request model for chat"""
    session_id: str
    message: str
    stream: bool = Field(default=True, description="Whether to stream the response")


class ChatResponse(BaseModel):
    """Response model for chat"""
    session_id: str
    message: ChatMessage
    sources: Optional[List[Dict[str, Any]]] = None
    node_ids: Optional[List[str]] = Field(None, description="All nodes used as context")
    relationship_ids: Optional[List[str]] = Field(None, description="All navigation relationships")
    cited_node_ids: Optional[List[str]] = Field(None, description="Nodes actually cited in the answer")
    citations: Optional[List[Dict[str, Any]]] = Field(None, description="Citation details")


class SearchRequest(BaseModel):
    """Request model for search"""
    entity_id: str
    query: str
    k: int = Field(default=5, ge=1, le=20, description="Number of results")
    doc_ids: Optional[List[str]] = Field(None, description="Filter by specific documents")


class SearchResult(BaseModel):
    """Search result"""
    content: str
    doc_id: str
    chunk_order_index: int
    source: Optional[str] = None
    score: Optional[float] = None


class SearchResponse(BaseModel):
    """Response model for search"""
    entity_id: str
    query: str
    results: List[SearchResult]
    total: int


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    version: str
    entities_loaded: int
    total_documents: int


class ErrorResponse(BaseModel):
    """Error response"""
    error: str
    detail: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class TaskStatus(str, Enum):
    """Task status enum"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class FileUploadTask(BaseModel):
    """File upload task status"""
    task_id: str
    entity_id: str
    filename: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    message: Optional[str] = None
    error: Optional[str] = None
    result: Optional[FileUploadResponse] = None


class TaskStatusResponse(BaseModel):
    """Response model for task status"""
    task_id: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    message: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class KnowledgeGraphNode(BaseModel):
    """Knowledge graph node representing a chunk"""
    id: str = Field(..., description="Node ID in format: {entity_id}_{doc_id}_{chunk_order_index}")
    nodeLabel: str
    properties: Dict[str, Any] = Field(default_factory=dict, description="Node properties including entity_id, doc_id, chunk_order_index, content, source, and metadata")


class KnowledgeGraphRelationship(BaseModel):
    """Knowledge graph relationship between nodes"""
    id: str = Field(..., description="Relationship ID in format: {source_id}:{target_id}")
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    label: str = Field(default="sequential", description="Type of relationship (e.g., sequential, reference)")
    properties: Optional[Dict[str, Any]] = None


class KnowledgeGraphRequest(BaseModel):
    """Request model for knowledge graph"""
    entity_ids: List[str] = Field(..., description="List of entity IDs to include in the graph")


class KnowledgeGraphResponse(BaseModel):
    """Response model for knowledge graph"""
    nodes: List[KnowledgeGraphNode]
    relationships: List[KnowledgeGraphRelationship]
    total_nodes: int
    total_relationships: int
    entity_ids: List[str]


# class ChunkMarkdown(BaseModel):
#     """Markdown content within a chunk"""
#     text: str = Field(..., description="Markdown text content")
#     chunk_order_index: int = Field(..., description="Order index of this chunk in the document")
#     source: str = Field(..., description="Source identifier (e.g., entity_id)")
#     filename: str = Field(..., description="Original filename")
#     pages: List[int] = Field(default_factory=list, description="Page numbers this chunk appears on")


# class ChunkMetadata(BaseModel):
#     """Metadata for a chunk"""
#     chunk_index: int = Field(..., description="Index of chunk")
#     tokens: int = Field(..., description="Token count")
#     processed_by: str = Field(default="FileProcessor", description="Processor that created this chunk")
#     doc_id: str = Field(..., description="Document ID")
#     entity_id: str = Field(..., description="Entity ID")


class ChunkCreate(BaseModel):
    """Request model for creating/ingesting a chunk"""
    chunk_id: str = Field(..., description="Unique chunk identifier (managed by client)")
    content: Dict[str, Any] = Field(..., description="Markdown content")
    metadata: Dict[str, Any] = Field(..., description="Chunk metadata")


class ChunkIngestResponse(BaseModel):
    """Response model for chunk ingestion"""
    success: bool
    chunk_id: str
    entity_id: str
    doc_id: str
    indexed: bool = Field(..., description="Whether chunk was indexed (False if duplicate)")
    message: str


class ChunkBatchIngestRequest(BaseModel):
    """Request model for batch chunk ingestion"""
    chunks: List[ChunkCreate] = Field(..., description="List of chunks to ingest")


class ChunkBatchIngestResponse(BaseModel):
    """Response model for batch chunk ingestion"""
    success: bool
    entity_id: str
    doc_id: str
    total_chunks: int
    indexed_chunks: int
    duplicate_chunks: int
    message: str
