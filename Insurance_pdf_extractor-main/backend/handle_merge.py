from __future__ import annotations

"""
Utilities for handling merged PDFs that contain multiple invoices/documents.

The core idea:
- Detect page ranges that correspond to individual invoices based on header patterns.
- Split the original PDF into per-invoice sub-PDFs.
- Let the existing extraction pipeline process each sub-PDF independently.

This module is intentionally self-contained so it can be imported from
CLI scripts, web handlers, or batch jobs.
"""

from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

try:
    from PyPDF2 import PdfReader, PdfWriter  # type: ignore[import]
    _HAS_PYPDF2 = True
except Exception:
    PdfReader = None  # type: ignore[assignment]
    PdfWriter = None  # type: ignore[assignment]
    _HAS_PYPDF2 = False

try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except Exception:
    fitz = None  # type: ignore[assignment]
    _HAS_FITZ = False


# Default is intentionally empty so this module stays generic.
# Callers (Insurance, Work Comp, Invoice, etc.) should pass their own patterns.
DEFAULT_HEADER_PATTERNS: Sequence[str] = ()


def _page_contains_any(text: str, patterns: Iterable[str]) -> bool:
    upper = text.upper()
    return any(p.upper() in upper for p in patterns)


def find_invoice_page_ranges_from_text_pages(
    page_texts: Sequence[str],
    header_patterns: Optional[Sequence[str]] = None,
) -> List[Tuple[int, int]]:
    """
    Given a list of page-level text strings, return (start_page, end_page) 0-based
    ranges for each detected invoice.

    Heuristic:
    - A page whose text contains any header pattern is treated as the FIRST page
      of a new invoice.
    - The invoice runs until the page before the next header (or the last page).
    - If no headers are found, the whole document is treated as a single range.
    """
    boundaries: List[int] = []
    patterns = list(header_patterns or DEFAULT_HEADER_PATTERNS)

    prev_matches: set[str] = set()
    for i, text in enumerate(page_texts):
        if not text or not patterns:
            prev_matches = set()
            continue

        upper = text.upper()
        matches = {p for p in patterns if p.upper() in upper}

        # Boundary heuristic:
        # - Start a new section when patterns appear on this page and:
        #   - it's the first page, OR
        #   - the previous page had no matches, OR
        #   - the current page's matches are DISJOINT from the previous page's matches
        #     (e.g. vendor name changes between invoices in a merged PDF).
        if matches and (i == 0 or not prev_matches or matches.isdisjoint(prev_matches)):
            boundaries.append(i)

        prev_matches = matches

    if not boundaries:
        # Single range spanning entire document
        return [(0, max(0, len(page_texts) - 1))]

    ranges: List[Tuple[int, int]] = []
    num_pages = len(page_texts)

    for idx, start in enumerate(boundaries):
        if idx + 1 < len(boundaries):
            end = boundaries[idx + 1] - 1
        else:
            end = num_pages - 1
        ranges.append((start, max(start, end)))

    return ranges


