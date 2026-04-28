"""
Enhanced Insurance Form Extractor with PyMuPDF + Tesseract
Features:
- Direct PDF text extraction with PyMuPDF
- Tesseract OCR for scanned content
- Layout-aware structure preservation
- Schema extraction with GPT-4
- User verification of extracted text
"""

import os
import json
import base64
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import re
from pathlib import Path
import subprocess
import sys
from io import BytesIO

# Dynamic year filter: Only include claims from the last 5 years (inclusive)
# e.g., if 2026, include 2022, 2023, 2024, 2025, 2026
MIN_INCLUDED_CLAIM_YEAR = datetime.now().year - 4

def filter_claims_by_claim_year(
    claims: List[Dict],
    *,
    min_year_inclusive: int = MIN_INCLUDED_CLAIM_YEAR,
    keep_unknown_year: bool = True,
) -> tuple[list[Dict], list[Dict], list[Dict]]:
    """
    Split claims into (included, excluded, unknown_year) by claim_year.

    - included: claim_year is None (if keep_unknown_year) OR claim_year >= min_year_inclusive
    - excluded: claim_year is not None AND claim_year < min_year_inclusive
    - unknown_year: claim_year is None (always returned separately)
    """
    included: list[Dict] = []
    excluded: list[Dict] = []
    unknown: list[Dict] = []

    for c in claims or []:
        year = c.get("claim_year")
        if year is None:
            unknown.append(c)
            if keep_unknown_year:
                included.append(c)
            # If keep_unknown_year=False, don't add to included OR excluded
            continue

        try:
            y = int(year)
        except Exception:
            # Treat non-parsable year as unknown
            unknown.append(c)
            if keep_unknown_year:
                included.append(c)
            else:
                excluded.append(c)
            continue

        if y >= int(min_year_inclusive):
            included.append(c)
        else:
            excluded.append(c)

    return included, excluded, unknown

try:
    import fitz  # PyMuPDF
    from PIL import Image
    import pytesseract
    from pdf2image import convert_from_path
    from openai import OpenAI
except ImportError:
    print("Installing required packages...")
    packages = ["pymupdf", "pytesseract", "Pillow", "openai", "pdf2image"]
    for pkg in packages:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
    import fitz
    from PIL import Image
    import pytesseract
    from openai import OpenAI
    from pdf2image import convert_from_path

# Fix for "Decompression Bomb" error in PIL
Image.MAX_IMAGE_PIXELS = None


@dataclass
class PageExtraction:
    """Data for a single page"""
    page_number: int
    image_path: str
    raw_text: str
    orientation: str  # 'portrait' or 'landscape'
    is_scanned: bool
    confidence: float


@dataclass
class InsuranceClaim:
    """Insurance Claim Data Structure"""
    employee_name: Optional[str] = None
    claim_number: Optional[str] = None
    injury_date_time: Optional[str] = None
    claim_year: Optional[int] = None
    status: Optional[str] = None
    injury_description: Optional[str] = None
    body_part: Optional[str] = None
    injury_type: Optional[str] = None
    claim_class: Optional[str] = None
    medical_paid: Optional[float] = None
    medical_reserve: Optional[float] = None
    indemnity_paid: Optional[float] = None
    indemnity_reserve: Optional[float] = None
    expense_paid: Optional[float] = None
    expense_reserve: Optional[float] = None
    total_paid: Optional[float] = None
    total_reserve: Optional[float] = None
    total_incurred: Optional[float] = None
    litigation: Optional[str] = "N"
    reopen: Optional[str] = "False"
    carrier_name: Optional[str] = None
    policy_number: Optional[str] = None
    confidence_score: Optional[float] = None
    


@dataclass
class LossRunReport:
    """Complete Loss Run Report with multiple claims"""
    policy_number: Optional[str] = None
    carrier_name: Optional[str] = None
    insured_name: Optional[str] = None
    report_date: Optional[str] = None
    policy_period: Optional[str] = None
    claims: Optional[List[InsuranceClaim]] = None
    extraction_metadata: Optional[Dict] = None
    
    def __post_init__(self):
        if self.claims is None:
            self.claims = []
    
    @property
    def total_claims(self) -> int:
        """Return total number of claims"""
        return len(self.claims) if self.claims else 0
    
    @property
    def total_incurred_all(self) -> float:
        """Return sum of all incurred amounts"""
        if not self.claims:
            return 0.0
        return sum(claim.total_incurred or 0 for claim in self.claims)


