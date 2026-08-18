"""
PDF Text Extraction using pdfplumber
Structure-aware and table-aware extraction
Preserves layout and formatting in output TXT file
"""

import pdfplumber
import json
import os
import re
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from gpu_config import gpu_concurrency_config


def _safe_crop(page, bbox):
    """
    Safely crop a page by clipping the bounding box to the page's boundaries.
    Returns the cropped page or None if the resulting area is invalid.
    """
    x0, y0, x1, y1 = bbox
    
    # Clip coordinates to page boundaries
    x0 = max(0, min(x0, page.width))
    x1 = max(0, min(x1, page.width))
    y0 = max(0, min(y0, page.height))
    y1 = max(0, min(y1, page.height))
    
    # Ensure x0 < x1 and y0 < y1
    left, right = min(x0, x1), max(x0, x1)
    top, bottom = min(y0, y1), max(y0, y1)
    
    # If the width or height is negligible, return None
    if right - left < 0.1 or bottom - top < 0.1:
        return None
        
    try:
        return page.crop((left, top, right, bottom), strict=False)
    except Exception as e:
        print(f"   ⚠️ _safe_crop failed even after clipping: {e}")
        return None


def detect_watermarks_ai(all_pages_text: List[str]) -> List[str]:
    """
    Use AI to detect watermarks by analyzing text patterns across pages.
    """
    try:
        from openai import OpenAI
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return []
        
        client = OpenAI(api_key=api_key)
        
        # Sample text from first 3-5 pages
        sample_pages = all_pages_text[:min(5, len(all_pages_text))]
        
        prompt = f"""Analyze these PDF pages and identify any watermark text.

Return JSON:
{{
  "watermark_texts": ["text1", "text2"],
  "confidence": 0.0-1.0
}}

PAGE SAMPLES:
{json.dumps(sample_pages, indent=2)}
"""
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=800,
            temperature=0.0
        )
        
        result = json.loads(response.choices[0].message.content)
        return result.get("watermark_texts", [])
        
    except Exception as e:
        print(f"   ⚠️ Watermark detection failed: {e}")
        return []


def filter_watermark_text(text: str, watermark_patterns: List[str]) -> str:
    """
    Remove watermark text from extracted content.
    """
    if not watermark_patterns:
        return text
    
    filtered_text = text
    for watermark in watermark_patterns:
        if watermark and len(watermark.strip()) > 0:
            filtered_text = filtered_text.replace(watermark, "")
            pattern = re.compile(re.escape(watermark), re.IGNORECASE)
            filtered_text = pattern.sub("", filtered_text)
    
    return filtered_text


