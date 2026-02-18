"""
PDF Text Extraction using pdfplumber
Structure-aware and table-aware extraction
Preserves layout and formatting in output TXT file
"""

import pdfplumber
import json
import os
from typing import List, Dict, Optional


def detect_watermarks_ai(all_pages_text: List[str]) -> List[str]:
    """
    Use AI to detect watermarks by analyzing text patterns across pages.
    
    AI identifies:
    - Repeated text appearing on multiple pages
    - Text that doesn't contribute to document content
    - Positional patterns indicating watermarks
    
    No hardcoded patterns - fully dynamic detection.
    """
    try:
        from openai import OpenAI
        
        # Get API key from environment
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("   ⚠️ No OpenAI API key found. Skipping watermark detection.")
            return []
        
        client = OpenAI(api_key=api_key)
        
        # Sample text from first 3-5 pages
        sample_pages = all_pages_text[:min(5, len(all_pages_text))]
        
        prompt = f"""Analyze these PDF pages and identify any watermark text.

Watermarks are:
- Text that appears repeatedly across multiple pages
- Usually in same position (header, footer, diagonal)
- Not part of the actual document content
- Examples: "CONFIDENTIAL", "DRAFT", company names, dates, page numbers

Return JSON:
{{
  "watermark_texts": ["text1", "text2"],
  "watermark_positions": ["header", "footer", "diagonal"],
  "confidence": 0.0-1.0,
  "reasoning": "why these are watermarks"
}}

If no watermarks detected, return empty watermark_texts array.

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
        watermarks = result.get("watermark_texts", [])
        
        if watermarks:
            print(f"   🔍 AI detected {len(watermarks)} watermark(s): {watermarks}")
            print(f"   Confidence: {result.get('confidence', 0):.2%}")
        else:
            print(f"   ✓ No watermarks detected")
        
        return watermarks
        
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
            # Remove all occurrences of the watermark
            filtered_text = filtered_text.replace(watermark, "")
            # Also try case-insensitive removal
            import re
            pattern = re.compile(re.escape(watermark), re.IGNORECASE)
            filtered_text = pattern.sub("", filtered_text)
    
    return filtered_text


def extract_pdf_with_pdfplumber(pdf_path: str, output_txt: str = None) -> tuple[str, list[dict]]:
    """
    Extract text, tables, and structure from PDF using pdfplumber
    Outputs to TXT file preserving original layout and returns content for pipeline.
    
    Args:
        pdf_path: Path to the PDF file
        output_txt: Optional path to output TXT file.
        
    Returns:
        tuple: (all_text string, pages_metadata list)
    """
    all_text = ""
    pages_metadata = []
    
    # Header Information
    header = "="*80 + "\n"
    header += f"PDF DOCUMENT EXTRACTION (pdfplumber)\n"
    header += "="*80 + "\n\n"
    all_text += header

    with pdfplumber.open(pdf_path) as pdf:
        # Check first page for reversed text heuristic
        try:
            sample_page = pdf.pages[0]
            sample_text = sample_page.extract_text()
            is_reversed = _check_if_reversed(sample_text)
            if is_reversed:
                print(f"⚠️ Detected reversed text encoding. Applying correction...")
        except:
            is_reversed = False

        # Process each page
        for page_num, page in enumerate(pdf.pages, start=1):
            page_content = ""
            
            # Page header
            page_header = f"\n{'='*80}\n"
            page_header += f"PAGE {page_num}\n"
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
                        top_area = page.crop((0, 0, page.width, bbox[1]))
                        top_text = top_area.extract_text(layout=True)
                        if top_text:
                            if is_reversed: top_text = _reverse_text_block(top_text)
                            page_content += top_text + "\n\n"
                
                # Write each table
                for idx, (table, table_bbox) in enumerate(zip(tables, table_bboxes), start=1):
                    # Fix reversed table cells if needed
                    if is_reversed:
                        table = [[_reverse_text_block(str(cell)) if cell else cell for cell in row] for row in table]

                    page_content += f"[TABLE {idx}]\n"
                    page_content += "-" * 80 + "\n"
                    page_content += format_table(table) + "\n"
                    page_content += "-" * 80 + "\n\n"
                    
                    # Extract text between tables
                    if idx < len(table_bboxes):
                        current_bbox = table_bbox.bbox
                        next_bbox = table_bboxes[idx].bbox
                        if next_bbox[1] > current_bbox[3]:
                            between_area = page.crop((0, current_bbox[3], page.width, next_bbox[1]))
                            between_text = between_area.extract_text(layout=True)
                            if between_text and between_text.strip():
                                if is_reversed: between_text = _reverse_text_block(between_text)
                                page_content += between_text + "\n\n"
                
                # Extract text after last table
                if table_bboxes:
                    last_bbox = table_bboxes[-1].bbox
                    if last_bbox[3] < page.height:
                        bottom_area = page.crop((0, last_bbox[3], page.width, page.height))
                        bottom_text = bottom_area.extract_text(layout=True)
                        if bottom_text and bottom_text.strip():
                            if is_reversed: bottom_text = _reverse_text_block(bottom_text)
                            page_content += bottom_text + "\n"
            else:
                if text:
                    page_content += text + "\n"
            
            all_text += page_content + "\n"
            
            # Collect metadata for connector compatibility
            pages_metadata.append({
                "page_number": page_num,
                "text": page_content,
                "extraction_method": "pdfplumber",
                "is_scanned": False,
                "confidence": 1.0
            })

    # Detect and filter watermarks using AI
    print(f"\n🔍 Checking for watermarks...")
    page_texts = [p["text"] for p in pages_metadata]
    watermarks = detect_watermarks_ai(page_texts)
    
    if watermarks:
        print(f"   Filtering {len(watermarks)} watermark(s) from extracted text...")
        all_text = filter_watermark_text(all_text, watermarks)
        # Also filter from individual page metadata
        for page_meta in pages_metadata:
            page_meta["text"] = filter_watermark_text(page_meta["text"], watermarks)

    # Save to file if output_txt is provided
    if output_txt:
        with open(output_txt, 'w', encoding='utf-8') as f:
            f.write(all_text)
        print(f"✓ Extraction saved to: {output_txt}")

    return all_text, pages_metadata


def _check_if_reversed(text: str) -> bool:
    """Detect if text is likely reversed (e.g. 'tropeR' instead of 'Report')."""
    if not text: return False
    # Check for common keywords that might appear reversed
    reversed_keywords = ["tropeR", "mialC", "ycailoP", "oitaR", "ssoL", "diap"]
    count = 0
    for kw in reversed_keywords:
        if kw in text or kw.lower() in text.lower():
            count += 1
    return count >= 2


def _reverse_text_block(text: str) -> str:
    """Reverse each line of text."""
    if not text: return ""
    lines = text.split('\n')
    reversed_lines = [line[::-1] for line in lines]
    return '\n'.join(reversed_lines)


def format_table(table: list[list]) -> str:
    """Format table data into aligned text format."""
    if not table or not table[0]:
        return ""
    
    col_widths = []
    for col_idx in range(len(table[0])):
        max_width = 0
        for row in table:
            if col_idx < len(row) and row[col_idx]:
                cell_text = str(row[col_idx]).strip()
                max_width = max(max_width, len(cell_text))
        col_widths.append(max_width)
    
    formatted_rows = []
    for row_idx, row in enumerate(table):
        formatted_cells = []
        for col_idx, cell in enumerate(row):
            cell_text = str(cell).strip() if cell else ""
            width = col_widths[col_idx]
            formatted_cells.append(cell_text.ljust(width))
        
        formatted_rows.append(" | ".join(formatted_cells))
        if row_idx == 0:
            formatted_rows.append("-+-".join(["-" * w for w in col_widths]))
    
    return "\n".join(formatted_rows)


def extract_with_pymupdf(pdf_path: str) -> tuple[str, list[dict]]:
    """
    Alternative PDF extraction using PyMuPDF (fitz).
    Used as fallback when pdfplumber fails to extract complete text.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        tuple: (all_text string, pages_metadata list)
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("   ⚠️ PyMuPDF not installed. Cannot use fallback extraction.")
        return "", []
    
    all_text = ""
    pages_metadata = []
    
    # Header
    header = "="*80 + "\n"
    header += f"PDF DOCUMENT EXTRACTION (pymupdf)\n"
    header += "="*80 + "\n\n"
    all_text += header
    
    doc = fitz.open(pdf_path)
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_content = ""
        
        # Page header
        page_header = f"\n{'='*80}\n"
        page_header += f"PAGE {page_num + 1}\n"
        page_header += f"{'='*80}\n\n"
        page_content += page_header
        
        # Extract text
        text = page.get_text()
        if text:
            page_content += text
        
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


