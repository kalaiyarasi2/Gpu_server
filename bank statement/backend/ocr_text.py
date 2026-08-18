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
from openai import OpenAI
from dotenv import load_dotenv

from PIL import Image
import importlib
import importlib.util

# Import shared utilities from ocr_utils
utils_path = Path(__file__).parent / "ocr_utils.py"
utils_spec = importlib.util.spec_from_file_location("bank_statement_ocr_utils", str(utils_path))
utils_mod = importlib.util.module_from_spec(utils_spec)
utils_spec.loader.exec_module(utils_mod)
robust_convert_pdf_to_images = utils_mod.robust_convert_pdf_to_images

# Robust dynamic import of BankTextQualityVerifier to avoid collisions
verifier_path = Path(__file__).parent / "text_quality_verifier.py"
spec = importlib.util.spec_from_file_location("bank_statement_verifier_local_ocr", str(verifier_path))
local_tqv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_tqv)
BankTextQualityVerifier = local_tqv.BankTextQualityVerifier


# Load environment variables
local_env = Path(__file__).parent / ".env"
root_env = Path(__file__).parent.parent.parent / ".env"

if local_env.exists():
    load_dotenv(dotenv_path=local_env)
elif root_env.exists():
    load_dotenv(dotenv_path=root_env)
else:
    load_dotenv() 

