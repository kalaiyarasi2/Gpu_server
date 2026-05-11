import json
import re
from typing import Dict, List, Optional, Tuple
from insurance_extractor import EnhancedInsuranceExtractor, filter_claims_by_claim_year, MIN_INCLUDED_CLAIM_YEAR
from pdf_rotation import auto_rotate_pdf_content
from auto_rotation_ocr import run_pipeline_preserve_layout
from pdf_detector import PDFDetector
import tempfile
import shutil

# Overlap added to each chunk boundary so claim rows straddling a boundary
# are fully visible in both chunks (RC2 / RC2b fix).
CHUNK_OVERLAP_CHARS = 600


class PolicyChunker:
    """Helper class to split text into chunks based on Policy Number headers."""
    
    def __init__(self, client):
        self.client = client

    def detect_policy_boundaries(self, text: str) -> List[Dict]:
        """
        Use AI to detect policy headers and their approximate locations.
        Returns a list of dicts: {"policy_number": "...", "start_index": int}

        RC6 FIX: Removed hardcoded company-name examples that biased detection
        toward SKMGT-style documents. Now uses fully generic placeholders.
        SLIDING WINDOW FIX: Iterates over full document in 100k-char windows
        with 5k overlap so no boundaries are missed in large documents.
        """
        print(f"\n🔍 Detecting policy boundaries iteratively over text ({len(text)} chars)...")

        chunk_window = 100000
        overlap = 5000
        items = []

        start_pos = 0
        while start_pos < len(text):
            end_pos = start_pos + chunk_window
            text_preview = text[start_pos:end_pos]

            # RC6: All examples now use generic placeholders – no real company names
            prompt = f"""Analyze the following insurance document text and identify all UNIQUE policy sections.
Look for "Policy Number", "Policy #", "Pol #", "NUMBER: [ID]" or similar headers that start a new section for a specific policy.
Note: Policy numbers may be on the line BELOW the label "Policy Number".

NEW REQUIREMENT: This document may repeat policy numbers for different years. 
Identify a new boundary if you see a header line with a Policy Number AND/OR a new year section description like:
"Claims where Date Of Loss between 1/1/2024 and 12/31/2024"

For each boundary, also identify the Insurance Carrier (the company name) associated with that section. 
Look for "Carrier:", "Policy Company:", or branding near the policy number.

Return a JSON object with a list of detected boundaries and the EXACT snippet of text that identifies the header.

Example Response:
{{
  "boundaries": [
    {{
      "identifier": "[POLICY_NUMBER] - [YEAR]",
      "carrier": "[INSURANCE_COMPANY_NAME]",
      "header_snippet": "[EXACT TEXT FROM DOCUMENT THAT MARKS A NEW SECTION]"
    }},
    {{
      "identifier": "[POLICY_NUMBER_2] - [YEAR]",
      "carrier": "[INSURANCE_COMPANY_NAME_2]",
      "header_snippet": "[EXACT TEXT FROM DOCUMENT THAT MARKS NEXT SECTION]"
    }}
  ]
}}

Important:
- The "header_snippet" MUST be EXACT text copied verbatim from the document below.
- Do NOT invent or paraphrase snippets.
- Identify boundaries for ALL carriers, ANY company name format.

DOCUMENT TEXT (Characters {start_pos} to {end_pos}):
{text_preview}
"""

            try:
                print(f"   --> Scanning for policies from character {start_pos} to {end_pos}...")
                response = self.client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    max_tokens=4000,
                    temperature=0.0
                )
                result = json.loads(response.choices[0].message.content)
                batch_items = result.get("boundaries", []) or result.get("policies", [])
                items.extend(batch_items)
            except Exception as e:
                print(f"   ⚠️ Policy boundary detection failed at pos {start_pos}: {e}")

            # Break if we've processed the end of the text
            if end_pos >= len(text):
                break

            start_pos += (chunk_window - overlap)

        # Find indices for each header snippet (outside the loop)
        boundaries = []
        for p in items:
            snippet = p.get("header_snippet")
            if snippet:
                idx = text.find(snippet)
                if idx != -1:
                    boundaries.append({
                        "policy_number": p.get("identifier") or p.get("policy_number"),
                        "carrier": p.get("carrier"),
                        "start_index": idx,
                        "header_snippet": snippet
                    })

        # Sort by index
        boundaries.sort(key=lambda x: x["start_index"])

        # Deduplicate by index
        unique_boundaries = []
        last_idx = -1
        for b in boundaries:
            if b["start_index"] != last_idx:
                unique_boundaries.append(b)
                last_idx = b["start_index"]

        print(f"✓ Detected {len(unique_boundaries)} policy boundaries")
        return unique_boundaries

    def split_into_chunks(self, text: str, boundaries: List[Dict], overlap: int = CHUNK_OVERLAP_CHARS) -> List[Dict]:
        """
        Splits the text into chunks based on detected boundaries.
        
        RC2 FIX: Each chunk now includes `overlap` chars from the END of the
        previous chunk so claim rows that straddle a policy boundary are fully
        visible in at least one chunk's context window.
        """
        if not boundaries:
            return [{"policy_number": "Unknown", "text": text}]
            
        chunks = []
        
        # Add content BEFORE the first boundary if meaningful
        if boundaries[0]["start_index"] > 10:
            first_idx = boundaries[0]["start_index"]
            pre_chunk = text[:first_idx].strip()
            if pre_chunk:
                chunks.append({
                    "policy_number": "Initial Section",
                    "text": pre_chunk
                })
        
        for i in range(len(boundaries)):
            start_idx = boundaries[i]["start_index"]
            end_idx = boundaries[i+1]["start_index"] if i+1 < len(boundaries) else len(text)
            
            # RC2: extend start backward by overlap so boundary claims aren't cut
            overlap_start = max(0, start_idx - overlap) if i > 0 else start_idx
            
            chunk_text = text[overlap_start:end_idx].strip()
            chunks.append({
                "policy_number": boundaries[i]["policy_number"],
                "carrier_name": boundaries[i].get("carrier"),
                "text": chunk_text
            })
            
        return chunks


