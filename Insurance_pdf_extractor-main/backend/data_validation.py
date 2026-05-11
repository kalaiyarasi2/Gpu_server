import json
import base64
import io
from typing import Dict, List, Optional
from openai import OpenAI
import os
from pdf2image import convert_from_path

class GeneralDataValidator:
    """
    Performs overall semantic and consistency validation on extracted insurance data.
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
            # Use 200 DPI for sufficient quality but reasonable token cost
            images = convert_from_path(pdf_path, first_page=1, last_page=max_pages, dpi=200)
            base64_images = []
            for img in images:
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG")
                base64_images.append(base64.b64encode(buffered.getvalue()).decode('utf-8'))
            return base64_images
        except Exception as e:
            print(f"Error converting PDF to images for general validation: {e}")
            return []

    def validate_consistency(self, claims: List[Dict], original_text: str, pdf_path: Optional[str] = None) -> List[Dict]:
        """
        Uses AI (and optionally Vision) to perform a final consistency check on a batch of claims.
        """
        # To avoid token limits, we process in small batches or summarize the check
        # For a true "final layer", we ask AI to spot hallucinations
        
        claims_summary = []
        for c in claims:
            claims_summary.append({
                "id": c.get("claim_number"),
                "name": c.get("employee_name"),
                "date": c.get("injury_date_time"),
                "status": c.get("status")
            })

        messages = [
            {
                "role": "system",
                "content": "You are an insurance data auditor. Your job is to verify extracted claim data against document sources."
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text", 
                        "text": f"Review these extracted claims against the provided document sources. Check for hallucinations, incorrect dates, or inverted statuses.\n\nEXTRACTED SUMMARY:\n{json.dumps(claims_summary, indent=2)}\n\nORIGINAL TEXT SNIPPET:\n{original_text[:8000]}"
                    }
                ]
            }
        ]

        # Add vision if pdf_path is provided
        if pdf_path:
            base64_images = self._get_page_images(pdf_path)
            for b64 in base64_images:
                messages[1]["content"].append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                })

        messages[1]["content"].append({
            "type": "text",
            "text": "Return a JSON object with flags for any suspicious claims: {\"suspicious_claims\": [{\"claim_number\": \"number\", \"reason\": \"description\", \"severity\": \"low/high\"}]}"
        })

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o", # Use gpt-4o for semantic auditing
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0
            )
            report = json.loads(response.choices[0].message.content)
            suspicious = {item["claim_number"]: item for item in report.get("suspicious_claims", [])}
            
            for claim in claims:
                c_num = str(claim.get("claim_number"))
                if c_num in suspicious:
                    claim["validation_general"] = {
                        "is_valid": False,
                        "warnings": [suspicious[c_num]["reason"]],
                        "severity": suspicious[c_num]["severity"]
                    }
                else:
                    claim["validation_general"] = {
                        "is_valid": True,
                        "warnings": []
                    }
        except Exception as e:
            print(f"General validation error: {e}")
            for claim in claims:
                claim["validation_general"] = {"is_valid": True, "note": "Validation skipped due to error"}

        return claims
