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
Get Chunk Tool
Retrieve full content and metadata of a specific chunk
"""

from typing import Any, Dict, List

from .._base_tool import BaseTool
from . import logger

from .....models.agent.content_models import (
    ToolResultContent,
)
from ._user_kbs_index import UserKBsIndex


class GetChunkTool(BaseTool):
    """
    Get full content and metadata of a specific chunk.
    Returns complete chunk information including full text content.
    """

    def __init__(self, index: UserKBsIndex):
        self.index = index

    @property
    def name(self) -> str:
        return "user_kbs-get_chunk"

    @property
    def description(self) -> str:
        return (
            "Retrieve full content and metadata of a specific chunk.\n\n"
            "Returns complete chunk information:\n"
            "- Full text content of the chunk\n"
            "- Chunk metadata and relationships\n"
            "- Document and knowledge base information\n"
            "- Chunk ordering information\n\n"
            "**PARAMETERS**:\n"
            "- description: One-sentence description of the task being performed\n"
            "- chunk_id: The chunk ID (required) - use user_kbs-list_document_chunks to see available chunks\n\n"
            "This tool retrieves the complete chunk content, unlike list_document_chunks which shows previews."
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
        }

    @property
    def required(self) -> List[str]:
        return ["description", "chunk_id"]

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute get chunk"""
        _ = kwargs.get("description")
        chunk_id = kwargs.get("chunk_id")

        if not chunk_id:
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message="Error: 'chunk_id' parameter is required",
                is_error=True,
            )

        logger.info(f"Getting chunk: {chunk_id}")

        try:
            result = self.index.get_chunk_by_id(chunk_id)

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
            logger.error(f"Error in get_chunk tool: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message=f"Error retrieving chunk: {str(e)}",
                is_error=True,
            )
