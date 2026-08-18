import os
from pathlib import Path

# Base directory for the trash folder (default: universal_trash_bin at root level)
BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRASH_ROOT_PATH = os.getenv("TRASH_ROOT_PATH", str(BASE_DIR / "universal_trash_bin"))

# Number of days to retain files before automatic deletion
TRASH_RETENTION_DAYS = int(os.getenv("TRASH_RETENTION_DAYS", "7"))

# Cleanup interval in seconds (default: 1 day = 86400 seconds)
# Can be lowered for testing, e.g., 3600 for 1 hour.
CLEANUP_INTERVAL = int(os.getenv("CLEANUP_INTERVAL", "86400"))
