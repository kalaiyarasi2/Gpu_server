"""
Improved PDF Invoice Data Extraction to Excel using LLM
Uses pdfplumber for better text extraction and OpenAI API for intelligent field extraction
"""

import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
import json

# Fix for pytesseract compatibility in Python 3.12+
import pkgutil
if not hasattr(pkgutil, 'find_loader'):
    import importlib.util
    pkgutil.find_loader = lambda name: importlib.util.find_spec(name)

import pandas as pd
from pathlib import Path
from openai import OpenAI
import pdfplumber
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
import io
import re
from typing import Dict, List, Optional


def clean_ocr_noise(text: str) -> str:
    """
    Clean common OCR noise from extracted text
    
    Args:
        text: Raw extracted text
        
    Returns:
        Cleaned text
    """
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            cleaned_lines.append("")
            continue
            
        # Skip lines that are just single characters (except likely page numbers or markers)
        # Protect page markers like [[PAGE_1]]
        if "[[PAGE_" in line:
            cleaned_lines.append(line)
            continue
            
        if len(line) < 2 and not line.isdigit():
            continue
            
        # Skip lines that are mostly punctuation/symbols (but ignore spaces in length)
        alnum_count = sum(c.isalnum() for c in line)
        non_space_len = len(line.replace(" ", ""))
        if non_space_len > 0 and alnum_count / non_space_len < 0.4:
            continue
            
        # Remove isolated single characters at start/end of line (common OCR artifacts)
        # e.g., "8 3140 W KENNEDY..." -> "3140 W KENNEDY..."
        # e.g., "...FL 33609 a" -> "...FL 33609"
        line = re.sub(r'^[^\w\s]\s+', '', line) # Remove leading symbol spaces
        line = re.sub(r'\s+[^\w\s]$', '', line) # Remove trailing symbol spaces
        
        # Remove isolated single digits/chars at limits if they look like noise
        # (e.g. "3 CMPLA LLC 5")
        if re.search(r'^\d\s+[A-Za-z]', line): 
            line = re.sub(r'^(\d)\s+', r'\1 ', line)
            
        # V3: Virtual Pipes - Replace large whitespace gaps with | 
        # This prevents the LLM from losing track of columns in landscape/wide docs
        line = re.sub(r'\s{3,}', ' | ', line)
        
        cleaned_lines.append(line)
        
    return '\n'.join(cleaned_lines)


def check_text_quality(text: str) -> float:
    """
    Check the quality of extracted text by calculating alphanumeric ratio.
    Returns a score between 0.0 and 1.0.
    """
    if not text:
        return 0.0
    
    # Remove synthetic page markers from quality assessment
    clean_meta = re.sub(r'\[\[PAGE_\d+\]\]', '', text)
    
    # Remove whitespace
    clean = re.sub(r'\s+', '', clean_meta)
    if not clean or len(clean) < 20: # If very little text remains, quality is effectively 0
        return 0.0
        
    # Count alphanumeric
    alnum = sum(c.isalnum() for c in clean)
    return alnum / len(clean)

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Define the fields to extract (Standardized 15 fields for 7-Layer Pipeline)
REQUIRED_FIELDS = [
    "INV_DATE",
    "INV_NUMBER",
    "BILLING_PERIOD",
    "LASTNAME",
    "FIRSTNAME",
    "MIDDLENAME",
    "SSN",
    "POLICYID",
    "MEMBERID",
    "PLAN_NAME",
    "PLAN_TYPE",
    "COVERAGE",
    "CURRENT_PREMIUM",
    "ADJUSTMENT_PREMIUM",
    "PRICING_ADJUSTMENT"
]


def extract_text_from_pdf_pymupdf(pdf_path: str, mode: str = "standard") -> str:
    """
    Extract text content from a PDF file using PyMuPDF (better for complex PDFs)
    
    Args:
        pdf_path: Path to the PDF file
        mode: Extraction mode ("standard" or "vertical")
        
    Returns:
        Extracted text as string
    """
    try:
        text: str = ""
        doc = fitz.open(pdf_path)
        print(f"  Total pages: {len(doc)}")
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            if mode == "vertical":
                # Preserves column integrity by extracting blocks of text sequentially
                blocks = page.get_text("blocks")
                # Sort blocks: top-to-bottom, then left-to-right (if same vertical level)
                blocks.sort(key=lambda b: (b[1], b[0]))
                page_text = "\n".join([b[4] for b in blocks])
            else:
                # Standard horizontal flow
                page_text = page.get_text()
            
            if page_text:
                text = text + f"\n[[PAGE_{page_num + 1}]]\n"
                text = text + page_text + "\n"
        
        doc.close()
        
        # Show preview of extracted text
        if text.strip():
            print(f"  [OK] Extracted {len(text)} characters")
            print(f"  Preview (first 500 chars):\n{text[:500]}\n")
        else:
            print(f"  [WARNING] No text extracted from {pdf_path}")
            
        return text
    except Exception as e:
        print(f"  [ERROR] Error extracting text from {pdf_path}: {e}")
        return ""


def extract_text_from_pdf_ocr(pdf_path: str) -> str:
    """
    Extract text content from a PDF file using OCR (Tesseract)
    Renders PDF pages to images first, then applies OCR.
    Now includes 'Optical Mirror Fix' to handle reversed text by flipping the image.
    """
    try:
        text: str = ""
        doc = fitz.open(pdf_path)
        print(f"  [OCR] Total pages: {len(doc)}")
        
        for page_num in range(len(doc)):
            print(f"  [OCR] Processing page {page_num + 1}/{len(doc)}...")
            page = doc[page_num]
            
            # Detect Landscape
            is_landscape = page.rect.width > page.rect.height
            if is_landscape:
                print(f"  [OCR][V3] Page {page_num + 1} is LANDSCAPE mode.")
            
            # Render page to image
            zoom = 4.0 
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            
            # Step 1: Preliminary fast OCR to detect anomalies
            # Use PSM 4 for landscape (single column of varying sizes), PSM 3 for portrait
            tess_config = "-c preserve_interword_spaces=1"
            if is_landscape:
                tess_config = "--psm 4 " + tess_config
            else:
                tess_config = "--psm 3 " + tess_config
                
            page_text = pytesseract.image_to_string(img, config=tess_config)
            
            # Step 2: Detect orientation/mirroring anomalies
            # Use high-confidence normal keywords to score orientation
            normal_keywords = ["invoice", "date", "description", "premium", "member", "blue", "shield", "total"]
            def get_normal_score(txt: str) -> int:
                count = 0
                t = txt.lower()
                for k in normal_keywords:
                    if k in t: count += 1
                return count

            raw_score = get_normal_score(page_text)
            # If we see mirrored patterns OR no normal text, it's an anomaly
            is_anomaly = detect_reversed_text(page_text) or raw_score < 1
            
            if is_anomaly:
                print(f"  [OCR][V3] Orientation anomaly detected on page {page_num + 1}. Attempting auto-correction...")
                
                # Option A: Flip Horizontal (Mirroring)
                img_mirrored = img.transpose(Image.FLIP_LEFT_RIGHT)
                text_mirrored = pytesseract.image_to_string(img_mirrored)
                score_mirrored = get_normal_score(text_mirrored)
                
                # Option B: Rotate 180 (Upside Down)
                img_rotated = img.rotate(180)
                text_rotated = pytesseract.image_to_string(img_rotated)
                score_rotated = get_normal_score(text_rotated)
                
                # Pick the winner based on keyword scoring
                if score_mirrored > raw_score and score_mirrored >= score_rotated:
                    print(f"  [OCR][V3] Page {page_num + 1} corrected via Flip H.")
                    page_text = text_mirrored
                elif score_rotated > raw_score:
                    print(f"  [OCR][V3] Page {page_num + 1} corrected via Rotation 180.")
                    page_text = text_rotated
                else:
                    print(f"  [OCR][V3] Page {page_num + 1} orientation could not be auto-corrected.")
            else:
                print(f"  [OCR][V3] Page {page_num + 1} orientation verified as normal.")
            
            # Always add page markers even if text is empty to maintain chunk alignment
            text = text + f"\n[[PAGE_{page_num + 1}]]\n"
            if page_text:
                text = text + page_text + "\n"
        
        doc.close()
        return text
    except Exception as e:
        print(f"  [ERROR] OCR Error: {e}")
        return ""


