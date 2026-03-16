import re
import json
import base64
import io
from typing import List, Dict, Optional
from PIL import Image

from text_quality_verifier import TextQualityVerifier

class VisionRecoveryHandler:
    def __init__(self, openai_client):
        self.client = openai_client

    def run_extraction_health_check(self, pages_metadata: List[Dict]) -> List[int]:
        """
        Identify pages where pdfplumber likely missed data.
        Uses both heuristic checks (CID, missing labels) and the
        TextQualityVerifier to score each page.

        Returns: List of page numbers (1-indexed) that need Vision patching.
        """
        pages_to_verify: List[int] = []
        verifier = TextQualityVerifier()

        for page in pages_metadata:
            text = page.get("text", "")
            page_num = page.get("page_number")

            # Heuristic checks (bank-specific)
            transaction_markers = re.findall(r'Date|Amount|Description|Balance|Transaction|Deposit|Withdrawal', text, re.IGNORECASE)
            headers_found = any(k in text for k in ["Account Number", "Beginning Balance", "Ending Balance", "Statement Period"])
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

            needs_patch = False

            # Strong heuristic triggers (cheap, non-AI) – always patch
            if is_unreadable:
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

            if needs_patch:
                pages_to_verify.append(page_num)

        return pages_to_verify

    def patch_text_with_vision(self, pdf_path: str, pages_metadata: List[Dict]) -> str:
        """
        Tiered recovery strategy to reduce costs:
        1. Identification: Identify problem pages.
        2. Tier 1 (OCR 600 DPI): Try high-res OCR first.
        3. Tier 2 (OCR 300 DPI): Try mid-res OCR if 600 fails quality check.
        4. Tier 3 (GPT-4 Vision): Use Vision only if OCR quality is poor.
        """
        pages_to_verify = self.run_extraction_health_check(pages_metadata)
        
        if not pages_to_verify:
            print("   ✓ Health Check: All pages look complete. No additional recovery needed.")
            return "".join([p.get("text", "") for p in pages_metadata])

        print(f"🔄 Targeted Recovery: Processing {len(pages_to_verify)} problem pages...")
        
        from pdf2image import convert_from_path
        import pytesseract
        verifier = TextQualityVerifier()
        
        patched_full_text = []
        
        for page in pages_metadata:
            page_num = page.get("page_number")
            page_text = page.get("text", "")
            quality_rec = page.get("quality_recommendation")
            quality_metrics = page.get("quality_metrics", {}) or {}
            cid_count = quality_metrics.get("cid_count", page_text.count("(cid:"))
            
            if page_num in pages_to_verify:
                try:
                    # Tier 1 & 2: OCR Recovery (Lower Cost)
                    # We try 600 DPI first as it's best for sharp text
                    current_best_text = page_text
                    current_best_method = "original"
                    current_best_score = page.get("quality_score", 0.0)
                    
                    for dpi in [600, 300]:
                        print(f"   🔍 Attempting OCR Recovery ({dpi} DPI) for Page {page_num}...")
                        images = convert_from_path(str(pdf_path), first_page=page_num, last_page=page_num, dpi=dpi)
                        if images:
                            ocr_text = pytesseract.image_to_string(images[0])
                            # Evaluate result quality
                            quality = verifier.page_quality(ocr_text)
                            score = quality.get("score", 0.0)
                            print(f"      [DEBUG OCR] DPI {dpi} Score: {score:.3f} | Extraction length: {len(ocr_text)}")
                            print(f"      [DEBUG OCR] Snippet: {repr(ocr_text[:150])}...")
                            
                            if score > current_best_score or score > 0.85:
                                current_best_text = ocr_text
                                current_best_score = score
                                current_best_method = f"tesseract-{dpi}dpi"
                                
                                # If OCR is 'ok', we can skip further tiers
                                if quality.get("recommendation") == "ok" and score > 0.9:
                                    break
                    
                    # Decisions based on OCR results
                    final_quality = verifier.page_quality(current_best_text)
                    if final_quality.get("recommendation") == "ok" and current_best_score > 0.8:
                        print(f"      ✓ OCR Recovery successful for Page {page_num} ({current_best_method}, Score: {current_best_score:.3f}). Skipping Vision.")
                        header = f"\n{'='*80}\nPAGE {page_num} (RECOVERED via {current_best_method.upper()})\n{'='*80}\n\n"
                        page_text = header + current_best_text + "\n"
                    else:
                        # Tier 3: GPT-4 Vision (Last Resort)
                        print(f"   👁️ OCR quality low (Score: {current_best_score:.3f}, Rec: {final_quality.get('recommendation', 'unknown')}). Falling back to GPT-4 Vision for Page {page_num}...")
                        images = convert_from_path(str(pdf_path), first_page=page_num, last_page=page_num, dpi=300)
                        if images:
                            if quality_rec == "full_vision" or cid_count > 50:
                                print(f"      👁️ Vision Re-OCR (full page) for Page {page_num}...")
                                vision_full_text = self._get_full_vision_page_text(images[0])
                                if vision_full_text.strip():
                                    header = f"\n{'='*80}\nPAGE {page_num} (RECOVERED via VISION)\n{'='*80}\n\n"
                                    page_text = header + vision_full_text + "\n"
                                    print(f"         ✓ Replaced Page {page_num} content via Vision")
                            else:
                                print(f"      👁️ Vision Patching Page {page_num}...")
                                vision_patch_text = self._get_vision_patch_for_page(images[0], page_text)
                                if vision_patch_text:
                                    patch_block = f"\n\n[VISION PATCH - PAGE {page_num}]\n{vision_patch_text}\n"
                                    page_text += patch_block
                                    print(f"         ✓ Patched Page {page_num} via Vision")
                                    
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

Looking at the page image, identify any transaction rows or missing metadata (Account Number, Dates, Balances) that are MISSING from the above text.
CRITICAL: Prioritize finding the **Check Number (`check_no`)** first for any individual check rows. 
Rules:
1. Locate the check number (usually 4-10 digits).
2. For each check number, extract the corresponding Amount and Date.
3. Ensure the amount decimal point and digits are perfectly accurate.
4. Do NOT include check numbers that are already present in the extracted text above.

Return JSON of MISSING fields only:
{{
  "missing_fields": [
     {{"label": "Check Transaction", "check_no": "12345", "amount": "450.00", "date": "01/12"}},
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
                if f.get("label") == "Check Transaction":
                    chk = str(f.get("check_no", ""))
                    amt = str(f.get("amount", ""))
                    dt = str(f.get("date", ""))
                    # Format as "[check_no] * [amount] [date]" so _parse_individual_check_summary can find it
                    rows.append(f"{chk} * {amt} {dt}")
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
            "Preserve tabular layout exactly (Date, Amount, Description columns).\n"
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
