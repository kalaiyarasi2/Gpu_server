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

from text_quality_verifier import TextQualityVerifier

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
        verifier = TextQualityVerifier()
        
        try:
            if verbose:
                print("Converting PDF to images...")
            
            # Initial high-DPI render (default 600)
            images = convert_from_path(
                str(self.pdf_path),
                dpi=dpi,
                fmt='jpeg'
            )
            
            total_pages = len(images)
            pages_metadata = [None] * total_pages  # Pre-sized to preserve order

            if verbose:
                print(f"Processing {total_pages} pages with OCR (parallel - layered fallback)...\n")

            def process_page(page_num, image):
                """OCR a single page with layered Tesseract → Vision fallback."""
                page_header = f"\n{'='*80}\nPAGE {page_num}\n{'='*80}\n\n"
                custom_config = f'--oem 3 --psm {psm_mode}'
                _verifier = TextQualityVerifier()

                if verbose:
                    print(f"OCR processing page {page_num}/{total_pages} (DPI {dpi})...")

                # 1) First attempt: high-DPI Tesseract
                text_hi = pytesseract.image_to_string(image, config=custom_config, lang=language)
                page_text_hi = text_hi if text_hi.strip() else "[No text detected on this page]\n"

                if _verifier.analyze_quality(page_text_hi).get('metrics', {}).get('reversed_marker_count', 0) >= 2:
                    if verbose: print(f"   ⚠️ Detected reversed text encoding at 600 DPI. Correcting (page {page_num})...")
                    page_text_hi = self._reverse_text_block(page_text_hi)

                quality_hi = _verifier.page_quality(page_text_hi)
                score_hi = quality_hi.get("score", 0.0)
                rec_hi = quality_hi.get("recommendation", "ok")

                final_text = page_text_hi
                extraction_method = "tesseract-ocr-600dpi"
                final_score = score_hi

                # 2) Second attempt: 300 DPI Tesseract if needed
                if rec_hi in ("dpi_fallback", "full_vision"):
                    if verbose:
                        print(
                            f"   ↪ High-DPI OCR quality low (score {score_hi:.3f}, rec '{rec_hi}'). "
                            f"Retrying Tesseract at 300 DPI (page {page_num})..."
                        )
                    try:
                        mid_images = convert_from_path(
                            str(self.pdf_path), dpi=300, fmt='jpeg',
                            first_page=page_num, last_page=page_num
                        )
                        if mid_images:
                            mid_image = mid_images[0]
                            text_mid = pytesseract.image_to_string(mid_image, config=custom_config, lang=language)
                            page_text_mid = text_mid if text_mid.strip() else "[No text detected on this page]\n"

                            if _verifier.analyze_quality(page_text_mid).get('metrics', {}).get('reversed_marker_count', 0) >= 2:
                                if verbose: print(f"   ⚠️ Detected reversed text encoding at 300 DPI. Correcting (page {page_num})...")
                                page_text_mid = self._reverse_text_block(page_text_mid)

                            quality_mid = _verifier.page_quality(page_text_mid)
                            score_mid = quality_mid.get("score", 0.0)
                            rec_mid = quality_mid.get("recommendation", "ok")

                            if score_mid > final_score or rec_mid == "ok":
                                if verbose:
                                    print(f"   ✓ 300 DPI OCR improved quality (score {score_mid:.3f}, rec '{rec_mid}') on page {page_num}.")
                                final_text = page_text_mid
                                extraction_method = "tesseract-ocr-300dpi"
                                final_score = score_mid
                                quality_hi = quality_mid
                                rec_hi = rec_mid
                    except Exception as e:
                        print(f"   ⚠️ 300 DPI fallback failed on page {page_num}: {e}")

                # 3) Final attempt: Vision OCR if still low quality
                if rec_hi in ("dpi_fallback", "full_vision") and self.client is not None:
                    if verbose:
                        print(
                            f"   ↪ OCR still low quality after retries "
                            f"(score {final_score:.3f}). Using Vision on page {page_num}..."
                        )
                    try:
                        vis_images = convert_from_path(
                            str(self.pdf_path), dpi=300, fmt='jpeg',
                            first_page=page_num, last_page=page_num
                        )
                        if vis_images:
                            vis_image = vis_images[0]
                            vis_text, vis_conf = self._extract_page_with_vision(vis_image)

                            if _verifier.analyze_quality(vis_text).get('metrics', {}).get('reversed_marker_count', 0) >= 2:
                                if verbose: print(f"   ⚠️ Detected reversed text encoding in Vision output. Correcting (page {page_num})...")
                                vis_text = self._reverse_text_block(vis_text)

                            if vis_text.strip():
                                final_text = vis_text
                                extraction_method = "gpt-4-vision-fallback"
                                final_score = max(final_score, vis_conf)
                                rec_hi = "ok"
                    except Exception as e:
                        print(f"   ⚠️ Vision fallback failed on page {page_num}: {e}")

                page_metadata = {
                    "page_number": page_num,
                    "text": page_header + final_text,
                    "is_scanned": True,
                    "extraction_method": extraction_method,
                    "confidence": final_score,
                    "quality_metrics": quality_hi.get("analysis", {}).get("metrics", {})
                }
                return page_num, page_header, final_text, page_metadata

            # Process pages in parallel batches (max 8 workers to avoid overloading CPU/RAM)
            from concurrent.futures import ThreadPoolExecutor, as_completed
            print(f"🚀 Launching Parallel OCR Pool for {total_pages} pages (max 8 workers)...")
            with ThreadPoolExecutor(max_workers=8) as executor:
                future_to_page = {
                    executor.submit(process_page, pg_num, img): pg_num
                    for pg_num, img in enumerate(images, 1)
                }
                for future in as_completed(future_to_page):
                    pg_num = future_to_page[future]
                    try:
                        page_num, page_header, final_text, page_metadata = future.result()
                        # Store in pre-allocated slot (0-indexed: page 1 → slot 0)
                        pages_metadata[page_num - 1] = page_metadata
                        if verbose:
                            print(f"   ✅ Finished OCR for page {page_num}/{total_pages} ({page_metadata['extraction_method']})")
                    except Exception as exc:
                        print(f"   ⚠️ OCR failed for page {pg_num}: {exc}")
                        pages_metadata[pg_num - 1] = {
                            "page_number": pg_num,
                            "text": f"\n{'='*80}\nPAGE {pg_num}\n{'='*80}\n\n[OCR FAILED]\n\n",
                            "is_scanned": True,
                            "extraction_method": "failed",
                            "confidence": 0.0,
                            "quality_metrics": {}
                        }

            # Reassemble text in strict page order (1, 2, 3, ... N)
            for pm in pages_metadata:
                if pm:
                    extracted_text.append(pm["text"])
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
            
            verifier = TextQualityVerifier()
            page_text, conf = self._extract_page_with_vision(image)
            
            if i == 1:
                is_reversed = verifier.analyze_quality(page_text).get('metrics', {}).get('reversed_marker_count', 0) >= 2
                if is_reversed:
                    print(f"⚠️ Detected reversed text encoding in Vision OCR. Applying correction...")

            if is_reversed:
                page_text = self._reverse_text_block(page_text)

            header = f"\n{'='*80}\nPAGE {i}\n{'='*80}\n\n"
            full_text.append(header + page_text + "\n\n")
            
            metadata.append({
                "page_number": i,
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
            "Extract ALL text from this document page.\n"
            "PRESERVE the EXACT layout including columns, tables, and spacing.\n"
            "Return ONLY the extracted text."
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

    def _check_if_reversed(self, text: str) -> bool:
        """Detect if text is likely reversed using centralized verifier."""
        if not text: return False
        try:
            from text_quality_verifier import TextQualityVerifier
            verifier = TextQualityVerifier()
            return verifier.analyze_quality(text).get('metrics', {}).get('reversed_marker_count', 0) >= 2
        except:
            return False

    def _reverse_text_block(self, text: str) -> str:
        """Reverse each line of text."""
        if not text: return ""
        lines = text.split('\n')
        reversed_lines = [line[::-1] for line in lines]
        return '\n'.join(reversed_lines)

