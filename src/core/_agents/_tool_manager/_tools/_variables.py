"""
Legacy variables module - re-exports from _file_utils for backward compatibility.

This module is deprecated. Please import directly from:
  from src.infrastructure.utils._file_utils import EXTENSION_TO_LANGUAGE, MAX_FILE_SIZE, MAX_DIR_ITEMS_PER_LEVEL, get_content_type
"""

from .....infrastructure.utils import (
    EXTENSION_TO_LANGUAGE,
    CONTENT_TYPE_MAP,
    MAX_FILE_SIZE,
    MAX_DIR_ITEMS_PER_LEVEL,
    get_content_type,
)

__all__ = [
    "EXTENSION_TO_LANGUAGE",
    "CONTENT_TYPE_MAP",
    "MAX_FILE_SIZE",
    "MAX_DIR_ITEMS_PER_LEVEL",
    "get_content_type",
]