class EnhancedInsuranceExtractor:
    """Enhanced extractor with layout awareness and verification"""
    
    def __init__(self, api_key: Optional[str] = None, output_dir: Optional[str] = None):
        """Initialize with OpenAI API"""
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
            print("✓ GPT-4 Vision API initialized")
        else:
            raise ValueError("OPENAI_API_KEY is required for enhanced extraction")
        
        self.output_dir = Path(output_dir) if output_dir else Path("outputs")
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def extract_text_from_pdf(self, pdf_path: str) -> Tuple[str, List[Dict]]:
        """
        Extract text from PDF using detection and appropriate extraction method.
        """
        from pdf_detector import PDFDetector
        from config import config
        
        try:
            print(f"🔍 Detecting PDF type...")
            detector = PDFDetector(pdf_path)
            is_scanned = detector.is_scanned()
            
            # Check if we should use Vision for scanned PDFs
            use_vision = getattr(config, 'OCR_ENGINE', 'tesseract') == 'vision'
            
            # STAGE 2 & 3: OCR Fallback (Schema -> Tesseract -> Vision)
            if is_scanned:
                print(f"📸 SCANNED PDF DETECTED: Starting OCR Pipeline...")
                
                # FIRST ATTEMPT: Rostaing-OCR (SchemaOCRExtractor)
                try:
                    print(f"   🚀 ATTEMPT 1: Schema OCR (rostaing-ocr layout preservation)...")
                    from schema_ocr import SchemaOCRExtractor
                    
                    schema_extractor = SchemaOCRExtractor(str(pdf_path), api_key=self.api_key)
                    # Use the structure-preserving method
                    rostaing_text = schema_extractor.extract_layout_text(save_debug_output=True)
                    
                    # Validate we actually got usable text
                    if rostaing_text and len(rostaing_text.strip()) > 50:
                        print(f"   ✅ Schema OCR succeeded. Extracted {len(rostaing_text)} characters.")
                        # Provide a single-page mocked metadata block to satisfy downstream expectations
                        mock_metadata = [{
                            "page_number": 1,
                            "text": rostaing_text,
                            "is_scanned": True,
                            "extraction_method": "schema-ocr-rostaing",
                            "confidence": 0.95
                        }]
                        return rostaing_text, mock_metadata
                    else:
                        print(f"   ⚠️ Schema OCR yielded insufficient text. Falling back...")
                        
                except Exception as e:
                    print(f"   ⚠️ Schema OCR failed ({e}). Falling back...")

                # SECOND ATTEMPT: Existing Tesseract -> Vision 
                print(f"   🚀 ATTEMPT 2: Fallback to OCR Pipeline (Tesseract Multi-DPI -> Vision)...")
                from ocr_text import OCRPDFExtractor
                ocr_extractor = OCRPDFExtractor(pdf_path)
                return ocr_extractor.extract(
                    dpi=getattr(config, 'OCR_DPI', 600),
                    psm_mode=getattr(config, 'OCR_PSM_MODE', 3)
                )
            else:
                print(f"📄 DIGITAL PDF DETECTED: Stage 1 (Hybrid Native Extraction)...")
                from pdf_plumber import extract_pdf_hybrid
                text, metadata, info = extract_pdf_hybrid(pdf_path)
                
                # Check quality and detect reversal in native extraction
                from text_quality_verifier import TextQualityVerifier
                verifier = TextQualityVerifier()
                num_pages = info.get('num_pages', len(metadata)) or len(metadata)
                
                # Check for reversal in native text if possible
                if text.strip() and verifier.analyze_quality(text, num_pages).get('metrics', {}).get('reversed_marker_count', 0) >= 2:
                    print(f"   ⚠️ Native extraction appears reversed. Attempting correction...")
                    from pdf_plumber import _reverse_text_block
                    text = _reverse_text_block(text)
                    # Update metadata text too
                    for p_meta in metadata:
                        p_meta["text"] = _reverse_text_block(p_meta["text"])
                
                # Re-verify quality after potential correction
                quality = verifier.analyze_quality(text, num_pages)
                
                if not quality['is_acceptable']:
                    print(f"   ⚠️ Native extraction quality low: {quality['reason']}")
                    print(f"   🚀 Falling back to OCR Pipeline...")
                    
                    # FIRST ATTEMPT: Rostaing-OCR (SchemaOCRExtractor)
                    try:
                        print(f"   🚀 ATTEMPT 1: Schema OCR (rostaing-ocr layout preservation)...")
                        from schema_ocr import SchemaOCRExtractor
                        
                        schema_extractor = SchemaOCRExtractor(str(pdf_path), api_key=self.api_key)
                        rostaing_text = schema_extractor.extract_layout_text(save_debug_output=True)
                        
                        if rostaing_text and len(rostaing_text.strip()) > 50:
                            print(f"   ✅ Schema OCR succeeded. Extracted {len(rostaing_text)} characters.")
                            mock_metadata = [{
                                "page_number": 1,
                                "text": rostaing_text,
                                "is_scanned": False,
                                "extraction_method": "schema-ocr-rostaing-fallback",
                                "confidence": 0.95
                            }]
                            return rostaing_text, mock_metadata
                        else:
                            print(f"   ⚠️ Schema OCR yielded insufficient text. Falling back...")
                            
                    except Exception as e:
                        print(f"   ⚠️ Schema OCR failed ({e}). Falling back...")

                    # SECOND ATTEMPT: Existing Tesseract -> Vision 
                    print(f"   🚀 ATTEMPT 2: Fallback to OCR Pipeline (Tesseract Multi-DPI -> Vision)...")
                    from ocr_text import OCRPDFExtractor
                    ocr_extractor = OCRPDFExtractor(pdf_path)
                    return ocr_extractor.extract(
                        dpi=getattr(config, 'OCR_DPI', 600),
                        psm_mode=getattr(config, 'OCR_PSM_MODE', 3)
                    )
                
                print(f"   ✅ Native extraction quality acceptable (Score: {verifier.quality_score(text, num_pages):.2f}).")
                
                # STAGE 1.5: Patch individual bad pages if needed
                from vision_recovery import VisionRecoveryHandler
                recovery_handler = VisionRecoveryHandler(self.client)
                text = recovery_handler.patch_text_with_vision(pdf_path, metadata)
                
                return text, metadata
                
        except Exception as e:
            print(f"⚠️ Detection/Extraction error: {e}")
            print(f"   Falling back to standard pdfplumber...")
            from pdf_plumber import extract_pdf_with_pdfplumber as external_extract
            return external_extract(pdf_path)

    def _infer_carrier_from_text(self, text: str) -> Optional[str]:
        """
        Heuristically infer the carrier/insurer name from the raw text.
        This is a dynamic, format-agnostic fallback that:
        - First looks for explicit 'Insurance ...' style names in the header region
        - Otherwise, falls back to using a website/domain found near the top
        """
        if not text:
            return None

        # Focus on the first page/header region where branding typically lives
        lines = text.splitlines()
        header_slice = "\n".join(lines[:120])

        # 0) NEW: Check for explicit labels FIRST (Carrier:, Policy Company:, Insurer:)
        # This prevents picking up claimant names that happen to be followed by "Indemnity"
        explicit_labels = [
            r"Policy Company:\s*([A-Z][A-Za-z&,\.\s]{2,80})",
            r"Carrier:\s*([A-Z][A-Za-z&,\.\s]{2,80})",
            r"Insurer:\s*([A-Z][A-Za-z&,\.\s]{2,80})",
            r"Underwritten by:\s*([A-Z][A-Za-z&,\.\s]{2,80})",
            r"Insurance Company:\s*([A-Z][A-Za-z&,\.\s]{2,80})"
        ]
        for pattern in explicit_labels:
            label_match = re.search(pattern, header_slice, re.IGNORECASE)
            if label_match:
                candidate = label_match.group(1).strip()
                # Basic validation
                if len(candidate) > 5 and not any(word in candidate.lower() for word in ["insured", "claimant"]):
                    return candidate

        # 1) Try to find explicit insurance-company style names
        # Examples this can catch:
        #  - State Compensation Insurance Fund
        #  - Stonetrust Commercial Insurance Company
        #  - AmTrust North America Insurance
        
        # Refined pattern: Be more restrictive if "Indemnity" is the only keyword
        # Added keywords like "Assurance", "Underwriters"
        # We want to avoid "Name Indemnity" unless it's followed by "Company" or similar if possible,
        # OR if it's NOT preceded by "Claimant Name"
        insurance_pattern = re.compile(
            r"([A-Z][A-Za-z&,\.\s]{2,80}?\s+(?:Insurance|Indemnity|Assurance|Accident|Casualty|Guarantee|Underwriters)(?:\s+(?:Company|Co\.?|Fund|Group|Services?))?)",
            re.IGNORECASE,
        )
        
        # Blacklist of keywords that indicate legal boilerplate / non-carrier text or row labels
        BLACKLIST = {
            "confidentiality", "privileged", "intended", "authorized", "representatives",
            "reliance", "communication", "error", "remove", "notify", "immediately",
            "subject to", "laws", "disclosure", "distribution",
            "claimant", "employee", "insured name", "loss date", "claim number"
        }
        
        matches = insurance_pattern.findall(header_slice)
        if matches:
            valid_matches = []
            for m in matches:
                m_clean = m.strip()
                # Skip if too short or too long
                if len(m_clean) < 5 or len(m_clean) > 100:
                    continue
                # Skip if contains blacklisted legal boilerplate keywords or row labels
                if any(word in m_clean.lower() for word in BLACKLIST):
                    continue
                # Skip if it's "Name Indemnity" but likely a claimant row
                # (Claimant names often have tabs/large gaps before the coverage type)
                if "\t" in m or "  " in m:
                    # If there's a large gap before the keyword, it's likely a table row
                    # unless it's very clearly a company name (ends in Company/Co)
                    if not any(word in m_clean.lower() for word in ["company", "co.", "fund", "group"]):
                        continue

                # Skip if it doesn't look like a proper name (e.g. starts with lowercase or too many special chars)
                if not m_clean[0].isupper() and not any(char.isdigit() for char in m_clean):
                   # Allow some flexibility for results from OCR but generally skip lower-case starts
                   pass 

                valid_matches.append(m_clean)

            if valid_matches:
                # Prefer the one that actually starts with Uppercase if available
                upper_starts = [m for m in valid_matches if m[0].isupper()]
                best_source = upper_starts if upper_starts else valid_matches
                
                # Still prefer longest among valid candidates (e.g. "ABC Insurance Company" over "ABC Insurance")
                best = max(best_source, key=len)
                return best

        # 2) Fallback: infer from website / domain in header
        url_pattern = re.compile(r"(https?://[^\s]+|www\.[^\s]+)", re.IGNORECASE)
        url_match = url_pattern.search(header_slice)
        if url_match:
            url = url_match.group(1)
            # Strip protocol
            url = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
            # Remove path/query fragments
            domain = url.split("/")[0]
            # Strip trailing punctuation
            domain = domain.rstrip(".,);")
            # Normalize "www."
            domain = re.sub(r"^www\.", "", domain, flags=re.IGNORECASE)
            # Reject generic/agency domains if known
            if domain and "atlas" not in domain.lower():
                # Optionally prettify: turn "fcbifund.com" -> "FCBI Fund"
                name_part = domain.split(".")[0]
                tokens = re.split(r"[_\-]+", name_part)
                pretty = " ".join(
                    t.upper() if len(t) <= 4 else t.capitalize()
                    for t in tokens
                    if t
                )
                return pretty or domain

        return None

    def _extract_full_pdf_via_vision(self, pdf_path: str, dpi: int = 300) -> Tuple[str, List[Dict]]:
        """
        Process entire PDF using GPT-4 Vision for layout preservation.
        """
        print(f"🔄 Converting PDF to images for Vision OCR...")
        images = convert_from_path(str(pdf_path), dpi=dpi)
        
        extracted_text = []
        pages_metadata = []
        total_pages = len(images)
        
        print(f"👁️ Processing {total_pages} pages with GPT-4 Vision...")
        
        for i, image in enumerate(images, 1):
            print(f"   Page {i}/{total_pages}...")
            
            # Add page separator
            page_header = f"\n{'='*80}\nPAGE {i}\n{'='*80}\n\n"
            extracted_text.append(page_header)
            
            # Call vision extraction
            page_text, is_scanned, confidence = self._extract_text_via_vision(image)
            
            extracted_text.append(page_text)
            
            # Collect metadata
            pages_metadata.append({
                "page_number": i,
                "text": page_header + page_text,
                "is_scanned": is_scanned,
                "extraction_method": "gpt-4o-vision",
                "confidence": confidence
            })
            
            extracted_text.append("\n\n")
            
        full_text = "".join(extracted_text)
        print(f"✓ Vision extraction complete: {len(full_text)} characters")
        
        return full_text, pages_metadata
    
    def _detect_claim_numbers_ai(self, text: str) -> Dict:
        """
        Use AI to detect ALL claim numbers in the document
        NO HARDCODED PATTERNS - AI figures it out!
        """
        print(f"\n🔍 Using AI to detect claim number patterns...")
        
        prompt = f"""You are an expert at analyzing insurance documents and identifying claim numbers.

Your task: Analyze this insurance document and IDENTIFY ALL UNIQUE CLAIM NUMBERS.

=== CRITICAL DISTINCTION: POLICY NUMBER vs CLAIM NUMBER ===

POLICY NUMBERS:
- Identify an entire insurance policy (covers an insured for a time period)
- Example: "SWC1364773" or "TWC4172502"
- Typically appear in a consistent location on every page
- Multiple different claims can belong to the SAME policy number
- Look for field labels like "Policy Number", "Policy #", "Pol #"

CLAIM NUMBERS:
- Identify a SINGLE claim/incident (one employee's injury)
- Each claim is UNIQUE and appears only once in the document
    - [STRICT] Do NOT invent examples.
    - [STRICT] If a claim number is NOT present in the text, DO NOT create one. Do NOT use placeholders. Simply skip the row or mark as null if it's a summary row.
- Often shown after "Claim #", "Claim No", or similar labels
- Can be simple numeric format OR prefixed format

GOLDEN RULE: If you see the SAME number appear as a header on multiple claim sections, it's a POLICY number, NOT a claim number.
           If you see a DIFFERENT number for each claim/injury, those are CLAIM numbers.

- "Converted #" field (e.g., [CLAIM_NUMBER]) = ACTUAL claim number (unique per claim)
- ❌ DO NOT extract SWC/TWC numbers as claim numbers!
- ✅ DO extract values after "Converted #" as claim numbers!

IMPORTANT INSTRUCTIONS:
1. **Literal Extraction Only**:
   - Extract the claim number EXACTLY as it is written in the document.
   - **NEVER** invent, assume, or append suffixes (like "-01", "-02") if they aren't explicitly typed in the text.
   - **Berkshire Homestates/Redwood Blacklist**: EXPLICITLY IGNORE any strings starting with `CRWC`. These are Policy Numbers, NOT claim numbers. 
   - **Homestates Format**: Claim numbers are typically 8-digit integers.
   - If the document says `ABC123`, result must be `ABC123`. Do NOT add `-01`.

2. **The Header vs. Row Separation**:
   - **Policy Numbers**: Usually in Headers (labeled "Policy #", "Policy Number"). These are **EXCLUSIONS**.
   - **Claim Numbers**: Found within data rows, paired with "Claimant Name" and "Date of Incident".

3. **Strict Validation**:
   - A string is ONLY a claim number if it is paired with actual incident data (Name, Date).
   - **DO NOT** create a claim entry if the only number you find is a `CRWC` policy number.

3. STRICT EXCLUSIONS (DO NOT LIST AS CLAIM NUMBERS):
   - Policy numbers (even if they look like claim numbers)
   - Page numbers
   - Dates
   - Dollar amounts
   - Employee IDs
   - Report IDs

=== SELF-VALIDATION INSTRUCTIONS ===

After detecting claim numbers, perform these checks:

1. **Uniqueness Test**: 
   - Count how many times each detected number appears in the document
   - If a number appears on EVERY page or for MULTIPLE different employees → It's a POLICY number, NOT a claim number
   
2. **Pattern Analysis**:
   - Analyze the format of detected numbers
   - If all numbers follow the same prefix pattern (e.g., all start with "SWC") → Likely policy numbers
   - If numbers are diverse in format → Likely claim numbers
   
3. **Context Validation**:
   - Check what label appears before each number
   - "Policy #", "Policy Number" → EXCLUDE
   - "Claim #", "Claim Number", "Converted #", "Department Number", "Dept #" → INCLUDE
   
4. **Cross-Reference Check**:
   - Compare detected numbers against employee names
   - Each unique employee should have a unique claim number
   - If same number appears for multiple employees → POLICY number

5. **Header-Based Policy Identification**:
   - Look for alphanumeric identifiers (e.g., W610628) in the top header lines of each page.
   - These are often following the company name or "Location" label.
   - Even if they don't have a "Policy:" label, identify them as policy numbers.

For each claim number found, note:
   - The exact format/pattern it follows
   - Where it appears in the document
   - How confident you are it's a claim number (0.0-1.0)
   - Validation results from the checks above

Return a JSON object with this structure:

{{
  "claim_numbers": [
    {{
      "claim_number": "[CLAIM_NUMBER_1]",
      "pattern_description": "Follows 'Claim#' label",
      "first_occurrence": "near line 45",
      "confidence": 0.95,
      "validation_passed": true,
      "uniqueness_score": 1.0,
      "context_label": "Claim#"
    }}
  ],
  "rejected_numbers": [
    {{
      "number": "SWC1364773",
      "reason": "Appears for multiple employees - likely policy number",
      "context_label": "Policy Number"
    }}
  ],
  "detected_patterns": [
    {{
      "pattern_name": "FCBIF format",
      "pattern_description": "Claim# followed by digits",
      "example": "Claim# [NUMBER]",
      "count": 7
    }}
  ],
  "total_unique_claims": 7,
  "confidence": 0.92
}}

DOCUMENT TEXT (COMPLETE):
{text}

Return ONLY the JSON. No explanations. Ensure you catch EVERY claim number, especially those on later pages. Scan the ENTIRE text length.
"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": prompt
                }],
                response_format={"type": "json_object"},
                max_tokens=8000,
                temperature=0.0
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # Extract claim numbers
            claim_numbers = [c["claim_number"] for c in result.get("claim_numbers", [])]
            patterns = result.get("detected_patterns", [])
            
            print(f"✓ AI detected {len(claim_numbers)} unique claim numbers")
            for pattern in patterns:
                print(f"  - {pattern['pattern_name']}: {pattern['count']} claims")
            
            return result
            
        except Exception as e:
            print(f"❌ Error in AI claim detection: {e}")
            import traceback
            traceback.print_exc()
            return {
                "claim_numbers": [],
                "detected_patterns": [],
                "total_unique_claims": 0,
                "confidence": 0.0
            }
    
    def _extract_text_via_vision(self, image: Image.Image) -> Tuple[str, bool, float]:
        """
        Use GPT-4 Vision to extract text while preserving layout.
        """
        buffered = BytesIO()
        image.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        prompt = """You are an expert OCR system that preserves document layout and structure.

