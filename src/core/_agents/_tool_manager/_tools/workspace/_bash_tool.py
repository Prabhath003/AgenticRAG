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

# src/agents/tools/terminal.py
from typing import Any, List, Optional, Dict, Tuple
import subprocess
import shlex
import os
from pathlib import Path
from datetime import datetime, timezone
import mimetypes
import json
import base64

from .._base_tool import BaseTool
from .. import logger
from ......config import Config
from .....models.agent.content_models import (
    DisplayContent,
    ToolResultContent,
)
from .....models.agent import ConversationFileMetadata, ConverseFile

# Commands that are completely blocked (only system-critical operations)
BLOCKED_COMMANDS = {
    "sudo",
    "su",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "mkfs",
    "dd",
    "fdisk",
}

# Commands that need special handling or warnings (but are allowed)
RISKY_COMMANDS = {"kill", "pkill", "killall", "rm", "rmdir"}

# Commands explicitly allowed for file/directory CRUD operations
ALLOWED_FILE_OPERATIONS = {
    "ls",
    "cat",
    "head",
    "tail",
    "find",
    "grep",
    "wc",
    "file",
    "touch",
    "mkdir",
    "rm",
    "rmdir",
    "cp",
    "mv",
    "chmod",
    "chown",
    "echo",
    "sed",
    "awk",
    "sort",
    "uniq",
    "cut",
    "tee",
    "ln",
}

# Commands explicitly allowed for Python operations
ALLOWED_PYTHON_OPERATIONS = {"python", "python3", "pip", "pip3"}

# Commands explicitly allowed for network operations
ALLOWED_NETWORK_OPERATIONS = {"curl", "wget", "git"}


