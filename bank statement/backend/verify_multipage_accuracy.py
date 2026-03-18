import os
import json
import sys
from pathlib import Path

# Add backend to path for imports
sys.path.append(str(Path(__file__).parent))

def verify_multipage():
    print("🚀 Running Multi-Page Accuracy Verification...")
    from statement_extractor import StatementExtractor
    
    extractor = StatementExtractor()
    pdf_path = "sources/Bank statement (1).pdf"
    
    # Run the full pipeline
    print(f"🔄 Processing {pdf_path} (Full 5-page run)...")
    payload = extractor.process_pdf(pdf_path)
    
    with open("verify_result_multipage.json", "w") as f:
        json.dump(payload, f, indent=2)
    
    # payload is the wrapper; actual data is under 'data' key
    data = payload.get("data", payload)
    all_rows = data.get("deposits_and_credits", []) + data.get("checks_and_other_debits", [])
    
    # 1. Verify AETNA (Page 1 Fix)
    aetna_0202 = next((r for r in all_rows if "AETNA" in str(r.get("description")) and "02/02" in str(r.get("date"))), None)
    
    # 2. Verify Page 2 Transactions (from User's Screenshot)
    # 02/04 ACH WPS
    wps_0204 = next((r for r in all_rows if "WPS" in str(r.get("description")) and "02/04" in str(r.get("date")) and "Deposit" in str(r.get("description"))), None)
    
    # 02/04 LIVELY Withdrawal
    lively_0204 = next((r for r in all_rows if "LIVELY" in str(r.get("description")) and "02/04" in str(r.get("date"))), None)

    print("\n📊 Verification Results:")
    print("-" * 30)
    
    # Test 1: AETNA
    if aetna_0202 and abs(float(aetna_0202.get("amount", 0)) - 95.96) < 0.01:
        print("✅ PAGE 1: Found AETNA 02/02: Amount=95.96 (CORRECT)")
    else:
        print(f"❌ PAGE 1: AETNA 02/02 failed. Found amount={aetna_0202.get('amount') if aetna_0202 else 'NOT FOUND'}")

    # Test 2: WPS (Page 2)
    if wps_0204 and abs(float(wps_0204.get("amount", 0)) - 53.78) < 0.01:
        print("✅ PAGE 2: Found WPS 02/04: Amount=53.78 (CORRECT)")
    else:
        # Note: If it's 2231, it's still taking balance
        print(f"❌ PAGE 2: WPS 02/04 failed. Found amount={wps_0204.get('amount') if wps_0204 else 'NOT FOUND'}")

    # Test 3: LIVELY (Page 2)
    if lively_0204 and abs(float(lively_0204.get("amount", 0)) - 500.0) < 0.01:
        print("✅ PAGE 2: Found LIVELY 02/04: Amount=500.00 (CORRECT)")
    else:
        print(f"❌ PAGE 2: LIVELY 02/04 failed. Found amount={lively_0204.get('amount') if lively_0204 else 'NOT FOUND'}")
        
    # Count total corrected rows
    corrected_count = len([r for r in all_rows if r.get("validation_fixed")])
    print(f"\n✨ Total rows auto-corrected by Deterministic Engine: {corrected_count}")

if __name__ == "__main__":
    verify_multipage()