def extract_text_from_pdf_improved(pdf_path: str) -> str:
    """
    Extract text content from a PDF file using pdfplumber (better quality)
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        Extracted text as string
    """
    try:
        text: str = ""
        with pdfplumber.open(pdf_path) as pdf:
            print(f"  Total pages: {len(pdf.pages)}")
            for page_num, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text()
                # Always add page markers even if text is empty to maintain chunk alignment
                text = text + f"\n[[PAGE_{page_num}]]\n"
                if page_text:
                    text = text + page_text + "\n"
        
        # Show preview of extracted text
        if text.strip():
            print(f"  [OK] Extracted {len(text)} characters")
            print(f"  Preview (first 500 chars):\n{text[:500]}\n")
        else:
            print(f"  [WARNING] Warning: No text extracted from {pdf_path}")
            
        return text
    except Exception as e:
        print(f"  [ERROR] Error extracting text from {pdf_path}: {e}")
        return ""

def detect_reversed_text(text: str) -> bool:
    """
    Detect if the text appears to be reversed (mirrored).
    """
    # Use very high-confidence mirrored OCR patterns
    reversed_patterns = [
        "sdioani", "s0iovui", "adiovui", "eciovni", "eciovnu", # INVOICE
        "esos", "szoz", "scoz", "ezos",  # 2025/2026
        "voitaat2", "240ivaa2", "evitatneserpeR",        # ADMINISTRATION / SERVICES / Representative
        "sssal9", "anig", "auie", "anigruoc", "anamuh",   # CROSS / BLUE / Insurance / Humana
        "fih2@", "muimerp", "tnemetats", "gnillib", "rebmun", "etad", "egap", # MEMBERSHIP / UNUM keywords
        "ytnuoc"                         # COUNTRY
    ]
    
    # Remove all whitespace and common punctuation for robust matching
    clean_text = re.sub(r'[^a-zA-Z0-9]', '', str(text)).lower()
    
    match_count = 0
    for pattern in reversed_patterns:
        if pattern in clean_text:
            match_count += 1
            
    return match_count >= 1

def unmirror_text(text: str) -> str:
    """
    Reverse each line of text to fix mirroring issues, but ONLY for pages that look mirrored.
    """
    pages = text.split('--- PAGE')
    fixed_pages = []
    
    for page in pages:
        if not page.strip():
            continue
            
        # Detect if this specific page is mirrored
        if detect_reversed_text(page):
            lines = page.split('\n')
            fixed_lines = [line[::-1] for line in lines]
            fixed_pages.append('--- PAGE' + '\n'.join(fixed_lines))
        else:
            fixed_pages.append('--- PAGE' + page)
            
    return '\n'.join(fixed_pages)

