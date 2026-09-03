import os
import hashlib
import mimetypes
import logging
from dataclasses import dataclass
from typing import Tuple, List, Optional
from pathlib import Path

# Try to import magic for better MIME detection, fallback to mimetypes
try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    MAGIC_AVAILABLE = False
    logging.warning("python-magic not available, falling back to mimetypes")

logger = logging.getLogger(__name__)

@dataclass
class FileValidationResult:
    is_valid: bool
    reason: Optional[str]
    file_hash: Optional[str]
    detected_mime: Optional[str]
    file_size_bytes: int

class FileValidator:
    def __init__(self, config: dict):
        self.max_size_bytes = config.get("max_file_size_mb", 1024) * 1024 * 1024
        self.allowed_extensions = [ext.lower() for ext in config.get("allowed_extensions", [".pdf", ".xlsx", ".xls", ".csv"])]
        
        # Mapping of extension to allowed mimetypes
        self.allowed_mime_types = {
            ".pdf": ["application/pdf"],
            ".xlsx": ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"],
            ".xls": ["application/vnd.ms-excel", "application/CDFV2"],
            ".csv": ["text/csv", "text/plain"]
        }

    def validate(self, file_path: str) -> FileValidationResult:
        """Run all validation checks on the file."""
        if not os.path.exists(file_path):
            return FileValidationResult(False, "File not found", None, None, 0)
            
        file_size = os.path.getsize(file_path)
        
        # 1. Size check
        if file_size > self.max_size_bytes:
            return FileValidationResult(
                False, 
                f"File exceeds maximum allowed size ({file_size} > {self.max_size_bytes} bytes)",
                None, None, file_size
            )
            
        # 2. Hash file
        file_hash = self._calculate_hash(file_path)
        
        # 3. Extension check
        ext = Path(file_path).suffix.lower()
        if ext not in self.allowed_extensions:
            return FileValidationResult(
                False,
                f"Extension {ext} is not allowed",
                file_hash, None, file_size
            )
            
        # 4. MIME type check
        detected_mime = self._detect_mime_type(file_path)
        if not self._is_mime_allowed_for_ext(detected_mime, ext):
             return FileValidationResult(
                False,
                f"Detected MIME type {detected_mime} does not match extension {ext}",
                file_hash, detected_mime, file_size
            )
            
        # 5. Basic structure check (magic bytes)
        struct_valid, struct_reason = self._validate_structure(file_path, ext)
        if not struct_valid:
             return FileValidationResult(
                False,
                f"File structure invalid: {struct_reason}",
                file_hash, detected_mime, file_size
            )
            
        return FileValidationResult(True, None, file_hash, detected_mime, file_size)

    def _calculate_hash(self, file_path: str) -> str:
        """Calculate SHA-256 hash of the file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _detect_mime_type(self, file_path: str) -> str:
        """Detect MIME type using python-magic or mimetypes."""
        if MAGIC_AVAILABLE:
            try:
                return magic.from_file(file_path, mime=True)
            except Exception as e:
                logger.warning(f"magic.from_file failed: {e}. Falling back to mimetypes.")
        
        mime_type, _ = mimetypes.guess_type(file_path)
        return mime_type or "application/octet-stream"

    def _is_mime_allowed_for_ext(self, mime: str, ext: str) -> bool:
        """Check if the detected MIME type is appropriate for the extension."""
        allowed = self.allowed_mime_types.get(ext, [])
        # Some flexibility for CSVs and text files
        if ext == ".csv" and ("text" in mime or mime == "application/octet-stream"):
            return True
        # fallback allowing excel to be detected as zip
        if ext == ".xlsx" and mime == "application/zip":
             return True
        return mime in allowed or not allowed

    def _validate_structure(self, file_path: str, ext: str) -> Tuple[bool, str]:
        """Perform basic magic byte / header validation based on extension."""
        try:
            with open(file_path, "rb") as f:
                header = f.read(8)
                
            if ext == ".pdf":
                if not header.startswith(b"%PDF-"):
                    return False, "Missing PDF magic bytes (%PDF-)"
            elif ext == ".xlsx":
                if not header.startswith(b"PK\x03\x04"):
                    return False, "Missing ZIP magic bytes for XLSX (PK)"
            elif ext == ".xls":
                if not header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
                    return False, "Missing OLE2 magic bytes for XLS"
            # CSV is plain text, hard to validate structure purely by header reliably
                    
            return True, ""
        except Exception as e:
            return False, f"Could not read file header: {e}"