class ChunkedInsuranceExtractor(EnhancedInsuranceExtractor):
    """
    Extends EnhancedInsuranceExtractor to support two chunking modes:
    1. Policy+Year-boundary chunking  (via detect_policy_boundaries)
    2. Page-aware chunking            (secondary fallback for large chunks)
    
    Implements all 6 RC fixes to prevent missed/false claims.
    """
    
    def process_pdf_with_verification(self, pdf_path: str, target_claim_number: Optional[str] = None) -> Dict:
        """
        Complete pipeline with verification steps - Overridden to support chunking report.
        """
        from datetime import datetime
        import os
        
        print(f"\n{'='*60}")
        print(f"🚀 PROCESSING: {os.path.basename(pdf_path)}")
        print(f"{'='*60}")
        
        temp_rotated_dir = tempfile.mkdtemp()
        temp_rotated_pdf = os.path.join(temp_rotated_dir, "rotated_temp.pdf")
        original_pdf_path = pdf_path
        
        is_scanned = None
        try:
            print(f"🔍 Identifying PDF type (Digital vs Scanned)...")
            detector = PDFDetector(pdf_path)
            is_scanned = detector.is_scanned()
            
            if is_scanned:
                print(f"📸 SCANNED PDF DETECTED. Applying high-accuracy OSD rotation...")
                # Use the new scanned-specific rotation module
                # Pass temp_rotated_dir as work_dir to ensure isolation
                rotated_pdf, reports = run_pipeline_preserve_layout(
                    pdf_path, 
                    work_dir=temp_rotated_dir,
                    output_pdf=temp_rotated_pdf,
                    dpi=200, 
                    osd_min_conf=0.3
                )
                was_rotated = any(r.get('applied_rotate', 0) != 0 for r in reports)
            else:
                print(f"📄 DIGITAL PDF DETECTED. Applying standard text-based rotation...")
                # Use existing digital-specific rotation module
                was_rotated = auto_rotate_pdf_content(pdf_path, temp_rotated_pdf)
            
            if was_rotated:
                print(f"   ✓ Document rotated/corrected. Processing updated version.")
                pdf_path = temp_rotated_pdf
            else:
                print(f"   ✓ Document orientation correct.")
        except Exception as e:
            print(f"   ⚠️ Rotation check failed: {e}. Proceeding with original.")
            
        # Create session output directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:20]
        # Use original filename for slug to avoid 'rotated_temp_pdf' in folder names
        file_slug = os.path.basename(original_pdf_path).replace(" ", "_").replace(".", "_")[:20]
        session_id = f"{timestamp}_{file_slug}"
        session_dir = self.output_dir / f"extraction_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        
        self.current_session_dir = session_dir

        # Save the processed PDF to the output directory for reference
        processed_pdf_name = f"processed_{os.path.basename(original_pdf_path)}"
        processed_pdf_path = session_dir / processed_pdf_name
        try:
            shutil.copy2(pdf_path, processed_pdf_path)
            print(f"📄 Processed PDF saved for reference: {processed_pdf_path}")
        except Exception as e:
            print(f"⚠️ Failed to save processed PDF: {e}")
        
        # Step 1: Extract text
        all_text, pages_metadata = self.extract_text_from_pdf(pdf_path, is_scanned=is_scanned)
        
        # Save combined text
        text_file = session_dir / "extracted_text.txt"
        with open(text_file, 'w', encoding='utf-8') as f:
            f.write(all_text)
        print(f"\n✓ Combined text saved: {text_file}")
        
        # Step 2: Extract schema
        print(f"\n{'='*60}")
        print(f"📋 SCHEMA EXTRACTION")
        print(f"{'='*60}")
        
        schema_data = self.extract_schema_from_text(all_text, target_claim_number, num_pages=len(pages_metadata))
        
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
        schema_data['extraction_metadata'] = extraction_metadata
        
        # Separate math fields from schema output
        all_claims = schema_data.get("claims", [])
        clean_claims_for_schema = []
        claims_analysis_data = []

        for claim in all_claims:
            schema_claim = claim.copy()
            math_valid = schema_claim.pop("math_valid", None)
            math_diff = schema_claim.pop("math_diff", None)
            # Remove confidence_score from schema JSON, but keep it in analysis
            confidence_score = schema_claim.pop("confidence_score", None)
            clean_claims_for_schema.append(schema_claim)
            
            claims_analysis_data.append({
                "claim_number": claim.get("claim_number"),
                "math_valid": math_valid,
                "math_diff": math_diff,
                "confidence_score": confidence_score
            })

        included_claims, excluded_claims, unknown_year_claims = filter_claims_by_claim_year(
            clean_claims_for_schema,
            min_year_inclusive=MIN_INCLUDED_CLAIM_YEAR,
            keep_unknown_year=True,
        )

        # Save analysis.json
        analysis_data = {
            "extraction_metadata": extraction_metadata,
            "report_date": schema_data.get("report_date"),
            "policy_number": schema_data.get("policy_number"),
            "insured_name": schema_data.get("insured_name"),
            "policy_period": schema_data.get("policy_period"),
            "total_claims": len(all_claims),
            "claims_validation_summary": claims_analysis_data,
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
            
        # Save schema (claims array + SummaryLevel array)
        claims_only = included_claims or []

        # Compute summary fields from claims and header data
        years_set = set()
        for claim in claims_only:
            year = claim.get("claim_year")
            if year:
                years_set.add(year)
        years_sorted = sorted(years_set)
        header_policy_number = schema_data.get("policy_number")
        header_carrier_name = schema_data.get("carrier_name")

        # Parse Estimated Annual from combined text (default to 0.0 if missing)
        estimated_annual_value = 0.0
        if all_text:
            m = re.search(
                r"Estimated\s+Annual\s*([$\\s]*[0-9][0-9,]*(?:\.[0-9]{1,2})?)",
                all_text,
                re.IGNORECASE,
            )
            if m:
                display_val = m.group(1).strip()
                numeric_str = re.sub(r"[^0-9.]", "", display_val)
                if numeric_str:
                    try:
                        estimated_annual_value = float(numeric_str)
                    except ValueError:
                        estimated_annual_value = 0.0

        summary_level = []
        for y in years_sorted:
            y_str = str(y)

            year_policy_numbers = set()
            if header_policy_number:
                year_policy_numbers.add(header_policy_number)
            for claim in claims_only:
                if str(claim.get("claim_year")) != y_str:
                    continue
                policy_number = claim.get("policy_number")
                if policy_number:
                    year_policy_numbers.add(policy_number)
            policy_number_value = sorted(year_policy_numbers)[0] if year_policy_numbers else None

            year_carrier_names = set()
            if header_carrier_name:
                year_carrier_names.add(header_carrier_name)
            for claim in claims_only:
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
            "claims": claims_only,
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
            
        # Verification package
        verification_data = {
            "session_id": session_id,
            "session_dir": str(session_dir),
            "source_pdf": pdf_path,
            "pages": pages_metadata,
            "combined_text": all_text,
            "extracted_schema": schema_output,
            "schema_file": str(schema_file),
            "summary": {
                "total_pages": len(pages_metadata),
                "scanned_pages": sum(1 for p in pages_metadata if p.get('is_scanned', False)),
                "avg_confidence": sum(p.get('confidence', 0.0) for p in pages_metadata) / len(pages_metadata) if pages_metadata else 0.0,
                "claims_count": len(claims_only)
            }
        }
        verification_file = session_dir / "verification_package.json"
        with open(verification_file, 'w', encoding='utf-8') as f:
            json.dump(verification_data, f, indent=2, ensure_ascii=False, default=str)
            
        print(f"\n{'='*60}")
        print(f"✅ EXTRACTION COMPLETE")
        print(f"{'='*60}")
        print(f"Output: {session_dir}")
        
        # Cleanup temp rotated file
        try:
            if 'temp_rotated_dir' in locals() and os.path.exists(temp_rotated_dir):
                shutil.rmtree(temp_rotated_dir, ignore_errors=True)
        except Exception:
            pass

        return verification_data

    def extract_schema_from_text(self, all_text: str, target_claim_number: Optional[str] = None, num_pages: Optional[int] = None, vision_pattern: Optional[Dict] = None) -> Dict:
        """
        OVERRIDE: Implements chunking before calling extraction.
        
        RC1 FIX: Build the GLOBAL master claim list from full text BEFORE
        chunking so each per-chunk extraction filters against the correct
        universe of IDs (not just the IDs visible in one slice).
        """
        if target_claim_number:
            return super().extract_schema_from_text(all_text, target_claim_number, vision_pattern=vision_pattern)
            
        print(f"\n⭐ NEW STEP: POLICY DETECTION & CHUNKING ⭐")

        # ── RC1: Build GLOBAL master list on full text ONCE ──────────────────
        print(f"\n📋 Building GLOBAL master claim list from full document...")
        global_detected = self._detect_claim_numbers_ai(all_text)
        global_master_list = [c["claim_number"] for c in global_detected.get("claim_numbers", [])]
        if global_master_list:
            print(f"   ✅ Global master list: {len(global_master_list)} IDs → {', '.join(global_master_list)}")
        else:
            print(f"   ⚠️ No global claim IDs detected — will use per-chunk detection.")
        # ──────────────────────────────────────────────────────────────────────
        
        chunker = PolicyChunker(self.client)
        boundaries = chunker.detect_policy_boundaries(all_text)
        
        chunks = []
        strategy = "policy-based"
        
        if len(boundaries) <= 1:
            # Large document fallback → dynamic AI chunking
            if num_pages and num_pages >= 55:
                print(f"   ⚠️ Large document ({num_pages} pages) with no clear policy boundaries.")
                print("   🔄 Falling back to dynamic AI chunking to avoid token limits...")
                dynamic_chunks = self._chunk_text_dynamically(all_text)
                
                for dc in dynamic_chunks:
                    chunks.append({
                        "policy_number": f"Part {dc.get('chunk_id', 0) + 1}",
                        "text": dc.get("text", "")
                    })
                strategy = "dynamic-fallback"
            else:
                print("   ℹ️ Single policy or no boundaries detected. Proceeding with single-shot extraction.")
                return super()._extract_all_claims(all_text, vision_pattern=vision_pattern,
                                                   pre_built_master_list=global_master_list or None)
        else:
            # RC2: split_into_chunks now adds CHUNK_OVERLAP_CHARS boundary overlap
            chunks = chunker.split_into_chunks(all_text, boundaries, overlap=CHUNK_OVERLAP_CHARS)
            
        # ── RC2b: Page-aware secondary fallback for large chunks ──────────────
        final_chunks = []
        for chunk in chunks:
            if len(chunk["text"]) > 20000:
                print(f"   ⚠️ Chunk '{chunk['policy_number']}' is too large ({len(chunk['text'])} chars). Splitting by pages...")
                page_markers = list(re.finditer(r'(?i)(=+\nPAGE\s+\d+\n=+|Page\s*\d+\s*(?:of|f|0f|o\s*f)?\s*\d+)', chunk["text"]))
                
                if page_markers:
                    last_pos = 0
                    sub_part = 1
                    for i, m in enumerate(page_markers):
                        if m.start() > last_pos + 100:
                            # RC2b: include CHUNK_OVERLAP_CHARS before the page marker
                            seg_end = m.start()
                            # extend into next segment by overlap so boundary claims are complete
                            overlap_end = min(len(chunk["text"]), seg_end + CHUNK_OVERLAP_CHARS)
                            final_chunks.append({
                                "policy_number": f"{chunk['policy_number']} (Part {sub_part})",
                                "text": chunk["text"][last_pos:overlap_end].strip()
                            })
                            last_pos = m.start()   # next chunk starts at the marker (not after overlap)
                            sub_part += 1
                    
                    # Add remaining tail
                    final_chunks.append({
                        "policy_number": f"{chunk['policy_number']} (Part {sub_part})",
                        "text": chunk["text"][last_pos:].strip()
                    })
                else:
                    # Char-split fallback: always overlap adjacent splits
                    chunk_size = 15000
                    text = chunk["text"]
                    part = 1
                    for i in range(0, len(text), chunk_size):
                        # RC2b: start each block CHUNK_OVERLAP_CHARS before cut point
                        block_start = max(0, i - CHUNK_OVERLAP_CHARS) if i > 0 else 0
                        final_chunks.append({
                            "policy_number": f"{chunk['policy_number']} (Split {part})",
                            "text": text[block_start:i + chunk_size]
                        })
                        part += 1
            else:
                final_chunks.append(chunk)
        # ─────────────────────────────────────────────────────────────────────
                
        chunks = final_chunks
        print(f"   ✂️ Final processing: {len(chunks)} chunks using {strategy} strategy.")
        
        # Generate Chunking Report
        report = {
            "total_original_chars": len(all_text),
            "num_chunks": len(chunks),
            "strategy": strategy,
            "num_pages": num_pages,
            "global_master_list": global_master_list,
            "chunks": [],
            "total_chunked_chars": sum(len(c["text"]) for c in chunks),
            "integrity_check": "Chunks include overlap — total may exceed original"
        }
        
        for chunk_idx, c in enumerate(chunks):
            report["chunks"].append({
                "chunk_id": chunk_idx + 1,
                "policy": c["policy_number"],
                "length": len(c["text"]),
                "preview_start": c["text"][:100],
                "preview_end": c["text"][-100:]
            })
            
        # Save chunking report
        if hasattr(self, 'current_session_dir'):
            report_file = self.current_session_dir / "chunking_report.json"
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            print(f"   ✓ Chunking report saved: {report_file}")
        
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Pre-allocate array to preserve strictly identical order regardless of thread finish times
        unordered_results = [None] * len(chunks)
        _super_extract = super()._extract_all_claims

        def process_chunk(idx, chunk):
            print(f"\n{'='*40}")
            print(f"📦 STARTING CHUNK {idx+1}/{len(chunks)}: Policy {chunk['policy_number']}")
            print(f"{'='*40}")
            # RC1: Pass global master list so each chunk filters against full ID universe.
            chunk_res = _super_extract(
                chunk["text"],
                vision_pattern=vision_pattern,
                pre_built_master_list=global_master_list or None
            )
            # Inject internal tracking ID (used only in chunking_report, not in claims)
            if isinstance(chunk_res, dict):
                chunk_res["_chunk_id"] = idx + 1
            return idx, chunk_res

        # Execute parallel AI API Calls (max 5 to protect OpenAI rate limits)
        print(f"\n🚀 Launching Parallel Processing Pool for {len(chunks)} chunks (max 5 workers)...")
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_idx = {executor.submit(process_chunk, i, chunk): i for i, chunk in enumerate(chunks)}
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    idx, chunk_result = future.result()
                    unordered_results[idx] = chunk_result
                    print(f"   ✅ Finished processing CHUNK {idx+1}/{len(chunks)}")
                except Exception as exc:
                    import traceback
                    print(f"   ⚠️ Chunk {idx+1} generated an exception: {exc}")
                    traceback.print_exc()
                    unordered_results[idx] = {}

        # Post-process results precisely in the original sequential order
        all_results = []
        for i, chunk_result in enumerate(unordered_results):
            if not chunk_result or "claims" not in chunk_result:
                print(f"   ⚠️ No claims found in chunk {i+1}")
                continue

            chunk = chunks[i]
            # RC7: Use carrier detected at the boundary header if present.
            chunk_carrier = chunk_result.get("carrier_name") or chunk.get("carrier_name")

            for c in chunk_result["claims"]:
                # Propagate carrier name from chunk header if claim doesn't have one
                if not c.get("carrier_name") and chunk_carrier:
                    c["carrier_name"] = chunk_carrier

                if not c.get("policy_number") or c.get("policy_number") == "Multiple":
                    # RC5: Strip (Part X) AND year suffix from policy label
                    clean_policy = re.sub(r' \(Part \d+\)$', '', str(chunk.get("policy_number", "")))
                    clean_policy = re.sub(r'\s*-\s*\d{4}$', '', clean_policy).strip()
                    c["policy_number"] = clean_policy

                # NOTE: chunk_id stored only in chunking_report.json — NOT added to claims.

            all_results.append(chunk_result)

        merged_result = self._merge_chunks(all_results, all_text=all_text,
                                           global_master_list=global_master_list,
                                           vision_pattern=vision_pattern)
        return merged_result

    def _merge_chunks(self, results_list: List[Dict], all_text: str = "",
                      global_master_list: Optional[List[str]] = None,
                      vision_pattern: Optional[Dict] = None) -> Dict:
        """
        Merges multiple extraction results into a single report.
        
        RC3 FIX: After merging all chunks, compare against the global master
        list and run a final recovery pass on the FULL document for any IDs
        still missing — these are the boundary-straddle casualties.
        
        RC4 FIX: Tie-breaking in _post_process_claims is now 'keep first'
        (i.e., existing wins when math scores are equal) — see insurance_extractor.py.
        """
        print(f"\n⭐ MERGING {len(results_list)} CHUNKS ⭐")
        
        if not results_list:
            return {"claims": []}
            
        merged = {
            "policy_number": "Multiple",
            "insured_name": results_list[0].get("insured_name"),
            "report_date": results_list[0].get("report_date"),
            "policy_period": "Multiple",
            "claims": []
        }
        
        policy_numbers = set()
        for res in results_list:
            if res.get("policy_number"):
                policy_numbers.add(res["policy_number"])
            if "claims" in res and isinstance(res["claims"], list):
                merged["claims"].extend(res["claims"])
        
        if len(policy_numbers) == 1:
            merged["policy_number"] = list(policy_numbers)[0]
        elif policy_numbers:
            merged["policy_number"] = ", ".join(sorted(list(policy_numbers)))

        # Infer a global carrier_name. Prioritize names found in individual chunks.
        # If multiple carriers found, list them.
        chunk_carriers = set()
        for res in results_list:
            # Check top level
            if res.get("carrier_name"):
                chunk_carriers.add(res["carrier_name"])
            # Check inside SummaryLevel if present
            sl = res.get("SummaryLevel")
            if isinstance(sl, list):
                for rec in sl:
                    if isinstance(rec, dict) and rec.get("carrier_name"):
                        chunk_carriers.add(rec["carrier_name"])
            elif isinstance(sl, dict):
                # Backward compatibility: older outputs stored comma-separated carrier_names
                if sl.get("carrier_names"):
                    names = [n.strip() for n in str(sl["carrier_names"]).split(",")]
                    for n in names:
                        if n:
                            chunk_carriers.add(n)
                if sl.get("carrier_name"):
                    chunk_carriers.add(sl["carrier_name"])
            # Check individual claims
            if "claims" in res and isinstance(res["claims"], list):
                for c in res["claims"]:
                    if c.get("carrier_name"):
                        chunk_carriers.add(c["carrier_name"])

        if chunk_carriers:
            merged["carrier_name"] = ", ".join(sorted(list(chunk_carriers)))
            print(f"   ✓ Aggregated carrier_name from chunks: {merged['carrier_name']}")
        elif all_text:
            # Only fallback to inference if no carriers found in chunks
            inferred_carrier = self._infer_carrier_from_text(all_text)
            if inferred_carrier:
                merged["carrier_name"] = inferred_carrier
                print(f"   ✓ Inferred global carrier_name: {inferred_carrier}")
            
        # Global post-processing + deduplication (RC4 tie-breaker is in _post_process_claims)
        print(f"   🔍 Performing global post-extraction audit...")
        merged = self._post_process_claims(merged, master_claim_list=global_master_list or None)
        
        print(f"   ✅ After merge: {len(merged['claims'])} total claims.")
        
        # ── RC3: Final global recovery for boundary-straddle casualties ──────
        if global_master_list and all_text:
            extracted_ids = {str(c.get("claim_number", "")).strip() for c in merged.get("claims", [])}
            # Normalise leading zeros for comparison
            extracted_ids_norm = {cid.lstrip("0") for cid in extracted_ids}
            still_missing = [
                m for m in global_master_list
                if str(m).strip() not in extracted_ids
                and str(m).strip().lstrip("0") not in extracted_ids_norm
            ]
            
            if still_missing:
                print(f"\n   🔁 RC3 GLOBAL RECOVERY: {len(still_missing)} claim(s) still missing after merge.")
                print(f"   Missing IDs: {', '.join(str(m) for m in still_missing)}")
                
                batch_size = 5
                for i in range(0, len(still_missing), batch_size):
                    batch = still_missing[i:i + batch_size]
                    print(f"   🔄 Global Recovery Batch {i//batch_size + 1}: {', '.join(str(b) for b in batch)}")
                    try:
                        recovery_data = self._extract_missing_claims_by_number(all_text, merged, batch)
                        if recovery_data and "claims" in recovery_data and recovery_data["claims"]:
                            merged["claims"].extend(recovery_data["claims"])
                            print(f"      ✓ Recovered {len(recovery_data['claims'])} claim(s) in this batch.")
                    except Exception as e:
                        print(f"      ⚠️ Global recovery batch failed: {e}")
                
                # Final dedup pass after recovery
                merged = self._post_process_claims(merged, master_claim_list=global_master_list)
                print(f"   ✅ Final count after global recovery: {len(merged['claims'])} claims.")
            else:
                print(f"   ✅ All {len(global_master_list)} global claim IDs accounted for.")
        # ─────────────────────────────────────────────────────────────────────
        
        return merged


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    print("ChunkedInsuranceExtractor loaded.")
