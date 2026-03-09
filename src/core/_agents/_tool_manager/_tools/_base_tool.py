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

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

from ....models.agent.content_models import (
    DisplayContent,
    ToolResultContent,
)


class BaseTool(ABC):
    """
    Abstract base tool class for all tools in the system.

    Each tool must implement:
    - name: Tool identifier
    - description: Human-readable description
    - properties: Dict of parameter properties
    - required: List of required parameter names
    - execute(): Main execution method with **kwargs
    """

    def __init__(self, **kwargs: Any):
        """
        Initialize tool with optional parameters.

        Args:
            **kwargs: Optional initialization parameters (subclasses can override)
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name identifier"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description"""
        pass

    @property
    @abstractmethod
    def properties(self) -> Dict[str, Dict[str, Any]]:
        """
        Define tool parameters.

        Returns:
            Dict mapping parameter name to {'type': '...', 'description': '...'}
            Types: 'string', 'integer', 'number', 'boolean', 'array', 'object'
        """
        pass

    @property
    @abstractmethod
    def required(self) -> List[str]:
        """
        Define required parameters.

        Returns:
            List of required parameter names
        """
        pass

    @abstractmethod
    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """
        Execute the tool with keyword arguments.

        Args:
            **kwargs: Tool parameters as keyword arguments

        Returns:
            ToolResultContent with message and optional display_content
        """
        pass

    def get_display_content(self, **kwargs: Any) -> Optional["DisplayContent"]:
        """
        Generate display content for the tool execution.

        Shows user what happened - can be overridden by subclasses.
        Default implementation returns None.

        Args:
            **kwargs: Tool parameters

        Returns:
            DisplayContent object or None
        """
        return None

    def get_tool_info(self) -> Dict[str, Any]:
        """
        Get OpenAI function tool schema.

        Uses properties and required lists to build the schema.

        Returns:
            Dictionary with 'type', 'function' containing name, description, parameters
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.properties,
                    "required": self.required,
                },
            },
        }
