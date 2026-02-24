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

"""
Logging utility for the Gmail Connector application.

Provides modular loggers that write detailed logs into rotating files,
automatically named based on the calling file, its directory, or a user-specified name.

Log files are saved in the `logs/` directory with preserved directory structure
(e.g., src/core/agent.py -> logs/src/core/agent.log) and rotate after reaching 100 MB (up to 5 backups).

Each log entry includes:
- Timestamp
- Log level (DEBUG, INFO, etc.)
- Logger name
- Filename and line number
- Function name
- Log message
"""

"""
Logging utility for the Gmail Connector application.

Provides modular loggers that write detailed logs into rotating files,
automatically named based on the calling file, its directory, or a user-specified name.

Log files are saved in the `logs/` directory with preserved directory structure
(e.g., src/core/agent.py -> logs/src/core/agent.log) and rotate after reaching 100 MB (up to 5 backups).

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
from typing import Optional
from rich.logging import RichHandler
from rich.console import Console
import sys

# Global flag to track if we've configured the root logger
_ROOT_LOGGER_CONFIGURED = False
# Console logging with Rich formatting
_console_logger = None


def _configure_root_logger():
    """Configure root logger to prevent library interference"""
    global _ROOT_LOGGER_CONFIGURED

    if _ROOT_LOGGER_CONFIGURED:
        return

    # Get root logger
    root_logger = logging.getLogger()

    # Remove any existing console handlers that libraries might have added
    for handler in root_logger.handlers[:]:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, RotatingFileHandler
        ):
            root_logger.removeHandler(handler)  # type: ignore

    # Set root logger level to WARNING to reduce noise from libraries
    root_logger.setLevel(logging.WARNING)

    _ROOT_LOGGER_CONFIGURED = True  # type: ignore


def _create_logger(module_name: str, log_path: Optional[str] = None) -> logging.Logger:
    """
    Internal helper to create and configure a logger.

    Args:
        module_name (str): Name to assign to the logger (used for naming the log file).
        log_path (str, optional): Relative path for the log file. If None, uses module_name directly.

    Returns:
        logging.Logger: Configured logger instance.
    """
    # Configure root logger first
    _configure_root_logger()

    logger = logging.getLogger(module_name)

    # Prevent propagation to root logger to avoid console output
    logger.propagate = False

    logger.setLevel(logging.DEBUG)

    # Determine the log file path
    if log_path:
        log_file = f"logs/{log_path}.log"
        # Ensure the directory structure exists
        log_dir = os.path.dirname(log_file)
        os.makedirs(log_dir, exist_ok=True)
    else:
        # Fallback to flat structure
        os.makedirs("logs", exist_ok=True)
        log_file = f"logs/{module_name}.log"

    # Check if logger already has the correct file handler
    existing_file_handler = None
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename.endswith(
            f"{module_name}.log"
        ):
            existing_file_handler = handler
            break

    if not existing_file_handler:
        # Set up rotating file handler
        file_handler = RotatingFileHandler(log_file, maxBytes=100 * 1024 * 1024, backupCount=5)

        # Set a detailed formatter
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(funcName)s() | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
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
    The log file structure mirrors the source directory structure.

    Returns:
        logging.Logger: Logger named after the caller's directory.
    """
    caller_path = inspect.stack()[1].filename

    # Get relative path from current working directory
    try:
        rel_path = os.path.relpath(os.path.dirname(caller_path), os.getcwd())
        dir_name = os.path.basename(os.path.dirname(caller_path))
        return _create_logger(dir_name, rel_path)
    except ValueError:
        # If paths are on different drives (Windows), fall back to basename
        dir_name = os.path.basename(os.path.dirname(caller_path))
        return _create_logger(dir_name)


