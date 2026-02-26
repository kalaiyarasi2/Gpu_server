import fitz
import io
from PIL import Image
import pytesseract
import os

pdf_path = r'C:\Users\INTERN\main_project\BCBS- 23 Restaurants- div001- Dec 25.pdf'
doc = fitz.open(pdf_path)

for idx in [1, 2]:
    p = doc[idx]
    zoom = 4.0
    mat = fitz.Matrix(zoom, zoom)
    pix = p.get_pixmap(matrix=mat)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert('L').point(lambda x: 0 if x < 180 else 255, '1')
    print(f"\n--- PAGE {idx+1} ---")
    raw = pytesseract.image_to_string(img, config='--psm 6')
    print(raw)

doc.close()
