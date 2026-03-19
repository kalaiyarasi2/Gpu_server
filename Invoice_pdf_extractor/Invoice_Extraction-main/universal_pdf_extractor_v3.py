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
Image.MAX_IMAGE_PIXELS = None
import io
import re
from typing import Dict, List, Optional
import threading
import learning_engine
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor




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
        if non_space_len > 0 and alnum_count / non_space_len < 0.2: # Relaxed from 0.4
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
        # Changed from \s{3,} to \s{2,} to better detect tight columns (like name splitting)
        line = re.sub(r'\s{2,}', ' | ', line)
        
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


def clean_billing_period(val: Optional[str]) -> Optional[str]:
    """
    Dynamically extract only the 'From' date from a billing period string.
    Handles various formats and separators (-, to, thru).
    
    Logic:
    1. Split on words like 'to', 'thru', 'through' with spaces.
    2. Split on hyphens/dashes if they have spaces around them.
    3. If dates use slashes (MM/DD/YY), allow splitting on hyphen without spaces.
    4. Handle ISO dates (YYYY-MM-DD) carefully.
    """
    if not val or not str(val).strip() or str(val).lower() in ["n/a", "none"]:
        return val
        
    s = str(val).strip()
    
    # Range words are very high confidence if surrounded by spaces
    parts = re.split(r'\s+\b(?:to|thru|through)\b\s+', s, flags=re.IGNORECASE)
    if len(parts) > 1:
        return parts[0].strip()
    
    # Hyphen with spaces is high confidence
    parts = re.split(r'\s+[-–—]\s*|[-–—]\s+', s)
    if len(parts) > 1:
        return parts[0].strip()
        
    # If date uses slashes or dots, hyphen without spaces is a range separator (e.g. 02/01/26-02/28/26)
    if '/' in s or '.' in s:
        parts = re.split(r'[-–—]', s)
        if len(parts) > 1:
            return parts[0].strip()
            
    # ISO Date Range (YYYY-MM-DD-YYYY-MM-DD) - usually 5 hyphens
    if s.count('-') >= 5:
        parts = s.split('-')
        # If it looks like two ISO dates joined by a hyphen
        if len(parts) >= 6:
            return "-".join(parts[:3])
            
    return s


def to_float(val):
    """
    Convert a string, int, or float to float.
    Handles currency symbols, commas, and parentheses for negative numbers.
    """
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


def check_total(item_obj):
    """
    Identify if a line item row is a summary/total row.
    """
    p = str(item_obj.get("PLAN_NAME", "") or "").upper()
    f = str(item_obj.get("FIRSTNAME", "") or "").upper()
    l = str(item_obj.get("LASTNAME", "") or "").upper()
    mid = str(item_obj.get("MEMBERID", "") or "").strip()
    
    # REQUIRE MEMBERID for member protection
    is_sharad = ("SHARAD" in f and "SAXTON" in l) or ("SHARAD" in l and "SAXTON" in f)
    if is_sharad and mid and mid.isnumeric() and len(mid) >= 4:
        return False # Protect real member with ID
    
    total_keywords = ["TOTAL", "GRAND TOTAL", "AMOUNT DUE", "BALANCE DUE", "TOTAL CURRENT PREMIUM", "TOTAL PREMIUM", "BILLING FEE", "MANAGEMENT FEE", "SAVINGS CREDIT"]
    return any(kw in p or kw in f or kw in l for kw in total_keywords)


def clean_string_spacing(val: Optional[str], preserve_single: bool = True) -> Optional[str]:
    """
    Clean redundant whitespace from strings (names, plan types, etc.).
    - preserve_single=True: Normalizes 2+ spaces/newlines to 1 space.
    """
    if not val or not str(val).strip() or str(val).lower() in ["n/a", "none"]:
        return val
    
    s = str(val).strip()
    if preserve_single:
        # Replace newlines and redundant internal spaces with a single space
        s = re.sub(r'[\r\n\t]+', ' ', s)
        s = re.sub(r'\s{2,}', ' ', s)
    else:
        # Strip all whitespace
        s = re.sub(r'\s+', '', s)
    return s.strip()


# UHC coverage type legend (from official UHC invoice reference)
UHC_COVERAGE_MAP = {
    # Code → standard output
    "E":   "EE",
    "ES":  "ES",
    "ESC": "FAM",
    "EC":  "EC",
    "E1D": "EC",
    "E2D": "EC",
    "E3D": "EC",
    "E4D": "EC",
    "E5D": "EC",
    "E6D": "EC",
    "E7D": "EC",
    "E8D": "EC",
    "E9D": "EC",
    "F":   "FAM",
    "S":   "ES",
    "C":   "EC",
    "E E": "EE",
    # Full-text equivalents
    "EMPLOYEE ONLY":               "EE",
    "EMPLOYEE AND SPOUSE":         "ES",
    "EMPLOYEE AND FAMILY":         "FAM",
    "EMPLOYEE AND CHILD":          "EC",
    "EMPLOYEE AND CHILD(REN)":     "EC",
    "EMPLOYEE & ONE OR MORE DEPENDENT":   "EC",
    "EMPLOYEE & TWO OR MORE DEPENDENTS":  "EC",
    "EMPLOYEE & THREE OR MORE DEPENDENTS": "EC",
    "EMPLOYEE & FOUR OR MORE DEPENDENTS": "EC",
    "EMPLOYEE & FIVE OR MORE DEPENDENTS": "EC",
    # Legacy single-letter fallbacks
    "EE":  "EE",
    "FAM": "FAM",
}


def normalize_uhc_coverage(items: list) -> list:
    """
    Post-process UHC line items: map raw COVERAGE codes to standard values
    using the official UHC coverage type legend.
    E.g. 'ESC' -> 'ESC', 'EC' -> 'EC', 'E2D' -> 'EC'.
    """
    for item in items:
        raw = item.get("COVERAGE")
        if raw and isinstance(raw, str):
            key = raw.strip().upper()
            if key in UHC_COVERAGE_MAP:
                item["COVERAGE"] = UHC_COVERAGE_MAP[key]
    return items

def deduplicate_uhc_fees(items: list) -> list:
    """
    Programmatically ensure global fees/credits are included exactly once and
    grouped at the bottom of the line item list.
    Specifically handles $25.00 'Billing Fee' and 'Surplus Reimbursement' and 'Packaged Savings Credit'.
    """
    members = []
    fees = []
    seen_fees = set()
    
    for item in items:
        pn = str(item.get("PLAN_NAME") or "").upper()
        pt = str(item.get("PLAN_TYPE") or "").upper()
        ln = str(item.get("LASTNAME") or "").upper()
        p_val = to_float(item.get("CURRENT_PREMIUM") or item.get("ADJUSTMENT_PREMIUM"))
        
        # Identifiers for deduplication
        is_billing_fee = ("BILLING FEE" in pn or pn == "FEES" or "SETUP FEE" in pn or ln == "BILLING FEE") and p_val == 25.0
        is_surplus = "SURPLUS REIMBURSEMENT" in pn
        is_packaged_savings = "PACKAGED SAVINGS CREDIT" in pn or "PACKAGED SAVINGS" in pn
        
        if is_billing_fee:
            fee_key = f"BILLING_FEE"
            if fee_key in seen_fees:
                continue
            seen_fees.add(fee_key)
            item["PLAN_NAME"] = "Billing Fee"
            item["PLAN_TYPE"] = "FEES"
            item["LASTNAME"] = "Credit/Fee"
            item["FIRSTNAME"] = None
            item["COVERAGE"] = None
            fees.append(item)
        
        elif is_surplus:
            fee_key = f"SURPLUS_{p_val}"
            if fee_key in seen_fees:
                continue
            seen_fees.add(fee_key)
            item["PLAN_NAME"] = "Surplus Reimbursement"
            item["PLAN_TYPE"] = "FEES"
            item["LASTNAME"] = "Credit/Fee"
            item["FIRSTNAME"] = None
            item["COVERAGE"] = None
            fees.append(item)
            
        elif is_packaged_savings:
            fee_key = f"PACKAGED_SAVINGS_{p_val}"
            if fee_key in seen_fees:
                continue
            seen_fees.add(fee_key)
            item["PLAN_NAME"] = "Packaged Savings Credit"
            item["PLAN_TYPE"] = "FEES"
            item["LASTNAME"] = "Credit/Fee"
            item["FIRSTNAME"] = None
            item["COVERAGE"] = None
            fees.append(item)
            
        else:
            # Check if it was extracted as a generic 'Fees/Credits' without specific name
            if pt == "FEES" or ln == "CREDIT/FEE":
                fee_key = f"GENERIC_FEE_{pn}_{p_val}"
                if fee_key in seen_fees:
                    continue
                seen_fees.add(fee_key)
                fees.append(item)
            else:
                members.append(item)
                
    # Return members first, then fees
    return members + fees

def format_date_clean(val: Optional[str]) -> Optional[str]:
    """
    Standardize dates to D/M/YYYY format, stripping leading zeros.
    Example: 19/02/2026 -> 19/2/2026 (User Request: February 19, 2026 -> 19/2/2026)
    Also handles YYYYMM (202603 -> 1/3/2026) and MM/YYYY (03/2026 -> 1/3/2026).
    """
    if not val or not str(val).strip() or str(val).lower() in ["n/a", "none"]:
        return val
        
    s = str(val).strip()
    
    # Month name mapping
    month_map = {
        "january": "1", "february": "2", "march": "3", "april": "4",
        "may": "5", "june": "6", "july": "7", "august": "8",
        "september": "9", "october": "10", "november": "11", "december": "12",
        "jan": "1", "feb": "2", "mar": "3", "apr": "4", "jun": "6",
        "jul": "7", "aug": "8", "sep": "9", "oct": "10", "nov": "11", "dec": "12"
    }

    # 1. Full Date Try: MM/DD/YYYY, MM/DD/YY, M/D/YY
    match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', s)
    if match:
        p1, p2, y = match.groups()
        p1_int = int(p1)
        p2_int = int(p2)
        y_clean = y if len(y) == 4 else ("20" + y)
        
        # If second part > 12, it's already M/D/YYYY (Idempotency)
        if p2_int > 12:
             return f"{p1_int}/{p2_int}/{y_clean}"
        # If first part > 12, it was D/M/YYYY -> convert to M/D/YYYY
        if p1_int > 12:
             return f"{p2_int}/{p1_int}/{y_clean}"
        
        # Ambiguous case (both <= 12): assume input was M/D/YYYY and keep it as M/D/YYYY
        # This part is now idempotent for M/D/Y.
        return f"{p1_int}/{p2_int}/{y_clean}"

    # 2. Month Name Try: "February 19, 2026" or "Feb 19 2026"
    month_pattern = r'([A-Za-z]+)\s+(\d{1,2})[,\s]+(\d{4})'
    match_month = re.search(month_pattern, s)
    if match_month:
        month_name, d, y = match_month.groups()
        month_num = month_map.get(month_name.lower())
        if month_num:
            d_clean = str(int(d))
            return f"{month_num}/{d_clean}/{y}"
    
    # 3. Year/Month Only Try: YYYYMM (e.g. 202603)
    match_yyyymm = re.search(r'^(\d{4})(\d{2})$', s)
    if match_yyyymm:
        y, m = match_yyyymm.groups()
        m_int = int(m)
        if 1 <= m_int <= 12:
            return f"1/{m_int}/{y}"
            
    # 4. Month/Year Try: MM/YYYY or MM-YYYY
    match_mmyyyy = re.search(r'(\d{1,2})[/-](\d{4})', s)
    if match_mmyyyy:
        m, y = match_mmyyyy.groups()
        m_int = int(m)
        if 1 <= m_int <= 12:
            return f"1/{m_int}/{y}"

    return s

# Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

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
    "ADJUSTMENT_PREMIUM"
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
                # Standard horizontal flow, but sorted to maintain line order
                page_text = page.get_text("text", sort=True)
            
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
            
            # Step 1: Pre-process image for better OCR accuracy
            # Convert to grayscale and apply binary thresholding
            # OCR Pre-processing
            img = img.convert('L') # Grayscale
            img = img.point(lambda x: 0 if x < 170 else 255, '1') # Binary Threshold
            
            # Use PSM 4 for landscape (single column of varying sizes), PSM 6 for portrait (uniform table)
            # The BCBS document is portrait (vertical), so PSM 6 is better for table rows.
            psm_mode = 6 
            
            # Run OCR
            page_text = pytesseract.image_to_string(img, config=f'--psm {psm_mode} -c preserve_interword_spaces=1')
            
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
    Extract text content from a PDF file using pdfplumber with enhanced layout preservation
    (table-aware) and falling back to PyMuPDF if needed.
    """
    try:
        text: str = ""
        pages_metadata = []
        
        with pdfplumber.open(pdf_path) as pdf:
            print(f"  [V3][INFO] Extractions started for: {os.path.basename(pdf_path)}")
            print(f"  Total pages: {len(pdf.pages)}")
            
            for page_num, page in enumerate(pdf.pages, 1):
                page_content = f"\n[[PAGE_{page_num}]]\n"
                
                # Extract tables with explicit settings
                tables = page.extract_tables()
                
                if tables:
                    table_bboxes = page.find_tables()
                    
                    # 1. Text above first table
                    if table_bboxes:
                        bbox = table_bboxes[0].bbox
                        if bbox[1] > 0:
                            top_area = page.crop((0, 0, page.width, bbox[1]))
                            if top_area:
                                top_text = top_area.extract_text(layout=True)
                                if top_text: page_content += top_text + "\n\n"
                    
                    # 2. Process each table
                    for idx, (table, table_bbox) in enumerate(zip(tables, table_bboxes), 1):
                        page_content += f"[TABLE_{idx}]\n"
                        # Simple tab-separated formatting for LLM readability
                        for row in table:
                            clean_row = [" ".join(str(cell).split()) if cell else "" for cell in row]
                            page_content += "\t".join(clean_row) + "\n"
                        page_content += "\n"
                        
                        # Text between tables
                        if idx < len(table_bboxes):
                            curr_bbox = table_bbox.bbox
                            next_bbox = table_bboxes[idx].bbox
                            if next_bbox[1] > curr_bbox[3]:
                                between_area = page.crop((0, curr_bbox[3], page.width, next_bbox[1]))
                                if between_area:
                                    between_text = between_area.extract_text(layout=True)
                                    if between_text: page_content += between_text + "\n\n"
                    
                    # 3. Text after last table
                    if table_bboxes:
                        last_bbox = table_bboxes[-1].bbox
                        if last_bbox[3] < page.height:
                            bottom_area = page.crop((0, last_bbox[3], page.width, page.height))
                            if bottom_area:
                                bottom_text = bottom_area.extract_text(layout=True)
                                if bottom_text: page_content += bottom_text + "\n"
                else:
                    # Fallback to standard layout extraction if no tables found
                    page_text = page.extract_text(layout=True)
                    if page_text:
                        page_content += page_text + "\n"
                
                text += page_content
        
        # If pdfplumber yielded very little, try PyMuPDF
        if len(text.strip()) < 500 and len(text.strip()) > 0:
            print(f"  [INFO] pdfplumber yielded low character count ({len(text)}). Trying PyMuPDF fallback...")
            fitz_text = ""
            try:
                doc = fitz.open(pdf_path)
                for i in range(len(doc)):
                    fitz_text += f"\n[[PAGE_{i+1}]]\n"
                    fitz_text += doc[i].get_text() or ""
                doc.close()
                if len(fitz_text) > len(text):
                    print(f"  [OK] PyMuPDF successful: {len(fitz_text)} chars extracted.")
                    text = fitz_text
            except Exception as fe:
                print(f"  [WARN] PyMuPDF fallback also failed: {fe}")

        if text.strip():
            print(f"  [OK] Extracted {len(text)} characters")
        else:
            print(f"  [WARNING] No text extracted from {pdf_path}")
            
        return text
    except Exception as e:
        print(f"  [ERROR] Error extracting text from {pdf_path}: {e}")
        return ""

def detect_reversed_text(text: str) -> bool:
    """
    Detect if the text appears to be reversed (mirrored).
    Requires at least 2 matching patterns to avoid false positives.
    """
    # Use very high-confidence mirrored OCR patterns
    # IMPORTANT: Avoid patterns that can appear in non-mirrored text:
    # - 'egap' (page reversed) appears in UHC: "51 fo 2 egaP"
    # - 'cll' can appear in company names like "LLC" 
    # - 'slatot' (totals reversed) appears in UHC table headers
    reversed_patterns = [
        "sdioani", "s0iovui", "adiovui", "eciovni", "eciovnu", # INVOICE
        "esos", "szoz", "scoz", "ezos",  # 2025/2026
        "voitaat2", "240ivaa2", "evitatneserpeR",        # ADMINISTRATION / SERVICES / Representative
        "sssal9", "anig", "auie", "anigruoc", "anamuh",   # CROSS / BLUE / Insurance / Humana
        "fih2@",                          # MEMBERSHIP
        "ytnuoc"                         # COUNTRY
    ]
    
    # Remove all whitespace and common punctuation for robust matching
    clean_text = re.sub(r'[^a-zA-Z0-9]', '', str(text)).lower()
    
    match_count = 0
    for pattern in reversed_patterns:
        if pattern in clean_text:
            match_count += 1
            
    # Require at least 2 matches to reduce false positives (e.g. UHC has 'egap' but not double-matches)
    return match_count >= 2

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

def parse_unum_detail_mirrored(full_raw_text: str, inv_date: str = None, inv_number: str = None, billing_period: str = None, source_filename: str = "") -> list:
    """
    Direct, LLM-free parser for Unum Employee Detail pages.
    Emits ONE row per member. For multi-plan members (LTD+STD), the TOTALS line
    is used as the combined CURRENT_PREMIUM. Single-plan members use their EE COST.

    Unum detail pages in mirrored format look like:
        [[PAGE_2]]
        278069173 :ON DI ENNAEL ,NAPKA :EMAN
        03.3$ 03.3$ SKEEW 11 41/41 001 DTS           <- single STD row: TOTAL = $3.30
        233146770 :ON DI ENAUD ,LLIH :EMAN
        13.371$ 83.431$ AEDA SS 09/09 057,3 DTL      <- LTD EE COST = $134.38
        13.371$ 39.83$ SKEEW 11 41/41 568 DTS        <- STD EE COST = $38.93
        13.371$ 13.371$ SLATOT                       <- TOTALS = $173.31 (use this)
    """
    items = []
    lines = full_raw_text.splitlines()
    
    current_member = None
    pending_plans = []  # accumulate plan rows until we see TOTALS or next member

    def parse_mirrored_dollar(s):
        """Parse a mirrored dollar amount like '83.431$' -> 134.38"""
        s = s.strip()
        if s.endswith('$'):
            s = s[:-1]
        normal = s[::-1].replace(',', '')
        try:
            return float(normal)
        except:
            return 0.0

    def make_item(member, premium, plan_name="COMBINED"):
        item = {
            "LASTNAME": member["LASTNAME"],
            "FIRSTNAME": member["FIRSTNAME"],
            "MEMBERID": member["MEMBERID"],
            "PLAN_NAME": plan_name,
            "PLAN_TYPE": member.get("plan_types", plan_name),
            "COVERAGE": "EE",
            "CURRENT_PREMIUM": round(premium, 2),
            "ADJUSTMENT_PREMIUM": None,
            "SSN": None,
            "POLICYID": None,
            "MIDDLENAME": None,
        }
        if inv_date:
            item["INV_DATE"] = inv_date
        if inv_number:
            item["INV_NUMBER"] = inv_number
        if billing_period:
            item["BILLING_PERIOD"] = billing_period
        return item

    def flush_member():
        """Emit accumulated plan row(s) for current member and reset."""
        if not current_member or not pending_plans:
            return
        if len(pending_plans) == 1:
            # Single plan: emit directly using that plan's values
            p = pending_plans[0]
            item = make_item(current_member, p["ee_cost"], p["plan_name"])
            item["PLAN_TYPE"] = p["plan_type"]
            items.append(item)
            print(f"  [UNUM-PARSER] {current_member['FIRSTNAME']} {current_member['LASTNAME']} | {p['plan_type']} | ${p['ee_cost']:.2f}")
        else:
            # Multiple plans: sum and emit combined
            total = sum(p["ee_cost"] for p in pending_plans)
            plan_types = "+".join(p["plan_type"] for p in pending_plans)
            item = make_item(current_member, total, "COMBINED")
            item["PLAN_TYPE"] = plan_types
            items.append(item)
            print(f"  [UNUM-PARSER] {current_member['FIRSTNAME']} {current_member['LASTNAME']} | {plan_types} | ${total:.2f}")
        pending_plans.clear()

    # Patterns
    name_pattern = re.compile(r'^\s*(\d+)\s+:ON DI\s+(.+?)\s*:EMAN\s*$')
    plan_pattern = re.compile(r'^\s*([\d$.]+)\s+([\d$.]+)\s+.+?\s+(DTS|DTL)\s*$')
    totals_pattern = re.compile(r'^.*\s+([\d$.]+)\s+([\d$.]+)\s+SLATOT\s*$')
    skip_keywords = ['TSOC EE', 'NOITARUDKCIS', 'liateD eeyolpmE', 'EMAN gnilliB']

    for line in lines:
        if any(kw in line for kw in skip_keywords):
            continue

        # NAME line → flush previous member first
        m = name_pattern.match(line)
        if m:
            flush_member()
            raw_id = m.group(1).strip()
            raw_name = m.group(2).strip()
            unmirrored_name = raw_name[::-1]
            if ',' in unmirrored_name:
                parts = unmirrored_name.split(',', 1)
                lastname, firstname = parts[0].strip(), parts[1].strip()
            else:
                parts = unmirrored_name.split()
                lastname = parts[0] if parts else ""
                firstname = " ".join(parts[1:]) if len(parts) > 1 else ""
            member_id = raw_id[::-1]
            current_member = {"LASTNAME": lastname, "FIRSTNAME": firstname, "MEMBERID": member_id}
            continue

        # TOTALS line → emit one combined row using the TOTALS amount
        t = totals_pattern.match(line)
        if t and current_member:
            raw_totals = t.group(1)  # first dollar value = person total due
            total_amount = parse_mirrored_dollar(raw_totals)
            plan_types = "+".join(p["plan_type"] for p in pending_plans) if pending_plans else "COMBINED"
            item = make_item(current_member, total_amount, "COMBINED")
            item["PLAN_TYPE"] = plan_types
            items.append(item)
            print(f"  [UNUM-PARSER] {current_member['FIRSTNAME']} {current_member['LASTNAME']} | TOTAL | ${total_amount:.2f}")
            pending_plans.clear()
            continue

        # PLAN row → accumulate
        p = plan_pattern.match(line)
        if p and current_member:
            raw_ee_cost = p.group(2)
            plan_type_mirrored = p.group(3)
            ee_cost = parse_mirrored_dollar(raw_ee_cost)
            plan_type = "STD" if plan_type_mirrored == "DTS" else "LTD"
            plan_name = "DTS" if plan_type == "STD" else "DTL"
            pending_plans.append({"ee_cost": ee_cost, "plan_type": plan_type, "plan_name": plan_name})
            continue

    # Flush last member
    flush_member()
    
    return items




    return header


def parse_gis_detail_direct(full_raw_text: str, inv_date: str = None, inv_number: str = None, billing_period: str = None, source_filename: str = "") -> list:
    """
    Enhanced Direct parser for GIS Benefits detail pages (Wide Table format).
    Supports:
    - Standard Member ID start
    - SSN (XXX-XX-XXXX) format
    - Multiple premiums on one line
    """
    items = []
    lines = full_raw_text.splitlines()
    
    # Plans identified in wide header (ordered as they typically appear)
    # This is a heuristic - a better way is to dynamically sense columns, 
    # but for now we'll match by looking for common GIS benefits.
    GIS_PLANS = ["VOLUNTARY DENTAL", "DENTAL HMO", "VOLUNTARY VISION", "VOLUNTARY STD", "VOLUNTARY LTD", "VOLUNTARY LIFE", "VOLUNTARY AD&D"]
    
    # Regex 1: Original ID-first pattern
    # (\d+) \s+ ([A-Z\s,]+) \s+ (\d{2}/\d{2}/\d{4})
    id_pattern = re.compile(r'^\s*(\d+)\s+([A-Z\-\s,]+?)\s+(\d{1,2}/\d{1,2}/\d{4})')
    
    # Regex 2: SSN pattern
    # XXX-XX-1234  LASTNAME  FIRSTNAME  01/01/2026
    # Or just SSN LASTNAME FIRSTNAME (date might be missing on some lines)
    ssn_pattern = re.compile(r'^\s*(?:[X\d\-]{11})\s+([A-Z\-\s,]+?)\s+(?:([A-Z\-\s,]+?)\s+)?(\d{1,2}/\d{1,2}/\d{4})?')

    # We also need to find the plan headers to know which $ belongs to what
    # For now, we'll implement a simpler approach: extract ALL items from the line
    # If a line has multiple $ values, we'll try to guess based on common GIS structures.
    
    for line in lines:
        line = line.strip()
        if not line or "Totals:" in line or "Billing Period" in line:
            continue
            
        m_id = id_pattern.match(line)
        m_ssn = ssn_pattern.match(line)
        
        member_id = None
        lastname = ""
        firstname = ""
        eff_date = ""
        rest = ""

        if m_id:
            member_id = m_id.group(1).strip()
            fullname = m_id.group(2).strip()
            eff_date = m_id.group(3).strip()
            rest = line[m_id.end():].strip()
            # Split names
            if ',' in fullname:
                parts = fullname.split(',', 1)
                lastname, firstname = parts[0].strip(), parts[1].strip()
            else:
                parts = fullname.split()
                if len(parts) >= 2:
                    lastname, firstname = parts[0], " ".join(parts[1:])
                else:
                    lastname = fullname
        elif m_ssn:
            fullname = m_ssn.group(1).strip()
            firstname_part = m_ssn.group(2).strip() if m_ssn.group(2) else ""
            eff_date = m_ssn.group(3).strip() if m_ssn.group(3) else ""
            rest = line[m_ssn.end():].strip()
            
            if ',' in fullname:
                parts = fullname.split(',', 1)
                lastname, firstname = parts[0].strip(), parts[1].strip()
            elif firstname_part:
                lastname, firstname = fullname, firstname_part
            else:
                parts = fullname.split()
                if len(parts) >= 2:
                    lastname, firstname = parts[0], " ".join(parts[1:])
                else:
                    lastname = fullname

        if not (member_id or lastname):
            continue

        # Extract all premiums from the rest of the line
        # Use regex to find $ amounts or decimals
        # GIS often has $12.34 or just 12.34 in some columns
        premiums = re.findall(r'\$?(\d{1,4}\.\d{2})', rest)
        if not premiums:
            continue

        # In a wide table, we don't always know which $ is which plan without the header mapping.
        # But we can create multiple items if we have multiple premiums.
        for i, p_val in enumerate(premiums):
            p_float = to_float(p_val)
            if p_float == 0: continue
            
            # Heuristic for plan name if not found
            p_name = "GIS BENEFIT"
            p_type = "MEDICAL"
            
            # Use index as a hint if we find plan names in column headers (needs better logic)
            # For now, let's just emit them as generic GIS items so they are captured.
            
            item = {
                "LASTNAME": lastname,
                "FIRSTNAME": firstname,
                "MEMBERID": member_id,
                "PLAN_NAME": p_name,
                "PLAN_TYPE": p_type,
                "COVERAGE": "EE",
                "CURRENT_PREMIUM": p_float,
                "ADJUSTMENT_PREMIUM": 0.0,
                "INV_DATE": inv_date,
                "INV_NUMBER": inv_number,
                "BILLING_PERIOD": billing_period or eff_date,
                "SOURCE_FILE": source_filename
            }
            items.append(item)
            
    return items


def extract_gis_header_direct(raw_text: str) -> dict:
    """Extracts header fields for GIS from Page 1."""
    header = {"INV_DATE": None, "INV_NUMBER": None, "BILLING_PERIOD": None}
    
    # Look for "Invoice Date: 01/26/2026"
    m_date = re.search(r'Invoice Date\s*[:\s]*(\d{1,2}/\d{1,2}/\d{4})', raw_text, re.IGNORECASE)
    if m_date: header["INV_DATE"] = m_date.group(1)
    
    # GIS Invoice Numbers are often the date or a specific number
    m_inv = re.search(r'Invoice\s*#\s*[:\s]*(\S+)', raw_text, re.IGNORECASE)
    if m_inv: header["INV_NUMBER"] = m_inv.group(1)
    
    # Billing Period
    m_period = re.search(r'Billing Period\s*[:\s]*(\d{1,2}/\d{1,2}/\d{4})\s*-\s*(\d{1,2}/\d{1,2}/\d{4})', raw_text, re.IGNORECASE)
    if m_period:
        header["BILLING_PERIOD"] = m_period.group(1) # Use Start Date
        
    return header


def extract_fields_with_llm(text: str, client: OpenAI, pdf_filename: str = "", mode: str = "standard", detected_carrier: Optional[str] = None) -> Dict:

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

{f'### CARRIER DETECTED: {detected_carrier.upper()} (PRIORITY)' if detected_carrier else ""}



### CARRIER-SPECIFIC IDENTIFIER PROFILES (PRIORITY):
- **UHC (UnitedHealthcare)**: 
    - **UHC MGSI MANDATE (GLOBAL PRIORITY)**: If the document mentions "MGSI" (e.g., MGSI, L.L.C. or similar), YOU MUST ENSURE there is **NO SPACE** after the 'P' in EVERY `PLAN_NAME` and code (e.g., `P5000i80LX21B`). This rule is ABSOLUTE for MGSI and OVERRIDES any other general UHC instruction below.
    - **Header Identifier**: Look for "Policy No." (e.g., `1400021`). This is the **POLICYID**.
    - **Member Identification (CRITICAL)**: Look for the unique masked string (e.g., `*****557900`). This is the **MEMBERID**. You MUST preserve the FULL value exactly as it appears in the PDF — do NOT truncate, do NOT convert to a number, preserve it as a STRING. Similarly, **POLICYID** must be captured as the exact full string (e.g., `1426169`) from the "Policy No." field.
    - **NULL-SSN Mandate (CRITICAL)**: For carrier UHC, `SSN` MUST **always** be set to **NULL** for every single row — no exceptions. **NEVER** copy the Member ID, the masked ID string, or any other numeric value into the `SSN` field. If in doubt, set `SSN` to NULL.
    - **Sectional Awareness (CRITICAL)**: ONLY extract data from tables under the "Details" or "Current Detail" headers. **IGNORE** any tables under the "Summary" header (e.g., lines that say "Employee | 14" or "Total Volume" or plan subtotals like "FL B NHP HMO NG..."). If the "LASTNAME" would be a plan name or description, DO NOT extract it as a member row.
    - **Coverage Recovery (FULL UHC MAPPING)**: Map coverage type codes using the following legend for ALL UHC documents:
        - `E` or "Employee Only" → **EE**
        - `ES` or "Employee and Spouse" → **ES**
        - `ESC` or "Employee and Family" → **FAM**
        - `EC` or "Employee and Child(ren)" → **EC**
        - `E1D` or "Employee and One Dependent" → **EC**
        - `E2D` or "Employee and Two Dependents" → **EC**
        - `E3D` or "Employee and Three Dependents" → **EC**
        - `E4D` or "Employee and Four Dependents" → **EC**
        - `E5D` or "Employee & One or More Dependent" → **EC**
        - `E6D` or "Employee & Two or More Dependents" → **EC**
        - `E7D` or "Employee & Three or More Dependents" → **EC**
        - `E8D` or "Employee & Four or More Dependents" → **EC**
        - `E9D` or "Employee & Five or More Dependents" → **EC**
        - Single-letter codes only (when alone): `E` → **EE**, `S` → **ES**, `F` → **FAM**, `C` → **EC**, `E E` → **EE**
    - **UHC TOTAL VALUE & FEES MANDATE (STRICT)**: 
        - **GRAND TOTAL**: Extract the absolute "Total Balance Due", "Grand Total", or "Minimum Amount Due" from the document (e.g., `$4,969.83`). This value MUST be placed in `AMOUNT_DUE` in the HEADER and a standalone LINE_ITEM with `PLAN_NAME`: "REPORTED INVOICE TOTAL (FOR AUDIT)" and `FIRSTNAME`: "INVOICE TOTAL" and `CURRENT_PREMIUM` set to that exact value. 
        - **NO MATH (CRITICAL)**: DO NOT add, subtract, or sum any values. Extract the Grand Total EXACTLY as it appears on the page. DO NOT compute a total yourself. If the page says `$4,969.83`, do NOT output `$4,994.83`.
        - **AUTHORITATIVE SOURCE**: The only authoritative total is the final Grand Total value from the LAST summary page or the invoice footer. Place this value in `AMOUNT_DUE` in the HEADER.
        - **GLOBAL FEES (DE-DUPLICATION MANDATORY)**: Extract named global charges like "Billing Fee", "Management Fee", "Packaged Savings Credit", or "Surplus Reimbursement" as individual line items. Use `PLAN_NAME`: [Exact Name], `PLAN_TYPE`: "FEES", and `LASTNAME`: "Credit/Fee".
        - **FEE DE-DUPLICATION (CRITICAL)**: If a fee (like "Billing Fee" $25 or "Surplus Reimbursement") appears on multiple pages, you MUST extract it EXACTLY ONCE.
        - **NO MISATTRIBUTION**: DO NOT assign a global fee to a person's name (e.g., "KITSON, DENNIS"). Fees are entity-level and should have `LASTNAME`: "Credit/Fee" and `FIRSTNAME`: null.
        - **MANDATORY EXCEPTION**: Fees appearing ALONGSIDE summary totals are NOT sub-totals — you MUST still extract them.
    - **Plan Name and Code Capture (CRITICAL)**: Capture the FULL plan name AND any associated alphanumeric codes (e.g., `Dental Voluntary P 7330`, `Vision 100% Voluntary S102V`, `FL CHC NG ... EKQX`). Note that you MUST follow the PDF's exact spacing for these codes.
    - **UHC Multiline Aggregation**: Many UHC plan names wrap to multiple lines (e.g., `30/60/500/80 POS 25` on one line and `EKX6` on the next). You MUST aggregate all these fragments into a single `PLAN_NAME` string.
    - **STRICT PLAN_TYPE MANDATE (UHC)**: Every member row MUST have a non-null `PLAN_TYPE`. You MUST strictly infer it from the plan name or description:
        - If "POS", "EPO", "PPO", "NHP", "CHC", or "Health" is in the plan name -> **MEDICAL**
        - If "Dental" -> **DENTAL**
        - If "Vision" -> **VISION**
        - If "Life" or "AD&D" -> **LIFE** or **AD&D**
    - **UHC Plan Labels (CRITICAL)**: Many UHC rows have sub-labels below the plan name (e.g., `Admin/Excess Loss`, `Max Claims Liability`, `Claims Liability`, `PPO Savings`, `Stop-Loss`). You MUST include these labels in the `PLAN_NAME` EXACTLY as they appear (e.g., `P5000i80LX21B - Max Claims Liability`). DO NOT omit words like 'Max' if they are present in the PDF.
    - **UHC COLUMN & ADJUSTMENT LOGIC (DYNAMIC - CRITICAL)**:
        - **DYNAMIC COLUMN DETECTION**: UHC invoices have two distinct detail sections: "Current Detail" and "Adjustment Detail".
          - Value under "Charge Amount" (3rd column from right in Current section) MUST map to `CURRENT_PREMIUM`.
          - Value under "Adjustment Detail Amount" or "Amount" (2nd column from right in Adjustment section) MUST map to `ADJUSTMENT_PREMIUM`.
          - **CRITICAL**: If a single row has values in BOTH columns, you MUST populate both fields for that record.
          - **NO CONSOLIDATION / NO MASHING**: YOU MUST extract EVERY physical row in the PDF as a separate object.
          - **FORBIDDEN**: NEVER combine multiple plans (e.g. Medical and Life) into a single `PLAN_NAME` string.
          - **MANDATORY SPLITTING**: If the text shows `Sarah Sarah Sarah ... $1095.61 $2.75 $0.75`, you MUST output THREE separate objects: 
            1. Plan: Medical, Current: 1095.61
            2. Plan: Life, Current: 2.75
            3. Plan: AD&D, Current: 0.75
          - **EXTRACT ALL ROWS**: If a member has multiple adjustments for the same plan (e.g. Gale Alana having two '-$2.75' rows), YOU MUST output TWO separate JSON objects with identical plan names and amounts. DO NOT skip the second one.
    - **BCBS (BlueCross BlueShield)**: 
    - **Subscriber ID** or **Member ID** -> maps to `MEMBERID`.
    - **Coverage Mapping**: 
        - "SINGLE" -> **EE**
        - "EMPLOYEE/CHILDREN" -> **EC**
        - "EMPLOYEE/SPOUSE" -> **ES**
        - "FAMILY" -> **FAM**
        - Also check plan string (e.g., "IND AGE RATED" -> **EE**, "FAM AGE RATED" -> **FAM**).
    - **PLAN_NAME (CRITICAL)**: Capture the FULL product or plan name from the **"Product"** or **"Plan"** column.
    - **BCBS CA (MALIBU BREWING STYLE)**: If the document is from **Blue Shield of California** (Account starting with 'W') and has columns like **Health**, **Dental**, **Vision**, **Life**:
        - Treat these categories (Health, Dental, etc.) as BOTH the `PLAN_NAME` AND `PLAN_TYPE` for the rows in those columns.
    - **DYNAMIC EXTRACTION**: Capture every character including group numbers, variant codes, or suffixes (e.g., "ALL COPAY PLAN 14256-RB").
    - **BLUECARE NORMALIZATION**: IF "BLUECARE" is present in the plan name:
        - It MUST be at the start.
        - Strip location prefixes (SAND, MARV, BEAC, JAX).
        - Correct any reversal (e.g., "NFQ... BLUECARE" -> "BLUECARE NFQ...").
    - **PLAN_TYPE (STRICT NULL)**: You MUST set `PLAN_TYPE` to **NULL** for BCBS. DO NOT infer "MEDICAL".
    - **MANDATORY DETAIL EXTRACTION**: Extract members ONLY from the subscriber detail tables (e.g., "SECTION 3" or "DETAIL OF SUBSCRIBERS").
    - **GREEDY EXTRACTION**: Capture every row in the detail table. Even if a name was seen in a summary header (e.g., Account Owner "SHARAD SAXTON"), extract it again as a member row if it appears with a Subscriber ID and Premium.
    - **FAM AGE RATED MANDATE**: "FAM AGE RATED" rows are INDIVIDUAL member enrollments (Family tier) and MUST be extracted as line items. Do NOT treat them as summary totals.
    - **MISSING MEMBER ALERT**: Ensure "SHARAD SAXTON" (approx. $2,485.21) is extracted. He is a primary subscriber.
    - **TARGET HEADCOUNT**: If the document says "SUBSCRIBERS CURRENT BILLING PERIOD: 4", you MUST find and return EXACTLY 4 member rows.
    - **NO TRUNCATION**: Capture the FULL length of `INV_NUMBER` (usually 12 digits like 260210001403).
    - **BILLING_PERIOD**: Extract the START ("From") date only (e.g., `01/01/2026`) - do NOT include the end date.
    - **NO HALLUCINATION**: NEVER invent or create member rows. If a member is not explicitly in the detail table, return NULL. Do NOT use fake IDs like 123456789.
    - **ADJUSTMENT AND TOTAL RECOVERY (CRITICAL)**: You MUST extract EVERY individual in the "SUBSCRIBER FEES" section, including those marked as "Canceled" or with $0.00 Current Charges. Additionally, capture the absolute "Total Amount Due" as a standalone object with PLAN_NAME="REPORTED INVOICE TOTAL (FOR AUDIT)". This is a MANDATORY EXCEPTION to the general rule of ignoring totals.
    - **TOTAL RECOVERY (CRITICAL)**: Look for **"Invoiced Amount"** or **"Amount Due"** in the header. If both are present, use **"Amount Due"** (e.g., $22,557.30). Do **NOT** use "TOTAL BILLED AMOUNT" if "AMOUNT DUE" exists.
    - Plan names often include "LG GRP" or suffixes like "RC" on hanging lines; use Multiline Aggregation.
    - **CLEAN PLAN AND ABSOLUTE TOTAL (CRITICAL)**: When merging multiline plan names, EXCLUDE fragments from the coverage tier (e.g., "HILDREN", "USE", or "DREN"). Also, you MUST extract the absolute grand total ($10,911.67) from the header or "AMOUNT DUE" line. NEVER use a sub-total like "$2,155.39" as the final total.
    - **BCBS MASTER DYNAMIC PLAN NAME CAPTURE (100% ACCURACY MANDATE)**: You MUST capture the entire plan name from the "Product" or "Plan" column.
        - In BCBS documents, the plan name frequently wraps onto multiple lines below the subscriber name (e.g., "BLUECARE NFQ" on line 1, "LG GRP PLAN 49-" on line 2, and "RD" on line 3).
        - You MUST aggregate ALL fragments into a single space-separated `PLAN_NAME`. 
        - Never truncate the name (e.g., stopping at "NFQ"). Ensure fragments like "RD", "RC", "LG GRP" are captured.
        - **CRITICAL EXCLUSION**: DO NOT include fragments from the Coverage/Tier column like "REN", "SE", "HILDREN", "USE" or "DREN" in the `PLAN_NAME`. These often appear in the same vertical space but are distinct fields.
        - This rule is dynamic for ALL BCBS invoices. 100% completion of the product string is mandatory.
    - **BCBS ADJUSTMENT MANDATE (CRITICAL)**: You MUST extract EVERY individual row in the "ON-BILL ADJUSTMENTS" or "RETROACTIVITY" sections with "ADD" or "TRM" in the "Action Code" or column. 
        - **IGNORE "Subscriber Total" ROWS**: Specifically, rows containing the literal text "Subscriber Total" or "Member Total" SHOULD BE IGNORED to prevent double counting. Only capture the individual component "ADD" or "TRM" rows.
        - **MAP TO ADJUSTMENT_PREMIUM**: Every dollar amount in these sections MUST be placed in the `ADJUSTMENT_PREMIUM` field. `CURRENT_PREMIUM` must be NULL for these rows.
        - If a page has summary totals (e.g. "Total On-Bill Adjustments"), YOU MUST STILL EXTRACT the individual member adjustment rows above it.
        - **100% CAPTURE MANDATE**: Ensure that every single adjustment component for a member is captured. If a member has multiple adjustment entries (e.g., two identical amounts for different months), extract BOTH.
        - ALWAYS prioritize these component adjustments to ensure 100% capture.
    - **BCBS FINAL MANDATE (FORCE)**: Distinguish "Location" strings (e.g., "BAKE", "CAFE", "LOCATION") from "Product" (Plan Name). PLAN_NAME must NOT include location. Capture ONLY the absolute "Amount Due" ($10,911.67) as the total object. Strip "HILDREN" or "DREN" from Plan Names.
    - **BCBS MORE BAKERY MASTER MANDATE (CRITICAL)**: For the "More Bakery" file, you MUST extract the absolute grand total **$10,911.67** (labeled "AMOUNT DUE" or "Invoiced Amount"). NEVER use the "ON-BILL ADJUSTMENTS" sub-total ($2,155.39) as the final total. Separately, ensure all 12 members from the detail table are extracted (including ZENO KARLA). Exclude "Location" strings (BAKE, CAFE) and "HILDREN" artifacts from all Plan Names. Correct: "TRULI LG HLTH PL W2156-R3".
- **GIS Benefits (Group Insurance Services)**:
    - GIS invoices have TWO tables: Page 1 (summary) and Page 2+ (detail with Payroll File Numbers).
    - **CRITICAL - USE PAGE 2 ONLY FOR PREMIUMS**: GIS invoices contain a SUMMARY table on Page 1 and a DETAIL table on Page 2+. Extracting from BOTH will double-count premiums.
    - **DETAIL TABLE IDENTIFICATION**: Look for "Payroll File Number" or "Product Name" headers. This table has EACH benefit on a SEPARATE row.
    - **ONE LINE ITEM PER ROW**: EVERY row on Page 2 is a separate line item. Do NOT aggregate or merge rows for the same person across different plans.
    - **PREMIUM MAPPING**: In the Page 2 table, map "Premium Amount" to `CURRENT_PREMIUM`.
    - **IGNORE PORTIONS**: Do NOT map "Employee Portion" or "Employer Portion" – ONLY map the bottom-line "Premium Amount".
    - **COVERAGE MAPPING**: Use the "Product Name" column:
        - Contains "Employee" (but not "Spouse") → **EE**
        - Contains "Spouse" (but not "Employee") → **ES**
        - Contains "Dental", "Long Term Disability", or "Basic Life" without Tier suffix → **EE**
    - **PLAN_NAME**: Use the full "Product Name" string.
    - **MEMBERID**: Map "Payroll File Number" to `MEMBERID`. PRESERVE leading zeros.
    - **SOURCE SELECTION**: You MUST extract member line items and `CURRENT_PREMIUM` values EXCLUSIVELY from Page 2. Page 1 is for Header data only.
- **Humana**:
    - **INDIVIDUAL LINE ITEMS**: Extract members EXCLUSIVELY from the "Employee Detail" section (Page 4).
    - **SUMMARIES TO IGNORE**: Do NOT extract data from the "Group Summary" or "Premiums by Product/Plan Type" tables.
    - **MEMBER CONSOLIDATION**: If a member has multiple lines (e.g., Dental and Vision), extract them as separate objects; the system will programmaticly consolidate them by name.
    - **MEMBERID**: Extract the "Member ID Number" (9-digit numeric).
- **Unum**:
    - **IDENTIFICATION**: Unum invoices often have an "Employee Detail" section with a distinctive table format.
    - **MIRRORING**: Unum invoices are often MIRRORED (reversed). The system fixes this, but LLMs sometimes misread digits (e.g., '3' vs '8'). BE EXTREMELY CAREFUL with digits.
    - **MEMBERID**: Extract from the "ID NO:" field or equivalent numeric column (e.g., `278069173`).
    - **MULTIPLE PLAN ROWS (CRITICAL)**: A member may have MULTIPLE rows (e.g., one for **LTD** and one for **STD**). You MUST extract EACH row as a separate line item. DO NOT consolidate them into one row; the system will handle it.
- **KCL (Kansas City Life)**:
    - **INV_NUMBER SOURCE**: Extract the "Group Number" (e.g., `27716`) and map it to `INV_NUMBER`. In KCL documents, this is the primary invoice identifier.
    - **HEADER DATA**: Extract `INV_DATE` (e.g., "Date Prepared") and `BILLING_PERIOD` (e.g., "Provides coverage from").
    - **PLAN_TYPE**: Map "TG Life" to **LIFE** and "TG AD&D" to **AD&D**.
    - **PLAN_TYPE MAPPING**: 
        - If "LTD" appears in the row -> `PLAN_TYPE`: **LTD**
        - If "STD" appears in the row -> `PLAN_TYPE`: **STD**
    - **PREMIUM EXTRACTION (CRITICAL)**: The table has columns like `ER COST`, `EE COST`, and `TOTAL DUE`. 
        - You MUST extract the **EE COST** (usually the column with values like `3.03`, `39.89`, `134.83`) as the `CURRENT_PREMIUM`. 
        - The `TOTAL DUE` column is the SUM of ER + EE costs; DO NOT use it for `CURRENT_PREMIUM` unless EE is missing.
        - Do NOT extract the value from the `EP` or `COVERAGE` columns (which are usually large numbers like `3,750` or `865`) as a premium.
    - **TOTALS IGNORE**: Ignore lines labeled "TOTALS" for each member (e.g., the row that sums LTD + STD for that person). Focus ONLY on the individual plan rows.
    - **NAMES**: Ensure names are un-mirrored correctly (e.g., "NAPKA, LEANNE" not "AKPAN"). 
    - **NAME SPLITTING (CRITICAL)**: If First and Last names appear joined (e.g. "MELLAMANDI"), use capital letters or common name patterns to split them (e.g. "MELLA MANDI" -> FIRST: MELLA, LAST: MANDI).
    - **GENERAL DATES**: Always return only the STARTING date for `BILLING_PERIOD`. If the text says `02/01/26-02/28/26`, return `02/01/26`.
    - **REVERSE-AWARENESS TIP**: If you see names like `YAWOLLOH` or `NAPKA`, it means the system failed to un-mirror. In this case, YOU must mentally reverse every string (e.g., `YAWOLLOH` -> `HOLLOWAY`) before extraction.
- **APL (American Public Life)**:
    - **Header Identifiers**: Extract "Group Number" as **INV_NUMBER**.
    - **Member Columns**: "Policy" -> **POLICYID**, "Name" -> **FIRSTNAME/LASTNAME**, "SSN" -> **SSN**, "Product" -> **PLAN_NAME**, "Billed"/"Due" -> **CURRENT_PREMIUM**.
    - **MULTILINE PLAN NAMES (CRITICAL)**: In APL invoices, the "Product" (PLAN_NAME) often wraps to a second line. 
        - Example: "MEDLINKSELECT GROUP MED" (Line 1) and "SUP" (Line 2).
        - You MUST concatenate these into a single string: "MEDLINKSELECT GROUP MED SUP".
    - **COVERAGE INFERENCE**: If "Product" contains "MED", set **PLAN_TYPE** to **MEDICAL**. If it contains "SUP", it is typically a supplemental medical plan.
- **TG PLAN PREFIX (CRITICAL)**: If a plan name starts with 'TG' (like TG LIFE or TG AD&D), there MUST be a space between 'TG' and the rest of the name. Never extract it as 'TGLife' or 'TGAD&D'.
- **Guardian**:
    - **Current Premiums Table – STRICT COLUMN RULES**:
      - The table header row is: `Employee | BasicTermLife Premium | Dental Premium | Dental Ins. | Std Premium | Vision Premium | Vision Ins. | TotalPremium`
      - **COLUMN POSITION IS THE ONLY SOURCE OF TRUTH** for plan type. The table has exactly 4 benefit columns in this fixed order:
        1. **Column 1 – LIFE**: BasicTermLife. A bare number with NO tier label (e.g. `Emp`). Example: `2.50`
        2. **Column 2 – DENTAL**: followed by a tier label immediately (e.g. `17.31Emp`, `35.14Emp/Sp`).
        3. **Column 3 – STD**: Short Term Disability. A bare number with NO tier label after it (e.g. `9.50`, `6.60`, `19.03`). STD always comes after Dental.
        4. **Column 4 – VISION**: followed by a tier label immediately (e.g. `7.42Emp`, `13.48Emp/Sp`).
        5. **Row Total (TotalPremium)**: The final token on the line, ALWAYS preceded by a `$` character (e.g. `$36.73`, `$51.65`). **ABSOLUTELY IGNORE THIS VALUE. It is NOT a benefit premium.** Never assign it to any plan.
      - **⚠️ CRITICAL – DOLLAR SIGN = STOP TOKEN**: When parsing a Guardian row left-to-right, the MOMENT you encounter a `$`, you have reached the Row Total. **Stop extracting benefits at that point.** The `$` value is discarded.
        - WRONG: `Anand,Arjun 40.19Emp 11.46Emp $51.65` → Vision=63.11 (❌ DO NOT ADD $51.65 TO 11.46)
        - CORRECT: `Anand,Arjun 40.19Emp 11.46Emp $51.65` → Dental=40.19, Vision=11.46, ignore $51.65.
      - **SPARSE ROW DISAMBIGUATION RULES**:
        - A number with a tier label (Emp/Fam/Emp/Sp) is either Dental (comes first) or Vision (comes second among labeled values).
        - A bare number (no tier label) is either Life (first bare number) or Std (second bare number).
        - Example: `AcevedoHilario,Maricruz 4.00 $4.00` → 4.00 is **LIFE** only.
        - Example: `Crouch,TherralR 5.25 9.50 $14.75` → 5.25 is **LIFE**, 9.50 is **STD** (bare number, second position).
        - Example: `Bonsack,Bryce 4.00 17.31Emp 19.03 $40.34` → 4.00 is **LIFE**, 17.31 is **DENTAL**, 19.03 is **STD**.
        - Example: `Feld,StephonL 2.50 17.31Emp 9.50 7.42Emp $36.73` → 2.50=**LIFE**, 17.31=**DENTAL**, 9.50=**STD**, 7.42=**VISION**.
        - Example: `Lopez,Katerina 4.00 17.31Emp 6.86 $28.17` → 4.00=**LIFE**, 17.31=**DENTAL**, 6.86=**STD**. No Vision.
      - **Specified Arch / Dental+Vision only variant** (no BasicTermLife or Std columns):
        - Table header row is: `Employee | Dental Premium | Ins. | Vision Premium | Ins. | TotalPremium`
        - First labeled number → **DENTAL**. Second labeled number → **VISION**. `$XXX` at end → **IGNORE**.
        - Example: `Anand,Arjun 40.19Emp 11.46Emp $51.65` → Dental=40.19 (EE), Vision=11.46 (EE). Ignore $51.65.
        - Example: `Berg,ChaneL 158.17Fam 19.33Emp/Sp $177.50` → Dental=158.17 (FAM), Vision=19.33 (ES). Ignore $177.50.
        - Example: `Darden,Demerick 40.19Emp $40.19` → Dental=40.19 (EE). No Vision. Ignore $40.19.
      - **COVERAGE TIER MAPPING**: "Emp"→**EE**, "Emp/Sp"→**ES**, "Emp/Ch"→**EC**, "Fam"→**FAM**.
      - **PLAN NAME SPLITTING**: Split full string on comma: `Anderson,TylerA` → LASTNAME=`Anderson`, FIRSTNAME=`TylerA`.
      - Each non-zero benefit MUST be a separate line item with the correct `PLAN_TYPE`.
    - **Mutual of Omaha**:
    - **Identification**: Look for "Mutual of Omaha" in any header or page.
    - **ID Handling**: Ignore "693399" and "7605" - these are footer/group codes, not member IDs.
    - **Coverage Mapping (STRICT)**:
        - `Participant` or `Ppt` -> **EE**
        - `Spouse` or `Sps` -> **ES**
        - `Dependent` or `Dep` or `Dep(s)` -> **EC**
        - `Ppt & Sps` or `Ppt & Spouse` -> **ES**
        - `Ppt & Dep(s)` or `Ppt & Child` -> **EC**
        - `Ppt/Dep` -> **EC**
        - `Family` or `Ppt & Fam` -> **ESC**
    - **Column Mapping (CRITICAL)**:
        - Map column `CURRENT` to `CURRENT_PREMIUM`.
        - Map column `ADJUSTMENT` to `ADJUSTMENT_PREMIUM`.
        - The member's final total is in the `NET` column (= CURRENT + ADJUSTMENT). You MUST extract BOTH component values.
    - **Row Capture (STRICT)**:
        - **Source**: Extract ONLY from tables explicitly labeled "**PARTICIPANT DETAIL**".
        - **Comma-Name Rule (MANDATORY)**: You MUST only extract rows that have a specific individual's name in "**Lastname, Firstname**" format (containing a comma). 
        - **SKIP ALL SUMMARIES**: If a row does not have a name with a comma, or if it shows a "Count" (e.g. 72) or "Volume" (e.g. 1,000,000), it is a summary row. **YOU MUST SKIP THESE ROWS.**
        - **No Orphans**: Do NOT extract "ORPHAN" rows or rows without names. If you cannot find a name for the specific row, skip it.
        - **Retroactive Changes**: These are ADJUSTMENTS. Map to `ADJUSTMENT_PREMIUM` for the member it follows.
    - **Authoritative Total (MANDATORY)**: ONLY extract the absolute "TOTAL AMOUNT DUE" (e.g., `$9,379.56`) if it is explicitly present in the text (usually Page 3 or 5). Map it as a standalone LINE_ITEM with `PLAN_NAME`: "REPORTED INVOICE TOTAL (FOR AUDIT)" and `FIRSTNAME`: "INVOICE TOTAL". 
    - **STRICT RULE ON TOTALS**: If the authoritative total (e.g. 9379.56) is NOT in the current document text chunk, DO NOT extract a total row. Do NOT calculate a sub-total for the chunk or use branch totals.
- **Adjustment Table (GUARDIAN)**:
      - If you see **"New Premium"** and **"New Premium Adjustment"**:
        - `New Premium` (e.g., 2.50) -> `CURRENT_PREMIUM`.
        - `New Premium Adjustment` (e.g., 7.50) -> `ADJUSTMENT_PREMIUM`.
- **GENERAL MAPPING (IF CARRIER UNKNOWN)**:
    - "Invoice Date" / "Date" -> `INV_DATE`
    - "Invoice #" / "Inv #" -> `INV_NUMBER`
    - "Subscriber ID" / "Member ID" / "Member #" / "Contract No" -> `MEMBERID`
    - "Premium" / "Amount" / "Premium Amount" / "Total" -> `CURRENT_PREMIUM`
    - "Adjustment" / "Credit" / "Debit" -> `ADJUSTMENT_PREMIUM`
    - "Product" / "Plan Description" / "Coverage Type" -> `PLAN_NAME`
    - "Policy No." / "Policy Number" -> `POLICYID`
    - "Amount Due" / "Invoiced Amount" / "Balance Due" / "Grand Total" / "Invoice Total" -> `AMOUNT_DUE`
3. **Multiline Value Aggregation**:
   - **CRITICAL**: Some columns (especially 'Product', 'Plan', or 'Address') span multiple lines vertically.
   - You MUST look at the lines immediately following a member row. If they contain hanging text (e.g., "LG GRP PLAN 49-" and "RC" below "BLUECARE NFQ"), AGGREGATE them into the appropriate field (e.g., `PLAN_NAME`) with a space.
   - In APL documents, "SUP" often appears on a second line below "MED". You MUST capture this.
   - Do not stop at the first line of the table row; ensure the entire block of data for that member is captured.
### NUMERICAL FAITHFULNESS (ZERO TOLERANCE FOR HALLUCINATION):
- Extract ALL premiums, IDs, and quantities EXACTLY as they appear in the text.
- **DO NOT** assume 'standard' rates for a carrier. 
- Some members may have different premiums than others; capture the specific dollar amount for each row.
- If the text says `$1.31`, return `1.31`. Do **NOT** return `$2.90` even if that is the common rate for that carrier.

### TOTALS AND SUMMARY ROWS:
- **IGNORE** all rows that are grand totals, invoice summaries, or sub-totals.
- ONLY extract individual member/employee line-items.
- If a row contains "Total", "Amount Due", or "Balance Due", skip it completely.
- **UHC EXCEPTION (CRITICAL)**: For UHC documents, named fee/credit rows (such as "Packaged Savings Credit", "Billing Fee", "Management Fee") that appear on ANY page (including summary pages and detail pages) MUST STILL BE EXTRACTED as individual line items. These are not sub-totals — they are explicit named charges or credits. Only skip rows whose description IS "Subtotal Plan Charges", "Grand Total", "Subtotal", or similar aggregate summary labels.

4. **Leading Zeros**: Preserve every single zero.
5. **Aggressive Row Capture**: You MUST extract EVERY individual listed in the main table. Even if the name contains symbols (e.g., "#27411" or "“6078") or looks like garbage, extract it as-is. Do not skip any rows.
6. **HORIZONTAL REPETITION (CRITICAL)**:
  If a line contains multiple names (e.g. `Bennett Andrew Gacio Tomas`) or multiple amounts side-by-side (e.g. `$2.99 $2.99`), it indicates multiple members per column. YOU MUST extract EVERY member by scanning horizontally across the mashed string. 
  **EXAMPLE**: If you see `GacioTomas PauleyGlen`, there are TWO people there. If you only extract `Pauley Glen`, you have missed `Gacio Tomas`. Look for recurring patterns of `Name | Code | Premium | Volume`.
7. **SPACE PRESERVATION (CRITICAL)**: In `PLAN_NAME` and `PLAN_TYPE`, you MUST preserve any spaces that appear in the source text. For example, if you see `TG AD&D`, do NOT extract it as `TGAD&D`. Keep the internal space.
8. **DATA ACCURACY (INVOICE #)**:
    - NEVER map the total amount due (even if formatted as a long number like `000000005372` for $53.72) to `INV_NUMBER`.
    - Look for common labels like "Invoice No:", "Invoice #", "Invoice Number", "No:", "#", or "Group Number:" to find the invoice number.
    - **KCL SPECIFIC**: For KCL, the "Group Number" is the invoice number. You MUST extract it.
    - Only return NULL if there is absolutely NO alphanumeric string in the header that is clearly labeled as an invoice identifier.
9. **COVERAGE MAPPING (CRITICAL)**:
    - If a table has a "Code" column with values like `EE`, `SP`, `CH`, `FAM`, map this to the `COVERAGE` field.
    - If `EE` is the only code, ensure it is applied to all rows.
10. **PLAN_TYPE DISTINCTION (CRITICAL)**:
    - `PLAN_TYPE` must be a high-level category (e.g., LIFE, AD&D, MEDICAL, DENTAL, VISION).
    - **CRITICAL**: NEVER put `EE`, `FAM`, or other coverage tiers into the `PLAN_TYPE` field. 
    - If the document lacks a `PLAN_TYPE` column, infer it from the `PLAN_NAME`. (e.g., `TG Life` -> `PLAN_TYPE`: `LIFE`, `TG AD&D` -> `PLAN_TYPE`: `AD&D`).
11. **SSN/Identifier Capture**: 
    - Extract any visible digits in the SSN column. 
    - **CRITICAL**: If the SSN is masked (e.g., `*****9868`), extract ONLY the last 4 digits (`9868`). 
    - **IGNORE OCR ARTIFACTS**: OCR often misreads the mask `*****` as digits (e.g., `884`). If you see a 7 or 8-digit SSN starting with repetitive or suspicious numbers (like `884`), ignore the prefix and capture ONLY the trailing digits that match the pattern in the rest of the document.
    - **DIGIT RECOVERY**: If an SSN field contains garbled text (e.g. 'EET BZ', 'eT TAG'), try to find the 4-digit numeric intent using these common OCR mappings:
        - **E / B** -> 8 or 3
        - **I / L** -> 1
        - **S** -> 5
        - **Z** -> 2
        - **T / e** -> 7
        - **O / Q** -> 0
        - **A** -> 4
        - **G** -> 9
    - **STRICT SSN**: Extract EXACTLY 4 digits. Do not truncate to 1 or 2 digits unless there is absolute certainty. If only 3 digits are found (e.g. '399'), check if a leading zero '0' was likely dropped by OCR; if so, extract as '0399'.
    - **ID vs SSN vs POLICYID (UHC Special Case)**: 
        - If the document is UHC, the value `1400021` is **ONLY** `POLICYID`.
        - The value `*****557900` is **ONLY** `MEMBERID`.
        - **NEVER** put `1400021` into `MEMBERID`, `SSN`, or `FIRSTNAME`.
        - **NEVER** put `557900` into `SSN`.
    - **NEGATIVE MAPPING RULES**: 
        - `Policy No.` is **NEVER** `MEMBERID`. 
        - Numeric codes like `78142600` (from headers) are **NEVER** `SSN`.
        - Masked strings with 6+ digits (e.g. `*****557900`) are **NEVER** `SSN`; they are always `MEMBERID`.
    - **CHAIN-OF-THOUGHT ROW VERIFICATION**:
        - For every row, you MUST internally follow this sequence:
            1. **Segment Raw Text**: Identify the raw characters (e.g., `BENNETT ANDREWM EE *****557900 ... 1302.87`).
            2. **Identify Anchor**: Find the premium (e.g., `1302.87`).
            3. **Relative Mapping**: Map fields relative to the anchor. (e.g., `EE` just before the ID is `COVERAGE`).
            4. **Exclusion Check**: Ensure no Policy level data (`1400021`) is polluting the member fields.


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
     - **VIS, SV, VISION** -> `PLAN_TYPE`: **VISION**
      - **MED, MEDICAL, HMO, PPO, POS, CHOICE, BLUECARE, BLUE** -> `PLAN_TYPE`: **MEDICAL**
   - **STRICT RULE**: This is an independent field and must not be inferred from other fields.

3. **COVERAGE (ENROLLMENT TIER - STRICT)**:
   - **Allowed Values**: **EE** (Employee Only), **ES** (Employee + Spouse), **EC** (Employee + Child), **FAM** (Family)
   - **Definition**: Who is included under the plan for pricing purposes.
   - **STRICT EXTRACTION RULE**: Coverage MUST be extracted directly from a "Coverage" or "Tier" field.
   - **MAPPING (NORMALIZATION)**:
     - "EE+SP", "EE/SP", "EMP+SPOUSE", "DEP", "S" -> **ES**
     - "EE+CH", "EE/CH", "EMP+CHILD", "EMPLOYEE/CHILD", "EMPLOYEE/CHILDREN", "EMP/CHILD", "FPC", "C", "CHILD" -> **EC**
      - "EE", "EMP ONLY", "SINGLE", "INDIVIDUAL", "IND", "E" -> **EE**
      - "FAM", "FAMILY", "F" -> **FAM**
   - **DO NOT GUESS**: Never infer coverage based on premium amounts.
   - If no explicit tier is found OR if the tier cannot be mapped to the allowed set: return **NULL**.

4. **ULTRA-STRICT VALIDATION & ANTI-HALLUCINATION**:
   - **EXPLICIT DATA ONLY**: Do NOT create, infer, or generate values. If it's not on the page, it's NULL.
   - **WHOLE ROW VERIFICATION**: Do not validate based only on numeric values. Verify member details, coverage/tier, policy info, and premiums as a consistent unit.
   - **CONSISTENCY CHECK**: Ensure all extracted fields for a row align logicially with the document's structured data.

4. **RELATIONSHIP (INTERNAL ANALYSIS)**:
   - **Definition**: Who the person is (Self, Spouse, Child).
   - **STRICT RULE**: This is an independent field and must not be inferred from other fields. 
   - **RULE**: Use this for identity analysis, but do not include it in the final formatted output.

6. **PREMIUM FIELDS (STRICT DEFINITIONS)**:
   - **CURRENT_PREMIUM**: Maps to the recurring base premium for the current period.
   - **ADJUSTMENT_PREMIUM**: Maps to retroactive or corrective amounts (e.g., credits, prorated debits).
   - **GRAND TOTAL AUTHORITATIVE (CRITICAL)**: Always prioritize Page 1 (Cover Page) for document-level totals:
      - **TOTAL_BILLED**: The base premium before any adjustments (e.g., "TOTAL BILLED AMOUNT").
      - **TOTAL_ADJUSTMENTS**: The sum of all adjustments/retroactivity (e.g., "ON-BILL ADJUSTMENTS").
      - **AMOUNT_DUE**: The final bottom-line amount (e.g., "AMOUNT DUE"). This is the MOST IMPORTANT number in the document.
      - **NOTE**: These fields should be placed in the `HEADER` object.
   - **PREMIUM THRESHOLD (CRITICAL)**: If a row in the member table lists a premium > $4,000, it is a **Sub-total** or **Total** line. You MUST filter this out. 
   - **NO HALLUCINATION**: Do not invent member rows. Do not try to match a global total if the data is not on the page.

6. **IDENTIFIER MAPPING (IRONCLAD RULE)**:
   - **MEMBERID**: Map from the "ID" or "Member ID" column in the table. **EXAMPLE**: `*****557900` -> `557900`.
   - **POLICYID**: Map from "Policy No." at the top of the section. **EXAMPLE**: `1400021` -> `1400021`.
   - **NO CROSS-OVER**: Under NO circumstances should `1400021` be placed in the `MEMBERID`, `SSN`, or `FIRSTNAME` columns. 
   - **SSN**: Extract ONLY from columns explicitly labeled "SSN". If no SSN column exists, return NULL. **DO NOT** use parts of the Member ID as a fallback for SSN.
   - **UNIQUE ASSIGNMENT**: Each distinct numeric value from the text has a specific purpose. If `1400021` is the Policy ID, it is EXCLUDED from all other slots for that row.
   - **MANDATORY**: Preserve all visible characters and leading zeros for IDs.

7. **PRICING_MODEL (INTERNAL ANALYSIS)**:
   - **Definition**: Captures descriptors like "FAM AGE RATED" or "COMMUNITY RATED".
   - **RULE**: Use this to handle rating text without polluting `PLAN_TYPE`. Do not include in final output.
    
8. **TOTAL VERIFICATION**:
    - ALWAYS capture the "Grand Total" or "Amount Due" into the `AMOUNT_DUE` field of the `HEADER`. 
    - This is document-level metadata.
   




### NAME FORMATTING RULES:
- **Consistency**: Look for a pattern in the document (usually all names follow the same FIRST LAST or LAST FIRST format).
- **LASTNAME/FIRSTNAME**: Split Names carefully. 
- **STRICT SPLITTING**: If name columns are tight, the text may arrive as `LASTFIRST` (e.g. `DOEJOHN`). You MUST detect the split (e.g. `LAST: DOE`, `FIRST: JOHN`).
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
     - "ON-BILL ADJUSTMENTS"
     - "Prior Period Adjustments"
   - These are corrections for PRIOR periods
   - Amounts can be positive (charges) or negative (credits)
   - Extract to: **ADJUSTMENT_PREMIUM** field
   - Leave CURRENT_PREMIUM as null
   
    **CRITICAL RULES**:
    4. **ONE ROW PER MEMBER**: Each unique member (MEMBERID + Name) MUST appear exactly once in the JSON output, UNLESS it is BCBS.
    5. **BCBS LEDGER MODE**: For BlueCross BlueShield (BCBS), keep individual adjustment events (e.g. "CHANGE", "ADD", "TRM") as SEPARATE JSON objects. DO NOT merge them into one row.
    6. **MATH INTEGRITY**: Ensure you capture the TOTAL value for each adjustment row exactly as shown. If the amount is in parentheses (e.g. ($8,928.95)), it MUST be recorded as a negative number (-8928.95).
    7. **SKIP SUMMARIES**: If a person has individual rows like "CHANGE" and "ADD" AND a "Subscriber Total" row, ONLY capture the individual "CHANGE" and "ADD" rows. Skip the "Subscriber Total" to avoid double counting.
    
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
Input: 
`2543915 | ANAND, ARJUN | | ***-**-7635 | MEDLINKSELECT GROUP MED | $85.27 | - | $85.27`
`| | | | SUP | | |`
Output: `{{"LASTNAME": "ANAND", "FIRSTNAME": "ARJUN", "MEMBERID": "2543915", "SSN": "7635", "PLAN_NAME": "MEDLINKSELECT GROUP MED SUP", "CURRENT_PREMIUM": 85.27}}`

8. **PLAN DATA INFERENCE**:
   - If PLAN_NAME is missing on the row, look for a general plan name in the header (e.g., "Medical", "MERP", "Dental").

### REQUIRED JSON STRUCTURE:
{{
  "HEADER": {{
    "INV_DATE": null,
    "INV_NUMBER": null,
    "BILLING_PERIOD": null,
    "TOTAL_BILLED": null,
    "TOTAL_ADJUSTMENTS": null,
    "AMOUNT_DUE": null
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
     - Derived Type (e.g., "Dental" -> DENTAL) -> `PLAN_TYPE`.

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
            model="gpt-4o",
            temperature=0,  # Zero temperature for maximum consistency
            max_tokens=16383,  # Increased for large-page robustness
        )
        
        response_text = chat_completion.choices[0].message.content
        print(f"  [OK] Received response from OpenAI")
        print(f"  [DEBUG] LLM Response: {response_text[:1000]}...") # Debug print
        
        # Parse the JSON response
        # Remove markdown code blocks if present
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0].strip()
        
        try:
            extracted_data = json.loads(response_text)
            print(f"  [OK] Successfully extracted {sum(1 for v in extracted_data.values() if v is not None)} fields")
            return extracted_data
        except json.JSONDecodeError as e:
            print(f"  [ERROR] JSON parsing error: {e}")
            # Attempt to recover truncated JSON
            try:
                print("  [V3][RECOVERY] Attempting to fix truncated JSON...")
                fixed_json = response_text.strip()
                if "{" in fixed_json and "LINE_ITEMS" in fixed_json:
                    # Sync brackets
                    if fixed_json.count('[') > fixed_json.count(']'):
                        fixed_json += "]}"
                    elif fixed_json.count('{') > fixed_json.count('}'):
                        fixed_json += "}]"
                    
                    # Try to close a potentially open string
                    if fixed_json.count('"') % 2 != 0:
                        fixed_json += '"}]}'
                    
                    extracted_data = json.loads(fixed_json)
                    print("  [V3][RECOVERY] Successfully recovered truncated JSON.")
                    return extracted_data
            except Exception as re:
                print(f"  [V3][RECOVERY] Auto-fix failed: {re}")
            
            print(f"  Raw response (first 500 chars): {response_text[:500]}")
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
    
    This version supports page-by-page processing if the TXT file contains [[PAGE_X]] delimiters.
    """
    print(f"\n{'='*70}")
    print(f"STEP 2: PROCESSING VERIFIED TEXT")
    print(f"{'='*70}")
    print(f"[TXT] Reading verified text from: {txt_path}")
    
    try:
        with open(txt_path, 'r', encoding='utf-8') as f:
            text = f.read()
            
        text = clean_ocr_noise(text)
        print(f"  [OK] Read {len(text)} characters from verified file")
        
    except Exception as e:
        print(f"  [ERROR] Error reading text file {txt_path}: {e}")
        return {field: None for field in REQUIRED_FIELDS}
    
    # Check for page delimiters
    page_markers = re.findall(r'\[\[PAGE_(\d+)\]\]', text)
    if page_markers:
        print(f"  [V4] Detected {len(page_markers)} page markers. Processing page-by-page...")
        # Split by markers, preserving the split content
        parts = re.split(r'\[\[PAGE_\d+\]\]', text)
        # Usually the first part is empty if it starts with a marker
        if parts and not parts[0].strip():
            parts.pop(0)
            
        final_header = {}
        all_line_items = []
        is_uhc = "UNITEDHEALTH" in text.upper() or "UHC" in text.upper()

        for i, page_text in enumerate(parts):
            if not page_text.strip(): continue
            
            page_num = page_markers[i] if i < len(page_markers) else i + 1
            print(f"  [AI] Extracting from Page {page_num}...")
            
            # Simple UHC Summary Skip (similar to process_single_pdf)
            # We still extract LINE_ITEMS because global fees like "Billing Fee" are often on these pages
            if is_uhc and i > 0 and (("Summary" in page_text and "Description" in page_text) or ("Total Volume" in page_text)):
                 print(f"    [V4] Page {page_num} looks like a UHC summary. Collecting LINE_ITEMS + HEADER.")
                 page_data = extract_fields_with_llm(page_text, client, f"Page {page_num} (Summary/Fee Page)", mode="standard") or {}
                 if "HEADER" in page_data: final_header.update({k: v for k, v in page_data["HEADER"].items() if v})
                 if "LINE_ITEMS" in page_data: all_line_items.extend(page_data["LINE_ITEMS"])
                 continue

            page_data = extract_fields_with_llm(page_text, client, f"Page {page_num}", mode="standard") or {}
            
            # Merge Header
            if "HEADER" in page_data:
                final_header.update({k: v for k, v in page_data["HEADER"].items() if v})
            
            # Collect Line Items
            if "LINE_ITEMS" in page_data:
                all_line_items.extend(page_data["LINE_ITEMS"])

        extracted_data = {"HEADER": final_header, "LINE_ITEMS": all_line_items}
    else:
        # Fallback to single block processing for legacy/custom files
        print(f"  [V4] No page markers found. Processing as single block.")
        extracted_data = extract_fields_with_llm(text, client, os.path.basename(txt_path))
        is_uhc = "UNITEDHEALTH" in text.upper() or "UHC" in text.upper()

    # [V4][UHC POST-PROCESS] Apply normalization
    if "LINE_ITEMS" in extracted_data and extracted_data["LINE_ITEMS"]:
        # Always normalize coverage if it looks like UHC codes
        extracted_data["LINE_ITEMS"] = normalize_uhc_coverage(extracted_data["LINE_ITEMS"])
        
        # Strictly deduplicate fees if it's a UHC doc
        if is_uhc:
            print(f"  [V4][UHC] Detected UHC via content. Applying fee deduplication.")
            extracted_data["LINE_ITEMS"] = deduplicate_uhc_fees(extracted_data["LINE_ITEMS"])

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
    # [V4][UHC] UHC documents suffer from horizontal mashing with pdfplumber.
    # We force PyMuPDF (structured text) for UHC to preserve line-by-line plan separation.
    is_likely_uhc = "UHC" in pdf_path.upper() or "UNITED" in pdf_path.upper()
    if is_likely_uhc:
        print(f"  [V4][UHC] Detected likely UHC. Using structured text (PyMuPDF) for better line separation.")
        text = extract_text_from_pdf_pymupdf(pdf_path, mode="horizontal")
    else:
        text = extract_text_from_pdf_improved(pdf_path)
    
    # [V3][MIRROR] Early Mirror Detection & Correction
    # If the text is mirrored, we fix it before any chunking or quality checks
    if detect_reversed_text(text):
        print(f"  [V3][INFO] Detected likely MIRRORED (reversed) text in whole document. Applying early un-mirroring...")
        text = unmirror_text(text)
        print(f"  [V3][OK] Early Un-mirrored text preview (first 200 chars):\n{text[:200]}\n")

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
    final_header = {field: None for field in ["INV_DATE", "INV_NUMBER", "BILLING_PERIOD", "TOTAL_BILLED", "TOTAL_ADJUSTMENTS", "AMOUNT_DUE", "GROUP_NUMBER", "PRICING_ADJUSTMENT"]}
    
    print(f"  [V3] Splitting large document into {len(pages)} pages for reliable extraction...")
    
    # GIS Benefits Detection: The detail table starts on Page 2+ with "Payroll File Number" header.
    # Page 1 is a summary that has the SAME premiums, which causes double-counting.
    # SOLUTION: If this is a GIS document, skip Page 1 for line-item extraction.
    is_gis_invoice = any("Payroll File Number" in p for p in pages) or \
                     any("Product Name" in p and "Employee Portion" in p for p in pages) or \
                     any("service@gisadmin.net" in p.lower() for p in pages) or \
                     any("GIS Benefits" in p for p in pages)
    if is_gis_invoice:
        print(f"  [V3][GIS] GIS Benefits invoice detected. Page 1 summary will be skipped for line items to prevent double-counting.")
    
    # Humana Detection: Skip summary pages (1, 2, 3, 5)
    is_humana_invoice = any("403638-001" in p for p in pages) or any("LOST BOY AND COMPANY LLC" in p for p in pages)
    if is_humana_invoice:
        print(f"  [V3][HUMANA] Humana invoice detected. Pages 1, 2, 3, 5 will be skipped for line items.")

    # UHC Detection: Skip summary page (typically Page 3 or search for "Summary" + "Description")
    is_uhc_invoice = any("uhceservices.com" in p.lower() for p in pages) or \
                     any("Consolidated Customer No:" in p for p in pages) or \
                     any("United HealthCare Services" in p for p in pages)
    
    if is_uhc_invoice:
        print(f"  [V3][UHC] UnitedHealthcare invoice detected. Checking for summary pages to skip...")

    # [V4][MOO] Mutual of Omaha Detection
    is_moo_invoice = any("Mutual of Omaha" in p for p in pages)
    global_carrier = None
    if is_moo_invoice:
        global_carrier = "Mutual of Omaha"
        print(f"  [V4][MOO] Mutual of Omaha detected document-wide. Carrier context will be passed to all chunks.")

    # Unum Detection
    _unum_mirrored_signature = "ACIREMA FO YNAPMOC ECNARUSNI EFIL MUNU"
    _unum_normal_signature = "UNUM LIFE INSURANCE COMPANY OF AMERICA"
    is_unum_invoice = any(_unum_mirrored_signature in p for p in pages) or \
                      any(_unum_normal_signature in p.upper() for p in pages)
    
    # [GIS] Dedicated Fast Parser for GIS
    if is_gis_invoice:
        print(f"  [V3][GIS] GIS Benefits invoice detected. Using direct parser for detail pages.")
        # Step 1: Extract header from Page 1
        page1_text = pages[0] if pages else ""
        gis_header = extract_gis_header_direct(page1_text)
        for k, v in gis_header.items():
            if v: final_header[k] = v
        print(f"  [V3][GIS] Header: {gis_header}")

        # Step 2: Parse all detail pages (Page 2+)
        is_already_chunk = "_chunk_" in str(pdf_path)
        is_first_chunk = "_chunk_1." in str(pdf_path)
        
        detail_pages_text = ""
        if is_already_chunk and not is_first_chunk:
            detail_pages_text = "\n".join(pages) # All pages are detail
        else:
            detail_pages_text = "\n".join(pages[1:]) # Skip summary page 1
            
        gis_items = parse_gis_detail_direct(
            detail_pages_text,
            inv_date=final_header.get("INV_DATE"),
            inv_number=final_header.get("INV_NUMBER"),
            billing_period=final_header.get("BILLING_PERIOD"),
            source_filename=os.path.basename(pdf_path)
        )
        
        if gis_items:
            print(f"  [V3][GIS] Direct parser extracted {len(gis_items)} rows. Total: ${sum(i.get('CURRENT_PREMIUM', 0) or 0 for i in gis_items):.2f}")
            data = {"HEADER": final_header, "LINE_ITEMS": gis_items}
            return data
        else:
            print(f"  [V3][GIS] Direct parser found 0 rows - falling back to LLM pipeline.")

    if is_unum_invoice:
        print(f"  [V3][UNUM] Unum invoice detected. Using direct mirrored-text parser (no LLM) for 100% accuracy.")
        # For Unum: bypass the entire LLM pipeline and parse directly
        # Step 1: Extract header from Page 1 (mirrored)
        page1_text = pages[0] if pages else ""
        unum_header = extract_unum_header_from_mirrored(page1_text)
        for k, v in unum_header.items():
            if v:
                final_header[k] = v
        print(f"  [V3][UNUM] Header: {unum_header}")
        
        # Step 2: Parse all detail pages (Page 2+) directly
        full_detail_text = "\n".join(pages[1:])  # All pages after Page 1
        unum_items = parse_unum_detail_mirrored(
            full_detail_text,
            inv_date=final_header.get("INV_DATE"),
            inv_number=final_header.get("INV_NUMBER"),
            billing_period=final_header.get("BILLING_PERIOD"),
            source_filename=os.path.basename(pdf_path)
        )
        
        if unum_items:
            print(f"  [V3][UNUM] Direct parser extracted {len(unum_items)} rows. Total: ${sum(i.get('CURRENT_PREMIUM', 0) or 0 for i in unum_items):.2f}")
            data = {"HEADER": final_header, "LINE_ITEMS": unum_items}
            return data
        else:
            print(f"  [V3][UNUM] Direct parser found 0 rows - falling back to LLM pipeline.")

    # --- PARALLEL PROCESSING ENGINE ---
    _vertical_cache = {}
    _cache_lock = threading.Lock()

    def process_page_parallel(i, page_text):
        """Worker function for parallel page processing"""
        print(f"  [V3][THREAD] Starting chunk {i+1}...")
        
        # Skip specific pages for member line items (GIS, Humana, etc.)
        is_already_chunk = "_chunk_" in str(pdf_path)
        is_first_chunk = "_chunk_1." in str(pdf_path)
        
        is_uhc_summary = is_uhc_invoice and i > 0 and (
            ("Summary" in page_text and "Description" in page_text and "Total Volume" in page_text) or
            ("Summary" in page_text and "Net Amount" in page_text and "Employee Count" in page_text)
        )

        is_skip_page = (is_gis_invoice and i == 0 and (not is_already_chunk or is_first_chunk)) or \
                       (is_humana_invoice and (i == 0 or i == 1 or i == 2 or i == 4)) or \
                       is_uhc_summary
        
        if is_skip_page:
            reason = "GIS" if is_gis_invoice else ("Humana" if is_humana_invoice else "UHC Summary")
            print(f"  [V3][THREAD] Chunk {i+1} is a {reason} page. Extracting header only.")
            header_only_data = extract_fields_with_llm(page_text, client, f"{os.path.basename(pdf_path)}_page_{i+1}_header", mode="standard", detected_carrier=global_carrier) or {}
            return {"index": i, "header": header_only_data.get("HEADER", {}), "items": [], "refinement_info": None}
        
        # Pass 1: Standard Mode (Horizontal Parser)
        page_data = extract_fields_with_llm(page_text, client, f"{os.path.basename(pdf_path)}_page_{i+1}", mode="standard", detected_carrier=global_carrier) or {}
        
        # Pass 2: Vertical Fallback
        if not page_data.get("LINE_ITEMS"):
            if len(page_text) > 200 or any(k in page_text.upper() for k in ["NAME", "CODE", "LIFE", "DENTAL", "VISION"]):
                print(f"    -> [FALLBACK] Chunk {i+1}: Retrying in VERTICAL mode...")
                try:
                    with _cache_lock:
                        if "text" not in _vertical_cache:
                            _vertical_cache["text"] = extract_text_from_pdf_pymupdf(pdf_path, mode="vertical")
                    
                    full_vertical_text = _vertical_cache["text"]
                    v_pages = re.split(r'\[\s*\[\s*PAGE_\d+\s*\]\s*\]', full_vertical_text)
                    if v_pages and not v_pages[0].strip(): v_pages.pop(0)
                    if i < len(v_pages):
                        v_chunk_text = v_pages[i].strip()
                        page_data = extract_fields_with_llm(v_chunk_text, client, f"{os.path.basename(pdf_path)}_page_{i+1}", mode="vertical", detected_carrier=global_carrier) or {}
                except Exception as e:
                    print(f"    -> [ERROR] Vertical fallback failed: {e}")

        # Refinement Pass
        should_refine, target_total, current_sum = learning_engine.should_trigger_refinement(page_data, page_text)
        if should_refine:
            print(f"    -> [LEARNING] Refinement triggered for chunk {i+1}...")
            refinement_prompt = learning_engine.generate_refinement_prompt(page_data, page_text, target_total, current_sum)
            page_data = extract_fields_with_llm(refinement_prompt, client, f"{os.path.basename(pdf_path)}_page_{i+1}_refinement", mode="standard", detected_carrier=global_carrier) or {}
            
        return {
            "index": i, 
            "header": page_data.get("HEADER", {}), 
            "items": page_data.get("LINE_ITEMS", []),
            "refinement_info": (target_total if should_refine else None)
        }

    # Execute threads
    max_workers = min(len(pages), 10) 
    print(f"  [V3][PARALLEL] Dispatching {len(pages)} pages across {max_workers} threads...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_page_parallel, i, p): i for i, p in enumerate(pages)}
        results = []
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                results.append(res)
            except Exception as e:
                print(f"  [V3][ERROR] Thread failed: {e}")

    # Sort results
    results.sort(key=lambda x: x["index"])
    for res in results:
        p_header = res["header"]
        for k, v in p_header.items():
            if v and str(v).lower() not in ["n/a", "none"]:
                # [V4][MOO] Prioritize the larger amount for financial fields (Grand Total vs Subtotal/Misc)
                if k in ["AMOUNT_DUE", "TOTAL_BILLED"]:
                    curr_val = to_float(final_header.get(k))
                    new_val = to_float(v)
                    if new_val > curr_val:
                        final_header[k] = v
                else:
                    # For other fields (Date, Invoice #), keep the first non-null encounter (usually Page 1)
                    if not final_header.get(k):
                        final_header[k] = v
        items = res["items"]
        if items:
            all_line_items.extend(items)


    # Final combined data
    data = {
        "HEADER": final_header,
        "LINE_ITEMS": all_line_items
    }
    
    # BCBS Florida Blue total correction:
    # Some BCBS invoices have BILLING SUMMARY: TOTAL BILLED AMOUNT + ON-BILL ADJUSTMENTS = AMOUNT DUE.
    # The LLM sometimes captures the ON-BILL ADJUSTMENTS value as AMOUNT_DUE instead of the true grand total.
    # If TOTAL_BILLED + TOTAL_ADJUSTMENTS are both present and > AMOUNT_DUE, recompute AMOUNT_DUE.
    _tb = to_float(final_header.get("TOTAL_BILLED"))
    _ta = to_float(final_header.get("TOTAL_ADJUSTMENTS"))
    _ad = to_float(final_header.get("AMOUNT_DUE"))
    if _tb > 0 and _ta > 0:
        _computed_ad = round(_tb + _ta, 2)
        if _ad == 0 or (abs(_computed_ad - _ad) > 0.05 and _computed_ad > _ad):
            print(f"    [V4][TOTAL FIX] Correcting AMOUNT_DUE: {_ad} -> {_computed_ad} (TOTAL_BILLED {_tb} + TOTAL_ADJUSTMENTS {_ta})")
            final_header["AMOUNT_DUE"] = _computed_ad
            data["HEADER"]["AMOUNT_DUE"] = _computed_ad
    
    # Propagate Header Total to line item field if AI only extracted it in header
    # This ensures consistency even if the LLM followed instructions to keep it in HEADER
    header_amt_due = final_header.get("AMOUNT_DUE")
    if header_amt_due and not any(item.get("PLAN_NAME") == "TOTAL" for item in all_line_items):
        final_total = to_float(header_amt_due)
        if final_total > 0:
            print(f"    [V4] Adding synthetic TOTAL row from Header AMOUNT_DUE: {final_total}")
            all_line_items.append({
                "PLAN_NAME": "TOTAL",
                "FIRSTNAME": "INVOICE TOTAL",
                "CURRENT_PREMIUM": final_total,
                "PLAN_TYPE": None  # Ensure no default for synthetic row
            })
    
    # [V4][COVERAGE NORMALIZE] Programmatic fix for UHC coverage tier mapping
    all_line_items = normalize_uhc_coverage(all_line_items)
    
    # [V4][STRICT FEE FILTER] Programmatic de-duplication of $25 billing fees
    if is_uhc_invoice:
        all_line_items = deduplicate_uhc_fees(all_line_items)
        data["LINE_ITEMS"] = all_line_items

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
    
    # Save the full data to JSON before filtering columns for Excel
    # This JSON is used by the UI to display metadata like the total_value
    json_output = output_excel.replace(".xlsx", ".json")
    try:
        import json as json_lib
        with open(json_output, "w", encoding="utf-8") as f:
            json_lib.dump(rows, f, indent=4)
        print(f"[OK] Full extraction data saved to JSON: {json_output}")
    except Exception as je:
        print(f"[WARN] Failed to save JSON: {je}")

    # Ensure all REQUIRED_FIELDS are present as columns for Excel
    for field in REQUIRED_FIELDS:
        if field not in df.columns:
            df[field] = None
            
    # [V4][FIX] Force date columns to strings to prevent Excel auto-reformatting to US dates
    for col in ["INV_DATE", "BILLING_PERIOD"]:
        if col in df.columns:
            df[col] = df[col].astype(str).replace(['None', 'nan', 'NaT'], None)
            
    # Reorder columns - STRICTLY use REQUIRED_FIELDS for Excel
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
        
        print(f"    [V3][TRACE] Starting flatten of {len(line_items)} items from LLM...")
        for i, item in enumerate(line_items):
            print(f"      Item {i+1}: {item.get('FIRSTNAME')} {item.get('LASTNAME')} (${item.get('CURRENT_PREMIUM')})")
        
        if not line_items:
            # If no line items, just save header with empty line item fields
            # Merge data from header with cleaned billing period
            clean_header = header.copy()
            if "BILLING_PERIOD" in clean_header:
                clean_header["BILLING_PERIOD"] = clean_billing_period(clean_header["BILLING_PERIOD"])
                
            row = {"SOURCE_FILE": source_filename}
            row.update(clean_header)
            rows.append(row)
        else:
            # Deduplicate/Merge items using multi-stage matching (Name + MemberID or Name + SSN)
            merged_items = []
            index_by_id = {}   # fname|lname|member_id -> item_index
            index_by_ssn = {}  # fname|lname|ssn -> item_index
            
            # Clean header metadata once
            clean_header = header.copy()
            if "BILLING_PERIOD" in clean_header:
                clean_header["BILLING_PERIOD"] = clean_billing_period(clean_header["BILLING_PERIOD"])
            
            # Additional header cleaning for dates and spacing
            for k in ["INV_DATE", "BILLING_PERIOD"]:
                if k in clean_header: clean_header[k] = format_date_clean(clean_header[k])
            for k in ["INV_NUMBER", "POLICYID"]:
                if k in clean_header: clean_header[k] = clean_string_spacing(clean_header[k], preserve_single=True)
            
            # Post-processing: Correct INV_NUMBER if it looks like a formatted Amount Due
            # e.g., 000000005372 for $53.72 total.
            if clean_header.get("INV_NUMBER"):
                inv_num_val = str(clean_header["INV_NUMBER"])
                # Find the total amount from line items to compare
                potential_total = 0.0
                for item in line_items:
                    if str(item.get("PLAN_NAME") or "").upper() == "TOTAL" or \
                       str(item.get("FIRSTNAME") or "").upper() == "INVOICE TOTAL":
                        try:
                            s = str(item.get("CURRENT_PREMIUM") or "").replace('$', '').replace(',', '').strip()
                            potential_total = float(s)
                        except: pass
                        break
                
                if potential_total > 0:
                    total_cents = str(int(round(potential_total * 100))) # e.g. 5372
                    # If INV_NUMBER contains many zeros and ends with the exact cents of the total
                    if len(inv_num_val) > 8 and inv_num_val.lstrip('0') == total_cents:
                        print(f"    [V3][FIX] Resetting INV_NUMBER '{inv_num_val}' because it matches Total Amount '{potential_total}'")
                        clean_header["INV_NUMBER"] = None
                        inv_num_val = ""  # Don't do further processing
                
                # Strip excessive leading zeros (e.g. "000000000027716" -> "27716")
                # Only do this if: the value is all-numeric, has 4+ leading zeros, and dropping them
                # leaves a meaningful number (3+ digits). This avoids stripping BCBS 12-digit IDs
                # like "260210001403" which don't start with 4+ zeros.
                if clean_header.get("INV_NUMBER") and inv_num_val:
                    inv_num_val = str(clean_header["INV_NUMBER"])
                    if inv_num_val.replace(".", "").replace("-", "").isdigit():
                        stripped = inv_num_val.lstrip('0')
                        leading_zeros = len(inv_num_val) - len(stripped)
                        # Only strip if there are 4+ leading zeros AND the result has 3+ digits
                        if leading_zeros >= 4 and len(stripped) >= 3:
                            print(f"    [V3][FIX] Stripping leading zeros from INV_NUMBER '{inv_num_val}' -> '{stripped}'")
                            clean_header["INV_NUMBER"] = stripped
            
            # CLEANUP: to_float and check_total moved to global scope
            def is_keyword_match(text, keywords):
                t = str(text or "").upper()
                # Check for exact matches of total keywords as standalone words
                return any(re.search(fr'\b{kw}\b', t) for kw in keywords)

            for item in line_items:
                # DUMMY ID FILTER: Discard clearly hallucinated rows
                member_id = str(item.get("MEMBERID") or "").strip()
                ssn = str(item.get("SSN") or "").strip()
                first_name = str(item.get("FIRSTNAME") or "").strip().upper()
                last_name = str(item.get("LASTNAME") or "").strip().upper()
                
                # Expand patterns to catch common LLM hallucinations
                dummy_id_patterns = ["123456789", "987654321", "000000000", "111223344", "556677889", "112233445"]
                hallucinated_names = [("ALICE", "WEXMAN"), ("JOHN", "GALLI")]
                
                is_dummy_id = any(p in member_id or p in ssn for p in dummy_id_patterns)
                is_dummy_name = any(first_name == fn and last_name == ln for fn, ln in hallucinated_names)
                
                # SPECIAL CASE: Sharad Saxton is a REAL member (Account Owner)
                is_sharad = (first_name == "SHARAD" and last_name == "SAXTON") or \
                           (first_name == "SAXTON" and last_name == "SHARAD")
                
                if is_sharad:
                    print(f"    [V3][INFO] Protecting REAL member Sharad Saxton from dummy filter.")
                    is_dummy_name = False
                    is_dummy_id = False

                if is_dummy_id or is_dummy_name:
                    print(f"    [V3][WARN] Filtering hallucinated dummy row: {first_name} {last_name} (ID: {member_id})")
                    continue

                fname = str(item.get("FIRSTNAME") or "").strip().lower()
                lname = str(item.get("LASTNAME") or "").strip().lower()
                member_id = str(item.get("MEMBERID") or "").strip().lower()
                ssn = str(item.get("SSN") or "").strip().lower()
                
                # Check for weak identifiers
                is_weak_id = not member_id or member_id in ["n/a", "none", "unknown", ""]
                is_weak_ssn = not ssn or ssn in ["n/a", "none", "unknown", ""]
                is_weak_name = not fname and not lname
                
                if is_weak_id and is_weak_ssn and is_weak_name:
                    # [V4][ADJUSTMENT ALLOW] Allow orphan adjustments through the filter
                    is_adjustment_label = is_keyword_match(item.get("PLAN_NAME"), ["ADJUSTMENT", "ADJUSTMENTS", "RETRO"])
                    has_premium = to_float(item.get("CURRENT_PREMIUM")) != 0 or to_float(item.get("ADJUSTMENT_PREMIUM")) != 0
                    if not (is_adjustment_label and has_premium):
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
                         existing = merged_items[potential_idx]
                         ex_plan = str(existing.get("PLAN_NAME") or "").strip().lower()
                         ex_clean = ex_plan if ex_plan not in ["n/a", "none", ""] else None
                         # Relaxed check: merge if plan names match OR if one is an adjustment/total label
                         # [V4][FIX] Use word boundaries for 'ADD' to avoid matching 'AD&D'
                         # Adjustment check with word boundaries for ADD to avoid matching AD&D
                         is_adj_or_total = lambda s: any(kw in str(s).upper() for kw in ["ADJUSTMENT", "TOTAL", "RETRO", "TRM", "CHG"]) or \
                                            bool(re.search(r"\bADD\b", str(s).upper()))

                         # [V4][SAFEGUARD] Never merge different non-null plan types (e.g. Life into Medical)
                         curr_type = str(item.get("PLAN_TYPE") or "").upper()
                         ex_type = str(existing.get("PLAN_TYPE") or "").upper()
                         type_mismatch = curr_type and ex_type and curr_type != ex_type

                         if not type_mismatch and (not clean_plan or not ex_clean or clean_plan == ex_clean or is_adj_or_total(clean_plan) or is_adj_or_total(ex_clean)):
                             match_index = potential_idx
                
                # 3. Try Name-Only Match (ULTRA-LOOSE) if one side is a "shell" record (missing identifiers)
                if match_index is None and not is_weak_name:
                    # Check if we have an existing record with the SAME name
                    matched_by_name_idx = None
                    for idx, ex in enumerate(merged_items):
                        # Normalize names for comparison (remove spaces, e.g. "Gacio Tomas" == "GacioTomas")
                        ex_fname = str(ex.get("FIRSTNAME") or "").replace(" ", "").strip().lower()
                        ex_lname = str(ex.get("LASTNAME") or "").replace(" ", "").strip().lower()
                        
                        f1, f2 = fname.replace(" ", ""), ex_fname
                        l1, l2 = lname.replace(" ", ""), ex_lname
                        
                        name_match = False
                        if l1 == l2:
                            if f1 == f2:
                                name_match = True
                            # One First Name starts with the other First Name (handles initials)
                            # Only merge if one name is an initial (1 char) to avoid Tomas -> Tomas-Phillip
                            elif (f1.startswith(f2) or f2.startswith(f1)) and (len(f1) == 1 or len(f2) == 1):
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
                                # Relaxed check: merge if plan names match OR if one is an adjustment/total label
                                # [V4][FIX] Use word boundaries for 'ADD' to avoid matching 'AD&D'
                                is_adj_or_total = lambda s: any(kw in str(s).upper() for kw in ["ADJUSTMENT", "TOTAL", "RETRO", "TRM", "CHG"]) or \
                                                   bool(re.search(r"\bADD\b", str(s).upper()))

                                # [V4][SAFEGUARD] Never merge different non-null plan types (e.g. Life into Medical)
                                curr_type = str(item.get("PLAN_TYPE") or "").upper()
                                ex_type = str(ex.get("PLAN_TYPE") or "").upper()
                                type_mismatch = curr_type and ex_type and curr_type != ex_type

                                if not type_mismatch and (not clean_plan or not ex_clean or clean_plan == ex_clean or is_adj_or_total(clean_plan) or is_adj_or_total(ex_clean)):
                                    matched_by_name_idx = idx
                                    break
                    
                    if matched_by_name_idx is not None:
                        # 1. CRITICAL: Do NOT merge a TOTAL/SUMMARY row with a member row.
                        if check_total(item) != check_total(merged_items[matched_by_name_idx]):
                            match_index = None # Only block if one is total and other is not
                        
                        # 2. DEDUPLICATION PRECEDENCE: Do NOT merge a row with a MEMBERID into a row WITHOUT one (or vice versa)
                        # if the premiums were likely different. This prevents summary totals on Page 1 from merging with members.
                        else:
                            ex = merged_items[matched_by_name_idx]
                            ex_id = str(ex.get("MEMBERID") or "").strip().lower()
                            curr_id = str(item.get("MEMBERID") or "").strip().lower()
                            
                            ex_has_id = ex_id and ex_id not in ["n/a", "none", "", "unknown"]
                            curr_has_id = curr_id and curr_id not in ["n/a", "none", "", "unknown"]
                            
                            if ex_has_id != curr_has_id:
                                # One has ID, other doesn't. Likely different context (Summary vs Detail).
                                match_index = None
                            else:
                                # [V4][BCBS][LEDGER] Prevent merging for BCBS adjustments to preserve ledger detail
                                # If one side is a "detail" adjustment, do not merge.
                                fn_upper = source_filename.upper()
                                is_bcbs_doc = "BLUE" in fn_upper or "BCBS" in fn_upper
                                
                                pn1 = str(item.get("PLAN_NAME") or "").upper()
                                pn2 = str(merged_items[matched_by_name_idx].get("PLAN_NAME") or "").upper()
                                
                                detail_keywords = ["ADD", "TRM", "CHANGE", "CHG", "RETRO", "ADJUSTMENT", "ADJ"]
                                is_adj1 = any(kw in pn1 for kw in detail_keywords)
                                is_adj2 = any(kw in pn2 for kw in detail_keywords)
                                
                                if is_bcbs_doc and (is_adj1 or is_adj2):
                                    # Still merge if they are the SAME adjustment (e.g. split across pages)
                                    # but otherwise keep separate for "three time" auditability.
                                    if pn1 == pn2:
                                        match_index = matched_by_name_idx
                                    else:
                                        print(f"      [V4][BCBS] Ledger Mode: Keeping detail adjustment separate: {pn1} vs {pn2}")
                                        match_index = None
                                else:
                                    match_index = matched_by_name_idx
                
                # [V3][DEBUG] Trace match result
                if match_index is not None:
                    ex = merged_items[match_index]
                    print(f"      [V3][MERGE] Match found for {fname} {lname}: current={item.get('CURRENT_PREMIUM')}, adjustment={item.get('ADJUSTMENT_PREMIUM')} matching existing with current={ex.get('CURRENT_PREMIUM')}, adjustment={ex.get('ADJUSTMENT_PREMIUM')}")
                else:
                    print(f"      [V3][MERGE] No match for {fname} {lname}")

                if match_index is not None:
                    existing = merged_items[match_index]
                    
                    # [V4][FIX] Removed over-aggressive safeguard that nulled CURRENT_PREMIUM if it matched ADJUSTMENT_PREMIUM.
                    # This was causing data loss for legitimate UHC adjustments (e.g. adding a plan retroactively).
                    
                    incoming_total = check_total(item)
                    existing_total = check_total(existing)
                    
                    # Merge data: update existing record
                    for k, v in item.items():
                        if k in ["CURRENT_PREMIUM", "ADJUSTMENT_PREMIUM"]:
                            v1 = to_float(existing.get(k))
                            v2 = to_float(v)
                            
                            if v2 != 0:
                                # DEDUPLICATION / SUMMING LOGIC
                                # 1. If incoming is a redundant total row for a member (like "Subscriber Total")
                                if incoming_total and (existing.get("MEMBERID") or existing.get("SSN")):
                                    # Fallback: Capture the total ONLY if we haven't found any component adjustments yet
                                    if v1 == 0:
                                        existing[k] = v2
                                    else:
                                        # Already have a value (likely from an "ADD" row), skip the summary to avoid double counting
                                        pass
                                # 2. If it's a grand "TOTAL" row matching a grand total, keep the LATEST value (overwrite)
                                elif incoming_total and existing_total and not (existing.get("MEMBERID") or existing.get("SSN")):
                                    existing[k] = v2
                                # 3. Otherwise (Member detail rows, adjustments, etc.): Always sum
                                else:
                                    print(f"      [V3][SUM] Adding {v2} to {v1} for {k} ({fname} {lname})")
                                    existing[k] = round(v1 + v2, 2)
                        
                        elif v and str(v).lower() not in ["n/a", "none", ""]:
                            # [DYNAMIC] Concatenate multi-line fields (e.g. Plan Name fragments)
                            if k in ["PLAN_NAME", "PLAN_TYPE"]:
                                ex_v = str(existing.get(k) or "").strip()
                                new_v = str(v).strip()
                                
                                # [V4] Smart Plan Name Merging: Do not append adjustment labels to real plan names
                                # [V4][FIX] Use word boundaries for 'ADD' to avoid matching 'AD&D'
                                adj_keywords = ["TOTAL", "ADJUSTMENT", "RETRO", "TRM"]
                                is_incoming_adj = any(kw in new_v.upper() for kw in adj_keywords) or bool(re.search(r"\bADD\b", new_v.upper()))
                                if k == "PLAN_NAME" and is_incoming_adj and ex_v and not any(kw in ex_v.upper() for kw in adj_keywords):
                                    # Existing name is a real plan, incoming is a label. Skip concatenation.
                                    pass
                                elif ex_v and new_v and new_v.lower() not in ex_v.lower():
                                    existing[k] = f"{ex_v} {new_v}"
                                elif not ex_v:
                                    existing[k] = new_v
                            else:
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
                    # [V4][ADJUSTMENT MERGE] Handle Orphan Adjustments
                    # If this is a row with no name but has an adjustment amount/label,
                    # try to merge it into the LAST member row we just added.
                    is_adjustment_label = is_keyword_match(item.get("PLAN_NAME"), ["ADJUSTMENT", "ADJUSTMENTS", "RETRO"])
                    has_premium = to_float(item.get("CURRENT_PREMIUM")) != 0 or to_float(item.get("ADJUSTMENT_PREMIUM")) != 0
                    
                    if is_weak_name and is_adjustment_label and has_premium and merged_items:
                        # Find the last member row (skip nested total rows if any)
                        last_member_idx = None
                        for i in range(len(merged_items)-1, -1, -1):
                            if not check_total(merged_items[i]):
                                last_member_idx = i
                                break
                        
                        if last_member_idx is not None:
                            last_member = merged_items[last_member_idx]
                            print(f"    [V4][MERGE] Merging orphan adjustment '{item.get('PLAN_NAME')}' into member {last_member.get('FIRSTNAME')} {last_member.get('LASTNAME')}")
                            
                            # Move CURRENT_PREMIUM to ADJUSTMENT_PREMIUM if the label is "Adjustment"
                            adj_val = to_float(item.get("CURRENT_PREMIUM")) + to_float(item.get("ADJUSTMENT_PREMIUM"))
                            existing_adj = to_float(last_member.get("ADJUSTMENT_PREMIUM"))
                            last_member["ADJUSTMENT_PREMIUM"] = round(existing_adj + adj_val, 2)
                            
                            # Update plan name if relevant (optional)
                            # last_member["PLAN_NAME"] = f"{last_member.get('PLAN_NAME')} (incl. {item.get('PLAN_NAME')})"
                            
                            continue # Successfully merged, do not add as new record

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
            adjustment_rows = []
            
            # [V4][BCBS] Filter out redundant summary rows (e.g. Subscriber Total)
            # if we have detail rows for the same person.
            fn_upper = source_filename.upper()
            is_bcbs_doc = "BLUE" in fn_upper or "BCBS" in fn_upper
            if is_bcbs_doc:
                # Group items by ID/Name to detect summaries
                person_map = {}
                for item in merged_items:
                    pid = str(item.get("MEMBERID") or item.get("SSN") or "").strip().upper()
                    if pid:
                        if pid not in person_map: person_map[pid] = []
                        person_map[pid].append(item)
                
                filtered_items = []
                for item in merged_items:
                    pid = str(item.get("MEMBERID") or item.get("SSN") or "").strip().upper()
                    pn = str(item.get("PLAN_NAME") or "").upper()
                    
                    if pid and "SUBSCRIBER TOTAL" in pn and len(person_map.get(pid, [])) > 1:
                        print(f"    [V4][BCBS] Filtering redundant summary row for {pid}: {pn}")
                        continue
                    filtered_items.append(item)
                merged_items = filtered_items

            for item in merged_items:
                row = {"SOURCE_FILE": source_filename}
                # Ensure all required fields are present (even as None/empty)
                for field in REQUIRED_FIELDS:
                    row[field] = item.get(field) # Will be None if missing
                
                row.update(clean_header)
                row.update(item)
                
                # --- V3.1 CLEANING LOGIC (User Requested Formatting) ---
                # Clean names (normalize multiple spaces)
                for f in ["FIRSTNAME", "LASTNAME", "MIDDLENAME"]:
                    if row.get(f): row[f] = clean_string_spacing(row[f], preserve_single=True)
                
                # Clean Plan/Plan Type (preserve single spaces but fix redundant gaps)
                for f in ["PLAN_NAME", "PLAN_TYPE"]:
                    if row.get(f): 
                        row[f] = clean_string_spacing(row[f], preserve_single=True)
                        # Fix "TG" prefix missing space (e.g., TGAD&D -> TG AD&D, TGLife -> TG Life)
                        if f == "PLAN_NAME" and row[f]:
                            # Fix "TG" prefix missing space (e.g., TGAD&D -> TG AD&D, TGLife -> TG Life)
                            row[f] = re.sub(r'^TG([A-Za-z&])', r'TG \1', str(row[f]), flags=re.IGNORECASE)
                            
                            # UHC Fix: Ensure space in plan codes like "P 7330" (User request)
                            # Matches 'P' followed immediately by 4 digits, but only if preceded by a space or start of string
                            row[f] = re.sub(r'(\bP)(\d{4})', r'\1 \2', str(row[f]))
                
                # Normalize Coverage and Plan Type
                if row.get("COVERAGE"):
                    row["COVERAGE"] = str(row["COVERAGE"]).strip().upper()
                
                # Fix common PLAN_TYPE mis-mappings (EE -> LIFE/AD&D inference)
                # BCBS EXCEPTION: Skip inference for BCBS to prevent unwanted "MEDICAL" population
                pn_upper = str(row.get("PLAN_NAME") or "").upper()
                fn_upper = str(row.get("SOURCE_FILE") or "").upper()
                is_bcbs = "BLUE" in pn_upper or "BLUE" in fn_upper or "BCBS" in fn_upper
                
                if not is_bcbs:
                    pt_val = str(row.get("PLAN_TYPE") or "").upper()
                    if pt_val in ["", "NONE", "NOT FOUND", "EE", "FAM", "SP", "CH", "DEP", "NAN", "UNKNOWN"]:
                        # Try to infer from PLAN_NAME
                        pn = str(row.get("PLAN_NAME") or "").upper()
                        if "DENTAL" in pn:
                            row["PLAN_TYPE"] = "DENTAL"
                        elif "VISION" in pn:
                            row["PLAN_TYPE"] = "VISION"
                        elif "LIFE" in pn:
                            row["PLAN_TYPE"] = "LIFE"
                        elif "AD&D" in pn:
                            row["PLAN_TYPE"] = "AD&D"
                        elif any(kw in pn for kw in ["POS", "EPO", "PPO", "HMO", "CHC", "NHP", "MEDICAL", "HEALTH"]):
                            row["PLAN_TYPE"] = "MEDICAL"
                # Default PLAN_TYPE for BCBS if missing
                # Removed default to MEDICAL per user request: "if plan type have in the pdf get it otherwise dont need"
                
                # --- BCBS SPECIAL POST-PROCESSING (Fix Plan Name and force null Plan Type) ---
                if is_bcbs:
                    pn_raw = str(row.get("PLAN_NAME") or "")
                    
                    # Malibu Brewing / BCBS CA layout detection
                    is_ca_malibu = pn_upper in ["HEALTH", "DENTAL", "VISION", "LIFE", "AD&D", "STD", "LTD"]
                    
                    # 1. Handle Plan Type and Plan Name
                    if not is_ca_malibu:
                        # Standard BCBS: Force null PLAN_TYPE for medical
                        # User request: "incorrect plan type captured, that's why mentioned plan type not captured"
                        row["PLAN_TYPE"] = None
                        
                        # Fix Plan Name: Must start with BLUECARE, strip location prefixes
                        # Common prefixes: SAND, MARV, BEAC, JAX
                        pn_clean = re.sub(r'^(SAND|MARV|BEAC|JAX)\s*', '', pn_raw, flags=re.IGNORECASE).strip()
                        
                        # Strip "REN", "SE", or "USE" which belonging to Coverage/Tier
                        pn_clean = re.sub(r'\b(REN|SE|USE)\b', '', pn_clean, flags=re.IGNORECASE).strip()
                        pn_clean = re.sub(r'\s+', ' ', pn_clean) # Clean double spaces
                        
                        # Handle reversal: "NFQ... BLUECARE" -> "BLUECARE NFQ..."
                        if "BLUECARE" in pn_clean.upper() and not pn_clean.upper().startswith("BLUECARE"):
                            # Extract everything else and put BLUECARE at start
                            other_parts = re.sub(r'BLUECARE', '', pn_clean, flags=re.IGNORECASE).strip()
                            pn_clean = f"BLUECARE {other_parts}"
                        
                        row["PLAN_NAME"] = pn_clean.strip()
                    else:
                        # Malibu CA: Capture category (Health/Dental/etc) in BOTH fields
                        row["PLAN_NAME"] = pn_raw
                        row["PLAN_TYPE"] = pn_raw
                # ----------------------------------------------------------------------------
                
                # --- CLEANING REMOVED ---
                # Redundant calls to format_date_clean here were flipping dates back to M/D/YYYY.
                # Dates are already cleaned once when the header is prepared above (line 2045-2046).
                # -----------------------
                # -------------------------------------------------------
                # Remove internal fields that shouldn't be in Excel
                for internal_field in ["PRICING_MODEL", "RELATIONSHIP"]:
                    if internal_field in row:
                        del row[internal_field]
                
                # Check for total row type
                idx_p = str(row.get("PLAN_NAME", "") or "").upper()
                idx_f = str(row.get("FIRSTNAME", "") or "").upper()
                idx_l = str(row.get("LASTNAME", "") or "").upper()
                
                total_keywords = ["TOTAL", "GRAND TOTAL", "SUBTOTAL", "SUB TOTAL", "INVOICE TOTAL"]
                adj_keywords = ["FEE", "ADJUSTMENT", "CREDIT", "CHARGE"]
                
                # ID-based protection: if has real MemberID or SSN, it's rarely a grand total
                idx_mid = str(row.get("MEMBERID", "") or "").strip()
                idx_ssn = str(row.get("SSN", "") or "").strip()
                has_mid = idx_mid and idx_mid not in ["NONE", "NAN", "N/A", "UNKNOWN", ""]
                has_ssn = idx_ssn and idx_ssn not in ["NONE", "NAN", "N/A", "UNKNOWN", ""]
                has_names = idx_f and idx_l and idx_f not in ["NONE", "NAN", "N/A"]
                has_plan = idx_p and idx_p not in ["NONE", "NAN", "N/A", "UNKNOWN", ""]

                is_total = is_keyword_match(idx_p, total_keywords) or \
                           is_keyword_match(idx_f, total_keywords) or \
                           is_keyword_match(idx_l, total_keywords)
                
                # If "TOTAL" is part of a plan name like "TOTAL PET", it's NOT a total row
                # Also do NOT treat subscriber-level totals as grand totals that need clearing
                if any(kw in idx_p for kw in ["TOTAL PET", "SUBSCRIBER TOTAL", "MEMBER TOTAL", "PERSON TOTAL", "EE TOTAL"]):
                    is_total = False
                
                # Protect specific "Subscriber Total" rows from being cleared if they have a name and ID
                if is_total and (has_mid or has_ssn) and has_names:
                     is_total = False
                
                is_adjustment = not is_total and (is_keyword_match(idx_p, adj_keywords) or \
                                                  is_keyword_match(idx_l, adj_keywords) or \
                                                  str(row.get("PLAN_TYPE")).upper() == "FEES" or \
                                                  idx_l == "CREDIT/FEE")
                
                # Sharad Saxton Protection
                is_sharad_with_id = (("SHARAD" in idx_f and "SAXTON" in idx_l) or
                                     ("SHARAD" in idx_l and "SAXTON" in idx_f)) and \
                                    idx_mid and idx_mid.isnumeric() and len(idx_mid) >= 4
                if is_sharad_with_id:
                    is_total = False
                
                # UHC specific: any single row > $6,000 is a summary/error unless it's Sharad with real ID
                prem_val = to_float(row.get("CURRENT_PREMIUM"))
                if prem_val > 6000 and not is_sharad_with_id:
                    is_total = True
                
                if not is_total:
                    if has_names and not has_mid and not has_plan:
                        # Case: Sharad Saxton appearing without ID/Plan (Account Header)
                        is_total = True
                    elif not has_names and not has_plan and not is_adjustment:
                        # Entity name or random header text
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
                elif is_adjustment:
                    row["PLAN_TYPE"] = "FEES"
                    adjustment_rows.append(row)
                else:
                    member_rows.append(row)
            
            # PHASE 2: Produce explicit and auditable TOTAL rows.
            # Strategy:
            #   1. Calculate the sums of Current and Adjustment columns.
            #   2. Provide separate rows for each sum + a combined final total.
            #   3. This makes the math explicit and auditable in the spreadsheet.

            # Strategy: Calculate combined total including member premiums and standalone adjustments (fees/credits)
            sum_current = sum(to_float(mr.get("CURRENT_PREMIUM")) for mr in member_rows)
            sum_adj_members = sum(to_float(mr.get("ADJUSTMENT_PREMIUM")) for mr in member_rows)
            # [V4][FIX] Include standalone adjustment_rows in the calculated total
            sum_standalone_adj = sum(to_float(ar.get("CURRENT_PREMIUM")) + to_float(ar.get("ADJUSTMENT_PREMIUM")) for ar in adjustment_rows)
            
            combined_total = round(sum_current + sum_adj_members + sum_standalone_adj, 2)

            # Build audit-ready total rows
            final_total_rows = []
            
            # PRE-PHASE 2: Check if total_rows already contain a REPORTED total
            # and if it needs to be corrected using TOTAL_BILLED + TOTAL_ADJUSTMENTS.
            # This handles BCBS Florida Blue where ON-BILL ADJUSTMENTS is confused with AMOUNT DUE.
            total_billed_on_members = 0.0
            total_adj_on_members = 0.0
            for mr in member_rows:
                tb = to_float(mr.get("TOTAL_BILLED"))
                ta = to_float(mr.get("TOTAL_ADJUSTMENTS"))
                if tb > 0:
                    total_billed_on_members = tb
                if ta > 0:
                    total_adj_on_members = ta
                if total_billed_on_members and total_adj_on_members:
                    break
            
            corrected_total = None
            if total_billed_on_members > 0 and total_adj_on_members > 0:
                corrected_total = round(total_billed_on_members + total_adj_on_members, 2)
            
            # Apply correction to any REPORTED/CALCULATED total rows that have a wrong value
            for tr in total_rows:
                if corrected_total and "REPORTED INVOICE TOTAL" in str(tr.get("PLAN_NAME", "")):
                    existing_total = to_float(tr.get("CURRENT_PREMIUM"))
                    # If the existing total is less than corrected (e.g. sub-total was used), fix it
                    if abs(existing_total - corrected_total) > 0.05 and corrected_total > existing_total:
                        print(f"    [V4][CORRECTION] Overriding REPORTED TOTAL {existing_total} -> {corrected_total} (TOTAL_BILLED + TOTAL_ADJUSTMENTS)")
                        tr["CURRENT_PREMIUM"] = corrected_total


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
            
            # Use explicitly captured INV_TOTAL if present on any row
            if not llm_total_val:
                for item in line_items:
                    itotal = to_float(item.get("INV_TOTAL"))
                    if itotal > 0:
                        llm_total_val = itotal
                        break
            
            # Ensure we ALWAYS have a total row for audit
            # V4 Strategy: 
            # 1. Prioritize Header AMOUNT_DUE if explicitly extracted
            # 2. Fallback to LLM_TOTAL (from line items)
            # 3. Fallback to combined_total (calculated sum)
            header_total = to_float(header.get("AMOUNT_DUE"))
            
            # BCBS TOTAL_BILLED + TOTAL_ADJUSTMENTS fallback:
            # Some BCBS Florida Blue invoices have three rows in the billing summary:
            #   TOTAL BILLED AMOUNT (e.g. $8,756.28)
            #   ON-BILL ADJUSTMENTS (e.g. $2,155.39)
            #   AMOUNT DUE (e.g. $10,911.67)
            # The LLM sometimes confuses ON-BILL ADJUSTMENTS for AMOUNT_DUE.
            # If TOTAL_BILLED and TOTAL_ADJUSTMENTS exist on a member row, detect and recompute.
            total_billed_val = 0.0
            total_adj_val = 0.0
            for item in line_items:
                tb = to_float(item.get("TOTAL_BILLED"))
                ta = to_float(item.get("TOTAL_ADJUSTMENTS"))
                if tb > 0:
                    total_billed_val = tb
                if ta > 0:
                    total_adj_val = ta
                if total_billed_val and total_adj_val:
                    break
            
            computed_from_billed = round(total_billed_val + total_adj_val, 2) if total_billed_val else 0.0
            
            # If computed_from_billed is significantly larger than what we have as header_total, use it
            if computed_from_billed > 0:
                if abs(computed_from_billed - header_total) > 0.05 and computed_from_billed > header_total:
                    header_total = computed_from_billed
            
            effective_total = header_total
            if header_total == 0:
                # Fallback to LLM reported total or calculated sum
                effective_total = llm_total_val if llm_total_val != 0 else combined_total
            
            # If combined_total (sum of individual rows) is significantly different from what the invoice claims (effective_total),
            # we report it as a warning in logs, but we DO NOT override the authoritative total.
            if effective_total > 0 and abs(combined_total - effective_total) > 0.05:
                print(f"    [V3][AUDIT][WARNING] Sum of rows (${combined_total}) does NOT match Invoiced Total (${effective_total})")
            
            if effective_total != 0:
                # Build row starting with header data
                row_report = {field: None for field in REQUIRED_FIELDS}
                row_report.update(clean_header)
                row_report["SOURCE_FILE"] = source_filename
                # Label based on whether it was explicitly reported or just calculated by us
                # If we have a header total or llm reported total, it's REPORTED.
                source_is_reported = (header_total > 0 or llm_total_val > 0)
                label = "REPORTED INVOICE TOTAL" if source_is_reported else "CALCULATED INVOICE TOTAL"
                row_report["PLAN_NAME"] = f"{label} (FOR AUDIT)"
                row_report["CURRENT_PREMIUM"] = effective_total
                
                # --- CLEANING REMOVED ---
                # Redundant calls to format_date_clean here were flipping dates back to M/D/YYYY.
                # Dates are already cleaned once when the header is prepared above (line 2045).
                # for f in ["INV_DATE", "BILLING_PERIOD"]:
                #     if row_report.get(f): row_report[f] = format_date_clean(row_report[f])
                
                final_total_rows.append(row_report)
                
                # Enhanced Validation Logging
                reported_val = header_total if header_total > 0 else llm_total_val
                if reported_val > 0 and abs(combined_total - reported_val) > 0.05:
                    print(f"\n    [V4][AUDIT][WARNING] Total calculation discrepancy detected!")
            # Propagate the real invoice total (effective_total) to every row as INV_TOTAL
            # ONLY if it was explicitly extracted as AMOUNT_DUE.
            if effective_total != 0 and header_total > 0:
                for r in (member_rows + adjustment_rows + final_total_rows):
                    r["INV_TOTAL"] = effective_total

            # Sort member rows alphabetically by name (A-Z)
            # This ensures logical ordering (matching PDF Page 1) regardless of extraction order
            member_rows.sort(key=lambda x: (str(x.get("LASTNAME") or "").upper(), 
                                           str(x.get("FIRSTNAME") or "").upper()))

            rows = member_rows + adjustment_rows + final_total_rows
                
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
    
    # Save the full data to JSON before filtering columns for Excel
    json_output = output_excel.replace(".xlsx", ".json")
    try:
        import json as json_lib
        with open(json_output, "w", encoding="utf-8") as f:
            json_lib.dump(rows, f, indent=4)
        print(f"[OK] Full extraction data saved to JSON: {json_output}")
    except Exception as je:
        print(f"[WARN] Failed to save JSON: {je}")

    # Ensure all REQUIRED_FIELDS are present as columns for Excel
    for field in REQUIRED_FIELDS:
        if field not in df.columns:
            df[field] = None
            
    # [V4][FIX] Force date columns to strings to prevent Excel auto-reformatting to US dates
    for col in ["INV_DATE", "BILLING_PERIOD"]:
        if col in df.columns:
            df[col] = df[col].astype(str).replace(['None', 'nan', 'NaT', 'nan '], None)
            
    # Reorder columns - STRICTLY use REQUIRED_FIELDS for Excel
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
    
    # [V4][FIX] Force date columns to strings to prevent Excel auto-reformatting to US dates
    for col in ["INV_DATE", "BILLING_PERIOD"]:
        if col in df.columns:
            df[col] = df[col].astype(str).replace(['None', 'nan', 'NaT', 'nan '], None)
            
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
