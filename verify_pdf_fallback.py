
import sys
import os

# Add relevant paths
sys.path.append(r"C:\Users\INTERN\main_project\Main--main\Insurance_pdf_extractor-main\backend")

from ocr_text import OCRPDFExtractor

pdf_path = r"C:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform\uploads\Bank statement.pdf"

print(f"Verifying fallback for: {pdf_path}")

try:
    extractor = OCRPDFExtractor(pdf_path)
    # We just need to see if it can convert to images
    print("Testing image conversion (should trigger fallback)...")
    images = extractor._convert_pdf_to_images(dpi=300)
    print(f"Success! Got {len(images)} images via fallback.")
    
    # Optional: try actual extraction of first page
    print("Testing extraction of page 1...")
    text, meta = extractor.extract(dpi=300, verbose=True)
    print(f"Extracted {len(text)} characters.")
    print("Verification PASSED!")

except Exception as e:
    print(f"Verification FAILED: {e}")
    import traceback
    traceback.print_exc()
