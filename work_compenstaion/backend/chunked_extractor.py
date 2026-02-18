import json
import re
from typing import Dict, List, Optional, Tuple
from work_compensation import EnhancedInsuranceExtractor
from pdf_rotation import auto_rotate_pdf_content
import tempfile
import shutil

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

    def split_into_chunks(self, text: str, boundaries: List[Dict]) -> List[Dict]:
        """Splits the text into chunks based on detected boundaries."""
        if not boundaries:
            return [{"policy_number": "Unknown", "text": text}]
            
        chunks = []
        
        # Add content BEFORE the first boundary if it exists
        if boundaries[0]["start_index"] > 10: # Threshold for meaningful start content
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
            
            chunk_text = text[start_idx:end_idx].strip()
            chunks.append({
                "policy_number": boundaries[i]["policy_number"],
                "text": chunk_text
            })
            
        return chunks

class ChunkedInsuranceExtractor(EnhancedInsuranceExtractor):
    """
    Extends EnhancedInsuranceExtractor to support policy-based chunking.
    This prevents token limit issues by splitting large documents into policy-specific chunks.
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
        
        # Save combined text
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

    def extract_schema_from_text(self, all_text: str, target_claim_number: Optional[str] = None) -> Dict:
        """
        OVERRIDE: Implements chunking before calling extraction.
        """
        if target_claim_number:
            return super().extract_schema_from_text(all_text, target_claim_number)
            
        print(f"\n⭐ NEW STEP: POLICY DETECTION & CHUNKING ⭐")
        
        chunker = PolicyChunker(self.client)
        boundaries = chunker.detect_policy_boundaries(all_text)
        
        if len(boundaries) <= 1:
            print("   ℹ️ Single policy or no boundaries detected. Proceeding with single-shot extraction.")
            return super()._extract_all_claims(all_text)
            
        chunks = chunker.split_into_chunks(all_text, boundaries)
        print(f"   ✂️ Split into {len(chunks)} chunks.")
        
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
        for i, chunk in enumerate(chunks):
            print(f"\n{'='*40}")
            print(f"📦 CHUNK {i+1}/{len(chunks)}: Policy {chunk['policy_number']}")
            print(f"{'='*40}")
            
            chunk_result = super()._extract_all_claims(chunk["text"])
            
            if "data" in chunk_result:
                all_results.append(chunk_result)
            else:
                print(f"   ⚠️ No structured data found in chunk {i+1}")
                
        merged_result = self._merge_chunks(all_results)
        return merged_result

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
                "generalQuestions": results_list[0].get("data", {}).get("generalQuestions", {}),
                "priorCarriers": [],
                "individuals": [],
                "premiumCalculation": results_list[0].get("data", {}).get("premiumCalculation", {})
            }
        }
        
        seen_rating = set()
        seen_carriers = set()
        seen_individuals = set()
        
        for res in results_list:
            inner = res.get("data", {})
            
            # Merge ratingByState
            for entry in inner.get("ratingByState", []):
                key = (entry.get("state"), entry.get("classCode"), entry.get("estAnnualPayroll"))
                if key not in seen_rating:
                    merged["data"]["ratingByState"].append(entry)
                    seen_rating.add(key)
            
            # Merge priorCarriers
            for carrier in inner.get("priorCarriers", []):
                key = (carrier.get("carrierName"), carrier.get("year"), carrier.get("policyNumber"))
                if key not in seen_carriers:
                    merged["data"]["priorCarriers"].append(carrier)
                    seen_carriers.add(key)
            
            # Merge individuals
            for ind in inner.get("individuals", []):
                key = (ind.get("name"), ind.get("title"))
                if key not in seen_individuals:
                    merged["data"]["individuals"].append(ind)
                    seen_individuals.add(key)
            
            # Update premiumCalculation (take the most complete one, or just the first if non-zero)
            # For simplicity, if the baseline is empty/zero, and this one isn't, use this one
            current_premium = inner.get("premiumCalculation", {})
            if current_premium.get("totalEstimatedAnnualPremium", 0.0) > 0 and \
               merged["data"]["premiumCalculation"].get("totalEstimatedAnnualPremium", 0.0) == 0:
                merged["data"]["premiumCalculation"] = current_premium
                    
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
