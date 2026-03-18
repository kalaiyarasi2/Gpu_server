
import fitz
from pdf2image import convert_from_path
import os

pdf_path = r"C:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform\uploads\Bank statement.pdf"

print(f"Testing file: {pdf_path}")

try:
    print("\n--- Testing pdf2image (Poppler) ---")
    images = convert_from_path(pdf_path, first_page=1, last_page=1)
    print(f"pdf2image success! Got {len(images)} images.")
except Exception as e:
    print(f"pdf2image failed: {e}")

try:
    print("\n--- Testing PyMuPDF (fitz) ---")
    doc = fitz.open(pdf_path)
    print(f"PyMuPDF success! Page count: {len(doc)}")
    page = doc[0]
    pix = page.get_pixmap(dpi=300)
    print(f"PyMuPDF rendered page 1: {pix.width}x{pix.height}")
    doc.close()
except Exception as e:
    print(f"PyMuPDF failed: {e}")
