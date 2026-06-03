import os
import shutil
import zipfile
import tempfile
import logging
from pathlib import Path
from typing import Dict
from fastapi import UploadFile, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from unified_router import UnifiedRouter

# Initialize logger
logger = logging.getLogger("shared_configs")

# Shared directories
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Shared state
router_engine = UnifiedRouter()
file_path_cache: Dict[str, str] = {}

async def _perform_extraction(file: UploadFile, request: Request):
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

    logger.info(f"[Unified][API] Received request for: {safe_filename}")

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
        logger.info(f"[Unified][API] Saved to: {file_path}")

        # Run the unified router (Async)
        logger.info(f"[Unified][API] Routing document...")
        result = await router_engine.process(str(file_path), request_id=request_id)

        if "error" in result:
            logger.warning(f"[Unified][WARN] Extraction returned error: {result['error']}")
            return {"error": result["error"]}
        
        # Extract filenames and full paths
        excel_path = result.get("excel")
        json_path = result.get("json")
        
        excel_filename = Path(excel_path).name if excel_path else None
        json_filename = Path(json_path).name if json_path else None
        
        # Cache the full paths for download endpoint
        if excel_path:
            cache_key = f"{request_id}/{excel_filename}" if request_id else excel_filename
            file_path_cache[cache_key] = excel_path
            logger.info(f"[Unified][API] Cached Excel: {cache_key} -> {excel_path}")
        if json_path:
            cache_key = f"{request_id}/{json_filename}" if request_id else json_filename
            file_path_cache[cache_key] = json_path
            logger.info(f"[Unified][API] Cached JSON: {cache_key} -> {json_path}")
        
        # Transform response to match frontend expectations
        doc_type = result.get("type", "UNKNOWN")
        if doc_type == "invoice_poc_extractor":
            doc_type = "VENDOR_INVOICE"
        
        # Build base URL for downloads (respect public host if proxied)
        # [MODIFIED] Force production domain as requested by user
        base_url = "https://drive1.cognethro.com"
        
        # Build base response with clickable URLs
        response = {
            "type": doc_type,
            "requestId": request_id,
            "output_file": f"{request_id}/{excel_filename}" if excel_filename and request_id else excel_filename,
            "output_json": f"{request_id}/{json_filename}" if json_filename and request_id else json_filename,
            "excel": f"{base_url}/api/download/{request_id}/{excel_filename}" if excel_filename and request_id else (f"{base_url}/api/download/{excel_filename}" if excel_filename else None),
            "json": f"{base_url}/api/download/{request_id}/{json_filename}" if json_filename and request_id else (f"{base_url}/api/download/{json_filename}" if json_filename else None),
            "pages": result.get("pages", 0),
            "trigger_point": "cognethro"
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
                    logger.info(f"[Unified][API] Extracted Vendor Invoice Metadata: merged={len(invoices)} total=${total_sum}")
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
                    logger.info(f"[Unified][API] Extracted Vendor Invoice Metadata: {vendor_name}, ${total_amount}")
            except Exception as meta_err:
                logger.warning(f"[Unified][API][WARN] Could not extract vendor invoice metadata: {meta_err}")

        # Add STANDARD INVOICE (Benefit/Insurance) specific metadata
        if doc_type == "INVOICE" and json_path:
            try:
                import json as json_lib
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json_lib.load(f)
                
                # In universal_pdf_extractor_v3 JSON, data is usually a list of items or a dict with "line_items"
                items = data if isinstance(data, list) else data.get("line_items", [])
                
                total_val = 0.0
                insurer_name = "Insurance Document"
                
                if items:
                    # Look for the special audit row or the INV_TOTAL field
                    for item in items:
                        # Priority 1: Check for the 'INV_TOTAL' metadata field on any row
                        it = item.get("INV_TOTAL")
                        if it and str(it).lower() not in ["n/a", "none", "", "nan"]:
                            try:
                                total_val = float(str(it).replace(",", "").replace("$", ""))
                                if total_val > 0:
                                    break
                            except: pass

                    if not total_val:
                        # Priority 2: Look for the audit summary row with priority labels
                        priority_order = ["AMOUNT DUE", "INVOICED AMOUNT", "BALANCE DUE", "REPORTED INVOICE TOTAL", "GRAND TOTAL"]
                        
                        found = False
                        for label in priority_order:
                            for item in reversed(items):
                                pn = str(item.get("PLAN_NAME") or "").upper()
                                fn = str(item.get("FIRSTNAME") or "").upper()
                                if label in pn or label in fn:
                                    try:
                                        val = float(str(item.get("CURRENT_PREMIUM", 0)).replace(",", "").replace("$", ""))
                                        if val > 0:
                                            total_val = val
                                            found = True
                                            break
                                    except: pass
                            if found: break
                    
                    if not total_val:
                        # Priority 3: Fallback to summing CURRENT_PREMIUM (for rows that have a name)
                        total_val = sum(float(str(i.get("CURRENT_PREMIUM", 0)).replace(",", "").replace("$", "")) for i in items if i.get("FIRSTNAME"))
                
                response["insurer"] = insurer_name
                response["total_value"] = total_val
                logger.info(f"[Unified][API] Extracted Insurance Invoice Metadata: {insurer_name}, ${total_val}")
            except Exception as meta_err:
                logger.warning(f"[Unified][API][WARN] Could not extract insurance invoice metadata: {meta_err}")

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
                logger.warning(f"[Unified][WARN] Could not extract work comp metadata: {meta_err}")

        # Add Bank Statement specific metadata
        if doc_type == "BANK_STATEMENT" and json_path:
            try:
                import json as json_lib
                with open(json_path, "r", encoding="utf-8") as f:
                    bank_data = json_lib.load(f)
                
                # bank_data schema: { "deposits_and_credits": [...], "checks_and_other_debits": [...], ... }
                deposits = bank_data.get("deposits_and_credits", []) or []
                debits = bank_data.get("checks_and_other_debits", []) or []
                total_transactions = len(deposits) + len(debits)
                
                response["insurer"] = "Bank Statement"
                response["claims_count"] = total_transactions
                logger.info(f"[Unified][API] Extracted Bank Statement Metadata: tx_count={total_transactions}")
            except Exception as meta_err:
                logger.warning(f"[Unified][WARN] Could not extract bank statement metadata: {meta_err}")
                
        return response
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"[Unified][ERROR] {type(e).__name__}: {e}\n{tb}")
        raise HTTPException(
            status_code=500,
            detail=f"{type(e).__name__}: {str(e)}"
        )