def extract_fields_with_llm(text: str, client: OpenAI, pdf_filename: str = "", mode: str = "standard") -> Dict:
    """
    Extract fields using OpenAI with enhanced 'Discovery' logic and mirrored text awareness
    """
    if not text or not text.strip():
        print(f"  [WARNING] No text to process for {pdf_filename}")
        return {field: None for field in REQUIRED_FIELDS}
    
    # Check for mirroring
    is_mirrored = detect_reversed_text(text)
    if is_mirrored:
        print(f"  [V3][INFO] Detected likely MIRRORED (reversed) text. Applying un-mirroring...")
        text = unmirror_text(text)
        print(f"  [V3][OK] Un-mirrored text preview (first 200 chars):\n{text[:200]}\n")



    # Mode-specific instructions (Does not touch user's strict rules below)
    mode_instructions = ""
    if mode == "vertical":
        mode_instructions = """
### VERTICAL BLOCK RULES:
- The text is in a vertical/stacked format (e.g., Column 1 then Column 2).
- Each member typically starts with their NAME (e.g., "SMITH JOHN").
- AGGREGATE all premiums for a single member into one `CURRENT_PREMIUM` field.
"""

    prompt = f"""You are a professional bank and insurance auditor specializing in complex PDF data recovery (V3).

Extract data from the document text provided below. 

### EXTRACTION MODE: {mode.upper()}
{mode_instructions}

### STRICT ANALYSIS STEP - DO NOT SKIP:
1. **Header Identification**: Find the table containing individuals/members (often called "Membership Detail", "Current Charges", "Subscriber Detail", etc.) and list the EXACT column headers.
2. **Column Mapping**: Map the discovered headers correctly:
   - "Contract No" / "Subscriber ID" / "ID#" / "Member ID" -> `MEMBERID`
   - "Premium" / "Amount" / "Premium Amount" / "Billed" / "Total" -> `CURRENT_PREMIUM`
   - "Adjustment" / "Credit" / "Debit" (ONLY if separate column exists) -> `ADJUSTMENT_PREMIUM`
   - "Product" / "Coverage Type" -> `PLAN_NAME`
   - "Policy Name" / "Plan Description" -> `POLICYID`
3. **Leading Zeros**: Preserve every single zero.
4. **Landscape Awareness**: This text may come from a LANDSCAPE document with dense columns. Ensure you look horizontally across mashed strings (e.g., "$100|ID123") to find all fields.
5. **Aggressive Row Capture**: You MUST extract EVERY individual listed in the main table. Do not skip any rows.


### STRICT EXTRACTION RULES - FOLLOW EXACTLY:

1. **EXPLICIT EXTRACTION ONLY**:
   - Extract ONLY values that are explicitly present in the document. 
   - **DO NOT infer or derive missing fields.**
   - When a field is not explicitly available in the source, return **NULL** rather than guessing.

2. **PLAN_TYPE (BENEFIT TYPE - CRITICAL)**:
   - **Allowed Values**: MEDICAL, DENTAL, VISION, LIFE, STD, LTD, VOLUNTARY
   - **Definition**: The type of insurance benefit provided.
   - **STRICT MAPPING**:
     - **DHM, DPO, GD** -> `PLAN_TYPE`: **DENTAL**
     - **VIS, SV** -> `PLAN_TYPE`: **VISION**
     - **MED, MEDICAL, HMO, PPO** -> `PLAN_TYPE`: **MEDICAL**
   - **STRICT RULE**: This is an independent field and must not be inferred from other fields.

3. **COVERAGE (ENROLLMENT TIER - STRICT)**:
   - **Allowed Values**: **EE** (Employee Only), **ES** (Employee + Spouse), **EC** (Employee + Child), **FAM** (Family)
   - **Definition**: Who is included under the plan for pricing purposes.
   - **STRICT EXTRACTION RULE**: Coverage MUST be extracted directly from a "Coverage" or "Tier" field.
   - **MAPPING (NORMALIZATION)**:
     - "EE+SP", "EE/SP", "EMP+SPOUSE" -> **ES**
     - "EE+CH", "EE/CH", "EMP+CHILD" -> **EC**
     - "EE", "EMP ONLY" -> **EE**
     - "FAM", "FAMILY" -> **FAM**
   - **DO NOT GUESS**: Never infer coverage based on premium amounts.
   - **STRICT FORBIDDEN RULE**: Terms like "IND AGE RATED" or "FAM AGE RATED" are NOT tiers.
   - If no explicit tier is found OR if the tier cannot be mapped to the allowed set: return **NULL**.

4. **ULTRA-STRICT VALIDATION & ANTI-HALLUCINATION**:
   - **EXPLICIT DATA ONLY**: Do NOT create, infer, or generate values. If it's not on the page, it's NULL.
   - **WHOLE ROW VERIFICATION**: Do not validate based only on numeric values. Verify member details, coverage/tier, policy info, and premiums as a consistent unit.
   - **CONSISTENCY CHECK**: Ensure all extracted fields for a row align logicially with the document's structured data.

4. **RELATIONSHIP (INTERNAL ANALYSIS)**:
   - **Definition**: Who the person is (Self, Spouse, Child).
   - **STRICT RULE**: This is an independent field and must not be inferred from other fields. 
   - **RULE**: Use this for identity analysis, but do not include it in the final formatted output.

5. **PREMIUM FIELDS (STRICT DEFINITIONS)**:
   - **CURRENT_PREMIUM**: Maps to the recurring base premium for the current period.
   - **ADJUSTMENT_PREMIUM**: Maps to retroactive or corrective amounts.
   - **PRICING_ADJUSTMENT**: Strictly for explicit numeric rate changes. If not explicitly stated, return **NULL**.

6. **IDENTIFIER MAPPING (IRONCLAD RULE)**:
   - **MEMBERID**: From "EE #", "Member ID", or "Policy Name". Preserve zeros.
   - **SSN**: From "SSN" column. **DIGITS ONLY** (e.g. "****_7635" -> "7635").
   - **MANDATORY**: You MUST extract masked digits. Capture any visible numbers.
   - **POLICYID**: Map from "Policy Name" if applicable.

7. **PRICING_MODEL (INTERNAL ANALYSIS)**:
   - **Definition**: Captures descriptors like "FAM AGE RATED" or "COMMUNITY RATED".
   - **RULE**: Use this to handle rating text without polluting `PLAN_TYPE`. Do not include in final output.
   





### NAME FORMATTING RULES:
- **Consistency**: Look for a pattern in the document (usually all names follow the same FIRST LAST or LAST FIRST format).
- **LASTNAME/FIRSTNAME**: Split Names carefully. 
- **BCBS RI Rule**: Names are likely **FIRST LAST** (e.g., "SHARAD SAXTON"). Confirm by checking common names.
- **Ignore Noise**: Do NOT put "N/A" or Department numbers (e.g., "3") into name fields.
- **Standard**: Prefer `LASTNAME, FIRSTNAME` if the document uses commas. If no commas, use your best judgment but keep it consistent across all rows.



5. **SECTION DETECTION (CRITICAL - READ VERY CAREFULLY)**:
   
   **YOU MUST identify which section each member appears in. This is THE MOST IMPORTANT rule.**
   
   **CURRENT CHARGES Section** (extract to CURRENT_PREMIUM):
   - Section headers to look for:
     - "Current Inforce Charges"
     - "Medical Charges"  
     - "Current Charges"
     - "Membership Detail"
     - "CURRENT INFORCE CHARGES"
     - "Member Relationship" (Hometown Health)
     - "Member ID Coverage" (Hometown Health)
   - These are charges for the CURRENT billing period
   - Amounts are typically positive
   - Extract to: **CURRENT_PREMIUM** field
   - Leave ADJUSTMENT_PREMIUM as null
   
   **RETROACTIVE/ADJUSTMENT Section** (extract to ADJUSTMENT_PREMIUM):
   - Section headers to look for:
     - "Retroactivity Charges/Credits"
     - "RETROACTIVITY CHARGES/CREDITS CONT."
     - "Eligibility Change(s)"
     - "Adjustments"
     - "Prior Period Adjustments"
   - These are corrections for PRIOR periods
   - Amounts can be positive (charges) or negative (credits)
   - Extract to: **ADJUSTMENT_PREMIUM** field
   - Leave CURRENT_PREMIUM as null
   
    **CRITICAL RULES**:
    1. **Section header determines the field, NOT the sign of the amount**
    2. If amount is negative AND in "Retroactivity" section → ADJUSTMENT_PREMIUM
    3. If amount is negative AND in "Current" section → CURRENT_PREMIUM (rare but possible)
    4. **ONE ROW PER MEMBER**: Each unique member (MEMBERID + Name) MUST appear exactly once in the JSON output.
    5. **MERGING CURRENT & RETRO**: If a member appears in BOTH the "Current" and "Retroactive/Adjustment" sections, you MUST merge them into a single JSON object.
       - The value from the "Current" section goes into **CURRENT_PREMIUM**.
       - The value from the "Retroactive" section goes into **ADJUSTMENT_PREMIUM**.
    6. **SUM MULTIPLE ADJUSTMENTS**: If a member has multiple entries in the adjustments section, SUM them into a single **ADJUSTMENT_PREMIUM** value for that member.
    
    **How to identify sections**:
    - Look for section headers in the document text
    - Section headers are usually in ALL CAPS or bold
    - Members listed after a section header belong to that section
    - Section continues until you see a new section header

6. **PREMIUM COLUMN LOGIC (ANTHEM/Multi-Column)**:
   - If you see multiple amount columns (e.g. Subscriber, Dep, Total):
     - **CURRENT_PREMIUM** MUST be the **TOTAL** amount.
     - **DO NOT** use "Subscriber Amount" or "Dependent Amount" as ADJUSTMENT_PREMIUM.
   - **ADJUSTMENT_PREMIUM** requires an explicit column header like "Adjustment", "Retro", "Credit", "Prorated".
   - If no explicit adjustment column exists, `ADJUSTMENT_PREMIUM` is null.



### EXAMPLE MAPPING (APL):
Input: `2543915 ANAND, ARJUN ****_7635 MEDLINK SELECT $85.27`
Output: `{{"LASTNAME": "ANAND", "FIRSTNAME": "ARJUN", "MEMBERID": "2543915", "SSN": "7635", "PLAN_NAME": "MEDLINK SELECT", "CURRENT_PREMIUM": 85.27}}`

8. **PLAN DATA INFERENCE**:
   - If PLAN_NAME is missing on the row, look for a general plan name in the header (e.g., "Medical", "MERP", "Dental").

### REQUIRED JSON STRUCTURE:
{{
  "HEADER": {{
    "INV_DATE": null,
    "INV_NUMBER": null,
    "BILLING_PERIOD": null
  }},
  "LINE_ITEMS": [
    {{
      "LASTNAME": null,
      "FIRSTNAME": null,
      "MIDDLENAME": null,
      "SSN": null,
      "POLICYID": null,
      "MEMBERID": null,
      "PLAN_NAME": null,
      "PLAN_TYPE": null,
      "COVERAGE": null,
      "CURRENT_PREMIUM": null,
      "ADJUSTMENT_PREMIUM": null,
      "PRICING_ADJUSTMENT": null
    }}
  ]
}}

### CRITICAL NUMERIC FORMATTING RULES:
- **Parentheses = Negative**: If you see (1,032.31) or ($1,032.31), extract as -1032.31
- **Remove Currency Symbols**: Strip $, commas, and other formatting
- **Preserve Sign**: Credits/adjustments in parentheses MUST be negative numbers

### CRITICAL EXTRACTION RULES (STRICT ADHERENCE REQUIRED):

    - **GRAND TOTAL & SUMMARY ROWS**: 
      - Locate the grand total premium amount (usually found in a summary or total section).
      - IMPORTANT: DO NOT add a `TOTAL_AMOUNT` field to the HEADER.
      - INSTEAD: Add a FINAL object to the `LINE_ITEMS` array with:
        - `PLAN_NAME`: "TOTAL"
        - `FIRSTNAME`: "INVOICE TOTAL"
        - `CURRENT_PREMIUM`: The grand total value.
        - All other fields: null.
      - **IGNORE ENTITY SUMMARY ROWS**: If you see a row containing the company/group name (e.g., "RAPID TRADING LLC") with a total amount, DO NOT extract it as an individual member line item. This is a summary of the whole document, not a person. ONLY extract names of individuals (people).
      - ERROR CASE: Never link planholder names found in headers (e.g., "Alicia Keel") to document-level totals found in summary tables.
      - **IGNORE NAME HEADERS**: Often invoices repeat a name at the top of a section or page (e.g., "Bill for: Sharad Saxton"). DO NOT extract these as line items if they are solo headers. ONLY extract names when they are part of the actual premium/billing table rows.
      - **CRITICAL: NEVER MISATTRIBUTE TOTALS**: A member's premium must be their own individual cost. NEVER attribute a sub-total or grand total (e.g., $3301.90) to an individual member row (e.g., SAXTON SHARAD). Sub-totals are for visual grouping only and MUST be ignored for individual line item extraction.

2. **WIDE FORMAT / MULTI-COLUMN TABLES**:
   - If coverages (Dental, Vision, LIFE, Std) are listed as COLUMNS:
     - Generate a SEPARATE JSON object for EVERY column with a non-zero value.
     - Column Header -> `PLAN_NAME`.
     - Value in Column -> `CURRENT_PREMIUM`.
     - Derived Type (e.g., "Dental" -> DENTAL) -> `COVERAGE`.

3. **ADJUSTMENT SECTION MAPPING (GUARDIAN)**:
   - If a table has **"New Premium"** and **"New Premium Adjustment"** columns:
     - The **"New Premium"** column (usually smaller, e.g., 2.50) is the monthly rate -> map to **CURRENT_PREMIUM**.
     - The **"New Premium Adjustment"** column (usually larger, e.g., 7.50) is the change -> map to **ADJUSTMENT_PREMIUM**.
     - **DO NOT SWAP THEM.**

4. **IDENTIFIER CONSISTENCY**: 
   - Repeatedly apply `MEMBERID` and `SSN` to every split row of the same person.

5. **NAME FORMATTING**: 
   - If names are "LASTNAME, FIRSTNAME", split them into their respective fields accordingly.

6. **HEADER DATA**: 
   - Extract actual dates (e.g., "01/17/2025") for `INV_DATE`, not the labels.

DOCUMENT TEXT:
{text}

JSON OUTPUT:"""

    try:
        print(f"  [AI] Calling OpenAI API to extract fields...")
        
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You are a professional bank and insurance auditor. You extract data with 100% accuracy, preserving leading zeros and distinguishing similar-looking identifiers."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            model="gpt-4.1",
            temperature=0,  # Zero temperature for maximum consistency
            max_tokens=14000,
        )
        
        response_text = chat_completion.choices[0].message.content
        print(f"  [OK] Received response from OpenAI")
        
        # Parse the JSON response
        # Remove markdown code blocks if present
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        extracted_data = json.loads(response_text)
        
        # Show what was extracted
        print(f"  [OK] Successfully extracted {sum(1 for v in extracted_data.values() if v is not None)} fields")
        
        return extracted_data
        
    except json.JSONDecodeError as e:
        print(f"  [ERROR] JSON parsing error: {e}")
        print(f"  Raw response: {response_text[:500]}")
        return {"HEADER": {}, "LINE_ITEMS": []}
    except Exception as e:
        print(f"  [ERROR] Error during LLM extraction: {e}")
        if "insufficient_quota" in str(e).lower() or "429" in str(e):
            raise e
        return {"HEADER": {}, "LINE_ITEMS": []}


