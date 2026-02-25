#!/usr/bin/env python3
"""
PDF Type Detector
Determines whether a PDF is digital (text-based) or scanned (image-based)
"""

from pathlib import Path
import warnings
from cryptography.utils import CryptographyDeprecationWarning

# Suppress deprecation warning from pypdf/cryptography
warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)

from pypdf import PdfReader


class PDFDetector:
    """
    Analyzes PDFs to determine if they contain extractable text
    or require OCR processing.
    """
    
    def __init__(self, pdf_path):
        """
        Initialize the detector with a PDF file path.
        
        Args:
            pdf_path: Path to the PDF file
        """
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    def is_page_scanned(self, page_index, text_threshold=50):
        """
        Check if a specific page is scanned or contains unreadable text.
        """
        import re
        try:
            reader = PdfReader(str(self.pdf_path))
            if page_index >= len(reader.pages):
                return True
                
            page = reader.pages[page_index]
            text = page.extract_text()
            
            if not text or len(text.strip()) < text_threshold:
                return True
            
            text = text.strip()
            
            # HEURISTIC: Check if text is actually readable
            
            # 1. Check for (cid:XX) tags
            cid_count = text.count("(cid:")
            
            # 2. Check for slash-coded characters
            slash_digit_count = len(re.findall(r'/[0-9]', text))
            
            # 3. Check alphanumeric ratio
            alnum_text = re.sub(r'[^a-zA-Z0-9]', '', text)
            alnum_ratio = len(alnum_text) / len(text) if len(text) > 0 else 0
            
            is_garbage = False
            if alnum_ratio < 0.3:
                is_garbage = True
            elif slash_digit_count > len(text) * 0.05:
                is_garbage = True
            elif cid_count * 7 > len(text) * 0.1:
                is_garbage = True
                
            return is_garbage
            
        except Exception:
            return True

    def is_scanned(self, text_threshold=50, pages_to_check=3):
        """
        Check if PDF is scanned by sampling pages.
        """
        try:
            reader = PdfReader(str(self.pdf_path))
            total_pages = len(reader.pages)
            pages_to_check = min(pages_to_check, total_pages)
            
            for i in range(pages_to_check):
                if not self.is_page_scanned(i, text_threshold):
                    return False # Found a readable page
            
            return True # No readable pages found in sample
        except Exception:
            return True

    def has_form_fields(self):
        """
        Check if the PDF contains fillable form fields (AcroForms or XFA).
        """
        try:
            reader = PdfReader(str(self.pdf_path))
            # Check for AcroForm in root
            if "/AcroForm" in reader.trailer["/Root"]:
                return True
            
            # Check individual pages for fields
            for page in reader.pages:
                if "/Annots" in page:
                    for annot in page["/Annots"]:
                        obj = annot.get_object()
                        if obj.get("/Subtype") == "/Widget" and obj.get("/FT"):
                            return True
            return False
        except Exception:
            return False
    
    def get_pdf_info(self):
        """
        Get detailed information about the PDF.
        
        Returns:
            dict: PDF metadata and statistics
        """
        try:
            reader = PdfReader(str(self.pdf_path))
            
            info = {
                'path': str(self.pdf_path),
                'file_size_kb': self.pdf_path.stat().st_size / 1024,
                'total_pages': len(reader.pages),
                'is_scanned': self.is_scanned(),
                'has_form_fields': self.has_form_fields(),
                'metadata': reader.metadata if reader.metadata else {}
            }
            
            return info
            
        except Exception as e:
            print(f"Error getting PDF info: {e}")
            return None
    
    def analyze(self):
        """
        Perform full analysis and print results.
        
        Returns:
            str: Recommended extraction method ('digital', 'ocr', or 'hybrid')
        """
        print(f"\n{'='*80}")
        print(f"PDF ANALYSIS")
        print(f"{'='*80}")
        print(f"File: {self.pdf_path}")
        print(f"Size: {self.pdf_path.stat().st_size / 1024:.2f} KB")
        
        info = self.get_pdf_info()
        
        if info:
            print(f"Pages: {info['total_pages']}")
            print(f"Type: {'Scanned' if info['is_scanned'] else 'Digital'}")
            print(f"Form Fields: {'Detected' if info['has_form_fields'] else 'Not Found'}")
            
            method = 'digital'
            if info['is_scanned']:
                method = 'ocr'
            elif info['has_form_fields']:
                method = 'hybrid' # Use digital + form extraction
            
            print(f"Recommended method: {method.upper()}")
            print(f"{'='*80}\n")
            
            return method
        
        return 'ocr'