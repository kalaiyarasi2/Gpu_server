import json
import os
from typing import Dict, Optional
from openai import OpenAI
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


class ClaimsAnalyzer:
    """
    Analyzes insurance claims data and generates structured summaries using OpenAI LLM.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the ClaimsAnalyzer with OpenAI API key.
        
        Args:
            api_key: OpenAI API key. If None, will try to read from environment variable.
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OpenAI API key not found. Please provide it as a parameter "
                "or set the OPENAI_API_KEY environment variable."
            )
        self.client = OpenAI(api_key=self.api_key)
    
    def validate_claims_data(self, claims_json: dict) -> bool:
        """
        Validates the structure of claims JSON data.
        
        Args:
            claims_json: Dictionary containing claims data
            
        Returns:
            True if valid, raises ValueError otherwise
        """
        if not isinstance(claims_json, (dict, list)):
            raise ValueError("Claims data must be a dictionary or a list")
        
        if not claims_json and claims_json != []:
            raise ValueError("Claims data is empty")
        
        # Add more specific validation based on your schema
        return True
    
    def _calculate_statistics(self, claims: list) -> dict:
        """
        Calculates key statistics from a list of claims.
        """
        stats = {
            "total_claims": len(claims),
            "status_breakdown": {"Open": 0, "Closed": 0, "Reopened": 0, "Other": 0},
            "total_medical_paid": 0.0,
            "total_indemnity_paid": 0.0,
            "total_expense_paid": 0.0,
            "total_medical_reserve": 0.0,
            "total_indemnity_reserve": 0.0,
            "total_expense_reserve": 0.0,
            "total_incurred": 0.0,
            "litigated_count": 0,
            "reopened_count": 0,
            "highest_incurred": {"claim_id": "N/A", "amount": 0.0},
            "lowest_incurred": {"claim_id": "N/A", "amount": float('inf')},
            "high_reserve_risk_claims": []
        }

        for claim in claims:
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

            # Financials (converting to float to be safe)
            m_paid = float(claim.get("medical_paid") or 0)
            i_paid = float(claim.get("indemnity_paid") or 0)
            e_paid = float(claim.get("expense_paid") or 0)
            m_res = float(claim.get("medical_reserve") or 0)
            i_res = float(claim.get("indemnity_reserve") or 0)
            e_res = float(claim.get("expense_reserve") or 0)
            total_inc = float(claim.get("total_incurred") or 0)

            stats["total_medical_paid"] += m_paid
            stats["total_indemnity_paid"] += i_paid
            stats["total_expense_paid"] += e_paid
            stats["total_medical_reserve"] += m_res
            stats["total_indemnity_reserve"] += i_res
            stats["total_expense_reserve"] += e_res
            stats["total_incurred"] += total_inc

            # Highest/Lowest
            if total_inc > stats["highest_incurred"]["amount"]:
                stats["highest_incurred"] = {"claim_id": claim.get("claim_number", "N/A"), "amount": total_inc}
            
            # Lowest incurred (filtered for > 0 if possible, otherwise min)
            if total_inc > 0 and total_inc < stats["lowest_incurred"]["amount"]:
                stats["lowest_incurred"] = {"claim_id": claim.get("claim_number", "N/A"), "amount": total_inc}

            # High Reserve Risk (reserves > 50% of incurred)
            total_res = m_res + i_res + e_res
            if total_inc > 0 and (total_res / total_inc) > 0.5:
                stats["high_reserve_risk_claims"].append({
                    "claim_id": claim.get("claim_number", "N/A"),
                    "total_incurred": total_inc,
                    "reserves": total_res
                })

        # Handle case where no claims have incurred > 0
        if stats["lowest_incurred"]["amount"] == float('inf'):
            if claims:
                min_claim = min(claims, key=lambda x: float(x.get("total_incurred") or 0))
                stats["lowest_incurred"] = {
                    "claim_id": min_claim.get("claim_number", "N/A"),
                    "amount": float(min_claim.get("total_incurred") or 0)
                }
            else:
                stats["lowest_incurred"]["amount"] = 0.0

        return stats

    def generate_claim_summary(
        self, 
        claims_json: dict,
        model: str = "gpt-4.1-mini",
        temperature: float = 0.2
    ) -> str:
        """
        Takes extracted claims JSON and returns structured summary using OpenAI LLM.
        
        Args:
            claims_json: Dictionary containing claims data
            model: OpenAI model to use (default: gpt-4.1-mini)
            temperature: LLM temperature setting (default: 0.2 for consistent output)
            
        Returns:
            Formatted summary string
        """
        # Validate input
        self.validate_claims_data(claims_json)
        
        if isinstance(claims_json, list):
            claims = claims_json
        else:
            claims = claims_json.get("claims", [])
        
        # Calculate statistics in Python
        stats = self._calculate_statistics(claims)
        
        # Format stats for prompt
        total_paid = stats["total_medical_paid"] + stats["total_indemnity_paid"] + stats["total_expense_paid"]
        total_reserves = stats["total_medical_reserve"] + stats["total_indemnity_reserve"] + stats["total_expense_reserve"]
        payment_velocity = total_paid / stats["total_incurred"] if stats["total_incurred"] > 0 else 0
        avg_incurred = stats["total_incurred"] / stats["total_claims"] if stats["total_claims"] > 0 else 0
        
        precalc_stats = f"""
