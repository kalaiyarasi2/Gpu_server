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
from config import config
from dataclasses import dataclass, asdict
from datetime import datetime
import re
from pathlib import Path
import subprocess
import sys
from io import BytesIO
import time

try:
    from monitor.service import request_monitor
except ImportError:
    request_monitor = None

try:
    import fitz  # PyMuPDF
    from PIL import Image
    import pytesseract
    from openai import OpenAI
except ImportError:
    print("Installing required packages...")
    packages = ["pymupdf", "pytesseract", "Pillow", "openai"]
    for pkg in packages:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
    import fitz
    from PIL import Image
    import pytesseract
    from openai import OpenAI


@dataclass
class PageExtraction:
    """Data for a single page"""
    page_number: int
    image_path: str
    raw_text: str
    orientation: str  # 'portrait' or 'landscape'
    is_scanned: bool
    confidence: float


# NEW SCHEMA DEFINITION
WORKERS_COMP_SCHEMA = {
    "name": "insurance_response",
    "description": "Schema for an insurance response containing demographics, rating by state, general questions, and prior carriers.",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "properties": {
                    "demographics": {
                        "type": "object",
                        "properties": {
                            "applicantName": { "type": "string" },
                            "businessDescription": { "type": "string" },
                            "email": { "type": "string" },
                            "fein": { "type": "string" },
                            "mailingStreet": { "type": "string" },
                            "mailingCity": { "type": "string" },
                            "mailingState": { "type": "string" },
                            "mailingZip": { "type": "string" },
                            "officePhone": { "type": "string" },
                            "mobilePhone": { "type": "string" },
                            "website": { "type": "string" },
                            "yearsInBusiness": { "type": ["number", "string"] },
                            "sicCode": { "type": ["number", "string"] },
                            "naicsCode": { "type": ["number", "string"] },
                            "proposedEffectiveDate": { "type": "string" },
                            "proposedExpirationDate": { "type": "string" },
                            "wcStates": { "type": "string" },
                            "agencyCustomerId": { "type": "string" }
                        },
                        "required": [
                            "applicantName", "mailingStreet", "mailingCity", "mailingState", "mailingZip",
                            "officePhone", "mobilePhone", "email", "website", "yearsInBusiness",
                            "sicCode", "naicsCode", "fein", "proposedEffectiveDate", "proposedExpirationDate",
                            "wcStates", "businessDescription", "agencyCustomerId"
                        ],
                        "additionalProperties": False
                    },
                    "generalQuestions": {
                        "type": "object",
                        "properties": {
                            "q1": { "type": "string" }, "q2": { "type": "string" }, "q3": { "type": "string" },
                            "q4": { "type": "string" }, "q5": { "type": "string" }, "q6": { "type": "string" },
                            "q7": { "type": "string" }, "q8": { "type": "string" }, "q9": { "type": "string" },
                            "q10": { "type": "string" }, "q11": { "type": "string" }, "q12": { "type": "string" },
                            "q13": { "type": "string" }, "q14": { "type": "string" }, "q15": { "type": "string" },
                            "q16": { "type": "string" }, "q17": { "type": "string" }, "q18": { "type": "string" },
                            "q19": { "type": "string" }, "q20": { "type": "string" }, "q21": { "type": "string" },
                            "q22": { "type": "string" }, "q23": { "type": "string" }, "q24": { "type": "string" }
                        },
                        "required": ["q1","q2","q3","q4","q5","q6","q7","q8","q9","q10","q11","q12","q13","q14","q15","q16","q17","q18","q19","q20","q21","q22","q23","q24"],
                        "additionalProperties": False
                    },
                    "priorCarriers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "year": { "type": "number" },
                                "carrierName": { "type": "string" },
                                "policyNumber": { "type": "string" },
                                "experienceMod": { "type": ["number", "string"] },
                                "annualPremium": { "type": ["number", "string"] },
                                "numberOfClaims": { "type": ["number", "string"] },
                                "amountPaid": { "type": ["number", "string"] },
                                "reserveAmount": { "type": ["number", "string"] }
                            },
                            "required": ["year","carrierName","policyNumber","annualPremium","experienceMod","numberOfClaims","amountPaid","reserveAmount"],
                            "additionalProperties": False
                        }
                    },
                    "ratingByState": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "state": { "type": "string" },
                                "classCode": { "type": "number" },
                                "fullTimeEmployees": { "type": ["number", "string"] },
                                "partTimeEmployees": { "type": ["number", "string"] },
                                "estAnnualPayroll": { "type": ["number", "string"] },
                                "ratePer100Payroll": { "type": ["number", "string"] },
                                "estAnnualPremium": { "type": ["number", "string"] }
                            },
                            "required": ["state","classCode","fullTimeEmployees","partTimeEmployees","estAnnualPayroll","ratePer100Payroll","estAnnualPremium"],
                            "additionalProperties": False
                        }
                    },
                    "individuals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": { "type": "string" },
                                "title": { "type": "string" },
                                "ownershipPercentage": { "type": ["number", "string"] },
                                "included": { "type": "string" }
                            },
                            "required": ["name", "title", "ownershipPercentage", "included"],
                            "additionalProperties": False
                        }
                    },
                    "premiumCalculation": {
                        "type": "object",
                        "properties": {
                            "totalEstimatedAnnualPremium": { "type": ["number", "string"] },
                            "experienceModification": { "type": ["number", "string"] },
                            "minimumPremium": { "type": ["number", "string"] },
                            "depositPremium": { "type": ["number", "string"] }
                        },
                        "required": ["totalEstimatedAnnualPremium", "experienceModification", "minimumPremium", "depositPremium"],
                        "additionalProperties": False
                    }
                },
                "required": ["demographics","ratingByState","generalQuestions","priorCarriers", "individuals", "premiumCalculation"],
                "additionalProperties": False
            }
        },
        "required": ["data"],
        "additionalProperties": False
    }
}


