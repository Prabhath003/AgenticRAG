import json
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Discriminator, Field, PrivateAttr, Tag

from . import ConversationFileMetadata

# ============================================================================
# CONTENT MODELS - Core message content types
# ============================================================================


class SystemPrompt(BaseModel):
    role: str = "system"
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_dump(self, *, for_model: bool = False, sender: Optional[str] = None, **kwargs: Any):
        if for_model:
            kwargs["exclude"] = kwargs.get("exclude", set()) | {"timestamp"}

        return super().model_dump(**kwargs)


class TextContent(BaseModel):
    start_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stop_timestamp: Optional[datetime] = Field(default=None)
    type: Literal["text"] = Field(default="text")
    text: str = Field(default="")
    citations: List[str] = Field(default_factory=lambda: [])

    def model_dump(self, *, for_model: bool = False, sender: Optional[str] = None, **kwargs: Any):
        if for_model:
            sender = sender or "human"
            if sender == "assistant":
                return {
                    "role": "assistant",
                    "content": "<answer>" + self.text + "</answer>",
                }
            else:
                return {"role": "user", "content": self.text}

        kwargs["exclude"] = kwargs.get("exclude", set()) | {
            "citations",
            "type",
            "stop_timestamp",
            "start_timestamp",
        }
        return super().model_dump(**kwargs)

    def add_citations(self, citations: List[str]):
        """Add citations efficiently using set operations for O(n) complexity."""
        existing = set(self.citations)
        for citation in citations:
            if citation not in existing:
                self.citations.append(citation)
                existing.add(citation)


# Supporting model for thought content summaries
class ThoughtSummary(BaseModel):
    """Summary of a thought block."""

    summary: str = Field(default="")


class ThoughtContent(BaseModel):
    start_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stop_timestamp: Optional[datetime] = None
    type: Literal["thinking"] = Field(default="thinking")
    thinking: str = Field(default="")
    summaries: List[ThoughtSummary] = Field(default_factory=lambda: [])
    cut_off: bool = Field(default=False)
    citations: List[str] = Field(default_factory=lambda: [])

    def model_dump(self, *, for_model: bool = False, sender: Optional[str] = None, **kwargs: Any):
        if for_model and sender == "assistant":
            return {
                "role": "assistant",
                "content": "<thinking>" + self.thinking + "</thinking>",
            }

        return super().model_dump(**kwargs)

    def add_citations(self, citations: List[str]):
        """Add citations efficiently using set operations for O(n) complexity."""
        existing = set(self.citations)
        for citation in citations:
            if citation not in existing:
                self.citations.append(citation)
                existing.add(citation)


# Supporting model for displaying tool output
class DisplayContent(BaseModel):
    """
    Enhanced display format for tool output.

    Supports multiple content types:
    - table: Structured data tables
    - ranking: Ranked/ordered lists
    - content_block: Rich text content blocks
    - json_block: JSON data blocks (legacy)
    - text: Plain text (legacy)
    """

    # Core type field
    type: Literal[
        "json_block",
        "text",
    ] = Field(default="text")

    # Legacy fields (maintain backwards compatibility)
    text: Optional[str] = Field(default=None)
    json_block: Optional[str] = Field(default=None)

    def model_dump(
        self, *, for_model: bool = False, sender: Optional[str] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        if for_model:
            return {}
        return super().model_dump(**kwargs)


# ============================================================================
# TOOL CONTENT MODELS - Tool invocation and results
# ============================================================================


class ToolUseContent(BaseModel):
    start_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stop_timestamp: Optional[datetime] = Field(default=None)
    type: Literal["tool_use"] = Field(default="tool_use")
    name: str = Field(default="")
    input: Dict[str, Any] = Field(default={})
    message: str = Field(default="")
    id: str = Field(default="")
    display_content: Optional[DisplayContent] = Field(default=None)
    integration_name: Optional[str] = Field(default=None)
    approval_options: Optional[List[str]] = Field(default=None)
    approval_key: Optional[str] = Field(default=None)

    _json_buffer: Optional[str] = PrivateAttr(default=None)

    def isJSONBuffer(self) -> bool:
        if self._json_buffer:
            return True
        return False

    def concatenateToJSONBuffer(self, chunk: str):
        if not self._json_buffer:
            self._json_buffer = ""
        self._json_buffer += chunk

    def unsetJSONBuffer(self):
        json_dict = None
        if self._json_buffer:
            json_dict = json.loads(self._json_buffer)
        self._json_buffer = None
        return json_dict

    def model_dump(
        self, *, for_model: bool = False, sender: Optional[str] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        if for_model and sender == "assistant":
            arguments: Dict[str, Any] = self.input.copy()
            # if self.display_content:
            #     arguments["display_content"] = self.display_content.model_dump()
            # if self.message:
            #     arguments["message"] = self.message
            # if self.integration_name:
            #     arguments["integration_name"] = self.integration_name
            # if self.approval_options:
            #     arguments["approval_options"] = self.approval_options

            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": self.id,
                        "type": "function",
                        "function": {
                            "name": self.name,
                            "arguments": json.dumps(arguments),
                        },
                    }
                ],
            }

        return super().model_dump(**kwargs)


