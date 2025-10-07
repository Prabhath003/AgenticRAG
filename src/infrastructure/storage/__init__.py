# -----------------------------------------------------------------------------
# Copyright (c) 2025 Edureka Backend
# All rights reserved.
#
# Developed by: GiKA AI Team
# Author: Prabhath Chellingi
# GitHub: https://github.com/Prabhath003
# Contact: prabhath@gikagraph.ai
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
