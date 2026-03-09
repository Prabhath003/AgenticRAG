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

from typing import Any, List, Dict, Optional
from pathlib import Path
from datetime import datetime, timezone
import json

from ......config import Config
from .._variables import EXTENSION_TO_LANGUAGE
from .._base_tool import BaseTool
from .. import logger
from ......infrastructure.utils._file_utils import get_content_type

from .....models.agent.content_models import (
    DisplayContent,
    ToolResultContent,
)
from .....models.agent import ConversationFileMetadata


class CreateFileTool(BaseTool):
    """Create a new file with content"""

    conversation_id: Optional[str] = None
    working_directory: str = Config.TERMINAL_CACHE_DIR

    def __init__(self, conversation_id: Optional[str] = None):
        self.conversation_id = conversation_id if conversation_id else "default"
        self._setup_working_directory()

    def _setup_working_directory(self):
        """Set up the conversation-specific working directory"""
        try:
            terminal_cache_base = Path(Config.TERMINAL_CACHE_DIR)
            terminal_cache_base.mkdir(parents=True, exist_ok=True)
            conversation_dir = terminal_cache_base / str(self.conversation_id)
            conversation_dir.mkdir(parents=True, exist_ok=True)
            self.working_directory = str(conversation_dir.resolve())
        except Exception as e:
            logger.error(f"Error setting up working directory: {e}")
            self.working_directory = str(Path.cwd())

    @property
    def name(self) -> str:
        return "create_file"

    @property
    def description(self) -> str:
        return (
            "Create new text/writable files (UTF-8 encoded). Use this tool with priority over bash_tool for text file creation.\n\n"
            f"Working Directory: {self.working_directory}\n"
            f"User-facing outputs: {{working_dir}}/user-data/outputs/\n"
            f"Helper files for internal processing: anywhere in {{working_dir}}\n"
            "**It is very important to follow the above format**\n\n"
            "Supported file types:\n"
            "• Code: .py, .js, .ts, .tsx, .jsx, .java, .cpp, .c, .cs, .rb, .go, .rs, .php, .swift, .kt, .scala, .pl, .lua, .jl, .r\n"
            "• Web: .html, .css, .scss, .sass, .less, .xml\n"
            "• Config: .json, .yaml, .yml, .toml, .ini, .cfg, .conf, .gradle, .makefile, .Dockerfile\n"
            "• Markup: .md, .tex\n"
            "• Shell: .sh, .bash, .zsh, .fish, .ps1\n"
            "• Database: .sql\n"
            "• Text: .txt, .text, .log\n"
            "Unsupported: Binary files, images, archives, compiled code\n\n"
            "For binary files (images, PDFs, archives, compiled binaries): Use Python with bash_tool to create and write them.\n"
            "Example: Write Python code to generate binary file, then execute with bash_tool."
        )

    @property
    def properties(self) -> Dict[str, Dict[str, str]]:
        return {
            "show": {
                "type": "boolean",
                "description": "Set to True to display the created file with syntax highlighting to the user. Set to False to create the file silently without displaying it. Use True when the file content should be visible to the user (e.g., code files, configurations), False for internal/helper files.",
            },
            "description": {
                "type": "string",
                "description": "One-sentence description of the task being performed",
            },
            "path": {
                "type": "string",
                "description": "Path to the file to create (relative to working directory)",
            },
            "file_text": {
                "type": "string",
                "description": "Content to write to the file",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["show", "description", "path", "file_text"]

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute file creation with given parameters"""
        _ = kwargs.get("description")
        file_path: Optional[str] = kwargs.get("path")
        file_text: Optional[str] = kwargs.get("file_text")
        show = kwargs.get("show", True)

        if not file_path:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'path' parameter is required",
                is_error=True,
            )

        if file_text is None:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'file_text' parameter is required",
                is_error=True,
            )

        logger.info(f"CreateFileTool creating: '{file_path}'")
        logger.info(f"✏️ Creating file: {file_path}")

        try:
            # Resolve the file path
            target_path = (Path(self.working_directory) / file_path).resolve()
            working_path = Path(self.working_directory).resolve()

            # Validate path is within working directory
            try:
                target_path.relative_to(working_path)
            except ValueError:
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: Access denied. Path is outside working directory: {file_path}",
                    is_error=True,
                )

            # Check if file extension is supported (plain text only)
            file_ext = Path(file_path).suffix.lower()
            content_type = get_content_type(Path(file_path).name)

            # Only allow plain text content types (text/*, application/json, application/xml)
            if not (
                content_type.startswith("text/")
                or content_type in ["application/json", "application/xml"]
            ):
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: Cannot create file '{file_path}' - unsupported file type '{file_ext}' (content type: {content_type}). This tool only supports plain text files. For binary files (PDF, images, archives, etc.), use the bash_tool with Python to create and write the file content.",
                    is_error=True,
                )

            # Create parent directories if needed
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Write the file
            target_path.write_text(str(file_text))

            file_size = len(str(file_text).encode("utf-8"))
            now = datetime.now(timezone.utc)

            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                content=[
                    {
                        "type": "text",
                        "text": f"File created successfully: {file_path} ({file_size} bytes)",
                        "uuid": func_call_id,
                    }
                ],
                is_error=False,
                display_content=(
                    DisplayContent(type="text", text=f"File created successfully: {file_path}")
                    if show
                    else None
                ),
                files=(
                    [
                        ConversationFileMetadata(
                            path=file_path,
                            size=file_size,
                            content_type=content_type,
                            created_at=now,
                            modified_at=now,
                            custom_metadata={
                                "extension": file_ext,
                                "language": EXTENSION_TO_LANGUAGE.get(file_ext, "unknown"),
                            },
                            version=1,
                        )
                    ]
                    if show
                    else None
                ),
            )

        except Exception as e:
            logger.error(f"Error creating file: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message=f"Error creating file: {str(e)}",
                is_error=True,
            )

    def get_display_content(self, **kwargs: Any) -> Optional["DisplayContent"]:
        """Generate display content showing the file being created"""
        try:
            path = kwargs.get("path", "N/A")
            file_text = kwargs.get("file_text", "")
            show = kwargs.get("show", True)

            if show:
                # Auto-detect language from file extension
                file_ext = Path(path).suffix.lower()
                language = EXTENSION_TO_LANGUAGE.get(file_ext, "")

                display_info: Dict[str, Any] = {
                    "language": language,
                    "code": file_text,
                    "filename": path,
                }

                return DisplayContent(
                    type="json_block", json_block=json.dumps(display_info, indent=2)
                )
            else:
                return None
        except Exception as e:
            logger.error(f"Error generating display content for create_file: {e}")
            return None
