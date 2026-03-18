import os
from pathlib import Path
from PIL import Image
from pdf2image import convert_from_path
import fitz  # PyMuPDF

def robust_convert_pdf_to_images(pdf_path, dpi=600, first_page=None, last_page=None):
    """
    Robustly convert PDF to images, falling back to PyMuPDF if Poppler (pdf2image) fails.
    Shared utility to break circular dependencies between ocr_text and vision_recovery.
    """
    try:
        # 1) Try pdf2image (Standard)
        images = convert_from_path(
            pdf_path,
            dpi=dpi,
            first_page=first_page,
            last_page=last_page,
            fmt='jpeg'
        )
        return images
    except Exception as e:
        print(f"   ⚠️ pdf2image failed ({e}). Falling back to PyMuPDF (fitz)...")
        try:
            doc = fitz.open(str(pdf_path))
            images = []
            
            # Adjust range for fitz (0-indexed)
            start = (first_page - 1) if first_page else 0
            end = last_page if last_page else len(doc)
            
            for i in range(start, end):
                page = doc.load_page(i)
                # Calculate zoom for requested DPI (default fitz is 72)
                zoom = dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                images.append(img)
            
            doc.close()
            return images
        except Exception as fe:
            print(f"   ❌ PyMuPDF fallback also failed: {fe}")
            raise
