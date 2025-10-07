"""Infrastructure modules for external services"""

from .file_processor.client import chunk_file, parse_file

__all__ = [
    'parse_file',
    'chunk_file'
]
