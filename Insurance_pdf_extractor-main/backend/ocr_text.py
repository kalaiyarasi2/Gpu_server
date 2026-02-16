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
    
    def extract(self, dpi=300, language='eng', psm_mode=1, verbose=True):
        """
        Extract text using OCR (Tesseract).
        
        Args:
            dpi: Image resolution for conversion (higher = better quality, slower)
            language: OCR language (eng, fra, deu, etc.)
            psm_mode: Page segmentation mode (1=auto with OSD, 3=auto, 6=single block)
            verbose: Print progress information
            
        Returns:
            str: OCR-extracted text
        """
        if verbose:
            print(f"\n{'='*80}")
            print(f"OCR PDF EXTRACTION")
            print(f"{'='*80}")
            print(f"Input file: {self.pdf_path}")
            print(f"File size: {self.pdf_path.stat().st_size / 1024:.2f} KB")
            print(f"DPI: {dpi}")
            print(f"Language: {language}")
            print(f"PSM Mode: {psm_mode}\n")
        
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
                    print(f"OCR processing page {page_num}/{total_pages}...")
                
                # Add page separator
                page_header = f"\n{'='*80}\nPAGE {page_num}\n{'='*80}\n\n"
                extracted_text.append(page_header)
                
                # Configure Tesseract OCR
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
                    "extraction_method": "tesseract-ocr",
                    "confidence": 0.85 # Tesseract estimated confidence
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
    
    def extract_with_confidence(self, dpi=300, language='eng'):
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
