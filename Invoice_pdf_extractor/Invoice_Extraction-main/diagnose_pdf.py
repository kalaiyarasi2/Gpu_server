import fitz  # PyMuPDF
import pdfplumber
import os

pdf_path = "GIS 23 Restaurant Services Feb'25 Inv.pdf"
output_dir = "extraction_debug"
os.makedirs(output_dir, exist_ok=True)

print(f"Analyzing {pdf_path}...")

# 1. PyMuPDF analysis
doc = fitz.open(pdf_path)
print(f"Total pages: {len(doc)}")

with open(os.path.join(output_dir, "pymupdf_analysis.txt"), "w", encoding="utf-8") as f:
    for i, page in enumerate(doc):
        f.write(f"\n--- PAGE {i+1} ---\n")
        text = page.get_text("text")
        f.write(text)
        
        # Check for blocks/tables
        blocks = page.get_text("blocks")
        f.write(f"\n[Blocks count: {len(blocks)}]\n")

# 2. pdfplumber analysis
with pdfplumber.open(pdf_path) as pdf:
    with open(os.path.join(output_dir, "pdfplumber_analysis.txt"), "w", encoding="utf-8") as f:
        for i, page in enumerate(pdf.pages):
            f.write(f"\n--- PAGE {i+1} ---\n")
            text = page.extract_text()
            f.write(text if text else "[No text extracted]")
            
            tables = page.extract_tables()
            f.write(f"\n[Tables count: {len(tables)}]\n")

print("Analysis complete. Check 'extraction_debug' folder.")
