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
Message Data Models for Agent System

Provides comprehensive message and content models for streaming agent responses:
- TextContent: Text responses
- ThoughtContent: Internal reasoning/thinking blocks
- ToolUseContent: Tool invocation with arguments
- ToolResultContent: Tool execution results
- TokenBudgetContent: Token consumption tracking
- Message: Complete message with multiple content blocks and metadata
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from rich import print

from . import ConversationFileMetadata
from .content_models import (
    ContentType,
    TextContent,
    ThoughtContent,
    TokenBudgetContent,
    ToolResultContent,
    ToolUseContent,
)
from .delta_models import (
    CitationDelta,
    ContentBlockDelta,
    ContentBlockStart,
    ContentBlockStop,
    InputJSONDelta,
    TextContentDelta,
    ThoughtContentDelta,
    ThoughtSummaryDelta,
)

# ============================================================================
# CONSTANTS AND UTILITIES
# ============================================================================

DUMMY_MESSAGE_ID = "msg_00000000-0000-4000-8000-000000000000"


def generate_chat_message_id_random() -> str:
    """Generate a unique message ID with UUID suffix."""
    return f"msg_{uuid.uuid4()}"


# ============================================================================
# MESSAGE MODEL - Complete message with content blocks and metadata
# ============================================================================


class MessageDeltaDelta(BaseModel):
    stop_reason: Optional[
        Literal[
            "stop_sequence",
            "user_interruption",
            "message_limit",
            "context_limit",
            "usage_limit",
            "end_turn",
            "error",
        ]
    ] = Field(default="end_turn")
    stop_sequence: Optional[str] = None
    files: Optional[List[ConversationFileMetadata]] = Field(default=None)
    related_questions: Optional[List[Dict[str, Any]]] = Field(default=None)


class MessageDelta(BaseModel):
    type: str = "message_delta"
    delta: MessageDeltaDelta = MessageDeltaDelta()


class MessageStop(BaseModel):
    type: str = "message_stop"


