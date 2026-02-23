#!/usr/bin/env python3
"""
OCR PDF Text Extractor
Extracts text from scanned/image-based PDFs using Tesseract OCR
Converts PDF pages to images and performs optical character recognition
"""

from pathlib import Path
import pytesseract
from pdf2image import convert_from_path


class OCRPDFExtractor:
    """
    OCR-based text extraction for scanned (image-based) PDFs.
    Converts pages to images and uses Tesseract for text recognition.
    """
    
    def __init__(self, pdf_path):
        """
        Initialize the extractor with a PDF file path.
        
        Args:
            pdf_path: Path to the PDF file
        """
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
        self.output_text = ""
    
    def extract(self, dpi=600, language='eng', psm_mode=1, verbose=True, enhancements=None):
        """
        Extract text using OCR (Tesseract).
        
        Args:
            dpi: Image resolution for conversion (higher = better quality, slower)
            language: OCR language (eng, fra, deu, etc.)
            psm_mode: Page segmentation mode (1=auto with OSD, 3=auto, 6=single block)
            verbose: Print progress information
            enhancements: Dict of image enhancement factors (contrast, sharpness, etc.)
            
        Returns:
            str: OCR-extracted text
        """
        from PIL import ImageEnhance, ImageOps, ImageFilter
        
        if enhancements is None:
            enhancements = {
                'contrast': 1.6,
                'sharpness': 2.2,
                'grayscale': True,
                'binarize': True,
                'edge_enhance': True
            }

        if verbose:
            print(f"\n{'='*80}")
            print(f"OCR PDF EXTRACTION (ENHANCED V2)")
            print(f"{'='*80}")
            print(f"Input file: {self.pdf_path}")
            print(f"File size: {self.pdf_path.stat().st_size / 1024:.2f} KB")
            print(f"DPI: {dpi}")
            print(f"Language: {language}")
            print(f"PSM Mode: {psm_mode}")
            print(f"Enhancements: {enhancements}\n")
        
        extracted_text = []
        
        try:
            if verbose:
                print("Converting PDF to images...")
            
            # Convert PDF pages to images
            images = convert_from_path(
                str(self.pdf_path),
                dpi=dpi,
                fmt='jpeg'
            )
            
            total_pages = len(images)
            pages_metadata = []
            
            if verbose:
                print(f"Processing {total_pages} pages with OCR...\n")
            
            for page_num, image in enumerate(images, 1):
                if verbose:
                    print(f"Enhancing and OCR processing page {page_num}/{total_pages}...")
                
                # --- IMAGE PREPROCESSING ---
                if enhancements.get('grayscale', True):
                    image = ImageOps.grayscale(image)
                
                if enhancements.get('contrast', 1.0) != 1.0:
                    enhancer = ImageEnhance.Contrast(image)
                    image = enhancer.enhance(enhancements['contrast'])
                
                if enhancements.get('sharpness', 1.0) != 1.0:
                    enhancer = ImageEnhance.Sharpness(image)
                    image = enhancer.enhance(enhancements['sharpness'])
                
                if enhancements.get('edge_enhance', False):
                    image = image.filter(ImageFilter.EDGE_ENHANCE_MORE)
                
                # Morphological Cleaning (Removes small dots/noise)
                if enhancements.get('morphology', False):
                    import numpy as np
                    import cv2
                    img_np = np.array(image)
                    kernel = np.ones((1, 1), np.uint8)
                    img_np = cv2.dilate(img_np, kernel, iterations=1)
                    img_np = cv2.erode(img_np, kernel, iterations=1)
                    from PIL import Image
                    image = Image.fromarray(img_np)

                # Binarization with adaptive-like fixed threshold for forms
                if enhancements.get('binarize', False):
                    threshold = enhancements.get('threshold', 200) # Lighter threshold to catch faint marks
                    image = image.point(lambda p: p > threshold and 255)
                # ---------------------------

                # Add page separator
                page_header = f"\n{'='*80}\nPAGE {page_num}\n{'='*80}\n\n"
                extracted_text.append(page_header)
                
                # Configure Tesseract OCR
                # Mode 1 is good for auto-layout, but mode 6 can sometimes be better for tables
                custom_config = f'--oem 3 --psm {psm_mode}'
                
                # Perform OCR
                text = pytesseract.image_to_string(
                    image,
                    config=custom_config,
                    lang=language
                )
                
                page_text = text if text.strip() else "[No text detected on this page]\n"
                extracted_text.append(page_text)
                
                # Collect metadata for pipeline
                pages_metadata.append({
                    "page_number": page_num,
                    "text": page_header + page_text,
                    "is_scanned": True,
                    "extraction_method": f"tesseract-ocr-enhanced-dpi{dpi}",
                    "confidence": 0.85 
                })
                
                # Add spacing between pages
                extracted_text.append("\n\n")
            
            self.output_text = "".join(extracted_text)
            
            if verbose:
                print(f"\n{'='*80}")
                print(f"EXTRACTION COMPLETE")
                print(f"{'='*80}")
                print(f"Characters extracted: {len(self.output_text):,}")
                print(f"Lines: {self.output_text.count(chr(10)):,}\n")
            
            return self.output_text, pages_metadata
            
        except Exception as e:
            print(f"OCR Error: {e}")
            raise
    
    def save_to_file(self, output_path=None):
        """
        Save extracted text to a file.
        
        Args:
            output_path: Path to output file (optional)
            
        Returns:
            Path: Path to the saved file
        """
        if not self.output_text:
            raise ValueError("No text has been extracted yet. Call extract() first.")
        
        # Generate output filename if not provided
        if output_path is None:
            output_path = self.pdf_path.with_suffix('.txt')
        else:
            output_path = Path(output_path)
        
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write to file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(self.output_text)
        
        print(f"Output saved to: {output_path}\n")
        
        return output_path
    
    def extract_with_confidence(self, dpi=600, language='eng'):
        """
        Extract text with confidence scores for each word.
        
        Args:
            dpi: Image resolution for conversion
            language: OCR language
            
        Returns:
            list: List of dicts with text and confidence for each page
        """
        print("Converting PDF to images for detailed OCR...")
        
        images = convert_from_path(str(self.pdf_path), dpi=dpi, fmt='jpeg')
        results = []
        
        for page_num, image in enumerate(images, 1):
            print(f"Processing page {page_num}/{len(images)}...")
            
            # Get detailed OCR data
            data = pytesseract.image_to_data(
                image,
                lang=language,
                output_type=pytesseract.Output.DICT
            )
            
            # Extract words with confidence
            page_results = {
                'page_num': page_num,
                'words': []
            }
            
            for i, word in enumerate(data['text']):
                if word.strip():  # Ignore empty strings
                    page_results['words'].append({
                        'text': word,
                        'confidence': data['conf'][i]
                    })
            
            results.append(page_results)
        
        return results


if __name__ == "__main__":
    import sys
    import os
    from datetime import datetime
    
    if len(sys.argv) < 2:
        print("Usage: python ocr_text.py <pdf_path> [output_path]")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)
        
    print(f"🚀 Starting OCR extraction for: {pdf_path}")
    extractor = OCRPDFExtractor(pdf_path)
    text, meta = extractor.extract(verbose=True)
    
    if output_path:
        saved_path = extractor.save_to_file(output_path)
    else:
        saved_path = extractor.save_to_file()
        
    print(f"✅ OCR extraction complete. Output saved to: {saved_path}")
