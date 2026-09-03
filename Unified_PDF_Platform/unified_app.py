import os
import sys
import shutil
import logging
import zipfile
import tempfile
import subprocess
from typing import List, Dict
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

# Fix for "Decompression Bomb" error in PIL
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

# Ensure stdout can handle UTF-8 for emojis
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


# Add project root to Python path so 'monitor' package can be imported
import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)  # pdf_extractor root
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
# Import summary router
from summary_api import router as summary_router
from ai_summary_file import router as ai_summary_router
from claims_dashboard_api import router as claims_dashboard_router
from management_claims_dashboard_api import router as management_claims_dashboard_router

# Import monitoring components
from monitor import add_monitoring_to_app
from monitor.endpoints import router as monitor_router

# Import documentation constants
from swagger_docs import (
    API_TITLE, API_DESCRIPTION, API_VERSION, 
    CUSTOM_SWAGGER_JS, COGNETHRO_SUMMARY,
    WORK_COMP_SUMMARY, WORK_COMP_SWAGGER_JS
)
from shared_configs import BASE_DIR, _perform_extraction, file_path_cache

# Initialize logger
logger = logging.getLogger("unified_app")

# Load environment variables from parent directory
load_dotenv(BASE_DIR.parent / ".env")

app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    docs_url=None,  # Override for custom download buttons logic
    redoc_url="/redoc"
)

# Attach monitoring middleware and endpoints
app = add_monitoring_to_app(app)
app.include_router(monitor_router)

# BASE_DIR and UPLOAD_DIR are now imported from shared_configs

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# BASE_DIR and UPLOAD_DIR are now defined at the top
# Include summary_api router
app.include_router(summary_router)
app.include_router(ai_summary_router)
app.include_router(claims_dashboard_router)
app.include_router(management_claims_dashboard_router)

# --- Background Email Pipeline Management ---
email_process = None

@app.on_event("startup")
def startup_event():
    global email_process
    print("[SYSTEM] Starting Unified Application...")
    email_pipeline_dir = os.path.join(parent_dir, "Email_pipeline")
    script_path = os.path.join(email_pipeline_dir, "main.py")
    
    if os.path.exists(script_path):
        print(f"[SYSTEM] Starting Email Pipeline Watcher from {email_pipeline_dir}...")
        try:
            email_process = subprocess.Popen(
                [sys.executable, "main.py", "--interval", "60"],
                cwd=email_pipeline_dir
            )
            print("[SYSTEM] Email Pipeline Watcher started successfully.")
        except Exception as e:
            print(f"[ERROR] Failed to start Email Pipeline Watcher: {e}")
    else:
        print(f"[WARNING] Email Pipeline script not found at {script_path}")

@app.on_event("shutdown")
def shutdown_event():
    global email_process
    print("[SYSTEM] Shutting down Unified Application...")
    if email_process:
        print("[SYSTEM] Terminating background Email Pipeline Watcher...")
        email_process.terminate()
        try:
            email_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            email_process.kill()
        print("[SYSTEM] Email Pipeline Watcher terminated.")


@app.get("/monitor", include_in_schema=False)
async def monitor_dashboard():
    """Serve the monitoring dashboard HTML (simple static page)."""
    dashboard_path = BASE_DIR.parent / "monitor" / "dashboard" / "index.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    return HTMLResponse(
        content="<h1>Monitor dashboard not found</h1><p>Expected <code>monitor/dashboard/index.html</code>.</p>",
        status_code=404,
    )

@app.get("/ai-monitor", include_in_schema=False)
async def ai_monitor_dashboard():
    """Serve the AI usage monitoring dashboard HTML."""
    dashboard_path = BASE_DIR.parent / "monitor" / "dashboard" / "ai_monitor.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path)
    return HTMLResponse(
        content="<h1>AI Monitor dashboard not found</h1><p>Expected <code>monitor/dashboard/ai_monitor.html</code>.</p>",
        status_code=404,
    )


# Mount static and templates for the new React frontend
frontend_dist_path = BASE_DIR / "frontend" / "dist"
if frontend_dist_path.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist_path / "assets")), name="assets")
    print(f"[OK] Mounted frontend assets from {frontend_dist_path / 'assets'}")
