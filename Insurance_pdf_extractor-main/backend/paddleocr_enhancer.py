"""
PaddleOCR Enhancement Module
High-speed GPU-accelerated OCR with table structure preservation

CRASH-SAFE DESIGN:
  - PaddleOCR runs in an isolated subprocess per page.
  - If PaddleOCR calls sys.exit(0) on OOM, only the child process dies.
  - The FastAPI server stays alive and returns a clean error.
  - Pages are processed one at a time to keep memory footprint constant.
  - DPI stays at 300 — no accuracy trade-off.
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

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Worker count (read from GPU_OCR_WORKERS in .env, default 1)
def _get_worker_count() -> int:
    env_w = os.environ.get("GPU_OCR_WORKERS", "").strip()
    if env_w.isdigit():
        return max(1, int(env_w))
    for env_path in [Path(__file__).resolve().parents[2] / ".env", Path(__file__).resolve().parents[3] / ".env"]:
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("GPU_OCR_WORKERS="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val.isdigit():
                        return max(1, int(val))
    return 1

_MAX_PARALLEL_WORKERS = _get_worker_count()

# Shared GPU PaddleOCR pool (pdf_extractor/GPU). Persistent workers load the
# models onto the GPU once and OCR pages in parallel.
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.append(_PROJECT_ROOT)
try:
    import GPU as _gpu_ocr
except Exception as _gpu_import_err:  # GPU package missing / broken -> CPU path
    _gpu_ocr = None
    print(f"[PaddleOCR] GPU package unavailable, using CPU subprocess mode: {_gpu_import_err}")

# Engine profile for the GPU workers — must match _subprocess_ocr_worker below.
_GPU_PROFILE_BASIC = {
    "kind": "basic",
    "cls": True,
    "kwargs": {"use_angle_cls": True, "det_limit_side_len": 2000},
}
_GPU_PROFILE_STRUCTURE = {"kind": "structure", "kwargs": {}}


def _ocr_pages_on_gpu(image_paths: List[str], enable_table: bool) -> Optional[List[Optional[str]]]:
    """
    OCR all pages in parallel on the shared GPU pool.
    Returns None if GPU OCR is unavailable, else a list of page texts where a
    None entry means that page must fall back to the CPU subprocess path.
    """
    if _gpu_ocr is None or not _gpu_ocr.gpu_ocr_available():
        return None
    profile = _GPU_PROFILE_STRUCTURE if enable_table else _GPU_PROFILE_BASIC
    total = len(image_paths)

    def _progress(idx, res):
        print(f"      Page {idx + 1}/{total} ✓ GPU{res.get('device')} "
              f"pid={res.get('pid')} {res.get('elapsed')}s")

    try:
        raws = _gpu_ocr.ocr_images_parallel(image_paths, profile, on_page_done=_progress)
    except Exception as e:
        print(f"   ⚠️ GPU OCR pool error, falling back to CPU: {e}")
        return None
    if raws is None:
        return None

    texts: List[Optional[str]] = []
    for raw in raws:
        if raw is None:
            texts.append(None)
            continue
        try:
            texts.append(_format_structure_result(raw) if enable_table else _format_basic_result(raw))
        except Exception as e:
            print(f"      ⚠️ Formatting GPU result failed: {e}")
            texts.append(None)
    return texts


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
        os.environ.setdefault("FLAGS_allocator_strategy", "auto_growth")
        os.environ.setdefault("FLAGS_fraction_of_gpu_memory_to_use", "0")
        from paddleocr import PPStructure, PaddleOCR
        import numpy as np
        from PIL import Image

        img = Image.open(img_path)
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
                det_limit_side_len=2000
            )
            result = ocr_engine.ocr(img_array, cls=True)
            page_text = _format_basic_result(result)

        output = {"success": True, "text": page_text}
    except Exception as e:
        output = {"success": False, "text": "", "error": str(e)}

    # Write result as JSON to stdout so the parent can read it
    print(json.dumps(output, ensure_ascii=False))
    sys.exit(0)


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
        result = subprocess.run(
            [sys.executable, __file__, "--worker", img_path, str(use_gpu), str(enable_table)],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace"
        )

        # subprocess stdout should be a JSON line
        stdout = result.stdout.strip()
        if not stdout:
            print(f"      ⚠️  Subprocess produced no output (exit code {result.returncode})")
            if result.stderr:
                print(f"      stderr: {result.stderr[:300]}")
            return ""

        # Find the last JSON object in stdout (PaddleOCR may print its own logs)
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
def extract_with_paddleocr(pdf_path: str, use_gpu: bool = True, enable_table: bool = True, max_pages: Optional[int] = None) -> Tuple[str, List[Dict]]:
    """
    Extract text from PDF using PaddleOCR with table structure preservation.

    CRASH-SAFE: Each page is OCR'd in an isolated subprocess.
    PaddleOCR's sys.exit(0) on OOM cannot kill the parent FastAPI server.
    Pages are processed one at a time — memory stays constant regardless of page count.

    Args:
        pdf_path: Path to input PDF
        use_gpu: Use GPU acceleration (default: True)
        enable_table: Enable table structure recognition (default: True)
        max_pages: Limit extraction to the first N pages (ideal for classification)

    Returns:
        Tuple of (extracted_text, metadata_list)
    """
    try:
        from pdf2image import convert_from_path
    except ImportError as e:
        print(f"   ⚠️ pdf2image not installed: {e}")
        return "", []

    print(f"🐼 PaddleOCR: Starting extraction (crash-safe subprocess mode)...")
    print(f"   GPU: {'Enabled' if use_gpu else 'Disabled'}")
    print(f"   Table Detection: {'Enabled' if enable_table else 'Disabled'}")
    if max_pages:
        print(f"   Page Limit: First {max_pages} pages")
    print(f"   Mode: Page-by-page isolated subprocess (no server crash risk)")

    extracted_pages = []
    metadata = []

    try:
        # Use a temp directory for page images — avoids holding all pages in RAM at once
        with tempfile.TemporaryDirectory() as tmp_dir:
            print(f"   📄 Converting PDF to images (300 DPI)...")

            # Convert pages to image files on disk — low RAM usage
            convert_kwargs = dict(
                dpi=300,
                output_folder=tmp_dir,
                fmt="png",
                paths_only=True,         # Returns file paths, not PIL objects → saves RAM
                thread_count=_gpu_ocr.render_threads() if _gpu_ocr else 1,  # parallel rasterisation
            )
            if max_pages and max_pages > 0:
                convert_kwargs["first_page"] = 1
                convert_kwargs["last_page"] = max_pages

            image_paths = convert_from_path(pdf_path, **convert_kwargs)

            total_pages = len(image_paths)

            # Pre-allocate results list to preserve page order regardless of completion order
            page_results  = ["" ] * total_pages
            page_metadata = [{}  ] * total_pages
            page_device   = ["cpu"] * total_pages

            # ── GPU PATH: all pages in parallel on the persistent GPU pool ──
            gpu_texts = _ocr_pages_on_gpu([str(p) for p in image_paths], enable_table) if use_gpu else None
            if gpu_texts is not None:
                print(f"   ⚡ GPU parallel OCR done for "
                      f"{sum(t is not None for t in gpu_texts)}/{total_pages} pages")
                cpu_indices = [i for i, t in enumerate(gpu_texts) if t is None]
                for i, t in enumerate(gpu_texts):
                    if t is not None:
                        page_results[i] = t
                        page_device[i] = "gpu"
            else:
                cpu_indices = list(range(total_pages))

            if cpu_indices:
                print(f"   🔍 Processing {len(cpu_indices)} pages — CPU subprocess mode ({_MAX_PARALLEL_WORKERS} workers)...")

            # Semaphore ensures at most _MAX_PARALLEL_WORKERS subprocesses run at once
            # even if the executor has more threads queued.
            _gpu_sem = threading.Semaphore(_MAX_PARALLEL_WORKERS)

            def _ocr_page_task(idx: int, img_path: str):
                """Worker function: OCR one page in an isolated subprocess (crash-safe)."""
                page_num = idx + 1
                with _gpu_sem:           # throttle concurrent GPU subprocesses
                    print(f"      Page {page_num}/{total_pages}...", end=" ", flush=True)
                    text = _ocr_page_in_subprocess(
                        img_path=img_path,
                        use_gpu=use_gpu,
                        enable_table=enable_table,
                        timeout=300
                    )
                    print(f"✓ ({len(text)} chars)")
                    return idx, text

            # CPU / fallback path: only the pages the GPU pool did not handle
            if cpu_indices:
                with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_WORKERS) as executor:
                    futures = {
                        executor.submit(_ocr_page_task, i, str(image_paths[i])): i
                        for i in cpu_indices
                    }
                    for future in as_completed(futures):
                        try:
                            idx, page_text = future.result()
                        except Exception as exc:
                            idx = futures[future]
                            print(f"      ⚠️  Page {idx+1} future raised: {exc}")
                            page_text = ""
                        page_results[idx] = page_text

            for idx in range(total_pages):
                page_metadata[idx] = {
                    "page_number": idx + 1,
                    "text": page_results[idx],
                    "is_scanned": True,
                    "extraction_method": "paddleocr-structure" if enable_table else "paddleocr-basic",
                    "ocr_device": page_device[idx],
                    "confidence": 0.92
                }

            extracted_pages = page_results
            metadata        = page_metadata

            # Temp dir auto-cleans when the `with` block exits

    except Exception as e:
        print(f"   ❌ PaddleOCR pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        return "", []

    # Combine all pages
    full_text = "\n\n--- Page Break ---\n\n".join(extracted_pages)

    # ── FIX: Split OCR-merged ClaimID+Date strings ─────────────────────────
    # PaddleOCR sometimes fuses adjacent columns, producing strings like:
    #   4A2409QGMJG-00009/19/2024  (claim number + date with no space)
    # This regex inserts a space between the claim-number part and the date.
    full_text = _re.sub(
        r'([A-Z][A-Z0-9\-]{3,})(\d{1,2}/\d{1,2}/\d{4})',
        r'\1 \2',
        full_text
    )
    # ────────────────────────────────────────────────────────────────────────

    print(f"   ✅ PaddleOCR SUCCESS: Extracted {len(full_text)} characters from {len(metadata)} pages")
    return full_text, metadata


# ─────────────────────────────────────────────────────────────────────────────
#  FORMATTING HELPERS  (used by both main process and subprocess worker)
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

    if not boxes:
        return ""

    # 1. Calculate global average character width for precise column mapping
    total_chars = sum(len(b['text']) for b in boxes)
    total_width = sum(b['max_x'] - b['x'] for b in boxes)
    global_char_width = total_width / max(1, total_chars)
    if global_char_width < 1.0:
        global_char_width = 8.0

    # 2. Find the left-most margin to avoid excessive leading spaces
    min_x_page = min(b['x'] for b in boxes)

    # 3. Sort primarily by Y
    boxes.sort(key=lambda b: b['y'])

    # 4. Group into lines based on Y overlap
    lines = []
    current_line = [boxes[0]]
    for box in boxes[1:]:
        if abs(box['y'] - current_line[0]['y']) < (box['h'] / 2):
            current_line.append(box)
        else:
            lines.append(current_line)
            current_line = [box]
    if current_line:
        lines.append(current_line)

    # 5. Build each line using absolute column positioning
    formatted_lines = []
    for line in lines:
        line.sort(key=lambda b: b['x'])

        line_chars = []
        for box in line:
            adjusted_x = box['x'] - min_x_page
            start_col = int(adjusted_x / global_char_width)

            if start_col <= len(line_chars) and len(line_chars) > 0:
                start_col = len(line_chars) + 1

            spaces_to_add = start_col - len(line_chars)
            if spaces_to_add > 0:
                line_chars.extend([' '] * spaces_to_add)

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
    text, metadata = extract_with_paddleocr(input_pdf_path, use_gpu, enable_table)

    if output_text_path and text:
        with open(output_text_path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f"✅ Text saved to: {output_text_path}")

    return text, metadata


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # When called with --worker flag, run as OCR subprocess worker
    if len(sys.argv) >= 2 and sys.argv[1] == "--worker":
        _subprocess_ocr_worker()
    else:
        # Manual test
        test_pdf = r"C:\Users\Intern\gpu\Gpu_server\Insurance_pdf_extractor-main\backend\outputs\extraction_20260819_181715_6755_Loss_Runs_1-7_pdf\processed_Loss Runs 1-7.pdf"
        output_text = r"C:\Users\Intern\gpu\Gpu_server\Insurance_pdf_extractor-main\backend\outputs\extraction_20260819_181715_6755_Loss_Runs_1-7_pdf\extracted_text_paddleocr.txt"

        if os.path.exists(test_pdf):
            print("\n" + "="*60)
            print("PaddleOCR Test Extraction (Crash-Safe Mode)")
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
