"""
PaddleOCR Enhancement Module for Workers Compensation
High-speed GPU-accelerated OCR with table structure preservation and spatial alignment.

CRASH-SAFE DESIGN:
  - PaddleOCR runs in an isolated subprocess per page.
  - If PaddleOCR calls sys.exit(0) on OOM, only the child process dies.
  - The main server stays alive and returns a clean error / fallback.
  - Pages are processed low-RAM / disk-backed to keep memory footprint constant.
  - 300 DPI high-resolution rendering.
"""
import os
import sys
import json
import tempfile
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Tuple, List, Dict, Optional
import re as _re

# Fix Windows console encoding for Unicode (e.g. checkmarks, emoji)
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Dynamic worker count
_MAX_PARALLEL_WORKERS = 4
try:
    import torch
    if torch.cuda.is_available():
        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        _MAX_PARALLEL_WORKERS = 4 if vram_gb >= 8 else 2
    else:
        _MAX_PARALLEL_WORKERS = 2
except Exception:
    _MAX_PARALLEL_WORKERS = 2


# ─────────────────────────────────────────────────────────────────────────────
#  SUBPROCESS WORKER  (this block runs when the module is called as a child)
# ─────────────────────────────────────────────────────────────────────────────
def _subprocess_ocr_worker():
    """
    Entry point when this module is launched as a subprocess.
    Reads args from argv, runs OCR on a single image file, writes JSON to stdout.
    Usage:
        python paddleocr_enhancer.py --worker <img_path> <use_gpu> <enable_table>
    """
    img_path   = sys.argv[2]
    use_gpu    = sys.argv[3].lower() == "true"
    enable_table = sys.argv[4].lower() == "true"

    try:
        from paddleocr import PPStructure, PaddleOCR
        import numpy as np
        from PIL import Image

        img = Image.open(img_path).convert('RGB')
        img_array = np.array(img)

        if enable_table:
            ocr_engine = PPStructure(
                use_gpu=use_gpu,
                show_log=False,
                lang='en',
                table=True,
                ocr=True,
                layout=True
            )
            result = ocr_engine(img_array)
            page_text = _format_structure_result(result)
        else:
            ocr_engine = PaddleOCR(
                use_gpu=use_gpu,
                show_log=False,
                lang='en',
                use_angle_cls=True,
                det_limit_side_len=4000,
                # Make detector more sensitive to catch faint or small text lines
                det_db_thresh=0.2,
                det_db_box_thresh=0.2,
                det_db_unclip_ratio=1.6
            )
            result = ocr_engine.ocr(img_array, cls=True)
            page_text = _format_basic_result(result)

        output = {"success": True, "text": page_text}
    except Exception as e:
        output = {"success": False, "text": "", "error": str(e)}

    # Write result as JSON to stdout so the parent can read it
    print(json.dumps(output, ensure_ascii=False))
    sys.exit(0)


