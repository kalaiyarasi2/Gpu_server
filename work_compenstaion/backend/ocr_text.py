import base64
import os
from pathlib import Path
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
    Standardized v3: Native -> Tesseract (600 DPI) -> Tesseract (300 DPI) -> GPT-4 Vision.
    """
    
    def __init__(self, pdf_path, api_key=None):
        """
        Initialize the extractor with a PDF file path.
        """
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None
        self.output_text = ""
    
    def extract(self, dpi=600, language='eng', psm_mode=1, verbose=True, engine='tesseract', **kwargs):
        """
        Extract text using standardized layered fallback.
        """
        if verbose:
            print(f"\n{'='*80}")
            print(f"OCR PDF EXTRACTION ({engine.upper()}) - STANDARDIZED FLOW")
            print(f"{'='*80}")
            print(f"Input file: {self.pdf_path}")
            print(f"Engine: {engine}")
            print(f"Base DPI: {dpi}")
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
            pages_metadata = []
            
            if verbose:
                print(f"Processing {total_pages} pages with layered OCR fallback...\n")
            
            for page_num, image in enumerate(images, 1):
                if verbose:
                    print(f"OCR processing page {page_num}/{total_pages} (DPI {dpi})...")
                
                page_header = f"\n{'='*80}\nPAGE {page_num}\n{'='*80}\n\n"
                extracted_text.append(page_header)
                
                # 1) First attempt: high-DPI Tesseract
                custom_config = f'--oem 3 --psm {psm_mode}'
                text_hi = pytesseract.image_to_string(
                    image,
                    config=custom_config,
                    lang=language
                )
                page_text_hi = text_hi if text_hi.strip() else "[No text detected on this page]\n"
                quality_hi = verifier.page_quality(page_text_hi)
                score_hi = quality_hi.get("score", 0.0)
                rec_hi = quality_hi.get("recommendation", "ok")
                
                final_text = page_text_hi
                extraction_method = f"tesseract-ocr-{dpi}dpi"
                final_score = score_hi
                
                # 2) Second attempt: 300-DPI Tesseract if needed
                if rec_hi in ("dpi_fallback", "full_vision"):
                    if verbose:
                        print(
                            f"   ↪ High-DPI OCR quality low (score {score_hi:.3f}, rec '{rec_hi}'). "
                            f"Retrying Tesseract at 300 DPI..."
                        )
                    try:
                        low_images = convert_from_path(
                            str(self.pdf_path),
                            dpi=300,
                            fmt='jpeg',
                            first_page=page_num,
                            last_page=page_num
                        )
                        if low_images:
                            low_image = low_images[0]
                            text_lo = pytesseract.image_to_string(
                                low_image,
                                config=custom_config,
                                lang=language
                            )
                            page_text_lo = text_lo if text_lo.strip() else "[No text detected on this page]\n"
                            quality_lo = verifier.page_quality(page_text_lo)
                            score_lo = quality_lo.get("score", 0.0)
                            rec_lo = quality_lo.get("recommendation", "ok")
                            
                            if score_lo > final_score or rec_lo == "ok":
                                if verbose:
                                    print(f"   ✓ 300 DPI OCR improved quality (score {score_lo:.3f}).")
                                final_text = page_text_lo
                                extraction_method = "tesseract-ocr-300dpi"
                                final_score = score_lo
                                rec_hi = rec_lo
                    except Exception as e:
                        print(f"   ⚠️ 300 DPI fallback failed on page {page_num}: {e}")
                
                # 3) Final attempt: GPT-4 Vision if still low quality
                if rec_hi == "full_vision" and self.client is not None:
                    if verbose:
                        print(
                            f"   ↪ OCR still low quality (score {final_score:.3f}). "
                            f"Using GPT-4 Vision on page {page_num}..."
                        )
                    try:
                        vis_images = convert_from_path(
                            str(self.pdf_path),
                            dpi=300,
                            fmt='jpeg',
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
                    "confidence": final_score
                })
                
                extracted_text.append("\n\n")
            
            self.output_text = "".join(extracted_text)
            return self.output_text, pages_metadata
            
        except Exception as e:
            print(f"OCR Error: {e}")
            raise

    def _extract_with_vision(self, dpi=300, verbose=True):
        """Extract text using GPT-4 Vision for the entire document."""
        if not self.client:
            raise ValueError("OpenAI API key required for Vision OCR.")
            
        print("Converting PDF to images for Vision OCR...")
        images = convert_from_path(str(self.pdf_path), dpi=dpi)
        
        full_text = []
        metadata = []
        
        for i, image in enumerate(images, 1):
            if verbose:
                print(f"Vision processing page {i}/{len(images)}...")
            
            page_text, conf = self._extract_page_with_vision(image)
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

    def _extract_page_with_vision(self, image):
        """Vision OCR for a single page image."""
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
    
    def save_to_file(self, output_path=None):
        if not self.output_text: return None
        if output_path is None: output_path = self.pdf_path.with_suffix('.txt')
        else: output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(self.output_text)
        return output_path

    def extract_with_confidence(self, dpi=600, language='eng'):
        images = convert_from_path(str(self.pdf_path), dpi=dpi, fmt='jpeg')
        results = []
        for page_num, image in enumerate(images, 1):
            data = pytesseract.image_to_data(image, lang=language, output_type=pytesseract.Output.DICT)
            page_results = {'page_num': page_num, 'words': []}
            for i, word in enumerate(data['text']):
                if word.strip():
                    page_results['words'].append({'text': word, 'confidence': data['conf'][i]})
            results.append(page_results)
        return results


if __name__ == "__main__":
    import sys
    import os
    if len(sys.argv) < 2:
        print("Usage: python ocr_text.py <pdf_path>")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)
        
    extractor = OCRPDFExtractor(pdf_path)
    text, meta = extractor.extract(verbose=True)
    saved_path = extractor.save_to_file()
    print(f"✅ OCR extraction complete. Output saved to: {saved_path}")
