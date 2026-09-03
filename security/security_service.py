import os
import json
import shutil
import logging
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime

from .file_validator import FileValidator, FileValidationResult
from .malware_scanner import MalwareScanner, ScanResult, ScanStatus

# We need to import the monitor DB to log events
import sys
base_dir = Path(__file__).resolve().parent.parent
if str(base_dir) not in sys.path:
    sys.path.append(str(base_dir))

try:
    from monitor.monitor_db import monitor_db
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logging.warning("monitor_db could not be imported. Security events won't be saved to DB.")

logger = logging.getLogger(__name__)

from enum import Enum
if sys.version_info >= (3, 11):
    class SecurityStatus(str, Enum):
        pass
else:
    class SecurityStatus(str):
        pass
    
class Status:
    CLEAN = "CLEAN"
    REJECTED = "REJECTED" # Validation failed
    INFECTED = "INFECTED"
    ERROR = "ERROR"

@dataclass
class SecurityResult:
    status: str
    file_path: Optional[str] # The new path of the file if moved (clean or quarantine)
    hash: Optional[str]
    reason: Optional[str]
    details: Dict[str, Any]

class SecurityGateway:
    def __init__(self, config_path: str = None):
        self.base_dir = Path(__file__).resolve().parent.parent
        self.config = self._load_config(config_path)
        
        self.validator = FileValidator(self.config)
        self.scanner = MalwareScanner(self.config)
        
        # Ensure directories exist
        self.storage_dir = self.base_dir / "storage"
        self.incoming_dir = self.storage_dir / "incoming"
        self.clean_dir = self.storage_dir / "clean"
        self.quarantine_dir = self.storage_dir / "quarantine"
        
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.clean_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def _load_config(self, config_path: str = None) -> dict:
        if not config_path:
            config_path = os.path.join(self.base_dir, "security", "security_config.json")
            
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load security config: {e}")
                
        # Default config if file doesn't exist
        return {
            "max_file_size_mb": 1024,
            "allowed_extensions": [".pdf", ".xlsx", ".xls", ".csv"],
            "clamav_host": "localhost",
            "clamav_port": 3310,
            "clamav_timeout": 30,
            "clamav_fallback_mode": "warn_and_allow",
            "quarantine_retention_days": 7
        }

    def process(self, file_path: str, request_id: str = None) -> SecurityResult:
        """
        Main entry point for the security gateway.
        Processes a file through validation and scanning.
        Returns the result and moves the file accordingly.
        """
        original_filename = os.path.basename(file_path)
        
        if not os.path.exists(file_path):
            return self._fail(Status.ERROR, "File not found", file_path=file_path, request_id=request_id)
            
        # 1. Validation
        val_result = self.validator.validate(file_path)
        if not val_result.is_valid:
            # We don't keep invalid files, just remove them
            try:
                os.remove(file_path)
            except Exception:
                pass
            return self._fail(Status.REJECTED, val_result.reason, hash=val_result.file_hash, file_path=None, request_id=request_id)
            
        # 2. Scanning
        scan_result = self.scanner.scan_file(file_path)
        
        details = {
            "file_size": val_result.file_size_bytes,
            "mime_type": val_result.detected_mime,
            "scan_status": scan_result.status.value,
            "threat_name": scan_result.threat_name
        }
        
        if scan_result.status == ScanStatus.INFECTED:
            # Quarantine the file
            quarantine_path = self._quarantine_file(file_path, val_result.file_hash, details)
            self._log_event(request_id, original_filename, val_result.file_hash, val_result.file_size_bytes, 
                            "INFECTED", scan_result.threat_name, "quarantined", quarantine_path, details)
            return SecurityResult(Status.INFECTED, quarantine_path, val_result.file_hash, scan_result.details, details)
            
        elif scan_result.status == ScanStatus.ERROR:
            # Block processing but don't delete (maybe temporary error)
            self._log_event(request_id, original_filename, val_result.file_hash, val_result.file_size_bytes, 
                            "ERROR", None, "blocked", file_path, details)
            return SecurityResult(Status.ERROR, file_path, val_result.file_hash, scan_result.details, details)
            
        # CLEAN or UNAVAILABLE (allowed by fallback)
        clean_path = self._move_to_clean(file_path, val_result.file_hash)
        
        status_to_log = "CLEAN" if scan_result.status == ScanStatus.CLEAN else "UNAVAILABLE"
        self._log_event(request_id, original_filename, val_result.file_hash, val_result.file_size_bytes, 
                        status_to_log, None, "allowed", clean_path, details)
                        
        return SecurityResult(Status.CLEAN, clean_path, val_result.file_hash, "File is safe", details)

    def _fail(self, status: str, reason: str, hash: str = None, file_path: str = None, request_id: str = None) -> SecurityResult:
        details = {"error": reason}
        if request_id:
            filename = os.path.basename(file_path) if file_path else "unknown"
            self._log_event(request_id, filename, hash, 0, "VALIDATION_FAILED", None, "rejected", None, details)
        return SecurityResult(status, file_path, hash, reason, details)

    def _quarantine_file(self, file_path: str, file_hash: str, details: dict) -> str:
        """Move an infected file to quarantine and save a metadata sidecar."""
        filename = os.path.basename(file_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"{timestamp}_{file_hash[:8]}_{filename}.qnd"
        quarantine_path = os.path.join(self.quarantine_dir, safe_name)
        
        try:
            shutil.move(file_path, quarantine_path)
            
            # Write sidecar
            sidecar_path = f"{quarantine_path}.json"
            meta = {
                "original_filename": filename,
                "quarantined_at": datetime.now().isoformat(),
                "hash": file_hash,
                "details": details
            }
            with open(sidecar_path, "w") as f:
                json.dump(meta, f, indent=2)
                
            return quarantine_path
        except Exception as e:
            logger.error(f"Failed to quarantine file {file_path}: {e}")
            return file_path

    def _move_to_clean(self, file_path: str, file_hash: str) -> str:
        """Move a safe file to the clean directory."""
        filename = os.path.basename(file_path)
        # We could rename it with hash, but keeping original name is better for downstream tools
        # We'll use a subfolder to avoid collisions
        unique_folder = os.path.join(self.clean_dir, file_hash[:16])
        os.makedirs(unique_folder, exist_ok=True)
        clean_path = os.path.join(unique_folder, filename)
        
        try:
            shutil.move(file_path, clean_path)
            return clean_path
        except Exception as e:
            logger.error(f"Failed to move file to clean dir {file_path}: {e}")
            return file_path

    def _log_event(self, request_id, filename, file_hash, size, scan_status, threat, action, q_path, details):
        """Log the event to the monitor database if available."""
        if not DB_AVAILABLE:
            return
            
        try:
            # We will implement this method in monitor_db next
            if hasattr(monitor_db, "create_security_event"):
                monitor_db.create_security_event(
                    request_id=request_id or "N/A",
                    filename=filename,
                    file_hash=file_hash,
                    file_size=size,
                    scan_status=scan_status,
                    threat_name=threat,
                    action_taken=action,
                    quarantine_path=q_path,
                    details=json.dumps(details)
                )
        except Exception as e:
            logger.error(f"Failed to log security event to DB: {e}")

    def cleanup_quarantine(self):
        """Utility to delete old quarantined files based on retention policy."""
        retention_days = self.config.get("quarantine_retention_days", 7)
        now = time.time()
        
        count = 0
        for item in os.listdir(self.quarantine_dir):
            item_path = os.path.join(self.quarantine_dir, item)
            if os.path.isfile(item_path):
                # Check modification time
                if os.stat(item_path).st_mtime < now - (retention_days * 86400):
                    try:
                        os.remove(item_path)
                        count += 1
                    except Exception as e:
                        logger.error(f"Failed to delete old quarantine file {item_path}: {e}")
        return count
