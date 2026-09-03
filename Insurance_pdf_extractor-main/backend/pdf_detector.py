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
    
    def is_scanned(self, text_threshold=50, pages_to_check=3):
        """
        Check if PDF is scanned (image-based) or contains extractable text.
        Also detects if the text layer is unreadable (garbage encoding).
        
        Args:
            text_threshold: Minimum characters to consider page as having text
            pages_to_check: Number of pages to sample for detection
            
        Returns:
            bool: True if PDF appears to be scanned or unreadable, False otherwise
        """
        import re
        try:
            reader = PdfReader(str(self.pdf_path))
            total_pages = len(reader.pages)
            
            # Check first few pages for text content
            pages_to_check = min(pages_to_check, total_pages)
            
            text_found = False
            for i in range(pages_to_check):
                text = reader.pages[i].extract_text()
                if not text:
                    continue
                
                text = text.strip()
                if len(text) < text_threshold:
                    continue

                # HEURISTIC: Check if text is actually readable
                
                # 1. Check for (cid:XX) tags which indicate broken font mapping (pdfminer style)
                cid_count = text.count("(cid:")
                
                # 2. Check for slash-coded characters (pypdf style)
                # These look like /114 /j107 etc.
                slash_digit_count = len(re.findall(r'/[0-9]', text))
                
                # 3. Check alphanumeric ratio
                alnum_text = re.sub(r'[^a-zA-Z0-9]', '', text)
                alnum_ratio = len(alnum_text) / len(text) if len(text) > 0 else 0
                
                # DETECTION CRITERIA:
                # - If ratio is very low (< 30%)
                # - OR if there's a high density of slash codes (> 5% of characters)
                # - OR if there's a high density of CID tags (> 10% of characters)
                
                is_garbage = False
                if alnum_ratio < 0.3:
                    print(f"   ⚠️ detected low alphanumeric ratio ({alnum_ratio:.2f}) on page {i+1}")
                    is_garbage = True
                elif slash_digit_count > len(text) * 0.05:
                    print(f"   ⚠️ detected high slash-code density on page {i+1}")
                    is_garbage = True
                elif cid_count * 7 > len(text) * 0.1:
                    print(f"   ⚠️ detected high CID density on page {i+1}")
                    is_garbage = True
                
                if not is_garbage:
                    text_found = True
                    break
            
            return not text_found  # Scanned if no substantial readable text found
            
        except Exception as e:
            print(f"Error checking PDF type: {e}")
            return True  # Default to OCR if unsure
    
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
            str: Recommended extraction method ('digital' or 'ocr')
        """
        print(f"\n{'='*80}")
        print(f"PDF ANALYSIS")
        print(f"{'='*80}")
        print(f"File: {self.pdf_path}")
        print(f"Size: {self.pdf_path.stat().st_size / 1024:.2f} KB")
        
        info = self.get_pdf_info()
        
        if info:
            print(f"Pages: {info['total_pages']}")
            print(f"Type: {'Scanned (Image-based)' if info['is_scanned'] else 'Digital (Text-based)'}")
            print(f"Recommended method: {'OCR Extraction' if info['is_scanned'] else 'Digital Extraction'}")
            print(f"{'='*80}\n")
            
            return 'ocr' if info['is_scanned'] else 'digital'
        
        return 'ocr'  # Default to OCR if analysis fails