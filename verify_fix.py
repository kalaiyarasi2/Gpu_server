import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load env vars
load_dotenv(Path(r"C:\Users\Intern\pdf_extractor\.env"))

# Add backend to path
sys.path.append(str(Path(r"C:\Users\Intern\pdf_extractor\Insurance_pdf_extractor-main\backend")))

try:
    from insurance_extractor import EnhancedInsuranceExtractor
    from PIL import Image
    
    print(f"PIL Image.MAX_IMAGE_PIXELS: {Image.MAX_IMAGE_PIXELS}")
    
    pdf_path = r"C:\Users\Intern\pdf_extractor\Unified_PDF_Platform\uploads\Chesapeake Employers Insurance - 22-25.fixed.pdf"
    
    if not os.path.exists(pdf_path):
        print(f"ERROR: File not found at {pdf_path}")
        sys.exit(1)
        
    extractor = EnhancedInsuranceExtractor()
    print("\nStarting test extraction (this might take a minute, we only care if it CRASHES with DecompressionBomb)...")
    
    # We'll use extract_text_from_pdf specifically as that's where the error occurred
    text, metadata = extractor.extract_text_from_pdf(pdf_path)
    
    print(f"\nSUCCESS! Extracted {len(text)} characters.")
    print(f"First 100 chars: {text[:100]}...")
    
except Exception as e:
    print(f"\nEXTRACTION FAILED: {e}")
    import traceback
    traceback.print_exc()
