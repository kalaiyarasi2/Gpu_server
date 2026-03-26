import re
import json
import base64
import io
from typing import List, Dict, Optional
from PIL import Image
from dotenv import load_dotenv

import importlib
import importlib.util
from pathlib import Path

# Robust dynamic import of BankTextQualityVerifier to avoid collisions with 
# other extractors in the same environment (e.g. Insurance, Work Comp).
verifier_path = Path(__file__).parent / "text_quality_verifier.py"
spec = importlib.util.spec_from_file_location("bank_statement_verifier_local", verifier_path)
local_tqv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_tqv)
BankTextQualityVerifier = local_tqv.BankTextQualityVerifier

# Load environment variables (Local first, then walk up to root if needed)
local_env = Path(__file__).parent / ".env"
root_env = Path(__file__).parent.parent.parent / ".env"

if local_env.exists():
    load_dotenv(dotenv_path=local_env)
elif root_env.exists():
    load_dotenv(dotenv_path=root_env)
else:
    load_dotenv() # Fallback to standard search

# Import shared utilities from ocr_utils dynamically
utils_path = Path(__file__).parent / "ocr_utils.py"
utils_spec = importlib.util.spec_from_file_location("bank_statement_ocr_utils_vsn", utils_path)
utils_mod = importlib.util.module_from_spec(utils_spec)
utils_spec.loader.exec_module(utils_mod)
robust_convert_pdf_to_images = utils_mod.robust_convert_pdf_to_images