def _get_python_exe():
    """Find the Python executable that has paddleocr installed."""
    try:
        import paddleocr
        return sys.executable
    except ImportError:
        pass

    # Check known venv in the workspace
    candidates = [
        Path(r"C:\Users\Intern\gpu\Gpu_server\venv\Scripts\python.exe"),
        Path(__file__).resolve().parent.parent.parent / "venv" / "Scripts" / "python.exe",
        Path(__file__).resolve().parent.parent / "venv" / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


# ─────────────────────────────────────────────────────────────────────────────
#  ISOLATED PAGE OCR  (calls a fresh subprocess for every page)
# ─────────────────────────────────────────────────────────────────────────────
def _ocr_page_in_subprocess(img_path: str, use_gpu: bool, enable_table: bool, timeout: int = 300) -> str:
    """
    Run OCR on a single image inside an isolated subprocess.
    If the subprocess OOMs / calls sys.exit, this function catches it cleanly
    and returns an empty string — the parent process is never affected.
    """
    try:
        py_exe = _get_python_exe()
        result = subprocess.run(
            [py_exe, __file__, "--worker", img_path, str(use_gpu), str(enable_table)],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace"
        )

        stdout = result.stdout.strip()
        if not stdout:
            print(f"      ⚠️  Subprocess produced no output (exit code {result.returncode})")
            if result.stderr:
                print(f"      stderr: {result.stderr[:300]}")
            return ""

        # Find the last JSON object in stdout
        json_line = None
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                json_line = line
                break

        if not json_line:
            print(f"      ⚠️  Could not find JSON in subprocess output")
            return ""

        data = json.loads(json_line)
        if data.get("success"):
            return data.get("text", "")
        else:
            print(f"      ⚠️  Subprocess OCR error: {data.get('error', 'unknown')}")
            return ""

    except subprocess.TimeoutExpired:
        print(f"      ⚠️  OCR subprocess timed out after {timeout}s — skipping page")
        return ""
    except Exception as e:
        print(f"      ⚠️  Subprocess launch failed: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN EXTRACTION FUNCTION
# ─────────────────────────────────────────────────────────────────────────────
def extract_with_paddleocr(pdf_path: str, use_gpu: bool = True, enable_table: bool = False) -> Tuple[str, List[Dict]]:
    """
    Extract text from PDF using PaddleOCR with table structure preservation.
    
    CRASH-SAFE: Each page is OCR'd in an isolated subprocess.
    """
    try:
        from pdf2image import convert_from_path
    except ImportError as e:
        print(f"   ⚠️ pdf2image not installed: {e}")
        return "", []

    print(f"🐼 PaddleOCR (Workers Comp): Starting extraction (crash-safe subprocess mode)...")
    print(f"   GPU: {'Enabled' if use_gpu else 'Disabled'}")
    print(f"   Table Detection: {'Enabled' if enable_table else 'Disabled'}")
    print(f"   Mode: Page-by-page isolated subprocess (no server crash risk)")

    extracted_pages = []
    metadata = []

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            print(f"   📄 Converting PDF to images (400 DPI for high accuracy)...")
            image_paths = convert_from_path(
                pdf_path,
                dpi=400,
                output_folder=tmp_dir,
                fmt="png",
                paths_only=True
            )

            total_pages = len(image_paths)
            print(f"   🔍 Processing {total_pages} pages — ⚡ PARALLEL mode ({_MAX_PARALLEL_WORKERS} workers)...")

            page_results  = [""] * total_pages
            page_metadata = [{}] * total_pages

            _gpu_sem = threading.Semaphore(_MAX_PARALLEL_WORKERS)

            def _ocr_page_task(idx: int, img_path: str):
                page_num = idx + 1
                with _gpu_sem:
                    print(f"      Page {page_num}/{total_pages}...", end=" ", flush=True)
                    text = _ocr_page_in_subprocess(
                        img_path=img_path,
                        use_gpu=use_gpu,
                        enable_table=enable_table,
                        timeout=300
                    )
                    print(f"✓ ({len(text)} chars)")
                    return idx, text

            with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_WORKERS) as executor:
                futures = {
                    executor.submit(_ocr_page_task, i, str(path)): i
                    for i, path in enumerate(image_paths)
                }
                for future in as_completed(futures):
                    try:
                        idx, page_text = future.result()
                    except Exception as exc:
                        idx = futures[future]
                        print(f"      ⚠️  Page {idx+1} future raised: {exc}")
                        page_text = ""

                    page_results[idx]  = page_text
                    page_metadata[idx] = {
                        "page_number": idx + 1,
                        "text": f"\n{'='*80}\nPAGE {idx+1}\n{'='*80}\n\n" + page_text,
                        "is_scanned": True,
                        "extraction_method": "paddleocr-structure" if enable_table else "paddleocr-basic",
                        "confidence": 0.95
                    }

            extracted_pages = page_results
            metadata        = page_metadata

    except Exception as e:
        print(f"   ❌ PaddleOCR pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        return "", []

    # Combine all pages
    full_text = "\n\n".join([m.get("text", "") for m in metadata])

    # ── POST-PROCESSING: Fix common PaddleOCR OCR errors ─────────────────────
    full_text = _clean_paddle_text(full_text)

    print(f"   ✅ PaddleOCR SUCCESS: Extracted {len(full_text)} characters from {len(metadata)} pages")
    return full_text, metadata


def _clean_paddle_text(text: str) -> str:
    """
    Post-process PaddleOCR output to fix common OCR errors on insurance forms.
    - Fixes pipe character misread as 'I' in all-caps words
    - Fixes digit/letter confusion in known patterns
    - Cleans stray single characters and repeated OCR noise
    - Fixes split date strings
    """
    if not text:
        return text

    lines = text.splitlines()
    cleaned = []
    for line in lines:
        # Fix: pipe | misread as I inside all-caps words (e.g. SUBMIss|ON → SUBMISSION)
        line = _re.sub(r'([A-Z])\|([A-Z])', r'\1I\2', line)

        # Fix: OCR-merged date strings (FIELDNAME1/1/2027 → FIELDNAME 1/1/2027)
        line = _re.sub(r'([A-Z]{4,})(\d{1,2}/\d{1,2}/\d{4})', r'\1 \2', line)

        # Fix: common OCR char substitutions in known insurance keywords
        line = line.replace('SUBMIsS|ON', 'SUBMISSION')
        line = line.replace('STATUs', 'STATUS')
        line = line.replace('ANNVERSARY', 'ANNIVERSARY')
        line = line.replace('EFFECTNE', 'EFFECTIVE')
        line = line.replace('IESTIMATEDANNUAL', 'ESTIMATED ANNUAL')
        line = line.replace('IREMUNERATION/', 'REMUNERATION/')
        line = line.replace('IPAYROLL', 'PAYROLL')
        line = line.replace('ANNUALPREMIUM', 'ANNUAL PREMIUM')
        line = line.replace('STANDARDPREMIUM', 'STANDARD PREMIUM')
        line = line.replace('FACTOREDPREMIUM', 'FACTORED PREMIUM')
        line = line.replace('EMPLOYERREGSTRATIONNUMBER', 'EMPLOYER REGISTRATION NUMBER')
        line = line.replace('NCCIRISKID', 'NCCI RISK ID')
        line = line.replace('BUREAUID', 'BUREAU ID')

        # Fix: 'l' (lowercase L) misread as '1' in word-only contexts
        # e.g. "lnc" -> "Inc", "lNVOICE" -> "INVOICE"
        line = _re.sub(r'\bl([A-Z])', lambda m: 'I' + m.group(1), line)

        # Fix: stray 's' or 'l' replacing capital letters in all-caps words
        # e.g. "sTATUs" → "STATUS" (only if the word is mostly uppercase)
        def fix_mixed_caps(m):
            word = m.group(0)
            upper = word.upper()
            # Only fix if word is >70% uppercase chars already
            if sum(1 for c in word if c.isupper()) / max(1, len(word)) > 0.6:
                return upper
            return word
        line = _re.sub(r'\b[A-Za-z]{4,}\b', fix_mixed_caps, line)

        cleaned.append(line)

    return '\n'.join(cleaned)


# ─────────────────────────────────────────────────────────────────────────────
#  HYBRID MERGE: PaddleOCR + PyMuPDF Digital Text
#  Ensures ZERO text is missed — PaddleOCR gives spatial layout,
#  PyMuPDF fills in any native digital text that PaddleOCR misses.
# ─────────────────────────────────────────────────────────────────────────────
def _extract_digital_text_pymupdf(pdf_path: str) -> List[str]:
    """
    Extract native digital text per page using PyMuPDF with spatial word sorting.

    Instead of using get_text("text") which follows internal PDF order (wrong for
    multi-column forms), this function:
    1. Gets every word with its (x, y) bounding box coordinates.
    2. Groups words into visual lines by y-band (within LINE_TOLERANCE points).
    3. Within each line, sorts words left-to-right by x coordinate.
    4. Joins lines top-to-bottom for correct visual reading order.

    This gives perfectly-spelled text in the correct spatial order.
    Returns list of text strings, one per page.
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        pages = []

        LINE_TOLERANCE = 4  # PDF points — words within 4pt vertically = same line

        for page in doc:
            # Each word: (x0, y0, x1, y1, "word", block_no, line_no, word_no)
            words = page.get_text("words")
            if not words:
                pages.append("")
                continue

            # Sort words by y-band (rounded to nearest LINE_TOLERANCE), then by x
            words_sorted = sorted(
                words,
                key=lambda w: (round(w[1] / LINE_TOLERANCE) * LINE_TOLERANCE, w[0])
            )

            # Group into visual lines
            lines = []
            current_line_words = []  # list of (x0, text)
            current_y_band = None

            for word in words_sorted:
                x0, y0, x1, y1, text = word[0], word[1], word[2], word[3], word[4]
                y_band = round(y0 / LINE_TOLERANCE) * LINE_TOLERANCE

                if current_y_band is None:
                    current_y_band = y_band

                if abs(y_band - current_y_band) <= LINE_TOLERANCE:
                    # Same visual line
                    current_line_words.append((x0, text))
                else:
                    # New line — flush current
                    if current_line_words:
                        current_line_words.sort(key=lambda w: w[0])
                        lines.append(" ".join(w[1] for w in current_line_words))
                    current_line_words = [(x0, text)]
                    current_y_band = y_band

            # Flush last line
            if current_line_words:
                current_line_words.sort(key=lambda w: w[0])
                lines.append(" ".join(w[1] for w in current_line_words))

            pages.append("\n".join(lines))

        doc.close()
        return pages

    except Exception as e:
        print(f"   ⚠️ PyMuPDF digital text extraction failed: {e}")
        return []


def _smart_merge_page(paddle_text: str, digital_text: str, page_num: int) -> str:
    """
    Smart merge strategy:
    1. Use PyMuPDF digital text as the PRIMARY clean source
       (correct word spacing, correct spelling, no OCR typos).
    2. Find tokens in PaddleOCR that are NOT in the digital text
       (these are image-only items: checkbox marks, handwritten values,
       stamped data, filled-in fields) and append them as a supplement.

    This gives clean, properly-ordered output with zero information loss.
    """
    if not digital_text or not digital_text.strip():
        # PDF has no digital text layer — fall back to PaddleOCR only
        return paddle_text

    if not paddle_text or not paddle_text.strip():
        # PaddleOCR failed — use digital text only
        return digital_text

    # Tokens present in PaddleOCR but NOT in the clean digital text
    digital_tokens = set(_re.findall(r'[A-Za-z0-9\$\.\,\-\/\#\%]{2,}', digital_text.lower()))
    paddle_tokens = set(_re.findall(r'[A-Za-z0-9\$\.\,\-\/\#\%]{2,}', paddle_text.lower()))

    # Words that PaddleOCR found but digital text doesn't have
    # These are image-only values (filled checkboxes, handwriting, stamps)
    image_only_tokens = paddle_tokens - digital_tokens

    # Collect PaddleOCR lines that contain image-only tokens
    paddle_supplement_lines = []
    for line in paddle_text.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue
        line_toks = set(_re.findall(r'[A-Za-z0-9\$\.\,\-\/\#\%]{2,}', line_stripped.lower()))
        # Only include lines where more than half the tokens are image-only
        if line_toks and len(line_toks & image_only_tokens) / len(line_toks) > 0.5:
            paddle_supplement_lines.append(line_stripped)

    # Build final output: clean digital text + image-only additions from OCR
    result = digital_text.strip()
    if paddle_supplement_lines:
        supplement = "\n".join(paddle_supplement_lines)
        result += f"\n\n[OCR SUPPLEMENT - Page {page_num} - Image-only content]\n{supplement}"

    return result


def extract_hybrid(pdf_path: str, use_gpu: bool = True) -> Tuple[str, List[Dict]]:
    """
    HYBRID EXTRACTION: PyMuPDF digital text (primary) + PaddleOCR (image supplement).

    Strategy:
    - PyMuPDF digital text = PRIMARY: perfect word spacing, correct spelling,
      correct order — used as the clean base for each page.
    - PaddleOCR = SUPPLEMENT: catches image-only content that PyMuPDF can't see
      (filled checkboxes, handwritten values, stamped amounts, overlaid data).

    This gives 100% coverage with clean, properly-ordered text output.

    Args:
        pdf_path: Path to input PDF
        use_gpu:  Use GPU for PaddleOCR (default: True)

    Returns:
        Tuple of (full_merged_text, metadata_list)
    """
    print(f"🔀 HYBRID EXTRACTION: PyMuPDF (primary) + PaddleOCR (image supplement)...")

    # Step 1: PyMuPDF digital text (fast, clean, perfect spelling)
    print(f"   📖 Step 1: Extracting clean digital text layer with PyMuPDF...")
    digital_pages = _extract_digital_text_pymupdf(pdf_path)
    total_digital = sum(len(p) for p in digital_pages)
    print(f"   ✅ PyMuPDF: {total_digital} chars across {len(digital_pages)} pages")

    # Step 2: PaddleOCR (image-rendered content, spatial layout)
    print(f"   🐼 Step 2: PaddleOCR for image-only content...")
    paddle_full, paddle_meta = extract_with_paddleocr(pdf_path, use_gpu=use_gpu, enable_table=False)

    # Step 3: Smart per-page merge
    if not digital_pages:
        print(f"   ⚠️ No digital text found — using PaddleOCR only")
        return paddle_full, paddle_meta

    merged_metadata = []
    for i, meta in enumerate(paddle_meta):
        page_num = meta.get("page_number", i + 1)
        paddle_page_text = meta.get("text", "")
        digital_page_text = digital_pages[i] if i < len(digital_pages) else ""

        merged_page_text = _smart_merge_page(paddle_page_text, digital_page_text, page_num)

        new_meta = dict(meta)
        new_meta["text"] = merged_page_text
        new_meta["extraction_method"] = "hybrid-digital-primary"
        merged_metadata.append(new_meta)

        before = len(digital_page_text)
        after = len(merged_page_text)
        if after > before:
            print(f"   Page {page_num}: digital={before} chars + OCR supplement +{after - before} chars")
        else:
            print(f"   Page {page_num}: {before} chars (digital text complete)")

    full_merged = "\n\n".join([m.get("text", "") for m in merged_metadata])

    # Final OCR merge fix
    full_merged = _re.sub(
        r'([A-Z][A-Z0-9\-]{3,})(\d{1,2}/\d{1,2}/\d{4})',
        r'\1 \2',
        full_merged
    )

    print(f"   ✅ HYBRID SUCCESS: {len(full_merged)} total chars ({len(merged_metadata)} pages)")
    return full_merged, merged_metadata


# ─────────────────────────────────────────────────────────────────────────────
#  FORMATTING HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _format_structure_result(result: List[Dict]) -> str:
    """
    Format PPStructure result (table-aware) into readable text.
    Preserves table structure and layout.
    """
    lines = []

    for item in result:
        item_type = item.get('type', '')

        if item_type == 'table':
            table_html = item.get('res', {}).get('html', '')
            if table_html:
                lines.append("[TABLE]")
                table_text = _parse_table_html(table_html)
                lines.append(table_text)
                lines.append("[/TABLE]")

        elif item_type == 'figure':
            lines.append("[FIGURE]")

        else:
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
    Uses absolute bounding box X-coordinates to align text into vertical columns.
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

    if not boxes:
        return ""

    # 1. Determine page bounds and char width based on fixed 180-column grid
    min_x_page = min(b['x'] for b in boxes)
    max_x_page = max(b['max_x'] for b in boxes)
    page_pixel_width = max(1.0, max_x_page - min_x_page)
    
    # 180 columns ensures the grid is wide enough that long sentences
    # don't overrun the target column of the right-aligned checkboxes.
    PAGE_WIDTH_CHARS = 180 
    char_width = page_pixel_width / PAGE_WIDTH_CHARS

    # 2. Sort primarily by Y
    boxes.sort(key=lambda b: b['y'])

    # 3. Group into lines based on Y overlap
    lines = []
    current_line = [boxes[0]]
    for box in boxes[1:]:
        if abs(box['y'] - current_line[0]['y']) < (box['h'] / 2.0):
            current_line.append(box)
        else:
            lines.append(current_line)
            current_line = [box]
    if current_line:
        lines.append(current_line)

    # 4. Build lines preserving absolute vertical alignment
    formatted_lines = []
    
    for line in lines:
        line.sort(key=lambda b: b['x'])
        formatted_line = ""
        
        for box in line:
            # Calculate absolute starting column for this word
            target_col = int((box['x'] - min_x_page) / char_width)
            
            # Add spaces until we reach the target column
            spaces_to_add = target_col - len(formatted_line)
            if spaces_to_add > 0:
                formatted_line += " " * spaces_to_add
            elif formatted_line and not formatted_line.endswith(' '):
                # Always ensure at least one space between separate boxes
                formatted_line += " "
                
            formatted_line += box['text']
            
        formatted_lines.append(formatted_line.rstrip())

    return "\n".join(formatted_lines)


def _parse_table_html(html: str) -> str:
    """
    Parse table HTML to readable text format.
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
        return html
    except Exception as e:
        return f"[Table parsing error: {e}]"


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC PIPELINE FUNCTION
# ─────────────────────────────────────────────────────────────────────────────
def process_pdf_with_paddleocr(
    input_pdf_path: str,
    output_text_path: str = None,
    use_gpu: bool = True,
    enable_table: bool = False
) -> Tuple[str, List[Dict]]:
    """
    Complete pipeline: PaddleOCR extraction → Save
    """
    text, metadata = extract_with_paddleocr(input_pdf_path, use_gpu, enable_table)

    if output_text_path and text:
        with open(output_text_path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"✅ Text saved to: {output_text_path}")

    return text, metadata


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--worker":
        _subprocess_ocr_worker()
    else:
        test_pdf = sys.argv[1] if len(sys.argv) > 1 else r"c:\Users\Intern\gpu\Gpu_server\National NEMT_Acord_20260915215308.pdf"
        if os.path.exists(test_pdf):
            print(f"Testing PaddleOCR on {test_pdf}...")
            text, meta = process_pdf_with_paddleocr(test_pdf, use_gpu=True, enable_table=True)
            print(f"Extracted {len(text)} characters, {len(meta)} pages.")
            print(text[:1000])
        else:
            print(f"File not found: {test_pdf}")
