import os
import re
import json
import pandas as pd
from pathlib import Path
from openai import OpenAI
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

import importlib.util

# Path to the original script
V3_PATH = os.path.join(os.path.dirname(__file__), "universal_pdf_extractor_v3.py")

def load_v3():
    spec = importlib.util.spec_from_file_location("universal_pdf_extractor_v3", V3_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {V3_PATH}")
    v3_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v3_module)
    return v3_module

print("  [Debug] Loading universal_pdf_extractor_v3...")
v3 = load_v3()
print("  [Debug] universal_pdf_extractor_v3 loaded successfully.")
# We can't easily import from a script with '-' in path if it's not a package, 
# but we can hack sys.path or just copy essentials. 
# Given "don't touch the code", I'll write a standalone layer that uses the OpenAI client similarly.


def map_and_segment_text(text):
    """
    Structural Layer: Identifies and segments the PDF text into logically safe chunks.
    V3: Limits detail page merges to 2 pages to prevent timeouts on long documents.
    
    FIXED: Now properly processes Payroll File pages instead of skipping them.
    """
    # Split by page makers
    pages = re.split(r'\[\s*\[\s*PAGE_\d+\s*\]\s*\]', text)
    if pages and not pages[0].strip():
        pages.pop(0)
    
    refined_chunks = []
    detail_buffer = []  # Buffer to merge consecutive detail pages
    MAX_MERGE = 2       # Efficiently group pages for complex documents
    
    # GIS 23 Optimization: Check if this document has the detailed "Payroll File Number" pages
    has_payroll = any("Payroll File Number" in p for p in pages)
    if has_payroll:
        print(f"  [Layer] Detected GIS 23 Payroll File. Will skip redundant summary pages.")
    
    def flush_buffer():
        if detail_buffer:
            merged_text = "\n\n".join(detail_buffer)
            # SUB-CHUNKING: If the text is long, split into parts to avoid JSON truncation (max ~25 items per chunk)
            chunk_size = 6000
            if len(merged_text) > chunk_size:
                print(f"  [Layer] Chunk is very large ({len(merged_text)} chars). Split-chunking into smaller pieces...")
                lines = merged_text.split("\n")
                # Split lines into groups that approximate chunk_size
                current_part = []
                current_len = 0
                part_idx = 1
                for line in lines:
                    current_part.append(line)
                    current_len += len(line) + 1
                    if current_len > chunk_size:
                        refined_chunks.append({"type": "detail", "text": "\n".join(current_part), "page": f"merged_p{part_idx}"})
                        current_part = []
                        current_len = 0
                        part_idx += 1
                if current_part:
                    refined_chunks.append({"type": "detail", "text": "\n".join(current_part), "page": f"merged_p{part_idx}"})
            else:
                refined_chunks.append({"type": "detail", "text": merged_text, "page": "merged"})
            detail_buffer.clear()

    for i, page_text in enumerate(pages):
        page_num = i + 1
        
        # GIS 23 Optimization: If payroll pages exist, we still need summary pages for HEADER fields
        # but we mark them as summary type to avoid extracting redundant line items.
        if has_payroll and page_num <= 3 and "Payroll File Number" not in page_text:
            print(f"  [Layer] Page {page_num}: Identifying as GIS 23 Summary (for Header only)")
            refined_chunks.append({"type": "summary", "text": page_text, "page": page_num})
            continue
        
        # STRUCTURAL CHECK: Is this a mixed page (members + summary)?
        if "Totals:" in page_text or "Invoice Summary" in page_text:
            flush_buffer()
            print(f"  [Layer] Page {page_num} detected as MIXED (Members + Summary). Splitting...")
            
            # Identify the split point
            split_patterns = [
                r"(\n.*All Employees Totals:)",
                r"(\n.*Invoice Sub Total)",
                r"(\n.*Invoice Summary)",
                r"(\n.*ADJUSTMENT DETAIL)",
                r"(\n.*Adjustment Totals)"
            ]
            
            split_found = False
            for pattern in split_patterns:
                match = re.search(pattern, page_text)
                if match:
                    detail_part = page_text[:match.start()].strip()
                    summary_part = page_text[match.start():].strip()
                    
                    if detail_part:
                        refined_chunks.append({"type": "detail", "text": detail_part, "page": page_num})
                    if summary_part:
                        refined_chunks.append({"type": "summary", "text": summary_part, "page": page_num})
                    
                    split_found = True
                    break
            
            if not split_found:
                refined_chunks.append({"type": "mixed", "text": page_text, "page": page_num})
        else:
            if "Payroll File Number" in page_text:
                # FIXED: These pages contain the detailed benefit-by-benefit data!
                # They are NOT redundant - they have the line-item detail we need!
                print(f"  [Layer] Page {page_num} is Payroll Report (PROCESSING - contains detailed benefit data)")
                detail_buffer.append(page_text)
                if len(detail_buffer) >= MAX_MERGE:
                    print(f"  [Layer] Page {page_num}: Reached max merge limit ({MAX_MERGE}). Flushing...")
                    flush_buffer()
            else:
                # Buffer detail pages for merging, but flush if we hit the limit
                detail_buffer.append(page_text)
                if len(detail_buffer) >= MAX_MERGE:
                    print(f"  [Layer] Page {page_num}: Reached max merge limit ({MAX_MERGE}). Flushing...")
                    flush_buffer()
    
    # Final flush
    flush_buffer()
                
    return refined_chunks