Your task: Extract ALL text from this document while preserving its EXACT layout.

⚠️ CRITICAL: If this is a BLANK PAGE or ERROR MESSAGE, indicate that clearly in your response.

CRITICAL REQUIREMENTS:
1. **Preserve Tables**: Keep rows and columns aligned using spaces or tabs
2. **Maintain Spacing**: Keep vertical spacing between sections
3. **Column Alignment**: If document has multiple columns, keep them separate
4. **Headers & Labels**: Clearly show all field labels and their values
5. **Numbers**: Extract all numbers with exact precision (decimals, commas)
6. **Handle Scans**: This may be a scanned document - extract carefully
7. **Orientation**: Document may be landscape or portrait - extract accordingly
8. **Blank Pages**: If page appears blank or contains only an error message, indicate this

EXTRACT EVERYTHING including:
- All headers and titles
- Field labels and their values
- Table contents (all rows and columns)
- Financial amounts
- Dates and times
- Names and identifiers
- Any footnotes or small text

IF THIS PAGE IS BLANK OR CONTAINS ONLY AN ERROR:
- Type: [BLANK PAGE] or [ERROR MESSAGE]
- Description of what you see

FORMAT YOUR RESPONSE AS:

```
[EXTRACTED TEXT - LAYOUT PRESERVED]
<paste the full text here maintaining layout>

[DOCUMENT ANALYSIS]
- Is Scanned: <yes/no>
- Quality: <excellent/good/fair/poor>
- Confidence: <0.0-1.0>
- Layout Type: <table/form/mixed/blank>
- Orientation: <portrait/landscape/unknown>
- Page Status: <content/blank/error>
```

IMPORTANT: 
- Do NOT summarize. Extract the COMPLETE text exactly as it appears.
- If page is blank or shows an error, still report the confidence as 0.0"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img_base64}"
                            }
                        }
                    ]
                }],
                max_tokens=8000,
                temperature=0.0  # Zero temperature for exact extraction
            )
            
            response_text = response.choices[0].message.content
            
            # Parse response
            extracted_text = ""
            is_scanned = False
            confidence = 0.9
            page_status = "content"
            
            # Extract the text section
            if "[EXTRACTED TEXT - LAYOUT PRESERVED]" in response_text:
                parts = response_text.split("[DOCUMENT ANALYSIS]")
                text_section = parts[0].replace("[EXTRACTED TEXT - LAYOUT PRESERVED]", "").strip()
                extracted_text = text_section.strip('`').strip()
                
                # Parse analysis section
                if len(parts) > 1:
                    analysis = parts[1]
                    if "Is Scanned: yes" in analysis.lower():
                        is_scanned = True
                    
                    # Extract confidence score
                    conf_match = re.search(r'Confidence:\s*([\d\.]+)', analysis)
                    if conf_match:
                        confidence = float(conf_match.group(1))
                    
                    # Check page status
                    if "[BLANK PAGE]" in extracted_text or "[ERROR MESSAGE]" in extracted_text:
                        page_status = "blank"
                        confidence = 0.0
                        extracted_text = "[BLANK PAGE - No extractable content]"
                    elif "Page Status: blank" in analysis.lower():
                        page_status = "blank"
                        confidence = 0.0
                        extracted_text = "[BLANK PAGE - No extractable content]"
            else:
                # Fallback: use entire response
                extracted_text = response_text
            
            print(f"✓ Extracted {len(extracted_text)} characters")
            print(f"  - Scanned: {is_scanned}")
            print(f"  - Confidence: {confidence:.2f}")
            print(f"  - Status: {page_status}")
            
            return extracted_text, is_scanned, confidence
            
        except Exception as e:
            print(f"❌ Error extracting text via vision: {e}")
            return "", False, 0.0

    
    def _chunk_text_dynamically(self, text: str, max_tokens: int = 6000) -> List[Dict]:
        """
        Use AI to intelligently split large documents into chunks.
        
        AI determines:
        - Natural boundaries (claim sections, page breaks)
        - Optimal overlap size to preserve context
        - Which sections can be safely split vs must stay together
        
        Returns: List of chunks with metadata
        """
        # If text is small enough, return as single chunk
        estimated_tokens = len(text) // 4  # Rough estimate: 1 token ≈ 4 chars
        if estimated_tokens <= max_tokens:
            return [{
                "chunk_id": 0,
                "text": text,
                "start_pos": 0,
                "end_pos": len(text),
                "strategy": "no_chunking_needed"
            }]
        
        print(f"\n📊 Document is large ({estimated_tokens} est. tokens). Using AI to determine chunking strategy...")
        
        # Sample beginning and end for AI analysis
        sample_text = text[:2000] + "\n...\n" + text[-1000:]
        
        prompt = f"""Analyze this insurance document and suggest optimal split points for processing.

Document length: {len(text)} characters (~{estimated_tokens} tokens)
Target chunk size: ~{max_tokens} tokens

Your task:
1. Identify natural boundaries (claim sections, page breaks, table boundaries)
2. Suggest split points that preserve complete claim information
3. Determine overlap needed between chunks to maintain context

IMPORTANT:
- Each chunk should contain COMPLETE claims (don't split a claim across chunks)
- Look for patterns like "PAGE X", "Claim#", "Employee Name:" that indicate boundaries
- Suggest overlap to ensure no data is lost between chunks

Return JSON:
{{
  "suggested_splits": [
    {{"position": 15000, "reason": "After claim section ends", "overlap_before": 300}},
    {{"position": 32000, "reason": "Page break detected", "overlap_before": 200}}
  ],
  "optimal_overlap": 300,
  "chunking_strategy": "claim-boundary-aware",
  "confidence": 0.95
}}

If no clear boundaries are found, suggest splitting at paragraph breaks with generous overlap.

DOCUMENT SAMPLE:
{sample_text}
"""
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=1500,
                temperature=0.0
            )
            
            chunking_plan = json.loads(response.choices[0].message.content)
            splits = chunking_plan.get("suggested_splits", [])
            default_overlap = chunking_plan.get("optimal_overlap", 300)
            
            print(f"   ✓ AI suggested {len(splits)} split points")
            print(f"   ✓ Strategy: {chunking_plan.get('chunking_strategy', 'adaptive')}")
            
            # Build chunks based on AI suggestions
            chunks = []
            current_pos = 0
            
            for idx, split in enumerate(splits):
                split_pos = split.get("position", 0)
                overlap = split.get("overlap_before", default_overlap)
                
                # Ensure split position is within bounds
                if split_pos > len(text):
                    split_pos = len(text)
                
                # Create chunk with overlap
                chunk_start = max(0, current_pos - overlap if idx > 0 else 0)
                chunk_end = split_pos
                
                chunks.append({
                    "chunk_id": idx,
                    "text": text[chunk_start:chunk_end],
                    "start_pos": chunk_start,
                    "end_pos": chunk_end,
                    "overlap": overlap if idx > 0 else 0,
                    "reason": split.get("reason", "AI-determined boundary")
                })
                
                current_pos = split_pos
            
            # Add final chunk
            if current_pos < len(text):
                chunks.append({
                    "chunk_id": len(chunks),
                    "text": text[max(0, current_pos - default_overlap):],
                    "start_pos": max(0, current_pos - default_overlap),
                    "end_pos": len(text),
                    "overlap": default_overlap,
                    "reason": "Final section"
                })
            
            return chunks
            
        except Exception as e:
            print(f"   ⚠️ AI chunking failed: {e}")
            print(f"   Falling back to simple chunking...")
            
            # Fallback: Simple chunking with fixed overlap
            chunks = []
            chunk_size = max_tokens * 4  # Convert tokens to chars
            overlap = 500
            current_pos = 0
            chunk_id = 0
            
            while current_pos < len(text):
                chunk_end = min(current_pos + chunk_size, len(text))
                chunk_start = max(0, current_pos - overlap if chunk_id > 0 else 0)
                
                chunks.append({
                    "chunk_id": chunk_id,
                    "text": text[chunk_start:chunk_end],
                    "start_pos": chunk_start,
                    "end_pos": chunk_end,
                    "overlap": overlap if chunk_id > 0 else 0,
                    "strategy": "fallback_fixed_size"
                })
                
                current_pos = chunk_end
                chunk_id += 1
            
            return chunks
    
    def extract_schema_from_text(self, all_text: str, target_claim_number: Optional[str] = None, vision_pattern: Optional[Dict] = None) -> Dict:
        """
        Extract structured schema from verified text
        NOW SUPPORTS MULTIPLE CLAIMS!
        """
        print(f"\n🎯 Extracting schema from text...")
        
        # Decide whether to extract all claims or just one
        if target_claim_number:
            print(f"   Target: Claim #{target_claim_number} only")
            claim_data = self._extract_single_claim(all_text, target_claim_number)
            return {
                "claims": [claim_data] if claim_data else [],
                "policy_number": claim_data.get("policy_number") if claim_data else None,
                "carrier_name": claim_data.get("carrier_name") if claim_data else None,
                "insured_name": claim_data.get("insured_name") if claim_data else None,
                "report_date": claim_data.get("report_date") if claim_data else None,
            }
        else:
            print(f"   Target: ALL claims in document")
            return self._extract_all_claims(all_text)
    
    def _analyze_document_format(self, text: str) -> Dict:
        """
        STAGE 1: Analyze document structure and format
        Let GPT-4 figure out how the data is organized
        """
        print(f"\n🔍 STAGE 1: Analyzing document format...")
        
        prompt = f"""You are analyzing an insurance loss run report to understand its structure.

Your task: Describe HOW the data is organized in this document so we can extract it accurately.

Carrier name: The insurance company that issued the policy and is financially responsible for the claims.

Answer these questions to help us extract data accurately:

1. What is the Carrier Name (Insurance Company)? 
   Definition: The insurance company that issued the policy and is financially responsible for the claims.
   Instructions:
   - Look for explicit mentions of an insurance company name.
   - Common indicators: Header/footer names, Copyright lines, Branding at top (e.g., State Fund Online), "Insurance Company", "Insurance Co.", etc.
   - 🛑 CRITICAL: Do NOT return the agency name. (e.g., "Atlas General Insurance Services" is often the agency/MGA, while "Service American Indemnity Company" is the carrier).
   - 🛑 CRITICAL: Do NOT return a claimant name. If you see a name followed by "Indemnity" in a table row (e.g., "John Doe - Indemnity"), that is the coverage type, NOT the carrier.
   - Do NOT return: Insured name, Brokerage name, District office, or Policyholder name.
   - If the carrier is clearly implied by document branding (e.g., "State Compensation Insurance Fund", "Stonetrust", or markers like "Report ID: 677" suggesting AmTrust North America), return that.
   - If no carrier can be confidently identified, return null.

2. How are claims organized? (one per row, multi-row per claim, one per page?)
3. How are financial categories (Medical, Indemnity, Expense) presented?
   - Are they in COLUMNS (e.g. Header says 'Med Paid', 'Ind Paid')? 
   - Or are they in ROWS (e.g. A label 'Medical' appears on one line, 'Indemnity' on another)?
4. IMPORTANT: Map the EXACT numeric column headers to their meanings.
   - e.g. "Column 1 is Payments, Column 2 is Reserve, Column 3 is Incurred"
5. Determine the EXACT row/block order if multi-row.
6. Are there specific labels that anchor the rows or columns? (e.g., "Payments", "Medical", "Indemnity", "Expense")

Return JSON:
{{
  "insurer": "company name",
  "format_type": "simple_columns" or "complex_multi_row" or "mixed",
  "claim_layout": "one_per_row" or "multi_row_per_claim" or "one_per_page",
  "financial_mapping": {{
    "category_dimension": "rows" or "columns",
    "value_dimension": "rows" or "columns",
    "column_map": {{"col_index_or_name": "meaning", "..."}},
    "row_map": {{"row_index_or_label": "meaning", "..."}},
    "dynamic_rules": "A highly technical extraction rule. e.g. 'Read claim ID from line 1. For Medical Paid, look for row labeled MEDICAL and take value from column 7 (Payments).'"
  }},
  "special_notes": "any quirks or unusual formatting",
  "confidence": 0.0-1.0
}}

DOCUMENT TEXT (first 8000 chars):
{text[:8000]}

Return ONLY the JSON. Ensure the dynamic_rules is extremely precise."""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=1500,
                temperature=0.0
            )
            
            format_info = json.loads(response.choices[0].message.content)
            
            print(f"   ✓ Format detected: {format_info.get('format_type', 'unknown')}")
            print(f"   ✓ Insurer: {format_info.get('insurer', 'unknown')}")
            print(f"   ✓ Claim layout: {format_info.get('claim_layout', 'unknown')}")
            print(f"   ✓ Confidence: {format_info.get('confidence', 0.0):.2%}")
            
            return format_info
            
        except Exception as e:
            print(f"   ⚠️  Format analysis failed: {e}")
            return {
                "format_type": "unknown",
                "confidence": 0.0
            }
    
    def _extract_all_claims(self, all_text: str, vision_pattern: Optional[Dict] = None,
                             pre_built_master_list: Optional[List[str]] = None) -> Dict:
        """
        UNIVERSAL EXTRACTION: Works with ANY format
        Uses a three-stage approach:
        1. Pre-Discovery: Detect all valid Claim IDs (Master List)
        2. Format Analysis: Understand the layout
        3. Constrained Extraction: Extract only those IDs

        RC1b: When called from ChunkedInsuranceExtractor, `pre_built_master_list`
        is the global list built from the full document.  Skips per-chunk
        re-detection so boundary claims are never filtered out.
        """
        # STAGE 0: Pre-Discovery (Master List)
        # RC1b: use the pre-built global list if provided, else detect locally
        if pre_built_master_list is not None:
            master_claim_list = pre_built_master_list
            if master_claim_list:
                print(f"   ✓ Using pre-built global master list ({len(master_claim_list)} IDs).")
            else:
                print("   ℹ️ Pre-built master list is empty — falling back to per-chunk detection.")
                detected_claims_info = self._detect_claim_numbers_ai(all_text)
                master_claim_list = [c["claim_number"] for c in detected_claims_info.get("claim_numbers", [])]
        else:
            detected_claims_info = self._detect_claim_numbers_ai(all_text)
            master_claim_list = [c["claim_number"] for c in detected_claims_info.get("claim_numbers", [])]

        # Dynamic fallback carrier that we will propagate if the model doesn't set one
        fallback_carrier: Optional[str] = None

        if not master_claim_list:
            print("   ⚠️ No unique claim numbers discovered. Falling back to layout-only extraction.")
            master_list_str = "No pre-detected list available. Detect claims dynamically."
        else:
            print(f"   ✓ Pre-discovered {len(master_claim_list)} valid claim IDs.")
            master_list_str = ", ".join(master_claim_list)
            
        # STAGE 1: Analyze document format
        format_info = self._analyze_document_format(all_text)

        # Prefer model-detected insurer/carrier; if missing, infer dynamically from text
        if isinstance(format_info, dict):
            fallback_carrier = (
                format_info.get("insurer")
                or format_info.get("carrier_name")
                or None
            )
        if not fallback_carrier:
            inferred = self._infer_carrier_from_text(all_text)
            if inferred:
                fallback_carrier = inferred
                if isinstance(format_info, dict):
                    format_info.setdefault("insurer", inferred)
        
        # Merge vision pattern into format_info if available
        if vision_pattern:
            format_info['vision_layout'] = vision_pattern.get('layout_type')
            format_info['vision_pattern'] = vision_pattern.get('pattern_description')
            if 'financial_mapping' not in format_info:
                format_info['financial_mapping'] = {}
            format_info['financial_mapping']['vision_formula'] = vision_pattern.get('formula_js')
            
        # STAGE 2: Build adaptive extraction prompt
        print(f"\n🎯 STAGE 2: Extracting claims using constrained adaptive prompt...")
        
        # Build format-specific instructions
        format_type = format_info.get('format_type', 'unknown')
        financial_mapping = format_info.get('financial_mapping', {})
        # Support both keys but prefer the more detailed dynamic_rules
        dynamic_rules = (
            financial_mapping.get('dynamic_rules') or 
            financial_mapping.get('dynamic_instruction') or 
            'Extract all financial fields carefully.'
        )
        
        # Injected Accuracy Constraints
        accuracy_constraints = f"""
=== ACCURACY CONSTRAINTS (MANDATORY) ===
1. MASTER CLAIM LIST: {master_list_str}
2. 🛑 ZERO-PHANTOM POLICY: Extract ONLY claims from the MASTER CLAIM LIST above. 
   - NEVER include placeholder names like 'John Smith', 'Jane Doe', 'John Doe', or 'Jane Smith'. 
   - These are calibration examples and NOT real data in this document.
   - If a claim ID is not in the list, DO NOT extract it.
3. 🛑 FIELD INTEGRITY: Do NOT swap Medical and Indemnity columns. Check headers for each row.
4. 🛑 CURRENCY: Remove all symbols ($, ,) and return numbers as floats.
5. 🛑 LITIGATION: ONLY extract if explicitly present (e.g., 'Litigated: Y', 'Litigation: No'). 
   - If there is NO mention of litigation, you MUST return null. 
   - NEVER assume 'No' if the field is missing.
6. 🛑 MULTIPLE EXPENSES: If a document has multiple expense columns/rows (e.g., Legal, Other, Admin, LAE), you MUST SUM THEM into the 'expense_paid' or 'expense_reserve' fields. Do NOT ignore any expense row.
7. 🛑 POLICY NUMBER: Extract ONLY a real alphanumeric policy identifier (e.g., W610628, SWC1364773, 64536).
   - A policy number is a short code assigned by the insurer to identify the policy.
   - NEVER use the insured/company name (e.g., 'A TOTAL SOLUTION INC') as a policy number.
   - If no explicit policy number is visible in the document, set policy_number to null.
8. 🛑 POLICY NUMBER FORMAT: A valid policy_number MUST contain at least one digit.
   - Allowed formats: purely numeric (e.g., "64536") or mixed alphanumeric (e.g., "SWC1364773").
   - If a candidate policy value contains NO digits (letters/spaces only), treat it as invalid and use null instead.
"""
        
        if format_type == 'complex_multi_row':
            financial_instructions = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 POLICY IDENTIFICATION (Critical) 🔴
