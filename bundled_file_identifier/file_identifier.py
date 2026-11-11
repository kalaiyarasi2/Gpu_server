"""
File Identifier
---------------
Classifies an uploaded PDF so the right extraction path can be chosen.

Output (FileType):
    digital        -> every content page has a real text layer
    semi_scanned   -> mix of digital pages and scanned (image-only) pages
    fully_scanned  -> every content page is a scanned image (needs OCR)

Install:
    pip install pymupdf

Usage:
    from file_identifier import FileIdentifier
    result = FileIdentifier().identify("upload.pdf")
    print(result.file_type.value)      # digital | semi_scanned | fully_scanned
    print(result.to_dict())            # full report incl. per-page details

CLI:
    python file_identifier.py path/to/file.pdf
    python file_identifier.py --max-pages 50 path/to/file.pdf
    python file_identifier.py --json path/to/file.pdf
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple, Union


try:
    import pymupdf as fitz  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "PyMuPDF (pymupdf) is required. Install with `pip install pymupdf`."
    ) from exc


# --------------------------------------------------------------------------- #
# Types
# --------------------------------------------------------------------------- #
class FileType(str, Enum):
    DIGITAL = "digital"
    SEMI_SCANNED = "semi_scanned"
    FULLY_SCANNED = "fully_scanned"


class PageType(str, Enum):
    DIGITAL = "digital"
    SCANNED = "scanned"
    BLANK = "blank"


@dataclass
class PageInfo:
    page_number: int  # 1-based
    page_type: PageType
    text_chars: int  # visible text characters
    image_coverage: float  # 0..1, fraction of page covered by images (unioned)
    has_invisible_text: bool  # OCR text layer hidden under a scan
    has_type3_fonts: bool = False  # Type3 bitmap/raster fonts
    drawings_count: int = 0  # vector path drawings count
    uncovered_ink_ratio: float = 0.0  # fraction of visual ink with no matching digital text
    confidence: float = 1.0  # 0..1 heuristic confidence for this classification
    reasons: List[str] = field(default_factory=list)


@dataclass
class IdentifierResult:
    file_type: FileType
    total_pages: int
    digital_pages: int
    scanned_pages: int
    blank_pages: int
    scanned_ratio: float  # scanned / (digital + scanned)
    pages: List[PageInfo] = field(default_factory=list)
    scanned_page_numbers: List[int] = field(default_factory=list)
    digital_page_numbers: List[int] = field(default_factory=list)
    pages_checked: int = 0
    max_pages_checked: Optional[int] = None
    saved_copy: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["file_type"] = self.file_type.value
        for p in d["pages"]:
            p["page_type"] = p["page_type"].value
        return d


# --------------------------------------------------------------------------- #
# Identifier
# --------------------------------------------------------------------------- #
class FileIdentifier:
    """
    Page-level rules
    ----------------
    - Scanned : a large image covers the page and there is little/no visible
                real text (often image-only or OCR-under-image).
    - Digital : enough visible, real text on the page (with embedded fonts
                treated as a strong digital signal).
    - Blank   : no text and no meaningful image coverage.

    Document-level rules (blank pages are ignored for "content" pages)
    -------------------------------------------------------------------
    - zero content pages (all blank) -> digital (no scanned pages exist)
    - no scanned pages               -> digital
    - no digital pages               -> fully_scanned
    - both                           -> semi_scanned

    Notes
    -----
    - Image coverage uses the union of image bounding boxes (overlaps merged)
      to avoid double-counting watermarks/stamps/layered figures.
    - Font presence (embedded fonts on the page) strongly biases toward
      DIGITAL when real visible text exists.
    - If max_pages_to_check is set, only a prefix is inspected; pages_checked
      and max_pages_checked are exposed in the result so callers can detect
      truncation.
    """

    def __init__(
        self,
        min_text_chars: int = 25,  # below this a page has "no real text"
        scan_image_coverage: float = 0.70,  # unioned image area / page area => scan
        blank_image_coverage: float = 0.05,
        min_drawings_for_scan: int = 50,  # vector drawings threshold when visible text is absent
        max_uncovered_ink_ratio: float = 0.80,  # dynamic ink threshold: >80% uncovered ink => scan
        max_pages_to_check: Optional[int] = None,  # cap for very large files
        treat_type3_as_scanned: bool = True,  # treat Type3 bitmap/raster fonts as scanned
        upload_dir: Optional[Union[str, Path]] = Path(__file__).resolve().parent / "upload_files",
    ):
        if min_text_chars < 0:
            raise ValueError("min_text_chars must be >= 0")
        if not (0.0 <= scan_image_coverage <= 1.0):
            raise ValueError("scan_image_coverage must be between 0.0 and 1.0")
        if not (0.0 <= blank_image_coverage <= 1.0):
            raise ValueError("blank_image_coverage must be between 0.0 and 1.0")
        if not (0.0 <= max_uncovered_ink_ratio <= 1.0):
            raise ValueError("max_uncovered_ink_ratio must be between 0.0 and 1.0")
        if max_pages_to_check is not None and max_pages_to_check < 1:
            raise ValueError("max_pages_to_check must be >= 1 if provided")

        self.min_text_chars = min_text_chars
        self.scan_image_coverage = scan_image_coverage
        self.blank_image_coverage = blank_image_coverage
        self.min_drawings_for_scan = min_drawings_for_scan
        self.max_uncovered_ink_ratio = max_uncovered_ink_ratio
        self.max_pages_to_check = max_pages_to_check
        self.treat_type3_as_scanned = treat_type3_as_scanned
        self.upload_dir = Path(upload_dir) if upload_dir is not None else None

    # ------------------------------------------------------------------ #
    def identify(self, pdf_path: Union[str, Path]) -> IdentifierResult:
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"File not found: {pdf_path}")
        if pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"Only PDF files are supported: {pdf_path}")

        saved_copy_path: Optional[str] = None
        if self.upload_dir is not None:
            try:
                dest_dir = self.upload_dir.resolve()
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_file = dest_dir / pdf_path.name
                if pdf_path != dest_file:
                    shutil.copy2(pdf_path, dest_file)
                saved_copy_path = str(dest_file)
            except Exception:
                saved_copy_path = None

        try:
            doc = fitz.open(pdf_path)
        except (fitz.FileDataError, fitz.EmptyFileError, RuntimeError) as exc:
            raise ValueError(f"Cannot open PDF (corrupt or unreadable): {exc}") from exc
        except Exception as exc:
            raise ValueError(f"Cannot open PDF: {exc}") from exc

        with doc:
            try:
                if getattr(doc, "needs_pass", False) or getattr(doc, "is_encrypted", False):
                    raise ValueError("PDF is password protected")
            except Exception:
                raise ValueError("PDF is password protected")

            total = doc.page_count
            if total < 0:
                total = 0

            limit = total
            if self.max_pages_to_check is not None:
                limit = min(total, self.max_pages_to_check)

            pages: List[PageInfo] = []
            for i in range(limit):
                try:
                    page = doc[i]
                except Exception as exc:
                    pages.append(
                        PageInfo(
                            page_number=i + 1,
                            page_type=PageType.BLANK,
                            text_chars=0,
                            image_coverage=0.0,
                            has_invisible_text=False,
                            confidence=0.0,
                            reasons=[f"unreadable_page: {exc}"],
                        )
                    )
                    continue
                pages.append(self._analyse_page(page, i + 1))

        return self._build_result(pages, total, limit, saved_copy=saved_copy_path)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _calculate_uncovered_ink_ratio(page: "fitz.Page") -> Tuple[float, int, int]:
        """
        Dynamically calculates the fraction of visible ink pixels that are NOT covered
        by any digital text bounding box.

        Returns:
            (uncovered_ratio, total_ink_pixels, uncovered_ink_pixels)
        """
        try:
            try:
                import numpy as np
                has_np = True
            except ImportError:
                has_np = False

            pix = page.get_pixmap(dpi=72, colorspace=fitz.csGRAY)
            w, h = pix.width, pix.height

            if has_np:
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(h, w)
                ink_mask = arr < 235
                total_ink = int(np.sum(ink_mask))
                if total_ink < 400:
                    return 0.0, total_ink, 0

                text_blocks = page.get_text("blocks")
                scale_x = w / page.rect.width if page.rect.width > 0 else 1.0
                scale_y = h / page.rect.height if page.rect.height > 0 else 1.0
                text_covered_mask = np.zeros_like(ink_mask, dtype=bool)

                for b in text_blocks:
                    if len(b) > 4 and b[4] and b[4].strip():
                        bx0 = max(0, int(b[0] * scale_x))
                        by0 = max(0, int(b[1] * scale_y))
                        bx1 = min(w, int(b[2] * scale_x) + 1)
                        by1 = min(h, int(b[3] * scale_y) + 1)
                        text_covered_mask[by0:by1, bx0:bx1] = True

                uncovered_ink = int(np.sum(ink_mask & (~text_covered_mask)))
                ratio = uncovered_ink / total_ink if total_ink > 0 else 0.0
                return float(ratio), total_ink, uncovered_ink

            # Pure Python fallback
            samples = bytearray(pix.samples)
            total_ink = 0
            ink_indices = []
            for idx, val in enumerate(samples):
                if val < 235:
                    total_ink += 1
                    ink_indices.append(idx)

            if total_ink < 400:
                return 0.0, total_ink, 0

            text_blocks = page.get_text("blocks")
            scale_x = w / page.rect.width if page.rect.width > 0 else 1.0
            scale_y = h / page.rect.height if page.rect.height > 0 else 1.0

            covered_pixels = bytearray(w * h)
            for b in text_blocks:
                if len(b) > 4 and b[4] and b[4].strip():
                    bx0 = max(0, int(b[0] * scale_x))
                    by0 = max(0, int(b[1] * scale_y))
                    bx1 = min(w, int(b[2] * scale_x) + 1)
                    by1 = min(h, int(b[3] * scale_y) + 1)
                    for y in range(by0, by1):
                        row_start = y * w
                        for x in range(bx0, bx1):
                            covered_pixels[row_start + x] = 1

            uncovered_ink = sum(1 for idx in ink_indices if not covered_pixels[idx])
            ratio = uncovered_ink / total_ink if total_ink > 0 else 0.0
            return float(ratio), total_ink, uncovered_ink
        except Exception:
            return 0.0, 0, 0

    # ------------------------------------------------------------------ #
    def _analyse_page(self, page: "fitz.Page", number: int) -> PageInfo:
        page_rect = page.rect
        page_area = max(page_rect.width * page_rect.height, 1.0)

        coverage = self._image_coverage_union(page, page_area)
        visible, invisible = self._count_text(page)
        has_fonts, has_only_type3, type3_ratio = self._font_analysis(page)
        has_invisible = invisible >= self.min_text_chars

        try:
            drawings_count = len(page.get_drawings())
        except Exception:
            drawings_count = 0

        uncovered_ratio, total_ink, uncovered_ink = self._calculate_uncovered_ink_ratio(page)

        ptype = PageType.BLANK
        confidence = 0.5
        reasons: List[str] = []

        reasons.append(
            f"visible={visible}, invisible={invisible}, img_cov={coverage:.3f}, fonts={has_fonts}, ink_tot={total_ink}, unc_ink={uncovered_ratio:.2f}"
        )

        is_type3_scanned = (
            self.treat_type3_as_scanned
            and visible >= self.min_text_chars
            and (has_only_type3 or type3_ratio >= 0.50)
        )

        if is_type3_scanned:
            ptype = PageType.SCANNED
            confidence = 0.90
            reasons.append("predominantly Type3 bitmap fonts (raster print-stream) -> scanned")
        elif total_ink < 400 and visible == 0 and not has_invisible:
            ptype = PageType.BLANK
            confidence = 0.98
            reasons.append("virtually no visual ink and no text -> blank")
        elif coverage >= self.scan_image_coverage or has_invisible:
            ptype = PageType.SCANNED
            confidence = 0.9 if has_invisible else 0.85
            if has_invisible:
                reasons.append("image-heavy or invisible_ocr_layer -> scanned")
            else:
                reasons.append("high raster image coverage -> scanned")
        elif total_ink >= 400 and uncovered_ratio >= self.max_uncovered_ink_ratio:
            # DYNAMIC INK COVERAGE: Over 80% of visible ink has no matching digital text layer
            ptype = PageType.SCANNED
            confidence = 0.95
            reasons.append(
                f"dynamic ink analysis: {uncovered_ratio*100:.1f}% of visible ink has no digital text -> scanned"
            )
        elif visible >= self.min_text_chars and has_fonts:
            ptype = PageType.DIGITAL
            confidence = 0.95
            reasons.append(f"has_fonts + text covers visual content (uncovered ink: {uncovered_ratio*100:.1f}%) -> digital")
        elif visible >= self.min_text_chars:
            ptype = PageType.DIGITAL
            confidence = 0.85
            reasons.append(f"sufficient visible text (uncovered ink: {uncovered_ratio*100:.1f}%) -> digital")
        else:
            ptype = PageType.BLANK
            confidence = 0.80
            reasons.append("low coverage and minimal text -> blank")

        if confidence < 0.0:
            confidence = 0.0
        if confidence > 1.0:
            confidence = 1.0

        return PageInfo(
            page_number=number,
            page_type=ptype,
            text_chars=visible,
            image_coverage=round(coverage, 3),
            has_invisible_text=has_invisible,
            has_type3_fonts=(has_only_type3 or type3_ratio > 0.0),
            drawings_count=drawings_count,
            uncovered_ink_ratio=round(uncovered_ratio, 3),
            confidence=round(confidence, 3),
            reasons=reasons,
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _merge_rects(rects: List[fitz.Rect]) -> List[fitz.Rect]:
        if len(rects) <= 1:
            return list(rects)

        boxes = [(r.x0, r.y0, r.x1, r.y1) for r in rects]
        boxes.sort(key=lambda b: (b[0], b[1]))

        merged: List[List[float]] = [list(boxes[0])]
        for current in boxes[1:]:
            last = merged[-1]
            if current[0] <= last[2] + 1e-6 and current[1] <= last[3] + 1e-6:
                last[2] = max(last[2], current[2])
                last[3] = max(last[3], current[3])
            else:
                merged.append(list(current))

        return [fitz.Rect(m[0], m[1], m[2], m[3]) for m in merged]

    @staticmethod
    def _image_coverage_union(page: "fitz.Page", page_area: float) -> float:
        try:
            infos = page.get_image_info(xrefs=True)
        except Exception:
            try:
                infos = page.get_image_info()
            except Exception:
                return 0.0

        rects: List[fitz.Rect] = []
        page_rect = page.rect
        for info in infos:
            bbox = info.get("bbox")
            if not bbox:
                continue
            try:
                r = fitz.Rect(bbox) & page_rect
            except Exception:
                continue
            if not r.is_empty and r.width > 0 and r.height > 0:
                rects.append(r)

        if not rects:
            return 0.0

        merged = FileIdentifier._merge_rects(rects)
        covered = 0.0
        for r in merged:
            covered += r.width * r.height

        cov = covered / page_area if page_area > 0 else 0.0
        if cov < 0.0:
            cov = 0.0
        if cov > 1.0:
            cov = 1.0
        return cov

    @staticmethod
    def _font_analysis(page: "fitz.Page") -> Tuple[bool, bool, float]:
        """
        Analyze fonts on the page.
        Returns:
            (has_fonts, has_only_type3, type3_ratio)
            - has_fonts: bool, whether any fonts are detected.
            - has_only_type3: bool, whether all detected fonts are Type3 bitmap fonts.
            - type3_ratio: float (0.0 to 1.0), fraction of text characters rendered with Type3 fonts.
        """
        try:
            fonts = page.get_fonts(full=False)
        except Exception:
            fonts = []

        font_type_by_name = {}
        font_types = set()
        for f in fonts:
            ftype = f[2].strip().lower() if len(f) > 2 and f[2] else ""
            fname = f[4].strip() if len(f) > 4 and f[4] else (f[3].strip() if len(f) > 3 else "")
            if ftype:
                font_types.add(ftype)
            if fname and ftype:
                font_type_by_name[fname] = ftype
                if len(f) > 3 and f[3]:
                    font_type_by_name[f[3].strip()] = ftype

        type3_chars = 0
        total_text_chars = 0
        has_any_spans = False

        try:
            td = page.get_text("dict")
            blocks = td.get("blocks", []) if isinstance(td, dict) else []
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                for l in b.get("lines", []):
                    if not isinstance(l, dict):
                        continue
                    for s in l.get("spans", []):
                        if not isinstance(s, dict):
                            continue
                        has_any_spans = True
                        text_val = s.get("text", "")
                        char_count = sum(1 for c in text_val if not c.isspace())
                        if char_count == 0:
                            continue
                        total_text_chars += char_count
                        fn = s.get("font") or s.get("fontname") or ""
                        ftype = font_type_by_name.get(fn, "")
                        if ftype == "type3":
                            type3_chars += char_count
        except Exception:
            pass

        has_fonts = bool(font_types) or has_any_spans
        has_only_type3 = bool(font_types) and all(ft == "type3" for ft in font_types)

        if total_text_chars > 0:
            type3_ratio = type3_chars / total_text_chars
        elif has_only_type3:
            type3_ratio = 1.0
        else:
            type3_ratio = 0.0

        return has_fonts, has_only_type3, type3_ratio

    @staticmethod
    def _has_fonts(page: "fitz.Page") -> bool:
        has_fonts, _, _ = FileIdentifier._font_analysis(page)
        return has_fonts

    @staticmethod
    def _count_text(page: "fitz.Page") -> Tuple[int, int]:
        visible = 0
        invisible = 0

        try:
            trace = page.get_texttrace()
        except Exception:
            trace = []

        if trace:
            for span in trace:
                if not isinstance(span, dict):
                    continue
                chars_list = span.get("chars") or []
                n = 0
                for ch in chars_list:
                    try:
                        code = ch[0] if ch else 0
                    except Exception:
                        code = 0
                    try:
                        c = chr(code)
                    except Exception:
                        c = ""
                    if c and not c.isspace():
                        n += 1
                if n == 0:
                    continue
                span_type = span.get("type")
                opacity = span.get("opacity", 1.0)
                is_invisible = False
                if span_type == 3:
                    is_invisible = True
                try:
                    if opacity is not None and float(opacity) <= 0.01:
                        is_invisible = True
                except Exception:
                    pass
                if is_invisible:
                    invisible += n
                else:
                    visible += n
            return visible, invisible

        try:
            t = page.get_text("text") or ""
        except Exception:
            t = ""
        visible_fallback = 0
        for c in t:
            if not c.isspace():
                visible_fallback += 1
        return visible_fallback, 0

    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_result(
        pages: List[PageInfo],
        total_pages: int,
        pages_checked: int,
        saved_copy: Optional[str] = None,
    ) -> IdentifierResult:
        digital_list = [p.page_number for p in pages if p.page_type == PageType.DIGITAL]
        scanned_list = [p.page_number for p in pages if p.page_type == PageType.SCANNED]
        blank_list = [p for p in pages if p.page_type == PageType.BLANK]

        digital_count = len(digital_list)
        scanned_count = len(scanned_list)
        blank_count = len(blank_list)
        content_count = digital_count + scanned_count

        if content_count == 0:
            ftype = FileType.DIGITAL
            scanned_ratio_val = 0.0
        elif scanned_count == 0:
            ftype = FileType.DIGITAL
            scanned_ratio_val = 0.0
        elif digital_count == 0:
            ftype = FileType.FULLY_SCANNED
            scanned_ratio_val = 1.0
        else:
            ftype = FileType.SEMI_SCANNED
            scanned_ratio_val = round(scanned_count / content_count, 3)

        max_pages_checked_val: Optional[int] = None
        if pages_checked < total_pages:
            max_pages_checked_val = pages_checked

        return IdentifierResult(
            file_type=ftype,
            total_pages=total_pages,
            digital_pages=digital_count,
            scanned_pages=scanned_count,
            blank_pages=blank_count,
            scanned_ratio=scanned_ratio_val,
            pages=pages,
            scanned_page_numbers=scanned_list,
            digital_page_numbers=digital_list,
            pages_checked=pages_checked,
            max_pages_checked=max_pages_checked_val,
            saved_copy=saved_copy,
        )


# --------------------------------------------------------------------------- #
# Hook for your extraction engine
# --------------------------------------------------------------------------- #
def route_to_engine(pdf_path: str) -> IdentifierResult:
    result = FileIdentifier().identify(pdf_path)

    if result.file_type == FileType.DIGITAL:
        pass  # -> direct text/table extraction, no OCR
    elif result.file_type == FileType.SEMI_SCANNED:
        pass  # -> digital extraction for result.digital_page_numbers
        #    OCR only for result.scanned_page_numbers
    else:  # FULLY_SCANNED
        pass  # -> OCR every page

    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Classify a PDF (or all PDFs in a directory) as digital/semi_scanned/fully_scanned"
    )
    p.add_argument("path", help="Path to PDF file or directory containing PDF files")
    p.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Only check first N pages (prefix sampling)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output JSON (default is pretty-printed JSON)",
    )
    p.add_argument(
        "--no-type3-as-scanned",
        dest="treat_type3_as_scanned",
        action="store_false",
        default=True,
        help="Do not treat Type3 bitmap/raster fonts as scanned",
    )
    p.add_argument(
        "--no-save",
        dest="save_to_upload",
        action="store_false",
        default=True,
        help="Do not copy tested files to upload_files directory",
    )
    return p.parse_args(argv[1:])


def main(argv: List[str] | None = None) -> int:
    try:
        args = _parse_args(sys.argv if argv is None else argv)
        upload_dir = (Path(__file__).resolve().parent / "upload_files") if args.save_to_upload else None
        fid = FileIdentifier(
            max_pages_to_check=args.max_pages,
            treat_type3_as_scanned=args.treat_type3_as_scanned,
            upload_dir=upload_dir,
        )
        target = Path(args.path)
        if not target.exists():
            raise FileNotFoundError(f"Path not found: {target}")

        if target.is_dir():
            pdf_files = sorted(set(list(target.glob("*.pdf")) + list(target.glob("*.PDF"))))
            if not pdf_files:
                print(f"No PDF files found in directory: {target}", file=sys.stderr)
                return 1
            results = {}
            for pdf_file in pdf_files:
                try:
                    results[pdf_file.name] = fid.identify(pdf_file).to_dict()
                except Exception as exc:
                    results[pdf_file.name] = {"error": str(exc)}
            out = json.dumps(results, indent=2, ensure_ascii=False)
            print(out)
            return 0

        res = fid.identify(target)
        out = json.dumps(res.to_dict(), indent=2, ensure_ascii=False)
        print(out)
        return 0
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 3
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
