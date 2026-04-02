import sys
from pathlib import Path
import os

# Add relevant paths
sys.path.append(r"c:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform")
from unified_router import UnifiedRouter

def test_fix():
    pdf_path = r"c:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform\uploads\Mutual Of Omaha- Greener Acres- March 26 .pdf"
    
    router = UnifiedRouter()
    print(f"Starting verification for: {pdf_path}")
    result = router.run_invoice_extractor(pdf_path)
    print(f"Result: {result}")

if __name__ == "__main__":
    test_fix()
