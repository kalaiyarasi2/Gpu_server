import os
import fitz  # PyMuPDF
from PIL import Image
import pytesseract
from typing import List, Dict, Tuple

def extract_text_from_pdf(pdf_path: str) -> Tuple[str, List[Dict]]:
    """
    Extract text from PDF using PyMuPDF + Tesseract
    Returns: (combined_text, pages_metadata)
    """
    print(f"\n{'='*60}")
    print(f"📄 Extracting text from PDF using PyMuPDF + Tesseract...")
    print(f"{'='*60}")
    
    doc = fitz.open(pdf_path)
    all_text = ""
    pages_metadata = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_info = {
            "page_number": page_num + 1,
            "text": "",
            "has_images": False,
            "is_scanned": False,
            "extraction_method": "pymupdf",
            "confidence": 0.95
        }
        
        print(f"\n📄 Page {page_num + 1}/{len(doc)}")
        
        # Step 1: Try direct text extraction with PyMuPDF
        try:
            text = page.get_text("text")
            if text and text.strip():
                page_info["text"] = text
                page_info["extraction_method"] = "pymupdf"
                print(f"  ✓ Direct extraction: {len(text)} characters")
            else:
                # Try table/structured extraction
                text = page.get_text("blocks")
                if text:
                    extracted_text = "\n".join([block[4] for block in text if isinstance(block, list) and len(block) > 4])
                    if extracted_text.strip():
                        page_info["text"] = extracted_text
                        page_info["extraction_method"] = "pymupdf_blocks"
                        print(f"  ✓ Block extraction: {len(extracted_text)} characters")
        except Exception as e:
            print(f"  ⚠ PyMuPDF extraction error: {e}")
        
        # Step 2: If PyMuPDF failed, try OCR with Tesseract
        if not page_info["text"] or len(page_info["text"]) < 50:
            print(f"  📸 Attempting Tesseract OCR...")
            try:
                # Render page to image
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)  # 2x zoom for better OCR
                img_data = pix.tobytes("ppm")
                img = Image.frombytes("RGB", [pix.width, pix.height], img_data)
                
                # Run Tesseract OCR
                ocr_text = pytesseract.image_to_string(img)
                if ocr_text and ocr_text.strip():
                    page_info["text"] = ocr_text
                    page_info["extraction_method"] = "tesseract"
                    page_info["is_scanned"] = True
                    page_info["confidence"] = 0.85  # OCR has lower confidence
                    print(f"  ✓ Tesseract OCR: {len(ocr_text)} characters")
            except Exception as e:
                print(f"  ⚠ Tesseract OCR error: {e}")
                page_info["is_scanned"] = True
        
        # Step 3: Check for blank pages
        if not page_info["text"] or len(page_info["text"]) < 10:
            page_info["text"] = "[BLANK PAGE - No extractable content]"
            page_info["confidence"] = 0.0
            print(f"  ℹ️  Blank page detected")
        
        all_text += f"\n{'='*60}\nPAGE {page_num + 1}\n{'='*60}\n\n"
        all_text += page_info["text"]
        all_text += "\n\n"
        
        pages_metadata.append(page_info)
    
    doc.close()
    
    print(f"\n✓ Extracted {len(pages_metadata)} pages")
    print(f"  Total text: {len(all_text)} characters")
    
    return all_text, pages_metadata
