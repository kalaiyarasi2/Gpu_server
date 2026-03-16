import os
import zipfile
import tempfile
from pathlib import Path
from fastapi import APIRouter, File, UploadFile, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse

# Import shared resources
from shared_configs import _perform_extraction, file_path_cache

from summary_for_json import UniversalDocumentAnalyzer as ClaimsAnalyzer

# Import documentation constants
from swagger_docs import (
    COGNETHRO_SUMMARY, COGNETHRO_DESCRIPTION, 
    WORK_COMP_SUMMARY, WORK_COMP_DESCRIPTION,
    BANK_STATEMENT_SUMMARY, BANK_STATEMENT_DESCRIPTION
)

router = APIRouter()

@router.get("/cognethro", include_in_schema=False)
async def cognethro_trigger_docs():
    """Redirect human visitors from the trigger point to the 'Real' standard Swagger documentation."""
    return RedirectResponse(url="/docs")

@router.post("/cognethro",
    summary=COGNETHRO_SUMMARY,
    description=COGNETHRO_DESCRIPTION)
async def cognethro_trigger(request: Request, file: UploadFile = File(...), download: bool = False):
    result = _perform_extraction(file, request)
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


@router.get("/work-comp", include_in_schema=False)
async def work_comp_trigger_docs():
    """Redirect human visitors to the Swagger documentation page."""
    return RedirectResponse(url="/docs")


@router.post("/work-comp",
    summary=WORK_COMP_SUMMARY,
    description=WORK_COMP_DESCRIPTION,
    tags=["Workers Compensation"])
async def work_comp_trigger(request: Request, file: UploadFile = File(...)):
    """
    Upload a Workers Compensation PDF and receive a direct JSON file download.
    Only PDF files are accepted.
    """
    from pathlib import Path as _Path
    from fastapi import HTTPException as _HTTPException
    from fastapi.responses import FileResponse as _FileResponse

    file_ext = _Path(file.filename).suffix.lower()
    if file_ext != ".pdf":
        raise _HTTPException(
            status_code=400,
            detail=f"Workers Compensation endpoint only accepts PDF files. Received: {file_ext}"
        )

    result = _perform_extraction(file, request)

    if isinstance(result, dict) and "error" not in result:
        json_filename = result.get("output_json")
        json_full_path = file_path_cache.get(json_filename)

        if json_full_path and _Path(json_full_path).exists():
            print(f"[Work Comp] Returning direct file download: {json_filename}")
            return _FileResponse(
                path=json_full_path,
                filename=json_filename,
                media_type="application/json"
            )

    # Fallback: return error dict if extraction failed
    return result


@router.get("/bank-statement", include_in_schema=False)
async def bank_statement_trigger_docs():
    """Redirect human visitors from the trigger point to the 'Real' standard Swagger documentation."""
    return RedirectResponse(url="/docs")


@router.post("/bank-statement",
    summary=BANK_STATEMENT_SUMMARY,
    description=BANK_STATEMENT_DESCRIPTION,
    tags=["Bank Statements"])
async def bank_statement_trigger(request: Request, file: UploadFile = File(...)):
    """
    Upload a Bank Statement PDF and receive structured JSON and Excel links.
    """
    from pathlib import Path as _Path
    from fastapi import HTTPException as _HTTPException

    file_ext = _Path(file.filename).suffix.lower()
    if file_ext != ".pdf":
        raise _HTTPException(
            status_code=400,
            detail=f"Bank Statement endpoint only accepts PDF files. Received: {file_ext}"
        )

    result = _perform_extraction(file, request)
    result["trigger_point"] = "bank-statement"
    
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

@router.post("/api/merge-json")
async def merge_json_endpoint(request: Request):
    """
    Merge multiple JSON files by their filenames (cached paths).
    Expected JSON: { "filenames": ["file1.json", "file2.json"] }
    """
    try:
        data = await request.json()
        filenames = data.get("filenames", [])
        
        if not filenames:
            return JSONResponse({"error": "No filenames provided"}, status_code=400)
        
        # Resolve full paths from cache
        full_paths = []
        for fn in filenames:
            path = file_path_cache.get(fn)
            if path:
                full_paths.append(path)
            else:
                # Fallback: check if it's already an absolute path
                if os.path.isabs(fn) and os.path.exists(fn):
                    full_paths.append(fn)
                else:
                    print(f"[Merge-API][WARN] Filename not found in cache: {fn}")

        if not full_paths:
            return JSONResponse({"error": "None of the provided files could be resolved"}, status_code=404)

        from merge_logic import DocumentMerger
        merger = DocumentMerger()
        merged_list = merger.merge_json_files(full_paths)
        
        return {
            "success": True,
            "count": len(merged_list),
            "merged_data": merged_list
        }

    except Exception as e:
        print(f"❌ Error merging JSON: {e}")
        return JSONResponse({"error": str(e), "success": False}, status_code=500)
