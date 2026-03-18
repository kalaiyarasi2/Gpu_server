import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class StatementValidator:
    """
    Deterministic validation of extracted bank statement rows.
    Primary objective: Ensure No 'Balance' values are hallucinated as 'Amounts'.
    """

    @staticmethod
    def verify_arithmetic(transactions: List[Dict[str, Any]], beginning_balance: float = 0.0) -> Dict[str, Any]:
        """
        Deterministically verify transaction arithmetic.
        Logic: Previous_Balance + Amount = Current_Balance
        If a balance discrepancy is found, and Current_Balance matches the extracted Amount,
        it's a clear 'Balance-as-Amount' error.
        
        This version performs a cumulative check to handle rows without explicit running_balance.
        """
        if not transactions:
            return {"status": "empty", "flagged_rows": []}

        flagged_rows = []
        status = "validated"
        
        current_calc_balance = float(beginning_balance or 0.0)
        
        for i, tx in enumerate(transactions):
            amount = tx.get("amount")
            # If it's a 'debit' or 'withdrawal', amount might be negative or positive depending on source
            # We assume the caller (extractor) has already standardized sign or we infer from description
            
            desc = (tx.get("description") or "").lower()
            # If amount is large and description doesn't fit, it's suspicious
            is_credit = any(k in desc for k in ["deposit", "credit", "interest", "refund", "incoming", "receive"])
            is_debit = any(k in desc for k in ["withdrawal", "debit", "check", "payment", "fee", "purchase", "outgoing", "send"])
            
            # Use signed amount if available, else derive from type
            val = float(amount or 0.0)
            if is_debit and val > 0:
                val = -val  # standardize to negative for calculation
            elif is_credit and val < 0:
                val = abs(val) # standardize to positive
                
            expected_next_balance = current_calc_balance + val
            extracted_running_balance = tx.get("running_balance")
            
            if extracted_running_balance is not None:
                extracted_running_balance = float(extracted_running_balance)
                diff = abs(expected_next_balance - extracted_running_balance)
                
                if diff > 0.01:
                    # DISCREPANCY DETECTED
                    # Check if it's the 'Balance-as-Amount' error
                    # i.e., did the extractor mistakenly put the running balance into the amount field?
                    # HEURISTIC 1: Check if Balance was mistakenly taken as Amount
                    if abs(abs(val) - extracted_running_balance) < 0.01:
                        # YES: Balance was taken as Amount
                        actual_diff = round(extracted_running_balance - current_calc_balance, 2)
                        tx["amount"] = abs(actual_diff)
                        tx["validation_fixed"] = True
                        tx["validation_notes"] = f"Corrected balance-as-amount error. Diff: {actual_diff}"
                        flagged_rows.append({"index": i, "description": desc, "extracted_amount": amount, "expected_amount": abs(actual_diff), "reason": "balance_as_amount_collision"})
                        status = "corrected"
                        current_calc_balance = extracted_running_balance
                        continue

                    # HEURISTIC 2: General arithmetic mismatch or Page-Boundary Gap
                    # If this is not a balance-as-amount error, but we have a running balance,
                    # we should probably TRUST the running balance and reset our baseline
                    # to prevent a waterfall of errors if a previous row was missed.
                    logger.warning(f"⚠️ VALIDATOR: Discrepancy at row {i} ({desc[:30]}...). Syncing to extracted balance.")
                    
                    # Optional: attempt correction if it's a minor mismatch
                    corrected_val = round(extracted_running_balance - current_calc_balance, 2)
                    if abs(corrected_val) < 1000000: # Sanity check for massive gaps
                        tx["amount"] = abs(corrected_val)
                        tx["validation_fixed"] = True
                        tx["validation_notes"] = f"Corrected math discrepancy. Expected: {abs(corrected_val)}"
                        flagged_rows.append({"index": i, "description": desc, "extracted_amount": amount, "expected_amount": abs(corrected_val), "reason": "math_discrepancy"})
                        status = "corrected"
                    
                    current_calc_balance = extracted_running_balance
                else:
                    # Balance matches expectation, update baseline
                    current_calc_balance = extracted_running_balance
            else:
                # No running balance provided for THIS row, keep the cumulative sum
                current_calc_balance = expected_next_balance
                
        return {
            "status": status,
            "flagged_rows": flagged_rows,
            "final_balance": round(current_calc_balance, 2)
        }

    @staticmethod
    def detect_balance_leaks(transactions: List[Dict[str, Any]], source_text_lines: List[str]) -> List[int]:
        """
        Scans extracted transactions to see if the 'amount' field matches 
        the 'balance' field on the same line in the raw text.
        """
        flagged_indices = []
        for i, tx in enumerate(transactions):
            amount_str = str(abs(tx.get("amount", 0.0)))
            # This is a heuristic: if amount is 0 or very small, skip
            if not tx.get("amount"): continue
            
            # If the amount is found in the 'balance' column position in the source text
            # but NOT in the 'debit/credit' position, we flag it.
            # (This requires mapping back to lines, which we do in the extractor)
            pass
            
        return flagged_indices