PRE-CALCULATED STATISTICS (USE THESE EXACT NUMBERS):
- Total Claims: {stats['total_claims']}
- Status Breakdown: {json.dumps(stats['status_breakdown'])}
- Total Incurred: ${stats['total_incurred']:,.2f}
- Total Paid: ${total_paid:,.2f}
  - Medical: ${stats['total_medical_paid']:,.2f}
  - Indemnity: ${stats['total_indemnity_paid']:,.2f}
  - Expense: ${stats['total_expense_paid']:,.2f}
- Total Reserves: ${total_reserves:,.2f}
- Litigated Count: {stats['litigated_count']}
- Reopened Count: {stats['reopened_count']}
- Highest Incurred Claim: ID {stats['highest_incurred']['claim_id']} (${stats['highest_incurred']['amount']:,.2f})
- Lowest Incurred Claim: ID {stats['lowest_incurred']['claim_id']} (${stats['lowest_incurred']['amount']:,.2f})
- Average Incurred: ${avg_incurred:,.2f}
- Payment Velocity (Paid/Incurred): {payment_velocity:.3f}
- High Reserve Risk Claims: {json.dumps(stats['high_reserve_risk_claims'])}
"""

        # Convert JSON to formatted string
        formatted_json = json.dumps(claims_json, indent=2)
        
        # System prompt (controls behavior)
        system_prompt = f"""
You are an insurance claim analyst AI with expertise in workers' compensation and liability claims.

Analyze the provided claims JSON and generate a comprehensive, professional summary.

IMPORTANT: I have pre-calculated the key financial statistics for you. You MUST use the pre-calculated numbers provided below for sections 1 and 2 of your report. Do not attempt to re-calculate them from the JSON, as the pre-calculated ones are the source of truth.

{precalc_stats}

Follow this structure for the summary:

1. **Overall Statistics**
   - Carrier Name (Identify from JSON)
   - Total number of claims (Use pre-calc)
   - Open vs Closed status breakdown (Use pre-calc)
   - Total incurred amount (Use pre-calc)
   - Total paid (breakdown: medical + indemnity + expense) (Use pre-calc)
   - Total reserves (Use pre-calc)
   - Litigated claims count (Use pre-calc)
   - Reopened claims count (Use pre-calc)

2. **Financial Insights**
   - Highest incurred claim (Use pre-calc: ID and amount)
   - Lowest incurred claim (Use pre-calc: ID and amount)
   - Average incurred per claim (Use pre-calc)
   - Claims with high reserve risk (Use pre-calc)
   - Payment velocity (Use pre-calc: paid/incurred ratio)

