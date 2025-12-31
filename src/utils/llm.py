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

# src/utils/llm.py
import requests
from typing import Dict, Any

from ..config.settings import Config
from ..log_creator import get_file_logger

logger = get_file_logger()

def inferModel(payload: Dict[str, Any]) -> Any:
    try:
        response = requests.post(Config.MODEL_SERVER_URI, json=payload)
        response.raise_for_status()
        response_data = response.json()
        
        return response_data["response"]
    except Exception as e:
        logger.warning(f"Issue with model-server: {e}")