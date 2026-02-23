import os
import shutil
import logging
import zipfile
import tempfile
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

# Import ClaimsAnalyzer for summary feature
import sys
sys.path.append(str(Path(__file__).parent.parent / "Insurance_pdf_extractor-main" / "backend"))
from summary_for_json import ClaimsAnalyzer

# Import our router logic
from unified_router import UnifiedRouter

# Setup directories
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Load environment variables from parent directory
load_dotenv(BASE_DIR.parent / ".env")

app = FastAPI(
    title="Cognethro",
    description="Unified API for Insurance Document Extraction",
    version="1.0.0",
    docs_url=None,  # Override for custom download buttons logic
    redoc_url="/redoc"
)
router_engine = UnifiedRouter()

# File path cache: maps filename -> full absolute path
file_path_cache = {}

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# BASE_DIR and UPLOAD_DIR are now defined at the top

# Mount static and templates for the new React frontend
frontend_dist_path = BASE_DIR / "frontend" / "dist"
if frontend_dist_path.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist_path / "assets")), name="assets")
    print(f"[OK] Mounted frontend assets from {frontend_dist_path / 'assets'}")
else:
    print(f"⚠️ Warning: Frontend dist folder not found at {frontend_dist_path}. Run build first.")


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
            raise HTTPException(status_code=500, detail=result["error"])
        
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

@app.post("/api/extract", include_in_schema=False)
async def extract_document(request: Request, file: UploadFile = File(...)):
    return await _perform_extraction(file, request)

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    response = get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="Cognethro - Standard Swagger",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
    )
    
    # Manually inject our custom JS for download buttons
    custom_js = """
    <script>
    window.addEventListener('load', function() {
        const observer = new MutationObserver(() => {
            const results = document.querySelectorAll('.response .microlight');
            results.forEach((node) => {
                const text = node.textContent;
                if (text.includes('"excel": "http') && !node.parentElement.querySelector('.cognethro-dl-btns')) {
                    try {
                        const data = JSON.parse(text);
                        const btnContainer = document.createElement('div');
                        btnContainer.className = 'cognethro-dl-btns';
                        btnContainer.style = 'margin-top: 15px; display: flex; gap: 10px; padding: 10px; background: #222; border-radius: 4px; border: 1px solid #444;';
                        
                        if (data.excel) {
                            const exBtn = document.createElement('a');
                            exBtn.href = data.excel;
                            exBtn.textContent = '📊 Download Excel';
                            exBtn.style = 'background: #052e16; color: #4ade80; border: 1px solid #166534; padding: 10px 16px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 13px;';
                            exBtn.download = data.output_file || 'result.xlsx';
                            btnContainer.appendChild(exBtn);
                        }
                        
                        if (data.json) {
                            const jsBtn = document.createElement('a');
                            jsBtn.href = data.json;
                            jsBtn.textContent = '{ } Download JSON';
                            jsBtn.style = 'background: #0c1a33; color: #93c5fd; border: 1px solid #1e40af; padding: 10px 16px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 13px;';
                            jsBtn.download = data.output_json || 'result.json';
                            btnContainer.appendChild(jsBtn);
                        }
                        
                        node.parentElement.appendChild(btnContainer);
                    } catch (e) {}
                }
            });
        });
        observer.observe(document.body, { childList: true, subtree: true });
    });
    </script>
    """
    
    html_content = response.body.decode("utf-8")
    new_html = html_content.replace("</body>", f"{custom_js}</body>")
    return HTMLResponse(content=new_html, status_code=response.status_code)

# Injecting the custom Script via a separate HTML header middleware if needed, 
# or just keeping it simple for now to get it working.

@app.get("/cognethro", include_in_schema=False)
async def cognethro_trigger_docs():
    """Redirect human visitors from the trigger point to the 'Real' standard Swagger documentation."""
    return RedirectResponse(url="/docs")