def validate_extraction_quality(text: str, pdf_path: str) -> dict:
    """
    Validate the quality and completeness of extracted text.
    
    Args:
        text: Extracted text to validate
        pdf_path: Path to original PDF
        
    Returns:
        dict: Validation metrics
    """
    import re
    
    # Count claim numbers (insurance-specific validation)
    claim_pattern = r'W\d{2}IN\d{5}|\d{6}B'
    claim_numbers = re.findall(claim_pattern, text)
    unique_claims = len(set(claim_numbers))
    
    # Calculate text density
    lines = text.split('\n')
    non_empty_lines = [l for l in lines if l.strip()]
    avg_line_length = sum(len(l) for l in non_empty_lines) / len(non_empty_lines) if non_empty_lines else 0
    
    # Check for page markers
    page_markers = text.count('PAGE ')
    
    # Calculate completeness score
    completeness_score = 1.0
    issues = []
    
    if unique_claims == 0:
        completeness_score -= 0.5
        issues.append("No claim numbers detected")
    
    if avg_line_length < 10:
        completeness_score -= 0.3
        issues.append("Low text density")
    
    if page_markers == 0:
        completeness_score -= 0.2
        issues.append("No page markers found")
    
    return {
        "claim_numbers_found": unique_claims,
        "total_characters": len(text),
        "total_lines": len(lines),
        "non_empty_lines": len(non_empty_lines),
        "avg_line_length": round(avg_line_length, 2),
        "page_markers": page_markers,
        "completeness_score": max(0.0, completeness_score),
        "issues": issues,
        "is_complete": completeness_score >= 0.7
    }