def get_file_logger() -> logging.Logger:
    """
    Creates a logger based on the filename of the calling file.

    Useful when each module should have its own log file.
    The log file structure mirrors the source file structure.

    Returns:
        logging.Logger: Logger named after the caller's filename (without extension).
    """
    caller_path = inspect.stack()[1].filename

    # Get relative path from current working directory
    try:
        rel_path = os.path.relpath(caller_path, os.getcwd())
        # Remove the .py extension from the relative path
        rel_path_no_ext = os.path.splitext(rel_path)[0]
        file_name = os.path.splitext(os.path.basename(caller_path))[0]
        return _create_logger(file_name, rel_path_no_ext)
    except ValueError:
        # If paths are on different drives (Windows), fall back to basename
        file_name = os.path.splitext(os.path.basename(caller_path))[0]
        return _create_logger(file_name)


def get_logger_by_name(name: str, preserve_structure: bool = False) -> logging.Logger:
    """
    Creates a logger based on a custom name.

    Useful when you want full control over the logger naming.

    Args:
        name (str): Custom name for the logger and log file.
                   Can include path separators (e.g., 'api/endpoints') to create nested structure.
        preserve_structure (bool): If True and name contains path separators, preserve directory structure.

    Returns:
        logging.Logger: Logger with the given custom name.
    """
    if preserve_structure and ("/" in name or "\\" in name):
        # Normalize path separators
        normalized_name = name.replace("\\", "/")
        return _create_logger(os.path.basename(normalized_name), normalized_name)
    return _create_logger(name)


def suppress_library_loggers():
    """
    Suppress verbose logging from common AI/ML libraries
    Call this early in your application startup
    """
    library_loggers = [
        "transformers",
        "whisper",
        "openai",
        "langchain",
        "langchain_community",
        "langchain_huggingface",
        "sentence_transformers",
        "faiss",
        "urllib3",
        "requests",
        "httpx",
        "httpcore",
        "asyncio",
        "matplotlib",
        "PIL",
        "marker",
        "pdf2docx",
        "pymongo",
    ]

    for logger_name in library_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
        # Ensure they don't propagate to root
        logging.getLogger(logger_name).propagate = False


def get_console_logger():
    """
    Get a console logger with Rich formatting and proper logging format.

    Returns a logging.Logger instance configured with RichHandler that produces
    formatted output matching the file logger format:
    TIMESTAMP | LEVEL | LOGGER_NAME | FILENAME:LINE | FUNCTION() | MESSAGE

    Returns:
        logging.Logger: Logger configured for console output with Rich formatting.

    Example:
        >>> logger = get_console_logger()
        >>> logger.info("Application started")
        >>> logger.warning("This is a warning")
        >>> logger.error("An error occurred")
    """
    global _console_logger

    if _console_logger is not None:
        return _console_logger

    _console_logger = logging.getLogger("console")
    _console_logger.propagate = False
    _console_logger.setLevel(logging.DEBUG)

    # Clear any existing handlers
    _console_logger.handlers.clear()

    if RichHandler:
        # Use RichHandler for colored output
        handler = RichHandler(rich_tracebacks=True, markup=False)
    else:
        # Fallback to standard StreamHandler
        handler = logging.StreamHandler()

    # Set formatter to match file logger format
    formatter = logging.Formatter(fmt="%(funcName)s() | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)

    _console_logger.addHandler(handler)

    return _console_logger


def configure_uvicorn_logging(force_terminal: bool = False, width: int = 200):
    """
    Configure uvicorn and related loggers with Rich formatting.

    Args:
        force_terminal: Force Rich to format as if writing to a terminal
                    (useful for nohup/file redirection)
        width: Fixed console width for formatting (default 120)
    """
    loggers_to_configure = ["uvicorn", "uvicorn.access", "uvicorn.error"]

    for logger_name in loggers_to_configure:
        logger = logging.getLogger(logger_name)
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.handlers.clear()

        # Use RichHandler with console configuration
        console = Console(force_terminal=force_terminal, width=width, file=sys.stdout)

        handler = RichHandler(
            console=console,
            rich_tracebacks=True,
            markup=False,
            show_time=True,
            show_level=True,
            show_path=False,
        )

        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(funcName)s() | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