class EnhancedInsuranceExtractor:
    """Enhanced extractor with layout awareness and verification"""
    
    def __init__(self, api_key: Optional[str] = None, output_dir: Optional[str] = None, request_id: Optional[str] = None):
        """Initialize with OpenAI API"""
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.request_id = request_id
        
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
            print("✓ GPT-4 Vision API initialized")
        else:
            raise ValueError("OPENAI_API_KEY is required for enhanced extraction")
        
        self.output_dir = Path(output_dir) if output_dir else Path("outputs")
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def extract_text_from_pdf(self, pdf_path: str) -> Tuple[str, List[Dict]]:
        """
        Extract text from PDF using page-level hybrid strategy (Digital + OCR + Form Fields).
        Covers: Scanned, Digital, Combined, and Editable PDFs.
        """
        # Use importlib for explicit path-based import to avoid sys.modules collision
        # with the Insurance backend's pdf_detector (which lacks is_page_scanned)
        import importlib.util as _ilu
        import pathlib as _pl
        _wc_dir = _pl.Path(__file__).parent
        def _load_wc_module(name):
            spec = _ilu.spec_from_file_location(
                f"_wc_{name}", _wc_dir / f"{name}.py"
            )
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        _pdf_detector_mod = _load_wc_module("pdf_detector")
        PDFDetector = _pdf_detector_mod.PDFDetector
        _pdf_plumber_mod = _load_wc_module("pdf_plumber")
        extract_pdf_with_pdfplumber = _pdf_plumber_mod.extract_pdf_with_pdfplumber
        extract_form_data = _pdf_plumber_mod.extract_form_data
        _ocr_text_mod = _load_wc_module("ocr_text")
        OCRPDFExtractor = _ocr_text_mod.OCRPDFExtractor
        
        try:
            print(f"🔍 Analyzing PDF structure for 100% coverage...")
            detector = PDFDetector(pdf_path)
            
            # 1. CHECK FOR VISION EXTRACTION OVERRIDE
            if getattr(config, 'USE_VISION_EXTRACTION', False):
                print(f"👁️ VISION EXTRACTION ENABLED: Processing images with {config.VISION_MODEL}")
                return self._extract_via_vision(pdf_path)

            # 2. EXTRACT FORM FIELD DATA (Top Priority for Editable PDFs)
            form_data = extract_form_data(pdf_path)
            if form_data:
                print(f"✅ Extracted data from fillable form fields/XFA.")

            # 3. PAGE-LEVEL HYBRID EXTRACTION (4-Stage Flow)
            all_text_parts = []
            if form_data:
                all_text_parts.append(form_data)
            
            pages_metadata = []
            
            # Initial digital extraction
            digital_text, digital_metadata = extract_pdf_with_pdfplumber(pdf_path)
            total_pages = len(digital_metadata)
            
            _verifier_mod = _load_wc_module("text_quality_verifier")
            TextQualityVerifier = _verifier_mod.TextQualityVerifier
            verifier = TextQualityVerifier()
            
            print(f"📄 Processing {total_pages} pages using Unified 4-Stage Strategy...")
            
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from parallel_processor import ParallelPageProcessor
            
            # --- STAGE 1: Identify pages needing OCR ---
            pages_to_ocr = []
            for i in range(total_pages):
                page_meta = digital_metadata[i]
                page_text = page_meta.get("text", "")
                quality = verifier.analyze_quality(page_text, num_pages=1)
                
                if not quality['is_acceptable']:
                    page_meta["rejection_reason"] = quality['reason']
                    page_meta["quality_metrics"] = quality.get('metrics', {})
                    pages_to_ocr.append(i + 1)
                else:
                    pages_metadata.append(page_meta)
                    all_text_parts.append(page_text)
            
            # --- STAGE 2: Process low-quality pages in parallel ---
            if pages_to_ocr:
                print(f"   ⚡ Digital quality low for {len(pages_to_ocr)} pages. Parallel OCR start...")
                processor = ParallelPageProcessor(pdf_path, api_key=self.api_key, max_workers=4)
                
                with ThreadPoolExecutor(max_workers=4) as executor:
                    future_to_page = {executor.submit(processor.process_page, p_num): p_num for p_num in pages_to_ocr}
                    
                    ocr_results = {}
                    for future in as_completed(future_to_page):
                        p_num = future_to_page[future]
                        try:
                            ocr_results[p_num] = future.result()
                        except Exception as e:
                            print(f"   ⚠️ Parallel OCR failed for page {p_num}: {e}")

                # Rebuild the final list in correct sequence
                final_metadata = []
                final_text_parts = [form_data] if form_data else []
                
                ocr_count = 0
                for i in range(total_pages):
                    p_num = i + 1
                    if p_num in ocr_results:
                        res = ocr_results[p_num]
                        final_metadata.append({
                            "page_number": p_num,
                            "text": res.get("text", ""),
                            "extraction_method": res.get("extraction_method"),
                            "confidence": res.get("confidence"),
                            "is_scanned": True
                        })
                        final_text_parts.append(res.get("text", ""))
                        ocr_count += 1
                    else:
                        # Find the existing digital meta
                        # (We need to be careful with ordering here)
                        for dm in digital_metadata:
                            if dm.get("page_number") == p_num:
                                final_metadata.append(dm)
                                final_text_parts.append(dm.get("text", ""))
                                break
                
                pages_metadata = final_metadata
                all_text_parts = final_text_parts
                print(f"   ✓ Parallel OCR finished. {ocr_count} pages recovered.")
            else:
                # All pages were acceptable digitally
                pages_metadata = digital_metadata
                all_text_parts = [form_data] + [m.get("text", "") for m in digital_metadata] if form_data else [m.get("text", "") for m in digital_metadata]
            
            # Step 4: Logical Rearrangement (Rearrange correctly after extraction)
            print(f"📊 Rearranging {len(pages_metadata)} pages logically based on content...")
            pages_metadata = self._rearrange_pages_logically(pages_metadata)
            
            # Rebuild combined text from sorted metadata
            # form_data always stays at the very top as it has no page number
            final_parts = [form_data] if form_data else []
            for m in pages_metadata:
                final_parts.append(m.get("text", ""))
            
            combined_text = "\n\n".join(final_parts)
            return combined_text, pages_metadata
                
        except Exception as e:
            print(f"⚠️ Detection/Extraction error: {e}")
            import traceback
            traceback.print_exc()
            print(f"   Falling back to standard pdfplumber...")
            from pdf_plumber import extract_pdf_with_pdfplumber as external_extract
            return external_extract(pdf_path)
    
    def _extract_via_vision(self, pdf_path: str) -> Tuple[str, List[Dict]]:
        """
        Extract text directly via GPT-4 Vision from PDF page images.
        """
        import fitz
        from PIL import Image
        import io
        
        doc = fitz.open(pdf_path)
        all_text = []
        metadata = []
        
        print(f"📸 Processing {len(doc)} pages via Vision...")
        
        for i in range(len(doc)):
            page = doc[i]
            pix = page.get_pixmap(dpi=config.OCR_DPI)
            img_data = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_data))
            
            print(f"   Page {i+1}: Extracting structure via Vision...")
            text, is_scanned, confidence = self._extract_page_with_vision(img)
            
            page_header = f"\n{'='*80}\nPAGE {i+1}\n{'='*80}\n\n"
            all_text.append(page_header + text)
            
            metadata.append({
                "page_number": i + 1,
                "text": page_header + text,
                "is_scanned": is_scanned,
                "extraction_method": f"gpt-4-vision-{config.VISION_MODEL}",
                "confidence": confidence
            })
            
        return "\n\n".join(all_text), metadata

    def _extract_page_with_vision(self, image: Image.Image) -> Tuple[str, bool, float]:
        """
        Extract text from a single image using GPT-4 Vision.
        """
        import io
        buffered = io.BytesIO()
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
"""
        try:
            start_time = time.time()
            response = self.client.chat.completions.create(
                model=config.VISION_MODEL,
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
                max_tokens=4000,
                temperature=0.0
            )
            elapsed = time.time() - start_time
            if self.request_id and request_monitor:
                request_monitor.record_ai_usage(
                    request_id=self.request_id,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    processing_time=elapsed,
                    model=config.VISION_MODEL
                )
            
            response_text = response.choices[0].message.content
            
            # Simplified parsing for the vision response
            extracted_text = ""
            is_scanned = False
            confidence = 0.9
            
            if "[EXTRACTED TEXT - LAYOUT PRESERVED]" in response_text:
                parts = response_text.split("[DOCUMENT ANALYSIS]")
                extracted_text = parts[0].replace("[EXTRACTED TEXT - LAYOUT PRESERVED]", "").strip().strip('`').strip()
                if len(parts) > 1:
                    if "Is Scanned: yes" in parts[1].lower():
                        is_scanned = True
                    conf_match = re.search(r'Confidence:\s*([\d\.]+)', parts[1])
                    if conf_match:
                        confidence = float(conf_match.group(1))
            else:
                extracted_text = response_text
                
            return extracted_text, is_scanned, confidence
        except Exception as e:
            print(f"❌ Vision error: {e}")
            return "", False, 0.0
    
    
    def _rearrange_pages_logically(self, pages_metadata: List[Dict]) -> List[Dict]:
        """
        Sorts pages based on logical page numbers detected in the text.
        """
        import re
        
        def get_sort_key(p_meta):
            text = p_meta.get("text", "")
            # Remove synthetic "PAGE X" headers added by our extractors to find real markers
            clean_text = re.sub(r'^={10,}\s*PAGE\s+\d+\s*={10,}', '', text, flags=re.IGNORECASE | re.DOTALL).strip()
            
            # Priority 1: "Page X of Y" or "Page X / Y"
            match = re.search(r'Page\s*(\d+)\s*(?:of|/)\s*\d+', clean_text, re.IGNORECASE)
            if match: return int(match.group(1))
            
            # Priority 2: "Page X" (usually at top or bottom)
            sample = clean_text[:800] + "\n" + clean_text[-800:]
            match = re.search(r'Page\s*(\d+)', sample, re.IGNORECASE)
            if match: return int(match.group(1))
            
            # Priority 3: Simple digit at corners (REMOVED - TOO RISKY)
            # We now require an explicit 'Page' or 'Pg' marker to avoid confusion with Loc numbers.

            # Fallback: original physical page number
            return p_meta.get("page_number", 999)

        return sorted(pages_metadata, key=get_sort_key)
    
    
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
- Examples: "CLAIM-123", "ABC-456", "2024-001"
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
   - **Homestates Format**: Claim numbers are typically 8-digit integers (e.g., `44070643`).
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
   - "Claim #", "Claim Number", "Converted #" → INCLUDE
   
4. **Cross-Reference Check**:
   - Compare detected numbers against employee names
   - Each unique employee should have a unique claim number
   - If same number appears for multiple employees → POLICY number

For each claim number found, note:
   - The exact format/pattern it follows
   - Where it appears in the document
   - How confident you are it's a claim number (0.0-1.0)
   - Validation results from the checks above

Return a JSON object with this structure:

{{
  "claim_numbers": [
    {{
      "claim_number": "20825",
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
      "example": "Claim# 20825",
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
            start_time = time.time()
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
            elapsed = time.time() - start_time
            if self.request_id and request_monitor:
                request_monitor.record_ai_usage(
                    request_id=self.request_id,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    processing_time=elapsed,
                    model="gpt-4o"
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
            start_time = time.time()
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=1500,
                temperature=0.0
            )
            elapsed = time.time() - start_time
            if self.request_id and request_monitor:
                request_monitor.record_ai_usage(
                    request_id=self.request_id,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    processing_time=elapsed,
                    model="gpt-4o"
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
    
    def extract_schema_from_text(self, all_text: str, target_claim_number: Optional[str] = None) -> Dict:
        """
        Extract structured schema from verified text
        NOW SUPPORTS MULTIPLE CLAIMS!
        """
        print(f"\n🎯 Extracting schema from text...")
        
        # Decide whether to extract all claims or just one
        if target_claim_number:
            print(f"   Target: Claim #{target_claim_number} only")
            return self._extract_single_claim(all_text, target_claim_number)
        else:
            print(f"   Target: ALL claims in document")
            return self._extract_all_claims(all_text)
    
    def _analyze_document_format(self, text: str) -> Dict:
        """
        STAGE 1: Analyze document structure and format
        Let GPT-4 figure out how the data is organized
        """
        print(f"\n🔍 STAGE 1: Analyzing document format...")
        
        prompt = f"""You are analyzing a Workers' Compensation application form (ACORD 130) to understand its structure.