Look for the policy number (e.g., W610628) in the top header section of the page.
It might be part of a string like: "Location - SKMGT LE BLEU CHATEAU, INC. - W610628"
Select this as the policy_number even if it lacks a "Policy Number:" label.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 FORMAT CALIBRATION (Mandatory) 🔴
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
This document uses a complex block-based or multi-row structure. 

⚠️ DYNAMIC MAPPING STRATEGY:
{dynamic_rules}

⚠️ INDEMNITY CALCULATION:
- If you see both "TD" (Temporary Disability) and "PD" (Permanent Disability) for a single claim, YOU MUST SUM THEM.
- medical_paid = Medical Paid
- medical_reserve = Medical Outstanding
- indemnity_paid = TD Paid + PD Paid
- indemnity_reserve = TD Outstanding + PD Outstanding
- expense_paid = Expense Paid
- expense_reserve = Expense Outstanding

⚠️ STATUS MAPPING (Strict):
- "C" -> "Closed"
- "O" -> "Open"
- "R", "RC", "REOP" -> "Reopened"

⚠️ MATH CHECKSUM:
Paid + Reserve == Incurred (For each category).
Sum of (M, I, E) = Total.
If the math doesn't match perfectly, you have swapped Columns/Rows or missed PD/TD summation!
"""
        elif format_type == 'simple_columns':
            financial_instructions = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ SIMPLE COLUMNAR FORMAT DETECTED ✅
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This format has clearly labeled columns. Extract values directly:
- Look for columns: Ind Paid, Ind Resv, Med Paid, Med Resv, Exp Paid, Exp Resv, Total Inc
- Each claim is one row
- Read values directly from the columns
- NO complex calculations needed

MAPPING:
- medical_paid = "Med Paid" column
- medical_reserve = "Med Resv" column
- indemnity_paid = "Ind Paid" column
- indemnity_reserve = "Ind Resv" column
- expense_paid = "Exp Paid" column
- expense_reserve = "Exp Resv" column
- recovery = "Recov" column
- total_incurred = "Total Inc" column
"""
        else:
            financial_instructions = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ UNKNOWN/MIXED FORMAT DETECTED ⚠️
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Carefully analyze each claim's structure and extract accordingly.
Look for patterns in how financial data is presented.
Validate your extractions by checking if Paid + Reserve = Incurred.
"""
        
        # Build the complete extraction prompt
        prompt = f"""You are an expert at extracting structured data from insurance loss run reports.

DOCUMENT FORMAT ANALYSIS:
{json.dumps(format_info, indent=2)}

{financial_instructions}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 EXTRACTION TASK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚠️ DYNAMIC MAPPING RULES FOR THIS DOCUMENT:
{dynamic_rules}

{accuracy_constraints}

Extract EVERY SINGLE CLAIM from this document.

Return JSON:
{{
  "policy_number": "alphanumeric policy code (e.g. W610628) or null if not explicitly present",
  "carrier_name": "Insurance company name (The entity financially responsible) or null",
  "insured_name": "string or null",
  "report_date": "YYYY-MM-DD or null",
  "policy_period": "string or null",
  "claims": [
    {{
      "employee_name": "full name",
      "carrier_name": "The insurance company for this claim or report",
      "policy_number": "alphanumeric policy code for this specific claim's policy year, or null if not found",
      "claim_number": "claim number",
      "injury_date_time": "YYYY-MM-DD",
      "claim_year": 2020,
      "status": "Open or Closed or Reopened",
      "reopen": "True or False",
      "injury_description": "description",
      "body_part": "body part or null",
      "injury_type": "Indemnity or Medical Only or Expense",
      "claim_class": "STRICTLY NUMERIC class code ONLY (e.g. 882707). NO letters. Correct OCR typos (e.g. 34p -> 340). Null if valid number is missing",
      "medical_paid": "string (e.g. '1,973.00')",
      "medical_reserve": "string",
      "indemnity_paid": "string",
      "indemnity_reserve": "string",
      "expense_paid": "string",
      "expense_reserve": "string",
      "total_paid": "string",
      "total_reserve": "string",
      "total_incurred": "string",
      "litigation": "Yes if explicitly Yes/Y, else No",
      "confidence_score": "0.0 to 1.0 (float)"
    }}
  ]
}}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GENERAL EXTRACTION RULES (Apply to ALL formats)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


