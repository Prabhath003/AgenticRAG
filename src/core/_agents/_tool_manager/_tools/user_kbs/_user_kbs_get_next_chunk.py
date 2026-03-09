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
Get Next Chunk Tool
Retrieve the next chunk in document sequence
"""

from typing import Any, Dict, List

from .._base_tool import BaseTool
from . import logger

from .....models.agent.content_models import (
    ToolResultContent,
)
from ._user_kbs_index import UserKBsIndex


class GetNextChunkTool(BaseTool):
    """
    Get the next chunk in the document sequence.
    Navigate forwards through a document's chunks.
    """

    def __init__(self, index: UserKBsIndex):
        self.index = index

    @property
    def name(self) -> str:
        return "user_kbs-get_next_chunk"

    @property
    def description(self) -> str:
        return (
            "Get the next chunk in the document sequence.\n\n"
            "Navigate forwards through a document to read the succeeding chunk.\n"
            "Returns full content of the next chunk with metadata.\n\n"
            "**PARAMETERS**:\n"
            "- description: One-sentence description of the task being performed\n"
            "- chunk_id: The current chunk ID (required)\n\n"
            "Returns None if this is the last chunk in the document.\n"
            "Use with get_previous_chunk to navigate through documents sequentially."
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
                "description": "Current chunk ID to get the next chunk from",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["description", "chunk_id"]

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute get next chunk"""
        _ = kwargs.get("description")
        chunk_id = kwargs.get("chunk_id")

        if not chunk_id:
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message="Error: 'chunk_id' parameter is required",
                is_error=True,
            )

        logger.info(f"Getting next chunk for: {chunk_id}")

        try:
            result = self.index.get_next_chunk(chunk_id)

            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                content=[{"type": "text", "text": result, "uuid": func_call_id}],
            )

        except Exception as e:
            logger.error(f"Error in get_next_chunk tool: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message=f"Error retrieving next chunk: {str(e)}",
                is_error=True,
            )
