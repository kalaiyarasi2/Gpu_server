from pdf2image import convert_from_path
from PIL import Image
import pytesseract
import cv2
import numpy as np
import os
import shutil
from pypdf import PdfReader, PdfWriter


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

    Args:
        pdf_path:           Input PDF.
        work_dir:           Scratch folder for intermediate images.
        output_pdf:         Final output PDF path.
        dpi:                Render DPI for PDF → image conversion.
        osd_conf_threshold: Preferred Tesseract OSD confidence (for reporting/tuning).
        osd_min_conf:       Minimum OSD confidence floor used by dynamic logic.
        skew_threshold:     Minimum skew angle (°) to apply fine correction.
        max_correction_attempts:
                            Max detect-rotate-validate cycles per page.
        reprocess_failed_pages:
                            If True, do one final safe reprocess for pages
                            that still fail after normal attempts.

    Returns:
        Path to corrected PDF, plus a per-page report list.
    """
    raw_dir       = os.path.join(work_dir, 'raw')
    corrected_dir = os.path.join(work_dir, 'corrected')
    os.makedirs(corrected_dir, exist_ok=True)

    # 1. PDF → images
    raw_paths = pdf_to_images(pdf_path, output_dir=raw_dir, dpi=dpi)

    corrected_paths = []
    page_reports    = []

    for raw_path in raw_paths:
        page_name      = os.path.basename(raw_path)
        corrected_path = os.path.join(corrected_dir, page_name)

        print(f"\n-- Page: {page_name} ----------------------")

        attempts_used = 0
        passed = False
        report = {}

        # 2-4. Detect -> Rotate -> Validate (dynamic attempts)
        for attempt in range(1, max_correction_attempts + 1):
            attempts_used = attempt
            print(f"[2] Attempt {attempt}/{max_correction_attempts}")

            # Always detect from the original raw image to avoid compounding
            # rotation artifacts across retries.
            osd_angle, osd_conf = detect_rotation(raw_path)
            skew_angle = detect_skew(raw_path)
            print(f"[2] OSD={osd_angle} deg (conf={osd_conf:.1f}) | skew={skew_angle:.2f} deg")

            # On retries, apply a gentler skew angle so unstable pages do not
            # get progressively over-rotated.
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

            # Nothing actionable from latest validation; avoid pointless repeats.
            if report['osd_angle'] == 0 and abs(report['skew_angle']) < skew_threshold:
                print("[4] Validation failed but no actionable correction left; stopping retries.")
                break

            print(f"[4] Retrying correction for {page_name}...")

        if not passed and reprocess_failed_pages:
            print(f"[4] Reprocessing {page_name} with safe fallback (raw page).")
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

        report['page'] = page_name
        report['attempts'] = attempts_used
        report['retried'] = attempts_used > 1
        page_reports.append(report)
        corrected_paths.append(corrected_path)

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

    for idx, (raw_path, page) in enumerate(zip(raw_paths, reader.pages), start=1):
        page_name = os.path.basename(raw_path)
        print(f"\n-- Page: {page_name} ----------------------")

        osd_angle, osd_conf = detect_rotation(raw_path)
        skew_angle = detect_skew(raw_path)
        rotate_angle = osd_angle if (osd_angle != 0 and osd_conf >= osd_min_conf) else 0

        print(
            f"[2] OSD={osd_angle} deg (conf={osd_conf:.1f}) | "
            f"skew={skew_angle:.2f} deg | apply_rotate={rotate_angle} deg"
        )

        if rotate_angle:
            page.rotate(rotate_angle)

        writer.add_page(page)
        page_reports.append({
            'page': page_name,
            'page_index': idx,
            'osd_angle': osd_angle,
            'osd_conf': round(osd_conf, 2),
            'skew_angle': round(skew_angle, 2),
            'applied_rotate': rotate_angle,
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
