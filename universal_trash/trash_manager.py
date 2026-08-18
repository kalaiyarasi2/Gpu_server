import os
import shutil
import logging
from datetime import datetime
from universal_trash.config import TRASH_ROOT_PATH

# Configure a basic logger for the trash manager
logger = logging.getLogger("universal_trash.manager")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('[%(asctime)s] %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

def move_to_trash(file_path: str, module_name: str, file_type: str = "processed") -> bool:
    """
    Moves a file to the universal trash folder.
    
    Args:
        file_path: The absolute or relative path to the file to be trashed.
        module_name: The name of the module trashing the file (e.g., 'Email_pipeline', 'Unified_PDF_Platform').
        file_type: A sub-category for the file (e.g., 'input', 'output', 'extracted', 'downloads').
        
    Returns:
        bool: True if successful, False otherwise.
    """
    if not os.path.exists(file_path):
        logger.warning(f"File not found, cannot move to trash: {file_path}")
        return False

    # Create the structured path: TRASH_ROOT_PATH / module_name / YYYY-MM-DD / file_type
    date_str = datetime.now().strftime("%Y-%m-%d")
    trash_dir = os.path.join(TRASH_ROOT_PATH, module_name, date_str, file_type)
    
    try:
        os.makedirs(trash_dir, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create trash directory {trash_dir}: {e}")
        return False
    
    filename = os.path.basename(file_path)
    dest_path = os.path.join(trash_dir, filename)
    
    # If a file with the same name exists in the trash today, append a timestamp
    if os.path.exists(dest_path):
        name, ext = os.path.splitext(filename)
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"{name}_{timestamp}{ext}"
        dest_path = os.path.join(trash_dir, filename)
    
    try:
        shutil.move(file_path, dest_path)
        logger.info(f"File moved to trash: {dest_path}")
        return True
    except PermissionError:
        logger.error(f"Permission denied. File might be locked or in use: {file_path}")
        return False
    except Exception as e:
        logger.error(f"Failed to move file {file_path} to trash: {e}")
        return False

def copy_to_trash(file_path: str, module_name: str, file_type: str = "processed") -> bool:
    """
    Copies a file to the universal trash folder instead of moving it.
    Useful when you want to keep the file in its original location but also back it up in trash.
    """
    if not os.path.exists(file_path):
        return False

    date_str = datetime.now().strftime("%Y-%m-%d")
    trash_dir = os.path.join(TRASH_ROOT_PATH, module_name, date_str, file_type)
    
    try:
        os.makedirs(trash_dir, exist_ok=True)
        filename = os.path.basename(file_path)
        dest_path = os.path.join(trash_dir, filename)
        
        if os.path.exists(dest_path):
            name, ext = os.path.splitext(filename)
            timestamp = datetime.now().strftime("%H%M%S")
            filename = f"{name}_{timestamp}{ext}"
            dest_path = os.path.join(trash_dir, filename)
            
        shutil.copy2(file_path, dest_path)
        logger.info(f"File copied to trash: {dest_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to copy file {file_path} to trash: {e}")
        return False
