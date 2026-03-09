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
List Knowledge Base Documents Tool
Lists all documents in a knowledge base with IDs and file names
"""

from typing import Any, Dict, List

from .._base_tool import BaseTool
from . import logger

from .....models.agent.content_models import (
    ToolResultContent,
)
from ._user_kbs_index import UserKBsIndex


class ListKBDocumentsTool(BaseTool):
    """
    List all documents in a specific knowledge base.
    Provides LLM-passable string format showing document IDs and file names.
    """

    def __init__(self, index: UserKBsIndex):
        self.index = index

    @property
    def name(self) -> str:
        return "user_kbs-list_kb_documents"

    @property
    def description(self) -> str:
        return (
            "List all documents in a specific knowledge base.\n\n"
            "Returns a formatted list showing:\n"
            "- Document ID (use with other tools)\n"
            "- File name of the document\n\n"
            "**PARAMETERS**:\n"
            "- description: One-sentence description of the task being performed\n"
            "- kb_id: The knowledge base ID (required) - use user_kbs-list_all_kbs to see available KBs\n\n"
            "Use the Document ID with user_kbs-list_document_chunks to view chunks within a document."
        )

    @property
    def properties(self) -> Dict[str, Dict[str, str]]:
        return {
            "description": {
                "type": "string",
                "description": "One-sentence description of the task being performed",
            },
            "kb_id": {
                "type": "string",
                "description": "Knowledge base ID (use user_kbs-list_all_kbs to see available KBs)",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["description", "kb_id"]

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute list KB documents"""
        _ = kwargs.get("description")
        kb_id = kwargs.get("kb_id")

        if not kb_id:
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message="Error: 'kb_id' parameter is required",
                is_error=True,
            )

        logger.info(f"Listing documents in KB: {kb_id}")

        try:
            result = self.index.list_documents_in_kb(kb_id)

            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                content=[{"type": "text", "text": result, "uuid": func_call_id}],
            )

        except Exception as e:
            logger.error(f"Error in list_kb_documents tool: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message=f"Error listing documents: {str(e)}",
                is_error=True,
            )
