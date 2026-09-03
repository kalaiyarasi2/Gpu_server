"""
PaddleOCR Enhancement Module
High-speed GPU-accelerated OCR with table structure preservation
"""
import os
from pathlib import Path
from typing import Tuple, List, Dict, Optional

def extract_with_paddleocr(pdf_path: str, use_gpu: bool = True, enable_table: bool = True) -> Tuple[str, List[Dict]]:
    """
    Extract text from PDF using PaddleOCR with table structure preservation.
    
    Args:
        pdf_path: Path to input PDF
        use_gpu: Use GPU acceleration (default: True)
        enable_table: Enable table structure recognition (default: True)
        
    Returns:
        Tuple of (extracted_text, metadata_list)
        
    Requirements:
        pip install paddleocr paddlepaddle-gpu pdf2image
        OR
        pip install paddleocr paddlepaddle pdf2image (CPU version)
    """
    try:
        from paddleocr import PPStructure, PaddleOCR
        from pdf2image import convert_from_path
        import numpy as np
    except ImportError as e:
        print(f"   ⚠️ PaddleOCR not installed: {e}")
        print(f"   Install with: pip install paddleocr paddlepaddle-gpu pdf2image")
        return "", []
    
    print(f"🐼 PaddleOCR: Starting extraction...")
    print(f"   GPU: {'Enabled' if use_gpu else 'Disabled'}")
    print(f"   Table Detection: {'Enabled' if enable_table else 'Disabled'}")
    
    try:
        # Initialize PaddleOCR
        if enable_table:
            # Use PPStructure for table-aware extraction
            ocr_engine = PPStructure(
                use_gpu=use_gpu,
                show_log=False,
                lang='en',
                table=True,
                ocr=True,
                layout=True
            )
        else:
            # Use basic PaddleOCR with high resolution limit to prevent data loss
            ocr_engine = PaddleOCR(
                use_gpu=use_gpu,
                show_log=False,
                lang='en',
                use_angle_cls=True,
                det_limit_side_len=4000  # Prevent downscaling of large dense pages
            )
        
        # Convert PDF to images
        print(f"   📄 Converting PDF to images...")
        images = convert_from_path(pdf_path, dpi=300)  # Increased DPI to catch small text
        
        extracted_pages = []
        metadata = []
        
        total_pages = len(images)
        print(f"   🔍 Processing {total_pages} pages...")
        
        for page_num, image in enumerate(images, start=1):
            print(f"      Page {page_num}/{total_pages}...", end=" ")
            
            # Convert PIL Image to numpy array
            img_array = np.array(image)
            
            if enable_table:
                # Structure-aware extraction
                result = ocr_engine(img_array)
                page_text = _format_structure_result(result)
            else:
                # Basic OCR extraction
                result = ocr_engine.ocr(img_array, cls=True)
                page_text = _format_basic_result(result)
            
            extracted_pages.append(page_text)
            
            metadata.append({
                "page_number": page_num,
                "text": page_text,
                "is_scanned": True,
                "extraction_method": "paddleocr-structure" if enable_table else "paddleocr-basic",
                "confidence": 0.92
            })
            
            print(f"✓ ({len(page_text)} chars)")
        
        # Combine all pages
        full_text = "\n\n--- Page Break ---\n\n".join(extracted_pages)
        
        # ── FIX: Split OCR-merged ClaimID+Date strings ─────────────────────────
        # PaddleOCR sometimes fuses adjacent columns, producing strings like:
        #   4A2409QGMJG-00009/19/2024  (claim number + date with no space)
        # This regex inserts a space between the claim-number part and the date
        # so downstream parsers and the LLM see them as two separate tokens.
        # Pattern: [UPPER-ALPHA+DIGITS+DASH sequence] immediately followed by [M/D/YYYY or MM/DD/YYYY]
        import re as _re
        full_text = _re.sub(
            r'([A-Z][A-Z0-9\-]{3,})(\d{1,2}/\d{1,2}/\d{4})',
            r'\1 \2',
            full_text
        )
        # ────────────────────────────────────────────────────────────────────────
        
        print(f"   PaddleOCR SUCCESS: Extracted {len(full_text)} characters")
        return full_text, metadata

        
    except Exception as e:
        print(f"   ❌ PaddleOCR failed: {e}")
        import traceback
        traceback.print_exc()
        return "", []


def _format_structure_result(result: List[Dict]) -> str:
    """
    Format PPStructure result (table-aware) into readable text.
    Preserves table structure and layout.
    """
    lines = []
    
    for item in result:
        item_type = item.get('type', '')
        
        if item_type == 'table':
            # Format table structure
            table_html = item.get('res', {}).get('html', '')
            if table_html:
                lines.append("[TABLE]")
                # Parse table HTML and format as text
                table_text = _parse_table_html(table_html)
                lines.append(table_text)
                lines.append("[/TABLE]")
        
        elif item_type == 'figure':
            # Skip figures/images
            lines.append("[FIGURE]")
        
        else:
            # Regular text block
            text_result = item.get('res', [])
            if isinstance(text_result, list):
                for text_item in text_result:
                    if isinstance(text_item, dict):
                        text = text_item.get('text', '')
                    elif isinstance(text_item, (list, tuple)) and len(text_item) >= 2:
                        text = text_item[1][0] if isinstance(text_item[1], (list, tuple)) else text_item[1]
                    else:
                        text = str(text_item)
                    
                    if text.strip():
                        lines.append(text.strip())
    
    return "\n".join(lines)


