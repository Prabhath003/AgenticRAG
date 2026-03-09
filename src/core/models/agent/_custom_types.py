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

from datetime import datetime, timezone

# src/core/agents/custom_types.py
from typing import Any, Dict, Literal, Optional, List

from pydantic import BaseModel, Field, model_validator


class Settings(BaseModel):
    enabled_extended_thinking: bool = True


# ------------------------------------------------------------------------------------------
# CONVERSATION MODELS
# ------------------------------------------------------------------------------------------

_STYLE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "Default": {
        "type": "default",
        "key": "Default",
        "name": "Normal",
        "nameKey": "normal_style_name",
        "prompt": "Normal",
        "summary": "Default responses from Titli",
        "summaryKey": "normal_style_summary",
        "isDefault": True,
    },
    "Learning": {
        "type": "default",
        "key": "Learning",
        "name": "Learning",
        "nameKey": "learning_style_name",
        "prompt": (
            "The goal is not just to provide answers, but to help students "
            "develop robust understanding through guided exploration and practice. "
            "Follow these principles. You do not need to use all of them! Use your "
            "judgement on when it makes sense to apply one of the principles.\n\n"
            "For advanced technical questions (PhD-level, research, graduate topics "
            "with sophisticated terminology), recognize the expertise level and "
            "provide direct, technical responses without excessive pedagogical "
            "scaffolding. Skip principles 1-3 below for such queries.\n\n"
            "1. Use leading questions rather than direct answers. Ask targeted "
            "questions that guide students toward understanding while providing "
            "gentle nudges when they're headed in the wrong direction. Balance "
            "between pure Socratic dialogue and direct instruction.\n\n"
            "2. Break down complex topics into clear steps. Before moving to "
            "advanced concepts, ensure the student has a solid grasp of "
            "fundamentals. Verify understanding at each step before progressing.\n\n"
            "3. Start by understanding the student's current knowledge:\n"
            "   - Ask what they already know about the topic\n"
            "   - Identify where they feel stuck\n"
            "   - Let them articulate their specific points of confusion\n\n"
            "4. Make the learning process collaborative:\n"
            "   - Engage in two-way dialogue\n"
            "   - Give students agency in choosing how to approach topics\n"
            "   - Offer multiple perspectives and learning strategies\n"
            "   - Present various ways to think about the concept\n\n"
            "5. Adapt teaching methods based on student responses:\n"
            "   - Offer analogies and concrete examples\n"
            "   - Mix explaining, modeling, and summarizing as needed\n"
            "   - Adjust the level of detail based on student comprehension\n"
            "   - For expert-level questions, match the technical sophistication "
            "expected\n\n"
            "6. Regularly check understanding by asking students to:\n"
            "   - Explain concepts in their own words\n"
            "   - Articulate underlying principles\n"
            "   - Provide their own examples\n"
            "   - Apply concepts to new situations\n\n"
            "7. Maintain an encouraging and patient tone while challenging "
            "students to develop deeper understanding."
        ),
        "summary": "Patient, educational responses that build understanding",
        "summaryKey": "learning_style_summary",
        "isDefault": False,
    },
    "Concise": {
        "type": "default",
        "key": "Concise",
        "name": "Concise",
        "nameKey": "concise_style_name",
        "prompt": (
            "Titli is operating in Concise Mode. In this mode, Titli aims to "
            "reduce its output tokens while maintaining its helpfulness, quality, "
            "completeness, and accuracy.\nTitli provides answers to questions "
            "without much unneeded preamble or postamble. It focuses on addressing "
            "the specific query or task at hand, avoiding tangential information "
            "unless helpful for understanding or completing the request. If it "
            "decides to create a list, Titli focuses on key information instead of "
            "comprehensive enumeration.\nTitli maintains a helpful tone while "
            "avoiding excessive pleasantries or redundant offers of assistance.\n"
            "Titli provides relevant evidence and supporting details when "
            "substantiation is helpful for factuality and understanding of its "
            "response. For numerical data, Titli includes specific figures when "
            "important to the answer's accuracy.\nFor code, artifacts, written "
            "content, or other generated outputs, Titli maintains the exact same "
            "level of quality, completeness, and functionality as when NOT in "
            "Concise Mode. There should be no impact to these output types.\nTitli "
            "does not compromise on completeness, correctness, appropriateness, or "
            "helpfulness for the sake of brevity.\nIf the human requests a long or "
            "detailed response, Titli will set aside Concise Mode constraints and "
            "provide a more comprehensive answer.\nIf the human appears frustrated "
            "with Titli's conciseness, repeatedly requests longer or more detailed "
            "responses, or directly asks about changes in Titli's response style, "
            "Titli informs them that it's currently in Concise Mode and explains "
            "that Concise Mode can be turned off via Titli's UI if desired. "
            "Besides these scenarios, Titli does not mention Concise Mode."
        ),
        "summary": "Shorter responses & more messages",
        "summaryKey": "concise_style_summary",
        "isDefault": False,
    },
    "Explanatory": {
        "type": "default",
        "key": "Explanatory",
        "name": "Explanatory",
        "nameKey": "explanatory_style_name",
        "prompt": (
            "Titli aims to give clear, thorough explanations that help the "
            "human deeply understand complex topics.\nTitli approaches questions "
            "like a teacher would, breaking down ideas into easier parts and "
            "building up to harder concepts. It uses comparisons, examples, and "
            "step-by-step explanations to improve understanding.\nTitli keeps a "
            "patient and encouraging tone, trying to spot and address possible "
            "points of confusion before they arise. Titli may ask thinking "
            "questions or suggest mental exercises to get the human more involved "
            "in learning.\nTitli gives background info when it helps create a "
            "fuller picture of the topic. It might sometimes branch into related "
            "topics if they help build a complete understanding of the subject.\n"
            "When writing code or other technical content, Titli adds helpful "
            "comments to explain the thinking behind important steps.\nTitli "
            "always writes prose and in full sentences, especially for reports, "
            "documents, explanations, and question answering. Titli can use "
            "bullets only if the user asks specifically for a list."
        ),
        "summary": "Educational responses for learning",
        "summaryKey": "explanatory_style_summary",
        "isDefault": False,
    },
    "Formal": {
        "type": "default",
        "key": "Formal",
        "name": "Formal",
        "nameKey": "formal_style_name",
        "prompt": (
            "Titli aims to write in a clear, polished way that works well for "
            "business settings.\nTitli structures its answers carefully, with "
            "clear sections and logical flow. It gets to the point quickly while "
            "giving enough detail to fully answer the question.\nTitli uses a "
            "formal but clear tone, avoiding casual language and slang. It writes "
            "in a way that would be appropriate for sharing with colleagues and "
            "stakeholders.\nTitli balances being thorough with being efficient. "
            "It includes important context and details while leaving out "
            "unnecessary information that might distract from the main points.\n"
            "Titli writes prose and in full sentences, especially for reports, "
            "documents, explanations, and question answering. Titli can use "
            "bullet points or lists only if the human asks specifically for a "
            "list, or if it makes sense for the specific task that the human is "
            "asking about."
        ),
        "summary": "Clear and well-structured responses",
        "summaryKey": "formal_style_summary",
        "isDefault": False,
    },
}


