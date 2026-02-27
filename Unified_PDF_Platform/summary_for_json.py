import json
import os
from typing import Dict, Optional, Any, List
from openai import OpenAI
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

class UniversalDocumentAnalyzer:
    """
    Analyzes various document types (Claims, Invoices, Receipts) and generates structured 
    summaries using OpenAI LLM.
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the UniversalDocumentAnalyzer with OpenAI API key.
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
        if not isinstance(data_json, (dict, list)):
            raise ValueError("Data must be a dictionary or a list")
        
        if not data_json and data_json != []:
            raise ValueError("Data is empty")
        
        return True
    
    def _calculate_claims_statistics(self, claims: list) -> dict:
        """
        Calculates key statistics from a list of insurance claims.
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

            # Financials
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
            
            if total_inc > 0 and total_inc < stats["lowest_incurred"]["amount"]:
                stats["lowest_incurred"] = {"claim_id": claim.get("claim_number", "N/A"), "amount": total_inc}

            # High Reserve Risk
            total_res = m_res + i_res + e_res
            if total_inc > 0 and (total_res / total_inc) > 0.5:
                stats["high_reserve_risk_claims"].append({
                    "claim_id": claim.get("claim_number", "N/A"),
                    "total_incurred": total_inc,
                    "reserves": total_res
                })

        if stats["lowest_incurred"]["amount"] == float('inf'):
            stats["lowest_incurred"]["amount"] = 0.0

        return stats

    def _calculate_invoice_statistics(self, data: list) -> dict:
        """
        Calculates key statistics from a list of invoices/items.
        """
        stats = {
            "total_items": len(data),
            "total_amount": 0.0,
            "vendor_breakdown": {},
            "date_range": {"earliest": None, "latest": None},
            "highest_value_item": {"description": "N/A", "amount": 0.0}
        }

        for item in data:
            amount = float(item.get("TOTAL_AMOUNT") or item.get("total_amount") or 0.0)
            stats["total_amount"] += amount
            
            vendor = item.get("VENDOR_NAME") or item.get("vendor_name") or "Unknown"
            stats["vendor_breakdown"][vendor] = stats["vendor_breakdown"].get(vendor, 0) + amount
            
            if amount > stats["highest_value_item"]["amount"]:
                stats["highest_value_item"] = {
                    "description": item.get("ITEM_DESCRIPTION") or item.get("item_description") or "N/A",
                    "amount": amount
                }
        
        return stats

    def generate_summary(
        self, 
        data_json: Any,
        model: str = "gpt-4.1-mini",
        temperature: float = 0.2
    ) -> str:
        """
        Detects document type and generates a context-aware summary.
        """
        self.validate_data(data_json)
        
        # Extract the list of items
        if isinstance(data_json, list):
            items = data_json
        elif isinstance(data_json, dict):
            # Try common keys
            items = data_json.get("claims") or data_json.get("invoices") or data_json.get("data") or []
        else:
            items = []

        if not items:
            return "No items found to summarize."

        # Detect Document Type
        is_claims = any(k in items[0] for k in ["claim_number", "medical_paid", "total_incurred"])
        doc_type = "Insurance Claims" if is_claims else "Invoices/General Data"

        if is_claims:
            stats = self._calculate_claims_statistics(items)
            total_paid = stats["total_medical_paid"] + stats["total_indemnity_paid"] + stats["total_expense_paid"]
            total_reserves = stats["total_medical_reserve"] + stats["total_indemnity_reserve"] + stats["total_expense_reserve"]
            
            context_stats = f"""
DOC_TYPE: Insurance Claims
STATISTICS:
- Total Claims: {stats['total_claims']}
- Status Breakdown: {json.dumps(stats['status_breakdown'])}
- Total Incurred: ${stats['total_incurred']:,.2f}
- Total Paid: ${total_paid:,.2f} (Medical: ${stats['total_medical_paid']:,.2f}, Indemnity: ${stats['total_indemnity_paid']:,.2f})
- Total Reserves: ${total_reserves:,.2f}
- Litigated: {stats['litigated_count']}, Reopened: {stats['reopened_count']}
"""
        else:
            stats = self._calculate_invoice_statistics(items)
            context_stats = f"""
DOC_TYPE: Invoices / Vendor Data
STATISTICS:
- Total Items: {stats['total_items']}
- Total Combined Amount: ${stats['total_amount']:,.2f}
- Highest Value Item: {stats['highest_value_item']['description']} (${stats['highest_value_item']['amount']:,.2f})
- Vendors involved: {", ".join(list(stats['vendor_breakdown'].keys())[:5])}
"""

        system_prompt = f"""
You are a professional data analyst AI. Analyze the provided {doc_type} JSON and generate a comprehensive summary.
Use the following statistics as the primary source of truth:

{context_stats}

Structure your report:
1. **Executive Summary** (Key totals and overview)
2. **Detailed Breakdown** (Patterns, vendor/carrier specifics, categories)
3. **Observations & Risk Flags** (High value items, anomalies, missing data)
4. **Action Items / Recommendations**

Format professionally with headers and bullet points.
"""
        
        user_prompt = f"Data to analyze:\n{json.dumps(data_json, indent=2)}"
        
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error generating summary: {str(e)}"

    def generate_claim_summary(self, data_json: Any) -> str:
        """Alias for backward compatibility with summary_api.py"""
        return self.generate_summary(data_json)
