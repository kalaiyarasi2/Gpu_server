import json
import os

json_path = r"c:\Users\INT002\pdf_extractor\Unified_PDF_Platform\unified_outputs\Aetna Bar louie (BLH) Feb-25 Inv_invoice.json"

if os.path.exists(json_path):
    with open(json_path, "r") as f:
        data = json.load(f)
    
    print(f"Total items: {len(data)}")
    
    # Check for duplicate SSNs
    ssn_counts = {}
    for i, row in enumerate(data):
        ssn = row.get('SSN')
        if ssn:
            if ssn not in ssn_counts:
                ssn_counts[ssn] = []
            ssn_counts[ssn].append(i)
            
    duplicates = {s: indices for s, indices in ssn_counts.items() if len(indices) > 1}
    
    print(f"Found {len(duplicates)} SSNs with multiple rows.")
    for ssn, indices in duplicates.items():
        print(f"\nSSN {ssn}:")
        for idx in indices:
            row = data[idx]
            print(f"  Row {idx}: {row.get('LASTNAME')}, {row.get('FIRSTNAME')} | Plan: {row.get('PLAN_NAME')} | Current: {row.get('CURRENT_PREMIUM')} | Adj: {row.get('ADJUSTMENT_PREMIUM')}")

else:
    print(f"File not found: {json_path}")
