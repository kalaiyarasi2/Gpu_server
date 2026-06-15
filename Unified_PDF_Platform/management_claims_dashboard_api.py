"""
Management Claims Dashboard API
===============================
Accepts a Loss Run Excel file, analyzes it with OpenAI using the
refined Management Claims Dashboard prompt, and returns a comprehensive
report as a downloadable .txt file or JSON.
"""

import os
import sys
import json
import tempfile
import traceback
from typing import Optional, Dict, Any, Tuple
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

# ── Management Claims Dashboard System Prompt ────────────────────────────────
MANAGEMENT_DASHBOARD_PROMPT = r"""You are an expert Workers' Compensation Claims Analytics and Risk Management AI specializing in loss run analysis, portfolio management, underwriting intelligence, claims management, and executive reporting.

Your objective is to analyze workers' compensation loss run data and generate a comprehensive Management Claims Dashboard for executive leadership, risk managers, underwriters, brokers, TPAs, and insurance carriers.

# INPUT

The input contains pre-calculated exact metrics in JSON format, followed by the raw Loss Run data (converted to pipe-delimited text).

**CRITICAL RULE:** You MUST use the exact numbers provided in the "PRE-CALCULATED METRICS (JSON)" section for your numerical outputs. Do NOT perform any arithmetic or summation on the raw text data yourself. The raw text data is provided ONLY for you to write the qualitative "Observations", "Safety Recommendations", "Executive Insights", and the "Scorecard Status".

# CLIENT IDENTIFICATION RULE

**Use Employer Location as the Client Name.** If Employer Location is blank, use Employer Name. Aggregate all claims under the same Client Name.

# REQUIRED OUTPUT STRUCTURE

Generate a Management Claims Dashboard containing the following sections EXACTLY as structured below. 
**CRITICAL:** Use `**` for all labels and key data points as shown. Do not add extra spaces between bullet points if not shown in the structure. Do not miss any sections.

---

## SECTION 1 – EXECUTIVE SUMMARY

### Calculations:
- **Total Claims:** [Use value from JSON: Executive Summary -> Total Claims]
- **Open Claims:** [Use value from JSON: Executive Summary -> Open Claims]
- **Closed Claims:** [Use value from JSON: Executive Summary -> Closed Claims]
- **Total Incurred:** $[Use value from JSON: Executive Summary -> Total Incurred]
- **Total Paid:** $[Use value from JSON: Executive Summary -> Total Paid]
- **Outstanding Reserve:** $[Use value from JSON: Executive Summary -> Outstanding Reserve]
- **Litigation Claims:** [Use value from JSON: Executive Summary -> Litigation Claims]
- **Litigation %:** [Use value from JSON: Executive Summary -> Litigation %]
- **Average Claim Cost:** $[Use value from JSON: Executive Summary -> Average Claim Cost]
- **Largest Claim:** $[Use value from JSON: Executive Summary -> Largest Claim]
- **Portfolio AI Risk Score:** [Use value from JSON: Executive Summary -> Portfolio AI Risk Score]
- **Overall Portfolio Risk Classification:** [Use value from JSON: Executive Summary -> Overall Portfolio Risk Classification]
- **Management Recommendation:** [Qualitative Insight]

---

## SECTION 2 – TOP 10 CLAIMS

(List EXACTLY the 10 claims provided in the JSON: Top 10 Claims)
1. **Claim Number:** [Value]
   - **Employer Location:** [Value]
   - **State:** [Value]
   - **Claim Status:** [Value]
   - **Net Incurred:** $[Value]
   - **Outstanding Reserve:** $[Value]

### Observations:
- **Percentage of Total Portfolio Losses Represented by Top 10 Claims:** [Calculate/Estimate based on JSON values]%
- **Largest Claim Contributor:** [Value] with $[Value]

---

## SECTION 3 – LOSSES BY STATE

(List EXACTLY the top states provided in the JSON: Losses by State)
1. **State:** [Value]
   - **Claim Count:** [Value]
   - **Total Paid:** $[Value]
   - **Outstanding Reserve:** $[Value]
   - **Total Incurred:** $[Value]

### Observations:
- **State Concentration %:** [Value] represents [Value]% of total portfolio losses.
- **Highest Risk State:** [Value]
- **Top 5 States:** [Value]
- **States Over 20% Portfolio Losses:** [Value]
- **State Concentration Alert:** [Value]

---

## SECTION 4 – LOSSES BY CLASS CODE

(List EXACTLY the top class codes provided in the JSON: Losses by Class Code)
1. **Class Code:** [Value]
   - **Class Description:** [Value]
   - **Claim Count:** [Value]
   - **Total Incurred:** $[Value]
   - **Average Severity:** $[Value]

### Observations:
- **Highest Loss Class Codes:** [Value]
- **Highest Frequency Class Codes:** [Value]
- **Highest Severity Class Codes:** [Value]

### Underwriting Observations:
- [Observation 1]
- [Observation 2]

---

## SECTION 5 – TOP INJURY CAUSES

(List EXACTLY the top injury causes provided in the JSON: Top Injury Causes)
1. **Cause of Injury:** [Value]
   - **Claim Count:** [Value]
   - **Total Incurred:** $[Value]
   - **Average Severity:** $[Value]

### Observations:
- **Top 10 Causes:** [Value]
- **Largest Cost Drivers:** [Value]
- **Emerging Trends:** [Value]

### Safety Recommendations:
- [Recommendation 1]
- [Recommendation 2]
- [Recommendation 3]

---

## SECTION 6 – OPEN CLAIM AGING

(List EXACTLY the aging buckets provided in the JSON: Open Claim Aging)
- **0-30 Days:**
  - **Claim Count:** [Value]
  - **Total Reserve:** $[Value]
  - **Total Incurred:** $[Value]

- **31-90 Days:**
  - **Claim Count:** [Value]
  - **Total Reserve:** $[Value]
  - **Total Incurred:** $[Value]

- **91-180 Days:**
  - **Claim Count:** [Value]
  - **Total Reserve:** $[Value]
  - **Total Incurred:** $[Value]

- **181-365 Days:**
  - **Claim Count:** [Value]
  - **Total Reserve:** $[Value]
  - **Total Incurred:** $[Value]

- **Over 365 Days:**
  - **Claim Count:** [Value]
  - **Total Reserve:** $[Value]
  - **Total Incurred:** $[Value]

### Observations:
- **Stale Claims:** [Value]
- **High Reserve Claims:** [Value]
- **Claims Requiring Management Review:** [Value]

---

## SECTION 7 – LITIGATION DASHBOARD

- **Litigated Claims:** [Use value from JSON: Executive Summary -> Litigation Claims]
- **Non-Litigated Claims:** [Calculate based on JSON]
- **Litigation %:** [Use value from JSON: Executive Summary -> Litigation %]
- **Litigated Incurred:** $[Estimate based on JSON]
- **Non-Litigated Incurred:** $[Estimate based on JSON]
- **Average Litigated Severity:** $[Estimate based on JSON]
- **Average Non-Litigated Severity:** $[Estimate based on JSON]

### Observations:
- **Litigation Cost Multiplier:** [Value]x
- **Claims with Attorney Involvement:** [Value]

### Highlight:
- [Value]

---

## SECTION 8 – AI RISK SCORE BY CLIENT

(List EXACTLY the clients provided in the JSON: AI Risk Score by Client)
### AI Risk Score by Client:
1. **Client Name:** [Value]
   - **Claim Count:** [Value]
   - **Total Incurred:** $[Value]
   - **AI Risk Score:** [Value]
   - **Risk Category:** [Value]

### AI Risk Score Model:
- **Risk Score Range:** 0-100
- **100:** Lowest Risk
- **0:** Highest Risk

### Weighted Scoring Components:
- **Claim Frequency:** 25%
- **Severity:** 25%
- **Litigation:** 20%
- **Open Reserve Ratio:** 15%
- **State Risk:** 10%
- **Class Code Risk:** 5%

### Scoring Formulas & Tables:
- **Claim Frequency Score:** (Claims / Payroll Exposure) <1: 25, 1-2: 20, 2-3: 15, 3-5: 10, >5: 0
- **Severity Score:** (Avg Incurred) <$5k: 25, $5k-$10k: 20, $10k-$20k: 15, $20k-$40k: 10, >$40k: 0
- **Litigation Score:** (Litigation %) <5%: 20, 5-10%: 15, 10-20%: 10, 20-30%: 5, >30%: 0
- **Reserve Ratio Score:** (Outstanding Reserve / Total Incurred) <20%: 15, 20-30%: 12, 30-40%: 9, 40-50%: 6, >50%: 0
- **State Risk Score:** Factors (TX: 1, FL: 2, GA: 2, NC: 3, PA: 4, NY: 5, IL: 6, CA: 8) - Weighted Factor out of 10
- **Class Code Risk Score:** Factors (Clerical: 1, Sales: 2, Retail: 3, Warehousing: 5, Manufacturing: 6, Transportation: 8, Construction: 10, Police/Fire: 10) - Weighted Factor out of 5

### Risk Categories:
- **85-100:** Preferred
- **70-84:** Standard
- **55-69:** Moderate
- **40-54:** Elevated
- **0-39:** Critical

### Ranking Rule:
Rank highest risk clients first.

### Observations:
- **Highest Risk Clients:** [Value]

---

## SECTION 9 – TREND VS PRIOR YEAR

### Trend Analysis:
- **Claims by Year:** [Value]
- **Incurred by Year:** $[Value]
- **Paid by Year:** $[Value]
- **Reserve by Year:** $[Value]

### Observations:
- **Year-over-Year Change %:** [Value]%
- **Frequency Trend:** [Value]
- **Severity Trend:** [Value]

### Assessment:
- **Overall Assessment:** [Value]

---

## SECTION 10 – EXECUTIVE INSIGHTS

### Executive Insights:
- **Top 5 Risk Drivers:** [Value]
- **Top 5 Clients Requiring Review:** [Value]
- **Top 5 Open Claims:** [Value]
- **Top 5 Cost Drivers:** [Value]
- **Top 5 Safety Initiatives:** [Value]
- **Portfolio Outlook:** [Value]
- **Reserve Adequacy Assessment:** [Value]
- **Litigation Assessment:** [Value]
- **Underwriting Assessment:** [Value]

---

## FINAL EXECUTIVE SCORECARD

### Scorecard Status:
- **Claim Frequency Status:** [Value]
- **Severity Status:** [Value]
- **Litigation Status:** [Value]
- **Reserve Status:** [Value]
- **State Concentration Status:** [Value]
- **Class Code Exposure Status:** [Value]
- **Overall Portfolio Risk Score:** [Use value from JSON: Executive Summary -> Portfolio AI Risk Score]

### Final Classification:
- **Preferred:** [Yes/No]
- **Standard:** [Yes/No]
- **Moderate:** [Yes/No]
- **Elevated:** [Yes/No]
- **High Risk:** [Yes/No]
- **Critical:** [Yes/No]

### Management Recommendation:
- [Detail 1]
- [Detail 2]

### Underwriting Action Plan:
- [Step 1]
- [Step 2]
"""

