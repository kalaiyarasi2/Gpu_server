from pdf2image import convert_from_path
from PIL import Image
import pytesseract
import cv2
import numpy as np
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pypdf import PdfReader, PdfWriter
from dotenv import load_dotenv
from gpu_config import gpu_manager, gpu_concurrency_config

# Load environment variables
load_dotenv()

# Configure Tesseract path if provided in environment
TESSERACT_PATH = os.getenv("TESSERACT_PATH")
if TESSERACT_PATH:
    if os.path.isdir(TESSERACT_PATH):
        tess_exe = os.path.join(TESSERACT_PATH, "tesseract.exe")
        if os.path.exists(tess_exe):
            pytesseract.pytesseract.tesseract_cmd = tess_exe
    elif os.path.exists(TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


# ── 1. PDF → IMAGES ────────────────────────────────────────────────────────────

def pdf_to_images(pdf_path, output_dir='pipeline/raw', dpi=300):
    """Convert each PDF page to a JPEG image."""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    os.makedirs(output_dir, exist_ok=True)
    images = convert_from_path(pdf_path, dpi=dpi)

    saved_paths = []
    for i, image in enumerate(images):
        output_path = os.path.join(output_dir, f'page_{i+1:03d}.jpg')
        image.save(output_path, 'JPEG')
        saved_paths.append(output_path)
        print(f"[1] Saved raw page: {output_path}")

    return saved_paths


# ── 2. ROTATION DETECTION ──────────────────────────────────────────────────────

def detect_rotation(image_path):
    """
    Detect required rotation using Tesseract OSD.
    Returns (angle, confidence) where angle is degrees to rotate (0/90/180/270).
    """
    with Image.open(image_path) as img:
        try:
            osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
            return int(osd['rotate']), float(osd['orientation_conf'])
        except pytesseract.TesseractError as e:
            print(f"[2] OSD failed for {image_path}: {e}")
            return 0, 0.0


def detect_skew(image_path):
    """
    Detect fine-grained skew angle using OpenCV contour analysis.
    Returns angle in degrees (typically -45° to 45°).
    """
    img = cv2.imread(image_path)
    if img is None:
        return 0.0

    gray  = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur  = cv2.GaussianBlur(gray, (9, 9), 0)
    thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 5))
    dilate = cv2.dilate(thresh, kernel, iterations=5)

    contours, _ = cv2.findContours(dilate, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0

    largest = max(contours, key=cv2.contourArea)
    angle = cv2.minAreaRect(largest)[-1]

    # Normalize OpenCV rectangle angle to a deskew range around 0.
    # Fine skew correction should stay in roughly [-45, 45];
    # near-90 readings are orientation artifacts, not true skew.
    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90

    return -angle  # Positive = clockwise correction needed


# ── 3. ROTATE ──────────────────────────────────────────────────────────────────

def rotate_image(image_path, output_path,
                 osd_angle=0, osd_conf=0.0, skew_angle=0.0,
                 osd_conf_threshold=20.0, skew_threshold=0.5,
                 osd_min_conf=8.0):
    """
    Apply coarse OSD rotation then fine skew correction.

    Args:
        osd_angle:          Coarse rotation in degrees (0/90/180/270).
        osd_conf:           Tesseract confidence for OSD.
        skew_angle:         Fine skew angle from contour analysis.
        osd_conf_threshold: Preferred OSD confidence to trust coarse rotation.
        skew_threshold:     Minimum skew angle (°) to bother correcting.
        osd_min_conf:       Minimum confidence floor to allow OSD correction.

    Returns:
        dict with keys: osd_applied, skew_applied, final_angle
    """
    with Image.open(image_path) as img:
        result = {'osd_applied': False, 'skew_applied': False, 'final_angle': 0.0}

        # Step A: coarse OSD correction (90° increments)
        if osd_angle != 0 and osd_conf >= osd_min_conf:
            img = img.rotate(-osd_angle, expand=True)
            result['osd_applied'] = True
            result['final_angle'] += osd_angle
            print(
                f"[3] OSD rotation applied: {osd_angle} deg "
                f"(conf={osd_conf:.1f}, floor={osd_min_conf:.1f})"
            )

        corrected_pil = img.copy()  # detach image data before file handle closes

    # Step B: fine skew correction via OpenCV (works on the OSD-corrected image)
    if abs(skew_angle) >= skew_threshold:
        # Save intermediate for OpenCV to read
        corrected_pil.save(output_path, 'JPEG')

        cv_img = cv2.imread(output_path)
        (h, w)  = cv_img.shape[:2]
        M       = cv2.getRotationMatrix2D((w // 2, h // 2), skew_angle, 1.0)
        rotated = cv2.warpAffine(cv_img, M, (w, h),
                                 flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
        cv2.imwrite(output_path, rotated)
        result['skew_applied'] = True
        result['final_angle'] += skew_angle
        print(f"[3] Skew correction applied: {skew_angle:.2f} deg")
    else:
        corrected_pil.save(output_path, 'JPEG')

    if not result['osd_applied'] and not result['skew_applied']:
        print(f"[3] No correction needed.")

    return result


# ── 4. VALIDATION ──────────────────────────────────────────────────────────────

def validate_rotation(image_path, skew_threshold=0.5, osd_conf_threshold=20.0, osd_min_conf=8.0):
    """
    Re-run detection on the corrected image to confirm alignment.

    Returns:
        (passed: bool, report: dict)
    """
    osd_angle, osd_conf = detect_rotation(image_path)
    skew_angle          = detect_skew(image_path)

    osd_ok = (osd_angle == 0) or (osd_conf < osd_min_conf)

    # Adaptive skew tolerance:
    # once orientation is upright with usable confidence, allow mild residual skew.
    effective_skew_threshold = skew_threshold
    if osd_angle == 0 and osd_conf >= osd_min_conf:
        effective_skew_threshold = max(skew_threshold, 2.0)

    skew_ok = abs(skew_angle) < effective_skew_threshold
    passed = osd_ok and skew_ok

    report = {
        'passed':    passed,
        'osd_angle': osd_angle,
        'osd_conf':  osd_conf,
        'skew_angle': round(skew_angle, 2),
    }
    status = "PASS" if passed else "FAIL"
    print(
        f"[4] Validation {status} | OSD={osd_angle} deg (conf={osd_conf:.1f}) "
        f"| skew={skew_angle:.2f} deg (limit={effective_skew_threshold:.2f})"
    )
    return passed, report


# ── 5. IMAGES → PDF ────────────────────────────────────────────────────────────

def images_to_pdf(image_paths, output_pdf='output.pdf', resize_to=None, pdf_resolution=300.0):
    """Combine corrected images into a single multi-page PDF."""
    images = []
    for path in image_paths:
        if not os.path.exists(path):
            print(f"[5] Skipped missing: {path}")
            continue
        img = Image.open(path).convert('RGB')
        if resize_to:
            img = img.resize(resize_to, Image.Resampling.LANCZOS)
        images.append(img)

    if not images:
        print("[5] No valid images — PDF not created.")
        return None

    images[0].save(
        output_pdf,
        save_all=True,
        append_images=images[1:],
        quality=95,
        # Keep PDF page dimensions aligned with render DPI.
        # Using 150 here doubles page boxes (e.g., 1584x1224 instead of 792x612).
        resolution=pdf_resolution
    )
    print(f"[5] PDF saved: {output_pdf} ({len(images)} pages)")
    return output_pdf


# ── FULL PIPELINE ──────────────────────────────────────────────────────────────

def run_pipeline(pdf_path,
                 work_dir='pipeline',
                 output_pdf='corrected.pdf',
                 dpi=300,
                 osd_conf_threshold=20.0,
                 osd_min_conf=8.0,
                 skew_threshold=0.5,
                 max_correction_attempts=3,
                 reprocess_failed_pages=True):
    """
    Full pipeline:
      PDF → raw images → detect rotation → rotate → validate → PDF
    """
    raw_dir       = os.path.join(work_dir, 'raw')
    corrected_dir = os.path.join(work_dir, 'corrected')
    os.makedirs(corrected_dir, exist_ok=True)

    # 1. PDF → images
    raw_paths = pdf_to_images(pdf_path, output_dir=raw_dir, dpi=dpi)

    def process_page_full(raw_path):
        """Worker function for parallel full page rotation/deskew pipeline."""
        page_name      = os.path.basename(raw_path)
        corrected_path = os.path.join(corrected_dir, page_name)
        
        attempts_used = 0
        passed = False
        report = {}

        # 2-4. Detect -> Rotate -> Validate (dynamic attempts)
        for attempt in range(1, max_correction_attempts + 1):
            attempts_used = attempt
            osd_angle, osd_conf = detect_rotation(raw_path)
            skew_angle = detect_skew(raw_path)

            # On retries, apply a gentler skew angle
            skew_scale = max(0.0, 1.0 - (attempt - 1) * 0.35)
            applied_skew = skew_angle * skew_scale

            rotate_image(
                raw_path, corrected_path,
                osd_angle=osd_angle, osd_conf=osd_conf,
                skew_angle=applied_skew,
                osd_conf_threshold=osd_conf_threshold,
                skew_threshold=skew_threshold,
                osd_min_conf=osd_min_conf
            )

            passed, report = validate_rotation(
                corrected_path,
                skew_threshold=skew_threshold,
                osd_conf_threshold=osd_conf_threshold,
                osd_min_conf=osd_min_conf
            )
            if passed:
                break

            if report['osd_angle'] == 0 and abs(report['skew_angle']) < skew_threshold:
                break

        if not passed and reprocess_failed_pages:
            shutil.copy2(raw_path, corrected_path)
            passed, report = validate_rotation(
                corrected_path,
                skew_threshold=skew_threshold,
                osd_conf_threshold=osd_conf_threshold,
                osd_min_conf=osd_min_conf
            )
            report['reprocessed'] = True
        else:
            report['reprocessed'] = False

        report.update({
            'page': page_name,
            'corrected_path': corrected_path,
            'attempts': attempts_used,
            'retried': attempts_used > 1
        })
        return report

    # Parallel processing
    max_workers = gpu_concurrency_config.get('pdf_rendering', {}).get('max_workers', 8)
    print(f"\n🚀 Launching Parallel OCR Pipeline ({len(raw_paths)} pages, {max_workers} workers)...")
    
    page_reports = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_page_full, path): path for path in raw_paths}
        for future in as_completed(futures):
            report = future.result()
            page_reports.append(report)
            print(f"   ✓ Processed: {report['page']} | Attempts: {report['attempts']} | Pass: {report['passed']}")

    # Maintain order
    page_reports.sort(key=lambda x: x['page'])
    corrected_paths = [r['corrected_path'] for r in page_reports]

    # 5. Images → PDF
    print("\n-- Building output PDF --------------------")
    result_pdf = images_to_pdf(
        corrected_paths,
        output_pdf=output_pdf,
        pdf_resolution=float(dpi)
    )

    # Summary
    print("\n-- Pipeline Summary -----------------------")
    for r in page_reports:
        flag = "! " if r['retried'] else "+ "
        print(f"  {flag}{r['page']} | OSD={r['osd_angle']} deg "
              f"skew={r['skew_angle']} deg | {'PASS' if r['passed'] else 'FAIL'}")

    return result_pdf, page_reports


def run_pipeline_preserve_layout(pdf_path,
                                 work_dir='pipeline',
                                 output_pdf='corrected.pdf',
                                 dpi=200,
                                 osd_min_conf=8.0):
    """
    Detect page orientation from rendered images, but rotate original PDF pages.
    This preserves the original page geometry/layout and avoids image-rebuild sizing artifacts.
    """
    raw_dir = os.path.join(work_dir, 'raw')
    raw_paths = pdf_to_images(pdf_path, output_dir=raw_dir, dpi=dpi)

    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    page_reports = []

    def process_page_rotation(idx, raw_path):
        """Worker function for parallel rotation detection."""
        page_name = os.path.basename(raw_path)
        osd_angle, osd_conf = detect_rotation(raw_path)
        skew_angle = detect_skew(raw_path)
        rotate_angle = osd_angle if (osd_angle != 0 and osd_conf >= osd_min_conf) else 0
        
        return {
            'page_index': idx,
            'page_name': page_name,
            'osd_angle': osd_angle,
            'osd_conf': round(osd_conf, 2),
            'skew_angle': round(skew_angle, 2),
            'applied_rotate': rotate_angle,
            'passed': True
        }

    # Use ThreadPoolExecutor for parallel OCR orientation detection
    max_workers = gpu_concurrency_config.get('pdf_rendering', {}).get('max_workers', 8)
    print(f"\n🚀 Launching Parallel Orientation Detection ({len(raw_paths)} pages, {max_workers} workers)...")
    
    unordered_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_page_rotation, i, path) for i, path in enumerate(raw_paths, 1)]
        for future in as_completed(futures):
            res = future.result()
            unordered_results.append(res)
            print(f"   ✓ Detected: {res['page_name']} | rotate={res['applied_rotate']} deg")

    # Re-sort to maintain PDF page order
    unordered_results.sort(key=lambda x: x['page_index'])

    for r in unordered_results:
        # pypdf pages are 0-indexed
        page = reader.pages[r['page_index'] - 1]
        if r['applied_rotate']:
            page.rotate(r['applied_rotate'])

        writer.add_page(page)
        page_reports.append({
            'page': r['page_name'],
            'page_index': r['page_index'],
            'osd_angle': r['osd_angle'],
            'osd_conf': r['osd_conf'],
            'skew_angle': r['skew_angle'],
            'applied_rotate': r['applied_rotate'],
            'passed': True
        })

    with open(output_pdf, 'wb') as f:
        writer.write(f)

    print(f"\n[5] PDF saved: {output_pdf} ({len(page_reports)} pages)")
    print("\n-- Pipeline Summary -----------------------")
    for r in page_reports:
        print(
            f"  + {r['page']} | OSD={r['osd_angle']} deg "
            f"(conf={r['osd_conf']}) | rotate={r['applied_rotate']} deg"
        )

    return output_pdf, page_reports


# ── Usage ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    pdf, reports = run_pipeline_preserve_layout(
        pdf_path='old/26-27 WC - Loss_runs_3.pdf',
        output_pdf='corrected.pdf',
        dpi=200,
        osd_min_conf=0.3
    )
