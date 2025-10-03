# -----------------------------------------------------------------------------
# Copyright (c) 2025 Backend
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

# src/config/settings.py
import os
import json
from dotenv import load_dotenv

load_dotenv()

from ..log_creator import get_file_logger

logger = get_file_logger()

class Config:
    # MongoDB Configuration
    USE_MONGO = False
    MONGODB_URL = os.getenv("MONGODB_URL")
    DATABASE_NAME = "gmail-connector"
    
    # RAG Configuration
    EMBEDDINGS_MODEL = "all-MiniLM-L6-v2"
    
    # GPT Configuration
    GPT_MODEL = "gpt-4.1-mini"
    TEMPERATURE = 0.3
    
    # Backend
    BACKEND_PORT = 8001
    DATA_DIR = "data/"
                
    # Email Configuration
    IMAP_SERVER = 'imap.gmail.com' 

    # Model inference server
    MODEL_SERVER_URI = "http://localhost:1121/infer"

    @classmethod
    def load_json_config(cls):
        config_path = os.getenv("CONFIG_PATH")
        if config_path and os.path.isfile(config_path):
            try:
                with open(config_path, "r") as f:
                    config_data = json.load(f)
                    for key, value in config_data.items():
                        setattr(cls, key, value)
            except Exception as e:
                logger.error(f"Failed to load config from {config_path}: {e}")
                
Config.load_json_config()