def extract_text_to_file(pdf_path: str, output_txt: Optional[str] = None, use_ocr: bool = False) -> Optional[str]:
    """
    STEP 1: Extract text from PDF and save to TXT file for human verification
    
    Args:
        pdf_path: Path to PDF file
        output_txt: Output TXT file path (optional, auto-generated if not provided)
        use_ocr: Whether to use OCR for extraction (default: False)
        
    Returns:
        Path to the created TXT file
    """
    print(f"\n{'='*70}")
    print(f"STEP 1: EXTRACTING TEXT FOR VERIFICATION")
    if use_ocr:
        print(f"MODE: OCR (Optical Character Recognition)")
    print(f"{'='*70}")
    print(f"[PDF] Source PDF: {pdf_path}")
    
    # Auto-generate output filename if not provided
    if output_txt is None:
        pdf_name = Path(pdf_path).stem
        suffix = "_ocr" if use_ocr else "_extracted"
        output_txt = str(Path(pdf_path).parent / f"{pdf_name}{suffix}.txt")
    
    # Extract text from PDF
    if use_ocr:
        text = extract_text_from_pdf_ocr(pdf_path)
        # Apply noise cleaning for OCR text
        text = clean_ocr_noise(text)
    else:
        # Extract text from PDF using PyMuPDF (better quality)
        text = extract_text_from_pdf_pymupdf(pdf_path)
    
    quality_score = check_text_quality(text)
    print(f"  [INFO] Text quality score: {quality_score:.2f}")
    
    if not text.strip() or (quality_score < 0.2 and not use_ocr):
        print(f"  [WARNING] Text quality is low ({quality_score:.2f}). Attempting OCR fallback...")
        try:
            text = extract_text_from_pdf_ocr(pdf_path)
            text = clean_ocr_noise(text)
            new_score = check_text_quality(text)
            print(f"  [INFO] OCR text quality score: {new_score:.2f}")
            
            # Update suffix for clarity if auto-switched
            if "_extracted" in output_txt:
                output_txt = output_txt.replace("_extracted", "_ocr_auto")
                
        except Exception as e:
            print(f"  [ERROR] OCR fallback failed: {e}")
    
    if not text.strip():
        print(f"  [WARNING] Warning: No text extracted from {pdf_path}")
        return None
    
    # Save to TXT file
    try:
        with open(output_txt, 'w', encoding='utf-8') as f:
            f.write(text)
        
        print(f"\n{'='*70}")
        print(f"[SUCCESS] TEXT EXTRACTION COMPLETE!")
        print(f"{'='*70}")
        print(f"[TXT] Extracted text saved to: {output_txt}")
        print(f"[DATA] Total characters: {len(text)}")
        print(f"\n{'='*70}")
        print(f"[WARNING]  NEXT STEPS:")
        print(f"{'='*70}")
        print(f"1. Open and review the extracted text file:")
        print(f"   {output_txt}")
        print(f"2. Make any necessary corrections or edits")
        print(f"3. Save the file after verification")
        print(f"4. Run Step 2 to process the verified text:")
        print(f"   python improved_pdf_extractor.py --process \"{output_txt}\"")
        print(f"{'='*70}\n")
        
        return output_txt
        
    except Exception as e:
        print(f"  [ERROR] Error saving text to {output_txt}: {e}")
        return None


def process_verified_text_file(txt_path: str, client: OpenAI, source_filename: Optional[str] = None) -> Dict:
    """
    STEP 2: Process verified TXT file and extract fields using LLM
    
    Args:
        txt_path: Path to verified TXT file
        client: Groq client instance
        source_filename: Original source filename for reference
        
    Returns:
        Dictionary with extracted fields
    """
    print(f"\n{'='*70}")
    print(f"STEP 2: PROCESSING VERIFIED TEXT")
    print(f"{'='*70}")
    print(f"[TXT] Reading verified text from: {txt_path}")
    
    # Read verified text from file
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            text = f.read()
            
        # Apply noise cleaning (safe to run even on clean text)
        text = clean_ocr_noise(text)
        
        print(f"  [OK] Read {len(text)} characters from verified file")
        
    except Exception as e:
        print(f"  [ERROR] Error reading text file {txt_path}: {e}")
        return {field: None for field in REQUIRED_FIELDS}
    
    # Extract fields using LLM
    extracted_data = extract_fields_with_llm(text, client, os.path.basename(txt_path))
    
    # Add source filename
    if source_filename:
        extracted_data['SOURCE_FILE'] = source_filename
    else:
        extracted_data['SOURCE_FILE'] = os.path.basename(txt_path)
    
    return extracted_data


