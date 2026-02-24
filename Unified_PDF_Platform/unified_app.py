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


# Import shared resources
from shared_configs import router_engine, file_path_cache, _perform_extraction, BASE_DIR, UPLOAD_DIR
# Import summary functions
from summary_api import get_claim_summary, cognethro_trigger, cognethro_trigger_docs

# Import documentation constants
from swagger_docs import (
    API_TITLE, API_DESCRIPTION, API_VERSION, 
    CUSTOM_SWAGGER_JS, COGNETHRO_SUMMARY
)

# Load environment variables from parent directory
load_dotenv(BASE_DIR.parent / ".env")

app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    docs_url=None,  # Override for custom download buttons logic
    redoc_url="/redoc"
)

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
# Explicitly map summary_api routes to avoid 405 mapping issues
app.get("/cognethro", include_in_schema=False)(cognethro_trigger_docs)
app.post("/cognethro", 
    summary=COGNETHRO_SUMMARY,
    include_in_schema=True)(cognethro_trigger)
app.post("/api/claim-summary", 
    summary="Generate AI Summary")(get_claim_summary)

# Mount static and templates for the new React frontend
frontend_dist_path = BASE_DIR / "frontend" / "dist"
if frontend_dist_path.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist_path / "assets")), name="assets")
    print(f"[OK] Mounted frontend assets from {frontend_dist_path / 'assets'}")
else:
    print(f"⚠️ Warning: Frontend dist folder not found at {frontend_dist_path}. Run build first.")



# _perform_extraction is now imported from shared_configs

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
    custom_js = CUSTOM_SWAGGER_JS
    
    html_content = response.body.decode("utf-8")
    new_html = html_content.replace("</body>", f"{custom_js}</body>")
    return HTMLResponse(content=new_html, status_code=response.status_code)

# Injecting the custom Script via a separate HTML header middleware if needed, 
# or just keeping it simple for now to get it working.


@app.get("/api/download/{filepath:path}", include_in_schema=False)
async def download_file(filepath: str):
    """Download endpoint that handles both absolute and relative paths."""
    print(f"[Download] Requested file: {filepath}")
    
    # First, check the cache for the full path
    if filepath in file_path_cache:
        file_path = Path(file_path_cache[filepath])
    else:
        # Safety: If the path contains a URL (e.g. from a bad frontend call), strip it
        if "://" in filepath:
            filepath = filepath.split("/")[-1]
            print(f"[Download] Stripped URL from path, now: {filepath}")
            
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
