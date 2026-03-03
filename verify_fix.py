import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Get the project root directory
BASE_DIR = Path(__file__).resolve().parent

# Load env vars from root
load_dotenv(BASE_DIR / ".env")

# Add backend to path
BACKEND_DIR = BASE_DIR / "Insurance_pdf_extractor-main" / "backend"
sys.path.append(str(BACKEND_DIR))

try:
    from insurance_extractor import EnhancedInsuranceExtractor
    from PIL import Image
    
    print(f"PIL Image.MAX_IMAGE_PIXELS: {Image.MAX_IMAGE_PIXELS}")
    
    # Try the original filename found in the listing (without .fixed)
    pdf_path = BASE_DIR / "Unified_PDF_Platform" / "uploads" / "Chesapeake Employers Insurance - 22-25.pdf"
    
    if not pdf_path.exists():
        # Try any other PDF if this one is missing
        print(f"WARNING: Preferred test file not found at {pdf_path}")
        uploads_dir = BASE_DIR / "Unified_PDF_Platform" / "uploads"
        pdf_files = list(uploads_dir.glob("*.pdf"))
        if pdf_files:
            pdf_path = pdf_files[0]
            print(f"Using alternative test file: {pdf_path}")
        else:
            print(f"ERROR: No PDF files found in {uploads_dir}")
            sys.exit(1)
        
    extractor = EnhancedInsuranceExtractor()
    print(f"\nStarting test extraction on {pdf_path.name}...")
    print("(This might take a minute, we only care if it CRASHES with DecompressionBomb)...")
    
    # We'll use extract_text_from_pdf specifically as that's where the error occurred
    text, metadata = extractor.extract_text_from_pdf(str(pdf_path))
    
    print(f"\nSUCCESS! Extracted {len(text)} characters.")
    print(f"First 100 chars: {text[:100]}...")
    
except Exception as e:
    print(f"\nEXTRACTION FAILED: {e}")
    import traceback
    traceback.print_exc()
