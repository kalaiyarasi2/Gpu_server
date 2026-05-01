import os
import zipfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, File, UploadFile, HTTPException, Request, Query
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, PlainTextResponse
from pydantic import BaseModel, Field

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

# ── Request model for /api/claim-summary ─────────────────────────────────────
class ClaimSummaryRequest(BaseModel):
    """
    Pass your extracted claims array here to generate an AI Insurance Claims
    Analysis Report — identical to the 'AI Summary' button in the UI.
    """
    claims: List[Dict[str, Any]] = Field(
        ...,
        description="Array of claim objects from the extracted JSON (the 'claims' array).",
        example=[
            {
                "employee_name": "Gordon, Tina",
                "carrier_name": "Redwood Fire and Casualty Insurance Company",
                "policy_number": "STWC710881",
                "claim_number": "44107873",
                "injury_date_time": "2025-08-06",
                "claim_year": 2025,
                "status": "Open",
                "reopen": "False",
                "injury_description": "Glass bowl broke and cut foot.",
                "body_part": "Foot right",
                "injury_type": "Indemnity",
                "claim_class": "8810",
                "medical_paid": 27061.43,
                "medical_reserve": 50553.27,
                "indemnity_paid": 7972.84,
                "indemnity_reserve": 22485.31,
                "expense_paid": 7066.18,
                "expense_reserve": 17539.11,
                "total_paid": 42100.45,
                "total_reserve": 90577.69,
                "total_incurred": 132678.14,
                "litigation": "No"
            }
        ]
    )
# ─────────────────────────────────────────────────────────────────────────────

# ── Request model for /api/merge-json ────────────────────────────────────────
class MergeJsonRequest(BaseModel):
    """
    Provide the list of JSON filenames (from a previous extraction) to merge.
    Use the exact filename returned in the extraction response (e.g. 'extracted_schema.json').
    """
    filenames: List[str] = Field(
        ...,
        description="List of extracted JSON filenames to merge together.",
        example=["extracted_schema.json", "extracted_schema.json"]
    )
# ─────────────────────────────────────────────────────────────────────────────

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

    result = await _perform_extraction(file, request)
    return JSONResponse(content=result)


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

@router.post(
    "/api/claim-summary",
    summary="Get Claim Summary",
    description=(
        "Generate an AI Insurance Claims Analysis Report from extracted claims data.\n\n"
        "**How to use:**\n"
        "1. Paste your `claims` array (from the extracted JSON output) into the request body below.\n"
        "2. Click **Execute**.\n"
        "3. The response contains a `summary` field with the full report text.\n\n"
        "**Want a downloadable .txt file?** Add `?download=true` to the URL."
    ),
    tags=["AI Summary"]
)
async def get_claim_summary(
    body: ClaimSummaryRequest,
    download: bool = Query(False, description="Set to true to download the summary as a .txt file")
):
    """
    Generate an AI summary for provided claims data.
    """
    claims_data = {"claims": body.claims}

    try:
        analyzer = ClaimsAnalyzer(api_key=os.getenv("OPENAI_API_KEY"))
        summary = analyzer.generate_claim_summary(claims_data)

        if download:
            # Write to a temp file and return as download
            tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='_claims_summary.txt',
                delete=False, encoding='utf-8'
            )
            tmp.write(summary)
            tmp.flush()
            tmp.close()
            return FileResponse(
                path=tmp.name,
                filename="claims_analysis_report.txt",
                media_type="text/plain"
            )

        return JSONResponse({
            'success': True,
            'summary': summary
        })

    except Exception as e:
        print(f"❌ Error generating summary: {e}")
        return JSONResponse({
            'error': str(e),
            'success': False
        }, status_code=500)

@router.post(
    "/api/merge-json",
    summary="Merge JSON Endpoint",
    description=(
        "Merge multiple extracted JSON files into a single combined dataset.\n\n"
        "**How to use:**\n"
        "1. Run an extraction on two or more PDFs first.\n"
        "2. Copy the `output_json` filename from each extraction response "
        "(e.g. `extracted_schema.json`).\n"
        "3. Paste those filenames into the `filenames` array below.\n"
        "4. Click **Execute** to get merged results."
    ),
    tags=["AI Summary"]
)
async def merge_json_endpoint(body: MergeJsonRequest):
    """
    Merge multiple JSON files by their filenames (cached paths).
    """
    filenames = body.filenames

    if not filenames:
        return JSONResponse({"error": "No filenames provided"}, status_code=400)

    try:
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
            return JSONResponse(
                {"error": "None of the provided files could be resolved from cache. "
                          "Make sure you extracted those PDFs in the current server session."},
                status_code=404
            )

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