@app.post("/cognethro",
    summary="Cognethro Trigger Point — Extract Document",
    description="""
The **Cognethro Trigger Point**.

- **Browser**: Visit `GET /cognethro` to open the interactive Swagger UI.
- **API/curl**: `POST /cognethro` with a `file` field to extract and get download URLs.
- **Direct Download**: Add `download=true` to your POST request to get a ZIP file containing both Excel and JSON directly as a single download.
""")
async def cognethro_trigger(request: Request, file: UploadFile = File(...), download: bool = False):
    result = await _perform_extraction(file, request)
    if isinstance(result, dict):
        result["trigger_point"] = "cognethro"
        
        # If direct download is requested, create a ZIP and return it
        if download and "error" not in result:
            excel_filename = result.get("output_file")
            json_filename = result.get("output_json")
            
            excel_path = file_path_cache.get(excel_filename)
            json_path = file_path_cache.get(json_filename)
            
            if excel_path and json_path:
                zip_filename = f"{Path(file.filename).stem}_extracted.zip"
                zip_path = Path(tempfile.gettempdir()) / zip_filename
                
                with zipfile.ZipFile(zip_path, 'w') as zipf:
                    zipf.write(excel_path, excel_filename)
                    zipf.write(json_path, json_filename)
                
                print(f"[Unified][API] Returning ZIP download for {file.filename}")
                return FileResponse(
                    path=zip_path,
                    filename=zip_filename,
                    media_type="application/zip"
                )
    
    return result

@app.get("/api/download/{filepath:path}", include_in_schema=False)
async def download_file(filepath: str):
    """Download endpoint that handles both absolute and relative paths."""
    print(f"[Download] Requested file: {filepath}")
    
    # First, check the cache for the full path
    if filepath in file_path_cache:
        file_path = Path(file_path_cache[filepath])
        print(f"[Download] Found in cache: {file_path}")
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
    file_path = Path(filepath)
    
    if not file_path.exists():
        # Try as just the filename in unified_outputs
        file_path = BASE_DIR / "unified_outputs" / filepath
        
    if not file_path.exists():
        # Try relative to BASE_DIR
        file_path = BASE_DIR / filepath
    
    # Try searching in the insurance outputs directory
    if not file_path.exists() and filepath.endswith('.json'):
        insurance_outputs = Path("c:/Main_project/Insurance_pdf_extractor-main/backend/outputs")
        for session_dir in insurance_outputs.glob("extraction_*"):
            potential_file = session_dir / filepath
            if potential_file.exists():
                file_path = potential_file
                break
    
    # Try searching in unified_outputs for any matching filename
    if not file_path.exists():
        unified_out = BASE_DIR / "unified_outputs"
        if unified_out.exists():
            for potential_file in unified_out.glob(f"**/{filepath}"):
                file_path = potential_file
                break
        
    if not file_path.exists():
        print(f"[Download] File not found: {filepath}")
        print(f"[Download] Cache contents: {list(file_path_cache.keys())}")
        raise HTTPException(status_code=404, detail=f"File not found: {filepath}")
    
    filename = file_path.name
    if filename.endswith(".xlsx"):
        media_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif filename.endswith(".json"):
        media_type = 'application/json'
    else:
        media_type = 'application/octet-stream'
        
    return FileResponse(path=file_path, filename=filename, media_type=media_type)

@app.post("/api/claim-summary")
async def get_claim_summary(request: Request):
    """
    Generate an AI summary for provided data (Claims or Invoices)
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON payload"}, status_code=400)

    if not data or 'claims' not in data:
        # Check if it's a list directly
        if isinstance(data, list):
            claims_data = {'claims': data}
        else:
            return JSONResponse({'error': 'No data provided (expected "claims" field)'}, status_code=400)
    else:
        claims_data = data

    try:
        # Initialize analyzer with API key from environment
        analyzer = ClaimsAnalyzer(api_key=os.getenv("OPENAI_API_KEY"))
        summary = analyzer.generate_claim_summary(claims_data)

        return {
            'success': True,
            'summary': summary
        }

    except Exception as e:
        print(f"❌ Error generating summary: {e}")
        return JSONResponse({
            'error': str(e),
            'success': False
        }, status_code=500)

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
    # Diagnostic: Print all registered routes
    print("\n[Diagnostic] Registered Routes:")
    for route in app.routes:
        methods = getattr(route, "methods", "N/A")
        print(f" - {route.path} [{methods}]")
    print("\n" + "="*50)
    print("UNIFIED INTELLIGENT ROUTER STARTING")
    print("Access the UI at: http://localhost:8007")
    print("="*50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8007)
