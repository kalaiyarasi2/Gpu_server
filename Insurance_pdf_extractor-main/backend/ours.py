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
        if not isinstance(claims_json, dict):
            raise ValueError("Claims data must be a dictionary")
        
        if not claims_json:
            raise ValueError("Claims data is empty")
        
        # Add more specific validation based on your schema
        return True
    
    def generate_claim_summary(
        self, 
        claims_json: dict,
        model: str = "gpt-4o-mini",
        temperature: float = 0.2
    ) -> str:
        """
        Takes extracted claims JSON and returns structured summary using OpenAI LLM.
        
        Args:
            claims_json: Dictionary containing claims data
            model: OpenAI model to use (default: gpt-4o-mini)
            temperature: LLM temperature setting (default: 0.2 for consistent output)
            
        Returns:
            Formatted summary string
        """
        # Validate input
        self.validate_claims_data(claims_json)
        
        # Convert JSON to formatted string
        formatted_json = json.dumps(claims_json, indent=2)
        
        # Detect if this is an Invoice or a Claims document
        is_invoice = False
        first_item = {}
        if isinstance(claims_json, dict):
            if "LINE_ITEMS" in claims_json:
                is_invoice = True
                first_item = claims_json.get("LINE_ITEMS", [{}])[0] if claims_json.get("LINE_ITEMS") else {}
            elif "claims" in claims_json:
                first_item = claims_json.get("claims", [{}])[0] if claims_json.get("claims") else {}
                if "CURRENT_PREMIUM" in first_item or "INV_DATE" in claims_json.get("HEADER", {}):
                    is_invoice = True
        elif isinstance(claims_json, list) and claims_json:
            first_item = claims_json[0]
            if "CURRENT_PREMIUM" in first_item:
                is_invoice = True

        if is_invoice:
            # INVOICE SUMMARY PROMPT
            system_prompt = """
You are an expert insurance billing auditor. Analyze the provided premium invoice JSON and generate a professional summary.

Sections:
1. **Invoice Overview**
   - Billing Period & Invoice Date
   - Total Premium Amount (if present in header)
   - Calculated Total (sum of all CURRENT_PREMIUM and ADJUSTMENT_PREMIUM)
   
2. **Breakdown by Coverage**
   - Summarize totals for each PLAN_NAME (e.g., Medical, Dental, Vision)
   - Count of members in each plan
   
3. **Adjustment Summary**
   - Total retroactive adjustments/credits
   - Identification of significant adjustments

4. **Member Insights**
   - Total member count
   - Identification of highest premium members
   
Format the output with clear headers and bullet points. Use currency formatting.
"""
        else:
            # CLAIMS SUMMARY PROMPT (Existing)
            system_prompt = """
You are an insurance claim analyst AI with expertise in workers' compensation and liability claims.

Analyze the provided claims JSON and generate a comprehensive, professional summary:

1. **Overall Statistics**
   - Total number of claims
   - Open vs Closed status breakdown
   - Total incurred amount
   - Total paid (breakdown: medical + indemnity + expense)
   - Total reserves
   - Litigated claims count

2. **Financial Insights**
   - Highest/Lowest incurred claims
   - Average incurred per claim
   - Claims with high reserve risk
   
3. **Injury & Medical Insights**
   - Most common injury types
   - Body parts most frequently affected
   
4. **Risk Flags & Recommendations**

Format the output professionally with clear headers and bullet points.
"""
        
        # User prompt (actual data)
        user_prompt = f"""
Here is the extracted data JSON for analysis:

{formatted_json}

Please provide a comprehensive summary following the structure outlined in your instructions.
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
        
        print(f"Loaded {len(claims_data.get('claims', []))} claims")
        
        # Generate summary
        print("Generating summary with OpenAI LLM...")
        summary_output = analyzer.generate_claim_summary(
            claims_data,
            model="gpt-4o-mini",
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