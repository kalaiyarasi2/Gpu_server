from __future__ import annotations

"""
Utilities for handling merged PDFs that contain multiple invoices/documents.
Improved boundary detection with optional LLM-based vendor identification.
"""

import os
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
DEFAULT_HEADER_PATTERNS: Sequence[str] = ()


def _identify_vendor_with_llm(text: str, client: Any) -> str:
    """Use a fast LLM call to identify the primary vendor on a page."""
    if not client:
        return "UNKNOWN"
    
    try:
        # Just take the first 1000 characters for speed and cost
        snippet = text[:1000]
        prompt = f"""Identify the primary company NAME (the vendor/issuer) of this invoice snippet. 
        It is usually at the very top. Return ONLY the name, or UNKNOWN.
        
        SNIPPET:
        {snippet}
        
        VENDOR NAME:"""
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=20,
            timeout=15
        )
        return response.choices[0].message.content.strip().upper()
    except Exception as e:
        print(f"[handle_merge] LLM Vendor ID failed: {e}")
        return "UNKNOWN"


def find_invoice_page_ranges_from_text_pages(
    page_texts: Sequence[str],
    header_patterns: Optional[Sequence[str]] = None,
    client: Optional[Any] = None,
) -> List[Tuple[int, int]]:
    """
    Given a list of page-level text strings, return (start_page, end_page) 0-based
    ranges for each detected invoice.

    Improved Heuristic:
    1. Splits if current page matches are DISJOINT from previous matches.
    2. Splits if the set of matches undergoes a "transition" (symmetric difference).
    3. (Optional) Splits if an LLM identifies a different vendor than the previous invoice.
    """
    boundaries: List[int] = []
    patterns = list(header_patterns or DEFAULT_HEADER_PATTERNS)

    prev_matches: set[str] = set()
    prev_vendor: str = "UNKNOWN"
    
    # Generic invoice start patterns (always check these even if not in dedicated list)
    universal_starts = ["TAX INVOICE", "INVOICE #", "INVOICE NUMBER", "BILL NUMBER", "ORIGINAL FOR RECIPIENT"]
    
    llm_calls_made = 0
    MAX_LLM_VENDORS = 10 # Prevent runaway LLM costs/time on huge files

    for i, text in enumerate(page_texts):
        if not text:
            prev_matches = set()
            prev_vendor = "UNKNOWN"
            continue

        upper = text.upper()
        # Find exact matches from the pattern list
        matches = {p for p in patterns if p.upper() in upper}
        
        # Check for universal invoice start indicators
        has_universal_start = any(us in upper for us in universal_starts)

        is_split = False
        if matches:
            if i == 0 or not prev_matches:
                is_split = True
            elif matches.isdisjoint(prev_matches):
                is_split = True
            else:
                # Overlapping headers. Check if it's a transition (e.g. Airtel -> Spectra)
                new_stuff = matches - prev_matches
                old_stuff = prev_matches - matches
                if new_stuff and old_stuff:
                    is_split = True
        
        # LLM fallback for high-confidence but ambiguous starts (like common "TAX INVOICE" header)
        if not is_split and has_universal_start and client and i > 0 and llm_calls_made < MAX_LLM_VENDORS:
            current_vendor = _identify_vendor_with_llm(text, client)
            llm_calls_made += 1
            if current_vendor != "UNKNOWN" and prev_vendor != "UNKNOWN" and current_vendor != prev_vendor:
                print(f"[handle_merge] LLM detected vendor change at page {i+1}: {prev_vendor} -> {current_vendor}")
                is_split = True
            
            if current_vendor != "UNKNOWN":
                prev_vendor = current_vendor

        if is_split:
            boundaries.append(i)
            # Update current vendor for next comparison if we just split
            if client:
                prev_vendor = _identify_vendor_with_llm(text, client)

        prev_matches = matches

    if not boundaries:
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
    """Physically split a PDF into one sub-PDF per page range."""
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs: List[Path] = []

    if _HAS_PYPDF2:
        try:
            reader = PdfReader(str(pdf_path))
            for idx, (start, end) in enumerate(ranges, start=1):
                writer = PdfWriter()
                start_clamped = max(0, min(start, len(reader.pages) - 1))
                end_clamped = max(start_clamped, min(end, len(reader.pages) - 1))
                for p in range(start_clamped, end_clamped + 1):
                    writer.add_page(reader.pages[p])
                out_path = output_dir / f"invoice_{idx:02d}.pdf"
                with out_path.open("wb") as f:
                    writer.write(f)
                outputs.append(out_path)
            return outputs
        except Exception as e:
            print(f"PyPDF2 split failed: {e}, falling back to fitz...")

    if not _HAS_FITZ:
        raise ModuleNotFoundError("No PDF splitting library (PyPDF2/fitz) available.")

    src = fitz.open(str(pdf_path))
    try:
        max_page = len(src) - 1
        for idx, (start, end) in enumerate(ranges, start=1):
            start_clamped = max(0, min(start, max_page))
            end_clamped = max(start_clamped, min(end, max_page))
            out_doc = fitz.open()
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
    client: Optional[Any] = None,
) -> Tuple[List[Tuple[int, int]], List[Path]]:
    """Helper that finds ranges and splits PDF."""
    ranges = find_invoice_page_ranges_from_text_pages(
        page_texts, 
        header_patterns=header_patterns, 
        client=client
    )
    temp_split_root = Path(temp_split_root)
    split_dir = temp_split_root / (Path(pdf_path).stem + "_split")
    sub_pdfs = split_pdf_by_page_ranges(pdf_path, ranges, split_dir)
    return ranges, sub_pdfs


__all__ = [
    "DEFAULT_HEADER_PATTERNS",
    "find_invoice_page_ranges_from_text_pages",
    "split_pdf_by_page_ranges",
    "handle_merged_pdf_with_page_texts",
]
