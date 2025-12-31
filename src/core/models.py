from dataclasses import dataclass
from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

@dataclass
class File:
    filename: str
    content: bytes
    
class TaskStatus(str, Enum):
    """Task status enum"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    
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
    
class KnowledgeGraph(BaseModel):
    """Response model for knowledge graph"""
    nodes: List[KnowledgeGraphNode]
    relationships: List[KnowledgeGraphRelationship]
    total_nodes: int
    total_relationships: int
    entity_ids: List[str]