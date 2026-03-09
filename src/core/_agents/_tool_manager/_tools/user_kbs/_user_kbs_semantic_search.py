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
Semantic Search Tool
Search chunks using vector similarity across knowledge bases
"""

from typing import Any, Dict, List

from .._base_tool import BaseTool
from . import logger

from .....models.agent.content_models import (
    ToolResultContent,
)
from ._user_kbs_index import UserKBsIndex


class SemanticSearchTool(BaseTool):
    """
    Search chunks using vector similarity search across knowledge bases.
    Finds semantically similar content based on meaning, not just keywords.
    """

    def __init__(self, index: UserKBsIndex):
        self.index = index

    @property
    def name(self) -> str:
        return "user_kbs-semantic_search"

    @property
    def description(self) -> str:
        return (
            "Search chunks using semantic/vector similarity search.\n\n"
            "This tool finds chunks with similar meaning to your query, not just keyword matches.\n"
            "Returns chunks sorted by relevance with full content.\n\n"
            "**PARAMETERS**:\n"
            "- description: One-sentence description of the task being performed\n"
            "- query: Search query text (required)\n"
            "- kb_ids: Optional comma-separated KB IDs to limit search (default: all KBs)\n"
            "- doc_ids: Optional comma-separated Document IDs to limit search\n"
            "- n_results: Number of results to return (default: 10, max: 50)\n\n"
            "**EXAMPLES**:\n"
            "- query='project timeline and deadlines'\n"
            "- query='budget allocation' kb_ids='kb_001,kb_002' n_results=5\n"
            "- query='implementation details' doc_ids='doc_123,doc_456'\n\n"
            "**USE CASES**:\n"
            "- Find related content across multiple documents\n"
            "- Semantic similarity matching for research\n"
            "- Discover relevant chunks for a topic or concept\n"
            "- Multi-KB knowledge base exploration"
        )

    @property
    def properties(self) -> Dict[str, Dict[str, str]]:
        return {
            "description": {
                "type": "string",
                "description": "One-sentence description of the task being performed",
            },
            "query": {
                "type": "string",
                "description": "Search query text",
            },
            "kb_ids": {
                "type": "string",
                "description": "Optional comma-separated KB IDs to limit search (default: all KBs)",
            },
            "doc_ids": {
                "type": "string",
                "description": "Optional comma-separated Document IDs to limit search",
            },
            "n_results": {
                "type": "integer",
                "description": "Number of results to return (default: 10, max: 50)",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["description", "query"]

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute semantic search"""
        _ = kwargs.get("description")
        query = kwargs.get("query")
        kb_ids_str = kwargs.get("kb_ids")
        doc_ids_str = kwargs.get("doc_ids")
        n_results = kwargs.get("n_results", 10)

        if not query:
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message="Error: 'query' parameter is required",
                is_error=True,
            )

        # Parse comma-separated IDs
        kb_ids = [k.strip() for k in kb_ids_str.split(",")] if kb_ids_str else None
        doc_ids = [d.strip() for d in doc_ids_str.split(",")] if doc_ids_str else None

        # Validate n_results
        try:
            n_results = min(int(n_results), 50) if n_results else 10
        except (ValueError, TypeError):
            n_results = 10

        logger.info(
            f"Semantic search: query='{query[:100]}...', kb_ids={kb_ids}, n_results={n_results}"
        )

        try:
            result = self.index.query_chunks(
                query_text=query,
                kb_ids=kb_ids,
                doc_ids=doc_ids,
                n_results=n_results,
            )

            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                content=[{"type": "text", "text": result, "uuid": func_call_id}],
            )

        except Exception as e:
            logger.error(f"Error in semantic_search tool: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                name=self.name,
                message=f"Error during semantic search: {str(e)}",
                is_error=True,
            )
