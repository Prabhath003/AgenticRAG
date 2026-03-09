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
List Document Chunks Tool
Lists all chunks in a document with IDs and content previews
"""

from typing import Any, Dict, List

from .._base_tool import BaseTool
from . import logger

from .....models.agent.content_models import (
    ToolResultContent,
)
from ._user_kbs_index import UserKBsIndex


class ListDocumentChunksTool(BaseTool):
    """
    List all chunks in a specific document.
    Provides LLM-passable string format showing chunk IDs and preview of first 100 characters.
    """

    def __init__(self, index: UserKBsIndex):
        self.index = index

    @property
    def name(self) -> str:
        return "user_kbs-list_document_chunks"

    @property
    def description(self) -> str:
        return (
            "List all chunks in a specific document.\n\n"
            "Returns a formatted list showing:\n"
            "- Chunk ID (use with other tools to get full content)\n"
            "- Preview of first 100 characters of the chunk\n\n"
            "**PARAMETERS**:\n"
            "- description: One-sentence description of the task being performed\n"
            "- doc_id: The document ID (required) - use user_kbs-list_kb_documents to see available documents\n\n"
            "Use the Chunk ID with user_kbs-get_chunk to retrieve full content and metadata of a specific chunk."
        )

    @property
    def properties(self) -> Dict[str, Dict[str, str]]:
        return {
            "description": {
                "type": "string",
                "description": "One-sentence description of the task being performed",
            },
            "doc_id": {
                "type": "string",
                "description": "Document ID (use user_kbs-list_kb_documents to see available documents)",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["description", "doc_id"]

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute list document chunks"""
        _ = kwargs.get("description")
        doc_id = kwargs.get("doc_id")

        if not doc_id:
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message="Error: 'doc_id' parameter is required",
                is_error=True,
            )

        logger.info(f"Listing chunks in document: {doc_id}")

        try:
            result = self.index.get_document_chunks(doc_id)

            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                content=[{"type": "text", "text": result, "uuid": func_call_id}],
            )

        except Exception as e:
            logger.error(f"Error in list_document_chunks tool: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message=f"Error listing chunks: {str(e)}",
                is_error=True,
            )