# ── Analyzer Class ───────────────────────────────────────────────────────────

class ManagementDashboardAnalyzer:
    """
    Reads a Loss Run Excel file, converts it to text, and sends it to
    OpenAI with the Management Claims Dashboard prompt to generate a 
    comprehensive report.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OpenAI API key not found. Set OPENAI_API_KEY in your "
                "environment or pass it directly."
            )
        self.client = OpenAI(api_key=self.api_key)

    @staticmethod
    def calculate_dashboard_metrics(df: pd.DataFrame) -> str:
        # Ensure money columns are numeric
        for col in ["Total Net Incurred", "Total Paid", "Total Reserve", "Indemnity Paid", "Medical Paid"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(r"[\$,]", "", regex=True), errors="coerce").fillna(0)

        # 1. Executive Summary
        total_claims = len(df)
        open_claims = len(df[df["Status"].astype(str).str.upper() == "OPEN"]) if "Status" in df.columns else 0
        closed_claims = len(df[df["Status"].astype(str).str.upper() == "CLOSED"]) if "Status" in df.columns else 0
        total_incurred = float(df["Total Net Incurred"].sum()) if "Total Net Incurred" in df.columns else 0
        total_paid = float(df["Total Paid"].sum()) if "Total Paid" in df.columns else 0
        outstanding_reserve = float(df["Total Reserve"].sum()) if "Total Reserve" in df.columns else 0
        
        litigation_claims = 0
        if "Litigation?" in df.columns:
            litigation_claims = len(df[df["Litigation?"].astype(str).str.upper() == "YES"])
        litigation_pct = (litigation_claims / total_claims * 100) if total_claims > 0 else 0
        
        avg_claim_cost = (total_incurred / total_claims) if total_claims > 0 else 0
        largest_claim = float(df["Total Net Incurred"].max()) if not df.empty and "Total Net Incurred" in df.columns else 0

        # 2. Top 10 Claims
        top_10_claims = []
        if "Total Net Incurred" in df.columns:
            top_10 = df.sort_values(by="Total Net Incurred", ascending=False).head(10)
            cols = [c for c in ["Claim Number", "Employer Location", "Accident State", "Status", "Total Net Incurred", "Total Reserve"] if c in df.columns]
            top_10_claims = top_10[cols].to_dict(orient="records")
        
        # 3. Losses by State
        state_group = []
        if "Accident State" in df.columns and "Total Net Incurred" in df.columns:
            state_group = df.groupby("Accident State").agg(
                Claim_Count=("Claim Number", "count") if "Claim Number" in df.columns else ("Total Net Incurred", "count"),
                Total_Paid=("Total Paid", "sum") if "Total Paid" in df.columns else ("Total Net Incurred", "sum"), # dummy fallback
                Outstanding_Reserve=("Total Reserve", "sum") if "Total Reserve" in df.columns else ("Total Net Incurred", "sum"),
                Total_Incurred=("Total Net Incurred", "sum")
            ).reset_index().sort_values(by="Total_Incurred", ascending=False).head(10).to_dict(orient="records")

        # 4. Losses by Class Code
        class_group = []
        if "NCCI Class Code" in df.columns and "Total Net Incurred" in df.columns:
            class_group_df = df.groupby(["NCCI Class Code", "NCCI Class Description"] if "NCCI Class Description" in df.columns else ["NCCI Class Code"]).agg(
                Claim_Count=("Total Net Incurred", "count"),
                Total_Incurred=("Total Net Incurred", "sum")
            ).reset_index()
            class_group_df["Average_Severity"] = class_group_df["Total_Incurred"] / class_group_df["Claim_Count"]
            class_group = class_group_df.sort_values(by="Total_Incurred", ascending=False).head(10).to_dict(orient="records")
        
        # 5. Top Injury Causes
        cause_group = []
        if "Accident Cause" in df.columns and "Total Net Incurred" in df.columns:
            cause_group_df = df.groupby("Accident Cause").agg(
                Claim_Count=("Total Net Incurred", "count"),
                Total_Incurred=("Total Net Incurred", "sum")
            ).reset_index()
            cause_group_df["Average_Severity"] = cause_group_df["Total_Incurred"] / cause_group_df["Claim_Count"]
            cause_group = cause_group_df.sort_values(by="Total_Incurred", ascending=False).head(10).to_dict(orient="records")

        # 6. Open Claim Aging
        aging_summary = []
        if "Date of Loss" in df.columns and "Status" in df.columns:
            if "ValuationDate" in df.columns:
                valuation_date = pd.to_datetime(df["ValuationDate"], errors="coerce").max()
            else:
                valuation_date = pd.Timestamp.today()
            
            df_temp = df.copy()
            df_temp["Date of Loss"] = pd.to_datetime(df_temp["Date of Loss"], errors="coerce")
            open_df = df_temp[df_temp["Status"].astype(str).str.upper() == "OPEN"].copy()
            
            if not open_df.empty and not pd.isna(valuation_date):
                open_df["Days Open"] = (valuation_date - open_df["Date of Loss"]).dt.days
                bins = [-1, 30, 90, 180, 365, float("inf")]
                labels = ["0-30 Days", "31-90 Days", "91-180 Days", "181-365 Days", "Over 365 Days"]
                open_df["Aging Bucket"] = pd.cut(open_df["Days Open"], bins=bins, labels=labels)
                aging_summary = open_df.groupby("Aging Bucket").agg(
                    Claim_Count=("Claim Number", "count") if "Claim Number" in df.columns else ("Total Net Incurred", "count"),
                    Total_Reserve=("Total Reserve", "sum") if "Total Reserve" in df.columns else ("Total Net Incurred", "sum"),
                    Total_Incurred=("Total Net Incurred", "sum") if "Total Net Incurred" in df.columns else ("Total Net Incurred", "sum")
                ).reset_index().to_dict(orient="records")

        # 8. AI Risk Score by Client
        client_col = "Employer Location" if "Employer Location" in df.columns else "Account"
        clients_risk = []
        if client_col in df.columns:
            for client_name, group in df.groupby(client_col):
                claim_count = len(group)
                total_incurred_client = group["Total Net Incurred"].sum() if "Total Net Incurred" in group.columns else 0
                total_reserve_client = group["Total Reserve"].sum() if "Total Reserve" in group.columns else 0
                avg_incurred = total_incurred_client / claim_count if claim_count > 0 else 0
                
                litigated = len(group[group["Litigation?"].astype(str).str.upper() == "YES"]) if "Litigation?" in group.columns else 0
                litigation_pct_client = (litigated / claim_count * 100) if claim_count > 0 else 0
                
                reserve_ratio = (total_reserve_client / total_incurred_client * 100) if total_incurred_client > 0 else 0
                
                main_state = group["Accident State"].mode()[0] if "Accident State" in group.columns and not group["Accident State"].mode().empty else "XX"
                main_class = str(group["NCCI Class Code"].mode()[0]) if "NCCI Class Code" in group.columns and not group["NCCI Class Code"].mode().empty else "0"

                if claim_count < 5: freq_score = 25
                elif claim_count < 10: freq_score = 20
                elif claim_count < 15: freq_score = 15
                elif claim_count < 20: freq_score = 10
                else: freq_score = 0
                
                if avg_incurred < 5000: sev_score = 25
                elif avg_incurred < 10000: sev_score = 20
                elif avg_incurred < 20000: sev_score = 15
                elif avg_incurred < 40000: sev_score = 10
                else: sev_score = 0
                
                if litigation_pct_client < 5: lit_score = 20
                elif litigation_pct_client < 10: lit_score = 15
                elif litigation_pct_client < 20: lit_score = 10
                elif litigation_pct_client < 30: lit_score = 5
                else: lit_score = 0
                
                if reserve_ratio < 20: res_score = 15
                elif reserve_ratio < 30: res_score = 12
                elif reserve_ratio < 40: res_score = 9
                elif reserve_ratio < 50: res_score = 6
                else: res_score = 0
                
                state_factors = {"TX": 1, "FL": 2, "GA": 2, "NC": 3, "PA": 4, "NY": 5, "IL": 6, "CA": 8}
                state_factor = state_factors.get(main_state, 5)
                state_score = max(0, 10 - state_factor)
                
                class_factors = {"8810": 1, "8018": 3, "8292": 5, "2002": 6, "7219": 8, "7720": 10, "808": 8}
                class_factor = class_factors.get(main_class, 5)
                class_score = max(0, 5 - (class_factor / 2))
                
                total_score = freq_score + sev_score + lit_score + res_score + state_score + class_score
                
                if total_score >= 85: category = "Preferred"
                elif total_score >= 70: category = "Standard"
                elif total_score >= 55: category = "Moderate"
                elif total_score >= 40: category = "Elevated"
                else: category = "Critical"
                
                clients_risk.append({
                    "Client Name": str(client_name),
                    "Claim Count": claim_count,
                    "Total Incurred": total_incurred_client,
                    "AI Risk Score": round(total_score),
                    "Risk Category": category
                })
        
        top_risky_clients = sorted(clients_risk, key=lambda x: x["AI Risk Score"])[:5]
        
        # Portfolio Score
        portfolio_score = 0
        portfolio_classification = "Critical"
        if clients_risk:
            portfolio_score = round(sum([c["AI Risk Score"] for c in clients_risk]) / len(clients_risk))
            if portfolio_score >= 85: portfolio_classification = "Preferred"
            elif portfolio_score >= 70: portfolio_classification = "Standard"
            elif portfolio_score >= 55: portfolio_classification = "Moderate"
            elif portfolio_score >= 40: portfolio_classification = "Elevated"
            else: portfolio_classification = "Critical"

        metrics = {
            "Executive Summary": {
                "Total Claims": total_claims,
                "Open Claims": open_claims,
                "Closed Claims": closed_claims,
                "Total Incurred": total_incurred,
                "Total Paid": total_paid,
                "Outstanding Reserve": outstanding_reserve,
                "Litigation Claims": litigation_claims,
                "Litigation %": f"{litigation_pct:.2f}%",
                "Average Claim Cost": avg_claim_cost,
                "Largest Claim": largest_claim,
                "Portfolio AI Risk Score": portfolio_score,
                "Overall Portfolio Risk Classification": portfolio_classification
            },
            "Top 10 Claims": top_10_claims,
            "Losses by State": state_group,
            "Losses by Class Code": class_group,
            "Top Injury Causes": cause_group,
            "Open Claim Aging": aging_summary,
            "AI Risk Score by Client": top_risky_clients
        }
        
        return json.dumps(metrics, indent=2)

    @staticmethod
    def excel_to_text(file_path: str) -> Tuple[str, str]:
        """
        Read every sheet in the Excel workbook and convert to a
        pipe-delimited text representation, and calculate metrics.
        """
        parts: list[str] = []
        all_dfs = []
        try:
            with pd.ExcelFile(file_path, engine="openpyxl") as xls:
                for sheet_name in xls.sheet_names:
                    try:
                        df = pd.read_excel(xls, sheet_name=sheet_name)
                        df = df.dropna(how="all").dropna(axis=1, how="all")

                        if df.empty:
                            continue

                        parts.append(f"=== Sheet: {sheet_name} ===")
                        parts.append(df.to_csv(sep="|", index=False, na_rep=""))
                        parts.append("") 
                        all_dfs.append(df)
                    except Exception as e:
                        print(f"Warning: Could not read sheet {sheet_name}: {e}")
        except Exception as e:
            print(f"Error opening Excel file: {e}")

        full_df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
        metrics_json = ManagementDashboardAnalyzer.calculate_dashboard_metrics(full_df)

        return "\n".join(parts), metrics_json

    def generate_dashboard(
        self,
        excel_text: str,
        metrics_json: str,
        model: str = "gpt-4o",
        temperature: float = 0.2,
    ) -> str:
        """
        Send textualised Excel data to OpenAI and return the report.
        """
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": MANAGEMENT_DASHBOARD_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "Analyze the following Loss Run data and generate "
                            "the full Management Claims Dashboard as specified.\n\n"
                            f"=== PRE-CALCULATED METRICS (JSON) ===\n{metrics_json}\n\n=== RAW TEXT DATA ===\n{excel_text}"
                        ),
                    },
                ],
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error generating Management Dashboard: {str(e)}"


# ── FastAPI Router ───────────────────────────────────────────────────────────

router = APIRouter()

@router.post(
    "/api/management-claims-dashboard",
    summary="Generate Management Claims Dashboard from Excel",
    description=(
        "Upload a Loss Run Excel file and receive a comprehensive "
        "AI-generated Management Claims Dashboard following the 10-section structure."
    ),
    tags=["Management Dashboard"],
)
async def generate_management_dashboard(
    file: UploadFile = File(..., description="Loss Run Excel file (.xlsx or .xls)"),
    download: bool = Query(True, description="Return a .txt file (true) or JSON (false)"),
    model: str = Query("gpt-4o", description="OpenAI model to use"),
    temperature: float = Query(0.2, description="Sampling temperature"),
):
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    ext = Path(filename).suffix.lower()
    if ext not in (".xlsx", ".xls"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Only .xlsx and .xls are accepted.",
        )

    tmp_input_path = None
    try:
        # Save upload to temp
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext, prefix="management_lossrun_") as tmp_input:
            tmp_input.write(await file.read())
            tmp_input_path = tmp_input.name

        # Process
        analyzer = ManagementDashboardAnalyzer()
        excel_text, metrics_json = analyzer.excel_to_text(tmp_input_path)

        if not excel_text.strip():
            raise HTTPException(status_code=400, detail="Excel file has no readable data.")

        dashboard_text = analyzer.generate_dashboard(
            excel_text=excel_text,
            metrics_json=metrics_json,
            model=model,
            temperature=temperature,
        )

        if download:
            tmp_output = tempfile.NamedTemporaryFile(
                mode="w", suffix="_management_dashboard.txt", delete=False, encoding="utf-8"
            )
            tmp_output.write(dashboard_text)
            tmp_output.flush()
            tmp_output.close()

            return FileResponse(
                path=tmp_output.name,
                filename="management_claims_dashboard.txt",
                media_type="text/plain",
            )

        return JSONResponse({"success": True, "dashboard": dashboard_text})

    except Exception as e:
        print(f"ERROR: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    finally:
        if tmp_input_path and os.path.exists(tmp_input_path):
            os.unlink(tmp_input_path)

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    uvicorn.run(app, host="0.0.0.0", port=8010)
