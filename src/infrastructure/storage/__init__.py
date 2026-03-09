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

"""Storage module for JSON-based data persistence and vector indexing"""

from ._s3_service import S3Service, get_s3_service
from ._chromadb_store import ChromaDBStore, get_chromadb_store

__all__ = [
    "S3Service",
    "get_s3_service",
    "ChromaDBStore",
    "get_chromadb_store",
]
