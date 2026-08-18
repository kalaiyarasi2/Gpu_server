import os
import time
import logging
import threading
from universal_trash.config import TRASH_ROOT_PATH, TRASH_RETENTION_DAYS, CLEANUP_INTERVAL

# Configure logging
logger = logging.getLogger("universal_trash.cleanup")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('[%(asctime)s] %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

def perform_cleanup():
    """
    Scans the Universal Trash Folder and deletes files older than the retention period.
    Also removes empty directories.
    """
    logger.info("Universal Trash Cleanup Started")
    
    if not os.path.exists(TRASH_ROOT_PATH):
        logger.info(f"Trash root path does not exist yet: {TRASH_ROOT_PATH}. Skipping cleanup.")
        return

    now = time.time()
    retention_seconds = TRASH_RETENTION_DAYS * 86400
    cutoff_time = now - retention_seconds
    
    files_scanned = 0
    files_deleted = 0
    files_skipped = 0
    
    # Bottom-up traversal so we can delete empty directories after their contents
    for root, dirs, files in os.walk(TRASH_ROOT_PATH, topdown=False):
        for name in files:
            files_scanned += 1
            file_path = os.path.join(root, name)
            try:
                # Check modification time
                mtime = os.path.getmtime(file_path)
                if mtime < cutoff_time:
                    os.remove(file_path)
                    files_deleted += 1
                    logger.info(f"File deleted: {file_path}")
                else:
                    files_skipped += 1
            except PermissionError:
                logger.error(f"Permission denied deleting file (might be locked): {file_path}")
                files_skipped += 1
            except Exception as e:
                logger.error(f"File deletion failure for {file_path}: {e}")
                files_skipped += 1
                
        # Remove empty directories
        for name in dirs:
            dir_path = os.path.join(root, name)
            try:
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    logger.info(f"Empty directory removed: {dir_path}")
            except OSError:
                pass # Directory not empty or locked, safe to ignore
            except Exception as e:
                logger.error(f"Failed to remove directory {dir_path}: {e}")
                
    logger.info(f"Files Scanned: {files_scanned}")
    logger.info(f"Files Deleted: {files_deleted}")
    logger.info(f"Files Skipped: {files_skipped}")
    logger.info("Cleanup Completed")

def _cleanup_loop():
    """Background loop that sleeps and triggers cleanup periodically."""
    # Run once immediately on startup
    try:
        perform_cleanup()
    except Exception as e:
        logger.error(f"Unexpected error in initial cleanup run: {e}")
        
    while True:
        time.sleep(CLEANUP_INTERVAL)
        try:
            perform_cleanup()
        except Exception as e:
            logger.error(f"Unexpected error in cleanup service: {e}")

def start_scheduled_cleanup():
    """
    Starts the cleanup service in a background daemon thread.
    Returns the Thread object.
    """
    thread = threading.Thread(target=_cleanup_loop, daemon=True, name="TrashCleanupThread")
    thread.start()
    logger.info(f"Scheduled cleanup service started. Interval: {CLEANUP_INTERVAL}s, Retention: {TRASH_RETENTION_DAYS} days.")
    return thread

if __name__ == "__main__":
    # If run directly, just perform a one-off cleanup
    perform_cleanup()