Your task: Describe HOW the data is organized in this document and identify the key parties.

CRITICAL ROLE DISTINCTION (The "Anchor" Rules):
1. **AGENCY**: This is the insurance broker/agent. They are usually located in the TOP-LEFT box. (Common examples: "Thomas & Thomas", "Insurance Services").
2. **APPLICANT**: This is the actual business being insured. They are usually located in the MIDDLE-LEFT or TOP-RIGHT box, often explicitly labeled "APPLICANT NAME" or "INSURED".

Answer these questions:
1. What is the Agency name? (Check top-left)
2. What is the Applicant/Insured name? (Check middle-left or top-right; Look for labels like 'APPLICANT NAME')
3. Are there tables for "Rating by State" or "Class Codes"?
4. Is there a section for "Prior Carriers" or "Loss History"?
5. Are there "General Questions" with Y/N answers?

Return JSON:
{{
  "agency": "agency name",
  "applicant": "business name",
  "has_rating_table": true/false,
  "has_prior_carriers": true/false,
  "has_questions": true/false,
  "special_notes": "e.g. Applicant is Macias, Agency is Thomas & Thomas",
  "confidence": 0.0-1.0
}}

DOCUMENT TEXT (first 8000 chars):
{text[:8000]}

Return ONLY the JSON."""

        try:
            start_time = time.time()
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=1500,
                temperature=0.0
            )
            elapsed = time.time() - start_time
            if self.request_id and request_monitor:
                request_monitor.record_ai_usage(
                    request_id=self.request_id,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    processing_time=elapsed,
                    model="gpt-4o"
                )
            
            format_info = json.loads(response.choices[0].message.content)
            
            print(f"   ✓ Format detected: {format_info.get('applicant', 'unknown')}")
            print(f"   ✓ Confidence: {format_info.get('confidence', 0.0):.2%}")
            
            return format_info
            
        except Exception as e:
            print(f"   ⚠️  Format analysis failed: {e}")
            return {
                "format_type": "unknown",
                "confidence": 0.0
            }
    
    def _extract_all_claims(self, all_text: str) -> Dict:
        """
        UNIVERSAL EXTRACTION: Works with ANY format
        Optimized for Workers' Compensation Application forms.
        """
        # STAGE 1: Analyze document format
        format_info = self._analyze_document_format(all_text)
        
        # STAGE 2: Build extraction prompt
        print(f"\n🎯 STAGE 2: Extracting application data using Workers' Comp schema...")
        
        prompt = f"""You are an expert at extracting structured data from Workers' Compensation application forms (ACORD 130).
        
