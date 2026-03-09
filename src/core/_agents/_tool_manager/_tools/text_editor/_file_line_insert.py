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
import json
from difflib import SequenceMatcher
from datetime import datetime, timezone

from ......config import Config
from .._variables import EXTENSION_TO_LANGUAGE
from .._base_tool import BaseTool
from .. import logger
from .__edit_history import EditHistoryManager
from ......infrastructure.utils._file_utils import get_content_type

from .....models.agent.content_models import (
    DisplayContent,
    ToolResultContent,
)
from .....models.agent import ConversationFileMetadata


class FileLineInsertTool(BaseTool):
    """Insert text at a specific line in a file"""

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
        return "file_line_insert"

    @property
    def description(self) -> str:
        return (
            "Insert text at a specific line in text/writable files. Use this tool to insert new lines at desired positions.\n\n"
            f"Working Directory: {self.working_directory}\n"
            f"User-facing outputs: {{working_dir}}/user-data/outputs/\n"
            f"Helper files: anywhere in {{working_dir}}\n"
            "**It is very important to follow the above format**\n\n"
            "Supported: Plain text, code files, configuration files, JSON, YAML, Markdown, source code, etc.\n"
            "Unsupported: Binary files, images, archives, compiled code\n\n"
            "Inserts the provided text after the specified line number (use 0 to insert at beginning).\n\n"
            "EXACT INPUT TEMPLATE:\n"
            '{"show": <bool>, "description": "<task>", "path": "<file>", "insert_line": <line_number>, "insert_text": "<text_to_insert>"}\n'
        )

    @property
    def properties(self) -> Dict[str, Dict[str, str]]:
        return {
            "show": {
                "type": "boolean",
                "description": "Set to True to display the edited file with syntax highlighting to the user. Set to False to edit the file silently without displaying it. Use True when the file content should be visible to the user (e.g., code files, configurations), False for internal/helper files.",
            },
            "description": {
                "type": "string",
                "description": "One-sentence description of the task being performed",
            },
            "path": {
                "type": "string",
                "description": "Path to the file to modify (relative to working directory)",
            },
            "insert_line": {
                "type": "integer",
                "description": "The line number after which to insert the text (0 for beginning of file)",
            },
            "insert_text": {"type": "string", "description": "The text to insert"},
        }

    @property
    def required(self) -> List[str]:
        return ["show", "description", "path", "insert_line", "insert_text"]

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute line insertion with given parameters"""
        _ = kwargs.get("description")
        show = kwargs.get("show", True)
        file_path: Optional[str] = kwargs.get("path")
        insert_line: Optional[int] = kwargs.get("insert_line")
        insert_text: Optional[str] = kwargs.get("insert_text")

        if not file_path:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'path' parameter is required",
                is_error=True,
            )

        if insert_line is None:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'insert_line' parameter is required",
                is_error=True,
            )

        if insert_text is None:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'insert_text' parameter is required",
                is_error=True,
            )

        logger.info(f"LineInsertFileTool inserting in: '{file_path}'")
        logger.info(f"🔄 Inserting text at line {insert_line} in file: {file_path}")

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
                    display_content=self.get_display_content(**kwargs),
                )

            # Check if file exists
            if not target_path.exists():
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: File not found: {file_path}",
                    is_error=True,
                    display_content=self.get_display_content(**kwargs),
                )

            if not target_path.is_file():
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: Path is not a file: {file_path}",
                    is_error=True,
                    display_content=self.get_display_content(**kwargs),
                )

            # Check if file is plain text (supported content type)
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
                    message=f"Error: Cannot edit file '{file_path}' - unsupported content type '{content_type}'. This tool only supports plain text files. For binary files (PDF, images, archives, etc.), use the bash_tool with Python to read, modify, and write the file content.",
                    is_error=True,
                )

            # Read the file
            content = target_path.read_text()
            original_lines = content.split("\n")
            lines = original_lines.copy()

            # Validate insert_line is within valid range (0 to line count)
            insert_line = int(insert_line)
            if insert_line < 0 or insert_line > len(lines):
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: insert_line {insert_line} out of range (0-{len(lines)})",
                    is_error=True,
                )

            # Insert the text at the specified line
            # insert_line=0 means insert at beginning, insert_line=n means after line n
            lines.insert(insert_line, str(insert_text))
            new_content = "\n".join(lines)

            # Auto-track edit history (save snapshot before writing changes)
            history_manager = EditHistoryManager(self.working_directory)
            original_content = "\n".join(original_lines)

            # Determine current version based on edit history count
            history_dir = (
                Path(self.working_directory)
                / ".edit_history"
                / f"{Path(file_path).stem}{Path(file_path).suffix}"
            )
            current_version = 1
            if history_dir.exists():
                history_entries = list(history_dir.glob("*.json"))
                current_version = len(history_entries) + 1

            history_manager.save_snapshot(
                file_path=file_path,
                content=original_content,
                operation="file_line_insert",
                tool_name=self.name,
                current_version=current_version,
            )
            # Cleanup old history entries beyond 50
            history_manager.cleanup_history(file_path, keep_count=50)

            # Write the updated content
            target_path.write_text(new_content)
            modified_lines = new_content.split("\n")

            # Calculate line changes using edit distance: analyze sequence differences
            matcher = SequenceMatcher(None, original_lines, modified_lines)
            opcodes = matcher.get_opcodes()

            # Count insertions and deletions from opcodes
            new_lines_added = 0
            old_lines_removed = 0

            for tag, i1, i2, j1, j2 in opcodes:
                if tag == "delete":
                    old_lines_removed += i2 - i1
                elif tag == "insert":
                    new_lines_added += j2 - j1
                elif tag == "replace":
                    # For replace operations, count both removed and added
                    old_lines_removed += i2 - i1
                    new_lines_added += j2 - j1

            # Build output message
            output = f"✅ Successfully inserted text in file: {file_path}\n"
            output += f"Inserted at line: {insert_line}\n"
            output += (
                f"📄 {file_path}: {new_lines_added} lines added, {old_lines_removed} lines removed"
            )

            # Build display content with file change information
            display_json: Dict[str, Any] = {
                "file": file_path,
                "new_lines_added": new_lines_added,
                "old_lines_removed": old_lines_removed,
            }
            display_content = DisplayContent(type="json_block", json_block=json.dumps(display_json))

            # Calculate file metadata
            file_size = len(new_content.encode("utf-8"))
            # Get file timestamps from filesystem
            file_stat = target_path.stat()
            created_at = datetime.fromtimestamp(file_stat.st_ctime, tz=timezone.utc)
            modified_at = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)

            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                content=[{"type": "text", "text": output, "uuid": func_call_id}],
                is_error=False,
                display_content=display_content,
                files=(
                    [
                        ConversationFileMetadata(
                            path=file_path,
                            size=file_size,
                            content_type=content_type,
                            created_at=created_at,
                            modified_at=modified_at,
                            custom_metadata={
                                "extension": file_ext,
                                "language": EXTENSION_TO_LANGUAGE.get(file_ext, "unknown"),
                            },
                            version=current_version + 1,
                        )
                    ]
                    if show
                    else None
                ),
            )

        except Exception as e:
            logger.error(f"Error inserting text in file: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message=f"Error inserting text: {str(e)}",
                is_error=True,
            )

    def get_display_content(self, **kwargs: Any) -> Optional["DisplayContent"]:
        """Generate display content showing the insertion parameters"""
        return None