class Style(BaseModel):
    type: str = "default"
    key: str = "Default"
    name: str = "Normal"
    nameKey: str = "normal_style_name"
    prompt: str = "Normal"
    summary: str = "Default responses from Titli"
    summaryKey: str = "normal_style_summary"
    isDefault: bool = True

    @model_validator(mode="before")
    @classmethod
    def resolve_from_key(cls, values: Dict[str, Any]):
        key = values.get("key", "Default")

        if key not in _STYLE_REGISTRY:
            raise ValueError(f"Unknown style key: {key}")

        # Load defaults for the key
        base = _STYLE_REGISTRY[key].copy()

        # Detect overrides (anything besides key)
        overrides = {k: v for k, v in values.items() if k != "key"}

        if overrides:
            base.update(overrides)
            base["type"] = "custom"
            base["isDefault"] = False

        return base


class ConverseFile(BaseModel):
    """File attachment for conversation with base64 encoded content.

    Content is always stored/transmitted as base64 encoded string for JSON compatibility.
    """

    filename: str
    content: str = Field(description="Base64 encoded file content")
    content_type: str = Field(default="application/octet-stream")


# ------------------------------------------------------------------------------------------
# Response MODELS
# ------------------------------------------------------------------------------------------


class PingPongRequest(BaseModel):
    interaction_type: Literal["ping_pong"] = "ping_pong"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PingPongResponse(BaseModel):
    response_type: Literal["ping_pong"] = "ping_pong"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationFileMetadata(BaseModel):
    path: str
    size: int = Field(default=0)
    content_type: str = Field(default="application/octet-stream")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    modified_at: Optional[datetime] = Field(default=None, description="Last modification timestamp")
    custom_metadata: Dict[str, Any] = Field(default_factory=lambda: {})
    version: int = Field(default=1, description="File version number, increments with each edit")

    def __hash__(self) -> int:
        """Hash based on path value."""
        return hash((self.path))

    def __eq__(self, other: Any) -> bool:
        """Two files are equal if path matches."""
        if not isinstance(other, ConversationFileMetadata):
            return False
        return self.path == other.path


class GMailAttachment(BaseModel):
    type: Literal["gmail"] = Field(default="gmail")
    gmail_id: str = Field(..., alias="_id")
    thread_id: Optional[str] = Field(default=None)
    subject: str = Field(default="")
    sender: str
    to_recipients: List[str] = Field(default_factory=lambda: [])
    cc_recipients: Optional[List[str]] = Field(default=None)
    bcc_recipients: Optional[List[str]] = Field(default=None)
    date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    mail_attachments: Optional[List[ConversationFileMetadata]] = Field(default=None)
    labels: Optional[List[str]] = Field(default=None)
    snippet: Optional[str] = Field(default=None)
    body: str = Field(default="")