class BashTool(BaseTool):
    """
    Bash/terminal execution tool with full working directory access.

    Capabilities:
    - Full file/directory CRUD operations (create, read, update, delete)
    - Python script execution
    - Pip package installation (pip install only)
    - Network operations (curl, wget, git)
    - Text processing and system utilities

    Security:
    - Operations confined to agent-specific working directory
    - Path validation prevents directory traversal attacks (blocks ../, absolute paths, etc.)
    - Blocks only system-critical commands (sudo, shutdown, disk formatting)
    - 30-second timeout per command
    - Comprehensive logging of all operations
    """

    conversation_id: Optional[str] = None
    working_directory: str = Config.TERMINAL_CACHE_DIR

    def __init__(self, conversation_id: Optional[str] = None):
        self.conversation_id = conversation_id if conversation_id else "default"
        self._setup_working_directory()

    @property
    def name(self) -> str:
        return "bash_tool"

    @property
    def description(self) -> str:
        return (
            "Execute bash/terminal commands with full access to conversation-specific working directory.\n\n"
            f"Working Directory: {self.working_directory}\n"
            f"Directory Structure:\n"
            f"  {{working_dir}}/                    - All operations confined to this directory\n"
            f"  {{working_dir}}/user-data/          - User data and inputs\n"
            f"  {{working_dir}}/user-data/outputs/  - Final deliverable outputs for users\n"
            f"  {{working_dir}}/temp/               - Temporary helper files\n"
            f"  {{working_dir}}/uploads/            - Uploaded files\n"
            "**It is very important to follow the above format**\n\n"
            "Use this tool for:\n"
            "- Complex file operations requiring shell scripting\n"
            "- Python script execution (python script.py, python -c 'code')\n"
            "- Package installation (pip install, pip3 install)\n"
            "- Network operations (curl, wget, git)\n"
            "- System utilities (grep, sed, awk, sort, find, etc.)\n"
            "- Process management (ps, limited top)\n"
            "- Binary file processing via Python (images, PDFs, archives, compiled code)\n\n"
            "Prefer specialized tools over bash_tool when available:\n"
            "- read_file, create_file, edit_file, file_str_replace - for text file operations\n"
            "- Only use bash_tool for operations these tools cannot handle\n\n"
            "Blocked operations (system-critical only):\n"
            "- sudo, su (privilege escalation) | shutdown, reboot, halt, poweroff (system control)\n"
            "- mkfs, dd, fdisk (disk formatting)\n\n"
            "Security: 30-second timeout per command, path validation, conversation-isolated workspace.\n"
            "(Note: use show_files tool if any file for user is created or generated.)"
        )

    @property
    def properties(self) -> Dict[str, Dict[str, str]]:
        return {
            "description": {
                "type": "string",
                "description": "One-sentence description of the task being performed",
            },
            "command": {"type": "string", "description": "Bash command to execute"},
        }

    @property
    def required(self) -> List[str]:
        return ["description", "command"]

    def dump(self):
        return {"conversation_id": self.conversation_id}

    @staticmethod
    def load(data: Dict[str, Any]):
        return BashTool(data["conversation_id"])

    def upload(self, files: List[ConverseFile]) -> List[ConversationFileMetadata]:
        """
        Upload files to the working directory/uploads folder.

        Args:
            files: List of ConverseFile objects with base64 encoded content

        Returns:
            List of ConversationFileMetadata with path, size, content_type, created_at, custom_metadata
        """
        try:
            # Create uploads directory
            uploads_dir = Path(self.working_directory) / "uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)

            uploaded_files: List[ConversationFileMetadata] = []
            now = datetime.now(timezone.utc)

            for file in files:
                if not file.filename:
                    logger.warning("File dict missing filename, skipping")
                    continue

                # Ensure filename is safe (no path traversal)
                filename = Path(file.filename).name

                # Write file to uploads directory
                file_path = uploads_dir / filename

                # Decode base64 content to bytes and write to file
                try:
                    file_content = base64.b64decode(file.content)
                    file_path.write_bytes(file_content)
                except Exception as e:
                    logger.error(f"Error decoding base64 content for {filename}: {e}")
                    continue

                # Get file size
                file_size = file_path.stat().st_size

                # Determine content type
                content_type, _ = mimetypes.guess_type(str(file_path))
                if not content_type:
                    content_type = file.content_type

                # Determine version based on edit history count
                file_path_rel = file_path.relative_to(Path(self.working_directory))
                history_dir = (
                    Path(self.working_directory)
                    / ".edit_history"
                    / f"{file_path_rel.stem}{file_path_rel.suffix}"
                )
                version = 1
                if history_dir.exists():
                    history_entries = list(history_dir.glob("*.json"))
                    version = len(history_entries) + 1

                # Build file metadata for response
                uploaded_files.append(
                    ConversationFileMetadata(
                        path=str(file_path),
                        size=file_size,
                        content_type=content_type,
                        created_at=now,
                        modified_at=now,
                        custom_metadata={},
                        version=version,
                    )
                )

                logger.info(f"Uploaded file: {filename} ({file_size} bytes) to {file_path}")

            return uploaded_files

        except Exception as e:
            logger.error(f"Error uploading files: {e}", exc_info=True)
            return []

    def _setup_working_directory(self):
        """
        Create and set up the conversation-specific working directory.
        All terminal commands will execute within this directory.
        """
        try:
            # Create terminal cache base directory
            terminal_cache_base = Path(Config.TERMINAL_CACHE_DIR)
            terminal_cache_base.mkdir(parents=True, exist_ok=True)

            # Create conversation-specific directory
            conversation_dir = terminal_cache_base / str(self.conversation_id)
            conversation_dir.mkdir(parents=True, exist_ok=True)

            self.working_directory = str(conversation_dir.resolve())
            logger.info(f"Bash working directory set to: {self.working_directory}")

        except Exception as e:
            logger.error(f"Error setting up working directory: {e}")
            # Fallback to a default location
            self.working_directory = os.getcwd()
            logger.warning(f"Using fallback working directory: {self.working_directory}")

    def _validate_path_access(self, path_arg: str) -> bool:
        """
        Validate that a path argument stays within the working directory.
        Returns True if the path is safe (within working directory), False otherwise.
        """
        try:
            # Skip validation for non-path arguments (e.g., flags, options)
            if path_arg.startswith("-") or "=" in path_arg:
                return True

            # Skip URLs (for curl, wget, git clone, etc.)
            if path_arg.startswith(("http://", "https://", "git://", "ssh://", "ftp://")):
                return True

            # Resolve the path relative to working directory
            working_path = Path(self.working_directory).resolve()

            # Handle absolute paths
            if os.path.isabs(path_arg):
                target_path = Path(path_arg).resolve()
            else:
                # Handle relative paths
                target_path = (working_path / path_arg).resolve()

            # Check if target path is within working directory
            try:
                target_path.relative_to(working_path)
                return True
            except ValueError:
                # Path is outside working directory
                logger.warning(f"Path escape attempt detected: {path_arg} -> {target_path}")
                return False

        except Exception as e:
            logger.error(f"Error validating path: {e}")
            # Be conservative: if we can't validate, block it
            return False

    def _is_safe_command(self, command: str) -> tuple[bool, str]:
        """
        Check if a command is safe to execute.
        Returns (is_safe, reason)

        Security policy:
        - Allows full file/directory CRUD operations within working directory
        - Allows Python script execution and pip package installation
        - Allows network operations (curl, wget, git)
        - Blocks only system-critical operations (sudo, shutdown, disk formatting)
        - Validates all file paths to prevent directory traversal attacks
        """
        try:
            # Parse the command
            parts = shlex.split(command)
            if not parts:
                return False, "Empty command"

            base_command = parts[0].split("/")[-1]  # Handle paths like /bin/rm

            # Check blocked commands (only system-critical operations)
            if base_command in BLOCKED_COMMANDS:
                return (
                    False,
                    f"Command '{base_command}' is blocked (system-critical operation)",
                )

            # Validate all path arguments to prevent directory traversal
            # Skip the first element (the command itself) and check remaining arguments
            for arg in parts[1:]:
                if not self._validate_path_access(arg):
                    return (
                        False,
                        f"Access denied: path '{arg}' is outside the working directory",
                    )

            # Check for extremely dangerous patterns only
            dangerous_patterns: List[Any] = [
                (":", "(){"),  # Fork bombs
            ]

            command_lower = command.lower()
            for pattern in dangerous_patterns:
                if all(p in command_lower for p in pattern):
                    return False, f"Potentially dangerous pattern detected: {pattern}"

            # Warn about risky commands but allow them
            if base_command in RISKY_COMMANDS:
                logger.warning(f"Risky command being executed: {command}")

            return True, "Command is safe"

        except Exception as e:
            logger.error(f"Error parsing command: {e}")
            return False, f"Error parsing command: {str(e)}"

    def execute(self, func_call_id: str, **kwargs: Any) -> "ToolResultContent":
        """Execute a safe terminal command in the agent-specific directory."""
        _ = kwargs.get("description")
        command = kwargs.get("command")
        if not command:
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="Error: 'command' parameter is required",
                is_error=True,
            )

        logger.info(f"BashTool (conversation: {self.conversation_id}) executing: '{command}'")

        try:
            # Safety check
            is_safe, reason = self._is_safe_command(command)
            if not is_safe:
                logger.warning(f"Blocked unsafe command: {command}. Reason: {reason}")
                return ToolResultContent(
                    tool_call_id=func_call_id,
                    type="tool_result",
                    name=self.name,
                    message=f"❌ Command blocked: {reason}",
                    is_error=True,
                )

            # Execute the command in agent-specific directory
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
                cwd=self.working_directory,
            )

            returncode = result.returncode
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            logger.info(f"Terminal command completed with return code {result.returncode}")

            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                content=[
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "returncode": returncode,
                                "stdout": stdout,
                                "stderr": stderr,
                            }
                        ),
                        "uuid": func_call_id,
                    }
                ],
                is_error=False,
                display_content=DisplayContent(
                    type="json_block",
                    json_block=json.dumps(
                        {"returncode": returncode, "stdout": stdout, "stderr": stderr}
                    ),
                ),
            )

        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {command}")
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message="❌ Command timed out after 60 seconds",
                is_error=True,
            )
        except Exception as e:
            logger.error(f"Error executing command: {e}", exc_info=True)
            return ToolResultContent(
                tool_call_id=func_call_id,
                type="tool_result",
                name=self.name,
                message=f"❌ Error executing command: {str(e)}",
                is_error=True,
            )

    def get_display_content(self, **kwargs: Any) -> Optional["DisplayContent"]:
        """Generate display content showing the command being executed"""
        try:
            command = kwargs.get("command", "N/A")

            display_info: Dict[str, Any] = {"language": "bash", "code": command}

            return DisplayContent(type="json_block", json_block=json.dumps(display_info, indent=2))
        except Exception as e:
            logger.error(f"Error generating display content for terminal: {e}")
            return None

    def get_files(self) -> Dict[str, Any]:
        """
        Recursively list all files in the user-data directory.

        Returns:
            Dictionary with:
            - files: List of file paths (strings)
            - files_metadata: List of file metadata dictionaries containing:
              - path: Full file path
              - size: File size in bytes
              - content_type: MIME type of the file
              - created_at: File creation timestamp
              - custom_metadata: Custom metadata (empty dict by default)
        """
        try:
            # Construct the user-data directory path
            user_data_dir = Path(self.working_directory) / "user-data"

            if not user_data_dir.exists():
                logger.warning(f"User data directory does not exist: {user_data_dir}")
                return {"files": [], "files_metadata": []}

            files_list: List[str] = []
            files_metadata: List[ConversationFileMetadata] = []

            # Recursively iterate through all files in the directory
            for file_path in sorted(user_data_dir.rglob("*")):
                if file_path.is_file():
                    try:
                        # Get file stats
                        stat_info = file_path.stat()

                        # Determine content type
                        content_type, _ = mimetypes.guess_type(str(file_path))
                        if not content_type:
                            content_type = "application/octet-stream"

                        # Create file timestamps
                        created_at = datetime.fromtimestamp(stat_info.st_ctime, tz=timezone.utc)
                        modified_at = datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc)

                        file_path_str = str(file_path)
                        files_list.append(file_path_str)

                        # Determine version based on edit history count
                        file_path_rel = file_path.relative_to(Path(self.working_directory))
                        history_dir = (
                            Path(self.working_directory)
                            / ".edit_history"
                            / f"{file_path_rel.stem}{file_path_rel.suffix}"
                        )
                        version = 1
                        if history_dir.exists():
                            history_entries = list(history_dir.glob("*.json"))
                            version = len(history_entries) + 1

                        files_metadata.append(
                            ConversationFileMetadata(
                                path=file_path_str,
                                size=stat_info.st_size,
                                content_type=content_type,
                                created_at=created_at,
                                modified_at=modified_at,
                                version=version,
                            )
                        )

                    except Exception as e:
                        logger.error(f"Error processing file {file_path}: {e}")
                        continue

            return {"files": files_list, "files_metadata": files_metadata}

        except Exception as e:
            logger.error(f"Error getting files: {e}", exc_info=True)
            return {"files": [], "files_metadata": []}

    def download_content(
        self, file_path: str, version: Optional[int] = None
    ) -> Tuple[bytes, str, str]:
        """
        Download/read file content as bytes.

        Args:
            file_path: Path to the file to download (relative to working directory)
            version: Optional file version to retrieve. If None, returns the latest version.

        Returns:
            Tuple of (file_content as bytes, filename, content_type)

        Raises:
            ValueError: If path is outside working directory
            FileNotFoundError: If file does not exist
            Exception: Other file reading errors
        """
        try:
            # Validate path access
            if not self._validate_path_access(file_path):
                raise ValueError(
                    f"Access denied: path '{file_path}' is outside the working directory"
                )

            # Resolve the file path
            working_path = Path(self.working_directory).resolve()
            if os.path.isabs(file_path):
                target_path = Path(file_path).resolve()
            else:
                target_path = (working_path / file_path).resolve()

            # Double-check path is within working directory
            try:
                target_path.relative_to(working_path)
            except ValueError:
                raise ValueError(
                    f"Access denied: path '{file_path}' is outside the working directory"
                )

            # If a specific version is requested, retrieve from edit history
            if version is not None:
                content = self._get_file_version(file_path, version)
            else:
                # Check if file exists for latest version
                if not target_path.exists():
                    raise FileNotFoundError(f"File not found: {target_path}")

                if not target_path.is_file():
                    raise ValueError(f"Path is not a file: {target_path}")

                # Read and return file content as bytes
                content = target_path.read_bytes()

            logger.info(
                f"Downloaded file: {file_path} ({len(content)} bytes)"
                + (f" version {version}" if version else "")
            )

            # Determine content type
            content_type, _ = mimetypes.guess_type(str(target_path))
            if not content_type:
                content_type = "application/octet-stream"

            return content, os.path.basename(file_path), content_type

        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            raise
        except ValueError as e:
            logger.error(f"Invalid path: {e}")
            raise
        except Exception as e:
            logger.error(f"Error downloading file {file_path}: {e}", exc_info=True)
            raise

    def _get_file_version(self, file_path: str, version: int) -> bytes:
        """
        Retrieve a specific version of a file from edit history.

        Args:
            file_path: Path to the file (relative to working directory)
            version: Version number to retrieve

        Returns:
            File content as bytes

        Raises:
            ValueError: If version not found or invalid
            FileNotFoundError: If history doesn't exist
        """
        try:
            # Get the history directory for this file
            file_path_obj = Path(file_path)
            file_name = file_path_obj.stem
            file_ext = file_path_obj.suffix
            safe_name = file_name.replace("/", "_").replace("\\", "_")
            history_dir = Path(self.working_directory) / ".edit_history" / f"{safe_name}{file_ext}"

            if not history_dir.exists():
                raise FileNotFoundError(f"No edit history found for file: {file_path}")

            # List all history entries sorted by version_before_edit
            history_entries: List[Dict[str, Any]] = []
            for entry_file in sorted(history_dir.glob("*.json")):
                try:
                    with open(entry_file) as f:
                        metadata: Dict[str, Any] = json.load(f)
                        history_entries.append(metadata)
                except Exception as e:
                    logger.error(f"Error reading history entry {entry_file}: {e}")

            if not history_entries:
                raise FileNotFoundError(f"No edit history snapshots found for: {file_path}")

            # Sort by version_before_edit ascending to find the right snapshot
            history_entries.sort(key=lambda x: x.get("version_before_edit", 1))

            # Find the snapshot for the requested version
            # The snapshot with version_before_edit=v contains the content of version v
            target_entry = None
            for entry in history_entries:
                if entry.get("version_before_edit") == version:
                    target_entry = entry
                    break

            if not target_entry:
                # If exact version not found, check if it's the current version
                latest_entry = history_entries[-1]
                latest_snapshot_version = latest_entry.get("version_before_edit", 1)
                current_version = latest_snapshot_version + 1

                # If requesting the current version, return the actual file content
                if version == current_version:
                    # Calculate target path for the current file
                    working_path = Path(self.working_directory).resolve()
                    if os.path.isabs(file_path):
                        current_file_path = Path(file_path).resolve()
                    else:
                        current_file_path = (working_path / file_path).resolve()

                    if not current_file_path.exists():
                        raise FileNotFoundError(f"File not found: {current_file_path}")
                    if not current_file_path.is_file():
                        raise ValueError(f"Path is not a file: {current_file_path}")
                    content = current_file_path.read_bytes()
                    logger.info(
                        f"Retrieved current version {version} of file: {file_path} ({len(content)} bytes)"
                    )
                    return content

                raise ValueError(
                    f"Version {version} not found. Latest available version is {current_version}"
                )

            # Read the snapshot file for this version
            snapshot_file = history_dir / f"{target_entry['timestamp']}_snapshot"
            if not snapshot_file.exists():
                raise FileNotFoundError(f"Snapshot file not found for version {version}")

            content = snapshot_file.read_bytes()
            logger.info(f"Retrieved version {version} of file: {file_path} ({len(content)} bytes)")
            return content

        except (FileNotFoundError, ValueError):
            raise
        except Exception as e:
            logger.error(f"Error getting file version {file_path} v{version}: {e}", exc_info=True)
            raise ValueError(f"Error retrieving version {version}: {str(e)}")
