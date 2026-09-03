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

            # Heuristic checks (existing behavior)
            claim_ids = re.findall(r'Claim\s*#|Claim\s*Number|\d{6,}', text, re.IGNORECASE)
            names_found = any(k in text for k in ["Claimant", "Employee", "Claimant Name"])
            cid_count = text.count("(cid:")
            is_unreadable = cid_count > 10
            is_columnar_missing = ("Accident Date" in text or "Medical" in text) and not names_found
            is_sparse = len(text.strip()) < 300 and len(claim_ids) > 0

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
            elif len(claim_ids) > 0 and not names_found:
                print(f"   ⚠️ Health Check: Page {page_num} has claims but no claimant label.")
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
        Only run GPT-4 Vision on 'problem pages' and patch the results back.
        """
        pages_to_verify = self.run_extraction_health_check(pages_metadata)
        
        if not pages_to_verify:
            print("   ✓ Health Check: All pages look complete. No Vision recovery needed.")
            return "".join([p.get("text", "") for p in pages_metadata])

        print(f"🔄 Targeted Vision Recovery: Processing {len(pages_to_verify)} problem pages...")
        
        from pdf2image import convert_from_path
        
        patched_full_text = []
        
        for page in pages_metadata:
            page_num = page.get("page_number")
            page_text = page.get("text", "")
            quality_rec = page.get("quality_recommendation")
            quality_metrics = page.get("quality_metrics", {}) or {}
            cid_count = quality_metrics.get("cid_count", page_text.count("(cid:"))
            
            if page_num in pages_to_verify:
                try:
                    images = convert_from_path(str(pdf_path), first_page=page_num, last_page=page_num)
                    if images:
                        # If the page is truly unreadable / full_vision, replace the entire page
                        if quality_rec == "full_vision" or cid_count > 50:
                            print(f"   👁️ Vision Re-OCR (full page) for Page {page_num}...")
                            vision_full_text = self._get_full_vision_page_text(images[0])
                            if vision_full_text.strip():
                                header = f"\n{'='*80}\nPAGE {page_num}\n{'='*80}\n\n"
                                page_text = header + vision_full_text + "\n"
                                print(f"      ✓ Replaced Page {page_num} content via Vision")
                        else:
                            # Otherwise, append missing fields as a patch
                            print(f"   👁️ Vision Patching Page {page_num}...")
                            vision_patch_text = self._get_vision_patch_for_page(images[0], page_text)
                            if vision_patch_text:
                                patch_block = f"\n\n[VISION PATCH - PAGE {page_num}]\n{vision_patch_text}\n"
                                page_text += patch_block
                                print(f"      ✓ Patched Page {page_num} via Vision")
                except Exception as e:
                    print(f"   ⚠️ Vision patching failed for page {page_num}: {e}")
            
            patched_full_text.append(page_text)
            
        return "".join(patched_full_text)

    def _get_vision_patch_for_page(self, image: Image.Image, existing_text: str) -> str:
        """
        Ask GPT-4 Vision specifically for fields missing from existing text.
        """
        prompt = f"""You are reviewing ONE page of an insurance loss run document.
        
The following text was already extracted from this page:
---
{existing_text[:1200]}...
---

Looking at the page image, identify any fields that are MISSING from the above text.
CRITICAL: Focus on the left-column or top-section blocks (Claimant Name, Class Code, Location, etc.) that pdfplumber often misses.

Return JSON of MISSING fields only:
{{
  "missing_fields": [
     {{"label": "Claimant Name", "value": "AARON MOORE"}},
     {{"label": "Class Code", "value": "5188"}}
  ]
}}

If NOTHING is missing, return {{"missing_fields": []}}. 
DO NOT repeat fields already found in the text.
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
                max_tokens=800,
                temperature=0.0
            )
            
            res_data = json.loads(response.choices[0].message.content)
            missing = res_data.get("missing_fields", [])
            
            if not missing:
                return ""
            
            return "\n".join([f"{f.get('label')}: {f.get('value')}" for f in missing])
            
        except Exception as e:
            print(f"      ⚠️ Vision API error helper: {e}")
            return ""

    def _get_full_vision_page_text(self, image: Image.Image) -> str:
        """
        Run Vision OCR on the entire page and return full text.
        Used for pages that are mostly CID/unreadable.
        """
        import io as _io
        prompt = (
            "Extract ALL visible text from this insurance document page.\n"
            "Preserve layout as much as possible (tables, columns, spacing).\n"
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
