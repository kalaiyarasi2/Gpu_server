import json
import logging
from typing import Dict, Any

# Set up logging for validation errors
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def call_llm_deterministically(client: Any, prompt: str, model: str = "gpt-4o") -> Any:
    """
    Wraps OpenAI API calls with deterministic settings.
    Requires 'seed' parameter (available in newer openai client versions).
    """
    return client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.0,
        seed=42  # Ensures deterministic output
    )

def validate_claim_math(claim: Dict[str, Any]) -> bool:
    """
    Strictly validates financial integrity of a single claim.
    Returns True if math is valid, False otherwise.
    """
    try:
        # Helper to convert potential string numbers to float
        def to_float(val: Any) -> float:
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                return float(val.replace(',', '').replace('$', ''))
            return 0.0

        medical_paid = to_float(claim.get("medical_paid", 0.0))
        indemnity_paid = to_float(claim.get("indemnity_paid", 0.0))
        expense_paid = to_float(claim.get("expense_paid", 0.0))
        
        medical_reserve = to_float(claim.get("medical_reserve", 0.0))
        indemnity_reserve = to_float(claim.get("indemnity_reserve", 0.0))
        expense_reserve = to_float(claim.get("expense_reserve", 0.0))
        
        total_incurred = to_float(claim.get("total_incurred", 0.0))
        
        paid_sum = medical_paid + indemnity_paid + expense_paid
        reserve_sum = medical_reserve + indemnity_reserve + expense_reserve
        
        # Check if Paid + Reserves matches Incurred within a small tolerance
        if abs((paid_sum + reserve_sum) - total_incurred) > 0.1:
            logger.warning(f"Math check failed for claim {claim.get('claim_number')}: "
                           f"Paid({paid_sum}) + Res({reserve_sum}) != Incurred({total_incurred})")
            return False
        return True
    except Exception as e:
        logger.error(f"Validation error for claim {claim.get('claim_number')}: {e}")
        return False
