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
            # --- Security Gateway ---
            import sys
            if platform_path not in sys.path:
                sys.path.append(platform_path)
            
            try:
                from security import SecurityGateway, Status
                security_gateway = SecurityGateway()
                
                print(f"   [SECURITY] Running security scan on: {os.path.basename(pdf_path)}")
                # Process the file synchronously (ExtractionModel runs in ThreadPoolExecutor)
                sec_result = security_gateway.process(pdf_path)
                
                if sec_result.status == Status.REJECTED or sec_result.status == Status.INFECTED:
                    return {
                        "filename": pdf_path,
                        "status": "security_blocked",
                        "error": f"Security check failed: {sec_result.reason}"
                    }
                elif sec_result.status == Status.ERROR:
                    return {
                        "filename": pdf_path,
                        "status": "error",
                        "error": "Security service unavailable or encountered an error"
                    }
                    
                # File is safe, update pdf_path to the clean path
                safe_pdf_path = sec_result.file_path
                print(f"   [SECURITY] File is {sec_result.status}. Proceeding to extraction.")
            except Exception as e:
                print(f"   [SECURITY] Warning: Failed to run security gateway: {e}")
                safe_pdf_path = pdf_path # Fallback to original if security module is somehow completely broken

            # Call the router directly (no subprocess overhead)
            print(f"   [PROCESS] Routing with direct module call: {os.path.basename(safe_pdf_path)}")
            extracted_data = ExtractionModel._router_instance.process(safe_pdf_path)
            
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