1. CLAIM NUMBER vs POLICY NUMBER - CRITICAL DISTINCTION
   
   ⚠️ MOST IMPORTANT RULE: DO NOT confuse policy numbers with claim numbers!
   
   **POLICY NUMBERS:**
   - Identify an entire insurance policy (covers multiple claims over a time period)
   - Examples: SWC1364773, TWC4172502, ZAWCI9740001, Policy #12345
   - Appear REPEATEDLY throughout the document (same number for multiple claims)
   - Found in fields labeled: "Policy Number", "Policy #", "Pol #", "Policy No"
   - ❌ DO NOT use policy numbers as claim numbers!
   
   **CLAIM NUMBERS:**
   - Identify a SINGLE claim/incident (one employee's injury)
   - Each claim number is UNIQUE - appears only ONCE in the document
   - Examples: [UNIQUE_CLAIM_ID]
   - Found in fields labeled: "Claim #", "Claim No", "Claim Number", "Converted #", "Department Number", "Dept #"
   
   **FORMAT-SPECIFIC GUIDANCE:**
   
   A. **AmTrust Format:**
      - Policy Number: SWC1364773 or TWC4172502 (appears at top of each claim)
      - Claim Number: Look for "Converted #" field (e.g., 3510012, 3543022)
      - ✅ USE: The "Converted #" value
      - ❌ IGNORE: The "Policy Number" value
   
   B. **FCBIF Format:**
      - Claim Number: Look for "Claim#" followed by number (e.g., Claim# 20825)
      - ✅ USE: The number after "Claim#"
   
   C. **DeliverRe Format:**
      - Claim Number: Starts with "DEL" (e.g., DEL22003452)
      - ✅ USE: The DEL-prefixed number

   D. **Strict Identification Rules (CRITICAL):**
      - **NO SUFFIX INVENTION**: Do NOT append characters to a number unless you see them in the raw text.
      - **CRWC Blacklist**: Numbers starting with `CRWC` are POLICY NUMBERS. Never extract them as claims. 
      - **Berkshire Homestates**: Claim numbers are 8-digit integers found next to the name.
      - **BiBERK (N9WC)**: These *do* have literal suffixes in the text (e.g., `-001`). Extract them exactly.
      - **Literal Match**: Ensure extracted claim numbers match exactly what is in the document. Do NOT add `-01`, `-02` etc.
   
   **VALIDATION:**
   - If you see the SAME number appearing for multiple different employees → It's a POLICY number, NOT a claim number
   - If each employee has a DIFFERENT number → Those are CLAIM numbers ✓
   
   **GOLDEN RULE:** When in doubt, look for:
   - "Claim #:", "Claim No:", "Claim Number:", "Converted #" → These introduce CLAIM numbers
   - "Policy Number", "Policy #", "Pol #" → These introduce POLICY numbers (ignore these!)

   E. **State Fund Online / Loss Analysis Report (CRITICAL)**:
      - Columns "Est. Comp" and "Est. Medical" represent the **TOTAL INCURRED** (Paid + Reserve).
      - DO NOT treat these as Reserves.
      - Calculate: Reserve = Estimated value - Paid value.
      - If Estimated == Paid, then Reserve = 0.

    F. **AmTrust North America Layout (CRITICAL)**:
       - AmTrust uses a 4-row block per financial category:
         1. **Reserves** (Top row)
         2. **Payments** (Second row)
         3. **Recoveries** (Third row)
         4. **Incurred** (Bottom row - Total)
       - DO NOT map the "Incurred" value to "Reserves". 
       - Always verify: Incurred = Reserves + Payments - Recoveries.
       - Since the schema lacks a "Recoveries" field, you MUST prioritize matching the final "Incurred" value.
       - If Payments - Recoveries = Incurred, extract the Net Paid values (Payments - Recoveries) to ensure math validation (Paid + Reserve == Incurred) passes.


2. EMPLOYEE NAME
   - Look for "Claimant:", "Employee Name:", or similar labels
   - Extract full name as shown

3. DATES - CRITICAL: USE DATE OF LOSS (DOL)
   - ALWAYS use "DOL" or "Date of Loss" for injury_date_time
   - DO NOT use "Date Rcvd" or "First Aware" - these are reporting dates
   - Convert all dates to YYYY-MM-DD format
   - Look for: "DOL:", "Loss Date:", "Injury Date:", "Occ Date:", "Accident Date:"

4. STATUS
   - C or Closed or RECLOSED → "Closed"
   - O or Open → "Open"
   - REOP or Reopened or R or RC or REOPEN → "Reopened"

5. INJURY TYPE
   - Medical or MED or MEDI or "Medical Only" or "Record Only" → "Medical Only"
   - Indemnity or COMP or Compensation or TTD or TPD or PPD → "Indemnity"
   - Expense or LAE or Service → "Expense"

6. BODY PART
   - Extract from "Nature of Injury", "Body Part", "Part Injured" fields
   - If not found, use null

7. INJURY DESCRIPTION
   - Look for "Nature of Injury:", "Cause of Injury:", "Loss Description:", "Accident Description:"
   - Extract the description text

8. CLAIM CLASS
   - Look for "Class Code:", "Class:", "Class Cd"
   - Extract the code (e.g., "7721", "7231")
   - If not found, use null

9. NUMBERS
   - Remove all $ signs and commas
   - Convert to decimal numbers
   - "$51,068.57" → 51068.57

10. NULL VALUES
    - Use null for truly missing data
    - Use 0.0 for financial fields that are zero

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ VALIDATION CHECKLIST FOR EACH CLAIM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before adding a claim to your JSON, verify:

✓ claim_number is extracted
✓ employee_name is extracted
✓ injury_date_time is in YYYY-MM-DD format
✓ status is "Open", "Closed", or "Reopened"
✓ All financial values are numbers (not strings)
✓ Financial calculations balance (Paid + Reserve ≈ Incurred)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📄 TEXT TO ANALYZE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{all_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 YOUR RESPONSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY the JSON object. No explanations. No markdown. Just the JSON.

Extract ALL claims. Do not skip any claim.

⚠️ CRITICAL REMINDER:
- This document may have MULTIPLE POLICY PERIODS or MULTIPLE POLICY SECTIONS
- You MUST scan the ENTIRE document from beginning to end
- Extract claims from ALL policy sections, NOT just the first one
- Continue extracting until you reach the end of the document
- Do NOT stop extraction after finding the first policy section totals

Follow the format-specific instructions above. Validate your extractions."""

        # Step 1: Initial Extraction Attempt
        data = {"claims": []}
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": prompt
                }],
                response_format={"type": "json_object"},
                max_tokens=8000,
                temperature=0.0
            )
            
            response_text = response.choices[0].message.content
            initial_data = json.loads(response_text)
            
            # Check consistency
            if "claims" in initial_data:
                data = initial_data
            elif isinstance(initial_data, dict):
                # Maybe it returned a single object instead of a list
                data = {"claims": [initial_data]}
            
            # Step 1.5: Mathematical Self-Correction Loop
            failed_math_claims = [c.get("claim_number") for c in data.get("claims", []) if not c.get("math_valid", True)]
            
            if failed_math_claims:
                print(f"   ⚠️  MATH VALIDATION FAILED for {len(failed_math_claims)} claim(s). Triggering self-correction...")
                print(f"   Failed IDs: {', '.join(str(c) for c in failed_math_claims)}")
                
                # Use a smaller batch size for correction to ensure focus
                correction_batch_size = 3
                for i in range(0, len(failed_math_claims), correction_batch_size):
                    batch = failed_math_claims[i:i + correction_batch_size]
                    print(f"   🔄 Correction Batch {i//correction_batch_size + 1}: {', '.join(str(c) for c in batch)}")
                    
                    try:
                        correction_data = self._extract_missing_claims_by_number(all_text, data, batch, is_correction=True)
                        if correction_data and "claims" in correction_data:
                            # The post-processing logic in _extract_missing_claims_by_number will handle merging
                            # We just need to ensure the updated claims are added/replaced
                            for updated_claim in correction_data["claims"]:
                                # Data will be updated via deduplication in next post_process
                                data["claims"].append(updated_claim)
                    except Exception as e:
                        print(f"      ⚠️ Correction attempt failed: {e}")
                
                # Re-run post-processing to merge corrected claims
                data = self._post_process_claims(data)
                
        except Exception as e:
            print(f"   ⚠️  Initial extraction or correction failed: {e}")
            data = {"claims": []}

        # Step 2: Verification & Recovery (ALWAYS RUNS)
        try:
            # VALIDATION CHECK: Use AI to detect claim numbers
            detected_claims_info = self._detect_claim_numbers_ai(all_text)
            claims_in_text = detected_claims_info.get('total_unique_claims', 0)
            claims_extracted = len(data.get("claims", []))

            # If header-level carrier_name is still missing, apply dynamic fallback
            if fallback_carrier and not data.get("carrier_name"):
                data["carrier_name"] = fallback_carrier

            # Pass the master claim list to post-processing for strict filtering
            master_claim_list = [c["claim_number"] for c in detected_claims_info.get("claim_numbers", [])]
            data = self._post_process_claims(data, master_claim_list=master_claim_list)
            
            if claims_in_text > claims_extracted:
                print(f"\n   ⚠️  INCOMPLETE EXTRACTION DETECTED")
                print(f"   Claims detected by AI: {claims_in_text}")
                print(f"   Claims extracted: {claims_extracted}")
                print(f"   Missing: {claims_in_text - claims_extracted}")
                
                # Get list of missing claim numbers
                detected_numbers = [c["claim_number"] for c in detected_claims_info.get("claim_numbers", [])]
                extracted_numbers = [c.get("claim_number") for c in data.get("claims", [])]
                missing_numbers = list(set(detected_numbers) - set(extracted_numbers))
                
                if missing_numbers:
                    print(f"   Attempting to extract {len(missing_numbers)} missing claims in batches...")
                        
                    # Process in batches of 5 to avoid timeouts/limits
                    batch_size = 5
                    for i in range(0, len(missing_numbers), batch_size):
                        batch = missing_numbers[i:i + batch_size]
                        print(f"   🔄 Recovery Batch {i//batch_size + 1}: {', '.join(batch)}")
                            
                        # RETRY LOGIC for each batch
                        max_retries = 2
                        for attempt in range(max_retries):
                            try:
                                retry_data = self._extract_missing_claims_by_number(all_text, data, batch)
                                if retry_data and "claims" in retry_data:
                                    new_claims = retry_data["claims"]
                                    if new_claims:
                                        # Check for math validity in this batch too
                                        failed_in_batch = [c.get("claim_number") for c in new_claims if not c.get("math_valid", True)]
                                        if failed_in_batch and attempt < max_retries - 1:
                                            print(f"      ⚠️  Math fail in recovery batch. Retrying correction for: {', '.join(str(c) for c in failed_in_batch)}")
                                            correction_data = self._extract_missing_claims_by_number(all_text, data, failed_in_batch, is_correction=True)
                                            if correction_data and "claims" in correction_data:
                                                # Replace failed claims with corrected ones
                                                valid_new_claims = [c for c in new_claims if str(c.get("claim_number")) not in [str(x) for x in failed_in_batch]]
                                                valid_new_claims.extend(correction_data["claims"])
                                                new_claims = valid_new_claims
                                        
                                        data["claims"].extend(new_claims)
                                        print(f"      ✓ Retrieved {len(new_claims)} claims in this batch")
                                        break # Success
                            except Exception as e:
                                print(f"      ⚠️  Recovery batch attempt {attempt+1} failed: {e}")
                                    
                        # Final merge check
                    data = self._post_process_claims(data)
                final_count = len(data.get("claims", []))
                print(f"   ✓ Recovery complete. Final count: {final_count}/{claims_in_text}")
            else:
                print(f"   ✓ All claims accounted for ({claims_in_text} total)")
                # Unconditionally run post-processing even if no claims were missing
                # to ensure claim_year and other normalizations are applied.
                data = self._post_process_claims(data)
                
        except Exception as e:
            print(f"   ❌ Error in recovery phase: {e}")
            import traceback
            traceback.print_exc()

        return data
            

    
    def _post_process_claims(self, data: Dict, master_claim_list: Optional[List[str]] = None) -> Dict:
        """
        Post-process extracted claims to fix formatting and field mapping
        Cleanup and deduplicate claims using math-driven quality scores.
        """
        if "claims" not in data or not data["claims"]:
            return data
            
        if master_claim_list:
             print(f"   🔍 Post-processing with master list: {', '.join(master_claim_list)}")

        # Helper: policy_number must contain at least one digit; otherwise treat as invalid
        def _is_valid_policy_number(value: Optional[str]) -> bool:
            if not value:
                return False
            return bool(re.search(r"\d", str(value)))

        # Status Mapping
        status_map = {
            'C': 'Closed', 'CL': 'Closed', 'CLOSED': 'Closed', 'RECLOSED': 'Closed',
            'O': 'Open', 'OP': 'Open', 'OPEN': 'Open',
            'R': 'Reopened', 'RC': 'Reopened', 'REOP': 'Reopened', 'REOPENED': 'Reopened', 'REOPEN': 'Reopened'
        }
        
        # Numeric fields to clean
        num_fields = [
            "medical_paid", "medical_reserve", "indemnity_paid", "indemnity_reserve",
            "expense_paid", "expense_reserve", "total_paid", "total_reserve", "total_incurred"
        ]
        
        seen_claim_numbers = {} # claim_number -> (claim_obj, quality_score)
        
        # Extract top-level default metadata
        default_policy = data.get("policy_number")
        # Enforce format rule at header level
        if default_policy and not _is_valid_policy_number(default_policy):
            default_policy = None
            data["policy_number"] = None
        default_carrier = data.get("carrier_name")
        
        for claim in data["claims"]:
            claim_num = str(claim.get("claim_number", "")).strip()
            if not claim_num:
                continue
                
            # 0. Metadata Propagation
            if not claim.get("policy_number") and default_policy:
                claim["policy_number"] = default_policy
            if not claim.get("carrier_name") and default_carrier:
                # RC4 FIX: Only propagate top-level carrier if it doesn't look like an aggregated list.
                # We block commas (aggregated list) but ALLOW spaces (multi-word company names).
                if "," not in str(default_carrier):
                    claim["carrier_name"] = default_carrier
            
            # Guard against calibration hallucinations from the prompt
            current_carrier = str(claim.get("carrier_name") or "").lower()
            if current_carrier in ["insurance company name", "the insurance company for this claim or report"]:
                claim["carrier_name"] = default_carrier if default_carrier else None
                
            # Enforce policy_number format rule at claim level
            if claim.get("policy_number") and not _is_valid_policy_number(claim.get("policy_number")):
                claim["policy_number"] = None

            # 1. Normalize Status
            raw_status = str(claim.get("status", "")).upper().strip()
            claim["status"] = status_map.get(raw_status, raw_status)
            
            # 1a-1. Handle Reopen flag
            if claim["status"] == "Reopened":
                claim["reopen"] = "True"
            else:
                # If AI didn't provide it or it's not "True"
                if claim.get("reopen") not in ["True", "False"]:
                    claim["reopen"] = "False"
            
            # 1b. Normalize Litigation
            litigation_val = claim.get("litigation")
            # Default to "N" and normalize Y/N
            raw_litigation = str(litigation_val or "N").upper().strip()
            if raw_litigation in ["Y", "YES", "TRUE"]:
                claim["litigation"] = "Yes"
            else:
                claim["litigation"] = "No"
            
            # 1c. Explicitly remove fields requested for removal
            claim.pop('recovery', None)
            claim.pop('deductible', None)
            
            # 1d. Normalize Confidence Score
            conf = claim.get("confidence_score")
            try:
                if conf is not None:
                    claim["confidence_score"] = float(conf)
                else:
                    # Heuristic fallback if AI missed it
                    claim["confidence_score"] = 0.9 if claim.get("math_valid") else 0.7
            except:
                claim["confidence_score"] = 0.8
            
            # 2. Normalize Injury Type (Indemnity, Medical Only, Expense)
            raw_type = str(claim.get("injury_type", "")).upper()
            if any(x in raw_type for x in ["COMP", "TTD", "TPD", "PPD", "INDEMNITY", "INDEM"]):
                claim["injury_type"] = "Indemnity"
            elif any(x in raw_type for x in ["MED", "MEDICAL", "MEDICAL ONLY"]):
                claim["injury_type"] = "Medical Only"
            elif any(x in raw_type for x in ["EXP", "EXPENSE"]):
                claim["injury_type"] = "Expense"
            
            # 3. Numeric cleanup
            for field in num_fields:
                val = claim.get(field)
                if isinstance(val, str):
                    clean_val = re.sub(r'[^\d.]', '', val)
                    try:
                        claim[field] = float(clean_val) if clean_val else 0.0
                    except:
                        claim[field] = 0.0
                elif val is None:
                    claim[field] = 0.0

            # 3a. Populate total_paid and total_reserve if missing OR for sanity
            calculated_paid = claim.get("medical_paid", 0.0) + claim.get("indemnity_paid", 0.0) + claim.get("expense_paid", 0.0)
            calculated_reserve = claim.get("medical_reserve", 0.0) + claim.get("indemnity_reserve", 0.0) + claim.get("expense_reserve", 0.0)
            
            # Update values
            claim["total_paid"] = round(calculated_paid, 2)
            claim["total_reserve"] = round(calculated_reserve, 2)

            # 3b. MED-only Guardrail: If injury_type is Medical Only, Indemnity MUST be 0
            if claim.get("injury_type") == "Medical Only":
                claim["indemnity_paid"] = 0.0
                claim["indemnity_reserve"] = 0.0

            # 3c. Extract Claim Year from Injury Date
            injury_date = str(claim.get("injury_date_time", "")).strip()
            claim["claim_year"] = None
            if injury_date:
                # Expecting YYYY-MM-DD or MM/DD/YYYY
                # Try finding 4 digits that start with 19 or 20
                match = re.search(r'(?:19|20)\d{2}', injury_date)
                if match:
                    try:
                        claim["claim_year"] = int(match.group(0))
                    except:
                        pass

            # 4. Calculate Quality Score (Checksum Match)
            # Use same logic as _validate_financial_data for consistency
            med_paid = claim.get('medical_paid', 0.0) or 0.0
            med_res = claim.get('medical_reserve', 0.0) or 0.0
            ind_paid = claim.get('indemnity_paid', 0.0) or 0.0
            ind_res = claim.get('indemnity_reserve', 0.0) or 0.0
            exp_paid = claim.get('expense_paid', 0.0) or 0.0
            exp_res = claim.get('expense_reserve', 0.0) or 0.0
            reported_total = claim.get('total_incurred', 0.0) or 0.0
            
            # Simple sum for gross check
            calc_sum = med_paid + med_res + ind_paid + ind_res + exp_paid + exp_res
            
            # Check if calc_sum matches perfectly
            quality_score = 0.5
            err_sum = abs(calc_sum - reported_total)
            
            if err_sum < 1.0:
                quality_score = 1.0
            
            claim["math_valid"] = (quality_score == 1.0)
            claim["math_diff"] = round(err_sum, 2)
            
            # 5. Name Normalization (Last, First)
            # If name is "First Last", convert to "Last, First"
            raw_name = str(claim.get("employee_name", "")).strip()
            if raw_name and "," not in raw_name:
                name_parts = raw_name.split()
                if len(name_parts) >= 2:
                    # Heuristic: Assume last word is surname for simple cases
                    # "John Smith" -> "Smith, John"
                    # "John M. Smith" -> "Smith, John M."
                    last = name_parts[-1]
                    first = " ".join(name_parts[:-1])
                    claim["employee_name"] = f"{last}, {first}"
            
            # 7. Deduplicate using Seen dictionary
            claim_num_raw = claim.get("claim_number")
            
            # STRICT FILTERING: Reject null, empty, or "none" claim numbers
            if claim_num_raw is None:
                print(f"      🗑️  Filtering claim with null/missing claim number")
                continue
                
            claim_num = str(claim_num_raw).strip()
            
            # Reject only truly invalid/placeholder claim numbers.
            # ⚠️ NEVER hardcode real-looking numeric IDs here — they may be
            #    valid claims from any carrier (e.g. FCBI uses 5-digit integers).
            #    Real phantom filtering is handled by the MASTER LIST below.
            INVALID_STRINGS = {"none", "null", "unknown", "n/a", "[claim_number_1]", ""}
            if not claim_num or claim_num.lower() in INVALID_STRINGS:
                print(f"      🗑️  Filtering invalid/placeholder claim number: '{claim_num}'")
                continue
                
            # MASTER LIST ENFORCEMENT
            if master_claim_list:
                # Use case-insensitive comparison but be cautious with leading zeros
                if claim_num not in master_claim_list and claim_num.lstrip('0') not in [m.lstrip('0') for m in master_claim_list]:
                    print(f"      🗑️  Filtering claim {claim_num} (Not in master claim list)")
                    continue

            if claim_num not in seen_claim_numbers:
                seen_claim_numbers[claim_num] = (claim, quality_score)
            else:
                existing_claim, old_score = seen_claim_numbers[claim_num]
                # RC4 FIX: Only replace if new claim has STRICTLY better math.
                # When scores are equal, keep the FIRST (existing) extraction.
                # Replacing by field-count is dangerous — more non-zero fields
                # can mean more wrong values from a boundary-truncated chunk.
                if quality_score > old_score:
                    seen_claim_numbers[claim_num] = (claim, quality_score)
                # (equal score → keep existing — no replacement)
            
        # Rebuild claims list and apply global filters
        final_claims = []
        for claim, quality_score in seen_claim_numbers.values():
            # PHANTOM FILTER: Remove calibration placeholders
            name_raw = str(claim.get("employee_name", "")).lower().strip()
            name_clean = name_raw.replace(",", "").replace(".", "").strip()
            
            # Catch calibration examples and phantom placeholders
            # and generic business entities incorrectly identified as employees
            phantom_keywords = [
                "john smith", "smith john", "john doe", "doe john",
                "jane smith", "smith jane", "jane doe", "doe jane",
                "alice johnson", "johnson alice", "michael johnson", "johnson michael",
                "escort service", "insurance company", "employers insurance", "policy total"
            ]
            if any(k in name_clean for k in phantom_keywords):
                print(f"      🗑️  Filtering phantom/business claim: {claim.get('employee_name')}")
                continue
                
            if " llc" in name_clean or " corp" in name_clean or " inc" in name_clean:
                 print(f"      🗑️  Filtering suspected business entity: {claim.get('employee_name')}")
                 continue
                
            if any(f in name_raw for f in ["placeholder", "test person"]):
                continue
                
            # GLOBAL NAME NORMALIZATION (Ensure Last, First)
            raw_name = str(claim.get("employee_name", "")).strip()
            if raw_name and "," not in raw_name:
                name_parts = raw_name.split()
                if len(name_parts) >= 2:
                    last = name_parts[-1]
                    first = " ".join(name_parts[:-1])
                    claim["employee_name"] = f"{last}, {first}"
                    
            final_claims.append(claim)
            
        data["claims"] = final_claims
        
        # Log final validation results
        for claim in data["claims"]:
            is_valid, errors = self._validate_financial_data(claim)
            if not is_valid:
                print(f"   ⚠️  Financial validation warnings for {claim.get('claim_number')}:")
                for error in errors:
                    print(f"      - {error}")
                    
        return data
    
    def _validate_financial_data(self, claim: Dict) -> Tuple[bool, List[str]]:
        """
        Validate financial calculations for a claim
        Returns: (is_valid, list_of_errors)
        """
        errors = []
        tolerance = 2.0  # Increased tolerance to $2.00 to account for rounding errors and minor discrepancies
        
        # Get values
        medical_paid = claim.get('medical_paid', 0.0) or 0.0
        medical_reserve = claim.get('medical_reserve', 0.0) or 0.0
        indemnity_paid = claim.get('indemnity_paid', 0.0) or 0.0
        indemnity_reserve = claim.get('indemnity_reserve', 0.0) or 0.0
        expense_paid = claim.get('expense_paid', 0.0) or 0.0
        expense_reserve = claim.get('expense_reserve', 0.0) or 0.0
        total_incurred = claim.get('total_incurred', 0.0) or 0.0
        
        # Calculate expected totals
        medical_incurred = medical_paid + medical_reserve
        indemnity_incurred = indemnity_paid + indemnity_reserve
        expense_incurred = expense_paid + expense_reserve
        
        calculated_total = (medical_incurred + indemnity_incurred + expense_incurred)
        
        # Validate total incurred
        if abs(calculated_total - total_incurred) > tolerance:
            # Check for common mapping errors to provide hints
            # HINT 1: Swapped Medical and Indemnity
            swapped_total = (indemnity_incurred + medical_incurred + expense_incurred)
            # (Wait, if only columns are swapped (Paid Med <-> Paid Ind), calculated_total remains same)
            
            # HINT 1: Using TOTAL row instead of category row (The BerkleyNet error)
            # If any individual category matches the calculated total, it might be a mapping leak
            if abs(medical_paid - calculated_total) < tolerance:
                errors.append(f"Possible mapping leak: medical_paid ({medical_paid}) matches total sum.")
            
            errors.append(
                f"Total mismatch: calculated ${calculated_total:.2f} != reported ${total_incurred:.2f}"
            )
        
        # Check for negative values
        for field in ['medical_paid', 'medical_reserve', 'indemnity_paid', 
                      'indemnity_reserve', 'expense_paid', 'expense_reserve', 'total_incurred']:
            value = claim.get(field, 0.0) or 0.0
            if value < 0:
                errors.append(f"{field} is negative: ${value:.2f}")
        
        is_valid = len(errors) == 0
        return is_valid, errors
    
    
    def _extract_missing_claims_by_number(self, all_text: str, existing_data: Dict, missing_claim_numbers: List[str], is_correction: bool = False) -> Dict:
        """
        Retry extraction for specific missing claim numbers identified by AI
        OR retry if math validation failed (is_correction=True).
        """
        if not missing_claim_numbers:
            return {"claims": []}
            
        retry_type = "CORRECTION" if is_correction else "RECOVERY"
        print(f"   [{retry_type}] Attempting matching for: {', '.join(missing_claim_numbers)}")
        
        correction_note = ""
        if is_correction:
            correction_note = """
⚠️ MATH VALIDATION FAILED for these claims in the previous pass. 

RE-EXAMINE the column headers and row labels for these specific IDs.
CRITICAL: 
1. Do NOT map 'TOTAL' row values to 'MEDICAL' or 'INDEMNITY' fields.
2. Distinguish between 'Paid' (Payments) and 'Reserves' (Outstanding).
3. Ensure Med(Paid+Res) + Indem(Paid+Res) + Exp(Paid+Res) == Total Incurred.
"""

        retry_prompt = f"""You are an expert insurance data extractor.
{correction_note}

Your Task: Extract COMPLETE data for ONLY these specific claim numbers:
{', '.join(missing_claim_numbers)}

Return a JSON object with this structure:
{{
  "claims": [
{{
  "employee_name": "full name",
  "claim_number": "exact claim number",
  "injury_date_time": "YYYY-MM-DD",
  "status": "Open/Closed/Reopened",
  "injury_description": "description",
  "body_part": "body part or null",
  "injury_type": "Indemnity or Medical Only or Expense",
  "claim_class": "STRICTLY NUMERIC class code ONLY. NO letters. Correct typos. Null if missing",
  "medical_paid": "string (e.g. '1,234.56')",
  "medical_reserve": "string",
  "indemnity_paid": "string",
  "indemnity_reserve": "string",
  "expense_paid": "string",
  "expense_reserve": "string",
  "total_paid": "string",
  "total_reserve": "string",
  "total_incurred": "string",
  "litigation": "Yes if explicitly Yes/Y, else No",
  "confidence_score": "0.0 to 1.0 (float)"
}}
  ]
}}

STRICT RULES:
1. DO NOT include any claims NOT in the list above.
2. Ensure math balances perfectly (Med+Ind+Exp = Total).
3. Extract exactly what you see in the text, preserving layout context.

TEXT TO ANALYZE:
{all_text}

Return ONLY the JSON."""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": retry_prompt}],
                response_format={"type": "json_object"},
                max_tokens=8000,
                temperature=0.0
            )
            
            retry_data = json.loads(response.choices[0].message.content)
            if "claims" in retry_data:
                retry_data = self._post_process_claims(retry_data)
                return retry_data
            return {"claims": []}
        except Exception as e:
            print(f"      ⚠️  Extraction retry failed: {e}")
            return {"claims": []}
    
    def _extract_single_claim(self, all_text: str, target_claim_number: str) -> Dict:
        """
        Extract only a specific claim by claim number
        """
        prompt = f"""You are extracting structured data from an insurance document.

This document may contain MULTIPLE claims, but you should extract ONLY the claim with number: {target_claim_number}

Return a JSON object with this structure:

{{
  "employee_name": "full claimant name",
  "carrier_name": "insurance company name",
  "policy_number": "policy identifier",
  "claim_number": "{target_claim_number}",
  "injury_date_time": "YYYY-MM-DD",
  "claim_year": 2020,
  "status": "Open/Closed/REOP",
  "reopen": "True or False",
  "injury_description": "cause of injury",
  "body_part": "injured body part",
  "injury_type": "COMP/MEDI/etc",
  "claim_class": "STRICTLY NUMERIC class code ONLY (NO letters/descriptions). Correct typos. Null if missing",
  "medical_paid": 0.0,
  "medical_reserve": 0.0,
  "indemnity_paid": 0.0,
  "indemnity_reserve": 0.0,
  "expense_paid": 0.0,
  "expense_reserve": 0.0,
  "total_paid": 0.0,
  "total_reserve": 0.0,
  "total_incurred": 0.0,
  "litigation": "Y if explicitly Yes/Y, else N",
  "confidence_score": 0.0
}}

RULES:
1. Find the claim with number {target_claim_number}
2. Extract ONLY that claim's data
3. Ignore all other claims in the document
4. Status codes: C=Closed, O=Open, REOP=Reopened
5. Litigation: If the document explicitly shows 'Yes' or 'Y' for legal/litigation status, return 'Y'. Otherwise, return 'N'.
6. Remove $ and commas from amounts

TEXT TO ANALYZE:
{all_text}

Return ONLY the JSON object for claim {target_claim_number}."""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": prompt
                }],
                response_format={"type": "json_object"},
                max_tokens=8000,
                temperature=0.1
            )
            
            response_text = response.choices[0].message.content
            data = json.loads(response_text)
            
            # Wrap in 'claims' list for post-processing consistency
            wrapped_data = {"claims": [data]}
            processed_data = self._post_process_claims(wrapped_data)
            
            # Extract the single processed claim back
            if processed_data.get("claims"):
                data = processed_data["claims"][0]
                
            print(f"✓ Extracted and processed claim #{target_claim_number}")
            return data
            
        except Exception as e:
            print(f"❌ Error extracting schema: {e}")
            return {}
    
    def validate_extraction(self, data: Dict, original_text: str) -> Dict:
        """
        Validate that all claims in the text were extracted
        """
        print(f"\n🔍 Validating extraction...")
        
        # Find all claim numbers mentioned in text
        claim_numbers_in_text = set()
        
        # Pattern 1: "Claim# 20677" or "Claim #20677"
        for match in re.finditer(r'Claim#?\s*(\d+)', original_text, re.IGNORECASE):
            claim_numbers_in_text.add(match.group(1))
            
        # Get claim numbers from extracted data
        if "claims" in data:
            # Multi-claim format
            claim_numbers_extracted = {
                str(claim.get("claim_number", "")) 
                for claim in data.get("claims", [])
                if claim.get("claim_number")
            }
        else:
            # Single claim format
            claim_num = data.get("claim_number")
            claim_numbers_extracted = {str(claim_num)} if claim_num else set()
        
        # Check for missing claims
        missing_claims = claim_numbers_in_text - claim_numbers_extracted
        extra_claims = claim_numbers_extracted - claim_numbers_in_text
        
        validation_report = {
            "total_in_text": len(claim_numbers_in_text),
            "total_extracted": len(claim_numbers_extracted),
            "missing_claims": list(missing_claims),
            "extra_claims": list(extra_claims),
            "is_complete": len(missing_claims) == 0
        }
        
        # Print report
        print(f"   Claims in text: {len(claim_numbers_in_text)}")
        print(f"   Claims extracted: {len(claim_numbers_extracted)}")
        
        if missing_claims:
            print(f"   ⚠️  MISSING: {', '.join(missing_claims)}")
        
        if extra_claims:
            print(f"   ⚠️  EXTRA: {', '.join(extra_claims)}")
        
        if validation_report["is_complete"]:
            print(f"   ✓ Extraction is COMPLETE")
        else:
            print(f"   ❌ Extraction is INCOMPLETE")
        
        return validation_report

    
    def process_pdf_with_verification(self, pdf_path: str, target_claim_number: Optional[str] = None) -> Dict:
        """
        Complete pipeline with verification steps
        Uses PyMuPDF + Tesseract for text extraction
        Returns all extracted data for user verification
        """
        print(f"\n{'='*60}")
        print(f"🚀 PROCESSING: {os.path.basename(pdf_path)}")
        print(f"{'='*60}")
        
        # Create session output directory with high precision and filename for uniqueness
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:20] # Add microseconds
        file_slug = os.path.basename(pdf_path).replace(" ", "_").replace(".", "_")[:20]
        session_id = f"{timestamp}_{file_slug}"
        session_dir = self.output_dir / f"extraction_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        
        # Step 1: Extract text from PDF using appropriate method
        all_text, pages_metadata = self.extract_text_from_pdf(pdf_path)
        
        # Determine extraction method from metadata
        extraction_method = "pymupdf-tesseract-enhanced"
        if pages_metadata and len(pages_metadata) > 0:
            extraction_method = pages_metadata[0].get("extraction_method", extraction_method)

        # Prepare page data for compatibility
        pages_data = pages_metadata
        
        # Save combined text for verification
        text_file = session_dir / "extracted_text.txt"
        with open(text_file, 'w', encoding='utf-8') as f:
            f.write(all_text)
        print(f"\n✓ Combined text saved: {text_file}")
        
        # Step 2: Extract schema from combined text
        print(f"\n{'='*60}")
        print(f"📋 SCHEMA EXTRACTION")
        print(f"{'='*60}")
        
        schema_data = self.extract_schema_from_text(all_text, target_claim_number)
        
        # Step 2.5: Advanced Modular Validation (Final Layers)
        print(f"\n🧪 RUNNING ADVANCED VALIDATION...")
        try:
            # Financial Validation & Pattern Discovery
            fin_validator = FinancialValidator(api_key=self.api_key)
            vision_pattern = None
            if pdf_path:
                vision_pattern = fin_validator.identify_calculation_pattern(all_text[:8000], pdf_path=pdf_path)
            
            # Re-extract with vision pattern if needed (Wait, we need to pass it to the extraction call)
            # This requires modifying how extract_schema_from_text is called
            schema_data = self.extract_schema_from_text(all_text, target_claim_number, num_pages=len(pages_metadata), vision_pattern=vision_pattern)
            
            # Validation Step
            schema_data["claims"] = fin_validator.validate_claims(schema_data.get("claims", []), vision_pattern or {})
            
            # General Data Validation
            gen_validator = GeneralDataValidator(api_key=self.api_key)
            schema_data["claims"] = gen_validator.validate_consistency(schema_data.get("claims", []), all_text[:15000], pdf_path=pdf_path)
            
            print(f"   ✓ Advanced validation complete")
        except Exception as e:
            print(f"   ⚠️  Advanced validation encountered an issue: {e}")
        
        # Validate extraction
        validation = self.validate_extraction(schema_data, all_text)
        
        # Print metadata to terminal (not saved to JSON)
        print(f"\n{'='*60}")
        print(f"📊 EXTRACTION METADATA")
        print(f"{'='*60}")
        print(f"Session ID: {session_id}")
        print(f"Source File: {os.path.basename(pdf_path)}")
        print(f"Total Pages: {len(pages_metadata)}")
        print(f"Extraction Method: {extraction_method}")
        print(f"Validation: {validation['total_extracted']} claims extracted, {len(validation['missing_claims'])} missing")
        
        # Add minimal metadata to JSON (without pages_metadata)
        extraction_metadata = {
            "extraction_date": datetime.now().isoformat(),
            "method": extraction_method,
            "num_pages": len(pages_metadata),
            "source_file": os.path.basename(pdf_path),
            "session_id": session_id,
            "target_claim": target_claim_number
        }
        schema_data['extraction_metadata'] = extraction_metadata
        
        # Prepare claims array for schema output (strip internal validation fields)
        raw_claims = schema_data.get("claims", []) or []
        claims_only = []
        for claim in raw_claims:
            if isinstance(claim, dict):
                cleaned = {
                    k: v
                    for k, v in claim.items()
                    if k not in ("confidence_score", "math_valid", "math_diff")
                }
                claims_only.append(cleaned)
            else:
                claims_only.append(claim)

        # Apply year filter BEFORE writing anything so analysis.json always reflects the filter
        included_claims, excluded_claims, unknown_year_claims = filter_claims_by_claim_year(
            claims_only,
            min_year_inclusive=MIN_INCLUDED_CLAIM_YEAR,
            keep_unknown_year=True,
        )
        print(f"\n🗓️  Year filter (>= {MIN_INCLUDED_CLAIM_YEAR}): {len(included_claims)} included, {len(excluded_claims)} excluded, {len(unknown_year_claims)} unknown year")

        # Create and save analysis.json in a single write with all fields
        analysis_data = {
            "extraction_metadata": extraction_metadata,
            "report_date": schema_data.get("report_date"),
            "policy_number": schema_data.get("policy_number"),
            "insured_name": schema_data.get("insured_name"),
            "policy_period": schema_data.get("policy_period"),
            "total_claims": len(raw_claims),
            "year_filter": {
                "min_claim_year_inclusive": MIN_INCLUDED_CLAIM_YEAR,
                "keep_unknown_year": True,
                "included_claims_count": len(included_claims),
                "excluded_claims_count": len(excluded_claims),
                "unknown_year_claims_count": len(unknown_year_claims),
            },
            "excluded_claims_before_year_threshold": excluded_claims,
        }
        analysis_file = session_dir / "analysis.json"
        with open(analysis_file, 'w', encoding='utf-8') as f:
            json.dump(analysis_data, f, indent=2, ensure_ascii=False)
        print(f"✓ Analysis saved: {analysis_file}")
        
        # Compute summary fields from claims and header data
        years_set = set()
        for claim in included_claims:
            year = claim.get("claim_year")
            if year:
                years_set.add(year)
        years_sorted = sorted(years_set)
        header_policy_number = schema_data.get("policy_number")
        header_carrier_name = schema_data.get("carrier_name")
        
        # Parse Estimated Annual amount from combined text
        estimated_annual_value, _ = self._parse_estimated_annual(all_text)
        
        # Build final schema object with claims array and SummaryLevel
        summary_level = []
        for y in years_sorted:
            y_str = str(y)

            year_policy_numbers = set()
            if header_policy_number:
                year_policy_numbers.add(header_policy_number)
            for claim in included_claims:
                if str(claim.get("claim_year")) != y_str:
                    continue
                policy_number = claim.get("policy_number")
                if policy_number:
                    year_policy_numbers.add(policy_number)
            policy_number_value = sorted(year_policy_numbers)[0] if year_policy_numbers else None

            year_carrier_names = set()
            if header_carrier_name:
                year_carrier_names.add(header_carrier_name)
            for claim in included_claims:
                if str(claim.get("claim_year")) != y_str:
                    continue
                carrier_name = claim.get("carrier_name")
                if carrier_name:
                    year_carrier_names.add(carrier_name)
            carrier_name_value = sorted(year_carrier_names)[0] if year_carrier_names else None

            summary_level.append(
                {
                    "estimated_annual": estimated_annual_value,
                    "year": y_str,
                    "policy_number": policy_number_value,
                    "carrier_name": carrier_name_value,
                }
            )
        schema_output = {
            "claims": included_claims,
            "SummaryLevel": summary_level,
            "claimsCount": {
                "lastFiveYears": len(included_claims),
                "olderThanFiveYears": len(excluded_claims),
                "total": len(included_claims) + len(excluded_claims)
            }
        }
        schema_file = session_dir / "extracted_schema.json"
        with open(schema_file, 'w', encoding='utf-8') as f:
            json.dump(schema_output, f, indent=2, ensure_ascii=False)
        print(f"✓ Schema saved: {schema_file}")
        
        # Step 3: Prepare verification package (for internal use only)
        # Note: verification_data contains full schema_data for internal processing
        # But extracted_schema.json file only contains claims array
        verification_data = {
            "session_id": session_id,
            "session_dir": str(session_dir),
            "source_pdf": pdf_path,
            "pages": pages_data,
            "combined_text": all_text,
            "combined_text_file": str(text_file),
            "extracted_schema": schema_output,
            "schema_file": str(schema_file),
            "summary": {
                "total_pages": len(pages_metadata),
                "scanned_pages": sum(1 for p in pages_metadata if p.get('is_scanned', False)),
                "avg_confidence": sum(p.get('confidence', 0.0) for p in pages_metadata) / len(pages_metadata) if pages_metadata else 0.0,
                "extraction_methods": [p.get('extraction_method', 'unknown') for p in pages_metadata]
            }
        }
        
        # Save verification package
        verification_file = session_dir / "verification_package.json"
        with open(verification_file, 'w', encoding='utf-8') as f:
            json.dump(verification_data, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"\n{'='*60}")
        print(f"✅ EXTRACTION COMPLETE")
        print(f"{'='*60}")
        print(f"Session: {session_id}")
        print(f"Output: {session_dir}")
        print(f"\nFiles created:")
        print(f"  - extracted_text.txt (combined text)")
        print(f"  - extracted_schema.json (structured data)")
        print(f"  - verification_package.json (full package)")
        print(f"\nExtraction Summary:")
        print(f"  - Total pages: {verification_data['summary']['total_pages']}")
        print(f"  - Scanned pages: {verification_data['summary']['scanned_pages']}")
        print(f"  - Avg confidence: {verification_data['summary']['avg_confidence']:.2%}")
        
        return verification_data
    
    def _parse_estimated_annual(self, text: str) -> Tuple[Optional[float], Optional[str]]:
        """
        Parse the 'Estimated Annual' dollar amount from the combined text.
        Returns (numeric_value, display_string). If not found or unparsable,
        numeric_value defaults to 0.0.
        """
        if not text:
            return 0.0, None
        
        # Look for patterns like 'Estimated Annual $59,019'
        match = re.search(
            r"Estimated\s+Annual\s*([$\\s]*[0-9][0-9,]*(?:\.[0-9]{1,2})?)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return 0.0, None
        
        display_value = match.group(1).strip()
        # Normalize to digits and decimal point for numeric value
        numeric_str = re.sub(r"[^0-9.]", "", display_value)
        if not numeric_str:
            return 0.0, display_value or None
        
        try:
            numeric_value: Optional[float] = float(numeric_str)
        except ValueError:
            numeric_value = 0.0
        
        return numeric_value, display_value or None


def main():
    """Main function"""
    import sys
    from dotenv import load_dotenv
    
    # Load environment variables
    load_dotenv()
    
    # Check for API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("\n❌ OPENAI_API_KEY not set!")
        print("Set it with: export OPENAI_API_KEY='sk-...'")
        print("Get your key from: https://platform.openai.com/api-keys")
        return
    
    extractor = EnhancedInsuranceExtractor(api_key)
    
    # Get PDF path
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
        target_claim = sys.argv[2] if len(sys.argv) > 2 else None
    else:
        upload_dir = "/mnt/user-data/uploads"
        if os.path.exists(upload_dir):
            pdfs = [f for f in os.listdir(upload_dir) if f.lower().endswith('.pdf')]
            if pdfs:
                pdf_path = os.path.join(upload_dir, pdfs[0])
                print(f"Found PDF: {pdf_path}")
                target_claim = None
            else:
                print("Usage: python enhanced_extractor.py <pdf_path> [claim_number]")
                return
        else:
            print("Usage: python enhanced_extractor.py <pdf_path> [claim_number]")
            return
    
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        return
    
    # Process with verification
    result = extractor.process_pdf_with_verification(pdf_path, target_claim)
    
    if "error" in result:
        print(f"\n❌ Error: {result['error']}")
        return
    
    # Display summary
    print("\n" + "="*60)
    print("EXTRACTION SUMMARY")
    print("="*60)
    print(f"Pages processed: {result['summary']['total_pages']}")
    print(f"Scanned pages: {result['summary']['scanned_pages']}")
    print(f"Avg confidence: {result['summary']['avg_confidence']:.2%}")
    print(f"\nOrientations: {', '.join(result['summary'].get('orientations', []))}")
    
    print("\n" + "="*60)
    print("EXTRACTED SCHEMA")
    print("="*60)
    print(json.dumps(result['extracted_schema'], indent=2, default=str))
    
    print("\n" + "="*60)
    print(f"✓ All files saved to: {result['session_dir']}")
    print("="*60)


if __name__ == "__main__":
    main()