import sys
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent.parent.parent

if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

# Load root package functions
from universal_trash import (
    move_to_trash,
    perform_cleanup
)

# Handle potential naming differences in start service
try:
    from universal_trash import start_cleanup_service
except ImportError:
    try:
        from universal_trash import start_scheduled_cleanup as start_cleanup_service
    except ImportError:
        start_cleanup_service = None

start_scheduled_cleanup = start_cleanup_service

# Load GPU-specific function
from .trash_manager import copy_to_trash

__all__ = [
    "move_to_trash",
    "copy_to_trash",
    "start_cleanup_service",
    "start_scheduled_cleanup",
    "perform_cleanup"
]