class ToolResultContent(BaseModel):
    tool_call_id: str = Field(default="")
    type: Literal["tool_result"] = Field(default="tool_result")
    name: str = Field(default="")
    message: Optional[str] = Field(default=None)
    content: List[Dict[str, str]] = Field(default=[])
    files: Optional[List[ConversationFileMetadata]] = Field(default=None)
    is_error: bool = Field(default=False)
    display_content: Optional[DisplayContent] = Field(default=None)
    integration_name: Optional[str] = Field(default=None)
    node_ids: List[str] = Field(default=[])
    relationship_ids: List[str] = Field(default=[])
    citations: List[str] = Field(default=[])

    def model_dump(self, *, for_model: bool = False, sender: Optional[str] = None, **kwargs: Any):
        if for_model and sender == "assistant":
            contents = [str(c.get("text")) for c in self.content if c.get("text") is not None]
            # if self.files:
            #     for file in self.files:
            #         contents.append(f"[{file.get('operation')} : {file.get('path')}]")
            if self.is_error:
                contents.append(f"is_error: {self.is_error}")
            # if self.integration_name:
            #     contents.append(f"integration_name: {self.integration_name}")

            return {
                "role": "tool",
                "content": "\n".join(contents),
                "tool_call_id": self.tool_call_id,
            }

        return super().model_dump(**kwargs)


class TokenBudgetContent(BaseModel):
    """Token consumption tracking for budget management."""

    type: Literal["token_budget"] = Field(default="token_budget")

    def model_dump(
        self, *, for_model: bool = False, sender: Optional[str] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        if for_model:
            return {}
        return super().model_dump(**kwargs)


# ============================================================================
# CONTENT TYPE DISCRIMINATOR - Type-safe union for all content models
# ============================================================================


# Discriminator function for Union types - uses the 'type' field
def get_content_type(
    v: Union[
        Dict[str, Any],
        TextContent,
        ThoughtContent,
        ToolUseContent,
        ToolResultContent,
        TokenBudgetContent,
        None,
    ],
):
    """Extract type field from content object for discriminator."""
    if v is None:
        return "null"  # Return a marker for None values
    if isinstance(v, dict):
        return v.get("type")
    return v.type


# Annotated Union with discriminator for efficient and reliable type matching
ContentType = Annotated[
    Union[
        Annotated[TextContent, Tag("text")],
        Annotated[ThoughtContent, Tag("thinking")],
        Annotated[ToolUseContent, Tag("tool_use")],
        Annotated[ToolResultContent, Tag("tool_result")],
        Annotated[TokenBudgetContent, Tag("token_budget")],
    ],
    Discriminator(get_content_type),
]
