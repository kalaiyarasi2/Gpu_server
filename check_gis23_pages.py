import pdfplumber

pdf_path = "GIS 23 Restaurant Services Feb'25 Inv.pdf"

try:
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages[:5]):
            print(f"--- Page {i+1} ---")
            text = page.extract_text()
            print(text[:500] if text else "NO TEXT FOUND")
            if "Payroll File Number" in text:
                print(f"FOUND: 'Payroll File Number' on page {i+1}")
            else:
                print(f"NOT FOUND: 'Payroll File Number' on page {i+1}")
except Exception as e:
    print(f"Error: {e}")
