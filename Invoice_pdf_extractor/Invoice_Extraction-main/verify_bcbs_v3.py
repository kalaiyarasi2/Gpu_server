import json
import os

def verify_bcbs(json_path):
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found")
        return
    
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    errors = []
    for i, r in enumerate(data):
        pn = str(r.get("PLAN_NAME") or "")
        pt = r.get("PLAN_TYPE")
        
        # Check if plan name starts with BLUECARE if it contains it
        if "BLUECARE" in pn.upper() and not pn.upper().startswith("BLUECARE"):
            errors.append(f"Row {i}: Plan Name does not start with BLUECARE - '{pn}'")
        
        # Check if location prefixes are stripped
        if any(prefix in pn.upper() for prefix in ["SAND", "MARV", "BEAC", "JAX"]):
            # Note: "SAND" or "MARV" might be part of a legitimate word, so we check for space or start
            if any(re.match(rf"^{prefix}\s", pn, re.IGNORECASE) for prefix in ["SAND", "MARV", "BEAC", "JAX"]):
                 errors.append(f"Row {i}: Location prefix not stripped - '{pn}'")

        # Check if PLAN_TYPE is null
        if pt is not None and str(pt).strip() != "":
            # Only count as error if it's a member row (excluding total rows which might have 'TOTAL')
            if r.get("MEMBERID"):
                errors.append(f"Row {i}: Plan Type should be null but is '{pt}'")
                
    if not errors:
        print(f"Success! All {len(data)} rows in {os.path.basename(json_path)} verified.")
    else:
        print(f"Found {len(errors)} issues in {os.path.basename(json_path)}:")
        for err in errors[:10]: # Print first 10
            print(f"  - {err}")
        if len(errors) > 10:
            print(f"  ... and {len(errors)-10} more.")

import re
verify_bcbs(r'c:\Users\INT002\updated_Extractor\pdf_extractor\Unified_PDF_Platform\unified_outputs\BCBS- Beachside- Greener Acres- div004- March 26_verify_v4.json')
verify_bcbs(r'c:\Users\INT002\updated_Extractor\pdf_extractor\Unified_PDF_Platform\unified_outputs\BCBS- Always Honest- div002- March 26_verify_v4.json')
