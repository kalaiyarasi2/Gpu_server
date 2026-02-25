import os
import shutil
import zipfile
import tempfile
from pathlib import Path
from typing import Dict
from fastapi import UploadFile, HTTPException, Request
from unified_router import UnifiedRouter

# Shared directories
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Shared state
router_engine = UnifiedRouter()
file_path_cache: Dict[str, str] = {}

async def _perform_extraction(file: UploadFile, request: Request):
    print(f"\n[Unified][API] Received request for: {file.filename}")
    
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in [".pdf", ".xlsx", ".xls", ".csv"]:
        raise HTTPException(status_code=400, detail="Only PDF, Excel and CSV files are supported")
    
    file_path = UPLOAD_DIR / file.filename
    try:
        # Save the uploaded file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Run the unified router
        print(f"[Unified][API] Routing document...")
        result = router_engine.process(str(file_path))
        
        if "error" in result:
            print(f"[Unified][WARN] Extraction returned error: {result['error']}")
            return {"error": result["error"]}
        
        # Extract filenames and full paths
        excel_path = result.get("excel")
        json_path = result.get("json")
        
        excel_filename = Path(excel_path).name if excel_path else None
        json_filename = Path(json_path).name if json_path else None
        
        # Cache the full paths for download endpoint
        if excel_path:
            file_path_cache[excel_filename] = excel_path
            print(f"[Unified][API] Cached Excel: {excel_filename} -> {excel_path}")
        if json_path:
            file_path_cache[json_filename] = json_path
            print(f"[Unified][API] Cached JSON: {json_filename} -> {json_path}")
        
        # Transform response to match frontend expectations
        doc_type = result.get("type", "UNKNOWN")
        
        # Build base URL for downloads
        base_url = str(request.base_url).rstrip("/")
        
        # Build base response with clickable URLs
        response = {
            "type": doc_type,
            "output_file": excel_filename,
            "output_json": json_filename,
            "excel": f"{base_url}/api/download/{excel_filename}" if excel_filename else None,
            "json": f"{base_url}/api/download/{json_filename}" if json_filename else None
        }
        
        # Add Work Compensation specific metadata
        if doc_type == "WORK_COMPENSATION" and json_path:
            try:
                import json as json_lib
                with open(json_path, "r", encoding="utf-8") as f:
                    wc_data = json_lib.load(f)
                
                inner = wc_data.get("data", {})
                demographics = inner.get("demographics", {})
                premium_calc = inner.get("premiumCalculation", {})
                rating_by_state = inner.get("ratingByState", [])
                
                # Detect form type from wcStates field or state list
                wc_states_raw = demographics.get("wcStates", "") or ""
                wc_states = [s.strip().upper() for s in wc_states_raw.replace(",", " ").split() if s.strip()]
                
                if "CA" in wc_states:
                    form_type = "California ACORD"
                elif "FL" in wc_states:
                    form_type = "Florida ACORD"
                elif wc_states:
                    form_type = f"ACORD ({', '.join(wc_states[:3])})"
                else:
                    form_type = "Standard ACORD 130"
                
                # Get total premium
                total_premium = premium_calc.get("totalEstimatedAnnualPremium", 0) or 0
                if not total_premium and rating_by_state:
                    total_premium = sum(
                        float(r.get("estimatedAnnualPremium", 0) or 0)
                        for r in rating_by_state
                    )
                
                applicant_name = demographics.get("applicantName", "N/A")
                
                response["work_comp_metadata"] = {
                    "form_type": form_type,
                    "total_premium": total_premium,
                    "applicant_name": applicant_name,
                    "wc_states": wc_states
                }
            except Exception as meta_err:
                print(f"[Unified][WARN] Could not extract work comp metadata: {meta_err}")
        
        return response
    except Exception as e:
        print(f"[Unified][ERROR] {e}")
        raise HTTPException(status_code=500, detail=str(e))
