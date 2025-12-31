"""Metrics and task tracking data structures."""

from dataclasses import dataclass, field
from typing import Dict, Any, List
from enum import Enum


class ServiceType(str, Enum):
    """Services used for processing."""
    OPENAI = "openai" 
    FILE_PROCESSOR = "file_processor"
    NATIVE = "native"  # For JSON, MD, TXT files
    TRANSFORMER = "transformer"  # For local transformer models (tokenizers, embeddings, etc.) - may use GPU


class TaskType(str, Enum):
    """Types of tasks tracked."""
    UPLOAD = "upload"
    QUERY = "query"


class TaskStatus(str, Enum):
    """Task execution status."""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_PARTIAL = "completed_partial"
    FAILED = "failed"
    
@dataclass
class Service:
    service_type: ServiceType
    breakdown: Dict[str, Any] = field(default_factory=Dict[str, Any])
    estimated_cost_usd: float = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "service_type": self.service_type.value,
            "breakdown": self.breakdown,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6)
        }
        
    @staticmethod
    def from_dict(data: Dict[str, Any]):
        return Service(
            ServiceType(data.get("service_type", "native")),
            breakdown=data.get("breakdown", {}),
            estimated_cost_usd=data.get("estimated_cost_usd", 0)
        )
    

@dataclass
class ProcessingMetrics:
    """Metrics for file processing operations."""
    services_used: List[Service] = field(default_factory=List[Service])
    processing_time_seconds: float = 0.0

    @property
    def estimated_cost_usd(self) -> float:
        """Estimate cost based on pages ($0.02 per page) plus XFA conversion cost if applicable."""
        return sum(service.estimated_cost_usd for service in self.services_used)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "services_used": [service.to_dict() for service in self.services_used],
            "processing_time_seconds": self.processing_time_seconds,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4)
        }
