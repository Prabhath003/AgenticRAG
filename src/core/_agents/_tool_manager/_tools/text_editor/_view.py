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

# import json

from ......config import Config
from .._variables import MAX_DIR_ITEMS_PER_LEVEL, MAX_FILE_SIZE
from .._base_tool import BaseTool
from .. import logger
from ......infrastructure.utils._file_utils import get_content_type

from .....models.agent.content_models import (
    DisplayContent,
    ToolResultContent,
)


class ViewTool(BaseTool):
    """Read file contents or list directory contents tool"""

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
        return "view"

    @property
    def description(self) -> str:
        return (
            "Read file contents or list directory contents. Accepts file or directory paths.\n"
            "• For files: Returns full content of text/writable files (UTF-8 encoded)\n"
            "• For directories: Lists all files and directories up to 2 levels deep with sizes\n\n"
            f"Working Directory: {self.working_directory}\n"
            f"User-facing outputs: {{working_dir}}/user-data/outputs/\n"
            f"Helper files: anywhere in {{working_dir}}\n"
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
            "For binary files (images, PDFs, compiled binaries, archives): Use Python with bash_tool to read and process them.\n"
            "Example: Write Python code to read binary file, then execute with bash_tool."
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
                "description": "Path to file or directory (relative to working directory). For directories, lists contents up to 2 levels deep.",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["description", "path"]

    def execute(self, func_call_id: str, **kwargs: Any) -> ToolResultContent:
        """Execute file read with given parameters"""

        _ = kwargs.get("description")
        file_path: Optional[str] = kwargs.get("path")
        if not file_path:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'path' parameter is required",
                is_error=True,
            )

        logger.info(f"View tool accessing: '{file_path}'")
        logger.info(f"👀 Viewing: {file_path}")

        try:
            # Resolve the path
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

            # Check if path exists
            if not target_path.exists():
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: Path not found: {file_path}",
                    is_error=True,
                )

            # Handle directory
            if target_path.is_dir():
                output = self._list_directory(target_path, file_path)
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    content=[
                        {
                            "type": "text",
                            "text": f"Here are the files and directories up to 2 levels deep in {file_path}, excluding hidden items and node_modules:\n{output}",
                            "uuid": func_call_id,
                        }
                    ],
                    is_error=False,
                    # display_content=DisplayContent(
                    #     type="json_block",
                    #     json_block=json.dumps({
                    #         "language": "text",
                    #         "code": output,
                    #         "filename": file_path
                    #     })
                    # )
                )

            # Handle file
            elif target_path.is_file():
                # Check if file is editable text (supported content type)
                __doc__ = Path(file_path).suffix.lower()
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
                        message=f"Error: Cannot view file '{file_path}' - unsupported content type '{content_type}'. This tool only supports editable text files. For binary files (PDF, images, archives, etc.), use the bash_tool with Python to read and process them.",
                        is_error=True,
                    )

                content = target_path.read_text()

                # Auto-detect language from file extension
                # file_ext = Path(file_path).suffix.lower()
                # language = EXTENSION_TO_LANGUAGE.get(file_ext, "")

                # Truncate content if it exceeds limit
                display_content_trunc, was_truncated, _, last_part_start = self._truncate_content(
                    content, file_path
                )
                # content_for_json = display_content_trunc if was_truncated else content

                truncation_notice = " (truncated for context)" if was_truncated else ""

                # Add line numbers with actual line numbers for truncated content
                if was_truncated:
                    numbered_content = self._add_line_numbers(
                        display_content_trunc,
                        start_line=1,
                        last_part_start=last_part_start,
                    )
                else:
                    numbered_content = self._add_line_numbers(display_content_trunc)

                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    content=[
                        {
                            "type": "text",
                            "text": f"Here's the content of {file_path}{truncation_notice} with line numbers:\n{numbered_content}",
                            "uuid": func_call_id,
                        }
                    ],
                    is_error=False,
                    # display_content=DisplayContent(
                    #     type="json_block",
                    #     json_block=json.dumps({
                    #         "language": language,
                    #         "code": content_for_json,
                    #         "filename": file_path
                    #     })
                    # )
                )
            else:
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: Path is neither a file nor a directory: {file_path}",
                    is_error=True,
                )

        except Exception as e:
            logger.error(f"Error viewing path: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message=f"Error viewing path: {str(e)}",
                is_error=True,
            )

    def _truncate_content(self, content: str, file_path: str) -> tuple[str, bool, int, int]:
        """
        Truncate content if it exceeds MAX_FILE_SIZE.
        Returns (truncated_content, was_truncated, first_part_lines, last_part_start_line)
        """
        if len(content) <= MAX_FILE_SIZE:
            return content, False, 0, 0

        # Show first and last portions with ellipsis
        chunk_size = MAX_FILE_SIZE // 2 - 100  # Leave space for ellipsis message
        first_part = content[:chunk_size]
        last_part = content[-chunk_size:]

        # Calculate line numbers
        first_part_lines = first_part.count("\n") + 1
        total_lines = content.count("\n") + 1
        # Ensure last_part always starts after first_part, even in files with no newlines
        last_part_start_line = max(total_lines - last_part.count("\n"), first_part_lines + 1)

        # Calculate byte sizes for each part
        first_part_bytes = len(first_part.encode("utf-8"))
        last_part_bytes = len(last_part.encode("utf-8"))
        hidden_bytes = len(content) - first_part_bytes - last_part_bytes

        truncated = (
            # f">>> START OF FILE (lines 1-{first_part_lines}, {first_part_bytes} bytes)\n"
            f"{first_part + '....'}\n"
            f"{'─' * 80}\n"
            f"[TRUNCATED] File too large ({len(content)} bytes, max {MAX_FILE_SIZE} bytes)\n"
            f"Hidden content: {hidden_bytes} bytes ({total_lines - first_part_lines - (last_part.count(chr(10)) + 1)} lines)\n"
            f"Resuming from line {last_part_start_line}...\n"
            f"{'─' * 80}\n"
            # f"<<< END OF FILE (lines {last_part_start_line}-{total_lines}, {last_part_bytes} bytes)\n"
            f"{'....' + last_part}"
        )
        logger.debug(
            f"total_lines: {total_lines}, first_part_lines: {first_part_lines}, last_part_start_line: {last_part_start_line}"
        )
        return truncated, True, first_part_lines, last_part_start_line

    def _add_line_numbers(self, content: str, start_line: int = 1, last_part_start: int = 0) -> str:
        """
        Add line numbers to content.
        For truncated files, handles actual line numbers across first and last parts.
        """
        lines = content.split("\n")

        # Find the truncation block boundaries
        truncation_start_idx = -1
        truncation_end_idx = -1

        for i, line in enumerate(lines):
            # Find the first separator line (starts with ─)
            if truncation_start_idx == -1 and line.startswith("─"):
                truncation_start_idx = i
            # Find the second separator line (after [TRUNCATED])
            elif truncation_start_idx != -1 and truncation_end_idx == -1 and line.startswith("─"):
                truncation_end_idx = i

        numbered_lines: List[str] = []

        if truncation_start_idx == -1:
            # Normal case: no truncation marker
            max_line_num = start_line + len(lines) - 1
            num_width = len(str(max_line_num))
            for i, line in enumerate(lines):
                line_num = start_line + i
                numbered_lines.append(f"{line_num:>{num_width}}\t{line}")
        else:
            # Truncated case: skip all metadata lines in the truncation block
            max_line_num = max(
                start_line + truncation_start_idx - 1,
                last_part_start + (len(lines) - truncation_end_idx - 2),
            )
            num_width = len(str(max_line_num))

            # First part: before truncation block (with line numbers)
            for i in range(truncation_start_idx):
                line_num = start_line + i
                numbered_lines.append(f"{line_num:>{num_width}}\t{lines[i]}")

            # Truncation block: all metadata lines (NO line numbers)
            for i in range(truncation_start_idx, truncation_end_idx + 1):
                numbered_lines.append(f"{'':>{num_width}}\t{lines[i]}")

            # Last part: after truncation block (with line numbers from last_part_start)
            last_part_content_idx = truncation_end_idx + 1
            for i in range(last_part_content_idx, len(lines)):
                line_num = last_part_start + (i - last_part_content_idx)
                numbered_lines.append(f"{line_num:>{num_width}}\t{lines[i]}")

        return "\n".join(numbered_lines)

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes to human-readable size"""
        size_float = float(size_bytes)
        for unit in ["B", "KB", "MB", "GB"]:
            if size_float < 1024:
                return f"{size_float:.1f}{unit}"
            size_float /= 1024
        return f"{size_float:.1f}TB"

    def _list_directory(
        self, dir_path: Path, display_path: str, level: int = 0, max_level: int = 2
    ) -> str:
        """
        List directory contents up to 2 levels deep with file sizes.
        Strategy: Show all level 1 items, limit level 2 items only if needed for context.
        """
        output = ""

        if level == 0:
            output += f"Directory: {display_path}\n"
            output += f"{'=' * 80}\n\n"

        if level >= max_level:
            return output

        try:
            items = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name))

            for item in items:
                indent = "  " * level
                prefix = "📁 " if item.is_dir() else "📄 "

                try:
                    if item.is_dir():
                        output += f"{indent}{prefix}{item.name}/\n"
                        if level < max_level:
                            # For level 2, check if we should limit items
                            if level == 1:
                                # Get child items count to decide if we should truncate
                                try:
                                    child_items = list(item.iterdir())
                                    child_count = len(child_items)

                                    # If current output is getting large, skip level 2 details
                                    if len(output) > MAX_FILE_SIZE - 1000:  # Leave some buffer
                                        output += f"{indent}  ⊢ ({child_count} items - skipped for context)\n"
                                    else:
                                        # Show first N items at level 2
                                        child_items = sorted(
                                            child_items,
                                            key=lambda x: (not x.is_dir(), x.name),
                                        )
                                        if child_count > MAX_DIR_ITEMS_PER_LEVEL:
                                            output += f"{indent}  ⊢ (Showing {MAX_DIR_ITEMS_PER_LEVEL} of {child_count} items)\n"
                                            child_items = child_items[:MAX_DIR_ITEMS_PER_LEVEL]

                                        for child in child_items:
                                            child_indent = "  " * (level + 1)
                                            child_prefix = "📁 " if child.is_dir() else "📄 "
                                            try:
                                                if child.is_dir():
                                                    output += f"{child_indent}{child_prefix}{child.name}/\n"
                                                else:
                                                    size = child.stat().st_size
                                                    size_str = self._format_size(size)
                                                    output += f"{child_indent}{child_prefix}{child.name} ({size_str})\n"
                                            except (PermissionError, OSError):
                                                output += f"{child_indent}{child_prefix}{child.name} (Error: access denied)\n"
                                except (PermissionError, OSError):
                                    output += f"{indent}  ⊢ (Permission denied)\n"
                            else:
                                output += self._list_directory(item, "", level + 1, max_level)
                    else:
                        size = item.stat().st_size
                        size_str = self._format_size(size)
                        output += f"{indent}{prefix}{item.name} ({size_str})\n"
                except (PermissionError, OSError) as e:
                    output += f"{indent}{prefix}{item.name} (Error: {str(e)})\n"

        except PermissionError:
            output += "  (Permission denied)\n"
        except Exception as e:
            output += f"  (Error: {str(e)})\n"

        return output

    def get_display_content(self, **kwargs: Any) -> Optional["DisplayContent"]:
        """Generate display content showing the file being read"""
        return None
