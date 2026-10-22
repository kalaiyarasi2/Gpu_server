"""
Smart PDF Connector Module
==========================
Intelligent Engine Bridge connecting Document Classification (Digital / Scanned / Semi-Scanned)
with Domain-Specific Routing:
  - Loss Runs (Insurance Claims):
      • digital       -> pdfplumber (table & structure preservation, 0% OCR overhead)
      • scanned       -> PaddleOCR (GPU/CPU robust text recognition)
      • semi_scanned  -> PaddleOCR (or selective hybrid)
  - ACORD Forms (Workers Comp / Commercial Applications):
      • digital       -> pdftotext -layout (strict 2D spatial grid & checkbox preservation in < 0.2s)
      • scanned       -> PaddleOCR
      • semi_scanned  -> PaddleOCR
  - Universal Router Snippet:
      • digital       -> PyMuPDF (fitz) first 2 pages in 0.08s
      • scanned       -> PaddleOCR first 2 pages

Powered by FileIdentifier (Type3 font detection, unioned image coverage, invisible OCR layers).
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import tempfile
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# --------------------------------------------------------------------------- #
# Automatic Path Resolution for Sub-Modules & File_identifier
# --------------------------------------------------------------------------- #
_CURRENT_DIR = Path(__file__).resolve().parent
_WORKSPACE_ROOT = _CURRENT_DIR.parent if _CURRENT_DIR.name == "Sales team - Copy" else _CURRENT_DIR

_CANDIDATE_DIRS = [
    _CURRENT_DIR,
    _WORKSPACE_ROOT,
    _WORKSPACE_ROOT / "File_identifier",
    _CURRENT_DIR / "File_identifier",
    _CURRENT_DIR / "Gpu_server",
    _CURRENT_DIR / "Gpu_server" / "Unified_PDF_Platform",
    _CURRENT_DIR / "Gpu_server" / "work_compenstaion" / "backend",
    _CURRENT_DIR / "Gpu_server" / "Insurance_pdf_extractor-main" / "backend",
    Path("C:/Users/Intern/gpu/Gpu_server"),
    Path("C:/Users/Intern/gpu/Gpu_server/File_identifier"),
]

for _d in _CANDIDATE_DIRS:
    if _d.exists():
        _d_str = str(_d.resolve())
        if _d_str not in sys.path:
            sys.path.insert(0, _d_str)

# --------------------------------------------------------------------------- #
# Engine Imports
# --------------------------------------------------------------------------- #
try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz  # type: ignore
    except ImportError:
        fitz = None

try:
    from file_identifier import FileIdentifier, FileType, IdentifierResult
    _FILE_IDENTIFIER_AVAILABLE = True
except ImportError:
    FileIdentifier = None
    FileType = None
    IdentifierResult = None
    _FILE_IDENTIFIER_AVAILABLE = False

try:
    from paddleocr_enhancer import extract_with_paddleocr
    _PADDLEOCR_AVAILABLE = True
except ImportError:
    extract_with_paddleocr = None
    _PADDLEOCR_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Data Structures
# --------------------------------------------------------------------------- #
@dataclass
class SmartExtractionResult:
    file_type: str                     # "digital" | "semi_scanned" | "fully_scanned"
    total_pages: int
    digital_pages: int
    scanned_pages: int
    blank_pages: int
    scanned_page_numbers: List[int]
    digital_page_numbers: List[int]
    text: str                          # Full extracted text across all pages
    pages_metadata: List[Dict[str, Any]] = field(default_factory=list)
    timing_sec: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_type": self.file_type,
            "total_pages": self.total_pages,
            "digital_pages": self.digital_pages,
            "scanned_pages": self.scanned_pages,
            "blank_pages": self.blank_pages,
            "scanned_page_numbers": self.scanned_page_numbers,
            "digital_page_numbers": self.digital_page_numbers,
            "timing_sec": self.timing_sec,
            "reasons": self.reasons,
        }


# --------------------------------------------------------------------------- #
# SmartPDFConnector Core Class
# --------------------------------------------------------------------------- #
class SmartPDFConnector:
    """
    Intelligent PDF Extraction Router powered by FileIdentifier.
    """

    def __init__(
        self,
        use_gpu: bool = True,
        min_text_chars: int = 25,
        scan_image_coverage: float = 0.70,
        treat_type3_as_scanned: bool = True,
        min_drawings_for_scan: int = 50,
    ):
        self.use_gpu = use_gpu
        self.min_text_chars = min_text_chars
        self.scan_image_coverage = scan_image_coverage
        self.treat_type3_as_scanned = treat_type3_as_scanned
        self.min_drawings_for_scan = min_drawings_for_scan

        if _FILE_IDENTIFIER_AVAILABLE and FileIdentifier:
            self._identifier = FileIdentifier(
                min_text_chars=min_text_chars,
                scan_image_coverage=scan_image_coverage,
                treat_type3_as_scanned=treat_type3_as_scanned,
                min_drawings_for_scan=min_drawings_for_scan,
                upload_dir=None,
            )
        else:
            self._identifier = None

    # ----------------------------------------------------------------------- #
    # 1. Pre-Flight Document Identification
    # ----------------------------------------------------------------------- #
    def identify(self, pdf_path: Union[str, Path], max_pages: Optional[int] = None) -> Any:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        if self._identifier:
            try:
                self._identifier.max_pages_to_check = max_pages
                return self._identifier.identify(pdf_path)
            except Exception as e:
                print(f"[SmartConnector] FileIdentifier warning: {e}. Falling back to basic check.")

        return self._heuristic_fallback_identify(pdf_path, max_pages=max_pages)

    def _heuristic_fallback_identify(self, pdf_path: Path, max_pages: Optional[int] = None) -> Any:
        class _FallbackResult:
            def __init__(self, file_type, total, dig, scan, blank, scan_nums, dig_nums):
                self.file_type = file_type
                self.total_pages = total
                self.digital_pages = dig
                self.scanned_pages = scan
                self.blank_pages = blank
                self.scanned_page_numbers = scan_nums
                self.digital_page_numbers = dig_nums
                self.pages = []

        if not fitz:
            return _FallbackResult("fully_scanned", 1, 0, 1, 0, [1], [])

        try:
            doc = fitz.open(str(pdf_path))
            total = len(doc)
            check_n = min(total, max_pages) if max_pages else total
            dig_nums, scan_nums = [], []

            for i in range(check_n):
                page = doc[i]
                text = page.get_text("text").strip()
                if len(text) > 40:
                    dig_nums.append(i + 1)
                else:
                    scan_nums.append(i + 1)

            doc.close()
            if not scan_nums:
                ft = "digital"
            elif not dig_nums:
                ft = "fully_scanned"
            else:
                ft = "semi_scanned"

            return _FallbackResult(ft, total, len(dig_nums), len(scan_nums), 0, scan_nums, dig_nums)
        except Exception:
            return _FallbackResult("fully_scanned", 1, 0, 1, 0, [1], [])

    # ----------------------------------------------------------------------- #
    # 2. Fast Snippet Extraction (for Router / Keyword Matching in 0.08s)
    # ----------------------------------------------------------------------- #
    def extract_classification_snippet(self, pdf_path: Union[str, Path], max_pages: int = 2) -> str:
        pdf_path = Path(pdf_path)
        t0 = time.time()
        ident_res = self.identify(pdf_path, max_pages=max_pages)
        file_type = ident_res.file_type.value if hasattr(ident_res.file_type, "value") else str(ident_res.file_type)

        snippet_parts = []
        if file_type == "digital":
            print(f"[SmartConnector] Digital PDF detected for snippet. Using fast PyMuPDF (first {max_pages} pages)...")
            text = self._extract_pymupdf_pages(pdf_path, pages=list(range(1, max_pages + 1)))
            snippet_parts.append(text)
        elif file_type == "fully_scanned":
            print(f"[SmartConnector] Scanned PDF detected for snippet. Routing first {max_pages} pages to PaddleOCR...")
            text = self._extract_paddleocr_pages(pdf_path, max_pages=max_pages)
            snippet_parts.append(text)
        else:  # semi_scanned
            print(f"[SmartConnector] Semi-scanned PDF detected for snippet. Running hybrid routing for first {max_pages} pages...")
            scanned_in_prefix = [p for p in ident_res.scanned_page_numbers if p <= max_pages]
            digital_in_prefix = [p for p in ident_res.digital_page_numbers if p <= max_pages]

            if 1 in digital_in_prefix:
                snippet_parts.append(self._extract_pymupdf_pages(pdf_path, pages=[1]))
            elif 1 in scanned_in_prefix:
                snippet_parts.append(self._extract_paddleocr_pages(pdf_path, max_pages=1))

            if max_pages >= 2 and ident_res.total_pages >= 2:
                if 2 in digital_in_prefix:
                    snippet_parts.append(self._extract_pymupdf_pages(pdf_path, pages=[2]))
                elif 2 in scanned_in_prefix:
                    snippet_parts.append(self._extract_paddleocr_pages(pdf_path, max_pages=2))

        final_snippet = "\n\n".join(part.strip() for part in snippet_parts if part and part.strip())
        elapsed = time.time() - t0
        print(f"[SmartConnector] Snippet extracted: {len(final_snippet)} chars in {elapsed:.2f}s (Type: {file_type})")
        return final_snippet[:50000]

    # ----------------------------------------------------------------------- #
    # 3. Domain-Specific Extraction (Loss Run vs ACORD vs Generic)
    # ----------------------------------------------------------------------- #
    def extract_smart(
        self,
        pdf_path: Union[str, Path],
        doc_type: str = "auto",  # "loss_run" | "acord" | "auto"
        enable_table: bool = True,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Unified entrypoint returning (combined_text, pages_metadata):
          - loss_run:
              • digital       -> pdfplumber
              • scanned       -> PaddleOCR
              • semi_scanned  -> PaddleOCR
          - acord:
              • digital       -> pdftotext -layout (fixed 2D grid spatial formatting)
              • scanned       -> PaddleOCR
              • semi_scanned  -> PaddleOCR
        """
        pdf_path = Path(pdf_path)
        t_start = time.time()

        # Step 1: Detect Document Category if "auto"
        resolved_doc_type = self._resolve_doc_type(pdf_path, doc_type)

        # Step 2: Identification via FileIdentifier
        ident_res = self.identify(pdf_path)
        file_type = ident_res.file_type.value if hasattr(ident_res.file_type, "value") else str(ident_res.file_type)

        print(f"\n[SmartConnector] === SMART EXTRACTION PIPELINE ===")
        print(f"  File: {pdf_path.name}")
        print(f"  Target Document: {resolved_doc_type.upper()}")
        print(f"  Classification:  {file_type.upper()} ({ident_res.total_pages} pages: {ident_res.digital_pages} digital, {ident_res.scanned_pages} scanned)")

        pages_metadata: List[Dict[str, Any]] = []

        if resolved_doc_type == "acord":
            # ─────────────────────────────────────────────────────────────
            # ACORD ROUTING MATRIX:
            #   • digital       -> pdftotext -layout (strict spatial alignment)
            #   • scanned       -> PaddleOCR
            #   • semi_scanned  -> PaddleOCR
            # ─────────────────────────────────────────────────────────────
            if file_type == "digital":
                print(f"  -> Path: [ACORD DIGITAL] pdftotext -layout. (0% OCR overhead)")
                pages_metadata = self._extract_pdftotext_layout(pdf_path)
            else:
                print(f"  -> Path: [ACORD {file_type.upper()}] PaddleOCR (GPU={self.use_gpu}).")
                pages_metadata = self._extract_paddleocr_full(pdf_path, enable_table=enable_table)

        elif resolved_doc_type == "loss_run":
            # ─────────────────────────────────────────────────────────────
            # LOSS RUN ROUTING MATRIX:
            #   • digital       -> pdfplumber (table columns & coordinates)
            #   • scanned       -> PaddleOCR
            #   • semi_scanned  -> PaddleOCR
            # ─────────────────────────────────────────────────────────────
            if file_type == "digital":
                print(f"  -> Path: [LOSS RUN DIGITAL] pdfplumber. (0% OCR overhead)")
                pages_metadata = self._extract_pdfplumber_full(pdf_path)
            else:
                print(f"  -> Path: [LOSS RUN {file_type.upper()}] PaddleOCR (GPU={self.use_gpu}).")
                pages_metadata = self._extract_paddleocr_full(pdf_path, enable_table=enable_table)

        else:
            # Generic document fallback
            if file_type == "digital":
                print(f"  -> Path: [GENERIC DIGITAL] pdfplumber.")
                pages_metadata = self._extract_pdfplumber_full(pdf_path)
            else:
                print(f"  -> Path: [GENERIC {file_type.upper()}] PaddleOCR.")
                pages_metadata = self._extract_paddleocr_full(pdf_path, enable_table=enable_table)

        # Sort pages numerically
        pages_metadata.sort(key=lambda x: x.get("page_number", 0))

        # Build combined text
        text_blocks = []
        for p in pages_metadata:
            header = f"--- PAGE {p.get('page_number')} [{p.get('extraction_method', 'unknown').upper()}] ---"
            p_text = (p.get("text") or "").strip()
            if p_text:
                text_blocks.append(f"{header}\n{p_text}")
            else:
                text_blocks.append(f"{header}\n[BLANK PAGE]")

        combined_text = "\n\n".join(text_blocks)
        elapsed = time.time() - t_start
        print(f"  ✓ Complete: {len(combined_text)} characters extracted in {elapsed:.2f}s total.\n")
        return combined_text, pages_metadata

    def extract_document(
        self,
        pdf_path: Union[str, Path],
        preferred_digital_engine: str = "pdfplumber",
        enable_table: bool = True,
        doc_type: str = "auto",
    ) -> SmartExtractionResult:
        """Compatibility wrapper returning SmartExtractionResult."""
        combined_text, pages_metadata = self.extract_smart(
            pdf_path=pdf_path,
            doc_type=doc_type,
            enable_table=enable_table,
        )
        ident_res = self.identify(pdf_path)
        file_type = ident_res.file_type.value if hasattr(ident_res.file_type, "value") else str(ident_res.file_type)

        return SmartExtractionResult(
            file_type=file_type,
            total_pages=ident_res.total_pages,
            digital_pages=ident_res.digital_pages,
            scanned_pages=ident_res.scanned_pages,
            blank_pages=ident_res.blank_pages,
            scanned_page_numbers=list(ident_res.scanned_page_numbers),
            digital_page_numbers=list(ident_res.digital_page_numbers),
            text=combined_text,
            pages_metadata=pages_metadata,
            timing_sec={"total": 0.0},
        )

    # ----------------------------------------------------------------------- #
    # 4. Engine Implementations
    # ----------------------------------------------------------------------- #
    def _extract_pdftotext_layout(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """
        Extracts document text using `pdftotext -layout` which is the industry standard
        for preserving rigid 2D grid coordinates and checkbox alignment in ACORD forms.
        """
        temp_txt = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
                temp_txt = Path(tmp.name)

            cmd = ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf_path.resolve()), str(temp_txt.resolve())]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if res.returncode == 0 and temp_txt.exists():
                full_raw_text = temp_txt.read_text(encoding="utf-8", errors="ignore")
                # pdftotext separates pages using Form Feed character '\x0c'
                raw_pages = full_raw_text.split("\x0c")
                if raw_pages and not raw_pages[-1].strip():
                    raw_pages.pop()

                pages_metadata = []
                for idx, page_str in enumerate(raw_pages, start=1):
                    pages_metadata.append({
                        "page_number": idx,
                        "text": page_str,
                        "extraction_method": "pdftotext_layout",
                    })

                if pages_metadata:
                    return pages_metadata
        except Exception as e:
            print(f"[SmartConnector] pdftotext execution error: {e}. Falling back to PyMuPDF layout.")
        finally:
            if temp_txt and temp_txt.exists():
                try:
                    temp_txt.unlink()
                except Exception:
                    pass

        # Fallback to PyMuPDF layout extraction
        return self._extract_pymupdf_full(pdf_path)

    def _extract_pdfplumber_full(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """Extracts text preserving tables using pdfplumber."""
        pages_metadata = []
        try:
            import pdfplumber
            with pdfplumber.open(str(pdf_path)) as pdf:
                for idx, page in enumerate(pdf.pages, start=1):
                    p_text = page.extract_text(layout=True) or page.extract_text() or ""
                    pages_metadata.append({
                        "page_number": idx,
                        "text": p_text,
                        "extraction_method": "pdfplumber",
                    })
            if pages_metadata:
                return pages_metadata
        except Exception as e:
            print(f"[SmartConnector] pdfplumber error: {e}. Falling back to PyMuPDF.")

        return self._extract_pymupdf_full(pdf_path)

    def _extract_pymupdf_full(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """Fast PyMuPDF text extractor fallback."""
        if not fitz:
            return []
        pages_metadata = []
        try:
            doc = fitz.open(str(pdf_path))
            for idx, page in enumerate(doc, start=1):
                p_text = page.get_text("text") or ""
                pages_metadata.append({
                    "page_number": idx,
                    "text": p_text,
                    "extraction_method": "pymupdf",
                })
            doc.close()
        except Exception as e:
            print(f"[SmartConnector] PyMuPDF extraction error: {e}")
        return pages_metadata

    def _extract_pymupdf_pages(self, pdf_path: Path, pages: List[int]) -> str:
        """Extract text from specific 1-based page numbers."""
        if not fitz:
            return ""
        text_parts = []
        try:
            doc = fitz.open(str(pdf_path))
            for p_num in pages:
                if 1 <= p_num <= len(doc):
                    text_parts.append(doc[p_num - 1].get_text("text") or "")
            doc.close()
        except Exception as e:
            print(f"[SmartConnector] PyMuPDF pages error: {e}")
        return "\n\n".join(text_parts)

    def _extract_paddleocr_full(self, pdf_path: Path, enable_table: bool = True) -> List[Dict[str, Any]]:
        """Extracts full document using PaddleOCR with graceful fallbacks."""
        if _PADDLEOCR_AVAILABLE and extract_with_paddleocr:
            try:
                _, pages_metadata = extract_with_paddleocr(
                    str(pdf_path),
                    use_gpu=self.use_gpu,
                    enable_table=enable_table,
                )
                if pages_metadata:
                    for p in pages_metadata:
                        p.setdefault("extraction_method", "paddleocr")
                    return pages_metadata
            except Exception as e:
                print(f"[SmartConnector] PaddleOCR error: {e}. Falling back to Tesseract.")

        # Tesseract fallback
        return self._extract_tesseract_full(pdf_path)

    def _extract_paddleocr_pages(self, pdf_path: Path, max_pages: int = 2) -> str:
        """Extract first N pages via PaddleOCR or Tesseract."""
        pages = self._extract_paddleocr_full(pdf_path, enable_table=False)
        parts = [p.get("text", "") for p in pages if p.get("page_number", 999) <= max_pages]
        return "\n\n".join(parts)

    def _extract_tesseract_full(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """Fallback OCR using pytesseract."""
        pages_metadata = []
        try:
            import pytesseract
            from PIL import Image
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(pdf_path))
            for idx in range(len(pdf)):
                page = pdf[idx]
                image = page.render(scale=2.0).to_pil()
                text = pytesseract.image_to_string(image, config="--psm 6")
                pages_metadata.append({
                    "page_number": idx + 1,
                    "text": text,
                    "extraction_method": "pytesseract",
                })
        except Exception as e:
            print(f"[SmartConnector] Tesseract fallback failed: {e}. Defaulting to PyMuPDF.")
            return self._extract_pymupdf_full(pdf_path)
        return pages_metadata

    def _resolve_doc_type(self, pdf_path: Path, doc_type: str) -> str:
        """Resolves 'auto' into 'acord', 'loss_run', or 'generic'."""
        if doc_type in ("acord", "loss_run"):
            return doc_type

        fname = pdf_path.name.upper()
        if "ACORD" in fname or "WORK_COMP" in fname or "130" in fname or "125" in fname:
            return "acord"
        if "LOSS" in fname or "CLAIM" in fname or "FCBI" in fname:
            return "loss_run"

        # Check snippet
        snippet = self._extract_pymupdf_pages(pdf_path, pages=[1]).upper()
        if "ACORD" in snippet or "WORKERS COMPENSATION APPLICATION" in snippet:
            return "acord"
        if "LOSS RUN" in snippet or "CLAIM DETAIL" in snippet or "POLICY PERIOD" in snippet:
            return "loss_run"

        return "generic"


# --------------------------------------------------------------------------- #
# Module-Level Convenience Functions
# --------------------------------------------------------------------------- #
_default_connector = SmartPDFConnector()

def classify_pdf(pdf_path: Union[str, Path], max_pages: Optional[int] = None) -> Any:
    """Classify PDF into 'digital', 'semi_scanned', or 'fully_scanned'."""
    return _default_connector.identify(pdf_path, max_pages=max_pages)

def extract_classification_snippet(pdf_path: Union[str, Path], max_pages: int = 2) -> str:
    """Extract fast text snippet for document router / keyword matching."""
    return _default_connector.extract_classification_snippet(pdf_path, max_pages=max_pages)

def extract_smart_text(
    pdf_path: Union[str, Path],
    doc_type: str = "auto",  # "loss_run" | "acord" | "auto"
    enable_table: bool = True,
    use_gpu: bool = True,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Main extraction function for POCs:
      - Loss Runs -> digital (pdfplumber), scanned/semi-scanned (PaddleOCR)
      - ACORD     -> digital (pdftotext -layout), scanned/semi-scanned (PaddleOCR)
    Returns (combined_text, pages_metadata).
    """
    connector = SmartPDFConnector(use_gpu=use_gpu)
    return connector.extract_smart(pdf_path, doc_type=doc_type, enable_table=enable_table)

def extract_document_text(
    pdf_path: Union[str, Path],
    preferred_digital_engine: str = "pdfplumber",
    enable_table: bool = True,
    use_gpu: bool = True,
    doc_type: str = "auto",
) -> SmartExtractionResult:
    """Compatibility wrapper returning SmartExtractionResult."""
    connector = SmartPDFConnector(use_gpu=use_gpu)
    return connector.extract_document(
        pdf_path,
        preferred_digital_engine=preferred_digital_engine,
        enable_table=enable_table,
        doc_type=doc_type,
    )


# --------------------------------------------------------------------------- #
# CLI Diagnostic Runner
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python smart_pdf_connector.py <path_to_pdf> [doc_type: auto|acord|loss_run]")
        sys.exit(1)

    target_pdf = sys.argv[1]
    target_type = sys.argv[2] if len(sys.argv) > 2 else "auto"

    print(f"Testing Smart PDF Connector on: {target_pdf} (doc_type={target_type})")
    connector = SmartPDFConnector()

    print("\n--- TEST: Smart Extraction ---")
    t0 = time.time()
    text, pages = connector.extract_smart(target_pdf, doc_type=target_type)
    elapsed = time.time() - t0

    print(f"\nResult Summary:")
    print(f"  Pages Extracted:      {len(pages)}")
    print(f"  Total Chars:          {len(text)}")
    print(f"  Time Elapsed:         {elapsed:.2f}s")
    for p in pages:
        print(f"    Page {p['page_number']}: {p['extraction_method']} ({len(p.get('text', ''))} chars)")
