import fitz
import re
import io
from PIL import Image
import pytesseract

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
        if non_space_len > 0 and alnum_count / non_space_len < 0.2:
            continue
        line = re.sub(r'^[^\w\s]\s+', '', line)
        line = re.sub(r'\s+[^\w\s]$', '', line)
        line = re.sub(r'\s{3,}', ' | ', line)
        cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)

pdf_path = r'C:\Users\INTERN\main_project\BCBS- 23 Restaurants- div001- Dec 25.pdf'
doc = fitz.open(pdf_path)

# Test Pages 2 and 3
for page_idx in [1, 2]:
    page = doc[page_idx]
    zoom = 4.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    img_orig = Image.open(io.BytesIO(pix.tobytes("png")))
    
    print(f"\n=== PAGE {page_idx + 1} ===")
    
    # Test different Thresholds
    for thresh in [150, 180, 200]:
        img = img_orig.convert('L')
        img = img.point(lambda x: 0 if x < thresh else 255, '1')
        
        raw = pytesseract.image_to_string(img, config='--psm 6 -c preserve_interword_spaces=1')
        cleaned = clean_ocr_noise(raw)
        
        print(f"\n--- Threshold {thresh} ---")
        if page_idx == 1:
            # Check for GREEN NIKKI SSN (7111)
            if "GREEN" in raw or "NIKKI" in raw:
                print("Found GREEN/NIKKI line:")
                line = [l for l in raw.split('\n') if "GREEN" in l or "NIKKI" in l][0]
                print(f"  RAW LINE: {line}")
            else:
                print("GREEN/NIKKI not found in raw.")
        
        if page_idx == 2:
            # Check for ROMANO KELLY Coverage (EMPLOYEE/CHILDREN)
            if "ROMANO" in raw:
                print("Found ROMANO line:")
                line = [l for l in raw.split('\n') if "ROMANO" in l][0]
                print(f"  RAW LINE: {line}")
            else:
                print("ROMANO not found in raw.")

doc.close()
