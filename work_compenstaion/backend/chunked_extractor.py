import json
import re
from typing import Dict, List, Optional, Tuple
from work_compensation import EnhancedInsuranceExtractor, parse_p3_gio_from_text
from pdf_rotation import auto_rotate_pdf_content
import tempfile
import shutil

MAX_CHUNK_CHARS = 15000  # Cap chunk size to avoid token limit issues


class PolicyChunker:
    """Helper class to split text into chunks based on Policy Number headers."""
    
    def __init__(self, client):
        self.client = client

    def detect_policy_boundaries(self, text: str) -> List[Dict]:
        """
        Use AI to detect policy headers and their approximate locations.
        Returns a list of dicts: {"policy_number": "...", "start_index": int}
        """
        print(f"\n🔍 Detecting policy boundaries in text ({len(text)} chars)...")
        
        # We only need to scan for headers, so we can use a subset of text if it's too long,
        # but for policy detection, scanning the full text is safer if within limits.
        # If text is extremely long, we might need to chunk the detection itself.
        text_preview = text if len(text) < 100000 else text[:100000] # Safety limit
        
        prompt = f"""Analyze the following insurance document text and identify all UNIQUE policy sections.
Look for "Policy Number", "Policy #", "Pol #", "NUMBER: [ID]" or similar headers that start a new section for a specific policy.
Note: Policy numbers may be on the line BELOW the label "Policy Number".

Return a JSON object with a list of detected policies and the EXACT snippet of text that identifies the policy header (and the policy number itself).

Example Response:
{{
  "policies": [
    {{
      "policy_number": "N9WC603272",
      "header_snippet": "Policy Number: N9WC603272"
    }},
    {{
      "policy_number": "SWC1364773",
      "header_snippet": "Policy Number\\nSWC1364773"
    }}
  ]
}}

DOCUMENT TEXT:
{text_preview}
"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=4000,
                temperature=0.0
            )
            
            result = json.loads(response.choices[0].message.content)
            policies = result.get("policies", [])
            
            # Find indices for each header snippet
            boundaries = []
            for p in policies:
                snippet = p.get("header_snippet")
                if snippet:
                    # Find first occurrence of snippet
                    idx = text.find(snippet)
                    if idx != -1:
                        boundaries.append({
                            "policy_number": p.get("policy_number"),
                            "start_index": idx,
                            "header_snippet": snippet
                        })
            
            # Sort by index
            boundaries.sort(key=lambda x: x["start_index"])
            
            # Deduplicate by index (sometimes AI might return similar snippets)
            unique_boundaries = []
            last_idx = -1
            for b in boundaries:
                if b["start_index"] != last_idx:
                    unique_boundaries.append(b)
                    last_idx = b["start_index"]
            
            print(f"✓ Detected {len(unique_boundaries)} policy boundaries")
            return unique_boundaries

        except Exception as e:
            print(f"⚠️ Policy boundary detection failed: {e}")
            return []

    def _split_oversized_chunk(self, chunk: Dict) -> List[Dict]:
        """Split a chunk that exceeds MAX_CHUNK_CHARS by page boundaries."""
        text = chunk["text"]
        policy_num = chunk["policy_number"]
        if len(text) <= MAX_CHUNK_CHARS:
            return [chunk]
        # Split by PAGE N or ==== PAGE N ==== patterns
        page_pat = re.compile(r"\n={5,}\s*\n\s*PAGE\s+(\d+)\s*\n\s*={5,}", re.IGNORECASE)
        matches = list(page_pat.finditer(text))
        if not matches:
            # Fallback: fixed-size split with overlap
            sub_chunks = []
            start = 0
            part = 0
            while start < len(text):
                end = min(start + MAX_CHUNK_CHARS, len(text))
                sub_chunks.append({
                    "policy_number": f"{policy_num} (part {part + 1})",
                    "text": text[start:end]
                })
                start = end
                part += 1
            return sub_chunks
        result = []
        for i, m in enumerate(matches):
            chunk_start = matches[i - 1].start() if i > 0 else 0
            chunk_end = m.start()
            sub_text = text[chunk_start:chunk_end].strip()
            if sub_text:
                result.append({
                    "policy_number": f"{policy_num} (page {m.group(1)})",
                    "text": sub_text
                })
        # Last segment: from last match to end
        if matches:
            last_start = matches[-1].start()
            sub_text = text[last_start:].strip()
            if sub_text:
                result.append({
                    "policy_number": f"{policy_num} (page {matches[-1].group(1)}+)",
                    "text": sub_text
                })
        return result if result else [chunk]

    def split_into_chunks(self, text: str, boundaries: List[Dict]) -> List[Dict]:
        """Splits the text into chunks based on detected boundaries. Caps chunk size."""
        if not boundaries:
            base = [{"policy_number": "Unknown", "text": text}]
            return self._split_oversized_chunk(base[0]) if len(text) > MAX_CHUNK_CHARS else base
            
        chunks = []
        
        # Add content BEFORE the first boundary if it exists
        if boundaries[0]["start_index"] > 10:
            first_idx = boundaries[0]["start_index"]
            pre_chunk = text[:first_idx].strip()
            if pre_chunk:
                raw = {"policy_number": "Initial Section", "text": pre_chunk}
                if len(pre_chunk) > MAX_CHUNK_CHARS:
                    chunks.extend(self._split_oversized_chunk(raw))
                else:
                    chunks.append(raw)
        
        for i in range(len(boundaries)):
            start_idx = boundaries[i]["start_index"]
            end_idx = boundaries[i+1]["start_index"] if i+1 < len(boundaries) else len(text)
            
            chunk_text = text[start_idx:end_idx].strip()
            raw_chunk = {
                "policy_number": boundaries[i]["policy_number"],
                "text": chunk_text
            }
            if len(chunk_text) > MAX_CHUNK_CHARS:
                chunks.extend(self._split_oversized_chunk(raw_chunk))
            else:
                chunks.append(raw_chunk)
        
        return chunks

class ChunkedInsuranceExtractor(EnhancedInsuranceExtractor):
    """
    Extends EnhancedInsuranceExtractor to support policy-based chunking.
    This prevents token limit issues by splitting large documents into policy-specific chunks.
    """

    @staticmethod
    def _fix_ocr_split_lines(text: str) -> str:
        """
        Pre-process OCR text to stitch orphaned payroll/dollar amounts back onto
        their rating table row BEFORE the text reaches the LLM.

        OCR frequently produces layouts like:

            $317371
            012 7380 IN    0.000 $0.00

        or:

            011 8810 IL    0.000 $0.00
            $710779

        In both cases the dollar amount belongs to that row but sits on a
        separate line. This function detects both patterns and merges them
        so the LLM sees a clean, single-line representation:

            012 7380 IN    $317371    0.000 $0.00
        """
        import re

        lines = text.split('\n')
        fixed = []
        i = 0

        # Pattern: a standalone dollar amount (orphaned payroll line)
        # e.g.  "   $317371"  or  "$1,407,974"  or  "  317371  "
        orphan_re = re.compile(r'^\s*\$?([\d,]+)\s*$')

        # Pattern: a rating table data row (LOC# CLASS STATE ... or just CLASS STATE ...)
        # Contains a 4-digit class code AND optional LOC# AND a state code
        # Row has no substantial payroll already filled in
        rating_row_re = re.compile(
            r'^(.*?\b\d{4}\b.*?\b[A-Z]{2}\b.*)$'
        )

        while i < len(lines):
            line = lines[i]
            m_orphan = orphan_re.match(line)

            if m_orphan:
                amount_raw = m_orphan.group(0).strip()  # keep as-is for readability
                amount_val = m_orphan.group(1).replace(',', '')

                # Check if the NEXT line is a rating row missing a payroll
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    if rating_row_re.match(next_line.strip()):
                        # Inject the amount into the next line before the rate column
                        # Insert before the first occurrence of "0.000" or at end
                        if '0.000' in next_line:
                            merged = next_line.replace('0.000', f'{amount_raw}    0.000', 1)
                        else:
                            merged = next_line.rstrip() + f'    {amount_raw}'
                        print(f"   🔧 OCR fix (amount-before-row): '{line.strip()}' + '{next_line.strip()}' → merged")
                        fixed.append(merged)
                        i += 2  # consumed both lines
                        continue

                # Check if the PREVIOUS fixed line was a rating row
                if fixed:
                    prev = fixed[-1]
                    if rating_row_re.match(prev.strip()):
                        # Inject the amount into the preceding line
                        if '0.000' in prev:
                            fixed[-1] = prev.replace('0.000', f'{amount_raw}    0.000', 1)
                        else:
                            fixed[-1] = prev.rstrip() + f'    {amount_raw}'
                        print(f"   🔧 OCR fix (amount-after-row): merged '{line.strip()}' into previous row")
                        i += 1
                        continue

            fixed.append(line)
            i += 1

        result = '\n'.join(fixed)
        return result
    
    def process_pdf_with_verification(self, pdf_path: str, target_claim_number: Optional[str] = None) -> Dict:
        """
        Complete pipeline with verification steps - Overridden to support chunking report.
        """
        from datetime import datetime
        import os
        
        print(f"\n{'='*60}")
        print(f"🚀 PROCESSING: {os.path.basename(pdf_path)}")
        print(f"{'='*60}")
        
        # --- PRE-PROCESSING: AUTO-ROTATION ---
        temp_rotated_dir = tempfile.mkdtemp()
        temp_rotated_pdf = os.path.join(temp_rotated_dir, "rotated_temp.pdf")
        
        try:
            print(f"🔄 Checking for rotation...")
            was_rotated = auto_rotate_pdf_content(pdf_path, temp_rotated_pdf)
            
            if was_rotated:
                print(f"   ✓ Document rotated. Processing corrected version.")
                pdf_path = temp_rotated_pdf # SWAP the path!
            else:
                print(f"   ✓ Document orientation correct.")
        except Exception as e:
            print(f"   ⚠️ Rotation check failed: {e}. Proceeding with original.")
            
        # Create session output directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:20]
        file_slug = os.path.basename(pdf_path).replace(" ", "_").replace(".", "_")[:20]
        session_id = f"{timestamp}_{file_slug}"
        session_dir = self.output_dir / f"extraction_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        
        self.current_session_dir = session_dir 
        
        # Step 1: Extract text
        all_text, pages_metadata = self.extract_text_from_pdf(pdf_path)

        # Step 1b: Fix OCR split-line payroll amounts BEFORE sending to LLM
        print(f"\n🔧 Running OCR split-line fix...")
        all_text = self._fix_ocr_split_lines(all_text)
        print(f"   ✓ Split-line fix complete.")

        # Save combined text (post-fix, so what LLM sees is what's saved)
        text_file = session_dir / "extracted_text.txt"
        with open(text_file, 'w', encoding='utf-8') as f:
            f.write(all_text)
        print(f"\n✓ Combined text saved: {text_file}")
        
        # Step 2: Extract schema
        print(f"\n{'='*60}")
        print(f"📋 SCHEMA EXTRACTION")
        print(f"{'='*60}")
        
        schema_data = self.extract_schema_from_text(all_text, target_claim_number)
        
        # Validate extraction
        validation = self.validate_extraction(schema_data, all_text)
        
        # Metadata
        extraction_metadata = {
            "extraction_date": datetime.now().isoformat(),
            "method": "pymupdf-tesseract-enhanced-chunked",
            "num_pages": len(pages_metadata),
            "source_file": os.path.basename(pdf_path),
            "session_id": session_id,
            "target_claim": target_claim_number
        }
        # analysis_data will contain the metadata, schema_data will stay clean
        
        # Save analysis.json
        analysis_data = {
            "extraction_metadata": extraction_metadata,
            "applicant_name": schema_data.get("data", {}).get("demographics", {}).get("applicantName"),
            "has_rating": validation.get("has_rating"),
            "has_prior_carriers": validation.get("has_prior_carriers")
        }
        analysis_file = session_dir / "analysis.json"
        with open(analysis_file, 'w', encoding='utf-8') as f:
            json.dump(analysis_data, f, indent=2, ensure_ascii=False)
            
        # Save schema (CLEAN)
        schema_file = session_dir / "extracted_schema.json"
        with open(schema_file, 'w', encoding='utf-8') as f:
            json.dump(schema_data, f, indent=2, ensure_ascii=False)
            
        # Verification package
        verification_data = {
            "session_id": session_id,
            "session_dir": str(session_dir),
            "source_pdf": pdf_path,
            "pages": pages_metadata,
            "combined_text": all_text,
            "extracted_schema": schema_data,
            "schema_file": str(schema_file),
            "summary": {
                "total_pages": len(pages_metadata),
                "scanned_pages": sum(1 for p in pages_metadata if p.get('is_scanned', False)),
                "avg_confidence": sum(p.get('confidence', 0.0) for p in pages_metadata) / len(pages_metadata) if pages_metadata else 0.0,
                "is_complete": validation.get("is_complete", False)
            }
        }
        verification_file = session_dir / "verification_package.json"
        with open(verification_file, 'w', encoding='utf-8') as f:
            json.dump(verification_data, f, indent=2, ensure_ascii=False, default=str)
            
        print(f"\n{'='*60}")
        print(f"✅ EXTRACTION COMPLETE")
        print(f"{'='*60}")
        print(f"Output: {session_dir}")
        
        # Cleanup temporary rotated file
        try:
            if os.path.exists(temp_rotated_dir):
                shutil.rmtree(temp_rotated_dir, ignore_errors=True)
        except:
            pass

        return verification_data

    def _is_acord_130_single_policy(self, text: str) -> bool:
        """
        Detect ACORD 130 or similar single-policy application forms.
        These have form field data (P3_GIO) and prior carrier policy numbers that
        are NOT separate policy sections. Policy chunking would incorrectly split them.
        """
        text_snip = text[:8000] if len(text) > 8000 else text
        has_form_fields = "FORM FIELD DATA" in text_snip and "P3_GIO_" in text_snip
        has_acord_130 = "ACORD 130" in text_snip or "ACORD130" in text_snip
        return bool(has_form_fields or has_acord_130)

    def extract_schema_from_text(self, all_text: str, target_claim_number: Optional[str] = None) -> Dict:
        """
        OVERRIDE: Implements chunking before calling extraction.
        """
        if target_claim_number:
            return super().extract_schema_from_text(all_text, target_claim_number)
        
        # Decide chunking strategy
        is_single_policy_form = self._is_acord_130_single_policy(all_text)
        text_length = len(all_text)
        
        print(f"\n⭐ NEW STEP: STRATEGIC CHUNKING (Form Type: {'Single' if is_single_policy_form else 'Multi'}, Length: {text_length}) ⭐")
        
        chunks = []
        chunker = PolicyChunker(self.client)
        
        if is_single_policy_form:
            if text_length > 25000:
                print(f"   ℹ️ Large ACORD 130/Single-policy detected ({text_length} chars). Splitting by PAGE headers.")
                # Split by PAGE headers
                page_splits = re.split(r'={20,}\s+PAGE \d+\s+={20,}', all_text)
                # Filter empty and merge small ones
                temp_chunks = []
                current_txt = ""
                for split in page_splits:
                    if not split.strip(): continue
                    if len(current_txt) + len(split) < 15000:
                        current_txt += "\n" + split
                    else:
                        if current_txt: temp_chunks.append(current_txt)
                        current_txt = split
                if current_txt: temp_chunks.append(current_txt)
                
                for i, txt in enumerate(temp_chunks):
                    chunks.append({"policy_number": f"PageChunk_{i+1}", "text": txt})
            else:
                print("   ℹ️ Small Single-policy application. Using single-shot extraction.")
                return super()._extract_all_claims(all_text)
        else:
            boundaries = chunker.detect_policy_boundaries(all_text)
            if len(boundaries) <= 1:
                print("   ℹ️ No multiple policy sections detected. Proceeding with single-shot.")
                return super()._extract_all_claims(all_text)
            chunks = chunker.split_into_chunks(all_text, boundaries)

        if not chunks:
            return super()._extract_all_claims(all_text)

        print(f"   ✂️ Split into {len(chunks)} chunks for parallel processing.")
        
        # Generate Chunking Report
        report = {
            "total_original_chars": len(all_text),
            "num_chunks": len(chunks),
            "chunks": [],
            "total_chunked_chars": sum(len(c["text"]) for c in chunks),
            "integrity_check": "Sum of chunk lengths is close to original"
        }
        
        for c in chunks:
            report["chunks"].append({
                "policy": c["policy_number"],
                "length": len(c["text"]),
                "preview_start": c["text"][:100],
                "preview_end": c["text"][-100:]
            })
            
        # Save to file if we have a session directory
        if hasattr(self, 'current_session_dir'):
            report_file = self.current_session_dir / "chunking_report.json"
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            print(f"   ✓ Chunking report saved: {report_file}")
        
        all_results = []
        # Extract context from the first part of the document (Demographics + Locations)
        # We look specifically for the Locations table which is crucial for mapping LOC# to State
        doc_context = all_text[:2500] 
        
        # Try to find and include the full location list if it's nearby
        # We need this to map LOC# (001, 002) back to State (CA, AK) on later pages
        loc_start = re.search(r'LOCATIONS.*?\n', all_text, re.IGNORECASE)
        if loc_start:
            loc_block = all_text[loc_start.start():loc_start.start()+5000]
            loc_block = re.split(r'POLICY INFORMATION|INDIVIDUALS INCLUDED|GENERAL INFORMATION', loc_block, flags=re.IGNORECASE)[0]
            doc_context += "\n\n--- LOCATION MAPPING (PRIMARY) ---\n" + loc_block

        ext_loc_start = re.search(r'Extended ACORD Locations.*?\n', all_text, re.IGNORECASE)
        if ext_loc_start:
            ext_loc_block = all_text[ext_loc_start.start():ext_loc_start.start()+8000]
            ext_loc_block = re.split(r'Extended ACORD Individuals|Extended ACORD Class Codes', ext_loc_block, flags=re.IGNORECASE)[0]
            doc_context += "\n\n--- LOCATION MAPPING (EXTENDED) ---\n" + ext_loc_block
            
        for i, chunk in enumerate(chunks):
            print(f"\n{'='*40}")
            print(f"📦 CHUNK {i+1}/{len(chunks)}: {chunk['policy_number']}")
            print(f"{'='*40}")
            
            # Inject context if it's not the first chunk
            extraction_text = chunk["text"]
            if i > 0:
                extraction_text = f"--- DOCUMENT CONTEXT (Demographics/Header) ---\n{doc_context}\n\n--- CONTENT TO EXTRACT FROM THIS CHUNK ---\n{chunk['text']}"
            
            chunk_result = super()._extract_all_claims(extraction_text)
            
            if "data" in chunk_result:
                all_results.append(chunk_result)
            else:
                print(f"   ⚠️ No structured data found in chunk {i+1}")
                
        merged_result = self._merge_chunks(all_results)
        return merged_result

    def _merge_general_questions(self, results_list: List[Dict]) -> Dict:
        """
        Merge generalQuestions from all chunks, preferring non-default values.
        When a question has "Y" in any chunk, use "Y"; otherwise use "N".
        """
        merged_gq = {}
        for i in range(1, 25):
            key = f"q{i}"
            merged_gq[key] = "N"
        for res in results_list:
            gq = res.get("data", {}).get("generalQuestions", {})
            for key, val in (gq or {}).items():
                if val and str(val).strip().upper() == "Y":
                    merged_gq[key] = "Y"
        return merged_gq

    def _deduplicate_rating(self, rating_list: list) -> list:
        """
        Post-merge deduplication for ratingByState.
        
        Rules:
        1. If a state+classCode combo has BOTH a non-zero payroll entry AND a zero-payroll entry,
           remove the zero-payroll entry (it's a ghost from Extended ACORD Class Codes).
        2. If a state+classCode combo has multiple non-zero payroll entries, keep all of them
           (they represent different locations with distinct payroll values).
        3. If a state+classCode combo has ONLY zero-payroll entries, keep exactly one.
        """
        from collections import defaultdict
        
        # Group entries by (state, classCode)
        groups = defaultdict(list)
        for entry in rating_list:
            state = entry.get("state", "")
            code = str(entry.get("classCode", ""))
            payroll = self._to_float(entry.get("estAnnualPayroll"))
            groups[(state, code)].append((payroll, entry))
        
        cleaned = []
        removed_count = 0
        for key, entries in groups.items():
            non_zero = [(p, e) for p, e in entries if p > 0]
            zero_entries = [(p, e) for p, e in entries if p <= 0]
            
            if non_zero:
                # Keep all non-zero entries (distinct locations)
                # Deduplicate among non-zero by exact payroll match
                seen_payrolls = set()
                for p, e in non_zero:
                    p_rounded = round(p, 2)
                    if p_rounded not in seen_payrolls:
                        cleaned.append(e)
                        seen_payrolls.add(p_rounded)
                    else:
                        removed_count += 1
                # Drop ALL zero entries when non-zero exist
                removed_count += len(zero_entries)
            else:
                # Only zero entries — keep exactly one
                if zero_entries:
                    cleaned.append(zero_entries[0][1])
                    removed_count += len(zero_entries) - 1
        
        if removed_count > 0:
            print(f"   🧹 Dedup: Removed {removed_count} zero-payroll / duplicate rating entries")
        
        return cleaned

    def _merge_chunks(self, results_list: List[Dict]) -> Dict:
        """Merges multiple extraction results into a single report."""
        print(f"\n⭐ MERGING {len(results_list)} CHUNKS ⭐")
        
        if not results_list:
            return {"data": {}}
            
        # Use first result as baseline
        merged = {
            "data": {
                "demographics": results_list[0].get("data", {}).get("demographics", {}),
                "ratingByState": [],
                "generalQuestions": self._merge_general_questions(results_list),
                "priorCarriers": [],
                "individuals": [],
                "premiumCalculation": results_list[0].get("data", {}).get("premiumCalculation", {})
            }
        }
        
        seen_carriers = set()
        seen_individuals = set()
        
        for res in results_list:
            inner = res.get("data", {})
            
            # Merge demographics (pick most complete, prefer non-N/A values)
            demo = inner.get("demographics", {})
            base_demo = merged["data"]["demographics"]
            if len(str(demo.get("applicantName", ""))) > len(str(base_demo.get("applicantName", ""))):
                base_demo.update(demo)
            # Fill in N/A fields from later chunks
            for dkey, dval in demo.items():
                if str(base_demo.get(dkey, "N/A")).strip().upper() in ("N/A", "") and str(dval).strip().upper() not in ("N/A", ""):
                    base_demo[dkey] = dval

            # Merge ratingByState (collect all, deduplicate later)
            for entry in inner.get("ratingByState", []):
                state = entry.get("state")
                payroll = self._to_float(entry.get("estAnnualPayroll"))
                
                # Skip entries with no state or obviously invalid
                if not state or state == "N/A":
                    if payroll <= 0:
                        continue
                
                merged["data"]["ratingByState"].append(entry)
            
            # Merge priorCarriers
            for carrier in inner.get("priorCarriers", []):
                c_name = str(carrier.get("carrierName", "")).lower().strip()
                c_year = str(carrier.get("year", ""))
                key = (c_name, c_year)
                
                if key not in seen_carriers and c_name and c_name != "n/a":
                    merged["data"]["priorCarriers"].append(carrier)
                    seen_carriers.add(key)
            
            # Merge individuals
            for ind in inner.get("individuals", []):
                key = (str(ind.get("name")).lower(), str(ind.get("title")).lower())
                if key not in seen_individuals and ind.get("name") and ind.get("name") != "N/A":
                    merged["data"]["individuals"].append(ind)
                    seen_individuals.add(key)
            
            # Update premiumCalculation (take the ones with non-zero premium)
            current_premium = inner.get("premiumCalculation", {})
            curr_val = self._to_float(current_premium.get("totalEstimatedAnnualPremium"))
            base_val = self._to_float(merged["data"]["premiumCalculation"].get("totalEstimatedAnnualPremium"))
            if curr_val > base_val:
                merged["data"]["premiumCalculation"] = current_premium
        
        # DEDUPLICATION PASS: Clean ratingByState
        merged["data"]["ratingByState"] = self._deduplicate_rating(merged["data"]["ratingByState"])
                    
        # FINAL PASS
        merged = self._post_process_claims(merged)
        return merged

if __name__ == "__main__":
    # Example usage (can be replaced by main_chunked.py)
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
    # Use a dummy test if needed or just leave as is for import
    print("ChunkedInsuranceExtractor loaded.")
