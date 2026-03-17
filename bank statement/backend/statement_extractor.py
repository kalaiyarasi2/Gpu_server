import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

# Load environment variables
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


def _safe_import_openpyxl():
    try:
        import openpyxl  # noqa: F401
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter

        return Alignment, Font, get_column_letter
    except ImportError:
        # Match existing project style: attempt install at runtime.
        import subprocess
        import sys

        subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter

        return Alignment, Font, get_column_letter


_DATE_RE = re.compile(r"^(?P<mm>\d{2})/(?P<dd>\d{2})\b")


@dataclass
class StatementMetadata:
    source_file: str
    extraction_date: str
    extraction_method: str
    rotated: bool
    account_number: Optional[str] = None
    period_start: Optional[str] = None  # MM/DD/YYYY
    period_end: Optional[str] = None  # MM/DD/YYYY


class StatementExtractor:
    """
    Bank statement extractor that outputs:
      - JSON: deposits_and_credits[], checks_and_other_debits[]
      - Excel: single sheet with two labeled sections
    """

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = Path(output_dir) if output_dir else Path("outputs")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------
    # Public API
    # ---------------------------
    def process_pdf(self, pdf_path: str) -> Dict[str, Any]:
        """
        Full pipeline:
          1) auto-rotate
          2) extract text (hybrid or OCR)
          3) parse deposits + debits
          4) write JSON + Excel + verification
        Returns a dict with paths + parsed data.
        """
        pdf_path = str(pdf_path)
        source_file = os.path.basename(pdf_path)

        # temp rotation file
        temp_rotated_dir = tempfile.mkdtemp()
        temp_rotated_pdf = os.path.join(temp_rotated_dir, "rotated_temp.pdf")
        rotated = False

        try:
            from pdf_rotation import auto_rotate_pdf_content

            rotated = auto_rotate_pdf_content(pdf_path, temp_rotated_pdf)
            working_pdf = temp_rotated_pdf if rotated else pdf_path
        except Exception:
            working_pdf = pdf_path

        # extraction (text is still saved for audit/debug)
        text, pages_metadata, extraction_method = self._extract_text(working_pdf)

        # 2a) Vision Recovery / Patching (for scanned or messy digital PDFs)
        try:
            import importlib.util
            
            # Dynamically load the local vision_recovery module to avoid sys.path collisions
            # (unified_router.py prepends multiple backend dirs to sys.path which can cause
            # the wrong vision_recovery.py to be imported)
            vision_ext_path = Path(__file__).parent / "vision_recovery.py"
            spec = importlib.util.spec_from_file_location("local_vision_recovery", vision_ext_path)
            local_vr = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(local_vr)
            VisionRecoveryHandler = local_vr.VisionRecoveryHandler
            
            from openai import OpenAI
            
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            recovery_handler = VisionRecoveryHandler(client)
            
            # This will internally run health check and patch only if needed
            new_text = recovery_handler.patch_text_with_vision(working_pdf, pages_metadata)
            if new_text != text:
                text = new_text
                extraction_method = f"{extraction_method} + vision-recovery"
                # Update individual page metadata objects if patched (for consistency)
                # Note: patch_text_with_vision returns a combined string, 
                # but we can re-extract metadata if we really need per-page accuracy.
        except Exception as e:
            print(f"   ⚠️ Vision Recovery skipped: {e}")

        # 2b) Document Intelligence (Phase 2: Structural Discovery & Phase 3: Dynamic Schema)
        intel_manager = None
        try:
            from dynamic_extraction_prototype import DynamicExtractionManager
            from openai import OpenAI
            
            intel_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            intel_manager = DynamicExtractionManager(intel_client)
            
            # Step 1: Define Requirements (Dynamic)
            intel_manager.add_requirement("Transaction Date", "The date the transaction was posted.")
            intel_manager.add_requirement("Description", "The merchant name or transaction details.")
            intel_manager.add_requirement("Withdrawal Amount", "The amount debited from the account.")
            intel_manager.add_requirement("Deposit Amount", "The amount credited to the account.")
            intel_manager.add_requirement("Check Number", "The check number for check transactions.", required=False)
            
            # Step 2: Discovery (Analyze 2-4 pages)
            sample_text = text[:10000] # Use a significant portion of the start
            print("\n🔍 Phase 2: Structural Discovery (Dynamic Analysis)...")
            self.doc_structure = intel_manager.analyze_document_flow(sample_text)
            print(f"   ✓ Archetype Identified: {self.doc_structure.get('archetype', 'unknown')}")
            
            # Step 3: Schema Generation
            print("🗺️ Phase 3: Dynamic Schema Generation...")
            self.dynamic_schema = intel_manager.generate_dynamic_schema()
            print(f"   ✓ Dynamic Schema Generated Mapping {len(self.dynamic_schema.get('mappings', []))} fields.")
            
            extraction_method = f"{extraction_method} + dynamic-schema"
        except Exception as e:
            print(f"   ⚠️ Dynamic Intelligence Error: {e}")
            self.doc_structure = None
            self.dynamic_schema = None

        # metadata parse (account number, period)
        meta = self._parse_statement_metadata(text)
        metadata = StatementMetadata(
            source_file=source_file,
            extraction_date=datetime.now().isoformat(),
            extraction_method=extraction_method,
            rotated=rotated,
            account_number=meta.get("account_number"),
            period_start=meta.get("period_start"),
            period_end=meta.get("period_end"),
        )

        deposits = self._parse_deposits_and_credits(text)
        debits = self._parse_checks_and_other_debits(text)

        # Prefer structure reconstruction when PDF contains real selectable text.
        # (Even if the text extractor fell back to OCR for any reason.)
        try:
            if self._pdf_has_extractable_text(working_pdf):
                coord_deposits, coord_debits, coord_method = self._extract_with_pdfplumber_coordinates(working_pdf)
                coord_checks = self._extract_pnc_check_summary_pdfplumber(working_pdf)

                expected_dep = self._extract_expected_count(text, r"Zero\s+Balance\s+Transfers\s+(?P<count>\d+)\s+transactions")
                expected_ach = self._extract_expected_count(text, r"ACH\s+Debits\s+(?P<count>\d+)\s+transactions")

                # Check if text-based parsing already found good data with descriptions
                text_deps_have_desc = any(d.get("description") for d in deposits)
                text_debs_have_desc = any(d.get("description") for d in debits)

                # Coordinate data should only override text-based results if:
                # 1) Expected count is known and coordinate matches it exactly, OR
                # 2) Text parser found nothing but coordinates found something, OR
                # 3) Coordinate found strictly more rows AND text data lacks descriptions
                dep_ok = False
                if expected_dep is not None and len(coord_deposits) == expected_dep:
                    dep_ok = True
                elif len(deposits) == 0 and len(coord_deposits) > 0:
                    dep_ok = True
                elif not text_deps_have_desc and len(coord_deposits) > len(deposits):
                    dep_ok = True

                deb_ok = False
                if expected_ach is not None and len(coord_debits) == expected_ach:
                    deb_ok = True
                elif len(debits) == 0 and len(coord_debits) > 0:
                    deb_ok = True
                elif not text_debs_have_desc and len(coord_debits) > len(debits):
                    deb_ok = True

                if dep_ok:
                    deposits = coord_deposits
                if deb_ok:
                    # Preserve parsed check-number rows if coord_debits are ACH-only
                    if coord_method.endswith("tables"):
                        check_rows = [d for d in debits if d.get("check_no")]
                        debits = coord_debits + check_rows
                    else:
                        debits = coord_debits
                # Always merge in coordinate-derived check summary rows (more reliable than OCR text for this section)
                if coord_checks:
                    by_key = {(d.get("check_no"), d.get("amount"), d.get("date")) for d in debits if d.get("check_no")}
                    for r in coord_checks:
                        k = (r.get("check_no"), r.get("amount"), r.get("date"))
                        if k not in by_key:
                            debits.append(r)
                            by_key.add(k)
                if dep_ok or deb_ok:
                    extraction_method = f"{extraction_method} + {coord_method}"
        except Exception:
            pass

        # 3) Finalize data: Deduplication and Sorting
        deposits = self._finalize_deposits(deposits)
        debits = self._finalize_debits(debits)

        # 3b) Adaptive Extraction Fallback
        # If we still have no transactions but discovered a dynamic schema, try adaptive extraction
        if not deposits and not debits and getattr(self, "dynamic_schema", None) and intel_manager:
            try:
                print("⚡ Triggering Adaptive Extraction Fallback...")
                adaptive_txs = intel_manager.execute_extraction(text)
                
                if adaptive_txs:
                    for tx in adaptive_txs:
                        amt = tx.get("amount", 0)
                        # Categorize based on amount sign or section context (handled by LLM standardized output)
                        if amt > 0:
                            deposits.append(tx)
                        elif amt < 0:
                            # Normalize debits to positive for internal format
                            tx["amount"] = abs(amt)
                            debits.append(tx)
                        else:
                            # If zero, look at description or check_no
                            if tx.get("check_no"):
                                debits.append(tx)
                            else:
                                deposits.append(tx)
                                
                    extraction_method = f"{extraction_method} + adaptive-execution"
                    # Re-finalize
                    deposits = self._finalize_deposits(deposits)
                    debits = self._finalize_debits(debits)
                    print(f"   ✓ Adaptive extraction recovered {len(deposits)} deposits and {len(debits)} debits.")
                else:
                    print("   ℹ️ Adaptive extraction returned no transactions.")
            except Exception as e:
                print(f"   ⚠️ Adaptive Extraction Fallback failed: {e}")
                import traceback
                traceback.print_exc()
        elif not deposits and not debits:
            print(f"   ℹ️ Skipping Adaptive Fallback: has_schema={bool(getattr(self, 'dynamic_schema', None))}, has_manager={bool(intel_manager)}")



        # 4) session output dir
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:20]
        file_slug = source_file.replace(" ", "_").replace(".", "_")[:40]
        session_id = f"{timestamp}_{file_slug}"
        session_dir = self.output_dir / f"statement_extraction_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)

        # save artifacts
        extracted_text_file = session_dir / "extracted_text.txt"
        extracted_json_file = session_dir / "extracted_statement.json"
        extracted_excel_file = session_dir / "extracted_statement.xlsx"
        verification_file = session_dir / "verification_package.json"

        extracted_text_file.write_text(text, encoding="utf-8")

        # Ensure metadata reflects any upgraded extraction method (e.g., coords/tables).
        metadata.extraction_method = extraction_method

        payload: Dict[str, Any] = {
            "metadata": asdict(metadata),
            "deposits_and_credits": deposits,
            "checks_and_other_debits": debits,
            "dynamic_intelligence": {
                "structure": getattr(self, "doc_structure", None),
                "schema": getattr(self, "dynamic_schema", None),
            }
        }
        extracted_json_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        self._write_excel(extracted_excel_file, deposits, debits)

        verification = self._build_verification(
            metadata=metadata,
            pages_metadata=pages_metadata,
            deposits=deposits,
            debits=debits,
            raw_text=text,
        )
        verification_file.write_text(json.dumps(verification, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        # cleanup rotation temp
        try:
            shutil.rmtree(temp_rotated_dir, ignore_errors=True)
        except Exception:
            pass

        return {
            "session_dir": str(session_dir),
            "extracted_text_file": str(extracted_text_file),
            "json_file": str(extracted_json_file),
            "excel_file": str(extracted_excel_file),
            "verification_file": str(verification_file),
            "data": payload,
        }

    # ---------------------------
    # Extraction (reuse existing utilities)
    # ---------------------------
    def _extract_text(self, pdf_path: str) -> Tuple[str, List[Dict[str, Any]], str]:
        """
        Reuse the insurance extractor strategy:
          - Prefer hybrid digital extraction (pdfplumber + pymupdf recovery)
          - Fall back to OCR for scanned/low-text PDFs
        """
        # First attempt: hybrid extraction
        try:
            from pdf_plumber import extract_pdf_hybrid

            text, pages_metadata, info = extract_pdf_hybrid(pdf_path)
            # Heuristic: if almost no text, treat as scanned and OCR
            if len(text.strip()) >= 200:
                return text, pages_metadata, info.get("final_method", "pdfplumber")
        except Exception:
            pass

        # OCR fallback
        from ocr_text import OCRPDFExtractor

        ocr_extractor = OCRPDFExtractor(pdf_path)
        text, pages_metadata = ocr_extractor.extract(verbose=False)
        return text, pages_metadata, "tesseract-ocr"

    # ---------------------------
    # Coordinate/table extraction (digital PDFs)
    # ---------------------------
    def _pdf_has_extractable_text(self, pdf_path: str) -> bool:
        """
        Fast heuristic: if pdfplumber can extract a decent number of words from page 1,
        treat it as digital/selectable text.
        """
        try:
            import pdfplumber

            with pdfplumber.open(pdf_path) as pdf:
                if not pdf.pages:
                    return False
                w = pdf.pages[0].extract_words()
                return len(w or []) >= 50
        except Exception:
            return False

    def _extract_with_pdfplumber_coordinates(
        self, pdf_path: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
        """
        Extract deposits and ACH debits using:
          1) pdfplumber find_tables/extract_table with tuned settings
          2) fallback: extract_words() + x/y grouping to rebuild rows

        Returns: (deposits_rows, ach_debit_rows, method_tag)
        """
        import pdfplumber

        deposits: List[Dict[str, Any]] = []
        ach: List[Dict[str, Any]] = []
        zions_deposits: List[Dict[str, Any]] = []
        zions_debits: List[Dict[str, Any]] = []

        # Tuned table settings (works when ruling lines are detectable).
        table_settings = {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "join_tolerance": 3,
            "edge_min_length": 20,
            "min_words_vertical": 1,
            "min_words_horizontal": 1,
        }

        def iter_lines_from_words(page) -> List[Tuple[float, List[Dict[str, Any]]]]:
            words = page.extract_words(
                # use_text_flow=True can drop/reorder table tokens in some PDFs.
                use_text_flow=False,
                keep_blank_chars=False,
                extra_attrs=["x0", "x1", "top", "bottom"],
            )
            if not words:
                return []
            # Group by y (top) within tolerance
            y_tol = 3.0
            words_sorted = sorted(words, key=lambda w: (w["top"], w["x0"]))
            lines: List[Tuple[float, List[Dict[str, Any]]]] = []
            current: List[Dict[str, Any]] = []
            current_y: Optional[float] = None
            for w in words_sorted:
                y = float(w["top"])
                if current_y is None or abs(y - current_y) <= y_tol:
                    current.append(w)
                    current_y = y if current_y is None else current_y
                else:
                    lines.append((float(current_y), sorted(current, key=lambda ww: ww["x0"])))
                    current = [w]
                    current_y = y
            if current:
                lines.append((float(current_y), sorted(current, key=lambda ww: ww["x0"])))
            return lines

        def retokenize_line_chars(line: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            """
            pdfplumber sometimes returns one character per 'word' (common in some PDFs).
            Rebuild tokens by joining adjacent chars based on x-gap.
            Output tokens keep x0/x1 span.
            """
            if not line:
                return []
            # Already tokenized if many multi-char entries exist.
            multi = sum(1 for w in line if len((w.get("text") or "")) > 1)
            if multi >= max(2, len(line) // 4):
                return line

            chars = sorted(line, key=lambda w: float(w["x0"]))
            tokens: List[Dict[str, Any]] = []
            cur_text = ""
            cur_x0 = float(chars[0]["x0"])
            cur_x1 = float(chars[0]["x1"])

            # Estimate typical char gap
            gaps = [float(chars[i + 1]["x0"]) - float(chars[i]["x1"]) for i in range(len(chars) - 1)]
            positive_gaps = [g for g in gaps if g >= 0]
            gap_thresh = 1.2
            if positive_gaps:
                positive_gaps_sorted = sorted(positive_gaps)
                median_gap = positive_gaps_sorted[len(positive_gaps_sorted) // 2]
                gap_thresh = max(1.2, median_gap * 1.8)

            for ch in chars:
                t = (ch.get("text") or "")
                x0 = float(ch["x0"])
                x1 = float(ch["x1"])
                gap = x0 - cur_x1
                if cur_text and gap > gap_thresh:
                    tokens.append({"text": cur_text.strip(), "x0": cur_x0, "x1": cur_x1, "top": ch["top"], "bottom": ch["bottom"]})
                    cur_text = t
                    cur_x0 = x0
                    cur_x1 = x1
                else:
                    cur_text += t
                    cur_x1 = max(cur_x1, x1)

            if cur_text.strip():
                tokens.append({"text": cur_text.strip(), "x0": cur_x0, "x1": cur_x1, "top": chars[-1]["top"], "bottom": chars[-1]["bottom"]})

            # Remove empty tokens
            return [tok for tok in tokens if tok.get("text")]

        def line_text(line: List[Dict[str, Any]]) -> str:
            return " ".join(w["text"] for w in line).strip()

        def _merge_money_tokens(tokens: List[str]) -> List[str]:
            """
            Some PDFs split amounts into multiple tokens like: ['2,879', ',880.74'].
            Merge adjacent tokens into a single money token when possible.
            """
            out: List[str] = []
            i = 0
            while i < len(tokens):
                t = tokens[i]
                if i + 1 < len(tokens):
                    merged = (t + tokens[i + 1]).replace(" ", "")
                    if re.fullmatch(r"\d[\d,]*\.\d{2}", merged):
                        out.append(merged)
                        i += 2
                        continue
                out.append(t)
                i += 1
            return out

        def _find_money_span(line: List[Dict[str, Any]], preferred_x0: Optional[float] = None) -> Optional[Tuple[str, float, float]]:
            """
            Find a money amount in a line. Handles split tokens like ['2,879', ',880.74'].
            Returns (amount_str, x0, x1) where x0/x1 span the merged amount.
            """
            candidates: List[Tuple[str, float, float]] = []
            # single-token money
            for w in line:
                t = (w["text"] or "").replace(" ", "")
                if re.fullmatch(r"\d[\d,]*\.\d{2}", t):
                    candidates.append((t, float(w["x0"]), float(w["x1"])))
            # two-token merged money
            for i in range(len(line) - 1):
                a = (line[i]["text"] or "").replace(" ", "")
                b = (line[i + 1]["text"] or "").replace(" ", "")
                merged = a + b
                if re.fullmatch(r"\d[\d,]*\.\d{2}", merged):
                    candidates.append((merged, float(line[i]["x0"]), float(line[i + 1]["x1"])))
            if not candidates:
                return None
            if preferred_x0 is not None:
                candidates.sort(key=lambda c: abs(c[1] - float(preferred_x0)))
            return candidates[0]

        def parse_date_amount_desc_from_line(line: List[Dict[str, Any]], amount_x0: Optional[float] = None) -> Optional[Dict[str, Any]]:
            # Date token should be near left
            if not line:
                return None
            tokens = [w["text"] for w in line]
            tokens = _merge_money_tokens(tokens)
            date_match = next((t for t in tokens if re.fullmatch(r"\d{2}/\d{2}", t)), None)
            if not date_match:
                return None
            date = date_match

            money_span = _find_money_span(line, preferred_x0=amount_x0)
            if not money_span:
                return None
            amount_str, money_x0, money_x1 = money_span
            amount = self._parse_amount(amount_str)

            # Description: words after the chosen amount (to the right)
            desc_words = [w["text"] for w in line if float(w["x0"]) > float(money_x1) + 1]
            desc = " ".join(desc_words).strip()
            return {"date": date, "amount": amount, "description": desc}

        def parse_date_amount_from_line(line: List[Dict[str, Any]], amount_x0: Optional[float] = None) -> Optional[Dict[str, Any]]:
            if not line:
                return None
            date_tok = None
            for w in line:
                if re.fullmatch(r"\d{2}/\d{2}", w["text"]):
                    date_tok = w
                    break
            if not date_tok:
                return None
            money_span = _find_money_span(line, preferred_x0=amount_x0)
            if not money_span:
                return None
            amount_str, money_x0, money_x1 = money_span
            amount = self._parse_amount(amount_str)
            
            # Description: words after the chosen amount (to the right)
            desc_tokens = [w for w in line if float(w["x0"]) > float(money_x1) + 1]
            desc_text = " ".join(w["text"] for w in desc_tokens).strip()

            return {"date": date_tok["text"], "amount": amount, "check_no": None, "description": desc_text or None}

        with pdfplumber.open(pdf_path) as pdf:
            # Accumulators for Zions column-split layouts (persist across pages)
            z_dep_dates: List[str] = []
            z_dep_amounts: List[float] = []
            z_dep_descs: List[str] = []

            z_deb_dates: List[str] = []
            z_deb_amounts: List[float] = []
            z_deb_descs: List[str] = []

            in_zions_deposits = False
            in_zions_debits = False

            for page in pdf.pages:
                # Attempt table extraction first (if it yields rows with date+amount, use it).
                try:
                    tables = page.find_tables(table_settings=table_settings)
                    for t in tables:
                        data = t.extract()
                        if not data or len(data) < 2:
                            continue
                        header = " ".join(str(c or "") for c in data[0]).lower()
                        if "date" in header and "amount" in header:
                            # Try classify by nearby section title
                            above_text = page.crop((0, max(0, t.bbox[1] - 120), page.width, t.bbox[1])).extract_text() or ""
                            above_lower = above_text.lower()
                            if "deposits" in above_lower and "credits" in above_lower:
                                for row in data[1:]:
                                    if not row:
                                        continue
                                    row_join = " ".join(str(c or "").strip() for c in row).strip()
                                    m = re.match(r"^(\d{2}/\d{2})\s+(\d[\d,]*\.\d{2})\s+(.*)$", row_join)
                                    if m:
                                        deposits.append(
                                            {"date": m.group(1), "amount": self._parse_amount(m.group(2)), "description": m.group(3).strip()}
                                        )
                            if "ach debits" in above_lower or ("checks" in above_lower and "debits" in above_lower):
                                for row in data[1:]:
                                    if not row:
                                        continue
                                    row_join = " ".join(str(c or "").strip() for c in row).strip()
                                    m = re.match(r"^(\d{2}/\d{2})\s+(\d[\d,]*\.\d{2})\b", row_join)
                                    if m:
                                        ach.append({"date": m.group(1), "amount": self._parse_amount(m.group(2)), "check_no": None})
                except Exception:
                    pass

                # Coordinate fallback parsing anchored to DETAIL headings.
                lines = iter_lines_from_words(page)
                deposits_anchor_y: Optional[float] = None
                ach_anchor_y: Optional[float] = None
                deposits_amount_x0: Optional[float] = None
                ach_amount_x0: Optional[float] = None

                for y, line in lines:
                    toks = retokenize_line_chars(line)
                    txt = line_text(toks).lower()
                    if "zero balance transfers" in txt and "transactions" in txt:
                        deposits_anchor_y = y
                    # Chase format: "Deposits and Credits" as section header
                    if deposits_anchor_y is None and "deposits and credits" in txt and "summary" not in txt:
                        deposits_anchor_y = y
                    if "ach debits" in txt and "transactions" in txt:
                        ach_anchor_y = y
                    # Chase format: "Withdrawals and Debits" as section header
                    if ach_anchor_y is None and "withdrawals and debits" in txt and "summary" not in txt:
                        ach_anchor_y = y

                # Learn amount column x positions from nearby header lines if present
                for y, line in lines:
                    toks = retokenize_line_chars(line)
                    txt = line_text(toks).lower()
                    if deposits_anchor_y is not None and y > deposits_anchor_y and y < deposits_anchor_y + 120:
                        if "amount" in txt and "date" in txt:
                            for w in toks:
                                if w["text"].lower() == "amount":
                                    deposits_amount_x0 = float(w["x0"])
                                    break
                    if ach_anchor_y is not None and y > ach_anchor_y and y < ach_anchor_y + 120:
                        if "amount" in txt and "date" in txt:
                            for w in toks:
                                if w["text"].lower() == "amount":
                                    ach_amount_x0 = float(w["x0"])
                                    break

                # Parse rows below anchors until we hit a "continued" footer/header-ish line
                for y, line in lines:
                    toks = retokenize_line_chars(line)
                    txt = line_text(toks).lower()
                    if deposits_anchor_y is not None and y > deposits_anchor_y:
                        if ("continued" in txt and "next page" in txt):
                            deposits_anchor_y = None
                        # Chase/generic: stop deposits at withdrawals/debits/total/daily balance
                        elif re.search(r'\b(withdrawals and debits|checks paid|daily balance)\b', txt):
                            deposits_anchor_y = None
                            # Check if this is the withdrawals header — set ach anchor
                            if 'withdrawals and debits' in txt:
                                ach_anchor_y = y
                        elif txt.strip().startswith('total') and re.search(r'\$[\d,]+\.\d{2}', txt):
                            pass  # Skip total lines
                        else:
                            row = parse_date_amount_desc_from_line(toks, amount_x0=deposits_amount_x0)
                            if row and row.get("amount") is not None:
                                # Ignore ledger balance / summary zones
                                if "ledger" in txt or "balance" in txt:
                                    continue
                                deposits.append(row)

                    if ach_anchor_y is not None and y > ach_anchor_y:
                        if ("continued" in txt and "next page" in txt):
                            ach_anchor_y = None
                        # Chase/generic: stop debits at daily balance/checks paid
                        elif re.search(r'\b(daily balance|checks paid)\b', txt) and 'withdrawals' not in txt:
                            ach_anchor_y = None
                        elif txt.strip().startswith('total') and re.search(r'\$[\d,]+\.\d{2}', txt):
                            pass  # Skip total lines
                        else:
                            row = parse_date_amount_from_line(toks, amount_x0=ach_amount_x0)
                            if row and row.get("amount") is not None:
                                if "ledger" in txt or "balance" in txt:
                                    continue
                                ach.append(row)

                    # Zions-style parsing: track section headers as a state machine
                    if txt.strip() == "deposits/credits":
                        in_zions_deposits = True
                        in_zions_debits = False
                        continue
                    if txt.strip() == "charges/debits":
                        in_zions_deposits = False
                        in_zions_debits = True
                        continue
                    if txt.strip() == "checks processed":
                        in_zions_deposits = False
                        in_zions_debits = False
                        continue

                    # Rows are already in-line when retokenized
                    if in_zions_deposits:
                        # Expect: MM/DD MM/DD AMOUNT DESCRIPTION...
                        m = re.match(
                            r"^(?P<post>\d{2}/\d{2})\s+(?P<eff>\d{2}/\d{2})\s+(?P<amt>\d[\d,]*\.\d{2})\s+(?P<desc>.+)$",
                            txt,
                            flags=re.IGNORECASE,
                        )
                        if m:
                            zions_deposits.append(
                                {
                                    "date": m.group("post"),
                                    "amount": self._parse_amount(m.group("amt")),
                                    "description": m.group("desc").strip(),
                                }
                            )
                            continue

                        # Column-split fallback within deposits: collect (date,date) lines, amount lines, desc lines.
                        m_dates = re.match(r"^(?P<post>\d{2}/\d{2})\s+(?P<eff>\d{2}/\d{2})\s*$", txt)
                        if m_dates:
                            z_dep_dates.append(m_dates.group("post"))
                            continue
                        m_amt = re.match(r"^(?P<amt>(?:\d[\d,]*|0?\.\d+)(?:\.\d{1,2})?)\.?\s*$", txt)
                        if m_amt:
                            a = self._parse_amount(m_amt.group("amt"))
                            if a is not None:
                                z_dep_amounts.append(a)
                            continue
                        # ignore headers
                        if any(h in txt for h in ("posting", "effective", "date", "amount", "description", "continued", "page")):
                            continue
                        if txt.strip():
                            z_dep_descs.append(line_text(toks).strip())
                            continue

                    if in_zions_debits:
                        m = re.match(
                            r"^(?P<post>\d{2}/\d{2})\s+(?P<eff>\d{2}/\d{2})\s+(?P<amt>\d[\d,]*\.\d{2})\s*-\s*(?P<desc>.*)$",
                            txt,
                            flags=re.IGNORECASE,
                        )
                        if m:
                            desc = (m.group("desc") or "").strip()
                            check_no = None
                            mchk = re.search(r"check\s+no:\s*(\d+)", desc, flags=re.IGNORECASE)
                            if mchk:
                                check_no = mchk.group(1)
                            amt = self._parse_amount(m.group("amt"))
                            zions_debits.append(
                                {"date": m.group("post"), "amount": abs(amt) if amt is not None else None, "check_no": check_no}
                            )
                            continue

                        # Column-split fallback for debits
                        m_dates = re.match(r"^(?P<post>\d{2}/\d{2})\s+(?P<eff>\d{2}/\d{2})\s*$", txt)
                        if m_dates:
                            z_deb_dates.append(m_dates.group("post"))
                            continue
                        m_amt = re.match(r"^(?P<amt>(?:\d[\d,]*|0?\.\d+)(?:\.\d{1,2})?)\.?\s*-\s*$", txt)
                        if m_amt:
                            a = self._parse_amount(m_amt.group("amt"))
                            if a is not None:
                                z_deb_amounts.append(abs(a))
                            continue
                        if any(h in txt for h in ("posting", "effective", "date", "amount", "description", "continued", "page")):
                            continue
                        if txt.strip():
                            z_deb_descs.append(line_text(toks).strip())
                            continue

        # Deduplicate while preserving order
        def dedupe(rows: List[Dict[str, Any]], key_fields: Tuple[str, ...]) -> List[Dict[str, Any]]:
            seen = set()
            out = []
            for r in rows:
                k = tuple(r.get(f) for f in key_fields)
                if k in seen:
                    continue
                seen.add(k)
                out.append(r)
            return out

        deposits = dedupe(deposits, ("date", "amount", "description"))
        # Do NOT dedupe ACH by (date, amount): legitimate duplicates happen.

        # If we found obvious over-collection, cap to the maximum "transactions for a total" count within the ACH block.
        # (This mirrors our text-based expected count approach.)
        expected_dep = None
        expected_ach = None
        try:
            # Extract expected counts from the PDF itself (more reliable than OCR text).
            # Deposits: "Zero Balance Transfers 20 transactions ..."
            # ACH: "ACH Debits 245 transactions ..."
            with pdfplumber.open(pdf_path) as pdf2:
                dep_counts: List[int] = []
                ach_counts: List[int] = []
                for pg in pdf2.pages:
                    t = (pg.extract_text() or "").lower()
                    dep_counts += [int(x) for x in re.findall(r"zero balance transfers\s+(\d+)\s+transactions", t)]
                    ach_counts += [int(x) for x in re.findall(r"ach debits\s+(\d+)\s+transactions", t)]
                expected_dep = max(dep_counts) if dep_counts else None
                expected_ach = max(ach_counts) if ach_counts else None
        except Exception:
            pass

        if expected_dep is not None and expected_dep > 0 and len(deposits) > expected_dep:
            deposits = deposits[:expected_dep]
        if expected_ach is not None and expected_ach > 0 and len(ach) > expected_ach:
            ach = ach[:expected_ach]

        # Flush column-split Zions accumulators into rows
        if z_dep_dates and z_dep_amounts and z_dep_descs:
            n = min(len(z_dep_dates), len(z_dep_amounts), len(z_dep_descs))
            for i in range(n):
                zions_deposits.append({"date": z_dep_dates[i], "amount": z_dep_amounts[i], "description": z_dep_descs[i]})

        if z_deb_dates and z_deb_amounts and z_deb_descs:
            n = min(len(z_deb_dates), len(z_deb_amounts), len(z_deb_descs))
            for i in range(n):
                desc = z_deb_descs[i]
                check_no = None
                mchk = re.search(r"Check\s+No:\s*(\d+)", desc, flags=re.IGNORECASE)
                if mchk:
                    check_no = mchk.group(1)
                zions_debits.append({"date": z_deb_dates[i], "amount": z_deb_amounts[i], "check_no": check_no})

        # If we detected Zions-style data, prefer returning that (caller decides whether to use)
        if zions_deposits or zions_debits:
            zions_deposits = dedupe(zions_deposits, ("date", "amount", "description"))
            return zions_deposits, zions_debits, "pdfplumber_coords+zions_tables"

        return deposits, ach, "pdfplumber_coords+tables"

    def _extract_pnc_check_summary_pdfplumber(self, pdf_path: str) -> List[Dict[str, Any]]:
        """
        Extract PNC-style "Check and Substitute Check Summary" using pdfplumber coordinates.
        This recovers check rows that may be truncated/missing in OCR text output.
        Returns rows: {check_no, amount, date}
        """
        import pdfplumber

        def iter_lines(page):
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False, extra_attrs=["x0", "x1", "top", "bottom"])
            if not words:
                return []
            y_tol = 3.0
            ws = sorted(words, key=lambda w: (w["top"], w["x0"]))
            lines = []
            cur = []
            cy = None
            for w in ws:
                y = float(w["top"])
                if cy is None or abs(y - cy) <= y_tol:
                    cur.append(w)
                    cy = y if cy is None else cy
                else:
                    lines.append((float(cy), sorted(cur, key=lambda ww: ww["x0"])))
                    cur = [w]
                    cy = y
            if cur:
                lines.append((float(cy), sorted(cur, key=lambda ww: ww["x0"])))
            return lines

        def retokenize(line):
            # Same char->token logic used elsewhere
            multi = sum(1 for w in line if len((w.get("text") or "")) > 1)
            if multi >= max(2, len(line) // 4):
                return line
            chars = sorted(line, key=lambda w: float(w["x0"]))
            gaps = [float(chars[i + 1]["x0"]) - float(chars[i]["x1"]) for i in range(len(chars) - 1)]
            pos = [g for g in gaps if g >= 0]
            gap_thresh = 1.2
            if pos:
                pos.sort()
                median = pos[len(pos) // 2]
                gap_thresh = max(1.2, median * 1.8)
            toks = []
            cur_text = ""
            cur_x0 = float(chars[0]["x0"])
            cur_x1 = float(chars[0]["x1"])
            for ch in chars:
                t = (ch.get("text") or "")
                x0 = float(ch["x0"])
                x1 = float(ch["x1"])
                gap = x0 - cur_x1
                if cur_text and gap > gap_thresh:
                    if cur_text.strip():
                        toks.append({"text": cur_text.strip(), "x0": cur_x0, "x1": cur_x1})
                    cur_text = t
                    cur_x0 = x0
                    cur_x1 = x1
                else:
                    cur_text += t
                    cur_x1 = max(cur_x1, x1)
            if cur_text.strip():
                toks.append({"text": cur_text.strip(), "x0": cur_x0, "x1": cur_x1})
            return toks

        out: List[Dict[str, Any]] = []
        with pdfplumber.open(pdf_path) as pdf:
            in_summary = False
            for page in pdf.pages:
                for _, raw_line in iter_lines(page):
                    toks = retokenize(raw_line)
                    txt = " ".join(t["text"] for t in toks).lower()
                    if "check and substitute check summary" in txt:
                        in_summary = True
                        continue
                    if in_summary and ("member fdic" in txt or "page" in txt and "of" in txt and "for the period" in txt):
                        # don't hard-stop; just keep scanning
                        pass
                    if not in_summary:
                        continue

                    # Parse repeated patterns: check_no [*] amount date
                    tokens = [t["text"] for t in toks]
                    i = 0
                    while i < len(tokens):
                        # Only treat 7-digit, non-leading-zero tokens as check numbers (PNC checks like 6232002)
                        if re.fullmatch(r"[1-9]\d{6}", tokens[i]):
                            check_no = tokens[i]
                            j = i + 1
                            if j < len(tokens) and tokens[j] == "*":
                                j += 1
                            if j + 1 < len(tokens) and re.fullmatch(r"[\d,]+\.\d{2}", tokens[j]) and re.fullmatch(
                                r"\d{2}/\d{2}", tokens[j + 1]
                            ):
                                out.append(
                                    {
                                        "check_no": check_no,
                                        "amount": self._parse_amount(tokens[j]),
                                        "date": tokens[j + 1],
                                    }
                                )
                                i = j + 2
                                continue
                        i += 1

        return out

    # ---------------------------
    # Parsing helpers
    # ---------------------------
    def _parse_statement_metadata(self, text: str) -> Dict[str, Optional[str]]:
        account_number = None
        period_start = None
        period_end = None

        # Account number: "Account Number: XX-XXXX-6381" or "Account number: ..."
        m = re.search(r"Account\s+Number:\s*([A-Z0-9X\-]+)", text, flags=re.IGNORECASE)
        if m:
            account_number = m.group(1).strip()

        # Period: "For the period 01/01/2026 to 01/31/2026"
        m = re.search(
            r"For\s+the\s+period\s+(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})",
            text,
            flags=re.IGNORECASE,
        )
        if m:
            period_start, period_end = m.group(1), m.group(2)

        # Chase format: "January 01, 2026 through January 30, 2026"
        if not period_start:
            month_names = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            m = re.search(
                rf"({month_names}\s+\d{{1,2}},\s+\d{{4}})\s+through\s+({month_names}\s+\d{{1,2}},\s+\d{{4}})",
                text,
                flags=re.IGNORECASE,
            )
            if m:
                period_start, period_end = m.group(1), m.group(2)

        return {"account_number": account_number, "period_start": period_start, "period_end": period_end}

    def _extract_block(self, text: str, start_marker_re: str, end_marker_res: List[str]) -> str:
        start = re.search(start_marker_re, text, flags=re.IGNORECASE)
        if not start:
            return ""
        start_idx = start.start()

        end_idx = len(text)
        for end_re in end_marker_res:
            m_end = re.search(end_re, text[start_idx:], flags=re.IGNORECASE)
            if m_end:
                end_idx = min(end_idx, start_idx + m_end.start())
        return text[start_idx:end_idx]

    def _parse_deposits_and_credits(self, text: str) -> List[Dict[str, Any]]:
        # Focus on the detailed transaction list (not the summary header which may share a line with debits).
        # Sample anchor: "Zero Balance Transfers 20 transactions for a total of $..."
        m = re.search(r"Zero\s+Balance\s+Transfers\s+\d+\s+transactions\b", text, flags=re.IGNORECASE)
        if not m:
            # Try Chase format before generic
            chase_result = self._parse_deposits_credits_chase(text)
            if chase_result:
                return chase_result
            return self._parse_deposits_credits_generic(text)
        tail = text[m.start():]
        # Some extraction layouts place the continuation "Transaction description" list after the "Checks..." header.
        # Stop at the start of the detailed debits sections instead (e.g. checks totals / ACH totals), not at the header.
        end_candidates = [
            # Stop when the statement transitions into the detailed checks section totals.
            r"\bChecks\s+and\s+Substitute\s+Checks\s+\d+\s+transactions\s+for\s+a\s+total\b",
            r"\b58\s+transactions\s+for\s+a\s+total\b",
        ]
        end_idx = len(tail)
        for pat in end_candidates:
            m_end = re.search(pat, tail, flags=re.IGNORECASE)
            if m_end:
                end_idx = min(end_idx, m_end.start())
        block = tail[:end_idx]

        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        # Phase 1: capture any inline rows "MM/DD <amount> <desc...>"
        inline_rows: List[Dict[str, Any]] = []
        pending_no_desc: List[Dict[str, Any]] = []

        inline_re = re.compile(
            r"^(?P<date>\d{2}/\d{2})\s+(?P<amount>\d[\d,\s]*\.\d{2})\s*(?P<desc>.*)$"
        )
        for ln in lines:
            m = inline_re.match(ln)
            if not m:
                continue
            date = m.group("date")
            amount = self._parse_amount(m.group("amount").replace(" ", ""))
            desc = (m.group("desc") or "").strip()
            if desc:
                inline_rows.append({"date": date, "amount": amount, "description": desc})
            else:
                pending_no_desc.append({"date": date, "amount": amount, "description": ""})

        # Phase 2: for extracts that split description into a separate "Transaction description" column,
        # pair remaining descriptions by order.
        desc_lines: List[str] = []
        in_desc = False
        for ln in lines:
            if re.fullmatch(r"Transaction", ln, flags=re.IGNORECASE):
                in_desc = True
                continue
            if in_desc and re.fullmatch(r"description", ln, flags=re.IGNORECASE):
                continue
            if in_desc:
                # stop if we hit a new major header
                if re.search(r"\bChecks\s+and\s+Other\s+Debits\b", ln, flags=re.IGNORECASE):
                    break
                if re.search(r"\bFor\s+the\s+period\b", ln, flags=re.IGNORECASE):
                    break
                if _DATE_RE.match(ln):
                    # ignore date-only lines (those are handled above)
                    continue
                # ignore generic headers
                if re.fullmatch(r"(Date|posted|Amount|Reference|number|\|)+", ln, flags=re.IGNORECASE):
                    continue
                desc_lines.append(ln.strip())

        # If we have pending rows without description and a description list, pair them.
        if pending_no_desc and desc_lines:
            n = min(len(pending_no_desc), len(desc_lines))
            for i in range(n):
                pending_no_desc[i]["description"] = desc_lines[i]

        # Merge and return in document order (best-effort: inline_rows first, then paired pending)
        return inline_rows + [r for r in pending_no_desc if r.get("description")]

    def _parse_checks_and_other_debits(self, text: str) -> List[Dict[str, Any]]:
        """
        Per requirement: include all debits.
          - ACH debit rows => check_no: null
          - Check summary rows (individual checks) => check_no populated
          - "Checks and Substitute Checks" daily summaries => check_no: null
        """
        debits: List[Dict[str, Any]] = []

        # Generic bank format: CHARGES/DEBITS + CHECKS PROCESSED
        if not re.search(r"\bACH\s+Debits\b", text, flags=re.IGNORECASE) and re.search(
            r"\bCHARGES/DEBITS\b", text, flags=re.IGNORECASE
        ):
            return self._parse_charges_debits_generic(text)

        # Chase format: "Withdrawals and Debits" section
        is_chase = False
        if not re.search(r"\bACH\s+Debits\b", text, flags=re.IGNORECASE) and re.search(
            r"\bWithdrawals\s+and\s+Debits\b", text, flags=re.IGNORECASE
        ):
            is_chase = True
            chase_debits = self._parse_debits_chase(text)
            if chase_debits:
                debits.extend(chase_debits)
        
        # Chase checks: "Checks Paid" section
        if is_chase or re.search(r"\bChecks\s+Paid\b", text, flags=re.IGNORECASE):
            chase_checks = self._parse_checks_paid_chase(text)
            if chase_checks:
                debits.extend(chase_checks)

        # A) ACH Debits transaction list (no check number).
        # Some extracts split the Date and Amount columns; we pair by order.
        ach_block = self._extract_block(text, r"\bACH\s+Debits\b", [r"\bCheck\s+and\s+Substitute\s+Check\s+Summary\b", r"\bMember\s+FDIC\b"])
        if ach_block:
            expected_ach = self._extract_max_expected_count(ach_block, r"(?P<count>\d+)\s+transactions\s+for\s+a\s+total")
            debits.extend(self._parse_ach_debits_columnar(ach_block, expected_count=expected_ach))

        # B) Individual check summary (has check number) near end.
        # Do NOT stop at page markers; the summary spans multiple pages.
        m = re.search(r"\bCheck\s+and\s+Substitute\s+Check\s+Summary\b", text, flags=re.IGNORECASE)
        if m:
            tail = text[m.start():]
            # end at Member FDIC if present, else end of doc
            m_end = re.search(r"\bMember\s+FDIC\b", tail, flags=re.IGNORECASE)
            check_summary_block = tail[: m_end.start()] if m_end else tail
            debits.extend(self._parse_individual_check_summary(check_summary_block))

        return debits

    # ---------------------------
    # Generic statement formats (e.g., Zions/ATX)
    # ---------------------------
    def _extract_section(self, text: str, start_re: str, end_res: List[str]) -> str:
        m = re.search(start_re, text, flags=re.IGNORECASE)
        if not m:
            return ""
        tail = text[m.end() :]
        end_idx = len(tail)
        for e in end_res:
            me = re.search(e, tail, flags=re.IGNORECASE)
            if me:
                end_idx = min(end_idx, me.start())
        return tail[:end_idx]

    def _parse_deposits_credits_chase(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse Chase-format deposits:
          Deposits and Credits
          Ledger    Description                                 Amount
          Date
          01/08     Online Transfer From Chk ...5856 ...        $44,541.86
          01/08     Online Transfer From Chk ...5856 ...        1,540.81
        Multi-line descriptions (continuation lines without date) are merged.
        """
        # Anchor to the detail header (on its own line) to avoid the summary table
        block = self._extract_section(
            text,
            r"(?m)^[ \t]*Deposits\s+and\s+Credits[ \t]*$",
            [r"(?m)^[ \t]*Withdrawals\s+and\s+Debits", r"(?m)^[ \t]*Checks\s+Paid", r"(?m)^[ \t]*Daily\s+Balance"],
        )
        if not block:
            return []
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]

        out: List[Dict[str, Any]] = []
        # Chase row: MM/DD  description text  amount (possibly with $ or without)
        # We allow whitespace after the amount at end of line.
        row_re = re.compile(
            r"^(?P<date>\d{2}/\d{2})\s+(?P<desc>.+?)\s+\$?(?P<amt>\d[\d,]*\.\d{2})\s*$"
        )
        current: Optional[Dict[str, Any]] = None
        for ln in lines:
            # Skip header lines
            if re.match(r"^(Ledger|Date|Deposits\s+and\s+Credits)\b", ln, flags=re.IGNORECASE):
                continue
            # Skip total lines
            if re.match(r"^Total\b", ln, flags=re.IGNORECASE):
                continue
            # Skip page footer lines
            if re.search(r"Page\s+\d+\s+of\s+\d+", ln, flags=re.IGNORECASE):
                continue
            # Skip disclaimer/fine print lines
            if re.match(r"^\*\s", ln) or re.search(r"Annual\s+Percentage\s+Yield", ln, flags=re.IGNORECASE):
                continue
            if re.search(r"Please\s+examine\s+this\s+statement", ln, flags=re.IGNORECASE):
                continue
            if re.search(r"(subject\s+to|notify\s+us|mailing\s+or|availability)", ln, flags=re.IGNORECASE):
                continue
            # Skip (continued) header
            if re.search(r"\(continued\)", ln, flags=re.IGNORECASE):
                continue
            # Account/period headers on continuation pages
            if re.search(r"Account\s+Number:", ln, flags=re.IGNORECASE):
                continue
            if re.search(r"\d{4}\s+through\s+", ln, flags=re.IGNORECASE):
                continue

            m = row_re.match(ln)
            if m:
                if current:
                    current["description"] = current["description"].strip()
                    out.append(current)
                amt = self._parse_amount(m.group("amt"))
                current = {"date": m.group("date"), "amount": amt, "description": m.group("desc").strip()}
            else:
                # Continuation line (no date prefix) — append to current description
                if current:
                    # Robustness: don't append if it looks like a new section or header
                    if not any(h in ln.lower() for h in ("ledger date", "amount", "description")):
                        current["description"] += " " + ln
        if current:
            current["description"] = current["description"].strip()
            out.append(current)
        return out

    def _parse_debits_chase(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse Chase-format debits:
          Withdrawals and Debits
          Ledger    Description                                 Amount
          Date
          01/05     Orig CO Name:Rainy Day Printi ...            $1.14
        Multi-line descriptions (ACH details etc.) are merged.
        """
        # Anchor to the detail header (on its own line) to avoid the summary table
        block = self._extract_section(
            text,
            r"(?m)^[ \t]*Withdrawals\s+and\s+Debits[ \t]*$",
            [r"(?m)^[ \t]*Checks\s+Paid", r"(?m)^[ \t]*Daily\s+Balance", r"(?m)^[ \t]*Daily\s+Ending\s+Balance"],
        )
        if not block:
            return []
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]

        out: List[Dict[str, Any]] = []
        row_re = re.compile(
            r"^(?P<date>\d{2}/\d{2})\s+(?P<desc>.+?)\s+\$?(?P<amt>\d[\d,]*\.\d{2})\s*$"
        )
        current: Optional[Dict[str, Any]] = None
        for ln in lines:
            # Skip headers
            if re.match(r"^(Ledger|Date|Withdrawals\s+and\s+Debits)\b", ln, flags=re.IGNORECASE):
                continue
            if re.match(r"^Total\b", ln, flags=re.IGNORECASE):
                continue
            if re.search(r"Page\s+\d+\s+of\s+\d+", ln, flags=re.IGNORECASE):
                continue
            if re.search(r"\(continued\)", ln, flags=re.IGNORECASE):
                continue
            if re.search(r"Account\s+Number:", ln, flags=re.IGNORECASE):
                continue
            if re.search(r"\d{4}\s+through\s+", ln, flags=re.IGNORECASE):
                continue

            m = row_re.match(ln)
            if m:
                if current:
                    current["description"] = current["description"].strip()
                    out.append(current)
                amt = self._parse_amount(m.group("amt"))
                current = {
                    "date": m.group("date"),
                    "amount": abs(amt) if amt is not None else None,
                    "check_no": None,
                    "description": m.group("desc").strip(),
                }
            else:
                # Continuation line
                if current:
                    if not any(h in ln.lower() for h in ("ledger date", "amount", "description")):
                        current["description"] += " " + ln
        if current:
            current["description"] = current["description"].strip()
            out.append(current)
        return out

    def _parse_checks_paid_chase(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse Chase-format 'Checks Paid' section:
          Checks Paid
          Check No    Date    Amount
          41936       08/06   $14,451.78
        """
        block = self._extract_section(
            text,
            r"(?m)^[ \t]*Checks\s+Paid[ \t]*$",
            [r"(?m)^[ \t]*Daily\s+Balance", r"(?m)^[ \t]*Daily\s+Ending\s+Balance", r"(?m)^[ \t]*Summary\s+of", r"(?m)Page\s+\d+"],
        )
        if not block:
            return []
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        
        out: List[Dict[str, Any]] = []
        # Entries may be columnar. Look for: num [optional *] date amount
        # e.g. "41936 08/06 $14,451.78" or "1002* 01/15 100.00"
        entry_re = re.compile(r"(?P<num>\d{3,})\*?\s+(?P<date>\d{2}/\d{2})\s+\$?(?P<amt>\d[\d,]*\.\d{2})")
        
        for ln in lines:
            if re.match(r"^(Check|No|Date|Amount|Checks\s+Paid)\b", ln, flags=re.IGNORECASE):
                continue
            if re.match(r"^Total\b", ln, flags=re.IGNORECASE):
                continue
            
            for m in entry_re.finditer(ln):
                out.append({
                    "date": m.group("date"),
                    "amount": abs(self._parse_amount(m.group("amt")) or 0.0),
                    "check_no": m.group("num"),
                    "description": f"Check {m.group('num')}"
                })
        return out

    def _parse_deposits_credits_generic(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse sections like:
          DEPOSITS/CREDITS
          Posting Effective
          Date Date Amount Description
          08/01 08/01 15,297.64 WIRE/IN-...;ORG ...
        Output: {date, amount, description} using Posting Date as date.
        """
        block = self._extract_section(
            text,
            r"(?m)^\s*DEPOSITS/CREDITS\s*$",
            [r"(?m)^\s*CHARGES/DEBITS\s*$", r"(?m)^\s*CHECKS\s+PROCESSED\s*$"],
        )
        if not block:
            return []
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]

        out: List[Dict[str, Any]] = []
        row_re = re.compile(
            r"^(?P<post>\d{2}/\d{2})\s+(?P<eff>\d{2}/\d{2})\s+(?P<amt>[\d,\s]*\.\d{2})\s+(?P<desc>.+)$"
        )
        current: Optional[Dict[str, Any]] = None
        for ln in lines:
            m = row_re.match(ln)
            if m:
                if current:
                    current["description"] = current["description"].strip()
                    out.append(current)
                amt = self._parse_amount(m.group("amt").replace(" ", ""))
                current = {"date": m.group("post"), "amount": amt, "description": m.group("desc").strip()}
            else:
                # continuation
                if current and not re.fullmatch(r"(Posting|Effective|Date|Amount|Description)", ln, flags=re.IGNORECASE):
                    # Robustness: if it looks like a new row (starts with date), don't treat as continuation
                    # even if it failed the main row regex (e.g. due to amount OCR error)
                    if not _DATE_RE.match(ln):
                        current["description"] += " " + ln
        if current:
            current["description"] = current["description"].strip()
            out.append(current)
        return out

    def _parse_charges_debits_generic(self, text: str) -> List[Dict[str, Any]]:
        """
        Parse sections like:
          CHARGES/DEBITS
          Posting Effective Date Date Amount Description
          08/04 08/04 1,723.61- Check No: 000000045763
        Also parse:
          CHECKS PROCESSED
          41936 08/06 $14,451.78 ...
        Output: {date, amount, check_no} (check_no may be null).
        """
        out: List[Dict[str, Any]] = []

        # A) CHARGES/DEBITS rows
        block = self._extract_section(
            text,
            r"(?m)^\s*CHARGES/DEBITS\s*$",
            [r"(?m)^\s*CHECKS\s+PROCESSED\s*$", r"(?m)^\s*DAILY\s+BALANCE\s*$"],
        )
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        row_re = re.compile(
            r"^(?P<post>\d{2}/\d{2})\s+(?P<eff>\d{2}/\d{2})\s+(?P<amt>[\d,\s]*\.\d{2})\s*-\s*(?P<desc>.*)$"
        )
        current: Optional[Dict[str, Any]] = None
        for ln in lines:
            m = row_re.match(ln)
            if m:
                if current:
                    out.append(current)
                amt = self._parse_amount(m.group("amt").replace(" ", ""))
                desc = (m.group("desc") or "").strip()
                check_no = None
                mchk = re.search(r"Check\s+No:\s*(\d+)", desc, flags=re.IGNORECASE)
                if mchk:
                    check_no = mchk.group(1)
                current = {"date": m.group("post"), "amount": abs(amt) if amt is not None else None, "check_no": check_no}
            else:
                # continuation lines may contain "Check No:" even if description line got split
                if current and not _DATE_RE.match(ln):
                    mchk = re.search(r"Check\s+No:\s*(\d+)", ln, flags=re.IGNORECASE)
                    if mchk and not current.get("check_no"):
                        current["check_no"] = mchk.group(1)
        if current:
            out.append(current)

        # B) CHECKS PROCESSED table (check_no + date + amount)
        check_block = self._extract_section(
            text,
            r"(?m)^\s*CHECKS\s+PROCESSED\s*$",
            [r"(?m)^\s*ACTIVITY\s+COUNT\s*$", r"(?m)^\s*DAILY\s+BALANCE\s*$"],
        )
        c_lines = [ln.strip() for ln in check_block.splitlines() if ln.strip()]
        # e.g. "41936 08/06 $14,451.78 45763* 08/04 $1,723.61 46187* 08/15 $714.92"
        entry_re = re.compile(r"(?P<num>\d{3,})\*?\s+(?P<date>\d{2}/\d{2})\s+\$?(?P<amt>[\d,]+\.\d{2})")
        for ln in c_lines:
            for m in entry_re.finditer(ln):
                out.append(
                    {
                        "date": m.group("date"),
                        "amount": abs(self._parse_amount(m.group("amt")) or 0.0),
                        "check_no": m.group("num"),
                    }
                )

        return out

    def _parse_daily_check_summaries(self, block: str) -> List[Dict[str, Any]]:
        """
        Lines look like: "01/02 Sum. 2 21,024.27 Summary"
        We capture date + amount; check_no is null.
        """
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        out: List[Dict[str, Any]] = []
        for ln in lines:
            m = re.match(r"^(?P<date>\d{2}/\d{2})\s+Sum\.\s+\d+\s+(?P<amount>[\d,]+\.\d{2})\b", ln)
            if not m:
                continue
            out.append({"date": m.group("date"), "amount": self._parse_amount(m.group("amount")), "check_no": None})
        return out

    def _parse_ach_debits_columnar(self, block: str, expected_count: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Columnar-safe parsing:
        - Collect all MM/DD tokens that appear under an ACH Debits section
        - Collect all monetary amounts that appear under that section
        - Pair by index to produce rows {date, amount, check_no: null}
        This is intentionally description-agnostic because the requirement only needs date/amount/check_no.
        """
        # Restrict to content after the ACH Debits header to reduce noise.
        m = re.search(r"\bACH\s+Debits\b", block, flags=re.IGNORECASE)
        if m:
            block = block[m.end():]

        # Dates: MM/DD (ignore the statement period dates which include year)
        dates = re.findall(r"\b\d{2}/\d{2}\b", block)

        # Amounts: 12.34 / 1,234.56 (allow commas)
        amount_strs = re.findall(r"\b\d[\d,]*\.\d{2}\b", block)
        amounts = [self._parse_amount(a) for a in amount_strs]

        n = min(len(dates), len(amounts))
        if expected_count is not None and expected_count > 0:
            n = min(n, expected_count)
        out: List[Dict[str, Any]] = []
        for i in range(n):
            out.append({"date": dates[i], "amount": amounts[i], "check_no": None})
        return out

    def _parse_individual_check_summary(self, block: str) -> List[Dict[str, Any]]:
        """
        Multi-column summary, entries resemble:
          6232002 * 20,748.71 01/02 009869897
        Repeated 1-3 times per line.
        We capture check_no, amount, date (date paid).
        """
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        out: List[Dict[str, Any]] = []

        # Entries may be multi-column and sometimes split across line breaks; parse over a flattened token stream.
        text = " ".join(lines).replace("|", " ")
        tokens = text.split()

        i = 0
        while i < len(tokens):
            tok = tokens[i]
            # Treat numbers between 4 and 10 digits as potential check numbers.
            # Avoid picking up long reference numbers (e.g. 10+ digits) or 
            # leading-zero patterns that are often sequence identifiers.
            if re.fullmatch(r"[1-9]\d{3,9}", tok):
                check_no = tok
                j = i + 1
                # optional "*"
                if j < len(tokens) and tokens[j] == "*":
                    j += 1

                # find next amount + date within a small window
                amt = None
                dt = None
                k = j
                while k < min(len(tokens), j + 8):
                    if amt is None and re.fullmatch(r"[\d,]+\.\d{2}", tokens[k]):
                        amt = tokens[k]
                        # date usually follows
                        if k + 1 < len(tokens) and re.fullmatch(r"\d{2}/\d{2}", tokens[k + 1]):
                            dt = tokens[k + 1]
                        k += 1
                    elif dt is None and re.fullmatch(r"\d{2}/\d{2}", tokens[k]):
                        dt = tokens[k]
                    k += 1

                if amt and dt:
                    out.append({"date": dt, "amount": abs(self._parse_amount(amt) or 0.0), "check_no": check_no})
                    i = j  # continue scanning after check_no
                else:
                    i += 1
            else:
                i += 1

        return out

    def _parse_amount(self, token: str) -> Optional[float]:
        token = (token or "").strip()
        if not token:
            return None
        # Normalize $ and commas, handle parentheses
        negative = False
        if token.startswith("(") and token.endswith(")"):
            negative = True
            token = token[1:-1]
        token = token.replace("$", "").replace(",", "")
        # OCR sometimes yields trailing dot (e.g., "10650.") or leading dot (".97")
        if token.startswith("."):
            token = "0" + token
        try:
            val = float(token)
            return -val if negative else val
        except Exception:
            return None

    # ---------------------------
    # Excel output
    # ---------------------------
    def _write_excel(self, output_path: Path, deposits: List[Dict[str, Any]], debits: List[Dict[str, Any]]) -> None:
        Alignment, Font, get_column_letter = _safe_import_openpyxl()
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Statement"

        title_font = Font(bold=True, size=12)
        header_font = Font(bold=True)
        wrap = Alignment(wrap_text=True, vertical="top")

        r = 1
        ws.cell(row=r, column=1, value="Deposits and credits").font = title_font
        r += 1
        ws.append(["date", "description", "amount"])
        for c in range(1, 4):
            ws.cell(row=r, column=c).font = header_font
        r += 1
        for row in deposits:
            ws.append([row.get("date"), row.get("description"), row.get("amount")])
            r += 1

        r += 1
        ws.cell(row=r, column=1, value="Checks and other debits").font = title_font
        r += 1
        # Requested layout: date | description | amount | check_no
        ws.append(["date", "description", "amount", "check_no"])
        for c in range(1, 5):
            ws.cell(row=r, column=c).font = header_font
        r += 1
        for row in debits:
            ws.append([
                row.get("date"), 
                row.get("description"), 
                row.get("amount"), 
                row.get("check_no")
            ])
            r += 1

        # basic formatting
        for col in range(1, 5):
            ws.column_dimensions[get_column_letter(col)].width = 22 if col not in (1, 2) else (15 if col == 1 else 60)
        ws.column_dimensions["A"].width = 15 # date
        ws.column_dimensions["B"].width = 60 # description
        ws.column_dimensions["C"].width = 20 # amount
        ws.column_dimensions["D"].width = 20 # check_no

        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=4):
            for cell in row:
                cell.alignment = wrap

        wb.save(str(output_path))

    # ---------------------------
    # Verification
    # ---------------------------
    def _build_verification(
        self,
        metadata: StatementMetadata,
        pages_metadata: List[Dict[str, Any]],
        deposits: List[Dict[str, Any]],
        debits: List[Dict[str, Any]],
        raw_text: str,
    ) -> Dict[str, Any]:
        warnings: List[str] = []

        # Attempt to parse expected counts from the statement summary.
        expected_zero_balance = self._extract_expected_count(raw_text, r"Zero\s+Balance\s+Transfers\s+(?P<count>\d+)\s+transactions")
        expected_ach_debits = self._extract_expected_count(raw_text, r"ACH\s+Debits\s+(?P<count>\d+)\s+transactions")
        expected_checks_total = self._extract_expected_count(raw_text, r"Checks\s+and\s+Substitute\s+Checks\s+(?P<count>\d+)\s+transactions")

        # Our deposits parser is aimed at the detailed list; for the sample it should match Zero Balance Transfers (20)
        if expected_zero_balance is not None and len(deposits) != expected_zero_balance:
            warnings.append(f"Deposits rows ({len(deposits)}) != expected Zero Balance Transfers count ({expected_zero_balance})")

        # Debits are all: daily summaries + ACH + individual check summary; counts will not match a single total.
        # But we can still sanity-check that we found ACH debit rows close to expected_ach_debits.
        parsed_ach = sum(1 for d in debits if d.get("check_no") is None)  # includes daily summaries too
        # We can estimate ACH-only by looking for 'ACH Debits' block and parsing count again is hard without extra field.
        # Keep it light: warn only if we found very few debits.
        if expected_ach_debits is not None and parsed_ach < max(10, expected_ach_debits // 10):
            warnings.append("Parsed very few non-check debits; ACH parsing may have failed.")

        if expected_checks_total is not None:
            parsed_checks_with_numbers = sum(1 for d in debits if d.get("check_no"))
            if parsed_checks_with_numbers < max(5, expected_checks_total // 10):
                warnings.append("Parsed very few check-number rows; check summary parsing may have failed.")

        return {
            "metadata": asdict(metadata),
            "summary": {
                "pages": len(pages_metadata) if pages_metadata else None,
                "deposits_and_credits_rows": len(deposits),
                "checks_and_other_debits_rows": len(debits),
                "checks_with_check_no_rows": sum(1 for d in debits if d.get("check_no")),
            },
            "expected_counts": {
                "zero_balance_transfers": expected_zero_balance,
                "ach_debits": expected_ach_debits,
                "checks_and_substitute_checks": expected_checks_total,
            },
            "warnings": warnings,
            "previews": {
                "deposits_and_credits_first_3": deposits[:3],
                "checks_and_other_debits_first_3": debits[:3],
            },
            "dynamic_intelligence": {
                "structure": getattr(self, "doc_structure", None),
                "schema": getattr(self, "dynamic_schema", None),
            }
        }

    def _finalize_deposits(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate and sort deposits by date."""
        if not rows:
            return []
        
        # Deduplicate
        seen = set()
        unique_rows = []
        for r in rows:
            # Normalize for comparison
            key = (
                str(r.get("date", "")).strip(),
                round(float(r.get("amount", 0) or 0), 2),
                re.sub(r"\s+", " ", str(r.get("description", "")).strip().upper())
            )
            if key not in seen:
                unique_rows.append(r)
                seen.add(key)
        
        # Sort by date (MM/DD)
        return sorted(unique_rows, key=lambda x: str(x.get("date", "")))

    def _finalize_debits(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate and sort debits by check_no (if present), then date."""
        if not rows:
            return []

        # Deduplicate
        seen = set()
        unique_rows = []
        for r in rows:
            # Normalize
            chk = str(r.get("check_no") or "").strip()
            if chk == "None" or not chk: chk = None
            
            # Ensure absolute value for consistent deduplication and display
            amount = abs(float(r.get("amount", 0) or 0))
            r["amount"] = amount  # Update row as well
            
            # Normalize check number by stripping leading zeros for deduplication
            norm_chk = chk.lstrip("0") if chk else None
            if not norm_chk and chk: norm_chk = chk # keep original if it's all zeros? unlikely but safe
            
            key = (
                norm_chk,
                round(amount, 2),
                str(r.get("date", "")).strip(),
                re.sub(r"\s+", " ", str(r.get("description", "")).strip().upper())
            )
            if key not in seen:
                unique_rows.append(r)
                seen.add(key)

        def debit_sort_key(row):
            chk = row.get("check_no")
            # Numeric sort for check numbers if possible
            chk_val = 0
            if chk:
                try:
                    chk_val = int(re.sub(r"\D", "", str(chk)))
                except ValueError:
                    chk_val = 999999999 # Non-numeric at the end
            
            date_val = str(row.get("date") or "99/99")
            # If no check number, we want them at the bottom sorted by date
            is_check = 0 if chk else 1
            return (is_check, chk_val, date_val)

        return sorted(unique_rows, key=debit_sort_key)

    def _extract_expected_count(self, text: str, pattern: str) -> Optional[int]:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            return None
        try:
            return int(m.group("count"))
        except Exception:
            return None

    def _extract_max_expected_count(self, text: str, pattern: str) -> Optional[int]:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if not matches:
            return None
        vals: List[int] = []
        for m in matches:
            try:
                vals.append(int(m))
            except Exception:
                continue
        return max(vals) if vals else None

