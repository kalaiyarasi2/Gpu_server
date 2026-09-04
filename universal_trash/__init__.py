from .trash_manager import move_to_trash
from .cleanup_service import start_scheduled_cleanup, perform_cleanup
start_cleanup_service = start_scheduled_cleanup

__all__ = [
    "move_to_trash",
    "start_cleanup_service",
    "start_scheduled_cleanup",
    "perform_cleanup"
]
