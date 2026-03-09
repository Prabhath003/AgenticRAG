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
Get Chunk Context Tool
Retrieve a chunk with surrounding context (before/after chunks)
"""

from typing import Any, Dict, List

from .._base_tool import BaseTool
from . import logger

from .....models.agent.content_models import (
    ToolResultContent,
)
from ._user_kbs_index import UserKBsIndex


class GetChunkContextTool(BaseTool):
    """
    Get a chunk with surrounding context from adjacent chunks.
    Returns the target chunk plus before/after chunks for sequential understanding.
    """

    def __init__(self, index: UserKBsIndex):
        self.index = index

    @property
    def name(self) -> str:
        return "user_kbs-get_chunk_context"

    @property
    def description(self) -> str:
        return (
            "Get a chunk with surrounding context (before and after chunks).\n\n"
            "Returns:\n"
            "- The target chunk with full content\n"
            "- Previous chunks in the document sequence\n"
            "- Next chunks in the document sequence\n\n"
            "**PARAMETERS**:\n"
            "- description: One-sentence description of the task being performed\n"
            "- chunk_id: The chunk ID (required) - use user_kbs-list_document_chunks\n"
            "- context_size: Number of before/after chunks (default: 1, max: 5)\n\n"
            "Useful for understanding chunks in their document context and reading sequential content."
        )

    @property
    def properties(self) -> Dict[str, Dict[str, str]]:
        return {
            "description": {
                "type": "string",
                "description": "One-sentence description of the task being performed",
            },
            "chunk_id": {
                "type": "string",
                "description": "Chunk ID (use user_kbs-list_document_chunks to see available chunks)",
            },
            "context_size": {
                "type": "integer",
                "description": "Number of before/after chunks to include (default: 1, max: 5)",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["description", "chunk_id"]

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute get chunk context"""
        _ = kwargs.get("description")
        chunk_id = kwargs.get("chunk_id")
        context_size = kwargs.get("context_size", 1)

        if not chunk_id:
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message="Error: 'chunk_id' parameter is required",
                is_error=True,
            )

        # Validate context_size
        try:
            context_size = min(int(context_size), 5) if context_size else 1
            context_size = max(context_size, 1)
        except (ValueError, TypeError):
            context_size = 1

        logger.info(f"Getting chunk context: chunk_id={chunk_id}, context_size={context_size}")

        try:
            result = self.index.get_chunk_context(chunk_id, context_size)

            if result.startswith("Error") or result.startswith("Chunk"):
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    name=self.name,
                    message=result,
                    is_error=result.startswith("Error"),
                )

            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                content=[{"type": "text", "text": result, "uuid": func_call_id}],
            )

        except Exception as e:
            logger.error(f"Error in get_chunk_context tool: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message=f"Error retrieving chunk context: {str(e)}",
                is_error=True,
            )
