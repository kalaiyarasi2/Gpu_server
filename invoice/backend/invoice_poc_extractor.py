"""
POC: General Invoice/Receipt Data Extraction to Excel/JSON
Uses high-accuracy extraction layers from V3 with a generalized invoice prompt.
"""

import os
import json
import re
import io
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pdfplumber
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --- CORE CONFIGURATION ---

REQUIRED_FIELDS = [
    "VENDOR_NAME",
    "INVOICE_NUMBER",
    "DATE",
    "TERMS",
    "DUE_DATE",
    "PO_NUMBER",
    "ITEM_DESCRIPTION",
    "QUANTITY",
    "RATE",
    "TAX_AMOUNT",
    "TOTAL_AMOUNT"
]

# --- TEXT EXTRACTION LAYERS (Inherited from V3) ---

def clean_ocr_noise(text: str) -> str:
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        if "[[PAGE_" in line:
            cleaned_lines.append(line)
            continue
        if len(line) < 2 and not line.isdigit():
            continue
        alnum_count = sum(c.isalnum() for c in line)
        non_space_len = len(line.replace(" ", ""))
        if non_space_len > 0 and alnum_count / non_space_len < 0.4:
            continue
        line = re.sub(r'^[^\w\s]\s+', '', line)
        line = re.sub(r'\s+[^\w\s]$', '', line)
        line = re.sub(r'\s{3,}', ' | ', line)
        cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)

def check_text_quality(text: str) -> float:
    if not text: return 0.0
    clean_meta = re.sub(r'\[\[PAGE_\d+\]\]', '', text)
    clean = re.sub(r'\s+', '', clean_meta)
    if not clean or len(clean) < 20: return 0.0
    alnum = sum(c.isalnum() for c in clean)
    return alnum / len(clean)

def extract_text_from_pdf_pymupdf(pdf_path: str, mode: str = "standard") -> str:
    try:
        text: str = ""
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            if mode == "vertical":
                blocks = page.get_text("blocks")
                blocks.sort(key=lambda b: (b[1], b[0]))
                page_text = "\n".join([b[4] for b in blocks])
            else:
                page_text = page.get_text()
            if page_text:
                text = text + f"\n[[PAGE_{page_num + 1}]]\n" + page_text + "\n"
        doc.close()
        return text
    except Exception as e:
        print(f"  [ERROR] PyMuPDF Error: {e}")
        return ""

def extract_text_from_pdf_ocr(pdf_path: str) -> str:
    try:
        text: str = ""
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            is_landscape = page.rect.width > page.rect.height
            zoom = 4.0 
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            tess_config = "-c preserve_interword_spaces=1"
            if is_landscape: tess_config = "--psm 4 " + tess_config
            else: tess_config = "--psm 3 " + tess_config
            page_text = pytesseract.image_to_string(img, config=tess_config)
            
            # Simple orientation fix
            normal_keywords = ["invoice", "bill", "total", "date", "vendor", "amount"]
            def score(t): return sum(1 for k in normal_keywords if k in t.lower())
            
            if score(page_text) < 1:
                img_mirrored = img.transpose(Image.FLIP_LEFT_RIGHT)
                text_mirrored = pytesseract.image_to_string(img_mirrored)
                img_rotated = img.rotate(180)
                text_rotated = pytesseract.image_to_string(img_rotated)
                
                s_orig = score(page_text)
                s_mirr = score(text_mirrored)
                s_rot = score(text_rotated)
                
                if s_mirr > s_orig and s_mirr >= s_rot: page_text = text_mirrored
                elif s_rot > s_orig: page_text = text_rotated
                
            text = text + f"\n[[PAGE_{page_num + 1}]]\n" + page_text + "\n"
        doc.close()
        return text
    except Exception as e:
        print(f"  [ERROR] OCR Error: {e}")
        return ""

def extract_text_from_pdf_improved(pdf_path: str) -> str:
    try:
        text: str = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text()
                text = text + f"\n[[PAGE_{page_num}]]\n"
                if page_text: text = text + page_text + "\n"
        return text
    except Exception: return ""

# --- LLM EXTRACTION LAYER ---

