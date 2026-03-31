import sys
import os
from pathlib import Path

# Add the project root to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from unified_router import UnifiedRouter

def test_identifier():
    router = UnifiedRouter()
    
    # Test cases for stronger identifier
    test_cases = [
        {
            "name": "Bank Statement Signal",
            "text": "Checking Account Summary\nBeginning Balance: $1,000.00\nEnding Balance: $2,000.00\nDeposits and other credits",
            "expected": "BANK_STATEMENT"
        },
        {
            "name": "Insurance Signal",
            "text": "Loss Run Report\nClaimant: John Doe\nDate of Loss: 01/01/2023\nIncurred Amount",
            "expected": "INSURANCE_CLAIMS"
        },
        {
            "name": "Legal Shield Benefit Invoice",
            "filename": "Legal Shield- Master- Feb 26.pdf",
            "text": "Legal Shield\nBenefit Invoice\nMaster Policy\nMember Premium: $50.00",
            "expected": "INVOICE"
        }
    ]
    
    for case in test_cases:
        filename = case.get("filename", "test.pdf")
        doc_type, reason = router._pre_classify(filename, ".pdf", case["text"])
        print(f"Test '{case['name']}': Expected {case['expected']}, Got {doc_type} ({reason})")
        assert doc_type == case["expected"]

if __name__ == "__main__":
    try:
        test_identifier()
        print("\n[OK] Identifier verification passed!")
    except Exception as e:
        print(f"\n[FAIL] Identifier verification failed: {e}")
        sys.exit(1)