def extract_pdf_with_pdfplumber(pdf_path: str, output_txt: str = None) -> tuple[str, list[dict]]:
    """
    Extract text, tables, and structure from PDF using pdfplumber in parallel.
    """
    all_text = ""
    pages_metadata = []
    
    # Header Information
    header = "="*80 + "\n"
    header += f"PDF DOCUMENT EXTRACTION (pdfplumber)\n"
    header += "="*80 + "\n\n"
    all_text += header

    # Detect reversal once from first page
    is_reversed = False
    total_pages = 0
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        try:
            sample_page = pdf.pages[0]
            sample_text = sample_page.extract_text()
            is_reversed = _check_if_reversed(sample_text)
            if is_reversed:
                print(f"⚠️ Detected reversed text encoding. Applying correction...")
        except:
            pass

    def process_page_plumber(p_num):
        """Worker function for parallel extraction."""
        try:
            with pdfplumber.open(pdf_path) as pdf_local:
                page = pdf_local.pages[p_num - 1]
                page_content = ""
                
                # Page header
                page_header = f"\n{'='*80}\n"
                page_header += f"PAGE {p_num}\n"
                page_header += f"{'='*80}\n\n"
                page_content += page_header
                
                # Extract tables first
                tables = page.extract_tables()
                
                # Extract text with layout
                text = page.extract_text(layout=True)
                if is_reversed and text:
                    text = _reverse_text_block(text)
                
                if tables:
                    table_bboxes = page.find_tables()
                    
                    # Extract text above first table
                    if table_bboxes:
                        bbox = table_bboxes[0].bbox
                        if bbox[1] > 0:
                            top_area = _safe_crop(page, (0, 0, page.width, bbox[1]))
                            if top_area:
                                top_text = top_area.extract_text(layout=True)
                                if top_text:
                                    if is_reversed: top_text = _reverse_text_block(top_text)
                                    page_content += top_text + "\n\n"
                    
                    # Write each table
                    for idx, (table, table_bbox) in enumerate(zip(tables, table_bboxes), start=1):
                        if is_reversed:
                            table = [[_reverse_text_block(str(cell)) if cell else cell for cell in row] for row in table]

                        page_content += f"[TABLE {idx}]\n"
                        page_content += "-" * 80 + "\n"
                        page_content += format_table(table) + "\n"
                        page_content += "-" * 80 + "\n\n"
                        
                        if idx < len(table_bboxes):
                            current_bbox = table_bbox.bbox
                            next_bbox = table_bboxes[idx].bbox
                            if next_bbox[1] > current_bbox[3]:
                                between_area = _safe_crop(page, (0, current_bbox[3], page.width, next_bbox[1]))
                                if between_area:
                                    between_text = between_area.extract_text(layout=True)
                                    if between_text and between_text.strip():
                                        if is_reversed: between_text = _reverse_text_block(between_text)
                                        page_content += between_text + "\n\n"
                    
                    # Extract text after last table
                    if table_bboxes:
                        last_bbox = table_bboxes[-1].bbox
                        if last_bbox[3] < page.height:
                            bottom_area = _safe_crop(page, (0, last_bbox[3], page.width, page.height))
                            if bottom_area:
                                bottom_text = bottom_area.extract_text(layout=True)
                                if bottom_text and bottom_text.strip():
                                    if is_reversed: bottom_text = _reverse_text_block(bottom_text)
                                    page_content += bottom_text + "\n"
                else:
                    if text:
                        page_content += text + "\n"
                
                return {
                    "page_number": p_num,
                    "text": page_content,
                    "extraction_method": "pdfplumber",
                    "is_scanned": False,
                    "confidence": 1.0
                }
        except Exception as e:
            print(f"   ⚠️ Page {p_num} extraction failed: {e}")
            return {
                "page_number": p_num,
                "text": f"\n[ERROR] Page {p_num} extraction failed: {e}\n",
                "extraction_method": "pdfplumber-error",
                "is_scanned": False,
                "confidence": 0.0
            }

    # Parallel processing
    max_workers = gpu_concurrency_config.get('pdf_rendering', {}).get('max_workers', 8)
    print(f"🚀 Launching Parallel pdfplumber Extraction ({total_pages} pages, {max_workers} workers)...")
    
    unordered_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_page_plumber, i) for i in range(1, total_pages + 1)]
        for future in as_completed(futures):
            unordered_results.append(future.result())

    # Maintain order
    unordered_results.sort(key=lambda x: x['page_number'])
    for res in unordered_results:
        all_text += res['text'] + "\n"
        pages_metadata.append(res)

    # Watermarks
    print(f"🔍 Checking for watermarks...")
    page_texts = [p["text"] for p in pages_metadata]
    watermarks = detect_watermarks_ai(page_texts)
    if watermarks:
        print(f"   Filtering {len(watermarks)} watermark(s)...")
        all_text = filter_watermark_text(all_text, watermarks)
        for page_meta in pages_metadata:
            page_meta["text"] = filter_watermark_text(page_meta["text"], watermarks)

    if output_txt:
        with open(output_txt, 'w', encoding='utf-8') as f:
            f.write(all_text)
    return all_text, pages_metadata


def _check_if_reversed(text: str) -> bool:
    if not text: return False
    reversed_keywords = ["tropeR", "mialC", "ycailoP", "oitaR", "ssoL", "diap"]
    count = 0
    sample = text[:10000]
    for kw in reversed_keywords:
        if kw in sample or kw.lower() in sample.lower():
            count += 1
    return count >= 2


def _reverse_text_block(text: str) -> str:
    if not text: return ""
    return '\n'.join([line[::-1] for line in text.split('\n')])


def format_table(table: list[list]) -> str:
    if not table or not table[0]: return ""
    col_widths = []
    for col_idx in range(len(table[0])):
        max_w = 15
        for row in table:
            if col_idx < len(row) and row[col_idx]:
                max_w = max(max_w, len(' '.join(str(row[col_idx]).split())))
        col_widths.append(max_w)
    
    rows = []
    for row_idx, row in enumerate(table):
        cells = []
        for col_idx, cell in enumerate(row):
            text = ' '.join(str(cell).split()) if cell else ""
            cells.append(text.ljust(col_widths[col_idx]))
        rows.append("\t".join(cells))
        if row_idx == 0:
            rows.append("\t".join(["-" * w for w in col_widths]))
    return "\n".join(rows)


