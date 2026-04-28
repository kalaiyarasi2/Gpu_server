#!/usr/bin/env python3
"""
OCR PDF Text Extractor
Extracts text from scanned/image-based PDFs using Tesseract OCR
Converts PDF pages to images and performs optical character recognition
"""
import os
from pathlib import Path
import pytesseract
from pdf2image import convert_from_path
import base64
from io import BytesIO
import time
import re
import threading
try:
    import rostaing_ocr
    ROSTAING_AVAILABLE = True
except ImportError:
    ROSTAING_AVAILABLE = False


class OCRPDFExtractor:
    """
    OCR-based text extraction for scanned (image-based) PDFs.
    Converts pages to images and uses Tesseract for text recognition.
    """
    
    def __init__(self, pdf_path, api_key=None):
        """
        Initialize the extractor with a PDF file path.
        """
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        from openai import OpenAI
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None
        self.output_text = ""
        
        self.rostaing_engine = None
        if ROSTAING_AVAILABLE:
            try:
                self.rostaing_engine = rostaing_ocr.RostaingOCR()
            except:
                self.rostaing_engine = rostaing_ocr
        
        # Caching results for parallel efficiency
        self._rostaing_cache = None
        self._rostaing_meta_cache = None
        self._lock = threading.Lock()
    
    def extract(self, dpi=600, language='eng', psm_mode=1, verbose=True, engine='tesseract', **kwargs):
        """
        Extract text using OCR with strict hierarchical fallback.
        """
        if verbose:
            print(f"\n{'='*80}")
            print(f"OCR PDF EXTRACTION (HIERARCHICAL)")
            print(f"{'='*80}")
            print(f"Input file: {self.pdf_path}")
            print()
        
        if engine == 'vision':
            return self._extract_with_vision(dpi=300, verbose=verbose)
        if engine == 'rostaing':
            return self._extract_with_rostaing(verbose=verbose)
        
        extracted_text = []
        from text_quality_verifier import TextQualityVerifier
        verifier = TextQualityVerifier()
        
        try:
            if verbose:
                print("Converting PDF to images...")
            
            # Initial images for Tesseract
            images = convert_from_path(str(self.pdf_path), dpi=600, fmt='jpeg')
            total_pages = len(images)
            pages_metadata = []
            
            for page_num, image in enumerate(images, 1):
                if verbose:
                    print(f"Processing Page {page_num}/{total_pages}...")
                
                # Add page separator
                page_header = f"\n{'='*80}\nPAGE {page_num}\n{'='*80}\n\n"
                
                final_text = "[Extraction failed]"
                extraction_method = "failed"
                final_score = 0.0
                rec_hi = "full_vision" # Initial state
                
                # --- STEP 1: Rostaing OCR ---
                if ROSTAING_AVAILABLE and self.rostaing_engine:
                    if verbose: print(f"   ↪ Step 1: Rostaing OCR...")
                    try:
                        ros_text, ros_meta = self._extract_with_rostaing(verbose=False)
                        if page_num <= len(ros_meta):
                            ros_page_text = ros_meta[page_num-1]["text"].split(f"PAGE {page_num}")[-1].strip('= \n')
                            if ros_page_text.strip():
                                qual = verifier.page_quality(ros_page_text)
                                final_text = ros_page_text
                                extraction_method = "rostaing-ocr"
                                final_score = qual['score']
                                rec_hi = qual['recommendation']
                                if verbose: print(f"     Score: {final_score:.2f}, Recommendation: {rec_hi}")
                    except Exception as e:
                        print(f"     ⚠️ Rostaing failed: {e}")

                # --- STEP 2: Tesseract 600 DPI ---
                if rec_hi != 'ok':
                    if verbose: print(f"   ↪ Step 2: Tesseract 600 DPI...")
                    try:
                        text_hi = pytesseract.image_to_string(image, config=f'--oem 3 --psm {psm_mode}', lang=language)
                        if text_hi.strip():
                            qual = verifier.page_quality(text_hi)
                            if qual['score'] > final_score or qual['analysis']['is_acceptable']:
                                final_text = text_hi
                                extraction_method = "tesseract-600dpi"
                                final_score = qual['score']
                                rec_hi = qual['recommendation']
                                if verbose: print(f"     Score: {final_score:.2f}, Recommendation: {rec_hi}")
                    except Exception as e:
                        print(f"     ⚠️ Tesseract 600 failed: {e}")

                # --- STEP 3: Tesseract 300 DPI ---
                if rec_hi != 'ok':
                    if verbose: print(f"   ↪ Step 3: Tesseract 300 DPI...")
                    try:
                        mid_imgs = convert_from_path(str(self.pdf_path), dpi=300, fmt='jpeg', first_page=page_num, last_page=page_num)
                        if mid_imgs:
                            text_mid = pytesseract.image_to_string(mid_imgs[0], config=f'--oem 3 --psm {psm_mode}', lang=language)
                            if text_mid.strip():
                                qual = verifier.page_quality(text_mid)
                                if qual['score'] > final_score or qual['analysis']['is_acceptable']:
                                    final_text = text_mid
                                    extraction_method = "tesseract-300dpi"
                                    final_score = qual['score']
                                    rec_hi = qual['recommendation']
                                    if verbose: print(f"     Score: {final_score:.2f}, Recommendation: {rec_hi}")
                    except Exception as e:
                        print(f"     ⚠️ Tesseract 300 failed: {e}")

                # --- STEP 4: GPT-4 Vision ---
                if rec_hi != 'ok' and self.client is not None:
                    if verbose: print(f"   ↪ Step 4: GPT-4 Vision (Last Resort)...")
                    try:
                        vis_imgs = convert_from_path(str(self.pdf_path), dpi=300, fmt='jpeg', first_page=page_num, last_page=page_num)
                        if vis_imgs:
                            vis_text, vis_conf = self._extract_page_with_vision(vis_imgs[0])
                            if vis_text.strip():
                                final_text = vis_text
                                extraction_method = "gpt-4-vision-fallback"
                                final_score = vis_conf
                                if verbose: print(f"     Confidence: {final_score:.2f}")
                    except Exception as e:
                        print(f"     ⚠️ Vision fallback failed: {e}")
                
                pages_metadata.append({
                    "page_number": page_num,
                    "text": page_header + final_text,
                    "is_scanned": True,
                    "extraction_method": extraction_method,
                    "confidence": final_score,
                    "quality_metrics": verifier.analyze_quality(final_text, num_pages=1).get("metrics", {})
                })
            
            # Rearrange pages logically if markers are found in text
            if verbose and total_pages > 1:
                print(f"\n📊 Rearranging {total_pages} pages logically...")
            
            pages_metadata.sort(key=self._get_logical_page_sort_key)
            
            # Rebuild output text from sorted metadata
            self.output_text = "".join([m["text"] + "\n\n" for m in pages_metadata])
            
            return self.output_text, pages_metadata
            
        except Exception as e:
            print(f"OCR Error: {e}")
            raise

    def extract_page(self, page_num, language='eng', psm_mode=1, verbose=True):
        """
        Extract a single page using the strict hierarchical hierarchy.
        """
        from text_quality_verifier import TextQualityVerifier
        verifier = TextQualityVerifier()
        
        final_text = "[Extraction failed]"
        extraction_method = "failed"
        final_score = 0.0
        rec = "full_vision"
        
        # Add page separator
        page_header = f"\n{'='*80}\nPAGE {page_num}\n{'='*80}\n\n"
        
        # --- STEP 1: Rostaing OCR ---
        if ROSTAING_AVAILABLE and self.rostaing_engine:
            try:
                ros_text, ros_meta = self._extract_with_rostaing(verbose=False)
                if page_num <= len(ros_meta):
                    ros_page_text = ros_meta[page_num-1]["text"].split(f"PAGE {page_num}")[-1].strip('= \n')
                    if ros_page_text.strip():
                        qual = verifier.page_quality(ros_page_text)
                        final_text = ros_page_text
                        extraction_method = "rostaing-ocr"
                        final_score = qual['score']
                        rec = qual['recommendation']
            except: pass

        # --- STEP 2: Tesseract 600 DPI ---
        if rec != 'ok' and rec != 'full_vision':
            try:
                images = convert_from_path(str(self.pdf_path), dpi=600, fmt='jpeg', first_page=page_num, last_page=page_num)
                if images:
                    text = pytesseract.image_to_string(images[0], config=f'--oem 3 --psm {psm_mode}', lang=language)
                    if text.strip():
                        qual = verifier.page_quality(text)
                        if qual['score'] > final_score or qual['analysis']['is_acceptable']:
                            final_text = text
                            extraction_method = "tesseract-600dpi"
                            final_score = qual['score']
                            rec = qual['recommendation']
            except: pass

        # --- STEP 3: Tesseract 300 DPI ---
        if rec != 'ok' and rec != 'full_vision':
            try:
                images = convert_from_path(str(self.pdf_path), dpi=300, fmt='jpeg', first_page=page_num, last_page=page_num)
                if images:
                    text = pytesseract.image_to_string(images[0], config=f'--oem 3 --psm {psm_mode}', lang=language)
                    if text.strip():
                        qual = verifier.page_quality(text)
                        if qual['score'] > final_score or qual['analysis']['is_acceptable']:
                            final_text = text
                            extraction_method = "tesseract-300dpi"
                            final_score = qual['score']
                            rec = qual['recommendation']
            except: pass

        # --- STEP 4: GPT-4 Vision ---
        if rec != 'ok' and self.client is not None:
            try:
                images = convert_from_path(str(self.pdf_path), dpi=300, fmt='jpeg', first_page=page_num, last_page=page_num)
                if images:
                    text, conf = self._extract_page_with_vision(images[0])
                    if text.strip():
                        final_text = text
                        extraction_method = "gpt-4-vision-fallback"
                        final_score = conf
            except: pass

        return {
            "page_number": page_num,
            "text": page_header + final_text,
            "extraction_method": extraction_method,
            "confidence": final_score
        }

    def _extract_with_rostaing(self, verbose=True):
        if not self.rostaing_engine: return "", []
        
        with self._lock:
            if self._rostaing_cache is not None:
                return self._rostaing_cache, self._rostaing_meta_cache
                
            try:
                full_text = ""
                if hasattr(self.rostaing_engine, 'ocr_extractor'):
                    import uuid
                    temp_output = self.pdf_path.with_suffix(f'.rostaing_temp_{uuid.uuid4().hex[:8]}.txt')
                    self.rostaing_engine.ocr_extractor(str(self.pdf_path), output_file=str(temp_output))
                    if temp_output.exists():
                        with open(temp_output, 'r', encoding='utf-8') as f:
                            full_text = f.read()
                        temp_output.unlink()
                elif hasattr(self.rostaing_engine, 'process_document'):
                    full_text = self.rostaing_engine.process_document(str(self.pdf_path))
                
                pages = full_text.split('\f') if '\f' in full_text else [full_text]
                metadata = []
                for i, p_text in enumerate(pages, 1):
                    metadata.append({"page_number": i, "text": f"PAGE {i}\n" + p_text, "extraction_method": "rostaing-ocr", "confidence": 0.95})
                
                self._rostaing_cache = full_text
                self._rostaing_meta_cache = metadata
                
                # Sort logically
                metadata.sort(key=self._get_logical_page_sort_key)
                full_text = "".join([m["text"] + "\n\n" for m in metadata])
                
                return full_text, metadata
            except: return "", []

    def _extract_with_vision(self, dpi=300, verbose=True):
        images = convert_from_path(str(self.pdf_path), dpi=dpi)
        full_text = []
        metadata = []
        for i, image in enumerate(images, 1):
            page_text, conf = self._extract_page_with_vision(image)
            header = f"\n{'='*80}\nPAGE {i}\n{'='*80}\n\n"
            full_text.append(header + page_text + "\n\n")
            metadata.append({"page_number": i, "text": header + page_text, "extraction_method": "gpt-4-vision", "confidence": conf})
        # Rearrange logically
        metadata.sort(key=self._get_logical_page_sort_key)
        full_text = "".join([m["text"] + "\n\n" for m in metadata])
        
        return full_text, metadata

    def _extract_page_with_vision(self, image, verbose=False):
        buffered = BytesIO()
        image.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        prompt = """Extract ALL text from this insurance document page while preserving layout and structure.
CRITICAL: Pay extremely close attention to CHECKBOXES and tables.
- If a checkbox is marked with an 'X', 'v', or checkmark, represent it as [X].
- For example, if 'YES' is checked, write '[X] YES'.
- For tables like PRIOR CARRIER or RATING INFORMATION, extract every row and column clearly.
- Even if a table seems empty, extract the headers and any handwritten or typed marks.
- CRITICAL: Do NOT skip "floating" or loose numbers outside formal tables. Explicitly extract the PREMIUM CALCULATION section, including EXPERIENCE MODIFICATION, TOTAL ESTIMATED ANNUAL PREMIUM, MINIMUM PREMIUM, and DEPOSIT PREMIUM numerical values.
Return the full text as Markdown-style tables where appropriate."""
        
        system_msg = "You are a professional insurance document OCR expert. Your goal is to extract every piece of data, including hand-marked checkboxes and complex grid data, with 100% fidelity."
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}}]}
                ],
                max_tokens=4000, 
                temperature=0.0
            )
            return response.choices[0].message.content, 0.95
        except Exception as e:
            print(f"Vision Error: {e}")
            return "", 0.0

    def _get_logical_page_sort_key(self, page_meta):
        """
        Helper to extract logical page number for sorting.
        """
        text = page_meta.get("text", "")
        # Remove the synthetic PAGE X header to avoid circular sorting
        clean_text = re.sub(r'^={10,}\s*PAGE\s+\d+\s*={10,}', '', text, flags=re.IGNORECASE | re.DOTALL).strip()
        
        # Priority 1: "Page X of Y"
        match = re.search(r'Page\s*(\d+)\s*(?:of|/)\s*\d+', clean_text, re.IGNORECASE)
        if match: return int(match.group(1))
        
        # Priority 2: "Page X" (at start or end of content)
        # Look at first 500 and last 500 chars
        sample = clean_text[:500] + "\n" + clean_text[-500:]
        match = re.search(r'Page\s*(\d+)', sample, re.IGNORECASE)
        if match: return int(match.group(1))
        
        # Priority 3: Simple digit at corners (REMOVED - TOO RISKY)
        # We now require an explicit 'Page' or 'Pg' marker to avoid confusion with Loc numbers.

        # Fallback to physical page number
        return page_meta.get("page_number", 999)

    def save_to_file(self, output_path=None):
        if not self.output_text: return None
        output_path = Path(output_path or self.pdf_path.with_suffix('.txt'))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f: f.write(self.output_text)
        return output_path
