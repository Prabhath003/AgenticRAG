"""
Shared dependencies and singletons for the AgenticRAG API.

The task_manager is instantiated once at import time and used by all route handlers.
"""

from src.core import Manager

# Module-level singleton — created once at import time
task_manager = Manager()
