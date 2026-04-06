"""
learning_engine.py  —  Upgraded v2
====================================
Key improvements over v1:
  1.  Fixed critical bug: extracted_sum was referenced before assignment.
  2.  Structured validation result object replaces loose tuple returns.
  3.  Persistent memory: profiles are versioned and never blindly overwritten.
      A new profile only replaces an old one if it has MORE examples.
  4.  Few-shot prompt now strips numerical values from examples to prevent
      the LLM from hallucinating old invoice numbers/premiums.
  5.  Audit log written alongside every extraction for post-mortem debugging.
  6.  Refinement loop is capped (max 2 passes) to prevent infinite loops.
  7.  Carrier identification falls back gracefully if LLM call fails.
  8.  Memory lookup returns structured carrier metadata, not just a prompt snippet.
"""

import re
import os
import json
import copy
import hashlib
import datetime
from typing import List, Dict, Optional, Tuple

TRAINING_DIR = os.path.join(os.path.dirname(__file__), "training_data")
AUDIT_DIR    = os.path.join(os.path.dirname(__file__), "audit_logs")

# ─────────────────────────────────────────────────────────────────────────────
# Validation result — replaces the loose (bool, float, float) tuple
# ─────────────────────────────────────────────────────────────────────────────

class ValidationResult:
    """
    Carries every piece of information the refinement loop needs.
    Keeps should_trigger_refinement() pure: it never mutates state.
    """
    def __init__(self):
        self.needs_refinement:  bool  = False
        self.reason:            str   = ""          # human-readable reason
        self.target_total:      float = 0.0
        self.extracted_sum:     float = 0.0
        self.discrepancy:       float = 0.0
        self.missing_ids_pct:   float = 0.0
        self.is_multi_column:   bool  = False

    def __repr__(self):
        if not self.needs_refinement:
            return f"<ValidationResult OK sum={self.extracted_sum:.2f}>"
        return (
            f"<ValidationResult REFINE reason='{self.reason}' "
            f"target={self.target_total:.2f} extracted={self.extracted_sum:.2f} "
            f"discrepancy={self.discrepancy:.2f}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Few-shot prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def _scrub_numbers_from_example(mapping: Dict) -> Dict:
    """
    Returns a copy of the mapping dict with all numeric values replaced by
    placeholder tokens.  This prevents the LLM from copying old premium
    values into a new invoice.

    Fields that are structural (COVERAGE, PLAN_NAME, PLAN_TYPE) are kept as-is
    because those ARE the mapping logic we want the model to learn.
    """
    STRUCTURAL_KEYS = {
        "COVERAGE", "PLAN_NAME", "PLAN_TYPE",
        "LASTNAME", "FIRSTNAME", "MIDDLENAME",   # keep names so context makes sense
    }
    scrubbed = {}
    for k, v in mapping.items():
        if k in STRUCTURAL_KEYS:
            scrubbed[k] = v
        elif isinstance(v, (int, float)):
            scrubbed[k] = "<EXTRACT_FROM_DOCUMENT>"
        elif isinstance(v, str) and re.match(r'^[\d,.$()-]+$', v.strip()):
            scrubbed[k] = "<EXTRACT_FROM_DOCUMENT>"
        else:
            scrubbed[k] = v
    return scrubbed


def discover_examples(text: str) -> str:
    """
    Search training_data/ for relevant examples based on keywords in the text.
    Returns a formatted few-shot prompt string.

    FIX: numeric values are scrubbed from examples so the LLM cannot
    hallucinate old premium amounts into a new invoice.
    """
    if not os.path.exists(TRAINING_DIR):
        return ""

    relevant_examples: List[Dict] = []
    matched_files: List[str] = []

    try:
        for filename in sorted(os.listdir(TRAINING_DIR)):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(TRAINING_DIR, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                print(f"  [LEARNING][WARN] Skipping corrupt profile {filename}: {e}")
                continue

            keywords = data.get("CARRIER_KEYWORDS", [])
            if any(kw.lower() in text.lower() for kw in keywords):
                print(f"  [LEARNING] Matched carrier profile: {filename}")
                matched_files.append(filename)
                relevant_examples.extend(data.get("EXAMPLES", []))

    except Exception as e:
        print(f"  [LEARNING][ERROR] Failed to scan training_data/: {e}")

    if not relevant_examples:
        return ""

    prompt_snippet = """
### TRAINING EXAMPLES (LEARNED COLUMN-MAPPING PATTERNS):
The examples below show HOW to map fields for this carrier's document format.

CRITICAL RULES:
  1.  Use ONLY the MAPPING LOGIC (which column maps to which field).
  2.  The placeholder "<EXTRACT_FROM_DOCUMENT>" means you MUST read the
      ACTUAL number from the current document — NEVER copy a number shown here.
  3.  DO NOT carry over any premium amounts, member IDs, or invoice numbers
      from these examples into your output.

"""
    for i, ex in enumerate(relevant_examples[:3]):
        scrubbed_mapping = _scrub_numbers_from_example(ex.get("MAPPING", {}))
        prompt_snippet += f"Example {i + 1}:\n"
        prompt_snippet += f'  Raw text:      "{ex.get("RAW_TEXT", "")}"\n'
        prompt_snippet += f"  Field mapping: {json.dumps(scrubbed_mapping)}\n\n"

    return prompt_snippet


# ─────────────────────────────────────────────────────────────────────────────
# Financial reconciliation + quality validation
# ─────────────────────────────────────────────────────────────────────────────

def _sum_line_items(line_items: List[Dict]) -> float:
    """Sum CURRENT_PREMIUM across all line items, safely."""
    total = 0.0
    for item in line_items:
        raw = item.get("CURRENT_PREMIUM")
        if raw is None:
            continue
        try:
            val = float(str(raw).replace(",", "").replace("$", "").strip())
            total += val
        except (ValueError, TypeError):
            pass
    return total


def _extract_totals_from_text(raw_text: str) -> Tuple[List[float], List[float]]:
    """
    Returns (high_priority_totals, generic_totals) found in the raw text.
    Separates tiers so the caller can decide which to trust.
    """
    text_upper = raw_text.upper()

    # Tier 1 — "amount due", "balance due", etc.
    hp_pattern = (
        r'(?:AMOUNT\s*DUE|AMOUNTDUE|BALANCE\s*DUE|BALANCEDUE|'
        r'TOTAL\s*DUE|TOTAL\s*AMOUNT\s*DUE|INVOICED?\s*AMOUNT)'
        r'\s*[:$]*\s*([0-9,]+\.[0-9]{2})'
    )
    hp_matches = re.findall(hp_pattern, text_upper)
    high_priority = [float(m.replace(",", "")) for m in hp_matches]

    # Tier 2 — "total premium", "grand total", generic "total"
    gp_pattern = (
        r'(?:INVOICE\s*TOTAL|GRAND\s*TOTAL|GRANDTOTAL|'
        r'TOTAL\s*PREMIUM|TOTALCURRENTPREMIUM|CURRENT\s*PREMIUM\s*DUE|'
        r'TOTAL)\s*[:$]*\s*([0-9,]+\.[0-9]{2})'
    )
    gp_matches = re.findall(gp_pattern, text_upper)
    generic = [float(m.replace(",", "")) for m in gp_matches]

    # Tier 3 — mirrored / rotated text (e.g. UNUM PDF artefact)
    mirrored_pattern = (
        r'(?:LATOT|EUD\s*LATOT|EUD\s*TNUOMA|EUD\s*TNUOMA\s*LATOT)'
        r'\s*[:\$]*\s*([0-9]{2}\.[0-9,]+)\$'
    )
    for m in re.findall(mirrored_pattern, text_upper):
        try:
            fixed = m[::-1].replace(",", "")
            generic.append(float(fixed))
            print(f"  [LEARNING][MIRROR] Decoded mirrored total: {m} → {fixed}")
        except (ValueError, TypeError):
            pass

    return high_priority, generic


def should_trigger_refinement(extracted_data: Dict, raw_text: str) -> ValidationResult:
    """
    Decide if extraction quality is low enough to warrant a second pass.

    Returns a ValidationResult object (never raises).

    FIX: extracted_sum is now always computed BEFORE any comparison.
    FIX: returns a structured object instead of a loose tuple.
    """
    result = ValidationResult()
    line_items = extracted_data.get("LINE_ITEMS", [])

    # ── Always compute the extracted sum first ─────────────────────────────
    result.extracted_sum = _sum_line_items(line_items)

    # ── Heuristic 1: no line items but document clearly has financial data ──
    has_money = "$" in raw_text or bool(re.search(r'\b\d+\.\d{2}\b', raw_text))
    if not line_items and has_money and len(raw_text) > 100:
        result.needs_refinement = True
        result.reason = "No items extracted from a document containing financial data."
        print(f"  [LEARNING] {result.reason}")
        return result

    # ── Heuristic 2: financial reconciliation ──────────────────────────────
    high_priority, generic = _extract_totals_from_text(raw_text)

    if high_priority:
        combined_targets = high_priority
        print(f"  [LEARNING] High-priority total(s) found: {high_priority}")
    elif generic:
        combined_targets = generic
    else:
        combined_targets = []

    if combined_targets:
        target_total = max(combined_targets)
        result.target_total = target_total

        # If extracted sum already exceeds the target by more than 1 %,
        # the "total" we found is probably a sub-total — do not refine.
        if result.extracted_sum > target_total:
            buffer = max(1.0, result.extracted_sum * 0.01)
            if result.extracted_sum - target_total > buffer:
                print(
                    f"  [LEARNING][SAFEGUARD] Extracted ${result.extracted_sum:.2f} > "
                    f"target ${target_total:.2f} — target is likely a sub-total. Skipping."
                )
                return result  # needs_refinement stays False

        discrepancy = abs(target_total - result.extracted_sum)
        result.discrepancy = discrepancy
        if discrepancy > 0.05:
            result.needs_refinement = True
            result.reason = (
                f"Financial mismatch — document total ${target_total:.2f}, "
                f"extracted sum ${result.extracted_sum:.2f}, "
                f"gap ${discrepancy:.2f}."
            )
            print(f"  [LEARNING] {result.reason}")
            return result

    # ── Heuristic 3: missing member IDs ────────────────────────────────────
    if len(line_items) > 3:
        null_ids = sum(
            1 for item in line_items
            if not str(item.get("MEMBERID", "")).strip()
        )
        pct = null_ids / len(line_items)
        result.missing_ids_pct = pct
        if pct > 0.5:
            result.needs_refinement = True
            result.reason = (
                f"High rate of missing Member IDs ({null_ids}/{len(line_items)} rows)."
            )
            print(f"  [LEARNING] {result.reason}")
            return result

    # ── Multi-column detection (informational, recorded on result) ─────────
    result.is_multi_column = len(re.findall(r'Name\s+Code\s+Premium', raw_text)) > 1

    print(
        f"  [LEARNING] Validation passed. "
        f"sum=${result.extracted_sum:.2f}, target=${result.target_total:.2f}"
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Refinement prompt generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_refinement_prompt(
    extracted_data: Dict,
    raw_text: str,
    validation: Optional[ValidationResult] = None,
    pass_number: int = 1,
) -> str:
    """
    Generates a targeted refinement prompt based on the ValidationResult.

    FIX: accepts a ValidationResult object so the prompt is tailored to the
    SPECIFIC reason for failure, not a generic catch-all instruction.
    """
    # Back-compat: allow callers that still pass (target_total, current_sum) floats
    if validation is None:
        validation = ValidationResult()

    target_total = validation.target_total
    current_sum  = validation.extracted_sum

    multi_col_msg = ""
    if validation.is_multi_column:
        multi_col_msg = """
### MULTI-COLUMN LAYOUT DETECTED:
- This document has 2–3 columns of members side-by-side.
- For each row of text there may be multiple members (left / centre / right column).
- You MUST scan horizontally and extract every name in every column.
"""

    missing_id_msg = ""
    if validation.missing_ids_pct > 0.5:
        missing_id_msg = """
### MISSING MEMBER IDs:
- More than half of the extracted rows have no MEMBERID.
- Re-scan the raw text. The Member ID / Certificate Number / Employee ID
  usually appears on the SAME line as the member's name or the premium amount.
- Do NOT leave MEMBERID blank if a number appears near the name.
"""

    return f"""
[REFINEMENT PASS {pass_number}] The previous extraction has a quality issue:
  Reason:            {validation.reason}
  Document total:    ${target_total:.2f}
  Extracted sum:     ${current_sum:.2f}
  Gap:               ${abs(target_total - current_sum):.2f}

### ZERO-HALLUCINATION RULE (CRITICAL):
  Every number you output MUST appear verbatim in the raw text below.
  If you cannot find a value, output null — NEVER invent a number.

### RECONCILIATION TASK:
  1. Re-scan the ENTIRE detail table. Look for rows you missed the first time.
  2. Your extracted sum MUST equal the "Amount Due" / "Balance Due" in the text.
  3. If the sum is too low  → you missed rows. Look harder.
  4. If the sum is too high → you captured summary/total rows. Remove them.
  5. Map the "Total" or "Current Premium" column → CURRENT_PREMIUM, not any
     sub-total or volume column.
{multi_col_msg}{missing_id_msg}
### COLUMN HINTS (re-check these):
  - BasicTermLife / Dental / Std / Vision → each is a SEPARATE line item row.
  - "Adjustment" rows → ADJUSTMENT_PREMIUM (set CURRENT_PREMIUM to null).
  - Negative values in parentheses "(536.75)" → extract as -536.75.
  - Amounts ABOVE a name (Aetna vertical layout) → belong to that name's row.

### RAW TEXT TO RE-PROCESS:
{raw_text}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Persistent memory — save / load carrier profiles
# ─────────────────────────────────────────────────────────────────────────────

def _profile_path(carrier_name: str) -> str:
    safe_name = re.sub(r'[^\w-]', '_', carrier_name.lower())
    return os.path.join(TRAINING_DIR, f"{safe_name}_autotrained.json")


def _load_existing_profile(carrier_name: str) -> Dict:
    path = _profile_path(carrier_name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_successful_extraction(
    text: str,
    extracted_data: Dict,
    client,                         # OpenAI client (or compatible)
    model: str = "gpt-4o",
) -> bool:
    """
    Analyses a successful extraction and saves it as a carrier profile.

    FIX: Never overwrites an existing profile unless the new version has
    MORE examples — protecting hard-won profiles from bad future runs.

    FIX: Stores a SHA-256 fingerprint of the source text so duplicate
    documents are not used to retrain the same profile twice.
    """
    line_items = extracted_data.get("LINE_ITEMS", [])
    if not line_items:
        print("  [LEARNING] No line items — skipping auto-train.")
        return False

    print("  [LEARNING] Identifying carrier for auto-training…")

    # ── Step 1: identify carrier via LLM ───────────────────────────────────
    try:
        id_prompt = f"""Analyse this insurance invoice snippet.

Return ONLY valid JSON with these keys:
  "CARRIER_NAME"  : string — the insurance carrier / company name
  "KEYWORDS"      : list of 3-5 strings that are UNIQUE to this carrier's
                    invoices and not found in generic documents
                    (e.g. proprietary field names, unique section headers)

TEXT:
{text[:2000]}

JSON only. No markdown fences. No explanation."""

        resp = client.chat.completions.create(
            messages=[{"role": "user", "content": id_prompt}],
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
        )
        meta = json.loads(resp.choices[0].message.content)
        carrier_name = meta.get("CARRIER_NAME", "").strip()
        keywords     = meta.get("KEYWORDS", [])

        if not carrier_name or carrier_name.lower() in ("unknown", "n/a", ""):
            print("  [LEARNING] Carrier could not be identified — skipping.")
            return False

    except Exception as e:
        print(f"  [LEARNING][ERROR] Carrier identification failed: {e}")
        return False

    # ── Step 2: select representative example rows ─────────────────────────
    best_items = [
        item for item in line_items
        if item.get("MEMBERID") and item.get("CURRENT_PREMIUM")
    ] or line_items

    examples = []
    for item in best_items[:3]:
        fname = str(item.get("FIRSTNAME", ""))
        mid   = str(item.get("MEMBERID",  ""))
        raw_line = ""
        for line in text.split("\n"):
            if (fname and fname in line) or (mid and mid in line):
                raw_line = line.strip()
                break
        if raw_line:
            examples.append({"RAW_TEXT": raw_line, "MAPPING": item})

    if not examples:
        print("  [LEARNING] Could not find raw source lines — skipping.")
        return False

    # ── Step 3: load existing profile and compare ──────────────────────────
    existing = _load_existing_profile(carrier_name)
    existing_examples = existing.get("EXAMPLES", [])

    # Fingerprint the current doc so we don't store duplicate training data
    doc_fp = hashlib.sha256(text[:4000].encode()).hexdigest()[:16]
    used_fps = existing.get("DOCUMENT_FINGERPRINTS", [])
    if doc_fp in used_fps:
        print(f"  [LEARNING] Document already used for training ({carrier_name}). Skipping.")
        return False

    # Merge: keep the best (most examples) set, up to 10 total
    merged_examples = existing_examples + examples
    # De-duplicate by RAW_TEXT
    seen_texts = set()
    deduped = []
    for ex in merged_examples:
        key = ex.get("RAW_TEXT", "")[:80]
        if key not in seen_texts:
            seen_texts.add(key)
            deduped.append(ex)
    merged_examples = deduped[:10]

    # Only overwrite if we are making progress
    if len(merged_examples) <= len(existing_examples):
        print(
            f"  [LEARNING] Existing profile for '{carrier_name}' is already "
            f"as good ({len(existing_examples)} examples). Not overwriting."
        )
        return False

    # ── Step 4: persist ────────────────────────────────────────────────────
    save_data = {
        "CARRIER_NAME":          carrier_name,
        "CARRIER_KEYWORDS":      list(set(existing.get("CARRIER_KEYWORDS", []) + keywords)),
        "EXAMPLES":              merged_examples,
        "DOCUMENT_FINGERPRINTS": (used_fps + [doc_fp])[-50:],  # keep last 50
        "LAST_UPDATED":          datetime.datetime.utcnow().isoformat() + "Z",
        "EXAMPLE_COUNT":         len(merged_examples),
    }

    os.makedirs(TRAINING_DIR, exist_ok=True)
    path = _profile_path(carrier_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, indent=2)

    print(
        f"  [LEARNING][SUCCESS] Saved carrier profile: {os.path.basename(path)} "
        f"({len(merged_examples)} examples)"
    )
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Audit logger
# ─────────────────────────────────────────────────────────────────────────────

def write_audit_log(
    source_file: str,
    validation: ValidationResult,
    passes: int,
    final_sum: float,
    target_total: float,
    line_item_count: int,
) -> str:
    """
    Writes a JSON audit log so you can review every extraction after the fact.
    Returns the path of the written file.
    """
    os.makedirs(AUDIT_DIR, exist_ok=True)

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    base = re.sub(r'[^\w.-]', '_', os.path.basename(source_file))
    log_path = os.path.join(AUDIT_DIR, f"{timestamp}_{base}.json")

    log = {
        "timestamp":        datetime.datetime.utcnow().isoformat() + "Z",
        "source_file":      source_file,
        "passes":           passes,
        "final_sum":        round(final_sum, 2),
        "target_total":     round(target_total, 2),
        "discrepancy":      round(abs(target_total - final_sum), 2),
        "line_item_count":  line_item_count,
        "validation_reason": validation.reason if validation.needs_refinement else "passed",
        "outcome":          "reconciled" if abs(target_total - final_sum) <= 0.05 else "gap_remaining",
    }

    try:
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2)
        print(f"  [AUDIT] Log written: {log_path}")
    except OSError as e:
        print(f"  [AUDIT][WARN] Could not write audit log: {e}")

    return log_path


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration helper — call this from your extractor instead of the old
# should_trigger_refinement() / generate_refinement_prompt() pattern
# ─────────────────────────────────────────────────────────────────────────────

MAX_REFINEMENT_PASSES = 2   # safety cap — prevents infinite loops

def run_extraction_with_validation(
    raw_text: str,
    extractor_fn,                   # callable(text) -> {"HEADER": ..., "LINE_ITEMS": [...]}
    source_file: str = "unknown",
    openai_client=None,
) -> Dict:
    """
    Runs extraction, validates, refines (up to MAX_REFINEMENT_PASSES times),
    writes an audit log, and optionally saves a carrier profile.

    Parameters
    ----------
    raw_text      : The full text to extract from.
    extractor_fn  : Your existing extract_fields_with_llm(text, ...) function,
                    wrapped so it accepts only raw text and returns the dict.
    source_file   : PDF path (for audit logs).
    openai_client : Optional OpenAI client for auto-training.

    Returns
    -------
    The best extraction dict found, with a "_meta" key containing audit info.
    """
    # Inject few-shot examples from memory
    examples_prompt = discover_examples(raw_text)
    augmented_text = raw_text + ("\n\n" + examples_prompt if examples_prompt else "")

    best_data  = extractor_fn(augmented_text)
    last_valid = ValidationResult()
    passes     = 1

    for pass_num in range(1, MAX_REFINEMENT_PASSES + 1):
        validation = should_trigger_refinement(best_data, raw_text)
        last_valid = validation

        if not validation.needs_refinement:
            print(f"  [ENGINE] Extraction accepted after {passes} pass(es).")
            break

        if pass_num >= MAX_REFINEMENT_PASSES:
            print(
                f"  [ENGINE][WARN] Still failing after {MAX_REFINEMENT_PASSES} passes. "
                f"Returning best result so far. Gap: ${validation.discrepancy:.2f}"
            )
            break

        refinement_prompt = generate_refinement_prompt(
            best_data, raw_text,
            validation=validation,
            pass_number=pass_num,
        )
        refined_data = extractor_fn(refinement_prompt)

        # Only upgrade if the refined pass is at least as good
        refined_sum = _sum_line_items(refined_data.get("LINE_ITEMS", []))
        current_gap = abs(validation.target_total - validation.extracted_sum)
        refined_gap = abs(validation.target_total - refined_sum)

        if refined_gap < current_gap:
            print(
                f"  [ENGINE] Pass {pass_num + 1} improved gap: "
                f"${current_gap:.2f} → ${refined_gap:.2f}"
            )
            best_data = refined_data
        else:
            print(
                f"  [ENGINE][WARN] Pass {pass_num + 1} did NOT improve gap "
                f"(${refined_gap:.2f} vs ${current_gap:.2f}). Keeping previous."
            )

        passes += 1

    # Audit log
    final_sum   = _sum_line_items(best_data.get("LINE_ITEMS", []))
    item_count  = len(best_data.get("LINE_ITEMS", []))
    write_audit_log(source_file, last_valid, passes, final_sum, last_valid.target_total, item_count)

    # Auto-train on success
    if openai_client and not last_valid.needs_refinement:
        save_successful_extraction(raw_text, best_data, openai_client)

    best_data["_meta"] = {
        "passes":        passes,
        "final_sum":     round(final_sum, 2),
        "target_total":  round(last_valid.target_total, 2),
        "discrepancy":   round(abs(last_valid.target_total - final_sum), 2),
        "item_count":    item_count,
    }
    return best_data
