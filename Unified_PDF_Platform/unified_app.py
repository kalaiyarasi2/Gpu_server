import os
import shutil
import logging
from typing import List, Dict
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
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

app = FastAPI(title="Insurance Form Extractor")
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
    print(f"✓ Mounted frontend assets from {frontend_dist_path / 'assets'}")
else:
    print(f"⚠️ Warning: Frontend dist folder not found at {frontend_dist_path}. Run build first.")


@app.post("/api/extract")
async def extract_document(file: UploadFile = File(...)):
    print(f"\n[Unified][API] Received request for: {file.filename}")
    
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
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
        
        # Build base response
        response = {
            "type": doc_type,
            "output_file": excel_filename,
            "output_json": json_filename,
            "excel": excel_path,
            "json": json_path
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

@app.get("/api/download/{filepath:path}")
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
    print("\n" + "="*50)
    print("UNIFIED INTELLIGENT ROUTER STARTING")
    print("Access the UI at: http://localhost:8007")
    print("="*50 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8007)
