import sys
import os
from pathlib import Path

# Add the Unified_PDF_Platform directory to sys.path so we can import UnifiedRouter
sys.path.append(r"c:\Users\INT002\pdf_extractor\Unified_PDF_Platform")

# Fix for Windows console encoding
sys.stdout.reconfigure(encoding='utf-8')

# Mock ChunkedInsuranceExtractor to avoid import error/delay
import sys
from unittest.mock import MagicMock
sys.modules['chunked_extractor'] = MagicMock()

from unified_router import UnifiedRouter

pdf_path = r"c:\Users\INT002\pdf_extractor\GIS 23 Restaurant Services Feb'25 Inv.pdf"

print(f"Testing Router on: {pdf_path}")

router = UnifiedRouter()
result = router.process(pdf_path)

print("\n--- Final Result ---")
print(result)