ACORD FORM SPECIFIC RULES:
1. **Applicant Name vs Agency**: 
   - **DO NOT** use the Agency name (usually top-left) as the Applicant Name. 
   - The **Agency** (e.g., "Thomas & Thomas") is the broker.
   - The **Applicant** (e.g., "Macias Sheet Metal") is the client.
   - Extract only the **Applicant** name into the `applicantName` field.
   - Use the **MAILING ADDRESS** and Zip Code associated with the Applicant, not the Agency.
2. **Zip Codes**: Zip codes in DE/MD often start with 19 or 21. If you see "1980%", it is "19801". Use the **MAILING ADDRESS** zip code for demographics, not the Location addresses found lower in the form.
3. **Fuzzy Date Correction**: OCR often misreads years (e.g., "3024" for "2024", "1900" for "2026"). If a year is logically impossible (like 3024) or a placeholder (like 1900), look for the surrounding context or use the current year as a baseline.
4. **Fuzzy Carrier Correction**: Misread carrier names should be corrected to their most likely official name. Examples: 
   - "m trust vor th America" -> "AmTrust North America"
   - "Altas" -> "Atlas"
   - "State Fund" -> "State Compensation Insurance Fund"
6. **Rating Table Precision**:
   - **State-to-Value Alignment**: ALWAYS ensure the 'EST ANNUAL PAYROLL' (or REMUNERATION) matches the correct State (e.g., AR, CO, CA). Do NOT skip rows or shift values between states.
   - **LOC# Confusion**: Ignore 'LOC#' or 'LOC' numbers (like 004, 005) when determining the State. The State code (AR, CO, etc.) is the primary key.
   - **Multi-row Logic**: If a table has multiple rows for the same class code but different states, extract each one uniquely.
