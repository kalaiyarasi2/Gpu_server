import sys
import os
import re
from pathlib import Path

# Add paths to sys.path
sys.path.append(r"C:\Users\INTERN\main_project\Main--main\invoice\backend")
sys.path.append(r"C:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform")

from handle_merge import find_invoice_page_ranges_from_text_pages

# Mocked header patterns from unified_router.py
MERGED_INVOICE_HEADER_PATTERNS = (
    "TAX INVOICE",
    "INVOICE#",
    "INVOICE #",
    "INVOICE NUMBER",
    "INVOICE NO",
    "INVOICE NO.",
    "BHARTI AIRTEL LTD",
    "SHYAM SPECTRA PVT. LTD",
    "SHYAM SPECTRA PRIVATE LIMITED",
    "ZOHO CORPORATION PRIVATE LIMITED",
)

def test_boundaries():
    text_file = r"C:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform\uploads\ilovepdf_merged (2)_extracted_text.txt"
    if not os.path.exists(text_file):
        print(f"Error: {text_file} not found")
        return

    with open(text_file, "r", encoding="utf-8") as f:
        full_text = f.read()

    # Split into pages based on [[PAGE_N]] marker
    page_splits = re.split(r'\[\[PAGE_\d+\]\]', full_text)
    # The first element is usually empty before the first marker
    pages = [p.strip() for p in page_splits if p.strip()]

    print(f"Total pages detected: {len(pages)}")
    
    ranges = find_invoice_page_ranges_from_text_pages(pages, MERGED_INVOICE_HEADER_PATTERNS)
    
    print("\nDetected Page Ranges:")
    for i, (start, end) in enumerate(ranges):
        print(f"Invoice {i+1}: Pages {start+1} to {end+1}")
        # Print matching signals for the start page
        start_page_text = pages[start].upper()
        matches = {p for p in MERGED_INVOICE_HEADER_PATTERNS if p.upper() in start_page_text}
        print(f"  Signals matched on start page: {matches}")

if __name__ == "__main__":
    test_boundaries()
