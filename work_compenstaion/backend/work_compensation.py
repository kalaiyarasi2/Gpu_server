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
        
        try:
            print(f"🔍 Detecting PDF type...")
            detector = PDFDetector(pdf_path)
            is_scanned = detector.is_scanned()
            
            if is_scanned:
                print(f"📸 SCANNED PDF DETECTED: Using Tesseract OCR fallback")
                from ocr_text import OCRPDFExtractor
                ocr_extractor = OCRPDFExtractor(pdf_path)
                return ocr_extractor.extract(
                    dpi=config.OCR_DPI,
                    psm_mode=config.OCR_PSM_MODE,
                    enhancements={
                        'contrast': config.OCR_CONTRAST,
                        'sharpness': config.OCR_SHARPNESS,
                        'grayscale': config.OCR_GRAYSCALE,
                        'binarize': config.OCR_BINARIZE,
                        'edge_enhance': config.OCR_EDGE_ENHANCE
                    }
                )
            else:
                print(f"📄 DIGITAL PDF DETECTED: Using Hybrid Extraction (pdfplumber + pymupdf fallback)")
                from pdf_plumber import extract_pdf_hybrid
                # Hybrid extraction returns (text, metadata, info)
                text, metadata, info = extract_pdf_hybrid(pdf_path)
                
                if info.get('fallback_used'):
                    print(f"   ℹ️ Hybrid Extraction recovered {len(info.get('recovered_claims', []))} claims using Smart Append")

                # STAGE 3: ACORD Recovery Pass
                # If we see ACORD but don't see typical headers, it might be a "Digital Scan"
                # (A PDF that has some text but the main form content is an image)
                should_run_ocr_recovery = False
                
                # Check for ACORD keyword or signature patterns
                has_acord_keyword = "ACORD" in text.upper()
                
                # Use alphanumeric character count for more accurate density check
                # (filters out thousands of spaces/pipes from pdfplumber)
                alnum_text_len = len(re.sub(r'[^a-zA-Z0-9]', '', text))
                avg_alnum_per_page = alnum_text_len / len(metadata) if len(metadata) > 0 else 0
                
                is_very_low_density = alnum_text_len < 100 * len(metadata) # Less than 100 alnum chars per page average
                
                if has_acord_keyword:
                    # Check for missing Agency ID which is usually at the top
                    if "AGENCY CUSTOMER ID" not in text.upper() and "ATOTALS" not in text.upper():
                        print(f"   ⚠️ ACORD form detected but key headers missing. Triggering OCR recovery...")
                        should_run_ocr_recovery = True
                    elif alnum_text_len < 400 * len(metadata): # Low alnum density for ACORD
                        print(f"   ⚠️ Low text density detected for ACORD ({alnum_text_len} alnum chars for {len(metadata)} pages). Triggering OCR recovery...")
                        should_run_ocr_recovery = True
                elif is_very_low_density and alnum_text_len > 0:
                    print(f"   ⚠️ Extremely low text density detected ({alnum_text_len} alnum chars). Triggering OCR recovery pass...")
                    should_run_ocr_recovery = True

                if should_run_ocr_recovery:
                    try:
                        from ocr_text import OCRPDFExtractor
                        ocr_extractor = OCRPDFExtractor(pdf_path)
                        ocr_text, ocr_meta = ocr_extractor.extract(
                            dpi=config.OCR_DPI,
                            psm_mode=config.OCR_PSM_MODE,
                            verbose=False,
                            enhancements={
                                'contrast': config.OCR_CONTRAST,
                                'sharpness': config.OCR_SHARPNESS,
                                'grayscale': config.OCR_GRAYSCALE,
                                'binarize': config.OCR_BINARIZE,
                                'edge_enhance': config.OCR_EDGE_ENHANCE
                            }
                        )
                        
                        # Merge metadata
                        for i, page in enumerate(ocr_meta):
                            if i < len(metadata):
                                metadata[i]["ocr_text"] = page.get("raw_text", "")
                        
                        # Append OCR text as a recovery block
                        text += "\n\n" + "="*80 + "\n"
                        text += "OCR RECOVERY BLOCK\n"
                        text += "="*80 + "\n\n"
                        text += ocr_text
                        
                        print(f"   ✅ OCR recovery complete. Added to extracted text.")
                    except Exception as ocr_err:
                        print(f"   ⚠️ OCR recovery failed: {ocr_err}")
                    
                return text, metadata
                
        except Exception as e:
            print(f"⚠️ Detection/Extraction error: {e}")
            print(f"   Falling back to standard pdfplumber...")
            from pdf_plumber import extract_pdf_with_pdfplumber as external_extract
            return external_extract(pdf_path)
    
    
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
            response = self.client.chat.completions.create(
                model="gpt-4.1",
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
                model="gpt-4.1",
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
            print(f"❌ Error extracting text: {e}")
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
                model="gpt-4.1",
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
        
        prompt = f"""You are analyzing a Workers' Compensation application form to understand its structure.

Your task: Describe HOW the data is organized in this document so we can extract it accurately.

Answer these questions:
1. What is the business/applicant name?
2. Are there tables for "Rating by State" or "Class Codes"?
3. Is there a section for "Prior Carriers" or "Loss History"?
4. Are there "General Questions" with Y/N answers?

Return JSON:
{{
  "applicant": "business name",
  "has_rating_table": true/false,
  "has_prior_carriers": true/false,
  "has_questions": true/false,
  "special_notes": "any quirks or unusual formatting",
  "confidence": 0.0-1.0
}}

DOCUMENT TEXT (first 8000 chars):
{text[:8000]}

Return ONLY the JSON."""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=1500,
                temperature=0.0
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
        
        prompt = f"""You are an expert at extracting structured data from Workers' Compensation application forms.

DOCUMENT FORMAT ANALYSIS:
{json.dumps(format_info, indent=2)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 EXTRACTION TASK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Extract ALL available information from the application form into the requested JSON structure.

=== KEY SECTIONS TO EXTRACT ===

1. DEMOGRAPHICS:
   - Applicant Name, Business Description, FEIN
   - Contact Info (Email, Phone, Website)
   - Mailing Address
   - SIC/NAICS codes, Years in Business
   - Proposed Policy Dates and States
   - Agency Customer ID (often found in headers like "Agency Customer ID" or "ATOTALS-01")

2. GENERAL QUESTIONS:
   - Extract q1 through q24 as "Y" or "N".
   - OCR often misreads checkmarks as fragments (z, 2, <, =, x, 9, |).
   - If no explanation is provided for a question, it is usually "N".

3. PRIOR CARRIERS:
   - Extract a list of previous insurance carriers including year, carrier name, policy number, and financial history.

4. RATING BY STATE:
   - Extract the rating information per state/class code, including employee counts and payroll.

5. INDIVIDUALS INCLUDED/EXCLUDED:
   - Extract Officers, Owners, and Partners from the "INDIVIDUALS INCLUDED/EXCLUDED" table.
   - Include Name, Title, Ownership %, and "Y" if included, "N" if excluded.

6. PREMIUM CALCULATION:
   - Extract the total estimated annual premium, experience modification factor, minimum premium, and deposit premium.
   - Look for "TOTAL ESTIMATED ANNUAL PREMIUM", "EXPERIENCE MODIFICATION", "MINIMUM PREMIUM", "DEPOSIT".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📄 TEXT TO ANALYZE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{all_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 YOUR RESPONSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY the JSON object following the strict schema provided.
"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4.1",
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
            
            response_text = response.choices[0].message.content
            data = json.loads(response_text)
            
            # Post-processing (simplified for the new schema)
            return self._post_process_claims(data)
                
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

    def _post_process_claims(self, data: Dict) -> Dict:
        """
        Post-process extracted application data.
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
            response = self.client.chat.completions.create(
                model="gpt-4.1",
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
            response = self.client.chat.completions.create(
                model="gpt-4.1",
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
        print(f"Validation: {validation['total_extracted']} claims extracted, {len(validation['missing_claims'])} missing")
        
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