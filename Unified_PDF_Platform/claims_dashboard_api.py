"""
Claims Dashboard API
====================
Accepts a Loss Run Excel file, analyzes it with OpenAI using the
Claims Dashboard prompt, and returns a comprehensive Claims Management
Dashboard as a downloadable .txt file.
"""

import os
import sys
import tempfile
import traceback
from typing import Optional
from pathlib import Path

import pandas as pd
from openai import OpenAI
from dotenv import load_dotenv

from fastapi import APIRouter, File, UploadFile, Query, HTTPException
from fastapi.responses import JSONResponse, FileResponse

# ── Environment ──────────────────────────────────────────────────────────────
load_dotenv()
current_dir = Path(__file__).resolve().parent
for parent in current_dir.parents:
    env_path = parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        break

# ── Claims Dashboard System Prompt ──────────────────────────────────────────
CLAIMS_DASHBOARD_PROMPT = r"""You are an expert Workers' Compensation Claims Analytics and Risk Management AI specializing in loss run analysis, portfolio management, underwriting intelligence, claims management, and executive reporting.

Your objective is to analyze workers' compensation loss run data and generate a comprehensive Claims Management Dashboard for executive leadership, risk managers, underwriters, brokers, TPAs, and insurance carriers.

# INPUT

The input may contain:

* Loss Run Excel files
* Loss Run PDFs
* Claims extracts
* Claims databases
* Carrier loss history reports

Common fields may include:

* Claim Number
* Employer Name
* Employer Location
* Location Number
* State
* Class Code
* Class Description
* Date of Injury
* Report Date
* Claim Status
* Claim Type
* Cause of Injury
* Nature of Injury
* Body Part
* Litigation Indicator
* Paid Loss
* Medical Paid
* Indemnity Paid
* Expense Paid
* Outstanding Reserve
* Total Incurred
* Net Incurred
* Return to Work Date

# CLIENT IDENTIFICATION RULE

Use Employer Location as the Client Name.

If Employer Location is blank:

1. Use Employer Name.
2. If both exist, prioritize Employer Location.
3. Aggregate all claims under the same Employer Location.

# REQUIRED OUTPUT

Generate a Management Claims Dashboard containing the following sections.

---

## SECTION 1 – EXECUTIVE SUMMARY

Calculate:

* Total Claims
* Open Claims
* Closed Claims
* Total Incurred
* Total Paid
* Outstanding Reserve
* Litigation Claims
* Litigation %
* Average Claim Cost
* Largest Claim
* Portfolio AI Risk Score

Provide KPI cards.

---

## SECTION 2 – TOP 10 CLAIMS

Display:

Rank
Claim Number
Employer Location
State
Claim Status
Net Incurred
Outstanding Reserve

Sort descending by Net Incurred.

Provide:

* Percentage of total portfolio losses represented by Top 10 claims.
* Largest claim contributor.

---

## SECTION 3 – LOSSES BY STATE

Aggregate:

State
Claim Count
Total Paid
Outstanding Reserve
Total Incurred

Sort descending by Total Incurred.

Provide:

* State concentration %
* Highest risk state
* Top 5 states

Highlight states representing more than 20% of portfolio losses.

---

## SECTION 4 – LOSSES BY CLASS CODE

Aggregate:

Class Code
Class Description
Claim Count
Total Incurred
Average Severity

Sort descending by Total Incurred.

Identify:

* Highest loss class codes
* Highest frequency class codes
* Highest severity class codes

Provide underwriting observations.

---

## SECTION 5 – TOP INJURY CAUSES

Aggregate:

Cause of Injury

Calculate:

* Claim Count
* Total Incurred
* Average Severity

Identify:

* Top 10 causes
* Largest cost drivers
* Emerging trends

Provide safety recommendations.

---

## SECTION 6 – OPEN CLAIM AGING

Open claims only.

Group into:

0-30 Days
31-90 Days
91-180 Days
181-365 Days
Over 365 Days

For each bucket calculate:

* Claim Count
* Total Reserve
* Total Incurred

Identify:

* Stale claims
* High reserve claims
* Claims requiring management review

---

## SECTION 7 – LITIGATION DASHBOARD

Calculate:

* Litigated Claims
* Non-Litigated Claims
* Litigation %
* Litigated Incurred
* Non-Litigated Incurred
* Average Litigated Severity
* Average Non-Litigated Severity

Provide:

* Litigation cost multiplier
* Claims with attorney involvement

Highlight if litigation exceeds 15%.

---

## SECTION 8 – AI RISK SCORE BY CLIENT

Using Employer Location as Client Name.

Create one score per client.

Risk Score Range:

0-100

Where:

100 = Lowest Risk
0 = Highest Risk

Use the following weighted model.

Claim Frequency = 25%

Severity = 25%

Litigation = 20%

Open Reserve Ratio = 15%

State Risk = 10%

Class Code Risk = 5%

---

Claim Frequency Score

Frequency Rate =
Claims / Payroll Exposure

Scoring:

<1 = 25

1-2 = 20

2-3 = 15

3-5 = 10

> 5 = 0

---

Severity Score

Average Incurred Per Claim

<$5,000 = 25

$5k-$10k = 20

$10k-$20k = 15

$20k-$40k = 10

> $40k = 0

---

Litigation Score

Litigated Claims %

<5% = 20

5%-10% = 15

10%-20% = 10

20%-30% = 5

> 30% = 0

---

Reserve Ratio Score

Outstanding Reserve /
Total Incurred

<20% = 15

20%-30% = 12

30%-40% = 9

40%-50% = 6

> 50% = 0

---

State Risk Score

Assign state factors:

TX = 1

FL = 2

GA = 2

NC = 3

PA = 4

NY = 5

IL = 6

CA = 8

Generate weighted average.

Maximum = 10

---

Class Code Risk Score

Clerical = 1

Sales = 2

Retail = 3

Warehousing = 5

Manufacturing = 6

Transportation = 8

Construction = 10

Police/Fire = 10

Maximum = 5

---

Return:

Client Name
Claim Count
Total Incurred
AI Risk Score
Risk Category

Categories:

85-100 = Preferred

70-84 = Standard

55-69 = Moderate

40-54 = Elevated

0-39 = Critical

Rank highest risk clients first.

---

## SECTION 9 – TREND VS PRIOR YEAR

Calculate:

Claims by Year
Incurred by Year
Paid by Year
Reserve by Year

Show:

Year-over-Year Change %

Frequency Trend

Severity Trend

Provide:

* Improving
* Stable
* Deteriorating

assessment.

---

## SECTION 10 – EXECUTIVE INSIGHTS

Generate:

Top 5 Risk Drivers

Top 5 Clients Requiring Review

Top 5 Open Claims

Top 5 Cost Drivers

Top 5 Safety Initiatives

Portfolio Outlook

Reserve Adequacy Assessment

Litigation Assessment

Underwriting Assessment

---

## VISUALIZATION REQUIREMENTS

Generate charts for:

* AI Risk Score by Client
* Losses by State
* Losses by Class Code
* Top Injury Causes
* Claim Trend by Year
* Open Claim Aging

Use executive dashboard formatting suitable for:

* Power BI
* Tableau
* Qlik
* Executive PDF Reports
* Carrier Management Reviews
* MGA Portfolio Reviews

---

## FINAL EXECUTIVE SCORECARD

Display:

Claim Frequency Status

Severity Status

Litigation Status

Reserve Status

State Concentration Status

Class Code Exposure Status

Overall Portfolio Risk Score

Provide final classification:

Preferred
Standard
Moderate
Elevated
High Risk
Critical

Conclude with a management recommendation and underwriting action plan.
"""