7. **Prior Carriers**: Even if the layout is messy, extract the Carrier Name, Policy #, and Premium. Look for "PRIOR CARRIER INFORMATION" headers.
8. **General Questions**: If you see fragments like "z", "x", "2", "|", or "9" in a check box area, interpret it as "Y" if context suggests a yes, but default to "N" if no explanation is provided.

DOCUMENT FORMAT ANALYSIS:
{json.dumps(format_info, indent=2)}

Extract ALL available information from the application form into the requested JSON structure.

=== KEY SECTIONS TO EXTRACT ===

1. DEMOGRAPHICS:
   - Applicant Name, Business Description, FEIN
   - Business Description: Look for "NATURE OF BUSINESS / DESCRIPTION OF OPERATIONS" section.
   - Contact Info (Email, Phone, Website)
   - Mailing Address
   - SIC/NAICS codes, Years in Business
   - Proposed Policy Dates and States
   - wcStates: List ALL unique states found in the ratingByState table.

2. GENERAL QUESTIONS:
   - Extract q1 through q24 as "Y" or "N".

3. PRIOR CARRIERS:
   - Extract a list of previous insurance carriers.

4. RATING BY STATE:
   - Extract rating information including payroll and class codes.

5. INDIVIDUALS INCLUDED/EXCLUDED:
   - Extract Officers, Owners, and Partners.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TEXT TO ANALYZE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{all_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 YOUR RESPONSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY the JSON object following the strict schema provided.
"""

        try:
            start_time = time.time()
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": prompt
                }],
                response_format={
                    "type": "json_schema",
                    "json_schema": WORKERS_COMP_SCHEMA
                },
                max_tokens=8000,
                temperature=0.0
            )
            elapsed = time.time() - start_time
            if self.request_id and request_monitor:
                request_monitor.record_ai_usage(
                    request_id=self.request_id,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    processing_time=elapsed,
                    model="gpt-4o"
                )
            
            response_text = response.choices[0].message.content
            data = json.loads(response_text)
            
            # Post-processing (simplified for the new schema)
            return self._post_process_claims(data, all_text)
                
        except Exception as e:
            print(f"   ⚠️  Extraction failed: {e}")
            import traceback
            traceback.print_exc()
            return {"data": {}}
            

    def _to_float(self, val) -> float:
        """Safe conversion to float"""
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            # Keep only digits, dots, and minus signs
            clean_val = re.sub(r'[^\d.-]', '', val)
            try:
                return float(clean_val) if clean_val else 0.0
            except:
                return 0.0
        return 0.0

    def _extract_premiums_from_text(self, text: str) -> Dict:
        """
        Extract premium values using regex from text.
        Sums 'Premium $X.XX' → totalEstimatedAnnualPremium.
        """
        premiums = re.findall(r'Premium \$([\d,]+\.?\d*)', text, re.IGNORECASE)
        total_prem = sum(float(p.replace(',', '')) for p in premiums)
        
        return {
            "totalEstimatedAnnualPremium": total_prem or 0.0,
            "experienceModification": 0.0,  # Default if not found
            "minimumPremium": 0.0,
            "depositPremium": 0.0
        }
    
    def _post_process_claims(self, data: Dict, all_text: str = "") -> Dict:
        """
        Post-process extracted application data + extract premiums from text.
        Performs numeric cleanup for financial fields in ratingByState and priorCarriers.
        """
        if "data" not in data:
            return data
            
        inner_data = data["data"]
        
        # 1. ratingByState Cleanup
        if "ratingByState" in inner_data and isinstance(inner_data["ratingByState"], list):
            for entry in inner_data["ratingByState"]:
                for field in ["fullTimeEmployees", "partTimeEmployees", "estAnnualPayroll", "ratePer100Payroll", "estAnnualPremium"]:
                    val = entry.get(field)
                    if isinstance(val, str):
                        clean_val = re.sub(r'[^\d.]', '', val)
                        try:
                            entry[field] = float(clean_val) if clean_val else 0.0
                        except:
                            entry[field] = 0.0
                    elif val is None:
                        entry[field] = 0.0

        # 2. priorCarriers Cleanup
        if "priorCarriers" in inner_data and isinstance(inner_data["priorCarriers"], list):
            for carrier in inner_data["priorCarriers"]:
                for field in ["annualPremium", "experienceMod", "numberOfClaims", "amountPaid", "reserveAmount"]:
                    carrier[field] = self._to_float(carrier.get(field))

        # 3. Individuals Cleanup
        if "individuals" in inner_data and isinstance(inner_data["individuals"], list):
            for ind in inner_data["individuals"]:
                if "ownershipPercentage" in ind:
                    ind["ownershipPercentage"] = self._to_float(ind["ownershipPercentage"])

        # 4. Premium Calculation Cleanup
        if "premiumCalculation" in inner_data and isinstance(inner_data["premiumCalculation"], dict):
            calc = inner_data["premiumCalculation"]
            for field in ["totalEstimatedAnnualPremium", "experienceModification", "minimumPremium", "depositPremium"]:
                calc[field] = self._to_float(calc.get(field))
            inner_data["premiumCalculation"] = calc
                        
        return data
    
    def _validate_financial_data(self, claim: Dict) -> Tuple[bool, List[str]]:
        """
        Validate financial calculations for a claim
        Returns: (is_valid, list_of_errors)
        """
        errors = []
        tolerance = 0.02  # Allow $0.02 tolerance for rounding
        
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
        
        calculated_total = medical_incurred + indemnity_incurred + expense_incurred
        
        # Validate total incurred
        if abs(calculated_total - total_incurred) > tolerance:
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
Common causes:
1. Swapped Medical and Indemnity columns.
2. Missed Recovery/Subro column (often the rightmost column).
3. Confusing Reserves with Paid amounts in multi-row layouts.

RE-EXAMINE the column headers and row labels for these specific IDs and ensure the math balances:
Medical(Paid+Res) + Indemnity(Paid+Res) + Expense(Paid+Res) - Recovery == Total Incurred.
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
      "injury_type": "MED or COMP",
      "claim_class": "class code",
      "medical_paid": "string",
      "medical_reserve": "string",
      "indemnity_paid": "string",
      "indemnity_reserve": "string",
      "expense_paid": "string",
      "expense_reserve": "string",
      "recovery": "string",
      "deductible": "string",
      "total_incurred": "string"
    }}
  ]
}}

STRICT RULES:
1. DO NOT include any claims NOT in the list above.
2. Ensure math balances perfectly.
3. Check if 'Total Incurred' includes or excludes 'Recovery'.

TEXT TO ANALYZE:
{all_text}

Return ONLY the JSON."""

        try:
            start_time = time.time()
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": retry_prompt}],
                response_format={"type": "json_object"},
                max_tokens=8000,
                temperature=0.0
            )
            elapsed = time.time() - start_time
            if self.request_id and request_monitor:
                request_monitor.record_ai_usage(
                    request_id=self.request_id,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    processing_time=elapsed,
                    model="gpt-4o"
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
  "claim_number": "{target_claim_number}",
  "injury_date_time": "YYYY-MM-DD",
  "claim_year": 2020,
  "status": "Open/Closed/REOP",
  "injury_description": "cause of injury",
  "body_part": "injured body part",
  "injury_type": "COMP/MEDI/etc",
  "claim_class": "class code and description",
  "medical_paid": 0.0,
  "medical_reserve": 0.0,
  "indemnity_paid": 0.0,
  "indemnity_reserve": 0.0,
  "expense_paid": 0.0,
  "expense_reserve": 0.0,
  "recovery": 0.0,
  "deductible": 0.0,
  "total_incurred": 0.0
}}