class Message(BaseModel):
    """
    Complete message model for agent communication.

    Supports streaming content blocks (text, thoughts, tool calls) with metadata
    and linked-list traversal via parent_message_uuid.
    """

    type: str = Field(default="message")
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    # Core message identity
    uuid: str = Field(default_factory=lambda: generate_chat_message_id_random(), alias="_id")
    tree_id: str
    sender: Literal["human", "assistant"] = Field(default="human")
    model: str = Field(default="")
    index: int = 0

    # Message content (supports multiple content blocks)
    text: str = Field(default="")  # Fallback text representation
    content: List[Optional[ContentType]] = Field(
        default_factory=lambda: []
    )  # Indexed content blocks (text, thoughts, tool calls, etc.)

    # Metadata and state
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = Field(default=None)
    truncated: bool = Field(default=False)
    stop_reason: Optional[
        Literal[
            "stop_sequence",
            "user_interruption",
            "message_limit",
            "context_limit",
            "usage_limit",
            "end_turn",
            "error",
        ]
    ] = Field(default=None)
    stop_sequence: Optional[str] = Field(default=None)

    # Associated files and metadata
    attachments: List[Dict[str, Any]] = Field(default_factory=lambda: [])
    files: List[ConversationFileMetadata] = Field(default_factory=lambda: [])
    files_v2: List[ConversationFileMetadata] = Field(default_factory=lambda: [])
    sync_sources: List[Dict[str, Any]] = Field(default_factory=lambda: [])
    related_questions: List[Dict[str, Any]] = Field(default_factory=lambda: [])

    # Conversation graph structure
    parent_message_uuid: str = DUMMY_MESSAGE_ID
    compacted: bool = False

    def update(self, message_block: MessageDelta | MessageStop):
        if isinstance(message_block, MessageDelta):
            self.stop_reason = message_block.delta.stop_reason
            self.stop_sequence = message_block.delta.stop_sequence

            # Add files to self.files, avoiding duplicates
            if message_block.delta.files:
                existing_files = set(self.files)
                for file_metadata in message_block.delta.files:
                    if file_metadata not in existing_files:
                        self.files.append(file_metadata)
                        existing_files.add(file_metadata)

            # Add related questions to self.related_questions, avoiding duplicates
            if message_block.delta.related_questions:
                self.related_questions.extend(message_block.delta.related_questions)
                # existing_questions = set(self.related_questions)
                # for question in message_block.delta.related_questions:
                #     if question not in existing_questions:
                #         self.related_questions.append(question)
                #         existing_questions.add(question)

    def update_content(
        self, content_block: ContentBlockStart | ContentBlockDelta | ContentBlockStop
    ):
        try:
            # Handle different content block types
            if isinstance(content_block, ContentBlockStart):
                # Start a new content block at the specified index
                block_index = content_block.index

                # Ensure the content list is large enough
                while len(self.content) <= block_index:
                    self.content.append(None)

                # Add the content block at the specified index
                self.content[block_index] = content_block.content_block

            elif isinstance(content_block, ContentBlockDelta):
                # Update the content block at the specified index
                block_index = content_block.index

                if block_index >= len(self.content) or self.content[block_index] is None:
                    print(f"No content block at index {block_index} in message {self.uuid}")
                    return False

                current_block = self.content[block_index]
                delta = content_block.delta

                # Update the current block based on delta type
                if isinstance(delta, ThoughtContentDelta):
                    if isinstance(current_block, ThoughtContent):
                        current_block.thinking += delta.thinking
                    else:
                        block_type = type(current_block).__name__
                        print(
                            f"Delta type mismatch: got ThoughtContentDelta "
                            f"but block at index {block_index} is {block_type}"
                        )

                elif isinstance(delta, ThoughtSummaryDelta):
                    if isinstance(current_block, ThoughtContent):
                        if delta.summary:
                            current_block.summaries.append(delta.summary)
                    else:
                        block_type = type(current_block).__name__
                        print(
                            f"Delta type mismatch: got ThoughtSummaryDelta "
                            f"but block at index {block_index} is {block_type}"
                        )

                elif isinstance(delta, TextContentDelta):
                    if isinstance(current_block, TextContent):
                        current_block.text += delta.text
                    else:
                        block_type = type(current_block).__name__
                        print(
                            f"Delta type mismatch: got TextContentDelta "
                            f"but block at index {block_index} is {block_type}"
                        )

                elif isinstance(delta, InputJSONDelta):
                    if isinstance(current_block, (ToolUseContent)):
                        # Accumulate JSON arguments
                        current_block.concatenateToJSONBuffer(delta.partial_json)
                    else:
                        block_type = type(current_block).__name__
                        print(
                            f"Delta type mismatch: got InputJSONDelta "
                            f"but block at index {block_index} is {block_type}"
                        )
                elif isinstance(delta, CitationDelta):
                    if isinstance(current_block, (TextContent, ThoughtContent)):
                        current_block.add_citations(delta.citation)
                    else:
                        block_type = type(current_block).__name__
                        print(
                            f"Delta type mismatch: got CitationDelta "
                            f"but block at index {block_index} is {block_type}"
                        )

                else:  # isinstance(delta, ToolUseBlockUpdateDelta)
                    if isinstance(current_block, (ToolUseContent, ToolResultContent)):
                        if delta.message:
                            current_block.message = delta.message
                        if delta.display_content:
                            current_block.display_content = delta.display_content
                    else:
                        block_type = type(current_block).__name__
                        print(
                            f"Delta type mismatch: got ToolUseBlockUpdateDelta "
                            f"but block at index {block_index} is {block_type}"
                        )

            else:  # isinstance(content_block, ContentBlockStop)
                # Mark end of content block at the specified index
                block_index = content_block.index

                if block_index >= len(self.content) or self.content[block_index] is None:
                    print(
                        f"No content block at index {block_index} "
                        f"to stop in message {self.uuid}"
                    )
                else:
                    current_block = self.content[block_index]
                    if (
                        not isinstance(current_block, (ToolResultContent, TokenBudgetContent))
                        and current_block
                    ):
                        current_block.stop_timestamp = content_block.stop_timestamp
                        if isinstance(current_block, ToolUseContent):
                            try:
                                # Parse accumulated JSON and store in input field
                                parsed_input = current_block.unsetJSONBuffer()
                                # Finalize JSON buffer if it exists
                                if parsed_input:
                                    current_block.input = parsed_input
                            except json.JSONDecodeError as e:
                                tool_name = current_block.name
                                print(f"Failed to parse JSON buffer for tool " f"{tool_name}: {e}")
            # Update message timestamp
            self.updated_at = datetime.now(timezone.utc)
            return True
        except Exception:
            return False

    # ========================================================================
    # MESSAGE SERIALIZATION METHODS
    # ========================================================================

    def model_dump(self, *, for_model: bool = False, **kwargs: Any) -> Dict[str, Any]:
        """
        Convert message to dictionary format.

        Args:
            for_model: If True, returns content in OpenAI message format.
                      If False, returns full model_dump().

        Returns:
            Dictionary with "content" key if for_model=True, else standard model dump.
        """
        if for_model:
            model_dump: Dict[str, List[Dict[str, Any]]] = {"content": []}
            if self.text:
                model_dump["content"].append({"role": "system", "content": self.text})
            if self.sender == "assistant":
                for obj in self.content:
                    if obj is None:  # Skip None placeholders
                        continue
                    formatted = obj.model_dump(for_model=True, sender=self.sender)
                    if formatted:  # Only append if not empty
                        model_dump["content"].append(formatted)

            else:
                # Format user message
                contents: List[str] = []
                for obj in self.content:
                    if obj is None:  # Skip None placeholders
                        continue
                    formatted = obj.model_dump(for_model=True, sender=self.sender)
                    if formatted and "content" in formatted:
                        contents.append(formatted["content"])

                if self.files:
                    for file in self.files:
                        contents.append(f"[uploaded : {file.path}]")
                if self.attachments:
                    for attachment in self.attachments:
                        contents.append(f"[attached : {json.dumps(attachment, indent=1)}]")

                model_dump["content"].append({"role": "user", "content": "\n".join(contents)})

            return model_dump
        else:
            return super().model_dump(**kwargs)


class MessageStart(BaseModel):
    type: str = "message_start"
    message: Message


class WithinLimit(BaseModel):
    type: str = "within_limit"
    resetsAt: Optional[str] = None
    remaining: Optional[str] = None
    perModelLimit: Optional[str] = None
    representativeClaim: str = "five_hour"
    overageDisabledReason: str = "org_level_disabled"
    windows: Dict[str, Any] = {
        "5h": {"status": "within_limit", "resets_at": 1764176400},
        "7d": {"status": "within_limit", "resets_at": 1764666000},
    }


class MessageLimit(BaseModel):
    type: str = "message_limit"
    message_limit: WithinLimit = WithinLimit()