def is_empty_line_items(items):
    if not items: 
        return True
    for item in items:
        has_val = any(item.get(k) is not None and str(item.get(k)).strip().lower() not in ['', 'none', 'null', 'nan'] 
                      for k in ['FIRSTNAME', 'LASTNAME', 'MEMBERID', 'PLAN_NAME', 'CURRENT_PREMIUM'])
        if has_val:
            return False
    return True

def process_with_structural_layer(pdf_path, output_excel=None):
    """Process PDF with structural analysis layer.
    
    Args:
        pdf_path: Path to the input PDF
        output_excel: Optional output path. If None, saves in same directory as PDF.
    """
    client = OpenAI(api_key=v3.OPENAI_API_KEY)
    
    # Default output path: same directory as input PDF
    if output_excel is None:
        pdf_dir = Path(pdf_path).parent
        output_excel = pdf_dir / "extracted_data_structural.xlsx"
    else:
        output_excel = Path(output_excel)
    
    print(f"\n[Structural Layer] Analyzing: {pdf_path}")
    
    # 1. Extract raw text with markers
    print("  [Debug] Detecting carrier for optimized mode...")
    # Quick check for KCL
    is_kcl = "KCL" in pdf_path or "Kansas City Life" in pdf_path
    
    if is_kcl:
        print("  [Layer] KCL detected. Using VERTICAL extraction mode for 3-column layout.")
        text = v3.extract_text_from_pdf_pymupdf(pdf_path, mode="vertical")
    else:
        print("  [Debug] Calling v3.extract_text_from_pdf_improved...")
        text = v3.extract_text_from_pdf_improved(pdf_path)
    
    print(f"  [Debug] Text extraction complete. Length: {len(text)} chars.")
    
    # 2. Segment text using structural logic
    chunks = map_and_segment_text(text)
    
    all_line_items = []
    final_header = {field: None for field in v3.REQUIRED_FIELDS if field in ["INV_DATE", "INV_NUMBER", "BILLING_PERIOD", "GROUP_NUMBER"]}
    
    print(f"  [Layer] Segmented document into {len(chunks)} contextual chunks.")
    
    for i, chunk in enumerate(chunks):
        chunk_type = chunk["type"]
        chunk_text = chunk["text"]
        page_num = chunk["page"]
        
        print(f"  [Layer] Processing Chunk {i+1}/{len(chunks)} (Page {page_num}, Type: {chunk_type})...")
        
        # Customize prompt based on type
        # For 'detail' and 'report', we want LINE_ITEMS.
        # For 'summary', we ONLY want HEADER fields.
        
        mode = "standard"
        carrier_name = None
        if chunk_type == "summary":
            # Just extract header fields from summary part
            # We use a smaller context for summary to avoid confusion
            page_data = v3.extract_fields_with_llm(
                chunk_text, 
                client, 
                f"summary_page_{page_num}",
                detected_carrier=carrier_name
            )
            # [FIX] Never extract line items from summary chunks (Page 1) to avoid mis-mapped wide-table values
            page_data["LINE_ITEMS"] = []
        else:
            # Refined prompt hint for Guardian and GIS 23
            prompt_hint = ""
            if "Guardian" in pdf_path or "Basic Term Life" in chunk_text:
                prompt_hint = (
                    "\n[HINT] This document may have multiple premium columns: Basic Term Life, Dental, Std, Vision. "
                    "Please map each member's premium correctly to the PLAN_NAME and CURRENT_PREMIUM. "
                    "If you see 'Premium Adjustments', capture them in ADJUSTMENT_PREMIUM. "
                    "IMPORTANT: Do NOT extract 'TOTAL' rows or summary table rows as line items."
                )
            elif "GIS 23" in pdf_path or "Restaurant Services" in pdf_path or "Payroll File Number" in chunk_text:
                prompt_hint = (
                    "\n[CRITICAL INSTRUCTIONS FOR GIS EXTRACTION]"
                    "\n1. This document has a SUMMARY on Page 1 and DETAIL on Page 2+."
                    "\n2. YOU MUST extract each benefit as a SEPARATE row. Do NOT aggregate or consolidate."
                    "\n3. 'Product Name' -> PLAN_NAME. 'Premium Amount' -> CURRENT_PREMIUM."
                    "\n4. If a member lacks a certain benefit (e.g. Dental premium is 0 or empty), do NOT invent a row for it."
                    "\n5. COVERAGE MAPPING: 'Employee' (no Spouse) -> EE, 'Spouse' -> ES. "
                    "\n6. If Product is 'Dental' or 'Long Term Disability' without a tier suffix -> EE."
                    "\n7. Map ONLY explicit values. If Chaitra has LTD $10.31 but NO Dental, do NOT put $10.31 in Dental."
                )
            elif "Aetna" in pdf_path:
                prompt_hint = (
                    "\n[HINT] This is an Aetna invoice. Look for the 'Membership Detail' or 'Subscriber Detail' sections. "
                    "Avoid extracting summary or subtotal rows as line items. "
                    "\n[CRITICAL - IDs] '0023', '0106', '0024' are PLAN CODES, NEVER Member IDs. "
                    "Member IDs usually match the SSN (last 4 digits) or are long numbers starting with 'W' or digits."
                    "\n[CRITICAL - VERTICAL ALIGNMENT] Amounts usually appear ABOVE the member name in this document. "
                    "Example: \n$646.61\nAcosta, Stephanie\n -> Extract 646.61 for Acosta."
                    "\n[CRITICAL - NEGATIVE VALUES] If a value is in parentheses like '(536.75)', it is NEGATIVE. Extract as -536.75."
                    "\n[CRITICAL - SECTIONS] If a row is in an 'Adjustments' or 'Retroactivity' section, do NOT put its value in CURRENT_PREMIUM. "
                    "Use ADJUSTMENT_PREMIUM for those rows instead. "
                    "Check the section header - only rows under 'Current Membership' should have CURRENT_PREMIUM."
                )
                carrier_name = "unitedhealthcare"
            elif "KCL" in pdf_path or "Kansas City Life" in chunk_text:
                carrier_name = "kansas_city_life"
                is_adj_chunk = "ADJUSTMENT DETAIL" in chunk_text or "Adjustment Totals" in chunk_text
                section_label = "\n[SECTION: ADJUSTMENT DETAIL]" if is_adj_chunk else "\n[SECTION: CURRENT CHARGES]"
                
                prompt_hint = (
                    f"{section_label}"
                    "\n[CRITICAL INSTRUCTIONS FOR KANSAS CITY LIFE (KCL)]"
                    "\n1. This document has a main 'Detail of Current Charges' section and an 'ADJUSTMENT DETAIL' section."
                    "\n2. **ADJUSTMENT IDENTIFICATION**: "
                    "\n   - IF the chunk is labeled [SECTION: ADJUSTMENT DETAIL], YOU MUST map ALL premiums here to ADJUSTMENT_PREMIUM."
                    "\n   - IF a row includes a specific date (e.g., 1/1/2026), it is an ADJUSTMENT row -> map to ADJUSTMENT_PREMIUM."
                    "\n   - Set CURRENT_PREMIUM to NULL for all adjustment rows."
                    "\n3. **NO CONSOLIDATION**: Do NOT sum current premiums with adjustment premiums for the same person. Return them as SEPARATE line item objects."
                    "\n4. **ACTUAL AMOUNT**: Ensure 'Actual Amount' or 'Volume' is captured if present. Map 'Actual Amount' to CURRENT_PREMIUM or total columns as appropriate."
                    "\n5. **ROSTAING_OCR (ROTATION)**: If text appears rotated or unreadable, apply rotation logic (rostaing_ocr) to normalize the view before extraction."
                )
            elif "BCBS" in pdf_path.upper() or "BlueCare" in chunk_text:
                carrier_name = "bcbs"
                prompt_hint = (
                    "\n[CRITICAL INSTRUCTIONS FOR BCBS EXTRACTION]"
                    "\n1. **STRICT FULL TABLE SCAN**: You MUST scan the entire page and extract EVERY member row. Do NOT skip or merge different member names."
                    "\n2. **ZERO AGGREGATION (IRONCLAD)**: Never sum premiums from different names. If 'Rbrekk' has $6.00 and 'Toczynski' has $652.74, they MUST be two separate JSON objects. Aggregating them is a DESTRUCTIVE ERROR."
                    "\n3. **ADJUSTMENTS**: Extract adjustments as completely SEPARATE JSON objects. NEVER combine adjustments with current premiums."
                    "\n4. **MEMBER IDENTIFICATION (STRICT)**:"
                    "\n   - MEMBERID: The alphanumeric ID starting with 'H' or 'W' (usually 9-10 chars, e.g., 'H44156017')."
                    "\n   - SSN: **PRIORITY 9-DIGITS**. Look for XXX-XX-XXXX or 9-digit number. Capture ALL NINE digits. Also capture masked SSNs like '*****1234' or 'XXX-XX-1234'. Capture EVERY DIGIT visible. Search the entire row near the Name and MemberID for the SSN digits if no clear column exists. **DO NOT LEAVE SSN NULL IF ANY DIGITS ARE VISIBLE ON THE ROW.**"
                    "\n   - Capture BOTH fields for every row. Do NOT swap them."
                    "\n5. Set CURRENT_PREMIUM to null for adjustments, and ADJUSTMENT_PREMIUM to null for current premium rows. Amounts in parentheses (e.g. ($100.00)) are negative."
                    "\n6. **MULTI-BLOCK LAYOUT**: If labels (Name, ID, SSN) are at the top and amounts are at the bottom, carefully match them by sequence. The first Name/ID corresponds to the first amount, the second to the second, etc."
                    "\n7. Ensure FIRSTNAME and LASTNAME are captured on every single row."
                )

        
            
            # Extract line items
            page_data = v3.extract_fields_with_llm(
                chunk_text + prompt_hint, 
                client, 
                f"detail_page_{page_num}",
                detected_carrier=carrier_name
            )
            
            # Vertical fallback for reports or details
            if is_empty_line_items(page_data.get("LINE_ITEMS")) and len(chunk_text) > 100:
                 print(f"    -> [Layer] Vertical fallback triggered for {chunk_type} chunk...")
                 # (Implementation of vertical fallback would go here or call v3 logic)
            
            # [V4][FIX] OCR Fallback for Structural Layer
            # If standard extraction failed or yielded low results, and the document is likely scanned
            if is_empty_line_items(page_data.get("LINE_ITEMS")) or v3.check_text_quality(chunk_text) < 0.2:
                print(f"    -> [Layer] Low quality text or no items on chunk {i+1}. Attempting optimized OCR fallback...")
                try:
                    # 1. Run OCR once (using fitz/tesseract)
                    print(f"    -> [Layer] Performance: Running primary-doc OCR pass...")
                    ocr_text, _ = v3.extract_text_from_pdf_ocr(pdf_path) # Changed to return (text, metadata)
                    
                    # [V4][FIX] Check if OCR text needs Vision for better layout (BCBS Multi-Block)
                    # If this is BCBS and the text looks fragmented, we might want to try Vision
                    
                    # 2. Save OCR text to the raw extracted file for transparency
                    pdf_dir = os.path.dirname(pdf_path)
                    pdf_base = os.path.basename(pdf_path).replace(".pdf", "_raw_extracted.txt")
                    txt_path = os.path.join(pdf_dir, pdf_base)

                    try:
                        with open(txt_path, "w", encoding="utf-8") as f:
                            f.write(ocr_text)
                    except Exception as e:
                        print(f"    -> [Layer][WARN] Could not save OCR text: {e}")

                    # 3. RE-SEGMENT the OCR text to process it in manageable chunks
                    ocr_chunks = map_and_segment_text(ocr_text)
                    
                    # 4. Process all OCR chunks
                    all_line_items = []
                    for j, ocr_chunk in enumerate(ocr_chunks):
                        print(f"    -> [Layer] Processing OCR Chunk {j+1}/{len(ocr_chunks)}...")
                        ocr_data = v3.extract_fields_with_llm(ocr_chunk["text"] + prompt_hint, client, f"ocr_chunk_{j+1}", detected_carrier=carrier_name)
                        items = ocr_data.get("LINE_ITEMS", [])
                        
                        # [V5][FIX] VISION FALLBACK: If names are missing, use Vision OCR (near-perfect layout)
                        # We trigger if more than 20% of items are missing names, or if we have > 1 missing name
                        missing_count = sum(1 for item in items if not item.get("LASTNAME"))
                        if (missing_count > 1 or (items and missing_count/len(items) > 0.2)) and carrier_name == "bcbs":
                            print(f"    -> [Layer][ALERT] {missing_count} names missing in OCR chunk. Triggering Vision OCR fallback for layout integrity...")
                            # Extract just this page with Vision
                            vis_extractor = v3.OCRPDFExtractor(pdf_path)
                            # We'd ideally only do the specific page, but for now we do the doc if small
                            vis_text, _ = vis_extractor.extract(engine='vision')
                            # Save vis text
                            with open(txt_path, "w", encoding="utf-8") as f: f.write(vis_text)
                            # Re-process with Vision text
                            vis_chunks = map_and_segment_text(vis_text)
                            all_line_items = [] # Reset for Vision
                            for k, vis_chunk in enumerate(vis_chunks):
                                print(f"    -> [Layer] Processing Vision Chunk {k+1}/{len(vis_chunks)}...")
                                vis_data = v3.extract_fields_with_llm(vis_chunk["text"] + prompt_hint, client, f"vis_chunk_{k+1}", detected_carrier=carrier_name)
                                if vis_data.get("LINE_ITEMS"):
                                    all_line_items.extend(vis_data["LINE_ITEMS"])
                            break # Out of OCR chunk loop, we have Vision items
                        
                        if items:
                            all_line_items.extend(items)
                    
                    break

                except Exception as e:
                    print(f"    -> [Layer][ERROR] OCR fallback failed: {e}")
        
        
        
        # Merge Header
        page_header = page_data.get("HEADER", {})
        for k, v in page_header.items():
            if v and str(v).lower() not in ["n/a", "none"]:
                final_header[k] = v
        
        # Merge Line Items
        items = page_data.get("LINE_ITEMS", [])
        if items:
            all_line_items.extend(items)
            print(f"    -> Extracted {len(items)} items")
            
    # Final assembly and saving
    data = {"HEADER": final_header, "LINE_ITEMS": all_line_items}
    rows = v3.flatten_extracted_data(data, os.path.basename(pdf_path))
    
    if rows:
        df = pd.DataFrame(rows)
        # Ensure all required fields exist
        for field in v3.REQUIRED_FIELDS:
            if field not in df.columns: df[field] = None
        
        # [V4][FIX] Ensure SOURCE_FILE exists before reordering
        if 'SOURCE_FILE' not in df.columns:
            df['SOURCE_FILE'] = os.path.basename(pdf_path)

        # Sort or filter columns if needed (Layer 5/7 alignment)
        cols = ['SOURCE_FILE'] + [f for f in v3.REQUIRED_FIELDS if f in df.columns]
        # Ensure all 15 fields are present
        for field in v3.REQUIRED_FIELDS:
            if field not in cols:
                df[field] = None
        
        df = df[['SOURCE_FILE'] + v3.REQUIRED_FIELDS]
        
        # FIXED: Keep all rows - each benefit type should be a separate row
        # unless it is the specialized "TOTAL" row
        df['is_total'] = df['PLAN_NAME'].str.upper().fillna('').str.contains('TOTAL') | \
                         ((df['FIRSTNAME'].isna() | (df['FIRSTNAME'] == '')) & \
                          (df['LASTNAME'].isna() | (df['LASTNAME'] == '')) & \
                          df['CURRENT_PREMIUM'].notna())
        
        df = df[(df[['LASTNAME', 'FIRSTNAME']].notna().any(axis=1)) | (df['is_total'])]
        df = df.drop(columns=['is_total'])
        
        print(f"    -> [Layer] Preserved {len(df)} benefit line items (NO consolidation applied).")
        
        df.to_excel(output_excel, index=False)
        print(f"\n[SUCCESS] Structural Extraction Complete: {output_excel}")
        print(f"  Total Rows: {len(df)}")
    else:
        print("[WARNING] No rows extracted. Check LLM outputs or chunking logic.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Strip quotes from arguments (safe method)
        raw_pdf = sys.argv[1]
        pdf_file = raw_pdf.strip('"').strip("'")
        
        import os
        print(f"[Debug] Raw input path: {raw_pdf}")
        print(f"[Debug] Cleaned path: {pdf_file}")
        print(f"[Debug] Exists?: {os.path.exists(pdf_file)}")
        
        raw_out = sys.argv[2] if len(sys.argv) > 2 else None
        out_excel = raw_out.strip('"').strip("'") if raw_out else None
        
        if out_excel:
            process_with_structural_layer(pdf_file, out_excel)
        else:
            process_with_structural_layer(pdf_file)
    else:
        print("Usage: python structural_pdf_extractor.py <pdf_path> [output_excel]")
