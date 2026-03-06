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

def _perform_extraction(file: UploadFile, request: Request):
    import traceback
    import re

    # ── Guard: filename must not be None or empty ────────────────────────────
    raw_filename = file.filename or ""
    if not raw_filename.strip():
        raise HTTPException(status_code=400, detail="filename is required. Make sure your request uses 'Content-Disposition: filename=...' in the file part.")

    # Sanitize: strip path separators to prevent path-traversal
    safe_filename = re.sub(r'[\\/:*?"<>|]', "_", raw_filename)
    file_ext = Path(safe_filename).suffix.lower()
    if file_ext not in [".pdf", ".xlsx", ".xls", ".csv"]:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{file_ext}'. Only PDF, Excel and CSV files are accepted.")

    print(f"\n[Unified][API] Received request for: {safe_filename}")

    # If monitoring is enabled, update the request record with real filename/size
    try:
        request_id = getattr(request.state, "monitoring_request_id", None)
        if request_id:
            from monitor.service import request_monitor
            # Best-effort size: UploadFile doesn't always expose .size; use file obj if possible
            file_size = None
            try:
                if hasattr(file, "size") and file.size is not None:
                    file_size = int(file.size)
            except Exception:
                file_size = None

            request_monitor.update_request_file_info(
                request_id=request_id,
                filename=safe_filename,
                file_size=file_size
            )
            request_monitor.update_request_status(request_id=request_id, status="processing")
    except Exception:
        pass

    file_path = UPLOAD_DIR / safe_filename
    try:
        # Save the uploaded file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        print(f"[Unified][API] Saved to: {file_path}")

        # Run the unified router (sync)
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
        if doc_type == "invoice_poc_extractor":
            doc_type = "VENDOR_INVOICE"
        
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
        
        # Add Vendor Invoice specific metadata (supports both single and merged outputs)
        if doc_type == "VENDOR_INVOICE" and json_path:
            try:
                import json as json_lib
                with open(json_path, "r", encoding="utf-8") as f:
                    invoice_data = json_lib.load(f)

                # Merged flat format: [ { "HEADER": {...}, "LINE_ITEMS": [...] }, ... ]
                if isinstance(invoice_data, list):
                    invoices = invoice_data or []
                    vendor_names = []
                    total_sum = 0.0
                    for inv in invoices:
                        data = inv or {}
                        header = (data or {}).get("HEADER") or {}
                        vn = header.get("VENDOR_NAME")
                        if vn:
                            vendor_names.append(str(vn))
                        ta = header.get("TOTAL_AMOUNT", 0) or 0
                        if isinstance(ta, str):
                            try:
                                ta = float(ta.replace(",", "").replace("$", ""))
                            except Exception:
                                ta = 0.0
                        try:
                            total_sum += float(ta)
                        except Exception:
                            pass

                    uniq = []
                    for v in vendor_names:
                        if v not in uniq:
                            uniq.append(v)

                    display_vendor = " | ".join(uniq[:3])
                    if len(uniq) > 3:
                        display_vendor = f"{display_vendor} (+{len(uniq) - 3} more)"

                    response["insurer"] = f"Merged invoices ({len(invoices)}) - {display_vendor}" if invoices else "Merged invoices"
                    response["total_value"] = total_sum
                    response["invoice_count"] = len(invoices)
                    print(f"[Unified][API] Extracted Vendor Invoice Metadata: merged={len(invoices)} total=${total_sum}")
                else:
                    # Single format: {"HEADER": {...}, "LINE_ITEMS": [...]}
                    header = invoice_data.get("HEADER", {})
                    vendor_name = header.get("VENDOR_NAME", "N/A")
                    total_amount = header.get("TOTAL_AMOUNT", 0)

                    # Try to clean total_amount if it's a string
                    if isinstance(total_amount, str):
                        try:
                            total_amount = float(total_amount.replace(",", "").replace("$", ""))
                        except Exception:
                            total_amount = 0

                    response["insurer"] = vendor_name
                    response["total_value"] = total_amount
                    print(f"[Unified][API] Extracted Vendor Invoice Metadata: {vendor_name}, ${total_amount}")
            except Exception as meta_err:
                print(f"[Unified][WARN] Could not extract vendor invoice metadata: {meta_err}")

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
        tb = traceback.format_exc()
        print(f"[Unified][ERROR] {type(e).__name__}: {e}\n{tb}")
        raise HTTPException(
            status_code=500,
            detail=f"{type(e).__name__}: {str(e)}"
        )