def process_single_pdf(pdf_path: str, client: OpenAI) -> Dict:
    """
    Process a single PDF file and extract fields
    
    Args:
        pdf_path: Path to PDF file
        client: OpenAI,
        
    Returns:
        Dictionary with extracted data
    """
    print(f"[V3] \n{'='*70}")
    print(f"[V3] Processing: {pdf_path}")
    print(f"[V3] {'='*70}")
    
    # Extract text from PDF
    text = extract_text_from_pdf_improved(pdf_path)
    
    # Perform quality check and OCR fallback
    quality_score = check_text_quality(text)
    if not text.strip() or quality_score < 0.2:
        print(f"  [WARNING] Text quality is low ({quality_score:.2f}). Attempting OCR fallback...")
        try:
            text = extract_text_from_pdf_ocr(pdf_path)
            # Apply noise cleaning for OCR text
            text = clean_ocr_noise(text)
            new_score = check_text_quality(text)
            print(f"  [INFO] OCR text quality score: {new_score:.2f}")
        except Exception as e:
            print(f"  [ERROR] OCR fallback failed in process_single_pdf: {e}")

    # [V3][VERIFY] Save raw extracted text for human verification
    pdf_stem = Path(pdf_path).stem
    raw_txt_path = Path(pdf_path).parent / f"{pdf_stem}_raw_extracted.txt"
    try:
        with open(raw_txt_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  [V3][VERIFY] Raw extracted text saved to: {raw_txt_path}")
    except Exception as e:
        print(f"  [V3][ERROR] Failed to save raw text: {e}")

    # Split text into pages
    # Regex allows for potential OCR whitespace/symbol variance around markers
    # We look for [[PAGE_n]] markers, allowing for [ [ or [  [ etc.
    page_markers = re.findall(r'\[\s*\[\s*PAGE_\d+\s*\]\s*\]', text)
    pages = re.split(r'\[\s*\[\s*PAGE_\d+\s*\]\s*\]', text)
    
    # Remove empty chunks but preserve empty pages (which will have minimal text after split)
    # Actually, re.split with a marker at the start returns [''] as first element.
    if pages and not pages[0].strip():
        pages.pop(0)
    
    pages = [p.strip() for p in pages]
    
    # [V3][VERIFY] Data Integrity Check for Chunking
    original_len = len(text)
    combined_pages_len = sum(len(p) for p in pages)
    markers_len = sum(len(m) for m in page_markers)
    # We also need to account for the characters re.split consumed (the markers) 
    # and the potential whitespace strip() removed. 
    # A simpler check: re-join and compare if feasible, or check if total significantly dropped.
    # Since we use strip(), we compare the length of re-joined pages + markers + estimated whitespace.
    print(f"  [V3][VERIFY] Chunking Integrity Check:")
    print(f"    - Original Text Length: {original_len}")
    print(f"    - Page Markers Count: {len(page_markers)}")
    if combined_pages_len + markers_len <= original_len:
        print(f"    - Result: PASS (No significant data loss detected beyond markers/whitespace)")
    else:
        print(f"    - Result: WARNING (Length mismatch: {combined_pages_len + markers_len} vs {original_len})")

    all_line_items = []
    final_header = {field: None for field in REQUIRED_FIELDS if field in ["INV_DATE", "INV_NUMBER", "BILLING_PERIOD", "GROUP_NUMBER", "PRICING_ADJUSTMENT"]}
    
    print(f"  [V3] Splitting large document into {len(pages)} pages for reliable extraction...")
    
    for i, page_text in enumerate(pages):
        print(f"  [V3] Processing chunk {i+1}/{len(pages)}...")
        
        # Pass 1: Standard Mode (Horizontal Parser)
        page_data = extract_fields_with_llm(page_text, client, f"{os.path.basename(pdf_path)}_page_{i+1}", mode="standard")
        
        # Pass 2: Vertical Fallback (if standard mode returns 0 line items)
        if not page_data.get("LINE_ITEMS"):
            # Heuristic: only retry if the chunk has substance or keywords
            if len(page_text) > 200 or any(k in page_text.upper() for k in ["NAME", "CODE", "LIFE", "DENTAL", "VISION"]):
                print(f"    -> [FALLBACK] No items in standard mode for chunk {i+1}. Retrying in VERTICAL mode...")
                try:
                    full_vertical_text = extract_text_from_pdf_pymupdf(pdf_path, mode="vertical")
                    v_pages = re.split(r'\[\s*\[\s*PAGE_\d+\s*\]\s*\]', full_vertical_text)
                    if v_pages and not v_pages[0].strip():
                        v_pages.pop(0)
                    
                    if i < len(v_pages):
                        v_chunk_text = v_pages[i].strip()
                        page_data = extract_fields_with_llm(v_chunk_text, client, f"{os.path.basename(pdf_path)}_page_{i+1}", mode="vertical")
                except Exception as e:
                    print(f"    -> [ERROR] Vertical fallback failed: {e}")

        # Merge header data from the successful pass (don't overwrite with nulls)
        page_header = page_data.get("HEADER", {})
        for k, v in page_header.items():
            if v and str(v).lower() not in ["n/a", "none"]:
                final_header[k] = v
        
        # Collect line items from the successful pass
        items = page_data.get("LINE_ITEMS", [])
        if items:
            print(f"    -> Extracted {len(items)} items from chunk {i+1}")
            all_line_items.extend(items)
        else:
            print(f"    -> No items found in chunk {i+1}")

    # Final combined data
    data = {
        "HEADER": final_header,
        "LINE_ITEMS": all_line_items
    }
    
    return data


def process_single_pdf_to_excel(pdf_path: str, output_excel: str):
    """
    Process a single PDF file and save to Excel
    
    Args:
        pdf_path: Path to PDF file
        output_excel: Output Excel file path
    """
    # Initialize OpenAI client
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # Process the PDF
    data = process_single_pdf(pdf_path, client)
    
    # Flatten data for Excel
    source_filename = os.path.basename(pdf_path)
    rows = flatten_extracted_data(data, source_filename)
    
    if not rows:
        print(f"  [WARNING] No rows extracted from {pdf_path}")
        return
        
    # Convert to DataFrame
    df = pd.DataFrame(rows)
    
    # Ensure all REQUIRED_FIELDS are present as columns
    for field in REQUIRED_FIELDS:
        if field not in df.columns:
            df[field] = None
            
    # Reorder columns
    cols = ['SOURCE_FILE'] + REQUIRED_FIELDS
    # Only pick columns that actually exist to avoid KeyError
    cols = [c for c in cols if c in df.columns]
    df = df[cols]
    
    # Save to Excel
    df.to_excel(output_excel, index=False, engine='openpyxl')
    
    print(f"\n{'='*70}")
    print(f"[SUCCESS] EXTRACTION COMPLETE!")
    print(f"{'='*70}")
    print(f"[PDF] Output saved to: {output_excel}")
    print(f"\n[DATA] Extracted Data Preview:")
    print(f"{'='*70}")
    
    # Display results nicely
    for col in cols:
        value = df[col].iloc[0]
        if pd.notna(value):
            print(f"  {col:25s}: {value}")
        else:
            print(f"  {col:25s}: (not found)")
    
    print(f"{'='*70}\n")


def process_multiple_pdfs(pdf_directory: str, output_excel: str):
    """
    Process multiple PDF files and save to Excel
    
    Args:
        pdf_directory: Directory containing PDF files
        output_excel: Output Excel file path
    """
    # Initialize OpenAI client
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # Find all PDF files
    pdf_files = list(Path(pdf_directory).glob("*.pdf"))
    
    if not pdf_files:
        print(f"[ERROR] No PDF files found in {pdf_directory}")
        return
    
    print(f"\n{'='*70}")
    print(f"Found {len(pdf_files)} PDF file(s) to process")
    print(f"{'='*70}")
    
    # Process each PDF
    all_data = []
    for pdf_file in pdf_files:
        data = process_single_pdf(str(pdf_file), client)
        all_data.append(data)
    
    # Convert to DataFrame
    df = pd.DataFrame(all_data)
    
    # Reorder columns
    cols = ['SOURCE_FILE'] + REQUIRED_FIELDS
    df = df[cols]
    
    # Save to Excel
    df.to_excel(output_excel, index=False, engine='openpyxl')
    
    print(f"\n{'='*70}")
    print(f"[SUCCESS] BATCH EXTRACTION COMPLETE!")
    print(f"{'='*70}")
    print(f"[PDF] Output saved to: {output_excel}")
    print(f"[DATA] Processed {len(all_data)} PDF file(s)")
    print(f"\n[SUMMARY] Summary:")
    print(df.to_string(index=False))
    print(f"{'='*70}\n")


def flatten_extracted_data(data: Dict, source_filename: str) -> List[Dict]:
    """
    Flatten nested JSON data (HEADER + LINE_ITEMS) into a list of rows for Excel
    """
    rows = []
    
    if "HEADER" in data and "LINE_ITEMS" in data:
        header = data["HEADER"]
        line_items = data["LINE_ITEMS"]
        
        if not line_items:
            # If no line items, just save header with empty line item fields
            row = {"SOURCE_FILE": source_filename}
            row.update(header)
            rows.append(row)
        else:
            # Deduplicate/Merge items using multi-stage matching (Name + MemberID or Name + SSN)
            merged_items = []
            index_by_id = {}   # fname|lname|member_id -> item_index
            index_by_ssn = {}  # fname|lname|ssn -> item_index
            
            def to_float(val):
                if val is None: return 0.0
                if isinstance(val, (int, float)): return float(val)
                try:
                    # Clean currency formatting
                    s = str(val).replace('$', '').replace(',', '').strip()
                    if '(' in s and ')' in s:
                        s = '-' + s.replace('(', '').replace(')', '')
                    return float(s)
                except:
                    return 0.0

            for item in line_items:
                fname = str(item.get("FIRSTNAME") or "").strip().lower()
                lname = str(item.get("LASTNAME") or "").strip().lower()
                member_id = str(item.get("MEMBERID") or "").strip().lower()
                ssn = str(item.get("SSN") or "").strip().lower()
                
                # Check for weak identifiers
                is_weak_id = not member_id or member_id in ["n/a", "none", "unknown", ""]
                is_weak_ssn = not ssn or ssn in ["n/a", "none", "unknown", ""]
                is_weak_name = not fname and not lname
                
                if is_weak_id and is_weak_ssn and is_weak_name:
                    # Skip empty/noise items that have no identifier and no name
                    print(f"    [V3][INFO] Skipping likely empty/noise line item (no ID/SSN/Name)")
                    continue
                
                # Possible match keys
                # PRIMARY KEY: Name + ID + Plan (Strict match for multi-plan differentiation)
                plan_name = str(item.get("PLAN_NAME") or "").strip().lower()
                clean_plan = plan_name if plan_name not in ["n/a", "none", ""] else None
                
                key_id_strict = f"{fname}|{lname}|{member_id}|{clean_plan}" if not is_weak_id and clean_plan else None
                key_ssn_strict = f"{fname}|{lname}|{ssn}|{clean_plan}" if not is_weak_ssn and clean_plan else None
                
                # SECONDARY KEY: Name + ID (Relaxed for merging adjustments without plan name)
                key_id_loose = f"{fname}|{lname}|{member_id}" if not is_weak_id else None
                key_ssn_loose = f"{fname}|{lname}|{ssn}" if not is_weak_ssn else None
                
                match_index = None
                
                # 1. Try Strict Match first
                if key_id_strict and key_id_strict in index_by_id:
                    match_index = index_by_id[key_id_strict]
                elif key_ssn_strict and key_ssn_strict in index_by_ssn:
                    match_index = index_by_ssn[key_ssn_strict]
                
                if match_index is None:
                     # Look for a potential match using loose keys
                     potential_idx = None
                     if key_id_loose and key_id_loose in index_by_id:
                         potential_idx = index_by_id[key_id_loose]
                     elif key_ssn_loose and key_ssn_loose in index_by_ssn:
                         potential_idx = index_by_ssn[key_ssn_loose]
                     
                     if potential_idx is not None:
                         # VALIDATE: Only merge if one of the plan names is missing, OR if they are the same
                         existing = merged_items[potential_idx]
                         ex_plan = str(existing.get("PLAN_NAME") or "").strip().lower()
                         ex_clean = ex_plan if ex_plan not in ["n/a", "none", ""] else None
                         if not clean_plan or not ex_clean or clean_plan == ex_clean:
                             match_index = potential_idx
                
                # 3. Try Name-Only Match (ULTRA-LOOSE) if one side is a "shell" record (missing identifiers)
                if match_index is None and not is_weak_name:
                    # Check if we have an existing record with the SAME name
                    matched_by_name_idx = None
                    for idx, ex in enumerate(merged_items):
                        ex_fname = str(ex.get("FIRSTNAME") or "").strip().lower()
                        ex_lname = str(ex.get("LASTNAME") or "").strip().lower()
                        
                        # [ENHANCEMENT] Handle middle initials in first name (e.g. "John" vs "John A")
                        f1, f2 = fname, ex_fname
                        l1, l2 = lname, ex_lname
                        
                        name_match = False
                        if l1 == l2:
                            # Direct first name match
                            if f1 == f2:
                                name_match = True
                            # One First Name starts with the other First Name (handles initials)
                            elif (len(f1) > 1 and len(f2) > 1) and (f1.startswith(f2) or f2.startswith(f1)):
                                name_match = True
                        
                        if name_match:
                            ex_id = str(ex.get("MEMBERID") or "").strip().lower()
                            ex_ssn = str(ex.get("SSN") or "").strip().lower()
                            
                            # Rule: Merge if the IDENTIFIER space is compatible
                            curr_no_id = is_weak_id and is_weak_ssn
                            ex_no_id = (not ex_id or ex_id in ["n/a", "none", ""]) and (not ex_ssn or ex_ssn in ["n/a", "none", ""])
                            
                            if curr_no_id or ex_no_id:
                                # Potential match - check plan name compatibility
                                ex_plan = str(ex.get("PLAN_NAME") or "").strip().lower()
                                ex_clean = ex_plan if ex_plan not in ["n/a", "none", ""] else None
                                if not clean_plan or not ex_clean or clean_plan == ex_clean:
                                    matched_by_name_idx = idx
                                    break
                    
                    if matched_by_name_idx is not None:
                        match_index = matched_by_name_idx
                
                if match_index is not None:
                    existing = merged_items[match_index]
                    
                    def check_total(item_obj):
                        p = str(item_obj.get("PLAN_NAME", "") or "").upper()
                        f = str(item_obj.get("FIRSTNAME", "") or "").upper()
                        l = str(item_obj.get("LASTNAME", "") or "").upper()
                        return any(kw in p or kw in f or kw in l for kw in ["TOTAL", "GRAND TOTAL"])

                    is_total_type = check_total(item) or check_total(existing)
                    
                    # Merge data: update existing record
                    for k, v in item.items():
                        if k in ["CURRENT_PREMIUM", "ADJUSTMENT_PREMIUM"]:
                            v1 = to_float(existing.get(k))
                            v2 = to_float(v)
                            
                            if v2 != 0:
                                # DEDUPLICATION: If it's a "TOTAL" row, keep the LATEST value, do not sum.
                                if is_total_type:
                                    existing[k] = v2
                                # For members: only add if the value is DIFFERENT (ignore redundant extractions of same row)
                                elif abs(v1 - v2) > 0.01:
                                    existing[k] = round(v1 + v2, 2)
                                else:
                                    # Identical value likely from chunk overlap/redundant extraction
                                    pass
                        elif v and str(v).lower() not in ["n/a", "none", ""]:
                            # Keep first non-null encounter for others, unless existing is null
                            if not existing.get(k) or str(existing.get(k)).lower() in ["n/a", "none", ""]:
                                existing[k] = v
                    
                    # Also update missing indices if the current item has them
                    # Index BOTH strict and loose keys to enable flexible matching
                    if match_index is not None:
                         if key_id_strict: index_by_id[key_id_strict] = match_index
                         if key_ssn_strict: index_by_ssn[key_ssn_strict] = match_index
                         if key_id_loose: index_by_id[key_id_loose] = match_index
                         if key_ssn_loose: index_by_ssn[key_ssn_loose] = match_index
                else:
                    # New record
                    new_item = item.copy()
                    current_idx = len(merged_items)
                    merged_items.append(new_item)
                    
                    if key_id_strict: index_by_id[key_id_strict] = current_idx
                    if key_ssn_strict: index_by_ssn[key_ssn_strict] = current_idx
                    if key_id_loose: index_by_id[key_id_loose] = current_idx
                    if key_ssn_loose: index_by_ssn[key_ssn_loose] = current_idx
            
            # PHASE 1: Separate member rows from total rows
            member_rows = []
            total_rows = []
            
            for item in merged_items:
                row = {"SOURCE_FILE": source_filename}
                # Ensure all required fields are present (even as None/empty)
                for field in REQUIRED_FIELDS:
                    row[field] = item.get(field) # Will be None if missing
                
                row.update(header)
                row.update(item)
                # Remove internal fields that shouldn't be in Excel
                for internal_field in ["PRICING_MODEL", "RELATIONSHIP"]:
                    if internal_field in row:
                        del row[internal_field]
                
                # Check for total row type
                idx_p = str(row.get("PLAN_NAME", "") or "").upper()
                idx_f = str(row.get("FIRSTNAME", "") or "").upper()
                idx_l = str(row.get("LASTNAME", "") or "").upper()
                
                # Enhanced detection for summary rows misclassified as members
                is_total = any(kw in idx_p or kw in idx_f or kw in idx_l for kw in ["TOTAL", "GRAND TOTAL"])
                
                # If FIRSTNAME and PLAN_NAME are empty, but LASTNAME and PREMIUM match a summary pattern
                if not is_total:
                    has_first = idx_f and idx_f not in ["NONE", "NAN", "N/A", "UNKNOWN"]
                    has_plan = idx_p and idx_p not in ["NONE", "NAN", "N/A", "UNKNOWN"]
                    if not has_first and not has_plan and idx_l:
                        # This looks like an entity name (e.g. RAPID TRADING LLC) rather than a person
                        is_total = True
                
                if is_total:
                    # Clear text labels for the final Excel output (leave only the amount)
                    fields_to_clear = [
                        "FIRSTNAME", "LASTNAME", "MEMBERID", "SSN", 
                        "PLAN_TYPE", "COVERAGE", "MIDDLENAME", "POLICYID",
                        "SOURCE_FILE", "INV_DATE", "INV_NUMBER", "BILLING_PERIOD"
                    ]
                    for field in fields_to_clear:
                        if field in row:
                            row[field] = None
                    total_rows.append(row)
                else:
                    member_rows.append(row)
            
            # PHASE 2: Produce explicit and auditable TOTAL rows.
            # Strategy:
            #   1. Calculate the sums of Current and Adjustment columns.
            #   2. Provide separate rows for each sum + a combined final total.
            #   3. This makes the math explicit and auditable in the spreadsheet.

            sum_current = sum(to_float(mr.get("CURRENT_PREMIUM")) for mr in member_rows)
            sum_adj = sum(to_float(mr.get("ADJUSTMENT_PREMIUM")) for mr in member_rows)
            combined_total = sum_current + sum_adj

            # Build audit-ready total rows
            final_total_rows = []
            
            # Row 1: Sum of all Current Premiums
            row_curr = {field: None for field in REQUIRED_FIELDS}
            row_curr["PLAN_NAME"] = "TOTAL CURRENT PREMIUM"
            row_curr["CURRENT_PREMIUM"] = sum_current
            final_total_rows.append(row_curr)
            
            # Row 2: Sum of all Adjustments (Only if non-zero)
            if abs(sum_adj) > 0.001:
                row_adj = {field: None for field in REQUIRED_FIELDS}
                row_adj["PLAN_NAME"] = "TOTAL ADJUSTMENTS"
                row_adj["ADJUSTMENT_PREMIUM"] = sum_adj
                final_total_rows.append(row_adj)
            
            # Row 3: Final Combined Total (at the bottom of Current Premium column per user request)
            row_grand = {field: None for field in REQUIRED_FIELDS}
            row_grand["PLAN_NAME"] = "GRAND TOTAL (COMBINED)"
            row_grand["CURRENT_PREMIUM"] = combined_total
            final_total_rows.append(row_grand)

            # Audit Check: If the LLM explicitly extracted a "TOTAL" line item that differs from our sum
            llm_total_val = 0.0
            for item in line_items:
                plan_name = str(item.get("PLAN_NAME", "")).upper()
                first_name = str(item.get("FIRSTNAME", "")).upper()
                
                # Heuristic: A true summary row usually has "TOTAL" but NO last name or empty plan name
                # Avoid triggering on "Total Pet" or "Total Dental"
                excluded_summaries = ["TOTAL PET", "TOTAL DENTAL", "TOTAL VISION", "TOTAL LIFE"]
                is_excluded = any(ex in plan_name for ex in excluded_summaries)
                
                if not is_excluded and ("TOTAL" in plan_name or "TOTAL" in first_name):
                    # One more check: a summary row usually doesn't have a First Name
                    if not item.get("LASTNAME"):
                        llm_total_val = to_float(item.get("CURRENT_PREMIUM"))
                        break
            
            if llm_total_val > 0 and abs(llm_total_val - combined_total) > 0.05:
                row_report = {field: None for field in REQUIRED_FIELDS}
                row_report["PLAN_NAME"] = "REPORTED INVOICE TOTAL (FOR AUDIT)"
                row_report["CURRENT_PREMIUM"] = llm_total_val
                final_total_rows.append(row_report)
                print(f"    [V3][AUDIT] Total mismatch detected! Calculated: {combined_total}, Reported: {llm_total_val}")

            rows = member_rows + final_total_rows
                
    else:
        # Fallback for legacy/error case
        row = {"SOURCE_FILE": source_filename}
        row.update(data)
        rows.append(row)
        
    return rows


def extract_step(pdf_path: str, output_txt: Optional[str] = None, use_ocr: bool = False):
    """
    STEP 1 Wrapper: Extract text from PDF to TXT file for verification
    
    Args:
        pdf_path: Path to PDF file
        output_txt: Output TXT file path (optional)
        use_ocr: Whether to use OCR extraction
    """
    extract_text_to_file(pdf_path, output_txt, use_ocr)


def process_step(txt_path: str, output_excel: str = "extracted_data.xlsx"):
    """
    STEP 2 Wrapper: Process verified TXT file and save to Excel
    
    Args:
        txt_path: Path to verified TXT file
        output_excel: Output Excel file path
    """
    # Initialize OpenAI client
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # Process the verified text
    data = process_verified_text_file(txt_path, client)
    
    # Flatten data for Excel
    rows = flatten_extracted_data(data, os.path.basename(txt_path))
    
    # Convert to DataFrame
    df = pd.DataFrame(rows)
    
    # Save to Excel
    df.to_excel(output_excel, index=False, engine='openpyxl')
    
    print(f"\n{'='*70}")
    print(f"[SUCCESS] EXTRACTION COMPLETE!")
    print(f"{'='*70}")
    print(f"[PDF] Output saved to: {output_excel}")
    print(f"Extracted {len(rows)} row(s)")
    print(f"\n[DATA] Extracted Data Preview (First Row):")
    print(f"{'='*70}")
    
    if rows:
        preview_row = rows[0]
        for key, value in preview_row.items():
            if value:
                print(f"  {key:25s}: {value}")
    
    print(f"{'='*70}\n")


def batch_extract_step(pdf_directory: str, output_directory: Optional[str] = None, use_ocr: bool = False):
    """
    STEP 1 Batch: Extract text from multiple PDFs to TXT files
    
    Args:
        pdf_directory: Directory containing PDF files
        output_directory: Output directory for TXT files (optional, uses same dir if not provided)
        use_ocr: Whether to use OCR extraction
    """
    # Find all PDF files
    pdf_files = list(Path(pdf_directory).glob("*.pdf"))
    
    if not pdf_files:
        print(f"[ERROR] No PDF files found in {pdf_directory}")
        return
    
    print(f"\n{'='*70}")
    print(f"BATCH EXTRACTION: Found {len(pdf_files)} PDF file(s)")
    if use_ocr:
        print(f"MODE: OCR (Optical Character Recognition)")
    print(f"{'='*70}\n")
    
    # Create output directory if specified
    if output_directory:
        Path(output_directory).mkdir(parents=True, exist_ok=True)
    
    # Process each PDF
    for pdf_file in pdf_files:
        if output_directory:
            output_txt = str(Path(output_directory) / f"{pdf_file.stem}_extracted.txt")
        else:
            output_txt = None
        extract_text_to_file(str(pdf_file), output_txt, use_ocr)


def batch_process_step(txt_directory: str, output_excel: str = "extracted_data.xlsx"):
    """
    STEP 2 Batch: Process multiple verified TXT files and save to Excel
    
    Args:
        txt_directory: Directory containing verified TXT files
        output_excel: Output Excel file path
    """
    # Initialize OpenAI client
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # Find all TXT files
    txt_files = list(Path(txt_directory).glob("*.txt"))
    
    if not txt_files:
        print(f"[ERROR] No TXT files found in {txt_directory}")
        return
    
    print(f"\n{'='*70}")
    print(f"BATCH PROCESSING: Found {len(txt_files)} TXT file(s)")
    print(f"{'='*70}\n")
    
    # Process each TXT file
    all_data = []
    for txt_file in txt_files:
        data = process_verified_text_file(str(txt_file), client)
        all_data.append(data)
    
    # Convert to DataFrame
    df = pd.DataFrame(all_data)
    
    # Reorder columns
    cols = ['SOURCE_FILE'] + REQUIRED_FIELDS
    df = df[cols]
    
    # Save to Excel
    df.to_excel(output_excel, index=False, engine='openpyxl')
    
    print(f"\n{'='*70}")
    print(f"[SUCCESS] BATCH PROCESSING COMPLETE!")
    print(f"{'='*70}")
    print(f"[PDF] Output saved to: {output_excel}")
    print(f"[DATA] Processed {len(all_data)} TXT file(s)")
    print(f"\n[SUMMARY] Summary:")
    print(df.to_string(index=False))
    print(f"{'='*70}\n")


if __name__ == "__main__":
    import sys
    
    # Check for --ocr flag
    use_ocr = "--ocr" in sys.argv
    if use_ocr:
        sys.argv.remove("--ocr")
    
    print(f"\n{'='*70}")
    print("PDF INVOICE DATA EXTRACTOR - TWO-STEP VERIFICATION")
    print(f"{'='*70}\n")
    
    if len(sys.argv) < 2:
        print("USAGE:")
        print("\n  STEP 1 - Extract text for verification:")
        print("    Single PDF:")
        print("      python improved_pdf_extractor.py --extract <pdf_file> [output.txt]")
        print("    Multiple PDFs:")
        print("      python improved_pdf_extractor.py --extract <pdf_directory> [output_directory]")
        print("\n  STEP 2 - Process verified text:")
        print("    Single TXT:")
        print("      python improved_pdf_extractor.py --process <txt_file> [output.xlsx]")
        print("    Multiple TXTs:")
        print("      python improved_pdf_extractor.py --process <txt_directory> [output.xlsx]")
        print("\n  OPTIONS:")
        print("    --ocr: Use Optical Character Recognition (for scanned/image-based PDFs)")
        print("\n  LEGACY MODE (direct extraction, no verification):")
        print("    python improved_pdf_extractor.py <pdf_file_or_directory> [output.xlsx]")
        print("\nEXAMPLES:")
        print("  # Step 1: Extract text")
        print("  python improved_pdf_extractor.py --extract invoice.pdf")
        print("  python improved_pdf_extractor.py --extract ./invoices/ ./extracted_texts/")
        print("\n  # Step 2: Process verified text")
        print("  python improved_pdf_extractor.py --process invoice_extracted.txt results.xlsx")
        print("  python improved_pdf_extractor.py --process ./extracted_texts/ all_results.xlsx")
        print("\n  # Legacy: Direct extraction")
        print("  python improved_pdf_extractor.py invoice.pdf results.xlsx")
        sys.exit(1)
    
    mode = sys.argv[1]
    
    # STEP 1: Extract mode
    if mode == "--extract":
        if len(sys.argv) < 3:
            print("[ERROR] Error: Please provide a PDF file or directory to extract")
            sys.exit(1)
        
        input_path = sys.argv[2]
        output_path = sys.argv[3] if len(sys.argv) > 3 else None
        
        if os.path.isfile(input_path):
            # Single PDF file
            extract_step(input_path, output_path, use_ocr)
        elif os.path.isdir(input_path):
            # Directory of PDFs
            batch_extract_step(input_path, output_path, use_ocr)
        else:
            print(f"[ERROR] Error: {input_path} is not a valid file or directory")
            sys.exit(1)
    
    # STEP 2: Process mode
    elif mode == "--process":
        if len(sys.argv) < 3:
            print("[ERROR] Error: Please provide a TXT file or directory to process")
            sys.exit(1)
        
        input_path = sys.argv[2]
        output_file = sys.argv[3] if len(sys.argv) > 3 else "extracted_data.xlsx"
        
        if os.path.isfile(input_path):
            if input_path.lower().endswith(".pdf"):
                process_single_pdf_to_excel(input_path, output_file)
            else:
                process_step(input_path, output_file)
        elif os.path.isdir(input_path):
            # Directory of TXT files
            batch_process_step(input_path, output_file)
        else:
            print(f"[ERROR] Error: {input_path} is not a valid file or directory")
            sys.exit(1)
    
    # LEGACY MODE: Direct extraction (backward compatibility)
    else:
        input_path = sys.argv[1]
        output_file = sys.argv[2] if len(sys.argv) > 2 else "extracted_data.xlsx"
        
        print("[WARNING]  Running in LEGACY MODE (no verification step)")
        print("    Consider using --extract and --process for better accuracy\n")
        
        if os.path.isfile(input_path):
            # Single PDF file
            process_single_pdf_to_excel(input_path, output_file)
        elif os.path.isdir(input_path):
            # Directory of PDFs
            process_multiple_pdfs(input_path, output_file)
        else:
            print(f"[ERROR] Error: {input_path} is not a valid file or directory")
            sys.exit(1)