else:
    print(f"[WARNING] Frontend dist folder not found at {frontend_dist_path}. Run build first.")



# _perform_extraction is now imported from shared_configs

@app.post("/api/extract", include_in_schema=False)
async def extract_document(request: Request, file: UploadFile = File(...)):
    return await _perform_extraction(file, request)

@app.get("/api/health")
async def health_check():
    """Quick connectivity test for external developers.
    Returns server status and the base URL they connected to.
    """
    return {
        "status": "ok",
        "message": "Cognethro Unified PDF Platform is running",
        "version": API_VERSION,
        "supported_types": ["PDF", "XLSX", "XLS", "CSV"],
        "extract_endpoint": "POST /api/extract  (multipart/form-data, field name: 'file')",
        "example_curl": 'curl -X POST https://drive1.cognethro.com/api/extract -F "file=@yourfile.pdf"'
    }

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    response = get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="Cognethro - Standard Swagger",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
    )
    
    # Manually inject our custom JS for download buttons
    custom_js = CUSTOM_SWAGGER_JS
    
    html_content = response.body.decode("utf-8")
    new_html = html_content.replace("</body>", f"{custom_js}</body>")
    return HTMLResponse(content=new_html, status_code=response.status_code)

@app.get("/work-comp-docs", include_in_schema=False)
async def work_comp_swagger_ui():
    """Dedicated Swagger UI for the Work Compensation endpoint with JSON-only download button."""
    response = get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="Work Compensation Extractor",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
    )
    html_content = response.body.decode("utf-8")
    new_html = html_content.replace("</body>", f"{WORK_COMP_SWAGGER_JS}</body>")
    return HTMLResponse(content=new_html, status_code=response.status_code)

# Injecting the custom Script via a separate HTML header middleware if needed, 
# or just keeping it simple for now to get it working.


@app.get("/api/download/{filepath:path}", include_in_schema=False)
async def download_file(filepath: str):
    """Download endpoint that handles both absolute and relative paths."""
    logger.info(f"[Download] Requested file: {filepath}")
    
    # First, check the cache for the full path
    if filepath in file_path_cache:
        file_path = Path(file_path_cache[filepath])
    else:
        # Safety: If the path contains a URL (e.g. from a bad frontend call), strip it
        if "://" in filepath:
            filepath = filepath.split("/")[-1]
            logger.info(f"[Download] Stripped URL from path, now: {filepath}")
            
    if filepath in file_path_cache:
        file_path = Path(file_path_cache[filepath])
        logger.info(f"[Download] Found in cache: {file_path}")
        if file_path.exists():
            filename = file_path.name
            if filename.endswith(".xlsx"):
                media_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            elif filename.endswith(".json"):
                media_type = 'application/json'
            else:
                media_type = 'application/octet-stream'
            return FileResponse(path=file_path, filename=filename, media_type=media_type)
    
    # Fallback: Try to find the file manually
    original_filepath = filepath
    file_path = Path(filepath)
    
    # If the filepath contains a slash (requestId/filename), try stripping the ID for fallback search
    filename_only = filepath
    if "/" in filepath:
        parts = filepath.split("/")
        filename_only = parts[-1]
        request_id_prefix = parts[0]
        
    if not file_path.exists():
        # Try as just the filename in unified_outputs
        file_path = BASE_DIR / "unified_outputs" / filename_only
        
    if not file_path.exists():
        # Try relative to BASE_DIR
        file_path = BASE_DIR / filename_only
    
    # Try searching in the insurance outputs directory (searching by filename)
    if not file_path.exists():
        insurance_outputs = BASE_DIR.parent / "work_compenstaion" / "backend" / "outputs"
        # Try to find a directory that matches the requestId if possible
        if "/" in original_filepath:
            req_id = original_filepath.split("/")[0]
            for session_dir in insurance_outputs.glob(f"extraction_*_{req_id[:4]}*"):
                potential_file = session_dir / filename_only
                if potential_file.exists():
                    file_path = potential_file
                    break
        
        # If still not found, just find the first matching filename
        if not file_path.exists():
            for session_dir in insurance_outputs.glob("extraction_*"):
                potential_file = session_dir / filename_only
                if potential_file.exists():
                    file_path = potential_file
                    break
    
    # Try searching in unified_outputs for any matching filename
    if not file_path.exists():
        unified_out = BASE_DIR / "unified_outputs"
        if unified_out.exists():
            for potential_file in unified_out.glob(f"**/{filename_only}"):
                file_path = potential_file
                break
        
    if not file_path.exists():
        logger.error(f"[Download] File not found: {original_filepath}")
        logger.error(f"[Download] Cache contents: {list(file_path_cache.keys())}")
        raise HTTPException(status_code=404, detail=f"File not found: {original_filepath}")
    
    filename = file_path.name
    logger.info(f"[Download] Serving file from fallback path: {file_path}")
    if filename.endswith(".xlsx"):
        media_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif filename.endswith(".json"):
        media_type = 'application/json'
    else:
        media_type = 'application/octet-stream'
        
    return FileResponse(path=file_path, filename=filename, media_type=media_type)