RULES:
1. Find the claim with number {target_claim_number}
2. Extract ONLY that claim's data
3. Ignore all other claims in the document
4. Status codes: C=Closed, O=Open, REOP=Reopened
5. Remove $ and commas from amounts

TEXT TO ANALYZE:
{all_text}

Return ONLY the JSON object for claim {target_claim_number}."""

        try:
            start_time = time.time()
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
            elapsed = time.time() - start_time
            if self.request_id and request_monitor:
                request_monitor.record_ai_usage(
                    request_id=self.request_id,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    processing_time=elapsed,
                    model="gpt-4o"
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
        Validate application extraction
        """
        print(f"\n🔍 Validating extraction...")
        
        is_complete = "data" in data and bool(data["data"].get("demographics", {}).get("applicantName"))
        
        validation_report = {
            "is_complete": is_complete,
            "has_demographics": "demographics" in data.get("data", {}),
            "has_rating": len(data.get("data", {}).get("ratingByState", [])) > 0,
            "has_prior_carriers": len(data.get("data", {}).get("priorCarriers", [])) > 0
        }
        
        if is_complete:
            print(f"   ✓ Application extraction looks COMPLETE")
        else:
            print(f"   ❌ Application extraction looks INCOMPLETE")
        
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
        
        # Step 1: Extract text from PDF using PyMuPDF + Tesseract
        all_text, pages_metadata = self.extract_text_from_pdf(pdf_path)
        
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
        
        # Validate extraction
        validation = self.validate_extraction(schema_data, all_text)
        
        # Print metadata to terminal (not saved to JSON)
        print(f"\n{'='*60}")
        print(f"📊 EXTRACTION METADATA")
        print(f"{'='*60}")
        print(f"Session ID: {session_id}")
        print(f"Source File: {os.path.basename(pdf_path)}")
        print(f"Total Pages: {len(pages_metadata)}")
        print(f"Extraction Method: pymupdf-tesseract-enhanced")
        print(f"Validation: Demographics={'Found' if validation.get('has_demographics') else 'Missing'}, Rating={'Found' if validation.get('has_rating') else 'Missing'}, Prior Carriers={'Found' if validation.get('has_prior_carriers') else 'Missing'}")
        
        # Add minimal metadata to JSON (without pages_metadata)
        extraction_metadata = {
            "extraction_date": datetime.now().isoformat(),
            "method": "pymupdf-tesseract-enhanced",
            "num_pages": len(pages_metadata),
            "source_file": os.path.basename(pdf_path),
            "session_id": session_id,
            "target_claim": target_claim_number
        }
        # analysis_data will contain the metadata, schema_data will stay clean
        
        # Save analysis.json (metadata only)
        analysis_data = {
            "extraction_metadata": extraction_metadata,
            "applicant_name": schema_data.get("data", {}).get("demographics", {}).get("applicantName"),
            "has_rating": validation.get("has_rating"),
            "has_prior_carriers": validation.get("has_prior_carriers")
        }
        
        analysis_file = session_dir / "analysis.json"
        with open(analysis_file, 'w', encoding='utf-8') as f:
            json.dump(analysis_data, f, indent=2, ensure_ascii=False)
        print(f"✓ Analysis saved: {analysis_file}")
        
        # Save schema (clean output)
        schema_file = session_dir / "extracted_schema.json"
        with open(schema_file, 'w', encoding='utf-8') as f:
            json.dump(schema_data, f, indent=2, ensure_ascii=False)
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
            "extracted_schema": schema_data,  # Use full schema_data
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


def parse_p3_gio_from_text(text: str) -> Dict[str, str]:
    """
    Parses P3_GIO form field data from the extracted text.
    Looks for patterns like 'P3_GIO_Q1: Y' or 'P3_GIO_Q1: Yes'
    """
    results = {}
    if not text:
        return results
        
    for i in range(1, 25):
        pattern = rf"P3_GIO_Q{i}:\s*(Y(?:es)?|N(?:o)?)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = match.group(1).strip().upper()
            results[f"q{i}"] = "Y" if val.startswith("Y") else "N"
            
    return results


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