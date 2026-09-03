import json
import os
import sys
from typing import Dict, Optional, Any, List
from openai import OpenAI
from pathlib import Path
from dotenv import load_dotenv

import tempfile
from fastapi import APIRouter, Query, HTTPException, Body
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

# Load env variables
load_dotenv()
current_dir = Path(__file__).resolve().parent
for parent in current_dir.parents:
    env_path = parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        break

class UnderwritingAnalyzer:
    """
    Analyzes claim submission materials and other insurance documents
    to generate a comprehensive underwriting assessment report using OpenAI.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the UnderwritingAnalyzer with OpenAI API key.
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OpenAI API key not found. Please provide it as a parameter "
                "or set the OPENAI_API_KEY environment variable."
            )
        self.client = OpenAI(api_key=self.api_key)

    def validate_data(self, data_json: Any) -> bool:
        """
        Validates the structure of the input JSON data.
        """
        if not isinstance(data_json, (dict, list, str)):
            raise ValueError("Data must be a dictionary, list, or string")
        return True

    def _calculate_statistics(self, data: Any) -> Dict[str, Any]:
        """
        Calculates key statistics if the input data represents claims.
        """
        stats = {
            "is_claims": False,
            "total_claims": 0,
            "total_incurred": 0.0,
            "total_paid": 0.0,
            "total_reserves": 0.0,
            "status_breakdown": {"Open": 0, "Closed": 0, "Reopened": 0, "Other": 0},
            "medical_paid": 0.0,
            "indemnity_paid": 0.0,
            "expense_paid": 0.0,
            "medical_reserve": 0.0,
            "indemnity_reserve": 0.0,
            "expense_reserve": 0.0,
            "litigated_count": 0,
            "reopened_count": 0,
            "average_claim": 0.0
        }

        # Determine if input contains list of claims
        claims = []
        if isinstance(data, list):
            claims = data
        elif isinstance(data, dict):
            claims = data.get("claims") or data.get("data") or []
            if not isinstance(claims, list):
                claims = []

        if not claims:
            return stats

        # Check if the list contains objects that look like claims
        is_claims = any(any(k in claim for k in ["claim_number", "medical_paid", "total_incurred"]) 
                        for claim in claims if isinstance(claim, dict))
        
        if not is_claims:
            return stats

        stats["is_claims"] = True
        stats["total_claims"] = len(claims)

        for claim in claims:
            if not isinstance(claim, dict):
                continue
            
            # Status
            status = claim.get("status", "Other")
            if status in stats["status_breakdown"]:
                stats["status_breakdown"][status] += 1
            else:
                stats["status_breakdown"]["Other"] += 1
            
            # Reopened count
            if str(claim.get("reopen")).lower() == "true":
                stats["reopened_count"] += 1
            
            # Litigation
            if str(claim.get("litigation", "")).lower() == "yes":
                stats["litigated_count"] += 1

            # Financials
            m_paid = float(claim.get("medical_paid") or 0)
            i_paid = float(claim.get("indemnity_paid") or 0)
            e_paid = float(claim.get("expense_paid") or 0)
            m_res = float(claim.get("medical_reserve") or 0)
            i_res = float(claim.get("indemnity_reserve") or 0)
            e_res = float(claim.get("expense_reserve") or 0)
            total_inc = float(claim.get("total_incurred") or 0)

            stats["medical_paid"] += m_paid
            stats["indemnity_paid"] += i_paid
            stats["expense_paid"] += e_paid
            stats["medical_reserve"] += m_res
            stats["indemnity_reserve"] += i_res
            stats["expense_reserve"] += e_res
            stats["total_incurred"] += total_inc
            stats["total_paid"] += (m_paid + i_paid + e_paid)
            stats["total_reserves"] += (m_res + i_res + e_res)

        if stats["total_claims"] > 0:
            stats["average_claim"] = round(stats["total_incurred"] / stats["total_claims"], 2)

        return stats

    def generate_underwriting_report(
        self, 
        data_json: Any, 
        model: str = "gpt-4o-mini", 
        temperature: float = 0.2,
        prospect_name: Optional[str] = None
    ) -> str:
        """
        Generates a comprehensive underwriting assessment report using OpenAI
        based on the provided submission data.
        """
        self.validate_data(data_json)
        
        # Try to infer prospect name from data if not provided
        if not prospect_name and isinstance(data_json, dict):
            # Check demographics from Work Comp ACORD
            demographics = data_json.get("data", {}).get("demographics", {}) if isinstance(data_json.get("data"), dict) else {}
            prospect_name = (
                data_json.get("company_name") or
                data_json.get("applicant_name") or
                data_json.get("employer_name") or
                data_json.get("insured_name") or
                data_json.get("prospect") or
                demographics.get("applicantName") or
                demographics.get("legalEntityName")
            )
            
        final_prospect = prospect_name if prospect_name else "[Identify Company Name from the data if possible, otherwise write 'Unknown']"
        
        # Calculate stats if possible to provide better context
        stats = self._calculate_statistics(data_json)
        
        # Construct statistics context if we detected claims
        stats_context = ""
        if stats["is_claims"]:
            stats_context = f"""
