"""
extraction_model.py - PDF Data Extraction Model
-------------------------------------------------
HOW TO PLUG IN YOUR OWN MODEL
  Replace the body of ExtractionModel.extract() with your model's call.

  Contract:
    INPUT  → pdf_path (str) : local path to downloaded PDF
    OUTPUT → dict           : structured extracted data

Strategies included:
  ✅ pypdf text extraction + regex heuristics  (default, no API key needed)
  💬 Claude vision API stub                    (uncomment to use)
  💬 OpenAI GPT-4o stub                        (uncomment to use)
  💬 LlamaParse stub                           (uncomment to use)
"""

import os
import sys
import json
from pathlib import Path

# Add the parent directory to sys.path so we can import the Unified_PDF_Platform
base_dir = Path(__file__).resolve().parent.parent
platform_path = str(base_dir / "Unified_PDF_Platform")
if platform_path not in sys.path:
    sys.path.append(platform_path)

try:
    from unified_router import UnifiedRouter
    ROUTER_AVAILABLE = True
except ImportError:
    ROUTER_AVAILABLE = False

class ExtractionModel:
    _router_instance = None

    def __init__(self):
        if ROUTER_AVAILABLE and ExtractionModel._router_instance is None:
            print("   [INIT] Initializing UnifiedRouter singleton...")
            ExtractionModel._router_instance = UnifiedRouter()

    def extract(self, pdf_path: str) -> dict:
        """
        Extract structured data from a local PDF file by calling the UnifiedRouter directly.

        Args:
            pdf_path: Path to the PDF (e.g. 'pdf_Ab12Cd34.pdf').

        Returns:
            dict — contains structured data from the extraction platform.
        """
        if not os.path.exists(pdf_path):
            return {"filename": pdf_path, "status": "error", "error": "File not found"}

        if not ROUTER_AVAILABLE:
            return {
                "filename": pdf_path,
                "status": "error",
                "error": "UnifiedRouter module not found. Ensure Unified_PDF_Platform is in the correct location."
            }

        try:
            # Call the router directly (no subprocess overhead)
            print(f"   [PROCESS] Routing with direct module call: {os.path.basename(pdf_path)}")
            extracted_data = ExtractionModel._router_instance.process(pdf_path)
            
            # Ensure status is set
            if "error" in extracted_data:
                extracted_data["status"] = "error"
            else:
                extracted_data["status"] = "extracted"
                
            return extracted_data

        except Exception as e:
            return {
                "filename": pdf_path,
                "status": "error",
                "error": f"Direct extraction failed: {str(e)}",
            }
