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

"""Storage module for JSON-based data persistence"""

from .json_storage import (
    JSONStorage,
    JSONStorageSession,
    JSONCollection,
    get_storage,
    get_storage_session
)

__all__ = [
    'JSONStorage',
    'JSONStorageSession',
    'JSONCollection',
    'get_storage',
    'get_storage_session'
]