Here are some computed metrics from the provided claims JSON to help with your underwriting analysis:
- **Total Claims**: {stats['total_claims']}
- **Total Incurred**: ${stats['total_incurred']:,.2f}
- **Total Paid**: ${stats['total_paid']:,.2f}
  - Medical Paid: ${stats['medical_paid']:,.2f}
  - Indemnity Paid: ${stats['indemnity_paid']:,.2f}
  - Expense Paid: ${stats['expense_paid']:,.2f}
- **Average Claim (Paid)**: ${stats['average_claim']:,.2f}
- **Total Reserves**: ${stats['total_reserves']:,.2f}
  - Medical Reserve: ${stats['medical_reserve']:,.2f}
  - Indemnity Reserve: ${stats['indemnity_reserve']:,.2f}
  - Expense Reserve: ${stats['expense_reserve']:,.2f}
- **Claims Status**:
  - Open: {stats['status_breakdown']['Open']}
  - Closed: {stats['status_breakdown']['Closed']}
  - Reopened: {stats['status_breakdown']['Reopened']}
  - Other: {stats['status_breakdown']['Other']}
- **Litigated Claims**: {stats['litigated_count']}
- **Reopened Claims**: {stats['reopened_count']}
"""

        system_prompt = """You are an expert commercial insurance underwriter and risk analyst specializing in underwriting evaluation, loss analysis, operational risk assessment, and insurance decision support.
 
Your task is to analyze the provided submission materials and generate a comprehensive underwriting assessment report.
 
========================

OBJECTIVE

========================
 
Review all provided underwriting documents and determine:
 
1. Overall risk quality

2. Probability of future losses

3. Claim frequency and severity trends

4. Operational and industry exposures

5. Financial and underwriting concerns

6. Market appetite suitability

7. Pricing and structural recommendations

8. Final underwriting recommendation
 
The analysis should resemble the work product of a senior commercial insurance underwriter, MGA risk analyst, captive analyst, or carrier underwriting manager.
 
========================

DOCUMENT TYPES TO ANALYZE

========================
 
Potential input documents may include:
 
- Loss Runs

- Accord Applications

- Supplemental Applications

- OSHA Logs

- Driver Schedules

- Payroll Reports

- Financial Statements

- Safety Manuals

- Vehicle Schedules

- Prior Carrier Information

- Claims Narratives

- Exposure Schedules

- Inspection Reports

- Mod Worksheets

- Business Descriptions

- Operational Narratives

- Employee Rosters

- Submission Emails
 
Analyze whichever documents are provided.
 
========================

UNDERWRITING ANALYSIS REQUIREMENTS

========================
 
Evaluate:
 
- Claim frequency

- Claim severity

- Open reserve exposure

- Litigation potential

- Operational hazards

- Geographic exposure

- Industry-specific risk

- Employee injury patterns

- Catastrophic exposure potential

- Safety culture indicators

- Loss trend deterioration/improvement

- Financial stability indicators

- Employee turnover indicators

- Vehicle/fleet exposure

- Ergonomic exposure

- Assault/crime exposure

- Repetitive motion exposure

- Slip/trip/fall exposure

- Equipment exposure

- Compliance concerns

- Operational scalability concerns
 
========================

UNDERWRITING LOGIC

========================
 
Apply realistic underwriting reasoning.
 
Examples:
 
- High frequency indicates operational instability

- Large open claims indicate reserve uncertainty

- Repetitive injuries indicate ergonomic deficiencies

- Multiple similar claims indicate weak controls

- Rapid claim growth indicates deteriorating operations

- Low frequency but high severity indicates catastrophe exposure

- Strong safety programs may offset moderate loss history

- Poor documentation increases underwriting uncertainty
 
Distinguish between:

- frequency-driven risk

- severity-driven risk

- operational risk

- catastrophic risk

- systemic risk
 
========================

RISK SCORING MODEL

========================
 
Generate an overall underwriting risk score from 0–100.
 
Definitions:
 
0–20 = Excellent Risk

21–40 = Good Risk

41–60 = Moderate Risk

61–80 = Elevated Risk

81–100 = High Hazard / Distressed Risk
 
The score should consider:
 
- historical losses

- operational complexity

- exposure profile

- reserve concerns

- safety indicators

- claim trends

- industry risk

- geographic risk

- underwriting uncertainty
 
========================

OUTPUT FORMAT

========================
 
