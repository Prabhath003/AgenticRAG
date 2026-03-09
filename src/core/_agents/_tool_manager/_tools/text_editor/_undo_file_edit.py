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
from datetime import datetime, timezone

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


class UndoFileEditTool(BaseTool):
    """Undo file edits by restoring previous versions from edit history"""

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
        return "undo_file_edit"

    @property
    def description(self) -> str:
        return (
            "Undo the last file edit by restoring the file to its previous state from edit history.\\n\\n"
            f"Working Directory: {self.working_directory}\\n"
            "Edit history is automatically tracked when using edit_file and file_line_insert tools.\\n\\n"
            "Each undo restores the file to its state before the most recent edit.\\n\\n"
            "EXACT INPUT TEMPLATE:\\n"
            '{\\"show\\": <bool>, \\"description\\": \\"<task>\\", \\"path\\": \\"<file>\\"}\\n'
        )

    @property
    def properties(self) -> Dict[str, Dict[str, str]]:
        return {
            "show": {
                "type": "boolean",
                "description": "Set to True to display the restored file with syntax highlighting to the user. Set to False to restore silently.",
            },
            "description": {
                "type": "string",
                "description": "One-sentence description of the undo operation",
            },
            "path": {
                "type": "string",
                "description": "Path to the file to undo (relative to working directory)",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["show", "description", "path"]

    def _get_history_dir(self, file_path: str) -> Path:
        """Get the history directory for a file"""
        file_path_obj = Path(file_path)
        file_name = file_path_obj.stem
        file_ext = file_path_obj.suffix
        safe_name = file_name.replace("/", "_").replace("\\", "_")
        history_dir = Path(self.working_directory) / ".edit_history" / f"{safe_name}{file_ext}"
        return history_dir

    def _list_history(self, file_path: str) -> List[Dict[str, Any]]:
        """List all edit history for a file"""
        history_dir = self._get_history_dir(file_path)
        if not history_dir.exists():
            return []

        history_entries: List[Dict[str, Any]] = []
        for entry_file in sorted(history_dir.glob("*.json"), reverse=True):
            try:
                with open(entry_file) as f:
                    metadata: Dict[str, Any] = json.load(f)
                    history_entries.append(metadata)
            except Exception as e:
                logger.error(f"Error reading history entry {entry_file}: {e}")

        return history_entries

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute undo operation - always undoes the last 1 edit"""
        _ = kwargs.get("description")
        show: bool = kwargs.get("show", True)
        file_path: Optional[str] = kwargs.get("path")
        steps: int = 1  # Always undo 1 step

        if not file_path:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'path' parameter is required",
                is_error=True,
            )

        logger.info(f"UndoFileEditTool undoing {steps} step(s) for: {file_path}")

        try:
            # Resolve file path
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

            # Check if file is editable text (supported content type)
            file_ext = Path(file_path).suffix.lower()
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
                    message=f"Error: Cannot undo edits for file '{file_path}' - unsupported content type '{content_type}'. This tool only supports editable text files. For binary files (PDF, images, archives, etc.), use the bash_tool with Python to read, modify, and write the file content.",
                    is_error=True,
                )

            # Get history
            history = self._list_history(file_path)
            if not history:
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"No edit history found for: {file_path}",
                    is_error=True,
                )

            if steps > len(history):
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Cannot undo {steps} steps. Only {len(history)} edit(s) in history.",
                    is_error=True,
                )

            # Sort history by version_before_edit (descending, most recent first)
            history_dir = self._get_history_dir(file_path)
            sorted_history = sorted(
                history, key=lambda x: x.get("version_before_edit", 1), reverse=True
            )

            # Find current position in history by matching file content
            current_content = target_path.read_text()
            current_history_index = -1  # Default: latest version (before any snapshot)

            for idx, entry in enumerate(sorted_history):
                snapshot_file = history_dir / f"{entry['timestamp']}_snapshot"
                if snapshot_file.exists():
                    try:
                        snap_content = snapshot_file.read_text()
                        if snap_content == current_content:
                            current_history_index = idx
                            break
                    except Exception:
                        pass

            # Calculate target index (go back steps from current position)
            target_history_index = current_history_index + steps

            if target_history_index < 0 or target_history_index >= len(sorted_history):
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Cannot undo {steps} steps from current position.",
                    is_error=True,
                )

            target_entry = sorted_history[target_history_index]
            snapshot_file = history_dir / f"{target_entry['timestamp']}_snapshot"

            if not snapshot_file.exists():
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"Error: Snapshot file not found for undo operation",
                    is_error=True,
                )

            # Read and restore the snapshot
            original_content = snapshot_file.read_text()
            _ = original_content.split("\n")

            # Store current version for redo capability (optional)
            if target_path.exists():
                _ = target_path.read_text()
            else:
                _ = ""

            # Restore the file
            target_path.write_text(original_content)
            _ = original_content.split("\n")

            # Build output message
            output = f"✅ Successfully undone {steps} edit(s) for file: {file_path}\\n"
            output += f"Restored to state from {target_entry['timestamp']}\\n"
            output += f"Operation: {target_entry['operation']}\\n"
            if target_entry.get("tool"):
                output += f"Tool: {target_entry['tool']}"

            # Determine the version being restored to
            restored_version = target_entry.get("version_before_edit", 1)

            # Build display content
            display_json: Dict[str, Any] = {
                "file": file_path,
                "restored_timestamp": target_entry["timestamp"],
                "restored_version": restored_version,
            }
            display_content = DisplayContent(type="json_block", json_block=json.dumps(display_json))

            # Calculate file metadata
            file_size = len(original_content.encode("utf-8"))
            # Get file timestamps from filesystem
            file_stat = target_path.stat()
            created_at = datetime.fromtimestamp(file_stat.st_ctime, tz=timezone.utc)
            modified_at = datetime.now(timezone.utc)

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
                            version=restored_version,
                        )
                    ]
                    if show
                    else None
                ),
            )

        except Exception as e:
            logger.error(f"Error undoing file edit: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message=f"Error undoing file edit: {str(e)}",
                is_error=True,
            )

    def get_display_content(self, **kwargs: Any) -> Optional["DisplayContent"]:
        """Generate display content for undo operation"""
        return None