# Configure Tesseract path if provided in environment
TESSERACT_PATH = os.getenv("TESSERACT_PATH")
if TESSERACT_PATH:
    if os.path.isdir(TESSERACT_PATH):
        tess_exe = os.path.join(TESSERACT_PATH, "tesseract.exe")
        if os.path.exists(tess_exe):
            pytesseract.pytesseract.tesseract_cmd = tess_exe
    elif os.path.exists(TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

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
    
    def extract(self, dpi=600, language='eng', psm_mode=1, verbose=True, engine='tesseract', first_page=None, last_page=None, force_vision=False, **kwargs):
        """
        Extract text using OCR (Tesseract or GPT-4 Vision).
        
        Args:
            dpi: Image resolution for conversion (higher = better quality, slower)
            language: OCR language (eng, fra, deu, etc.)
            psm_mode: Page segmentation mode (1=auto with OSD, 3=auto, 6=single block)
            verbose: Print progress information
            engine: OCR engine to use ('tesseract' or 'vision')
            first_page: First page to extract (1-indexed)
            last_page: Last page to extract (1-indexed)
            force_vision: If True, bypass Tesseract and use GPT-4 Vision directly.
            
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
        
        if engine == 'vision' or force_vision:
            if verbose and force_vision:
                print("   ℹ️ Forced Vision mode enabled. Bypassing Tesseract layers...")
            return self._extract_with_vision(dpi=dpi, verbose=verbose, first_page=first_page, last_page=last_page)
        
        extracted_text = []
        verifier = BankTextQualityVerifier()
        
        try:
            if verbose:
                print("Converting PDF to images...")
            
            # Use robust conversion helper with page range
            images = robust_convert_pdf_to_images(self.pdf_path, dpi=dpi, first_page=first_page, last_page=last_page)
            
            total_pages = len(images)
            pages_metadata = []
            
            if verbose:
                print(f"Processing {total_pages} pages with OCR (layered fallback)...\n")
            
            for i, image in enumerate(images):
                # Calculate the actual page number
                page_num = (first_page + i) if first_page else (i + 1)
                
                if verbose:
                    print(f"OCR processing page {page_num} (DPI {dpi})...")
                
                # Add page separator
                page_header = f"\n{'='*80}\nPAGE {page_num}\n{'='*80}\n\n"
                extracted_text.append(page_header)
                
                # 0) If force_vision is set, we bypass Tesseract
                if force_vision:
                    if verbose:
                        print(f"   ℹ️ Page {page_num} marked for high-fidelity Vision. Skipping Tesseract.")
                    rec_hi = "full_vision"
                    final_score = 0.0
                    page_text_hi = "[Forced Vision]"
                    final_text = page_text_hi
                    extraction_method = "forced-vision-preparation"
                else:
                    # 1) First attempt: high-DPI Tesseract
                    custom_config = f'--oem 3 --psm {psm_mode}'
                    text_hi = pytesseract.image_to_string(
                        image,
                        config=custom_config,
                        lang=language
                    )
                    
                    # If Tesseract returns totally empty, force a low quality score to trigger fallback
                    if not text_hi.strip():
                        page_text_hi = "[No text detected on this page]\n"
                        score_hi = 0.0
                        rec_hi = "full_vision"
                    else:
                        page_text_hi = text_hi
                        quality_hi = verifier.page_quality(page_text_hi)
                        score_hi = quality_hi.get("score", 0.0)
                        rec_hi = quality_hi.get("recommendation", "ok")
                    
                    final_score = score_hi
                    final_text = page_text_hi
                    extraction_method = "tesseract-ocr-600dpi"
                
                # 2) Second attempt: mid-DPI Tesseract if needed
                if rec_hi in ("dpi_fallback", "full_vision") and not force_vision:
                    if verbose:
                        print(
                            f"   ↪ High-DPI OCR quality low (score {score_hi:.3f}, rec '{rec_hi}'). "
                            f"Retrying Tesseract at 300 DPI..."
                        )
                    try:
                        mid_images = robust_convert_pdf_to_images(
                            self.pdf_path,
                            dpi=300,
                            first_page=page_num,
                            last_page=page_num
                        )
                        if mid_images:
                            mid_image = mid_images[0]
                            text_mid = pytesseract.image_to_string(
                                mid_image,
                                config=custom_config,
                                lang=language
                            )
                            page_text_mid = text_mid if text_mid.strip() else "[No text detected on this page]\n"
                            quality_mid = verifier.page_quality(page_text_mid)
                            score_mid = quality_mid.get("score", 0.0)
                            rec_mid = quality_mid.get("recommendation", "ok")
                            
                            # Prefer the mid-DPI result if it scores significantly better or is 'ok'
                            if score_mid > final_score or rec_mid == "ok":
                                if verbose:
                                    print(
                                        f"   ✓ 300 DPI OCR update "
                                        f"(score {score_mid:.3f}, rec '{rec_mid}')."
                                    )
                                final_text = page_text_mid
                                extraction_method = "tesseract-ocr-300dpi"
                                final_score = score_mid
                                quality_hi = quality_mid
                                rec_hi = rec_mid
                    except Exception as e:
                        print(f"   ⚠️ 300 DPI fallback failed on page {page_num}: {e}")
                
                # 3) Final attempt: Vision OCR if still low quality and Vision is available
                if rec_hi == "full_vision" and self.client is not None:
                    if verbose:
                        print(
                            f"   ↪ OCR still low quality after retries "
                            f"(score {final_score:.3f}). Using Vision on page {page_num}..."
                        )
                    try:
                        # Render just this page for Vision at moderate DPI
                        vis_images = robust_convert_pdf_to_images(
                            self.pdf_path,
                            dpi=300,
                            first_page=page_num,
                            last_page=page_num
                        )
                        if vis_images:
                            vis_image = vis_images[0]
                            vis_text, vis_conf = self._extract_page_with_vision(vis_image)
                            if vis_text.strip():
                                final_text = vis_text
                                extraction_method = "gpt-4-vision-fallback"
                                final_score = max(final_score, vis_conf)
                    except Exception as e:
                        print(f"   ⚠️ Vision fallback failed on page {page_num}: {e}")
                
                extracted_text.append(final_text)
                
                pages_metadata.append({
                    "page_number": page_num,
                    "text": page_header + final_text,
                    "is_scanned": True,
                    "extraction_method": extraction_method,
                    "confidence": final_score,
                    "quality_metrics": verifier.page_quality(final_text).get("analysis", {}).get("metrics", {})
                })
                
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
        
        images = robust_convert_pdf_to_images(self.pdf_path, dpi=dpi)
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

    def _extract_with_vision(self, dpi=300, verbose=True, first_page=None, last_page=None):
        """
        Extract text using GPT-4 Vision for near-perfect layout and word accuracy.
        """
        if not self.client:
            raise ValueError("OpenAI API key is required for Vision OCR. Set OPENAI_API_KEY environment variable.")
            
        print("Converting PDF to images for Vision OCR...")
        images = robust_convert_pdf_to_images(self.pdf_path, dpi=dpi, first_page=first_page, last_page=last_page)
        
        full_text = []
        metadata = []
        
        for i, image in enumerate(images):
            # Calculate actual page number
            page_num = (first_page + i) if first_page else (i + 1)
            
            if verbose:
                print(f"Vision processing page {page_num}...")
            
            page_text, conf = self._extract_page_with_vision(image)
            
            header = f"\n{'='*80}\nPAGE {page_num}\n{'='*80}\n\n"
            full_text.append(header + page_text + "\n\n")
            
            metadata.append({
                "page_number": page_num,
                "text": header + page_text,
                "is_scanned": True,
                "extraction_method": "gpt-4-vision",
                "confidence": conf
            })
        
        self.output_text = "".join(full_text)
        return self.output_text, metadata

    def _extract_page_with_vision(self, image, verbose=False):
        """
        Vision OCR for a single page image. Returns (text, confidence).
        """
        if not self.client:
            raise ValueError("OpenAI API key is required for Vision OCR. Set OPENAI_API_KEY.")

        buffered = BytesIO()
        image.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()

        prompt = (
            "Extract ALL text from this bank statement page.\n"
            "PRESERVE the EXACT layout including columns, tables, and spacing.\n"
            "CRITICAL: Bank statements often have a 'Balance' or 'Running Balance' column on the far right edge.\n"
            "You MUST capture this column completely. Do NOT truncate or skip digits near the right margin.\n"
            "If a line has multiple currency values (e.g. '95.96    1,582.10'), ensure both are captured and separated by spaces.\n"
            "Return ONLY the extracted text, no explanations."
        )

        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}
                ]
            }],
            max_tokens=4000,
            temperature=0.0
        )

        page_text = response.choices[0].message.content or ""
        confidence = 0.99 if page_text.strip() else 0.0
        return page_text, confidence