Return the report using the following structure:
 
# Underwriting Risk Analysis

## Prospect: [Company Name]
 
[Calculated Stats Block]
 
# Executive Summary
 
| Metric | Assessment |
|---|---|
| Overall Risk Score | XX / 100 |
| Underwriting Tier | |
| Frequency Trend | |
| Severity Trend | |
| Open Claim Exposure | |
| Estimated Experience Mod Direction | |
| Operational Risk | |
| Litigation Potential | |
| Financial Stability | |
| Recommended Market | |
| Recommended Action | |
 
# Exposure Analysis
 
Analyze:

- operations

- employee activities

- geographic footprint

- customer interactions

- equipment/fleet exposure

- environmental hazards
 
# Loss Analysis
 
Analyze:

- total incurred losses

- medical losses

- indemnity exposure

- expense exposure

- open reserves

- severity concentration

- recurring injury patterns
 
# Claim Metrics & Charts
 
Generate:

- top claims table

- injury category breakdown

- severity buckets

- open vs closed ratio

- claim trend analysis

- cause-of-loss charts
 
Example:
 
Slip/Trip/Fall         ███████████ 28%
Vehicle Incidents      ██████ 15%
Repetitive Motion      █████████████ 34%
Assault/Crime          ███ 7%
 
# Frequency Analysis
 
Discuss:

- recurring claim generation

- injury velocity

- operational patterns

- systemic safety issues
 
# Severity Analysis
 
Discuss:

- catastrophic potential

- reserve development risk

- large loss concentration

- claim escalation concerns
 
# Operational Risk Factors
 
Identify:

- unsafe operational patterns

- staffing concerns

- process weaknesses

- training deficiencies

- management concerns

- environmental exposures
 
# Positive Risk Factors
 
Identify favorable underwriting indicators.
 
# Underwriting Concerns
 
Identify major concerns including:

- reserve deterioration

- litigation risk

- catastrophic exposure

- operational instability

- compliance concerns

- inconsistent controls
 
# Market Appetite Assessment
 
| Market Type | Appetite |
|---|---|
| Preferred Market | |
| Standard Market | |
| Specialty Market | |
| Excess & Surplus | |
| Captive | |
| Assigned Risk | |
 
# Pricing Recommendations
 
Recommend:

- schedule credit/debit

- deductible structure

- collateral requirements

- loss-sensitive suitability

- attachment points

- self-insured retention considerations
 
# Recommended Underwriting Controls
 
Recommend:

- safety programs

- ergonomic controls

- driver monitoring

- telematics

- training requirements

- claims review process

- return-to-work program

- operational improvements
 
# Estimated Future Loss Probability
 
Estimate:

- probability of future claims

- probability of severe claims

- reserve deterioration potential

- expected trend direction

- estimated experience mod direction
 
# Underwriting Decision
 
Provide:

- Quote

- Conditional Quote

- Decline

- Refer to Specialty Market

- Require Additional Information
 
Explain the reasoning clearly.
 
# Final Underwriting Opinion
 
Provide a concise executive underwriting conclusion suitable for carrier management review.
 
========================

WRITING STYLE

========================
 
The writing style must be:
 
- professional

- analytical

- underwriting-focused

- executive-level

- concise but detailed

- insurance industry specific

- data-driven
 
Avoid generic AI wording.
 
Sound like:

- a senior underwriter

- MGA analyst

- captive analyst

- carrier risk consultant

- commercial insurance executive
 
========================

SPECIAL INSTRUCTIONS

========================
 
- Use realistic underwriting terminology

- Quantify observations whenever possible

- Explain WHY risks are concerning

- Explain WHY risks may still be insurable

- Identify operational trends

- Detect recurring loss causes

- Highlight emerging exposures

- Consider reserve adequacy

- Distinguish between frequency and severity issues

- Focus on practical underwriting implications
 
If information is incomplete:

- explicitly state assumptions

- provide best-estimate underwriting interpretation
 
At the end provide:
 
FINAL RISK SCORE: XX/100

FINAL UNDERWRITING DECISION: [Decision]"""

        # Construct original-style Executive Summary statistics block
        exec_stats_block = ""
        if stats["is_claims"]:
            exec_stats_block = f"""## 1. Executive Summary

- **Total Claims**: {stats['total_claims']}

- **Total Incurred**: ${stats['total_incurred']:,.2f}

- **Total Paid**: ${stats['total_paid']:,.2f}

- **Average Claim**: {stats['average_claim']}

  - **Medical Paid**: ${stats['medical_paid']:,.2f}

  - **Indemnity Paid**: ${stats['indemnity_paid']:,.2f}

- **Total Reserves**: ${stats['total_reserves']:,.2f}

