
import json
import os

target = r'c:\Users\INT002\updated_Extractor\pdf_extractor\Unified_PDF_Platform\unified_outputs\UHC- MGSI- March 26 (1)_invoice_FINAL_STRICT_V2.json'

if not os.path.exists(target):
    print(f"File not found: {target}")
    exit(1)

with open(target, 'r') as f:
    data = json.load(f)

members = [row for row in data if row.get('LASTNAME') and 'TOTAL' not in str(row.get('PLAN_NAME', '')).upper()]
missing = [row for row in members if row.get('PLAN_TYPE') in [None, '', 'NAN', 'UNKNOWN']]

print(f"Total Member Rows Evaluated: {len(members)}")
print(f"Rows Missing Plan Type: {len(missing)}")

if missing:
    print("\nMissing Plan Type Details:")
    for r in missing:
        print(f"  - Member: {r.get('LASTNAME')}, Plan: {r.get('PLAN_NAME')}")
else:
    print("\nSUCCESS: All member rows have a valid PLAN_TYPE.")

# Check for specific plan types
types_found = {}
for r in members:
    pt = r.get('PLAN_TYPE')
    types_found[pt] = types_found.get(pt, 0) + 1

print("\nPlan Type Distribution:")
for pt, count in types_found.items():
    print(f"  - {pt}: {count}")
