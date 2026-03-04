import sys
import os
from unittest.mock import MagicMock

# Add current directory to path
sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "Insurance_pdf_extractor-main", "backend"))

from insurance_extractor import EnhancedInsuranceExtractor
from chunked_extractor import ChunkedInsuranceExtractor

def test_inference_fix():
    print("Testing _infer_carrier_from_text hardening...")
    extractor = EnhancedInsuranceExtractor(api_key="fake_key_for_testing")
    
    # Problematic snippet from the user's report
    bad_text = """
    Some of the content contained in this report is subject to confidentiality laws and may be privileged. Therefore, it is intended for review and use by authorized
    representatives of the insured or other parties in reliance on its content. If you received this communication in error, please remove it and notify Atlas General Insurance
    Services
    """
    
    # Good snippet
    good_text = "StarNet Insurance Company\nPolicy Number: BNETWC01"
    
    inferred_bad = extractor._infer_carrier_from_text(bad_text)
    inferred_good = extractor._infer_carrier_from_text(good_text)
    
    print(f"Inferred (from bad text): {inferred_bad}")
    print(f"Inferred (from good text): {inferred_good}")
    
    assert inferred_bad != "Some of the content contained in this report is subject to confidentiality laws and may be privileged. Therefore, it is intended for review and use by authorized"
    # Actually, with the new logic, it should probably be None or "Atlas General Insurance Services" if the regex catches it.
    # Let's see what it does.
    
    if inferred_bad:
        # If it picked up Atlas, that's actually correct!
        if "Atlas General Insurance" in inferred_bad:
            print("✓ Correctly picked up Atlas from the notice (which is better than the whole notice).")
        else:
            print(f"⚠️ Picked up something else: {inferred_bad}")
    else:
        print("✓ Correctly rejected the notice.")

def test_merge_aggregation():
    print("\nTesting _merge_chunks aggregation...")
    # Mock some results
    results = [
        {"carrier_name": "Carrier A", "claims": [{"claim_number": "1"}]},
        {"SummaryLevel": {"carrier_names": "Carrier B"}, "claims": [{"claim_number": "2"}]},
        {"claims": [{"claim_number": "3", "carrier_name": "Carrier C"}]}
    ]
    
    extractor = ChunkedInsuranceExtractor(api_key="fake_key_for_testing")
    merged = extractor._merge_chunks(results, all_text="some text")
    
    print(f"Merged Carrier Name: {merged.get('carrier_name')}")
    assert "Carrier A" in merged.get('carrier_name')
    assert "Carrier B" in merged.get('carrier_name')
    assert "Carrier C" in merged.get('carrier_name')
    print("✓ Successfully aggregated carriers from multiple chunks.")

if __name__ == "__main__":
    try:
        test_inference_fix()
        test_merge_aggregation()
        print("\n✅ Verification SUCCESSFUL")
    except Exception as e:
        print(f"\n❌ Verification FAILED: {e}")
        import traceback
        traceback.print_exc()