def extract_pdf_hybrid(pdf_path: str, output_txt: str = None) -> tuple[str, list[dict], dict]:
    """
    Hybrid PDF extraction with automatic fallback.
    
    Strategy:
    1. Run pdfplumber (Primary - best layout)
    2. Run pymupdf (Secondary - fast, robust text)
    3. Compare unique claim counts
    4. If pdfplumber missed claims that pymupdf found:
       - Append the PyMuPDF text for those specific pages as "RECOVERY DATA"
       - This ensures the AI sees the missing info without losing general table structure
    
    Args:
        pdf_path: Path to the PDF file
        output_txt: Optional path to output TXT file
        
    Returns:
        tuple: (all_text, pages_metadata, extraction_info)
    """
    import re
    
    print(f"\n🔄 Starting hybrid PDF extraction...")
    
    # 1. Run pdfplumber (Primary)
    print(f"   1️⃣ Running pdfplumber extraction (Primary)...")
    text_plumber, pages_plumber = extract_pdf_with_pdfplumber(pdf_path, output_txt=None)
    
    # 2. Run pymupdf (Secondary)
    print(f"   2️⃣ Running pymupdf extraction (Secondary)...")
    text_pymupdf, pages_pymupdf = extract_with_pymupdf(pdf_path)
    
    # 3. Compare Claims
    claim_pattern = r'W\d{2}IN\d{5}|\d{6}B'
    claims_plumber = set(re.findall(claim_pattern, text_plumber))
    claims_pymupdf = set(re.findall(claim_pattern, text_pymupdf))
    
    missing_in_plumber = claims_pymupdf - claims_plumber
    
    print(f"   ✓ Comparison:")
    print(f"      pdfplumber found: {len(claims_plumber)} claims")
    print(f"      pymupdf    found: {len(claims_pymupdf)} claims")
    
    extraction_info = {
        "primary_method": "pdfplumber",
        "secondary_method": "pymupdf",
        "fallback_used": False,
        "final_method": "pdfplumber",
        "claims_plumber": len(claims_plumber),
        "claims_pymupdf": len(claims_pymupdf),
        "recovered_claims": []
    }
    
    if missing_in_plumber:
        print(f"   ⚠️ Primary extraction missed {len(missing_in_plumber)} claims: {sorted(list(missing_in_plumber))}")
        print(f"   🩹 Applying Smart Recovery (appending missing data)...")
        
        extraction_info['fallback_used'] = True
        extraction_info['final_method'] = 'pdfplumber + pymupdf_recovery'
        extraction_info['recovered_claims'] = list(missing_in_plumber)
        
        # Build recovery section
        recovery_text = "\n\n" + "="*80 + "\n"
        recovery_text += "RECOVERY DATA (Secondary Extraction)\n"
        recovery_text += "The following content is extracted using PyMuPDF to capture missing claims.\n"
        recovery_text += "="*80 + "\n\n"
        
        added_content = False
        # Map pymupdf pages by number
        pymupdf_map = {p['page_number']: p['text'] for p in pages_pymupdf}
        
        # Scan through pymupdf pages to find the missing claims
        pages_with_missing_content = set()
        for page_data in pages_pymupdf:
            p_text = page_data['text']
            # Check if this page contains any missing claim
            if any(missing in p_text for missing in missing_in_plumber):
                pages_with_missing_content.add(page_data['page_number'])
        
        # Append content from those pages
        for page_num in sorted(list(pages_with_missing_content)):
             p_text = pymupdf_map.get(page_num, "")
             recovery_text += f"\n--- RECOVERED CONTENT (Page {page_num}) ---\n"
             recovery_text += p_text + "\n"
             added_content = True
        
        if added_content:
            text_plumber += recovery_text
            print(f"   ✅ Appended recovery data from {len(pages_with_missing_content)} pages")
    
    else:
        print(f"   ✅ Primary extraction captured all claims found by secondary method")
    
    # Save to file if requested
    if output_txt:
        with open(output_txt, 'w', encoding='utf-8') as f:
            f.write(text_plumber)
        print(f"✓ Extraction saved to: {output_txt}")
    
    return text_plumber, pages_plumber, extraction_info