# ── Analyzer Class ───────────────────────────────────────────────────────────

class ClaimsDashboardAnalyzer:
    """
    Reads a Loss Run Excel file, converts it to text, and sends it to
    OpenAI with the Claims Dashboard prompt to generate a comprehensive
    Claims Management Dashboard report.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OpenAI API key not found. Set OPENAI_API_KEY in your "
                "environment or pass it directly."
            )
        self.client = OpenAI(api_key=self.api_key)

    # ── Excel → text ─────────────────────────────────────────────────────
    @staticmethod
    def excel_to_text(file_path: str) -> str:
        """
        Read every sheet in the Excel workbook and convert to a
        pipe-delimited text representation that preserves headers and rows.
        """
        xls = pd.ExcelFile(file_path, engine="openpyxl")
        parts: list[str] = []

        for sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name)

            # Drop completely empty rows / columns
            df = df.dropna(how="all").dropna(axis=1, how="all")

            if df.empty:
                continue

            parts.append(f"=== Sheet: {sheet_name} ===")
            parts.append(
                df.to_csv(sep="|", index=False, na_rep="")
            )
            parts.append("")  # blank line between sheets

        return "\n".join(parts)

    # ── Generate dashboard ───────────────────────────────────────────────
    def generate_dashboard(
        self,
        excel_text: str,
        model: str = "gpt-4o",
        temperature: float = 0.2,
    ) -> str:
        """
        Send the textualised Excel data to OpenAI and return the generated
        Claims Management Dashboard report.
        """
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": CLAIMS_DASHBOARD_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "Analyze the following Loss Run data and generate "
                            "the full Claims Management Dashboard as specified "
                            "in your instructions.\n\n"
                            f"{excel_text}"
                        ),
                    },
                ],
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error generating Claims Dashboard: {str(e)}"


# ── FastAPI Router ───────────────────────────────────────────────────────────

router = APIRouter()


@router.post(
    "/api/claims-dashboard",
    summary="Generate Claims Management Dashboard from Excel",
    description=(
        "Upload a Loss Run Excel file (`.xlsx` / `.xls`) and receive a "
        "comprehensive AI-generated Claims Management Dashboard.\n\n"
        "**Download as `.txt`:** The endpoint returns a downloadable text "
        "file by default.  Set `download=false` to get a JSON response instead."
    ),
    tags=["Claims Dashboard"],
)
async def generate_claims_dashboard(
    file: UploadFile = File(..., description="Loss Run Excel file (.xlsx or .xls)"),
    download: bool = Query(
        True, description="Return a downloadable .txt file (true) or JSON (false)"
    ),
    model: str = Query("gpt-4o", description="OpenAI model to use"),
    temperature: float = Query(0.2, description="Sampling temperature (0.0–1.0)"),
):
    # ── Validate file type ───────────────────────────────────────────────
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    ext = Path(filename).suffix.lower()
    if ext not in (".xlsx", ".xls"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Only .xlsx and .xls files are accepted.",
        )

    try:
        # ── Save upload to a temp file ───────────────────────────────────
        tmp_input = tempfile.NamedTemporaryFile(
            delete=False, suffix=ext, prefix="lossrun_"
        )
        tmp_input.write(await file.read())
        tmp_input.close()

        print(f"[Claims Dashboard] Saved upload to {tmp_input.name}")

        # ── Convert Excel → text ─────────────────────────────────────────
        analyzer = ClaimsDashboardAnalyzer()
        excel_text = analyzer.excel_to_text(tmp_input.name)

        if not excel_text.strip():
            raise HTTPException(
                status_code=400,
                detail="The uploaded Excel file appears to be empty or has no readable data.",
            )

        print(
            f"[Claims Dashboard] Converted Excel to text "
            f"({len(excel_text)} chars). Sending to {model}..."
        )

        # ── Generate dashboard via AI ────────────────────────────────────
        dashboard_text = analyzer.generate_dashboard(
            excel_text=excel_text,
            model=model,
            temperature=temperature,
        )

        print(f"[Claims Dashboard] Dashboard generated ({len(dashboard_text)} chars).")

        # ── Return result ────────────────────────────────────────────────
        if download:
            tmp_output = tempfile.NamedTemporaryFile(
                mode="w",
                suffix="_claims_dashboard.txt",
                delete=False,
                encoding="utf-8",
            )
            tmp_output.write(dashboard_text)
            tmp_output.flush()
            tmp_output.close()

            return FileResponse(
                path=tmp_output.name,
                filename="claims_management_dashboard.txt",
                media_type="text/plain",
            )

        return JSONResponse({"success": True, "dashboard": dashboard_text})

    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[Claims Dashboard] ERROR: {e}\n{tb}")
        return JSONResponse(
            {"success": False, "error": str(e)},
            status_code=500,
        )
    finally:
        # Clean up the uploaded temp file
        try:
            os.unlink(tmp_input.name)
        except Exception:
            pass


# ── Standalone Test Server ───────────────────────────────────────────────────
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn

    app = FastAPI(
        title="Claims Dashboard API (Standalone Test)",
        description="Upload a Loss Run Excel file and get a Claims Management Dashboard.",
        version="1.0.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    print("\n" + "=" * 55)
    print("  CLAIMS DASHBOARD — STANDALONE TEST SERVER")
    print("  Swagger UI : http://localhost:8009/docs")
    print("  Endpoint   : POST http://localhost:8009/api/claims-dashboard")
    print("=" * 55 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=8009)
