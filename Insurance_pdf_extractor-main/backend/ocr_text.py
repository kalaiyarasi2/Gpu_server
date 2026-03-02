#!/usr/bin/env python3
"""
OCR PDF Text Extractor
Extracts text from scanned/image-based PDFs using Tesseract OCR
Converts PDF pages to images and performs optical character recognition
"""

from pathlib import Path
import os
import base64
from io import BytesIO
import pytesseract
from pdf2image import convert_from_path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


class OCRPDFExtractor:
    """
    OCR-based text extraction for scanned (image-based) PDFs.
    Converts pages to images and uses Tesseract for text recognition.
    """
    
    def __init__(self, pdf_path, api_key=None):
        """
        Initialize the extractor with a PDF file path.
        
        Args:
            pdf_path: Path to the PDF file
            api_key: OpenAI API key for Vision OCR (optional)
        """
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None
        self.output_text = ""
    
    def extract(self, dpi=600, language='eng', psm_mode=1, verbose=True, engine='tesseract', **kwargs):
        """
        Extract text using OCR (Tesseract or GPT-4 Vision).
        
        Args:
            dpi: Image resolution for conversion (higher = better quality, slower)
            language: OCR language (eng, fra, deu, etc.)
            psm_mode: Page segmentation mode (1=auto with OSD, 3=auto, 6=single block)
            verbose: Print progress information
            engine: OCR engine to use ('tesseract' or 'vision')
            
        Returns:
            str: OCR-extracted text
        """
        if verbose:
            print(f"\n{'='*80}")
            print(f"OCR PDF EXTRACTION ({engine.upper()})")
            print(f"{'='*80}")
            print(f"Input file: {self.pdf_path}")
            print(f"File size: {self.pdf_path.stat().st_size / 1024:.2f} KB")
            print(f"DPI: {dpi}")
            if engine == 'tesseract':
                print(f"Language: {language}")
                print(f"PSM Mode: {psm_mode}")
            print()
        
        if engine == 'vision':
            return self._extract_with_vision(dpi=dpi, verbose=verbose)
        
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

    def _extract_with_vision(self, dpi=300, verbose=True):
        """
        Extract text using GPT-4 Vision for near-perfect layout and word accuracy.
        """
        if not self.client:
            raise ValueError("OpenAI API key is required for Vision OCR. Set OPENAI_API_KEY environment variable.")
            
        print("Converting PDF to images for Vision OCR...")
        images = convert_from_path(str(self.pdf_path), dpi=dpi)
        
        full_text = []
        metadata = []
        
        for i, image in enumerate(images, 1):
            if verbose:
                print(f"Vision processing page {i}/{len(images)}...")
            
            # Convert to base64
            buffered = BytesIO()
            image.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode()
            
            prompt = """Extract ALL text from this document. 
            PRESERVE the EXACT layout including columns, tables, and spacing.
            Return ONLY the extracted text followed by [PAGE_END]."""
            
            try:
                response = self.client.chat.completions.create(
                    model="gpt-4.1",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                        ]
                    }],
                    max_tokens=4000
                )
                
                page_text = response.choices[0].message.content
                
                header = f"\n{'='*80}\nPAGE {i}\n{'='*80}\n\n"
                full_text.append(header + page_text + "\n\n")
                
                metadata.append({
                    "page_number": i,
                    "text": header + page_text,
                    "is_scanned": True,
                    "extraction_method": "gpt-4.1-vision",
                    "confidence": 0.99
                })
            except Exception as e:
                print(f"Error on page {i}: {e}")
                full_text.append(f"\n[ERROR ON PAGE {i}: {e}]\n")
        
        self.output_text = "".join(full_text)
        return self.output_text, metadata