# CLI Usage
if __name__ == "__main__":
    import sys
    import os
    
    if len(sys.argv) < 2:
        print("Usage: python pdf_plumber.py <pdf_file> [output_txt] [--hybrid]")
        sys.exit(1)
    
    pdf_file = sys.argv[1]
    output_txt = None
    use_hybrid = False
    
    # Parse arguments
    for arg in sys.argv[2:]:
        if arg == "--hybrid":
            use_hybrid = True
        elif not output_txt:
            output_txt = arg
    
    if not os.path.exists(pdf_file):
        print(f"Error: File not found: {pdf_file}")
        sys.exit(1)
    
    print(f"\n{'='*80}")
    print(f"PDF TEXT EXTRACTION")
    print(f"{'='*80}")
    print(f"Input:  {pdf_file}")
    if output_txt:
        print(f"Output: {output_txt}")
    print(f"Mode:   {'Hybrid (with fallback)' if use_hybrid else 'Standard (pdfplumber only)'}")
    print(f"{'='*80}\n")
    
    if use_hybrid:
        all_text, pages_metadata, extraction_info = extract_pdf_hybrid(pdf_file, output_txt)
        
        print(f"\n{'='*80}")
        print(f"EXTRACTION INFO")
        print(f"{'='*80}")
        print(f"Method used:     {extraction_info.get('final_method')}")
        print(f"Fallback used:   {extraction_info.get('fallback_used')}")
        print(f"Claims (primary):   {extraction_info.get('claims_plumber')}")
        print(f"Claims (secondary): {extraction_info.get('claims_pymupdf')}")
        if extraction_info.get('recovered_claims'):
             print(f"Recovered:       {len(extraction_info['recovered_claims'])} claims")
    else:
        all_text, pages_metadata = extract_pdf_with_pdfplumber(pdf_file, output_txt)
    
    print(f"\n{'='*80}")
    print(f"EXTRACTION COMPLETE")
    print(f"{'='*80}")
    print(f"Total pages: {len(pages_metadata)}")
    print(f"Total characters: {len(all_text):,}")
    print(f"Average chars/page: {len(all_text)//len(pages_metadata) if pages_metadata else 0:,}")
    
    # Show sample
    if all_text:
        print(f"\nFirst 500 characters:")
        print(f"{'-'*80}")
        print(all_text[:500])
        print(f"{'-'*80}")
    
    if not output_txt:
        print(f"\n💡 Tip: Specify output file to save extracted text")
        print(f"   Example: python pdf_plumber.py {pdf_file} output.txt --hybrid")
    
    print()