- **Claims Status**: 

  - Closed: {stats['status_breakdown'].get('Closed', 0)}

  - Open: {stats['status_breakdown'].get('Open', 0)}

  - Reopened: {stats['status_breakdown'].get('Reopened', 0)}

  - Other: {stats['status_breakdown'].get('Other', 0)}

- **Litigated Claims**: {stats['litigated_count']}"""

        # Substitute prospect name and calculated statistics in system prompt
        system_prompt = system_prompt.replace("[Company Name]", final_prospect)
        system_prompt = system_prompt.replace("[Calculated Stats Block]", exec_stats_block)

        # Serialize inputs to string for OpenAI user prompt
        if isinstance(data_json, (dict, list)):
            user_content = f"{stats_context}\n\nSubmission materials/Data to analyze:\n{json.dumps(data_json, indent=2)}"
        else:
            user_content = f"{stats_context}\n\nSubmission materials/Data to analyze:\n{str(data_json)}"

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=temperature
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error generating underwriting report: {str(e)}"

# ── FastAPI Router & Endpoint ───────────────────────────────────────────────
router = APIRouter()
 
class UnderwritingSummaryResponse(BaseModel):
    success: bool
    summary: str
 
@router.post(
    "/api/underwriting-summary",
    summary="Get Underwriting AI Summary",
    description=(
        "Generate a comprehensive AI Underwriting Assessment Report from raw JSON data.\n\n"
        "**Want a downloadable .txt file?** Add `?download=true` to the URL."
    ),
    tags=["AI Summary"]
)
async def get_underwriting_summary(
    body: Dict[str, Any] = Body(
        ...,
        examples=[
            {
                "claims": [
                    {
                        "body_part": "Foot right",
                        "carrier_name": "Redwood Fire and Casualty Insurance Company",
                        "claim_class": "8810",
                        "claim_number": "44107873",
                        "claim_year": 2025,
                        "employee_name": "Gordon, Tina",
                        "expense_paid": 7066.18,
                        "expense_reserve": 17539.11,
                        "indemnity_paid": 7972.84,
                        "indemnity_reserve": 22485.31,
                        "injury_date_time": "2025-08-06",
                        "injury_description": "Glass bowl broke and cut foot.",
                        "injury_type": "Indemnity",
                        "litigation": "No",
                        "medical_paid": 27061.43,
                        "medical_reserve": 50553.27,
                        "policy_number": "STWC710881",
                        "reopen": "False",
                        "status": "Open",
                        "total_incurred": 132678.14,
                        "total_paid": 42100.45,
                        "total_reserve": 90577.69
                    }
                ]
            }
        ]
    ),
    download: bool = Query(False, description="Set to true to download the summary as a .txt file"),
    model: str = Query("gpt-4o-mini", description="OpenAI model to use"),
    temperature: float = Query(0.2, description="Sampling temperature"),
    prospect_name: Optional[str] = Query(None, description="Optional prospect/company name")
):
    try:
        analyzer = UnderwritingAnalyzer()
        summary = analyzer.generate_underwriting_report(
            data_json=body,
            model=model,
            temperature=temperature,
            prospect_name=prospect_name
        )
 
        if download:
            tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='_underwriting_summary.txt',
                delete=False, encoding='utf-8'
            )
            tmp.write(summary)
            tmp.flush()
            tmp.close()
            return FileResponse(
                path=tmp.name,
                filename="underwriting_assessment_report.txt",
                media_type="text/plain"
            )
 
        return JSONResponse({
            'success': True,
            'summary': summary
        })
    except Exception as e:
        print(f"❌ Error generating underwriting summary: {e}")
        return JSONResponse({
            'error': str(e),
            'success': False
        }, status_code=500)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate an Underwriting Assessment Report using AI.")
    parser.add_argument("input_file", help="Path to the JSON or TXT file containing submission data/claims.")
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI model to use (default: gpt-4o-mini).")
    parser.add_argument("--output", help="Path to save the generated markdown report.")
    parser.add_argument("--prospect", help="Name of the prospect/company to include in the report.")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input_file):
        print(f"Error: Input file '{args.input_file}' does not exist.")
        sys.exit(1)
        
    try:
        with open(args.input_file, "r", encoding="utf-8") as f:
            content = f.read()
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                # If it's not valid JSON, treat it as raw text
                data = content
    except Exception as e:
        print(f"Error reading input file: {e}")
        sys.exit(1)
        
    print(f"Analyzing data from {args.input_file} using {args.model}...")
    try:
        analyzer = UnderwritingAnalyzer()
        report = analyzer.generate_underwriting_report(data, model=args.model, prospect_name=args.prospect)
        
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"Report successfully saved to {args.output}")
        else:
            print("\n" + "="*40 + " GENERATED REPORT " + "="*40 + "\n")
            print(report)
            print("\n" + "="*98 + "\n")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