class VisionRecoveryHandler:
    def __init__(self, openai_client):
        self.client = openai_client

    def run_extraction_health_check(self, pages_metadata: List[Dict]) -> List[int]:
        """
        Identify pages where pdfplumber likely missed data.
        Uses both heuristic checks (CID, missing labels) and the
        BankTextQualityVerifier to score each page.

        Returns: List of page numbers (1-indexed) that need Vision patching.
        """
        pages_to_verify: List[int] = []
        verifier = BankTextQualityVerifier()

        for page in pages_metadata:
            text = page.get("text", "")
            page_num = page.get("page_number")

            # Heuristic checks (bank-specific)
            transaction_markers = re.findall(r'Date|Amount|Description|Balance|Transaction|Deposit|Withdrawal', text, re.IGNORECASE)
            # Heuristic: headers found on the START of a new account section
            has_beginning_balance = any(k in text for k in ["Beginning Balance", "ACCOUNT SUMMARY", "Direct Deposit Information"])
            # Continuation pages often have footers like "Account No. ... Page X of Y". 
            # We ignore those so they don't count as "headers found".
            headers_found = has_beginning_balance
            cid_count = text.count("(cid:")
            is_unreadable = cid_count > 10
            is_columnar_missing = ("Date" in text and "Amount" in text) and not transaction_markers
            is_sparse = len(text.strip()) < 200 and len(transaction_markers) > 0

            # New: per-page quality evaluation
            quality = verifier.page_quality(text)
            score = quality.get("score", 0.0)
            recommendation = quality.get("recommendation", "ok")

            # Attach diagnostics back onto the page metadata for debugging
            page["quality_score"] = score
            page["quality_recommendation"] = recommendation
            page["quality_metrics"] = quality.get("analysis", {}).get("metrics", {})

            # Check for "shredded" OCR by looking for many short lines with numbers
            lines = text.split('\n')
            short_numeric_lines = [l for l in lines if len(l.strip()) < 15 and any(c.isdigit() for c in l)]
            is_shredded = len(short_numeric_lines) > 20

            needs_patch = False

            # Strong heuristic triggers (cheap, non-AI) – always patch
            if is_unreadable or is_shredded:
                if is_shredded:
                    print(f"   ⚠️ Health Check: Page {page_num} looks 'shredded' (Too many short numeric lines).")
                else:
                    print(f"   ⚠️ Health Check: Page {page_num} is unreadable ({cid_count} CID codes).")
                needs_patch = True
            elif is_columnar_missing or is_sparse:
                print(f"   ⚠️ Health Check: Page {page_num} looks incomplete (Missing content).")
                needs_patch = True
            elif len(transaction_markers) > 0 and not headers_found:
                print(f"   ⚠️ Health Check: Page {page_num} has data markers but no statement headers.")
                needs_patch = True

            # Quality-based triggers (from TextQualityVerifier) – cost-aware:
            # - 'full_vision': page is truly bad → always patch
            # - 'dpi_fallback': only patch when score is low AND noise/CID are high
            # - 'missing_columns': table is truncated (Phase 2 fix)
            if recommendation == "full_vision":
                print(
                    f"   ⚠️ Quality Verifier: Page {page_num} scored {score:.3f} "
                    f"with recommendation '{recommendation}'."
                )
                needs_patch = True
            elif recommendation == "dpi_fallback":
                # Require both: moderate/low score and noticeable noise/CID
                noisy_enough = cid_count > 50 or score < 0.7
                if noisy_enough:
                    print(
                        f"   ⚠️ Quality Verifier (cost-aware): Page {page_num} scored {score:.3f}, "
                        f"cid={cid_count}, recommendation='{recommendation}' → patching."
                    )
                    needs_patch = True
            
            # New Phase 2: Missing column detection
            if needs_patch:
                # FORCE VISION for all continuation pages or shredded pages
                page["force_high_fidelity"] = (page_num > 1) or is_shredded or (recommendation == "full_vision")
                pages_to_verify.append(page_num)

        return pages_to_verify

    def patch_text_with_vision(self, pdf_path: str, pages_metadata: List[Dict]) -> str:
        """
        Streamlined recovery strategy:
        1. Identification: Identify problem pages (CIF, missing columns, etc.)
        2. Recovery: For problem pages, use the unified OCRPDFExtractor fallback sequence.
        """
        pages_to_verify = self.run_extraction_health_check(pages_metadata)
        
        if not pages_to_verify:
            print("   ✓ Health Check: All pages look complete. No additional recovery needed.")
            return "".join([p.get("text", "") for p in pages_metadata])

        print(f"🔄 Targeted Recovery: Processing {len(pages_to_verify)} problem pages...")
        
        # Late Dynamic Import of OCRPDFExtractor to break circularity
        ocr_path = Path(__file__).parent / "ocr_text.py"
        spec = importlib.util.spec_from_file_location("bank_ocr_internal", str(ocr_path))
        ocr_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ocr_mod)
        
        OCRPDFExtractor = ocr_mod.OCRPDFExtractor
        ocr_extractor = OCRPDFExtractor(pdf_path)
        patched_full_text = []
        
        for page in pages_metadata:
            page_num = page.get("page_number")
            page_text = page.get("text", "")
            method = page.get("extraction_method", "").lower()
            
            if page_num in pages_to_verify:
                # If the page was already extracted via Full Vision OCR in the first pass
                # and still failed health check, we might want to try one last 'patch'
                # or just accept that it's a very difficult page.
                # To save cost, we avoid a second Full Vision pass.
                if "vision" in method:
                    print(f"   ℹ️ Page {page_num} already processed by Vision. Skipping redundant recovery.")
                    patched_full_text.append(page_text)
                    continue

                try:
                    # Unified Recovery: Call OCRPDFExtractor for this specific page.
                    # This will automatically handle 600 DPI -> 300 DPI -> GPT Vision.
                    print(f"   🔍 Recovering Page {page_num} using unified OCR/Vision fallback...")
                    
                    # We render just this page
                    # Note: We need a way to tell OCRPDFExtractor to only do one page or handle it here
                    # To avoid re-opening the PDF many times, we can use images = robust_convert_pdf_to_images
                    # But calling extract() is cleaner. We'll modify OCRPDFExtractor to accept first/last page maybe?
                    # Or just use its existing logic but it might be overkill.
                    
                    # Wait, OCRPDFExtractor.extract(dpi=600, first_page=page_num, last_page=page_num) would be better.
                    # But OCRPDFExtractor.extract doesn't currently take first/last page.
                    # Let's check OCRPDFExtractor.extract arguments again.
                    # It calls robust_convert_pdf_to_images(self.pdf_path, dpi=dpi) which does the whole thing.
                    
                    # Use high-fidelity (Vision) if the page was marked as structurally problematic
                    force_v = page.get("force_high_fidelity", False)

                    rec_text, rec_meta = ocr_extractor.extract(
                        dpi=600, 
                        verbose=False, 
                        first_page=page_num, 
                        last_page=page_num,
                        force_vision=force_v
                    )
                    
                    if rec_text.strip():
                        # The rec_meta will contain the final extraction_method used (e.g., 'gpt-4-vision-fallback')
                        final_method = rec_meta[0].get("extraction_method", "unknown")
                        print(f"      ✓ Recovery successful for Page {page_num} via {final_method}.")
                        
                        header = f"\n{'='*80}\nPAGE {page_num} (RECOVERED via {final_method.upper()})\n{'='*80}\n\n"
                        page_text = header + rec_text + "\n"
                    
                except Exception as e:
                    print(f"   ⚠️ Recovery failed for page {page_num}: {e}")
            
            patched_full_text.append(page_text)
            
        return "".join(patched_full_text)


    def _get_vision_patch_for_page(self, image: Image.Image, existing_text: str) -> str:
        """
        Ask GPT-4 Vision specifically for fields missing from existing text.
        """
        prompt = f"""You are reviewing ONE page of a bank statement document.
        
The following text was already extracted from this page:
---
{existing_text[:1200]}...
---

Looking at the page image, identify any transaction rows or missing metadata (Account Number, Dates, Debits, Credits, Balances) that are MISSING from the above text.
CRITICAL: Prioritize finding the **Running Balance** for each transaction row.
Rules:
1. Locate the transaction (Date, Description, Amount).
2. For each transaction, extract the corresponding 'Running Balance' from the rightmost column.
3. Ensure the amount decimal point and digits are perfectly accurate.
4. If a line is clipped on the right, use visual context to reconstruct digits.

Return JSON of MISSING fields only:
{{
  "missing_fields": [
     {{"label": "Transaction row", "date": "01/12", "amount": "450.00", "running_balance": "12345.67", "description": "..."}},
     {{"label": "Account Number", "value": "XXXX-1234"}}
  ]
}}

If NOTHING is missing, return {{"missing_fields": []}}.
"""
        try:
            buffered = io.BytesIO()
            image.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_base64}"}
                        }
                    ]
                }],
                response_format={"type": "json_object"},
                max_tokens=4000, # Increased from 2000 to prevent truncation
                temperature=0.0
            )
            
            raw_content = response.choices[0].message.content
            try:
                res_data = json.loads(raw_content)
            except json.JSONDecodeError as je:
                print(f"      ⚠️ Initial JSON parse failed: {je}. Attempting cleanup...")
                # Basic cleanup for common LLM JSON mishaps
                cleaned = raw_content.strip()
                if "```json" in cleaned:
                    cleaned = cleaned.split("```json")[-1].split("```")[0].strip()
                elif "```" in cleaned:
                    cleaned = cleaned.split("```")[-1].split("```")[0].strip()
                
                # Fix common unterminated string issues if possible
                if cleaned.count('"') % 2 != 0:
                    cleaned += '"'
                if not cleaned.endswith("}"):
                    if cleaned.endswith("]"):
                        cleaned += "}"
                    else:
                        cleaned += "]}"
                
                try:
                    res_data = json.loads(cleaned)
                    print(f"      ✅ Text cleanup successful, JSON parsed.")
                except Exception:
                    print(f"      ❌ Final JSON parse failed. Raw content: {raw_content[:500]}...")
                    return ""

            missing = res_data.get("missing_fields", [])
            
            if not missing:
                return ""
            
            rows = []
            for f in missing:
                label = f.get("label", "").lower()
                if "check" in label:
                    chk = str(f.get("check_no") or f.get("value") or "")
                    amt = str(f.get("amount") or "")
                    dt = str(f.get("date") or "")
                    # Format as "[check_no] * [amount] [date]" 
                    rows.append(f"{chk} * {amt} {dt}")
                elif "transaction" in label:
                    dt = str(f.get("date") or "")
                    amt = str(f.get("amount") or "")
                    rb = str(f.get("running_balance") or "")
                    desc = str(f.get("description") or "")
                    # Format as "[date] [description] [amount] [running_balance]"
                    rows.append(f"{dt} {desc} {amt} {rb}")
                else:
                    rows.append(f"{f.get('label')}: {f.get('value')}")
            
            return "\n".join(rows)
            
        except Exception as e:
            print(f"      ⚠️ Vision API error helper: {e}")
            import traceback
            traceback.print_exc()
            return ""

    def _get_full_vision_page_text(self, image: Image.Image) -> str:
        """
        Run Vision OCR on the entire page and return full text.
        Used for pages that are mostly CID/unreadable.
        """
        import io as _io
        prompt = (
            "Extract ALL visible text from this bank statement page.\n"
            "Preserve tabular layout exactly (Date, Amount, Description, Running Balance columns).\n"
            "CRITICAL: Bank statements often have the 'Running Balance' column on the far right edge.\n"
            "Ensure you capture these values completely. Do NOT truncate or skip digits near the right margin.\n"
            "If a line has multiple currency values (e.g. '95.96    1,582.10'), ensure both are captured and separated by spaces.\n"
            "Ensure descriptions are captured fully even if they are in a separate column block.\n"
            "Return ONLY the extracted text, no explanations."
        )
        try:
            buffered = _io.BytesIO()
            image.save(buffered, format="PNG")
            img_base64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_base64}"}
                        }
                    ]
                }],
                max_tokens=4000,
                temperature=0.0
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            print(f"      ⚠️ Vision API full-page error: {e}")
            return ""
