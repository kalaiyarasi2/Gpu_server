import fitz
import re
import io
from PIL import Image
import pytesseract
import os

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
            # print(f"DEBUG: Dropped small line: {line}")
            continue
            
        alnum_count = sum(c.isalnum() for c in line)
        non_space_len = len(line.replace(" ", ""))
        if non_space_len > 0 and alnum_count / non_space_len < 0.2:
            # print(f"DEBUG: Dropped non-alnum line: {line} (ratio {alnum_count/non_space_len:.2f})")
            continue
            
        line = re.sub(r'^[^\w\s]\s+', '', line)
        line = re.sub(r'\s+[^\w\s]$', '', line)
        line = re.sub(r'\s{3,}', ' | ', line)
        
        cleaned_lines.append(line)
        
    return '\n'.join(cleaned_lines)

pdf_path = r'C:\Users\INTERN\main_project\BCBS- 23 Restaurants- div001- Dec 25.pdf'
doc = fitz.open(pdf_path)
page = doc[1] # Page 2

zoom = 4.0
mat = fitz.Matrix(zoom, zoom)
pix = page.get_pixmap(matrix=mat)
img = Image.open(io.BytesIO(pix.tobytes("png")))

# Test different PSM modes
for psm in [3, 4, 6, 11, 12]:
    config = f'--psm {psm} -c preserve_interword_spaces=1'
    raw = pytesseract.image_to_string(img, config=config)
    cleaned = clean_ocr_noise(raw)
    
    print(f"\n--- PSM {psm} ---")
    print(f"Raw contains 'BROWN': {'BROWN' in raw}")
    print(f"Cleaned contains 'BROWN': {'BROWN' in cleaned}")
    if 'BROWN' in raw and 'BROWN' not in cleaned:
        print("ALERT: BROWN was cleaned out!")
    
    # Check for Row 7: NIKKI GREEN
    print(f"Raw contains 'NIKKI': {'NIKKI' in raw}")
    print(f"Raw contains 'GREEN': {'GREEN' in raw}")

doc.close()
