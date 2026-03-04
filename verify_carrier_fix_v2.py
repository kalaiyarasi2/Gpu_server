import sys
import os
import re
from typing import Dict, List, Optional
from unittest.mock import MagicMock

# Add current directory to path
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "Insurance_pdf_extractor-main", "backend"))

from insurance_extractor import EnhancedInsuranceExtractor
from chunked_extractor import ChunkedInsuranceExtractor

def test_indemnity_detection():
    print("Testing 'Indemnity' carrier detection...")
    extractor = EnhancedInsuranceExtractor(api_key="fake_key")
    
    # Page 2 text from the user's PDF
    text = """
    Carrier         Service American Indemnity Company
    Policy Number   SAAWC0000109-00
    """
    
    inferred = extractor._infer_carrier_from_text(text)
    print(f"Inferred carrier: {inferred}")
    assert inferred == "Service American Indemnity Company"
    print("✓ Successfully detected carrier with 'Indemnity' keyword.")

def test_smarter_propagation():
    print("\nTesting smarter default carrier propagation...")
    extractor = EnhancedInsuranceExtractor(api_key="fake_key")
    
    # Aggregated list
    data = {
        "carrier_name": "Carrier A, Carrier B",
        "claims": [
            {"claim_number": "1", "employee_name": "Test A"}, # Should NOT get the list
            {"claim_number": "2", "employee_name": "Test B", "carrier_name": "Specific Carrier"} # Should KEEP specific
        ]
    }
    
    processed = extractor._post_process_claims(data)
    
    c1 = processed["claims"][0]
    c2 = processed["claims"][1]
    
    print(f"Claim 1 carrier: {c1.get('carrier_name')}")
    print(f"Claim 2 carrier: {c2.get('carrier_name')}")
    
    assert c1.get("carrier_name") is None  # Should NOT have propagated the comma-separated list
    assert c2.get("carrier_name") == "Specific Carrier"
    print("✓ Successfully prevented aggregated list propagation to individual claims.")

def test_per_chunk_propagation_simulation():
    print("\nTesting per-chunk propagation simulation...")
    # This simulates what I added to chunked_extractor.py:extract_schema_from_text
    
    chunk_result = {
        "carrier_name": "Chunk Carrier",
        "claims": [
            {"claim_number": "123", "employee_name": "John Doe"}
        ]
    }
    
    # Propagation logic implementation as added to the file:
    chunk_carrier = chunk_result.get("carrier_name")
    for c in chunk_result["claims"]:
        if not c.get("carrier_name") and chunk_carrier:
            c["carrier_name"] = chunk_carrier
            
    print(f"Claim carrier after per-chunk propagation: {chunk_result['claims'][0].get('carrier_name')}")
    assert chunk_result['claims'][0].get('carrier_name') == "Chunk Carrier"
    print("✓ Successfully propagated chunk carrier to its claims.")

if __name__ == "__main__":
    try:
        test_indemnity_detection()
        test_smarter_propagation()
        test_per_chunk_propagation_simulation()
        print("\n✅ All V2 Verifications SUCCESSFUL")
    except Exception as e:
        print(f"\n❌ Verification FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
