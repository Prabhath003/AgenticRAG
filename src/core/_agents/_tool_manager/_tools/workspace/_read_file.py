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

from ......config import Config
from .._base_tool import BaseTool
from .. import logger
from .._variables import MAX_FILE_SIZE
from ......infrastructure.utils._file_utils import get_content_type

from .....models.agent.content_models import (
    DisplayContent,
    ToolResultContent,
)


class ReadFileTool(BaseTool):
    """Read file contents tool"""

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
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read contents of text/writable files (UTF-8 encoded). Use this tool with priority over bash_tool for reading text files.\n"
            "Supports line range selection for efficient reading of large files (from_line and to_line parameters).\n\n"
            f"Working Directory: {self.working_directory}\n"
            f"User-facing outputs: {{working_dir}}/user-data/outputs/\n"
            f"Helper files: anywhere in {{working_dir}}\n"
            "**It is very important to follow the above format**\n\n"
            "Supported: Plain text, code files, configuration files, JSON, YAML, Markdown, source code, etc.\n"
            "Unsupported: Binary files, images, archives, compiled code\n\n"
            "For binary files (images, PDFs, compiled binaries, archives): Use Python with bash_tool to read and process them.\n"
            "Example: Write Python code to read binary file, then execute with bash_tool.\n"
            "(Note: use show_files tool if user also need to see the file)"
        )

    @property
    def properties(self) -> Dict[str, Dict[str, str]]:
        return {
            "description": {
                "type": "string",
                "description": "One-sentence description of the task being performed",
            },
            "path": {
                "type": "string",
                "description": "Path to the file to read (relative to working directory)",
            },
            "from_line": {
                "type": "integer",
                "description": "Optional: Starting line number (1-indexed). If omitted, reads from the beginning",
            },
            "to_line": {
                "type": "integer",
                "description": "Optional: Ending line number (1-indexed, inclusive). If omitted, reads to the end",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["description", "path"]

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute file read with given parameters"""

        _ = kwargs.get("description")
        file_path = kwargs.get("path")
        from_line = kwargs.get("from_line")
        to_line = kwargs.get("to_line")

        if not file_path:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'path' parameter is required",
                is_error=True,
            )

        logger.info(f"ReadFileTool reading: '{file_path}'")
        log_msg = f"📖 Reading file: {file_path}"
        if from_line is not None or to_line is not None:
            log_msg += f" (lines {from_line or 1}-{to_line or 'end'})"
        logger.info(log_msg)

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

            # Check if file exists
            if not target_path.exists():
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: File not found: {file_path}",
                    is_error=True,
                )

            # Read the file
            if not target_path.is_file():
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: Path is not a file: {file_path}",
                    is_error=True,
                )

            # Check if file is editable text (supported content type)
            _ = Path(file_path).suffix.lower()
            content_type = get_content_type(Path(file_path).name)

            # Only allow editable text content types (text/*, application/json, application/xml)
            if not (
                content_type.startswith("text/")
                or content_type in ["application/json", "application/xml"]
            ):
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: Cannot read file '{file_path}' - unsupported content type '{content_type}'. This tool only supports editable text files. For binary files (PDF, images, archives, etc.), use the bash_tool with Python to read and process them.",
                    is_error=True,
                )

            # Read file content (optionally with line range)
            if from_line is not None or to_line is not None:
                content, line_info = self._read_lines(target_path, from_line, to_line)
                if isinstance(content, str) and content.startswith("Error:"):  # type: ignore
                    return ToolResultContent(
                        tool_call_id=func_call_id,
                        type="tool_result",
                        name=self.name,
                        message=content,
                        is_error=True,
                    )
                if not line_info:  # Check if line_info is empty (error case)
                    return ToolResultContent(
                        tool_call_id=func_call_id,
                        type="tool_result",
                        name=self.name,
                        message=f"Error: Failed to read lines from {file_path}",
                        is_error=True,
                    )
                numbered_content = self._add_line_numbers(content, line_info["start"])
                output = f"Here's the content of {file_path} (lines {line_info['start']}-{line_info['end']} of {line_info['total']} total) with line numbers:\n\n{numbered_content}"
            else:
                content = target_path.read_text()

                # Truncate content if it exceeds limit with visual indicators
                display_content_trunc, was_truncated, _ = self._truncate_content(content)
                total_lines = content.count("\n") + 1

                truncation_info = ""
                if was_truncated:
                    truncation_info = f" (truncated for context, {total_lines} total lines)"
                else:
                    truncation_info = f" ({total_lines} lines)"

                # Add line numbers
                numbered_content = self._add_line_numbers(display_content_trunc)

                output = f"Here's the content of {file_path}{truncation_info} with line numbers:\n\n{numbered_content}"

            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                content=[{"type": "text", "text": output, "uuid": func_call_id}],
                name=self.name,
                is_error=False,
            )

        except Exception as e:
            logger.error(f"Error reading file: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message=f"Error reading file: {str(e)}",
                is_error=True,
            )

    def _read_lines(
        self,
        file_path: Path,
        from_line: Optional[int] = None,
        to_line: Optional[int] = None,
    ) -> tuple[str, Dict[str, int]]:
        """
        Read specific line range from file efficiently.
        Returns (content, line_info) where line_info contains start, end, total line counts.
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            total_lines = len(lines)

            # Validate and normalize line numbers (1-indexed)
            start = from_line if from_line is not None else 1
            end = to_line if to_line is not None else total_lines

            # Validate bounds
            if start < 1 or end < 1:
                return "Error: Line numbers must be >= 1", {}
            if start > total_lines:
                return (
                    f"Error: Starting line {start} is beyond file length ({total_lines} lines)",
                    {},
                )
            if start > end:
                return (
                    f"Error: Starting line ({start}) cannot be greater than ending line ({end})",
                    {},
                )

            # Clamp end to file length
            end = min(end, total_lines)

            # Extract lines (convert from 1-indexed to 0-indexed)
            selected_lines = lines[start - 1 : end]
            content = "".join(selected_lines).rstrip("\n")

            return content, {"start": start, "end": end, "total": total_lines}

        except Exception as e:
            return f"Error reading lines: {str(e)}", {}

    def _truncate_content(self, content: str) -> tuple[str, bool, int]:
        """
        Truncate content if it exceeds MAX_FILE_SIZE.
        Returns (truncated_content, was_truncated, last_part_start_line)
        Shows first portion with visual separator and truncation info.
        Users can use from_line/to_line parameters to read specific sections.
        """
        if len(content) <= MAX_FILE_SIZE:
            return content, False, 0

        # Show first part only
        truncated_content = content[:MAX_FILE_SIZE]

        # Calculate line numbers
        first_part_lines = truncated_content.count("\n") + 1
        total_lines = content.count("\n") + 1

        truncated = (
            f"{truncated_content}....\n"
            f"{'─' * 80}\n"
            f"[TRUNCATED] File too large ({len(content)} bytes, max {MAX_FILE_SIZE} bytes)\n"
            f"Showing first {MAX_FILE_SIZE} bytes ({first_part_lines} lines) of {total_lines} total lines\n"
            f"Use 'from_line' and 'to_line' parameters to read specific sections\n"
            f"{'─' * 80}"
        )

        logger.debug(f"total_lines: {total_lines}, first_part_lines: {first_part_lines}")
        return truncated, True, 0

    def _add_line_numbers(self, content: str, start_line: int = 1) -> str:
        """
        Add line numbers to content.
        For truncated files, truncation metadata lines don't get line numbers.
        """
        lines = content.split("\n")

        # Find the truncation block start (first separator line with ─)
        truncation_start_idx = -1
        for i, line in enumerate(lines):
            if line.startswith("─"):
                truncation_start_idx = i
                break

        numbered_lines: List[str] = []

        if truncation_start_idx == -1:
            # Normal case: no truncation marker
            max_line_num = start_line + len(lines) - 1
            num_width = len(str(max_line_num))
            for i, line in enumerate(lines):
                line_num = start_line + i
                numbered_lines.append(f"{line_num:>{num_width}}\t{line}")
        else:
            # Truncated case: number content before truncation block, skip metadata lines
            max_line_num = start_line + truncation_start_idx - 1
            num_width = len(str(max_line_num))

            # Content before truncation block (with line numbers)
            for i in range(truncation_start_idx):
                line_num = start_line + i
                numbered_lines.append(f"{line_num:>{num_width}}\t{lines[i]}")

            # Truncation block metadata lines (NO line numbers)
            for i in range(truncation_start_idx, len(lines)):
                numbered_lines.append(f"{'':>{num_width}}\t{lines[i]}")

        return "\n".join(numbered_lines)

    def get_display_content(self, **kwargs: Any) -> Optional["DisplayContent"]:
        """Generate display content showing the file being read"""
        return None
