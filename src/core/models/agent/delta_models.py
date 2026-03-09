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

"""
Response and Streaming Models for Agent System

Provides data models for streaming API responses with incremental content updates:
- ContentBlockStart: Initiates a new content block
- ContentBlockStop: Marks the end of a content block
- Delta models: Incremental updates (TextContentDelta, InputJSONDelta, etc.)
- ContentBlockDelta: Wrapper for delta updates with typed discriminator
"""

from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional, Union

from pydantic import BaseModel, Discriminator, Field, Tag

from .content_models import ContentType, DisplayContent, ThoughtSummary

# ============================================================================
# STREAMING EVENT MODELS - Content block lifecycle events
# ============================================================================


class ContentBlockStart(BaseModel):
    """
    Initiates a new content block in the message stream.

    Used when a new content type (text, thought, tool call) begins streaming.
    The index indicates which position in the message.content list.
    """

    type: str = Field(default="content_block_start")
    index: int = Field(default=0)
    content_block: ContentType


class ContentBlockStop(BaseModel):
    """
    Marks the end of a content block stream.

    Called when all streaming data for a content block has been received.
    Finalizes any pending operations (e.g., JSON parsing for tool arguments).
    """

    type: str = Field(default="content_block_stop")
    index: int = Field(default=0)
    stop_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================================
# DELTA MODELS - Incremental content updates during streaming
# ============================================================================


class ThoughtContentDelta(BaseModel):
    """Incremental update to a thought/reasoning block."""

    type: str = Field(default="thinking_delta")
    thinking: str = Field(default="")


class ThoughtSummaryDelta(BaseModel):
    """Summary update for a thought block."""

    type: str = Field(default="thinking_summary_delta")
    summary: ThoughtSummary = Field(default_factory=lambda: ThoughtSummary())


class TextContentDelta(BaseModel):
    """Incremental text chunk during streaming."""

    type: str = Field(default="text_delta")
    text: str = Field(default="")


class CitationDelta(BaseModel):
    type: str = Field(default="citation_delta")
    citation: List[str] = Field(default_factory=lambda: [])


class InputJSONDelta(BaseModel):
    """Partial JSON chunk for tool arguments (accumulated during streaming)."""

    type: str = Field(default="input_json_delta")
    partial_json: str = Field(default="")


class ToolUseBlockUpdateDelta(BaseModel):
    """Update to tool execution metadata (message, display content)."""

    type: str = Field(default="tool_use_block_update_delta")
    message: str = Field(default="")
    display_content: Optional[DisplayContent] = Field(default=None)


# ============================================================================
# DELTA TYPE DISCRIMINATOR - Type-safe union for all delta models
# ============================================================================


def get_delta_type(
    v: (
        Dict[str, Any]
        | ThoughtContentDelta
        | ThoughtSummaryDelta
        | TextContentDelta
        | InputJSONDelta
        | ToolUseBlockUpdateDelta
        | CitationDelta
    ),
):
    """
    Extract type field from delta object for discriminator.

    Allows Pydantic to automatically route delta updates to the correct model type
    based on the 'type' field value.
    """
    if isinstance(v, dict):
        return v.get("type")
    return v.type


# Annotated Union with discriminator for type-safe delta handling
ContentDeltaType = Annotated[
    Union[
        Annotated[ThoughtContentDelta, Tag("thinking_delta")],
        Annotated[ThoughtSummaryDelta, Tag("thinking_summary_delta")],
        Annotated[TextContentDelta, Tag("text_delta")],
        Annotated[InputJSONDelta, Tag("input_json_delta")],
        Annotated[ToolUseBlockUpdateDelta, Tag("tool_use_block_update_delta")],
        Annotated[CitationDelta, Tag("citation_delta")],
    ],
    Discriminator(get_delta_type),
]

# ============================================================================
# CONTENT BLOCK DELTA - Wrapper for incremental content updates
# ============================================================================


class ContentBlockDelta(BaseModel):
    """
    Incremental content block update during streaming.

    Wraps a delta update with the index of the content block being updated.
    The delta field contains the actual incremental data (text chunk, JSON partial, etc.).

    Example:
        - index=0, delta=TextContentDelta(text="Hello") → Text block 0 received "Hello"
        - index=1, delta=InputJSONDelta(partial_json='{"key"') → Tool block 1 received partial JSON
    """

    type: str = Field(default="content_block_delta")
    index: int = Field(default=0)
    delta: ContentDeltaType
