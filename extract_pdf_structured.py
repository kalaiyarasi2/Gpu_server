import fitz
import sys

pdf_path = r'c:\Users\INT002\updated_Extractor\pdf_extractor\Unified_PDF_Platform\uploads\UHC- Chil Brother- March 26 (1).pdf'
output_path = r'c:\Users\INT002\updated_Extractor\pdf_extractor\Unified_PDF_Platform\uploads\UHC_pymupdf_extracted_utf8.txt'

try:
    doc = fitz.open(pdf_path)
    full_text = []
    for i, page in enumerate(doc):
        full_text.append(f"[[PAGE_{i+1}]]")
        full_text.append(page.get_text())
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(full_text))
    print(f"Extraction successful: {output_path}")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