3. **Injury & Medical Insights**
   - Most common injury types (Analyze top 3-5 from JSON)
   - Body parts most frequently affected (Analyze from JSON)
   - Status distribution (Use pre-calc)
   - Average days to closure (if date information available in JSON)

4. **Risk Flags & Recommendations**
   - Claims requiring immediate attention (Identify from open claims with high reserves/incurred)
   - Potential fraud indicators (Search for patterns in the JSON data, e.g., multiple similar claims)
   - Cost containment opportunities (Based on injury types and body parts)

Format the output professionally with clear headers and bullet points.
Use currency formatting for dollar amounts.
Round percentages to 1 decimal place.
Do not repeat the raw JSON data.
"""
        
        # User prompt (actual data)
        user_prompt = f"""
Here is the extracted claims JSON data for additional analysis and context lookup:

{formatted_json}

Please provide a comprehensive summary following the structure and using the PRE-CALCULATED statistics provided.
"""
        
        try:
            # Call OpenAI LLM
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature
            )
            
            summary = response.choices[0].message.content
            return summary
            
        except Exception as e:
            raise RuntimeError(f"Error calling OpenAI API: {str(e)}")
    
    def save_summary(self, summary: str, output_path: str) -> None:
        """
        Save the generated summary to a file.
        
        Args:
            summary: The summary text to save
            output_path: Path where the summary should be saved
        """
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                # Add timestamp header
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"Claims Analysis Report\n")
                f.write(f"Generated: {timestamp}\n")
                f.write("=" * 80 + "\n\n")
                f.write(summary)
            print(f"Summary saved successfully to: {output_path}")
        except Exception as e:
            raise IOError(f"Error saving summary to file: {str(e)}")


# ==========================
# Example Usage
# ==========================
def main():
    """Main function demonstrating usage of ClaimsAnalyzer"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Analyze insurance claims JSON data.")
    parser.add_argument("input", help="Path to the input claims JSON file", default="extracted_schema.json", nargs="?")
    parser.add_argument("--output", help="Path to save the summary report", default=None)
    
    args = parser.parse_args()
    
    INPUT_FILE = args.input
    
    # If output not specified, put it in the same directory as input
    if args.output:
        OUTPUT_FILE = args.output
    else:
        input_path = os.path.abspath(INPUT_FILE)
        output_dir = os.path.dirname(input_path)
        OUTPUT_FILE = os.path.join(output_dir, "claims_summary.txt")
    
    try:
        # Initialize analyzer
        analyzer = ClaimsAnalyzer()
        
        # Load claims data
        if not os.path.exists(INPUT_FILE):
             raise FileNotFoundError(f"Could not find {INPUT_FILE}")

        print(f"Loading claims data from {INPUT_FILE}...")
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            claims_data = json.load(f)
        
        if isinstance(claims_data, dict):
            claims_list = claims_data.get('claims', [])
        elif isinstance(claims_data, list):
            claims_list = claims_data
        else:
            claims_list = []
            
        print(f"Loaded {len(claims_list)} claims")
        
        # Generate summary
        print("Generating summary with OpenAI LLM...")
        summary_output = analyzer.generate_claim_summary(
            claims_data,
            model="gpt-4.1-mini",
            temperature=0.2
        )
        
        # Display summary
        print("\n" + "=" * 80)
        print("CLAIMS ANALYSIS SUMMARY")
        print("=" * 80 + "\n")
        print(summary_output)
        
        # Save to file
        analyzer.save_summary(summary_output, OUTPUT_FILE)
        
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Please ensure the claims JSON file exists.")
    except ValueError as e:
        print(f"Validation Error: {e}")
    except RuntimeError as e:
        print(f"Runtime Error: {e}")
    except Exception as e:
        print(f"Unexpected Error: {e}")


if __name__ == "__main__":
    main()