def _format_basic_result(result: List) -> str:
    """
    Format basic PaddleOCR result into readable text while preserving spatial layout.
    Uses absolute bounding box X-coordinates to perfectly align text into vertical columns.
    """
    if not result or len(result) == 0 or not result[0]:
        return ""
    
    boxes = []
    for line_result in result[0]:
        if isinstance(line_result, (list, tuple)) and len(line_result) >= 2:
            bbox = line_result[0]
            text = line_result[1][0]
            if text.strip():
                x_coords = [pt[0] for pt in bbox]
                y_coords = [pt[1] for pt in bbox]
                boxes.append({
                    'text': text.strip(),
                    'x': min(x_coords),
                    'max_x': max(x_coords),
                    'y': sum(y_coords) / len(y_coords),
                    'h': max(y_coords) - min(y_coords)
                })
                
    if not boxes: return ""
    
    # 1. Calculate global average character width for precise column mapping
    total_chars = sum(len(b['text']) for b in boxes)
    total_width = sum(b['max_x'] - b['x'] for b in boxes)
    global_char_width = total_width / max(1, total_chars)
    if global_char_width < 1.0: global_char_width = 8.0
    
    # 2. Find the left-most margin to avoid excessive leading spaces
    min_x_page = min(b['x'] for b in boxes)
    
    # 3. Sort primarily by Y
    boxes.sort(key=lambda b: b['y'])
    
    # 4. Group into lines based on Y overlap
    lines = []
    current_line = [boxes[0]]
    for box in boxes[1:]:
        # If Y difference is less than half the character height, it's on the same line
        if abs(box['y'] - current_line[0]['y']) < (box['h'] / 2):
            current_line.append(box)
        else:
            lines.append(current_line)
            current_line = [box]
    if current_line: lines.append(current_line)
    
    # 5. Build each line using absolute column positioning
    formatted_lines = []
    for line in lines:
        line.sort(key=lambda b: b['x'])
        
        line_chars = []
        for box in line:
            # Map absolute X to a character column index
            adjusted_x = box['x'] - min_x_page
            start_col = int(adjusted_x / global_char_width)
            
            # If boxes overlap or are too close, ensure at least 1 space of separation
            if start_col <= len(line_chars) and len(line_chars) > 0:
                start_col = len(line_chars) + 1
                
            # Pad with spaces until we reach the target absolute column
            spaces_to_add = start_col - len(line_chars)
            if spaces_to_add > 0:
                line_chars.extend([' '] * spaces_to_add)
                
            # Insert the text
            line_chars.extend(list(box['text']))
            
        formatted_lines.append("".join(line_chars))
        
    return "\n".join(formatted_lines)


def _parse_table_html(html: str) -> str:
    """
    Parse table HTML to readable text format.
    Converts HTML table to plain text with column alignment.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        
        rows = []
        for tr in soup.find_all('tr'):
            cells = []
            for td in tr.find_all(['td', 'th']):
                cell_text = td.get_text(strip=True)
                cells.append(cell_text)
            if cells:
                rows.append(" | ".join(cells))
        
        return "\n".join(rows)
    except ImportError:
        # BeautifulSoup not available, return raw HTML
        return html
    except Exception as e:
        return f"[Table parsing error: {e}]"


def process_pdf_with_paddleocr(
    input_pdf_path: str, 
    output_text_path: str = None,
    use_gpu: bool = True,
    enable_table: bool = True
) -> Tuple[str, List[Dict]]:
    """
    Complete pipeline: PaddleOCR extraction → Save
    
    Args:
        input_pdf_path: Path to input PDF
        output_text_path: Path to save extracted text (optional)
        use_gpu: Use GPU acceleration
        enable_table: Enable table structure recognition
        
    Returns:
        Tuple of (extracted_text, metadata)
    """
    # Extract text
    text, metadata = extract_with_paddleocr(input_pdf_path, use_gpu, enable_table)
    
    # Save text if output path provided
    if output_text_path and text:
        with open(output_text_path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"✅ Text saved to: {output_text_path}")
    
    return text, metadata


if __name__ == "__main__":
    # Test with your problematic PDF
    test_pdf = r"C:\Users\Intern\gpu\Gpu_server\Insurance_pdf_extractor-main\backend\outputs\extraction_20260819_181715_6755_Loss_Runs_1-7_pdf\processed_Loss Runs 1-7.pdf"
    output_text = r"C:\Users\Intern\gpu\Gpu_server\Insurance_pdf_extractor-main\backend\outputs\extraction_20260819_181715_6755_Loss_Runs_1-7_pdf\extracted_text_paddleocr.txt"
    
    if os.path.exists(test_pdf):
        print("\n" + "="*60)
        print("PaddleOCR Test Extraction")
        print("="*60 + "\n")
        
        text, metadata = process_pdf_with_paddleocr(
            test_pdf, 
            output_text,
            use_gpu=True,
            enable_table=True
        )
        
        if text:
            print(f"\n{'='*60}")
            print("SAMPLE OUTPUT (first 800 chars):")
            print(f"{'='*60}")
            print(text[:800])
            print(f"\n{'='*60}")
            print(f"Total Characters: {len(text)}")
            print(f"Total Pages: {len(metadata)}")
            print(f"{'='*60}")
    else:
        print(f"Test PDF not found: {test_pdf}")
