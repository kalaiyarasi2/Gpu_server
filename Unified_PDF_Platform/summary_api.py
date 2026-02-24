import os
import zipfile
import tempfile
from pathlib import Path
from fastapi import APIRouter, File, UploadFile, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse

# Import shared resources
from shared_configs import _perform_extraction, file_path_cache

from summary_for_json import ClaimsAnalyzer

# Import documentation constants
from swagger_docs import COGNETHRO_SUMMARY, COGNETHRO_DESCRIPTION

router = APIRouter()

@router.get("/cognethro", include_in_schema=False)
async def cognethro_trigger_docs():
    """Redirect human visitors from the trigger point to the 'Real' standard Swagger documentation."""
    return RedirectResponse(url="/docs")

@router.post("/cognethro",
    summary=COGNETHRO_SUMMARY,
    description=COGNETHRO_DESCRIPTION)
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

@router.post("/api/claim-summary")
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
