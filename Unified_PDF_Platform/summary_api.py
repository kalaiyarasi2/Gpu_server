import os
import zipfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from collections import defaultdict
from fastapi import APIRouter, File, UploadFile, HTTPException, Request, Query
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, PlainTextResponse
from pydantic import BaseModel, Field

# Import shared resources
from shared_configs import _perform_extraction, file_path_cache

from summary_for_json import UniversalDocumentAnalyzer as ClaimsAnalyzer

# Import documentation constants
from swagger_docs import (
    COGNETHRO_SUMMARY, COGNETHRO_DESCRIPTION, 
    WORK_COMP_SUMMARY, WORK_COMP_DESCRIPTION
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


# ── Response models for /api/claim-analysis ──────────────────────────────────

class ClaimsStatus(BaseModel):
    closed: int = Field(..., description="Number of closed claims")
    open: int = Field(..., description="Number of open claims")
    reopened: int = Field(..., description="Number of reopened claims")
    other: int = Field(..., description="Number of claims with other status")

class ExecutiveSummary(BaseModel):
    total_claims: int = Field(..., description="Total number of claims")
    total_incurred: float = Field(..., description="Sum of total_incurred across all claims")
    total_paid: float = Field(..., description="Sum of all paid amounts (medical + indemnity + expense)")
    medical_paid: float = Field(..., description="Sum of medical_paid across all claims")
    indemnity_paid: float = Field(..., description="Sum of indemnity_paid across all claims")
    expense_paid: float = Field(..., description="Sum of expense_paid across all claims")
    total_reserves: float = Field(..., description="Sum of all reserves (medical + indemnity + expense)")
    medical_reserve: float = Field(..., description="Sum of medical_reserve across all claims")
    indemnity_reserve: float = Field(..., description="Sum of indemnity_reserve across all claims")
    expense_reserve: float = Field(..., description="Sum of expense_reserve across all claims")
    claims_status: ClaimsStatus = Field(..., description="Breakdown of claim statuses")
    litigated_claims: int = Field(..., description="Number of litigated claims")
    reopened_claims: int = Field(..., description="Number of reopened claims")

class ClaimDetail(BaseModel):
    employee_name: Optional[str] = None
    claim_number: Optional[str] = None
    carrier_name: Optional[str] = None
    policy_number: Optional[str] = None
    injury_date: Optional[str] = None
    claim_year: Optional[int] = None
    status: Optional[str] = None
    injury_description: Optional[str] = None
    body_part: Optional[str] = None
    injury_type: Optional[str] = None
    claim_class: Optional[str] = None
    medical_paid: float = 0.0
    medical_reserve: float = 0.0
    indemnity_paid: float = 0.0
    indemnity_reserve: float = 0.0
    expense_paid: float = 0.0
    expense_reserve: float = 0.0
    total_paid: float = 0.0
    total_reserve: float = 0.0
    total_incurred: float = 0.0
    litigation: Optional[str] = None
    reopen: Optional[str] = None

class YearBreakdown(BaseModel):
    year: int
    claim_count: int
    total_incurred: float
    total_paid: float
    total_reserves: float
    open_count: int
    closed_count: int

class CarrierBreakdown(BaseModel):
    carrier_name: str
    claim_count: int
    total_incurred: float
    total_paid: float
    total_reserves: float

class HighValueClaim(BaseModel):
    claim_number: Optional[str] = None
    employee_name: Optional[str] = None
    total_incurred: float
    total_paid: float
    total_reserves: float

class Observation(BaseModel):
    category: str = Field(..., description="Category: 'risk', 'info', 'warning'")
    message: str = Field(..., description="Human-readable observation message")

class ClaimAnalysisResponse(BaseModel):
    success: bool = True
    report_title: str = "Insurance Claims Analysis Report"
    generated_at: str = Field(..., description="ISO timestamp when the report was generated")
    executive_summary: ExecutiveSummary
    claims_overview: List[ClaimDetail] = Field(..., description="Detailed list of all claims")
    year_wise_breakdown: List[YearBreakdown] = Field(default=[], description="Claims aggregated by year")
    carrier_breakdown: List[CarrierBreakdown] = Field(default=[], description="Claims aggregated by carrier")
    top_high_value_claims: List[HighValueClaim] = Field(default=[], description="Top 5 claims by total_incurred")
    litigated_claims_list: List[ClaimDetail] = Field(default=[], description="Claims where litigation = Yes")
    observations: List[Observation] = Field(default=[], description="Auto-generated observations and risk flags")


# ── Endpoint: /api/claim-analysis ────────────────────────────────────────────

@router.post(
    "/api/claim-analysis",
    summary="Structured Claim Analysis Report",
    description=(
        "Generate a **structured JSON** Insurance Claims Analysis Report from extracted claims data.\n\n"
        "Unlike `/api/claim-summary` (which returns AI-generated text), this endpoint returns a "
        "deterministic, structured JSON response with:\n"
        "- **Executive Summary** — totals, status breakdown, paid/reserves\n"
        "- **Claims Overview** — full list of claims with all fields\n"
        "- **Year-wise Breakdown** — aggregated by claim year\n"
        "- **Carrier Breakdown** — aggregated by carrier/insurer\n"
        "- **Top High-Value Claims** — top 5 by total incurred\n"
        "- **Litigated Claims** — filtered list of litigated claims\n"
        "- **Observations** — auto-generated risk flags and insights\n\n"
        "**No LLM/API key required.** All values are computed server-side."
    ),
    tags=["AI Summary"],
    response_model=ClaimAnalysisResponse
)
async def get_claim_analysis(body: ClaimSummaryRequest):
    """
    Generate a structured JSON analysis report for provided claims data.
    """
    from datetime import datetime

    claims = body.claims
    if not claims:
        raise HTTPException(status_code=400, detail="No claims provided")

    # ── Executive Summary ────────────────────────────────────────────────
    status_counts = {"Open": 0, "Closed": 0, "Reopened": 0, "Other": 0}
    total_medical_paid = 0.0
    total_indemnity_paid = 0.0
    total_expense_paid = 0.0
    total_medical_reserve = 0.0
    total_indemnity_reserve = 0.0
    total_expense_reserve = 0.0
    total_incurred = 0.0
    litigated_count = 0
    reopened_count = 0

    # Year & Carrier aggregation
    year_agg = defaultdict(lambda: {
        "claim_count": 0, "total_incurred": 0.0,
        "total_paid": 0.0, "total_reserves": 0.0,
        "open_count": 0, "closed_count": 0
    })
    carrier_agg = defaultdict(lambda: {
        "claim_count": 0, "total_incurred": 0.0,
        "total_paid": 0.0, "total_reserves": 0.0
    })

    claim_details: List[ClaimDetail] = []
    litigated_list: List[ClaimDetail] = []
    high_value_candidates: List[dict] = []

    for c in claims:
        m_paid = float(c.get("medical_paid") or 0)
        i_paid = float(c.get("indemnity_paid") or 0)
        e_paid = float(c.get("expense_paid") or 0)
        m_res = float(c.get("medical_reserve") or 0)
        i_res = float(c.get("indemnity_reserve") or 0)
        e_res = float(c.get("expense_reserve") or 0)
        t_inc = float(c.get("total_incurred") or 0)
        t_paid = m_paid + i_paid + e_paid
        t_res = m_res + i_res + e_res

        total_medical_paid += m_paid
        total_indemnity_paid += i_paid
        total_expense_paid += e_paid
        total_medical_reserve += m_res
        total_indemnity_reserve += i_res
        total_expense_reserve += e_res
        total_incurred += t_inc

        # Status
        status = c.get("status", "Other")
        if status in status_counts:
            status_counts[status] += 1
        else:
            status_counts["Other"] += 1

        # Reopened
        if str(c.get("reopen", "")).lower() == "true":
            reopened_count += 1

        # Litigation
        is_litigated = str(c.get("litigation", "")).lower() == "yes"
        if is_litigated:
            litigated_count += 1

        # Build claim detail
        detail = ClaimDetail(
            employee_name=c.get("employee_name"),
            claim_number=c.get("claim_number"),
            carrier_name=c.get("carrier_name"),
            policy_number=c.get("policy_number"),
            injury_date=c.get("injury_date_time"),
            claim_year=c.get("claim_year"),
            status=status,
            injury_description=c.get("injury_description"),
            body_part=c.get("body_part"),
            injury_type=c.get("injury_type"),
            claim_class=c.get("claim_class"),
            medical_paid=m_paid,
            medical_reserve=m_res,
            indemnity_paid=i_paid,
            indemnity_reserve=i_res,
            expense_paid=e_paid,
            expense_reserve=e_res,
            total_paid=t_paid,
            total_reserve=t_res,
            total_incurred=t_inc,
            litigation=c.get("litigation"),
            reopen=c.get("reopen")
        )
        claim_details.append(detail)

        if is_litigated:
            litigated_list.append(detail)

        high_value_candidates.append({
            "claim_number": c.get("claim_number"),
            "employee_name": c.get("employee_name"),
            "total_incurred": t_inc,
            "total_paid": t_paid,
            "total_reserves": t_res
        })

        # Year aggregation
        year = c.get("claim_year")
        if year:
            ya = year_agg[year]
            ya["claim_count"] += 1
            ya["total_incurred"] += t_inc
            ya["total_paid"] += t_paid
            ya["total_reserves"] += t_res
            if status == "Open":
                ya["open_count"] += 1
            elif status == "Closed":
                ya["closed_count"] += 1

        # Carrier aggregation
        carrier = c.get("carrier_name") or "Unknown"
        ca = carrier_agg[carrier]
        ca["claim_count"] += 1
        ca["total_incurred"] += t_inc
        ca["total_paid"] += t_paid
        ca["total_reserves"] += t_res

    total_paid_all = total_medical_paid + total_indemnity_paid + total_expense_paid
    total_reserves_all = total_medical_reserve + total_indemnity_reserve + total_expense_reserve

    # ── Observations ─────────────────────────────────────────────────────
    observations: List[Observation] = []

    if litigated_count > 0:
        observations.append(Observation(
            category="risk",
            message=f"{litigated_count} claim(s) are in litigation — may require increased reserves."
        ))

    if reopened_count > 0:
        observations.append(Observation(
            category="warning",
            message=f"{reopened_count} claim(s) have been reopened — review for reserve adequacy."
        ))

    if total_reserves_all > total_paid_all and total_paid_all > 0:
        ratio = total_reserves_all / total_paid_all
        observations.append(Observation(
            category="info",
            message=f"Total reserves (${total_reserves_all:,.2f}) exceed total paid (${total_paid_all:,.2f}) by {ratio:.1f}x — indicates open exposure."
        ))

    if status_counts["Open"] > 0:
        observations.append(Observation(
            category="info",
            message=f"{status_counts['Open']} claim(s) are still open."
        ))

    if total_reserves_all == 0 and status_counts["Open"] == 0:
        observations.append(Observation(
            category="info",
            message="All claims are closed with zero outstanding reserves."
        ))

    # High-value threshold: claims > $100k
    high_value_over_100k = [hv for hv in high_value_candidates if hv["total_incurred"] > 100000]
    if high_value_over_100k:
        observations.append(Observation(
            category="risk",
            message=f"{len(high_value_over_100k)} claim(s) exceed $100,000 in total incurred — flag for management review."
        ))

    # ── Build response ───────────────────────────────────────────────────
    # Top 5 high-value claims
    high_value_candidates.sort(key=lambda x: x["total_incurred"], reverse=True)
    top_5 = [HighValueClaim(**hv) for hv in high_value_candidates[:5]]

    # Year breakdown sorted
    year_breakdown = sorted([
        YearBreakdown(year=yr, **data)
        for yr, data in year_agg.items()
    ], key=lambda x: x.year)

    # Carrier breakdown sorted by total_incurred desc
    carrier_list = sorted([
        CarrierBreakdown(carrier_name=name, **data)
        for name, data in carrier_agg.items()
    ], key=lambda x: x.total_incurred, reverse=True)

    return ClaimAnalysisResponse(
        success=True,
        report_title="Insurance Claims Analysis Report",
        generated_at=datetime.now().isoformat(),
        executive_summary=ExecutiveSummary(
            total_claims=len(claims),
            total_incurred=round(total_incurred, 2),
            total_paid=round(total_paid_all, 2),
            medical_paid=round(total_medical_paid, 2),
            indemnity_paid=round(total_indemnity_paid, 2),
            expense_paid=round(total_expense_paid, 2),
            total_reserves=round(total_reserves_all, 2),
            medical_reserve=round(total_medical_reserve, 2),
            indemnity_reserve=round(total_indemnity_reserve, 2),
            expense_reserve=round(total_expense_reserve, 2),
            claims_status=ClaimsStatus(
                closed=status_counts["Closed"],
                open=status_counts["Open"],
                reopened=status_counts["Reopened"],
                other=status_counts["Other"]
            ),
            litigated_claims=litigated_count,
            reopened_claims=reopened_count
        ),
        claims_overview=claim_details,
        year_wise_breakdown=year_breakdown,
        carrier_breakdown=carrier_list,
        top_high_value_claims=top_5,
        litigated_claims_list=litigated_list,
        observations=observations
    )