def extract_fields_with_llm(text: str, client: OpenAI, filename: str) -> Dict:
    prompt = f"""You are an expert financial auditor specializing in Invoice and Receipt extraction.
Extract data from the document text provided below.

### REQUESTED FIELDS:
- VENDOR_NAME: Entity issuing the invoice/receipt.
- INVOICE_NUMBER: The unique ID/number of the document.
- DATE: The document/billing date.
- TERMS: Payment terms (e.g., Net 30, Due on Receipt).
- DUE_DATE: The date payment is due.
- PO_NUMBER: Purchase Order number referenced.
- ITEM_DESCRIPTION: Description of individual line items.
- QUANTITY: Quantity of items.
- RATE: Unit price or rate per item.
- TAX_AMOUNT: Tax applied to the item or total.
- TOTAL_AMOUNT: The final amount billed/paid.

### EXTRACTION RULES:
1. **ITEMIZED ROWS**: Extract every individual line item found in the table. 
   - Each item should have its own entry in the `LINE_ITEMS` array.
2. **MULTIPLE ITEMS**: If one vendor has multiple line items, repeat the header info (Vendor, Date, etc.) for each row in your internal logic, but in the JSON, put them in the `LINE_ITEMS` array.
3. **HEADER DATA**: Extract common fields once and put them in the `HEADER` object.
4. **DECIMAL FORMAT**: Convert all amounts to pure numbers (no $ or commas). Handle negative amounts for credits.
5. **NULLS**: If a field is not found, return null. Do NOT hallucinate.

### DOCUMENT TEXT:
{text}

### OUTPUT FORMAT (JSON):
{{
  "HEADER": {{
    "VENDOR_NAME": null,
    "INVOICE_NUMBER": null,
    "DATE": null,
    "TERMS": null,
    "DUE_DATE": null,
    "PO_NUMBER": null,
    "TOTAL_AMOUNT": null
  }},
  "LINE_ITEMS": [
    {{
      "ITEM_DESCRIPTION": null,
      "QUANTITY": null,
      "RATE": null,
      "TAX_AMOUNT": null,
      "TOTAL_AMOUNT": null
    }}
  ]
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a precise data extractor."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0
        )
        data = json.loads(response.choices[0].message.content)
        return data
    except Exception as e:
        print(f"  [ERROR] LLM Error: {e}")
        return {"HEADER": {}, "LINE_ITEMS": []}

# --- SYSTEM WORKFLOW ---

def process_single_pdf(pdf_path: str, client: OpenAI) -> Dict:
    print(f"\n[POC] Processing: {os.path.basename(pdf_path)}")
    
    # Step 1: Extract Text
    text = extract_text_from_pdf_improved(pdf_path)
    if check_text_quality(text) < 0.2:
        print("  [INFO] Low text quality, using OCR...")
        text = extract_text_from_pdf_ocr(pdf_path)
    
    text = clean_ocr_noise(text)
    
    # SAVE LOCALLY AS TXT
    pdf_stem = Path(pdf_path).stem
    txt_path = Path(pdf_path).parent / f"{pdf_stem}_extracted_text.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  [INFO] Saved text to: {txt_path}")
    
    # Step 2: LLM Extract
    data = extract_fields_with_llm(text, client, os.path.basename(pdf_path))
    return data

def flatten_data(data: Dict, source_file: str) -> List[Dict]:
    rows = []
    header = data.get("HEADER", {})
    line_items = data.get("LINE_ITEMS", [])
    
    if not line_items:
        row = {"SOURCE_FILE": source_file}
        for field in REQUIRED_FIELDS:
            row[field] = header.get(field)
        rows.append(row)
    else:
        for item in line_items:
            row = {"SOURCE_FILE": source_file}
            # Combine Header and Item info
            row["VENDOR_NAME"] = header.get("VENDOR_NAME")
            row["INVOICE_NUMBER"] = header.get("INVOICE_NUMBER")
            row["DATE"] = header.get("DATE")
            row["TERMS"] = header.get("TERMS")
            row["DUE_DATE"] = header.get("DUE_DATE")
            row["PO_NUMBER"] = header.get("PO_NUMBER")
            
            row["ITEM_DESCRIPTION"] = item.get("ITEM_DESCRIPTION")
            row["QUANTITY"] = item.get("QUANTITY")
            row["RATE"] = item.get("RATE")
            row["TAX_AMOUNT"] = item.get("TAX_AMOUNT")
            # If line item has a total, use it, else fallback to header total for single-item cases
            row["TOTAL_AMOUNT"] = item.get("TOTAL_AMOUNT") or header.get("TOTAL_AMOUNT")
            rows.append(row)
            
    return rows

def main():
    if len(sys.argv) < 2:
        print("Usage: python invoice_poc_extractor.py <path_to_pdf>")
        return

    pdf_path = sys.argv[1]
    if not os.path.exists(pdf_path):
        print(f"Error: File {pdf_path} not found.")
        return

    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # Extraction
    data = process_single_pdf(pdf_path, client)
    
    # Save JSON
    json_path = Path(pdf_path).with_suffix(".json")
    with open(json_path, "w") as f:
        json.dump(data, f, indent=4)
    print(f"[OK] Saved JSON: {json_path}")
    
    # Flatten and Save Excel
    rows = flatten_data(data, os.path.basename(pdf_path))
    df = pd.DataFrame(rows)
    # Ensure all required fields exist and are in order
    cols = ["SOURCE_FILE"] + REQUIRED_FIELDS
    for col in cols:
        if col not in df.columns: df[col] = None
    df = df[cols]
    
    excel_path = Path(pdf_path).with_suffix(".xlsx")
    df.to_excel(excel_path, index=False)
    print(f"[OK] Saved Excel: {excel_path}")
    
    print("\n--- EXTRACTION PREVIEW ---")
    print(df.head().to_string(index=False))

if __name__ == "__main__":
    main()
