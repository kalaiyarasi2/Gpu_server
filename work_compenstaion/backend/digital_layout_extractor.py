"""
Digital Layout Extractor for ACORD Forms (Workers Compensation)
==============================================================
Uses `pdftotext -layout` to preserve exact 2D visual layouts, rigid grid
coordinates, label-value pairs, and checkbox alignments in digital ACORD forms.
"""

from __future__ import annotations

import os
import sys
import tempfile
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Union


def pdf_to_layout_txt(pdf_path: Union[str, Path], txt_path: Union[str, Path]) -> None:
    """
    Executes pdftotext with layout preservation.
    
    Args:
        pdf_path: Path to the input PDF file
        txt_path: Path to the output text file
    """
    pdf_path = str(Path(pdf_path).resolve())
    txt_path = str(Path(txt_path).resolve())

    # Build command with UTF-8 encoding support
    cmd = ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, txt_path]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        # Fallback if pdftotext is not directly on system PATH
        alt_poppler = Path("C:/Users/Intern/Downloads/Release-25.12.0-0/poppler-25.12.0/Library/bin/pdftotext.exe")
        if alt_poppler.exists():
            cmd[0] = str(alt_poppler)
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        else:
            raise


def extract_acord_layout(
    pdf_path: Union[str, Path],
    output_txt: Optional[Union[str, Path]] = None,
) -> Tuple[str, List[Dict[str, Union[int, str]]]]:
    """
    Extracts digital ACORD PDF text preserving layout and returns
    (combined_text, pages_metadata) matching the work_compensation pipeline contract.
    
    Args:
        pdf_path: Path to the PDF file
        output_txt: Optional path to save extracted text
        
    Returns:
        tuple: (combined_text, pages_metadata)
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    temp_file = None
    if output_txt:
        txt_dest = Path(output_txt).resolve()
        txt_dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            temp_file = Path(tmp.name)
        txt_dest = temp_file

    try:
        pdf_to_layout_txt(pdf_path, txt_dest)
        raw_text = txt_dest.read_text(encoding="utf-8", errors="ignore")

        # pdftotext separates individual pages with Form Feed '\x0c'
        raw_pages = raw_text.split("\x0c")
        if raw_pages and not raw_pages[-1].strip():
            raw_pages.pop()

        pages_metadata: List[Dict[str, Union[int, str]]] = []
        for idx, page_content in enumerate(raw_pages, start=1):
            pages_metadata.append({
                "page_number": idx,
                "text": page_content,
                "extraction_method": "pdftotext_layout",
            })

        print(f"   [OK] [DigitalLayout] Extracted {len(pages_metadata)} pages ({len(raw_text)} chars) via pdftotext -layout")
        return raw_text, pages_metadata

    finally:
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass


# Alias for work_compensation module compatibility
extract_digital_layout = extract_acord_layout


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python digital_layout_extractor.py <path_to_acord_pdf> [output_txt]")
        sys.exit(1)

    input_pdf = sys.argv[1]
    output_target = sys.argv[2] if len(sys.argv) > 2 else "output.txt"

    print(f"\n{'='*70}")
    print(f"ACORD DIGITAL LAYOUT EXTRACTION")
    print(f"{'='*70}")
    print(f"Input:  {input_pdf}")
    print(f"Output: {output_target}\n")

    text, metadata = extract_acord_layout(input_pdf, output_target)
    print(f"Pages:  {len(metadata)}")
    print(f"Chars:  {len(text)}")
    print(f"{'='*70}\n")
