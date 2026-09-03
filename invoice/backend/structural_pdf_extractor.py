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
                r"(\n.*Invoice Summary)"
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
    print("  [Debug] Calling v3.extract_text_from_pdf_improved...")
    text = v3.extract_text_from_pdf_improved(pdf_path)
    print(f"  [Debug] Text extraction complete. Length: {len(text)} chars.")
    
    # 2. Segment text using structural logic
    chunks = map_and_segment_text(text)
    
    all_line_items = []
    final_header = {field: None for field in v3.REQUIRED_FIELDS if field in ["INV_DATE", "INV_NUMBER", "BILLING_PERIOD", "GROUP_NUMBER", "PRICING_ADJUSTMENT"]}
    
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
        if chunk_type == "summary":
            # Just extract header fields from summary part
            # We use a smaller context for summary to avoid confusion
            page_data = v3.extract_fields_with_llm(chunk_text, client, f"summary_page_{page_num}")
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
                    "\n[CRITICAL INSTRUCTIONS FOR MULTI-BENEFIT EXTRACTION]"
                    "\n1. This is a GIS 23 Restaurant Services invoice with PAYROLL FILE detail pages."
                    "\n2. Each employee has MULTIPLE benefit types (Dental, Vision, STD, LTD, Life Insurance, etc.)."
                    "\n3. EXTRACT EACH BENEFIT AS A SEPARATE LINE ITEM - do NOT consolidate or aggregate."
                    "\n4. The 'Product Name' column contains the benefit type - map this to PLAN_NAME exactly as shown."
                    "\n5. The 'Premium Amount' column is the premium for THAT SPECIFIC benefit only."
                    "\n6. If you see columns like 'Employee Portion' and 'Employer Portion', use 'Employee Portion' for CURRENT_PREMIUM."
                    "\n7. Extract EVERY row in the table - each row is a separate benefit enrollment."
                    "\n8. The 'Volume' column may contain coverage amounts for life insurance."
                    "\n9. Do NOT skip rows, do NOT aggregate rows, do NOT consolidate different benefit types."
                    "\n10. Typical structure: One employee may have 5-8 separate rows for different benefits."
                    "\n11. CRITICAL: To save space, do NOT include null or empty fields in the JSON object for each line item."
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
            
            # Extract line items
            page_data = v3.extract_fields_with_llm(chunk_text + prompt_hint, client, f"detail_page_{page_num}")
            
            # Vertical fallback for reports or details
            if not page_data.get("LINE_ITEMS") and len(chunk_text) > 100:
                 print(f"    -> [Layer] Vertical fallback triggered for {chunk_type} chunk...")
                 # (Implementation of vertical fallback would go here or call v3 logic)
        
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