class DriveClassifyRequest(BaseModel):
    input_folder: str
    output_folder: str
    max_pages: int = 3
    min_score: float = 3.0
    model: str = "gpt-4o"

@app.get("/api/drive/status", include_in_schema=False)
async def drive_status(input_folder: str):
    input_dir = Path(input_folder)
    if not input_dir.exists() or not input_dir.is_dir():
        return {
            "connected": False,
            "pdf_count": 0,
            "pdf_files": [],
            "input_ok": False,
            "output_ok": False
        }
    pdf_files = [p.name for p in input_dir.glob("*.pdf")]
    return {
        "connected": True,
        "pdf_count": len(pdf_files),
        "pdf_files": pdf_files,
        "input_ok": True,
        "output_ok": True
    }

@app.post("/api/drive/classify", include_in_schema=False)
async def drive_classify(body: DriveClassifyRequest):
    input_dir = Path(body.input_folder)
    output_dir = Path(body.output_folder)
    
    if not input_dir.exists() or not input_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Input directory not found: {body.input_folder}")
        
    pdf_files = list(input_dir.glob("*.pdf"))
    if not pdf_files:
        return {"message": "No PDF files found in input directory", "processed": 0, "results": []}
        
    results = []
    from unified_router import UnifiedRouter
    from shared_configs import file_path_cache
    router = UnifiedRouter()
    
    for pdf_path in pdf_files:
        try:
            # Process document through the GPU UnifiedRouter
            res = await router.process(str(pdf_path))
            
            # Copy outputs to the output directory organized by classified document type
            doc_type = res.get("type") or res.get("document_type") or "Others"
            if doc_type == "invoice_poc_extractor":
                doc_type = "VENDOR_INVOICE"
                
            filename_stem = pdf_path.stem
            dest_dir = output_dir / doc_type / filename_stem
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy original PDF
            shutil.copy2(pdf_path, dest_dir / pdf_path.name)
            
            excel_out = res.get("excel")
            excel_filename = None
            if excel_out and os.path.exists(excel_out):
                excel_filename = f"{filename_stem}_extracted.xlsx"
                dest_excel = dest_dir / excel_filename
                shutil.copy2(excel_out, dest_excel)
                file_path_cache[excel_filename] = str(dest_excel)
                
            json_out = res.get("json")
            json_filename = None
            data = None
            if json_out and os.path.exists(json_out):
                json_filename = f"{filename_stem}_extracted.json"
                dest_json = dest_dir / json_filename
                shutil.copy2(json_out, dest_json)
                file_path_cache[json_filename] = str(dest_json)
                
                # Load JSON contents
                try:
                    import json as json_lib
                    with open(json_out, "r", encoding="utf-8") as f:
                        data = json_lib.load(f)
                except Exception as je:
                    print(f"Error loading JSON data: {je}")
            
            # Extract metadata
            insurer = "Unknown Document"
            total_value = 0.0
            claims_count = 0
            work_comp_metadata = None
            
            if data is not None:
                if doc_type == "VENDOR_INVOICE":
                    try:
                        if isinstance(data, list):
                            vendor_names = []
                            total_sum = 0.0
                            for inv in data:
                                header = (inv or {}).get("HEADER") or {}
                                vn = header.get("VENDOR_NAME")
                                if vn:
                                    vendor_names.append(str(vn))
                                ta = header.get("TOTAL_AMOUNT", 0) or 0
                                if isinstance(ta, str):
                                    try:
                                        ta = float(ta.replace(",", "").replace("$", ""))
                                    except:
                                        ta = 0.0
                                total_sum += float(ta)
                            uniq = list(set(vendor_names))
                            display_vendor = " | ".join(uniq[:3])
                            insurer = f"Merged invoices ({len(data)}) - {display_vendor}" if data else "Merged invoices"
                            total_value = total_sum
                        else:
                            header = data.get("HEADER", {})
                            insurer = header.get("VENDOR_NAME", "N/A")
                            total_amount = header.get("TOTAL_AMOUNT", 0)
                            if isinstance(total_amount, str):
                                try:
                                    total_amount = float(total_amount.replace(",", "").replace("$", ""))
                                except:
                                    total_amount = 0
                            total_value = total_amount
                    except Exception as me:
                        print(f"Error parsing vendor invoice metadata: {me}")
                        
                elif doc_type == "INVOICE":
                    insurer = "Insurance Document"
                    try:
                        items = data if isinstance(data, list) else data.get("line_items", [])
                        total_val = 0.0
                        if items:
                            for item in items:
                                it = item.get("INV_TOTAL")
                                if it and str(it).lower() not in ["n/a", "none", "", "nan"]:
                                    try:
                                        total_val = float(str(it).replace(",", "").replace("$", ""))
                                        if total_val > 0:
                                            break
                                    except: pass
                            if not total_val:
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
                                total_val = sum(float(str(i.get("CURRENT_PREMIUM", 0)).replace(",", "").replace("$", "")) for i in items if i.get("FIRSTNAME"))
                        total_value = total_val
                    except Exception as me:
                        print(f"Error parsing invoice metadata: {me}")

                elif doc_type in ["INSURANCE_CLAIMS", "INSURANCE"]:
                    insurer = "Insurance Document"
                    try:
                        claims = data.get("claims", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                        claims_count = len(claims)
                        if isinstance(data, dict) and data.get("carrier"):
                            insurer = data.get("carrier")
                    except Exception as me:
                        print(f"Error parsing claims metadata: {me}")
                        
                elif doc_type == "WORK_COMPENSATION":
                    insurer = "Workers Comp Application"
                    try:
                        inner = data.get("data", {})
                        demographics = inner.get("demographics", {})
                        premium_calc = inner.get("premiumCalculation", {})
                        rating_by_state = inner.get("ratingByState", [])
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
                            
                        total_premium = premium_calc.get("totalEstimatedAnnualPremium", 0) or 0
                        if not total_premium and rating_by_state:
                            total_premium = sum(float(r.get("estimatedAnnualPremium", 0) or 0) for r in rating_by_state)
                            
                        insurer = demographics.get("applicantName", "Workers Comp Application")
                        work_comp_metadata = {
                            "form_type": form_type,
                            "total_premium": total_premium,
                            "applicant_name": demographics.get("applicantName", "N/A"),
                            "wc_states": wc_states
                        }
                        total_value = total_premium
                    except Exception as me:
                        print(f"Error parsing work comp metadata: {me}")

                elif doc_type == "BANK_STATEMENT":
                    insurer = "Bank Statement"
                    try:
                        deposits = data.get("deposits_and_credits", []) or []
                        debits = data.get("checks_and_other_debits", []) or []
                        claims_count = len(deposits) + len(debits)
                    except Exception as me:
                        print(f"Error parsing bank statement metadata: {me}")
            
            results.append({
                "file_name": pdf_path.name,
                "status": "success",
                "document_type": doc_type,
                "output_dir": str(dest_dir),
                "excel_path": excel_filename,
                "json_path": json_filename,
                "excel_url": f"http://localhost:9000/api/gpu/api/download/{excel_filename}" if excel_filename else None,
                "json_url": f"http://localhost:9000/api/gpu/api/download/{json_filename}" if json_filename else None,
                "result": data,
                "metadata": {
                    "insurer": insurer,
                    "format": doc_type.lower(),
                    "confidence": 95,
                    "claims_count": claims_count,
                    "total_value": total_value,
                    "documentType": doc_type,
                    "work_comp_metadata": work_comp_metadata
                }
            })

            # Log to unified converter.db
            try:
                import sys
                from pathlib import Path
                workspace_root = str(Path(__file__).resolve().parent.parent.parent)
                if workspace_root not in sys.path:
                    sys.path.append(workspace_root)
                from database import poc_db
                poc_db.log_universal(
                    module="DRIVE",
                    action=f"Watch Folder GPU Extraction ({doc_type})",
                    file_name=pdf_path.name,
                    status="SUCCESS",
                    details=f"Extracted claims count: {claims_count}, Total value: {total_value}"
                )
            except Exception as db_err:
                print(f"Failed to log watch folder classification to converter.db: {db_err}")

        except Exception as e:
            results.append({
                "file_name": pdf_path.name,
                "status": "failed",
                "error": str(e)
            })

            # Log error to unified converter.db
            try:
                import sys
                from pathlib import Path
                workspace_root = str(Path(__file__).resolve().parent.parent.parent)
                if workspace_root not in sys.path:
                    sys.path.append(workspace_root)
                from database import poc_db
                poc_db.log_universal(
                    module="DRIVE",
                    action="Watch Folder GPU Extraction",
                    file_name=pdf_path.name,
                    status="FAILED",
                    details=str(e)
                )
            except Exception as db_err:
                print(f"Failed to log watch folder failure to converter.db: {db_err}")
            
    return {
        "message": f"Processed {len(pdf_files)} files",
        "processed": len(pdf_files),
        "results": results
    }

@app.get("/{path:path}", response_class=HTMLResponse)
async def serve_frontend(request: Request, path: str = ""):
    """Serve the React frontend for any non-API routes."""
    # This catch-all route should be at the very bottom
    
    # Check if the requested path is a file in the dist folder (e.g., Logo.png)
    file_in_dist = frontend_dist_path / path
    if path and file_in_dist.exists() and file_in_dist.is_file():
        # Determine media type based on extension
        ext = file_in_dist.suffix.lower()
        media_type = "application/octet-stream"
        if ext == ".png": media_type = "image/png"
        elif ext == ".jpg" or ext == ".jpeg": media_type = "image/jpeg"
        elif ext == ".svg": media_type = "image/svg+xml"
        elif ext == ".ico": media_type = "image/x-icon"
        elif ext == ".txt": media_type = "text/plain"
        
        return FileResponse(path=file_in_dist, media_type=media_type)

    index_path = frontend_dist_path / "index.html"
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
            
    return HTMLResponse(content="<h1>Frontend not built</h1><p>Please run <code>npm run build</code> in the frontend directory.</p>", status_code=404)

if __name__ == "__main__":
    import uvicorn
    import sys
    
    # [DYNAMIC] Port Selection
    port = 8007
    if "--port" in sys.argv:
        try:
            port_idx = sys.argv.index("--port")
            if port_idx + 1 < len(sys.argv):
                port = int(sys.argv[port_idx + 1])
        except ValueError:
            print(f"⚠️ Warning: Invalid port specified, falling back to {port}")

    # Diagnostic: Print all registered routes
    print("\n[Diagnostic] Registered Routes:")
    for route in app.routes:
        methods = getattr(route, "methods", "N/A")
        print(f" - {route.path} [{methods}]")
    print("\n" + "="*50)
    print("UNIFIED INTELLIGENT ROUTER STARTING")
    print(f"Access the UI at: http://localhost:{port}")
    print("="*50 + "\n")
    uvicorn.run("unified_app:app", host="0.0.0.0", port=port, reload=False)
