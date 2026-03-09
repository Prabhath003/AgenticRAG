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
List User Knowledge Bases Tool
Lists all knowledge bases with titles and descriptions
"""

from typing import Any, Dict, List

from .._base_tool import BaseTool
from . import logger

from .....models.agent.content_models import (
    ToolResultContent,
)
from ._user_kbs_index import UserKBsIndex


class ListAllKBsTool(BaseTool):
    """
    List all user knowledge bases with their titles and descriptions.
    Provides LLM-passable string format showing KB IDs, titles, and descriptions.
    """

    def __init__(self, index: UserKBsIndex):
        self.index = index

    @property
    def name(self) -> str:
        return "user_kbs-list_all_kbs"

    @property
    def description(self) -> str:
        return (
            "List all knowledge bases available to the user.\n\n"
            "Returns a formatted list showing:\n"
            "- Knowledge Base ID (use with other tools)\n"
            "- Title of the knowledge base\n"
            "- Description of the knowledge base\n\n"
            "Use the KB ID with other tools like user_kbs-list_kb_documents "
            "to explore documents within a specific knowledge base."
        )

    @property
    def properties(self) -> Dict[str, Dict[str, str]]:
        return {
            "description": {
                "type": "string",
                "description": "One-sentence description of the task being performed",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["description"]

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute list all knowledge bases"""
        _ = kwargs.get("description")

        logger.info("Listing all user knowledge bases")

        try:
            result = self.index.list_knowledge_bases()

            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                content=[{"type": "text", "text": result, "uuid": func_call_id}],
            )

        except Exception as e:
            logger.error(f"Error in list_all_kbs tool: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message=f"Error listing knowledge bases: {str(e)}",
                is_error=True,
            )
