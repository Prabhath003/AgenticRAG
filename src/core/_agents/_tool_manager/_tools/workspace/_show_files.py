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

from datetime import datetime, timezone
from typing import Any, List, Dict, Optional
from pathlib import Path

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


class ShowFilesTool(BaseTool):
    """Display multiple file contents to the user"""

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
        return "show_files"

    @property
    def description(self) -> str:
        return (
            "Display multiple file contents to the user.\n\n"
            "This tool reads and displays multiple files created via bash_tool or other file creation tools.\n"
            "Use this to show multiple file contents to the user for review or verification.\n\n"
            f"Working Directory: {self.working_directory}\n"
            f"User-facing outputs: {{working_dir}}/user-data/outputs/\n"
            f"Helper files: anywhere in {{working_dir}}\n"
            "**It is very important to follow the above format**\n\n"
            "Key features:\n"
            "• Displays contents of multiple text files to user\n"
            "• Supports all programming languages and markup formats\n"
            "• Perfect for reviewing multiple generated or modified files\n"
            "• Returns structured output with language detection for each file\n"
        )

    @property
    def properties(self) -> Dict[str, Dict[str, Any]]:
        return {
            "description": {
                "type": "string",
                "description": "One-sentence description of why showing these files",
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of file paths to display (relative to working directory)",
            },
        }

    @property
    def required(self) -> List[str]:
        return ["description", "paths"]

    def execute(self, func_call_id: str, **kwargs: Any) -> ToolResultContent:
        """Execute multiple file display with given parameters"""
        _ = kwargs.get("description")
        file_paths: Optional[List[str]] = kwargs.get("paths")

        if not file_paths:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'paths' parameter is required and must be a non-empty list",
                is_error=True,
            )

        logger.info(f"ShowFilesTool displaying: {file_paths}")
        logger.info(f"👁️  Showing files: {file_paths}")

        files_metadata: List[ConversationFileMetadata] = []
        errors: List[str] = []

        try:
            working_path = Path(self.working_directory).resolve()

            for file_path in file_paths:
                try:
                    # Resolve the file path
                    target_path = (Path(self.working_directory) / file_path).resolve()

                    # Validate path is within working directory
                    try:
                        target_path.relative_to(working_path)
                    except ValueError:
                        errors.append(
                            f"Access denied. Path is outside working directory: {file_path}"
                        )
                        continue

                    # Check if path exists
                    if not target_path.exists():
                        errors.append(f"File not found: {file_path}")
                        continue

                    # Check if it's a file (not a directory)
                    if not target_path.is_file():
                        errors.append(f"Path is not a file: {file_path}")
                        continue

                    # Get file metadata
                    file_stat = target_path.stat()
                    file_size = file_stat.st_size
                    file_ext = Path(file_path).suffix.lower()

                    content_type = get_content_type(Path(file_path).name)

                    # Get file timestamps
                    created_at = datetime.fromtimestamp(file_stat.st_ctime, tz=timezone.utc)
                    modified_at = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)

                    # Determine version based on edit history count
                    history_dir = (
                        Path(self.working_directory)
                        / ".edit_history"
                        / f"{Path(file_path).stem}{Path(file_path).suffix}"
                    )
                    version = 1
                    if history_dir.exists():
                        history_entries = list(history_dir.glob("*.json"))
                        version = len(history_entries) + 1

                    files_metadata.append(
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
                            version=version,
                        )
                    )

                except Exception as e:
                    logger.error(f"Error processing file {file_path}: {e}", exc_info=True)
                    errors.append(f"Error processing file {file_path}: {str(e)}")

            # Return error if no files were successfully processed
            if not files_metadata:
                error_message = (
                    "Error: No files could be processed. " + " | ".join(errors)
                    if errors
                    else "Error: No valid files provided."
                )
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=error_message,
                    is_error=True,
                )

            # Build success message
            success_paths = [fm.path for fm in files_metadata]
            message_text = f"Displaying {len(files_metadata)} file(s): {', '.join(success_paths)}"
            if errors:
                message_text += (
                    f"\n\nPartial errors ({len(errors)} files could not be processed):\n"
                    + "\n".join(errors)
                )

            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                content=[{"type": "text", "text": message_text, "uuid": func_call_id}],
                is_error=False,
                files=files_metadata,
            )

        except Exception as e:
            logger.error(f"Error showing files: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message=f"Error showing files: {str(e)}",
                is_error=True,
            )

    def get_display_content(self, **kwargs: Any) -> Optional[DisplayContent]:
        """Generate display content showing the files being displayed"""
        return None
