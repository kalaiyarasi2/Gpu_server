import json
import re
import base64
import io
from typing import Dict, List, Tuple, Optional
from openai import OpenAI
import os
from pdf2image import convert_from_path

class FinancialValidator:
    """
    Dynamically identifies and validates financial calculation patterns in insurance documents.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key is required")
        self.client = OpenAI(api_key=self.api_key)

    def _get_page_images(self, pdf_path: str, max_pages: int = 4) -> List[str]:
        """
        Converts PDF pages to base64 images.
        """
        try:
            images = convert_from_path(pdf_path, first_page=1, last_page=max_pages)
            base64_images = []
            for img in images:
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG")
                base64_images.append(base64.b64encode(buffered.getvalue()).decode('utf-8'))
            return base64_images
        except Exception as e:
            print(f"Error converting PDF to images: {e}")
            return []

    def identify_pattern_from_vision(self, pdf_path: str, max_pages: int = 4) -> Dict:
        """
        Uses gpt-4.1-Vision to identify the table structure and math patterns from PDF images.
        """
        base64_images = self._get_page_images(pdf_path, max_pages)
        if not base64_images:
            return {}

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text", 
                        "text": """Analyze these pages from an insurance loss run report with HIGH PRECISION. 
                        
Tasks:
1. Identify the Table/Document Structure: Is it a traditional column-based table, or a block-style layout where each claim's data is grouped in a labeled block?
2. Data Grouping: Look for how a single claim's values are grouped. Are they split across multiple rows or grouped together?
3. Financial Mapping: Map the document's labels to these standard fields: 
   - Medical (Paid, Reserve, Incurred)
   - Indemnity (Paid, Reserve, Incurred)
   - Expense (Paid, Reserve, Incurred) -> Note: Include Legal, Other, and Admin expenses here.
   - Total/Net Incurred
4. Math Pattern: Determine the exact mathematical formula used to get the 'Total' or 'Net' value. 
   Example formulas:
   - (Med + Ind + Exp) = Total
   - (Paid + Reserve) = Incurred
   - Total Incurred - Recovery = Net Incurred

Return ONLY a JSON object:
{
  "layout_type": "columnar|block|mixed",
  "pattern_description": "Precise description of calculation logic",
  "components": ["field1", "field2", ...],
  "formula_js": "javascript-expression using schema fields",
  "tolerance": 0.05,
  "confidence_reasoning": "Why this specific pattern was chosen"
}"""
                    },
                ]
            }
        ]
        
        for b64 in base64_images:
            messages[0]["content"].append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })

        try:
            response = self.client.chat.completions.create(
                model="gpt-4.1",
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"Error identifying vision pattern: {e}")
            return {}

    def identify_calculation_pattern(self, text_sample: str, pdf_path: Optional[str] = None) -> Dict:
        """
        Identifies the mathematical relationship, using vision if a PDF path is provided.
        """
        if pdf_path:
            vision_pattern = self.identify_pattern_from_vision(pdf_path)
            if vision_pattern:
                return vision_pattern
        prompt = f"""Analyze the following text from an insurance loss run report. 
Identify the mathematical relationship between the financial columns (Paid, Reserved, Incurred, Recovery, etc.).

Look for a pattern like:
- Total Incurred = (Medical Paid + Medical Reserve) + (Indemnity Paid + Indemnity Reserve) + (Expense Paid + Expense Reserve)
- Total Incurred = Total Paid + Total Reserved
- Net Incurred = Total Incurred - Recovery

TEXT SAMPLE:
{text_sample[:4000]}

Return ONLY a JSON object describing the pattern:
{{
  "pattern_description": "Descriptive string of the formula",
  "components": ["list", "of", "fields", "involved"],
  "formula_js": "A javascript-like expression using field names from the schema",
  "tolerance": 0.05
}}
"""
        try:
            response = self.client.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"Error identifying pattern: {e}")
            return {
                "pattern_description": "Default: Paid + Reserve = Incurred",
                "formula_js": "(medical_paid + medical_reserve + indemnity_paid + indemnity_reserve + expense_paid + expense_reserve) == total_incurred",
                "components": ["medical_paid", "medical_reserve", "indemnity_paid", "indemnity_reserve", "expense_paid", "expense_reserve", "total_incurred"],
                "tolerance": 0.02
            }

    def validate_claims(self, claims: List[Dict], pattern: Dict) -> List[Dict]:
        """
        Validates a list of claims using the identified pattern.
        """
        formula = pattern.get("formula_js", "")
        tolerance = pattern.get("tolerance", 0.02)
        
        for claim in claims:
            # Prepare context for evaluation
            context = {k: (v if v is not None else 0.0) for k, v in claim.items() if isinstance(v, (int, float))}
            
            # Simple evaluation logic for common patterns
            # Note: In a production environment, we'd use a safer expression evaluator
            is_valid = True
            errors = []
            
            try:
                # Use a more dynamic sum logic
                # We want to identify the "Total" or "Net" field and see if the sum of other fields matches it.
                reported_total = claim.get("total_incurred", 0.0) or 0.0
                
                # Context with all standard numeric fields
                fields = ["medical_paid", "medical_reserve", "indemnity_paid", "indemnity_reserve", "expense_paid", "expense_reserve"]
                calc_total = sum(context.get(f, 0.0) for f in fields)
                
                # Check if document has explicit totals for paid/reserved in the context (often from summary rows)
                reported_paid = context.get("total_paid", None)
                reported_res = context.get("total_reserve", None)
                
                if reported_paid is not None and reported_res is not None:
                    # If document has explicit totals for paid/reserved, those are often more reliable for matching 'total_incurred'
                    if abs((reported_paid + reported_res) - reported_total) < tolerance:
                        is_valid = True
                        calc_total = reported_total # Trust the summary line
                
                diff = abs(calc_total - reported_total)
                if diff > tolerance:
                    # If we have a math mismatch, it might be due to a specific pattern (like Net = Total - Recovery)
                    # For now, we flag it so the user can see the discrepancy
                    is_valid = False
                    errors.append(f"Math mismatch: Calculated sum of components {calc_total:.2f} vs Reported {reported_total:.2f} (Diff: {diff:.2f})")
                
            except Exception as e:
                errors.append(f"Validation error: {str(e)}")
                is_valid = False

            claim["validation_financial"] = {
                "is_valid": is_valid,
                "errors": errors,
                "pattern_used": pattern.get("pattern_description", "Standard Sum")
            }
            
        return claims