def extract_with_pymupdf(pdf_path: str) -> tuple[str, list[dict]]:
    try:
        import fitz
    except ImportError:
        return "", []
    
    all_text = ""
    pages_metadata = []
    doc = fitz.open(pdf_path)
    
    # Reversal check
    is_reversed = False
    if len(doc) > 0:
        if _check_if_reversed(doc[0].get_text()):
            is_reversed = True

    for page_num in range(len(doc)):
        text = doc[page_num].get_text()
        if is_reversed: text = _reverse_text_block(text)
        page_content = f"\n{'='*80}\nPAGE {page_num + 1}\n{'='*80}\n\n" + text
        all_text += page_content + "\n"
        pages_metadata.append({
            "page_number": page_num + 1,
            "text": page_content,
            "extraction_method": "pymupdf",
            "is_scanned": False,
            "confidence": 0.9
        })
    doc.close()
    return all_text, pages_metadata


def extract_pdf_hybrid(pdf_path: str, output_txt: str = None) -> tuple[str, list[dict], dict]:
    """
    Lazy Hybrid PDF extraction:
    1. Run pdfplumber as the primary engine.
    2. Check if a baseline of claim data is present.
    3. ONLY run PyMuPDF (the secondary engine) if data is missing.
    """
    print(f"\n🔄 Starting Lazy Hybrid PDF extraction...")
    
    # 1. Run primary engine
    text_plumber, pages_plumber = extract_pdf_with_pdfplumber(pdf_path)
    
    # Check for basic claim presence (heuristic)
    # RC: Broadened claim pattern to catch 8-10 digit IDs often used by ICW/others
    claim_pattern = r'(?:Claim\s*#?\s*:?\s*)?(\d{5,12}(?:[A-Z0-9]{1,3})?)'
    
    # Extract matches and ensure we aren't just matching generic numbers
    matches = re.findall(claim_pattern, text_plumber, re.IGNORECASE)
    # Filter: IDs must be at least 5 digits long to be considered a potential claim
    claims_p = {m for m in matches if len(m) >= 5}
    
    # 2. Lazy decision: Only run PyMuPDF if claims are missing or count is suspiciously low
    if len(claims_p) == 0:
        print(f"   ⚠️ Primary engine (pdfplumber) found 0 claims. Launching PyMuPDF fallback...")
        text_pymupdf, pages_pymupdf = extract_with_pymupdf(pdf_path)
        claims_m = set(re.findall(claim_pattern, text_pymupdf))
        missing = claims_m - claims_p
        
        extraction_info = {
            "primary_method": "pdfplumber",
            "fallback_used": True,
            "claims_plumber": 0,
            "claims_pymupdf": len(claims_m)
        }
        
        if missing:
            recovery = "\n\n" + "="*80 + "\nRECOVERY DATA (PyMuPDF)\n" + "="*80 + "\n"
            added = False
            for page_data in pages_pymupdf:
                if any(m in page_data['text'] for m in missing):
                    recovery += f"\n--- RECOVERED (Page {page_data['page_number']}) ---\n" + page_data['text']
                    added = True
            if added: text_plumber += recovery
    else:
        print(f"   ✓ Primary engine (pdfplumber) succeeded ({len(claims_p)} claims found). Skipping PyMuPDF.")
        extraction_info = {
            "primary_method": "pdfplumber",
            "fallback_used": False,
            "claims_plumber": len(claims_p),
            "claims_pymupdf": 0
        }
    
    if output_txt:
        with open(output_txt, 'w', encoding='utf-8') as f:
            f.write(text_plumber)
    return text_plumber, pages_plumber, extraction_info


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python pdf_plumber.py <pdf_file> [output_txt] [--hybrid]")
        sys.exit(1)
    
    pdf_file = sys.argv[1]
    output_txt = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else None
    use_hybrid = "--hybrid" in sys.argv
    
    if use_hybrid:
        text, pages, info = extract_pdf_hybrid(pdf_file, output_txt)
        print(f"Extraction Method: {info.get('primary_method')} (Hybrid)")
    else:
        text, pages = extract_pdf_with_pdfplumber(pdf_file, output_txt)
    print(f"Total pages: {len(pages)}")
