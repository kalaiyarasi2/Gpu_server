import os
import sys
import json
import logging
from typing import Dict, Any, Optional
from pathlib import Path

# Add Sales team - Copy root to sys.path to import core modules
cur = Path(__file__).resolve()
for p in cur.parents:
    if p.name == "Sales team - Copy" or (p / "core" / "prompt_cache_manager.py").exists():
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
        break

try:
    from core.prompt_cache_manager import cached_chat_completion
except ImportError:
    cached_chat_completion = None

# Set up logging for validation errors
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def call_llm_deterministically(
    client: Any,
    prompt: Optional[str] = None,
    model: str = "gpt-5.5",
    system_prompt: Optional[str] = None,
    user_content: Optional[str] = None,
    poc_name: str = "Insurance_pdf_extractor",
    file_name: str = "insurance_doc",
    step_name: str = "extraction",
    **kwargs
) -> Any:
    """
    Wraps OpenAI API calls with deterministic settings and automatic Prompt Caching.
    
    Supports:
    1. system_prompt + user_content (Optimal for Prompt Caching: 50% discount on static prefix)
    2. legacy single 'prompt' string
    """
    # 1. Optimal Prompt Caching Path: Explicit system_prompt + user_content
    if system_prompt and user_content and cached_chat_completion:
        return cached_chat_completion(
            client=client,
            static_system_prompt=system_prompt,
            dynamic_user_content=user_content,
            model=model,
            poc_name=poc_name,
            file_name=file_name,
            step_name=step_name,
            response_format={"type": "json_object"},
            seed=42,
            **kwargs
        )

    # 2. Legacy single prompt string path
    actual_prompt = prompt or user_content or ""
    if cached_chat_completion and len(actual_prompt) > 200:
        return cached_chat_completion(
            client=client,
            static_system_prompt="You are an expert insurance document extraction system. Follow all instructions verbatim and output strictly valid JSON.",
            dynamic_user_content=actual_prompt,
            model=model,
            poc_name=poc_name,
            file_name=file_name,
            step_name=step_name,
            response_format={"type": "json_object"},
            seed=42,
            **kwargs
        )

    # 3. Direct Fallback if module is not available
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": actual_prompt})

    return client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=1,
        seed=42,
        **kwargs
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
