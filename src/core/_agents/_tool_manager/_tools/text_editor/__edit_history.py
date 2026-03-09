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

from pathlib import Path
import json
from datetime import datetime, timezone
from typing import Dict, Any

from .. import logger


class EditHistoryManager:
    """Manages file edit history for undo functionality"""

    def __init__(self, working_directory: str):
        self.working_directory = Path(working_directory)
        self.history_base = self.working_directory / ".edit_history"

    def _get_history_dir(self, file_path: str) -> Path:
        """Get the history directory for a file"""
        file_path_obj = Path(file_path)
        file_name = file_path_obj.stem
        file_ext = file_path_obj.suffix
        safe_name = file_name.replace("/", "_").replace("\\", "_")
        history_dir = self.history_base / f"{safe_name}{file_ext}"
        return history_dir

    def save_snapshot(
        self,
        file_path: str,
        content: str,
        operation: str,
        tool_name: str = "unknown",
        current_version: int = 1,
    ) -> bool:
        """Save a snapshot of file content before editing

        Args:
            file_path: Path to the file (relative to working directory)
            content: The file content to snapshot
            operation: Description of the operation (e.g., 'edit', 'line_insert')
            tool_name: Name of the tool performing the operation
            current_version: Current version number before the edit

        Returns:
            True if snapshot was saved successfully, False otherwise
        """
        try:
            history_dir = self._get_history_dir(file_path)
            history_dir.mkdir(parents=True, exist_ok=True)

            # Generate timestamp-based filename
            timestamp = datetime.now(timezone.utc).isoformat()
            snapshot_file = history_dir / f"{timestamp}_snapshot"
            metadata_file = history_dir / f"{timestamp}.json"

            # Save snapshot content
            snapshot_file.write_text(content)

            # Save metadata with version information
            metadata: Dict[str, Any] = {
                "timestamp": timestamp,
                "operation": operation,
                "tool": tool_name,
                "file_path": file_path,
                "content_size": len(content),
                "lines": len(content.split("\n")),
                "version_before_edit": current_version,
            }
            metadata_file.write_text(json.dumps(metadata, indent=2))

            logger.info(
                f"Saved edit history snapshot for {file_path}: {timestamp} (version {current_version})"
            )
            return True

        except Exception as e:
            logger.error(f"Error saving edit history snapshot: {e}", exc_info=True)
            return False

    def cleanup_history(self, file_path: str, keep_count: int = 50) -> bool:
        """Clean up old history entries, keeping only the most recent ones

        Args:
            file_path: Path to the file (relative to working directory)
            keep_count: Number of recent snapshots to keep

        Returns:
            True if cleanup was successful
        """
        try:
            history_dir = self._get_history_dir(file_path)
            if not history_dir.exists():
                return True

            # Get all metadata files sorted by modification time
            metadata_files = sorted(
                history_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            # Remove old entries beyond keep_count
            for metadata_file in metadata_files[keep_count:]:
                try:
                    timestamp = metadata_file.stem
                    snapshot_file = history_dir / f"{timestamp}_snapshot"

                    metadata_file.unlink()
                    if snapshot_file.exists():
                        snapshot_file.unlink()

                    logger.debug(f"Cleaned up old history entry: {timestamp}")
                except Exception as e:
                    logger.warning(f"Error cleaning up history entry: {e}")

            return True

        except Exception as e:
            logger.error(f"Error cleaning up edit history: {e}", exc_info=True)
            return False
