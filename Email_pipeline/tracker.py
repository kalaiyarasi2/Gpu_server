"""
tracker.py - Processed Email Tracker
--------------------------------------
Keeps a persistent JSON file of every Gmail message ID that has
already been processed. This guarantees:
  • Already-read / already-processed emails are NEVER sent to extraction again
  • Works across restarts — state survives process exits
  • Thread-safe for single-process polling
"""

import json
import os
from datetime import datetime

TRACKER_FILE = "processed_ids.json"


def load_processed() -> dict:
    """Load the full tracker dict {message_id: {filename, processed_at}}."""
    if not os.path.exists(TRACKER_FILE):
        return {}
    try:
        with open(TRACKER_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # If file is corrupted or wrong encoding, backup and return empty
        if os.path.exists(TRACKER_FILE):
            try:
                os.rename(TRACKER_FILE, f"{TRACKER_FILE}.corrupt")
            except:
                pass
        return {}


def get_processed_ids() -> list:
    """Return just the list of processed message IDs."""
    return list(load_processed().keys())


def mark_processed(message_id: str, filenames: list[str]):
    """
    Record a message as processed.

    Args:
        message_id: Gmail message ID.
        filenames:  List of PDF filenames that were extracted from this message.
    """
    tracker = load_processed()
    tracker[message_id] = {
        "filenames":    filenames,
        "processed_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(TRACKER_FILE, "w", encoding="utf-8") as f:
        json.dump(tracker, f, indent=2)


def is_processed(message_id: str) -> bool:
    """Return True if this message has already been processed."""
    return message_id in load_processed()


def summary() -> dict:
    """Return a summary of what has been tracked so far."""
    data  = load_processed()
    files = [f for v in data.values() for f in v.get("filenames", [])]
    return {
        "total_emails_processed": len(data),
        "total_files_extracted":  len(files),
        "tracker_file":           TRACKER_FILE,
    }
