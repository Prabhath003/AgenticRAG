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

"""
Logging utility for the Gmail Connector application.

Provides modular loggers that write detailed logs into rotating files,
automatically named based on the calling file, its directory, or a user-specified name.

Log files are saved in the `logs/` directory and rotate after reaching 100 MB (up to 5 backups).

Each log entry includes:
- Timestamp
- Log level (DEBUG, INFO, etc.)
- Logger name
- Filename and line number
- Function name
- Log message
"""
import os
import logging
from logging.handlers import RotatingFileHandler
import inspect

# Global flag to track if we've configured the root logger
_ROOT_LOGGER_CONFIGURED = False

def _configure_root_logger():
    """Configure root logger to prevent library interference"""
    global _ROOT_LOGGER_CONFIGURED

    if _ROOT_LOGGER_CONFIGURED:
        return

    # Get root logger
    root_logger = logging.getLogger()

    # Remove any existing console handlers that libraries might have added
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, RotatingFileHandler):
            root_logger.removeHandler(handler)

    # Set root logger level to WARNING to reduce noise from libraries
    root_logger.setLevel(logging.WARNING)

    _ROOT_LOGGER_CONFIGURED = True

def _create_logger(module_name: str) -> logging.Logger:
    """
    Internal helper to create and configure a logger.

    Args:
        module_name (str): Name to assign to the logger (used for naming the log file).

    Returns:
        logging.Logger: Configured logger instance.
    """
    # Configure root logger first
    _configure_root_logger()

    logger = logging.getLogger(module_name)

    # Prevent propagation to root logger to avoid console output
    logger.propagate = False

    logger.setLevel(logging.DEBUG)

    # Ensure the logs/ directory exists
    os.makedirs("logs", exist_ok=True)
    log_file = f"logs/{module_name}.log"

    # Check if logger already has the correct file handler
    existing_file_handler = None
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename.endswith(f"{module_name}.log"):
            existing_file_handler = handler
            break

    if not existing_file_handler:
        # Set up rotating file handler
        file_handler = RotatingFileHandler(
            log_file, maxBytes=100 * 1024 * 1024, backupCount=5
        )

        # Set a detailed formatter
        formatter = logging.Formatter(
            fmt='%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(funcName)s() | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)

        # Clear any existing handlers and add our file handler
        logger.handlers.clear()
        logger.addHandler(file_handler)

        logger.debug(f"Logger initialized for {module_name}")
    return logger


def get_dir_logger() -> logging.Logger:
    """
    Creates a logger based on the directory name of the calling file.

    Useful for organizing logs by package or feature directory.

    Returns:
        logging.Logger: Logger named after the caller's directory.
    """
    caller_path = inspect.stack()[1].filename
    dir_name = os.path.basename(os.path.dirname(caller_path))
    return _create_logger(dir_name)


def get_file_logger() -> logging.Logger:
    """
    Creates a logger based on the filename of the calling file.

    Useful when each module should have its own log file.

    Returns:
        logging.Logger: Logger named after the caller's filename (without extension).
    """
    caller_path = inspect.stack()[1].filename
    file_name = os.path.splitext(os.path.basename(caller_path))[0]
    return _create_logger(file_name)


def get_logger_by_name(name: str) -> logging.Logger:
    """
    Creates a logger based on a custom name.

    Useful when you want full control over the logger naming.

    Args:
        name (str): Custom name for the logger and log file.

    Returns:
        logging.Logger: Logger with the given custom name.
    """
    return _create_logger(name)

def suppress_library_loggers():
    """
    Suppress verbose logging from common AI/ML libraries
    Call this early in your application startup
    """
    library_loggers = [
        'transformers',
        'whisper',
        'openai',
        'langchain',
        'langchain_community',
        'langchain_huggingface',
        'sentence_transformers',
        'faiss',
        'urllib3',
        'requests',
        'httpx',
        'httpcore',
        'asyncio',
        'matplotlib',
        'PIL',
        'marker',
        'pdf2docx',
        'pymongo'
    ]

    for logger_name in library_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
        # Ensure they don't propagate to root
        logging.getLogger(logger_name).propagate = False