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
import re
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


class EditFileTool(BaseTool):
    """Edit/update file content"""

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
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit text/writable files with flexible replacement modes. Use this tool with priority over bash_tool for text file edits.\n\n"
            f"Working Directory: {self.working_directory}\n"
            f"User-facing outputs: {{working_dir}}/user-data/outputs/\n"
            f"Helper files: anywhere in {{working_dir}}\n"
            "**It is very important to follow the above format**\n\n"
            "Supported: Plain text, code files, configuration files, JSON, YAML, Markdown, source code, etc.\n"
            "Unsupported: Binary files, images, archives, compiled code\n\n"
            "THREE REPLACEMENT MODES:\n"
            "1. STRING: Exact string matching with required line_number or count when string appears multiple times\n"
            "2. REGEX: Pattern matching with regex support, flags (i=case-insensitive, m=multiline, s=dotall)\n"
            "3. LINE: Replace specific line ranges with new content\n\n"
            "FEATURES:\n"
            "• Count-limited replacements: replace only first N occurrences\n"
            "• Line-based targeting: REQUIRED if string appears multiple times - specify line_number (1-indexed) to disambiguate\n"
            "• Regex patterns: match complex patterns with optional flags\n"
            "• Line range operations: replace entire line ranges with new content\n\n"
            "EXACT INPUT TEMPLATE:\n"
            '{"show": <bool>, "description": "<task>", "path": "<file>", "replacements": [...]}\n\n'
            "STRING REPLACEMENT:\n"
            '{"type": "string", "old_str": "find_this", "new_str": "replace_with", "count": <optional>, "line_number": <required if duplicate>}\n'
            "NOTE: line_number is REQUIRED (1-indexed) if the string appears multiple times in the file. Use count as alternative to target first N occurrences.\n\n"
            "REGEX REPLACEMENT:\n"
            '{"type": "regex", "pattern": "regex_pattern", "replacement": "replace_with", "count": <optional>, "flags": "im"}\n\n'
            "LINE REPLACEMENT:\n"
            '{"type": "line", "from_line": 10, "to_line": 15, "content": "new multi\\nline\\ncontent"}\n\n'
            "CONCRETE EXAMPLE:\n"
            "{\n"
            '  "show": true,\n'
            '  "description": "Update API config for production",\n'
            '  "path": "src/config/api.py",\n'
            '  "replacements": [\n'
            '    {"type": "string", "old_str": "API_KEY = \\"old_key\\"", "new_str": "AUTH_TOKEN = \\"new_key\\""},\n'
            '    {"type": "string", "old_str": "localhost:8000", "new_str": "prod.api.com", "count": 1},\n'
            '    {"type": "regex", "pattern": "http://", "replacement": "https://", "flags": "i"},\n'
            '    {"type": "line", "from_line": 20, "to_line": 25, "content": "# Updated timeout\\nTIMEOUT = 60"}\n'
            "  ]\n"
            "}\n\n"
            "For binary file modifications: Use Python with bash_tool to read, modify, and write binary content."
        )

    @property
    def properties(self) -> Dict[str, Dict[str, Any]]:
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
                "description": "File path relative to working directory",
            },
            "replacements": {
                "type": "array",
                "description": "List of replacement operations (string, regex, or line-based)",
                "items": {"type": "object"},
            },
        }

    @property
    def required(self) -> List[str]:
        return ["show", "description", "path", "replacements"]

    def _apply_string_replacement(
        self, content: str, replacement: Dict[str, Any]
    ) -> tuple[str, int]:
        """Apply string-based replacement with optional count limit and line targeting"""
        old_str = str(replacement.get("old_str", ""))
        new_str = str(replacement.get("new_str", ""))
        count = replacement.get("count")  # None means replace all
        line_number: Optional[int] = replacement.get("line_number")  # 1-indexed line number

        if not old_str:
            raise ValueError("String replacement requires 'old_str' field")

        # Check if pattern appears multiple times
        occurrences = content.count(old_str)
        if occurrences == 0:
            raise ValueError(f"Pattern not found: {repr(old_str[:50])}")

        # Require line_number or count if pattern appears multiple times
        if occurrences > 1 and not line_number and not count:
            raise ValueError(
                f"Pattern '{old_str[:50]}' appears {occurrences} times in file. Must specify 'line_number' (1-indexed) or 'count' to disambiguate"
            )

        if line_number:
            # Line-targeted replacement
            lines: List[str] = content.split("\n")
            if line_number < 1 or line_number > len(lines):
                raise ValueError(f"Line number {line_number} out of range (1-{len(lines)})")

            line_idx = line_number - 1
            if old_str not in lines[line_idx]:
                raise ValueError(f"Pattern not found on line {line_number}: {repr(old_str[:50])}")

            lines[line_idx] = lines[line_idx].replace(old_str, new_str, count or -1)
            return "\n".join(lines), 1
        else:
            # Standard replacement with optional count limit
            if count and count < occurrences:
                # Replace only first N occurrences
                result = content
                for _ in range(count):
                    result = result.replace(old_str, new_str, 1)
                return result, count
            else:
                # Replace all occurrences
                return content.replace(old_str, new_str), occurrences

    def _apply_regex_replacement(
        self, content: str, replacement: Dict[str, Any]
    ) -> tuple[str, int]:
        """Apply regex-based replacement with optional count limit"""
        pattern = str(replacement.get("pattern", ""))
        replacement_str = str(replacement.get("replacement", ""))
        count = replacement.get("count")  # None means replace all
        flags_str = replacement.get("flags", "")

        if not pattern:
            raise ValueError("Regex replacement requires 'pattern' field")

        # Parse flags
        regex_flags = 0
        if "i" in flags_str.lower():
            regex_flags |= re.IGNORECASE
        if "m" in flags_str.lower():
            regex_flags |= re.MULTILINE
        if "s" in flags_str.lower():
            regex_flags |= re.DOTALL

        try:
            compiled_pattern = re.compile(pattern, regex_flags)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {str(e)}")

        matches = list(compiled_pattern.finditer(content))
        if not matches:
            raise ValueError(f"Pattern not found: {pattern}")

        # Replace with optional count limit
        result = compiled_pattern.sub(replacement_str, content, count=count or 0)
        actual_replacements = len(matches) if not count else min(count, len(matches))
        return result, actual_replacements

    def _apply_line_replacement(self, content: str, replacement: Dict[str, Any]) -> tuple[str, int]:
        """Replace specific line ranges with new content"""
        from_line = replacement.get("from_line")
        to_line = replacement.get("to_line")
        new_content = str(replacement.get("content", ""))

        if from_line is None or to_line is None:
            raise ValueError("Line replacement requires 'from_line' and 'to_line' fields")

        lines = content.split("\n")
        if (
            from_line < 1
            or to_line < 1
            or from_line > len(lines)
            or to_line > len(lines)
            or from_line > to_line
        ):
            raise ValueError(
                f"Invalid line range: {from_line}-{to_line} (file has {len(lines)} lines)"
            )

        # Replace lines (1-indexed)
        from_idx = from_line - 1
        to_idx = to_line  # Exclusive end
        lines[from_idx:to_idx] = new_content.split("\n")
        return "\n".join(lines), 1

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute file edits with flexible replacement modes"""
        _ = kwargs.get("description")
        show: bool = kwargs.get("show", True)
        file_path: Optional[str] = kwargs.get("path")
        replacements: List[Dict[str, Any]] = kwargs.get("replacements", [])

        if not file_path:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'path' parameter is required",
                is_error=True,
            )

        if not replacements:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'replacements' parameter is required (list of replacement operations)",
                is_error=True,
            )

        if not isinstance(replacements, list):  # type: ignore
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'replacements' must be a list of replacement operations",
                is_error=True,
            )

        logger.info(f"EditFileTool editing file: {file_path}")

        try:
            # Resolve and validate path
            target_path = (Path(self.working_directory) / file_path).resolve()
            working_path = Path(self.working_directory).resolve()

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

            if not target_path.exists():
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: File not found: {file_path}",
                    is_error=True,
                )

            if not target_path.is_file():
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: Path is not a file: {file_path}",
                    is_error=True,
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

            # Read file
            content = target_path.read_text()
            original_lines = content.split("\n")
            total_replacements = 0

            # Track modified lines for display
            lines_added_count = 0
            lines_removed_count = 0

            # Apply replacements in sequence
            for repl_idx, replacement in enumerate(replacements, 1):
                repl_type = replacement.get("type", "string")

                try:
                    if repl_type == "string":
                        content, count = self._apply_string_replacement(content, replacement)
                        total_replacements += count
                    elif repl_type == "regex":
                        content, count = self._apply_regex_replacement(content, replacement)
                        total_replacements += count
                    elif repl_type == "line":
                        # Track line replacement counts
                        from_line = replacement.get("from_line", 1)
                        to_line = replacement.get("to_line", 1)
                        new_content = replacement.get("content", "")
                        num_old_lines = to_line - from_line + 1
                        num_new_lines = new_content.count("\n") + 1
                        lines_removed_count += num_old_lines
                        lines_added_count += num_new_lines

                        content, count = self._apply_line_replacement(content, replacement)
                        total_replacements += count
                    else:
                        return ToolResultContent(
                            tool_call_id=func_call_id,
                            type="tool_result",
                            name=self.name,
                            message=f"Error: Unknown replacement type '{repl_type}' in replacement #{repl_idx}",
                            is_error=True,
                        )
                except ValueError as e:
                    return ToolResultContent(
                        tool_call_id=func_call_id,
                        type="tool_result",
                        name=self.name,
                        message=f"Error in replacement #{repl_idx}: {str(e)}",
                        is_error=True,
                    )

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
                operation="edit_file",
                tool_name=self.name,
                current_version=current_version,
            )
            # Cleanup old history entries beyond 50
            history_manager.cleanup_history(file_path, keep_count=50)

            # Write updated content
            target_path.write_text(content)
            modified_lines = content.split("\n")

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
            output = (
                f"✅ Successfully edited 1 file with {total_replacements} total replacement(s)\n\n"
            )
            output += f"📄 {file_path}: {new_lines_added} lines added, {old_lines_removed} lines removed\n"

            # Build display content with file change information (only when show=True)
            display_content = None
            display_json: Dict[str, Any] = {
                "file": file_path,
                "new_lines_added": new_lines_added,
                "old_lines_removed": old_lines_removed,
            }
            display_content = DisplayContent(type="json_block", json_block=json.dumps(display_json))

            # Calculate file metadata
            file_size = len(content.encode("utf-8"))
            # Get file timestamps from filesystem
            file_stat = target_path.stat()
            created_at = datetime.fromtimestamp(file_stat.st_ctime, tz=timezone.utc)
            modified_at = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)

            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message=output,
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
            logger.error(f"Error editing file: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message=f"Error editing file: {str(e)}",
                is_error=True,
            )

    def get_display_content(self, **kwargs: Any) -> Optional["DisplayContent"]:
        """Generate display content showing add/remove counts for each file"""
        return None