def split_pdf_by_page_ranges(
    pdf_path: str | Path,
    ranges: Sequence[Tuple[int, int]],
    output_dir: str | Path,
) -> List[Path]:
    """
    Physically split a PDF into one sub-PDF per page range.

    Args:
        pdf_path: Source merged PDF.
        ranges: Iterable of (start_page, end_page) inclusive, 0-based.
        output_dir: Directory where sub-PDFs will be written.

    Returns:
        List of Paths to the created sub-PDF files, one per range.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs: List[Path] = []

    if _HAS_PYPDF2:
        reader = PdfReader(str(pdf_path))  # type: ignore[misc]
        for idx, (start, end) in enumerate(ranges, start=1):
            writer = PdfWriter()  # type: ignore[misc]

            start_clamped = max(0, min(start, len(reader.pages) - 1))
            end_clamped = max(start_clamped, min(end, len(reader.pages) - 1))

            for p in range(start_clamped, end_clamped + 1):
                writer.add_page(reader.pages[p])

            out_path = output_dir / f"invoice_{idx:02d}.pdf"
            with out_path.open("wb") as f:
                writer.write(f)

            outputs.append(out_path)
        return outputs

    # Fallback: use PyMuPDF (fitz) which is already used across this repo
    if not _HAS_FITZ:
        raise ModuleNotFoundError(
            "Neither PyPDF2 nor PyMuPDF (fitz) is available to split PDFs."
        )

    src = fitz.open(str(pdf_path))  # type: ignore[misc]
    try:
        max_page = len(src) - 1
        for idx, (start, end) in enumerate(ranges, start=1):
            start_clamped = max(0, min(start, max_page))
            end_clamped = max(start_clamped, min(end, max_page))

            out_doc = fitz.open()  # type: ignore[misc]
            try:
                out_doc.insert_pdf(src, from_page=start_clamped, to_page=end_clamped)
                out_path = output_dir / f"invoice_{idx:02d}.pdf"
                out_doc.save(str(out_path))
                outputs.append(out_path)
            finally:
                out_doc.close()
    finally:
        src.close()

    return outputs


def handle_merged_pdf_with_page_texts(
    pdf_path: str | Path,
    page_texts: Sequence[str],
    temp_split_root: str | Path,
    header_patterns: Optional[Sequence[str]] = None,
) -> Tuple[List[Tuple[int, int]], List[Path]]:
    """
    High-level helper that:
      1. Uses already-extracted page_texts to detect invoice page ranges.
      2. Splits the original PDF into per-invoice sub-PDFs.

    This is useful when your existing pipeline has *already* done text extraction
    (e.g. via pymupdf / tesseract) and you don't want to re-OCR the pages just
    to find boundaries.

    Returns:
        - list of (start_page, end_page) ranges
        - list of Paths to the sub-PDFs created in temp_split_root
    """
    ranges = find_invoice_page_ranges_from_text_pages(
        page_texts, header_patterns=header_patterns
    )

    temp_split_root = Path(temp_split_root)
    split_dir = temp_split_root / (Path(pdf_path).stem + "_split")

    sub_pdfs = split_pdf_by_page_ranges(pdf_path, ranges, split_dir)
    return ranges, sub_pdfs


def process_any_pdf_with_merge(
    extractor: Any,
    pdf_path: str | Path,
    target_claim_number: Optional[str] = None,
    header_patterns: Optional[Sequence[str]] = None,
    temp_split_root: str | Path = "outputs/merged_splits",
) -> dict:
    """
    High-level helper for web/API usage.

    Behaviour:
    - If no header_patterns are provided, or no boundaries are found, this simply
      calls extractor.process_pdf_with_verification(...) and returns its output.
    - If multiple invoice/document boundaries are detected, the PDF is split into
      per-range sub-PDFs and the extractor is run on each. All extracted claims
      are then merged into a single verification-style payload so existing UIs
      keep working.

    The returned dict is shaped like the normal verification_package.json to
    avoid breaking frontends that expect that structure.
    """
    pdf_path = str(pdf_path)

    # No patterns means "just behave like normal"
    if not header_patterns:
        return extractor.process_pdf_with_verification(pdf_path, target_claim_number)

    # First pass: get per-page text so we can detect boundaries
    try:
        all_text, pages_metadata = extractor.extract_text_from_pdf(pdf_path)
        page_texts = [p.get("text", "") for p in pages_metadata] or [all_text]
    except Exception:
        # If anything goes wrong, fall back to the normal path
        return extractor.process_pdf_with_verification(pdf_path, target_claim_number)

    ranges, sub_pdfs = handle_merged_pdf_with_page_texts(
        pdf_path=pdf_path,
        page_texts=page_texts,
        temp_split_root=temp_split_root,
        header_patterns=header_patterns,
    )

    # If we didn't actually get multiple chunks, just run the standard pipeline
    if not sub_pdfs or len(sub_pdfs) == 1:
        return extractor.process_pdf_with_verification(pdf_path, target_claim_number)

    # Run the existing pipeline once per sub-PDF
    per_invoice_results: List[dict] = []
    for sub_pdf in sub_pdfs:
        try:
            res = extractor.process_pdf_with_verification(str(sub_pdf), target_claim_number)
            per_invoice_results.append(res)
        except Exception as e:
            # Skip failed sub-documents but keep others
            print(f"⚠️ Error processing sub-PDF {sub_pdf}: {e}")

    if not per_invoice_results:
        # Fallback if everything failed
        return extractor.process_pdf_with_verification(pdf_path, target_claim_number)

    # Merge all claims into a single schema so the UI can show them together
    all_claims: List[dict] = []
    total_conf = 0.0
    conf_count = 0

    for res in per_invoice_results:
        schema = res.get("extracted_schema", {}) or {}
        claims = schema.get("claims", []) or []
        all_claims.extend(claims)

        summary = res.get("summary") or res.get("extraction_summary") or {}
        avg_conf = summary.get("avg_confidence")
        if isinstance(avg_conf, (int, float)):
            total_conf += float(avg_conf)
            conf_count += 1

    first = per_invoice_results[0]
    combined = dict(first)  # shallow copy base structure

    # Combined schema
    summary_records: List[dict] = []
    for res in per_invoice_results:
        schema = (res.get("extracted_schema") or {}) if isinstance(res.get("extracted_schema"), dict) else {}
        sl = schema.get("SummaryLevel")
        if isinstance(sl, list):
            summary_records.extend([r for r in sl if isinstance(r, dict)])
        elif isinstance(sl, dict):
            # Backward compatibility: older outputs stored a single object with comma-separated years
            years_raw = sl.get("years")
            years_list: List[str] = []
            if isinstance(years_raw, (int, float)):
                years_list = [str(int(years_raw))]
            elif isinstance(years_raw, str):
                years_list = [y.strip() for y in years_raw.split(",") if y.strip()]

            est = sl.get("estimated_annual")
            pol = sl.get("policy_numbers")
            car = sl.get("carrier_names")
            for y in years_list:
                summary_records.append(
                    {
                        "estimated_annual": est,
                        "year": str(y),
                        "policy_number": pol,
                        "carrier_name": car,
                    }
                )

    # Dedupe to one record per year (first record wins)
    by_year: dict = {}
    for r in summary_records:
        y = str(r.get("year")).strip() if r.get("year") is not None else ""
        if not y:
            continue
        if y not in by_year:
            by_year[y] = {
                "estimated_annual": r.get("estimated_annual"),
                "year": y,
                "policy_number": r.get("policy_number"),
                "carrier_name": r.get("carrier_name"),
            }

    def _year_sort_key(y: str):
        try:
            return (0, int(y))
        except Exception:
            return (1, y)

    summary_level = [by_year[y] for y in sorted(by_year.keys(), key=_year_sort_key)]

    combined_schema = {
        "claims": all_claims,
        "SummaryLevel": summary_level,
    }

    combined["extracted_schema"] = combined_schema

    # Update summary info
    summary = combined.get("summary") or {}
    summary["claims_count"] = len(all_claims)
    if conf_count:
        summary["avg_confidence"] = total_conf / conf_count
    combined["summary"] = summary

    # Tag metadata so we know this came from a merged run
    meta = combined.get("extraction_metadata") or {}
    meta["merged_invoice_count"] = len(per_invoice_results)
    method = meta.get("method") or ""
    if "multi-invoice" not in method:
        meta["method"] = f"{method}+multi-invoice".strip("+")
    combined["extraction_metadata"] = meta

    return combined


__all__ = [
    "DEFAULT_HEADER_PATTERNS",
    "find_invoice_page_ranges_from_text_pages",
    "split_pdf_by_page_ranges",
    "handle_merged_pdf_with_page_texts",
    "process_any_pdf_with_merge",
]

