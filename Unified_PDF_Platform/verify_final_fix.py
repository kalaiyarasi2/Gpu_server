import os
import json
import pandas as pd
from unified_router import UnifiedRouter

# Force the router to use the NEW V2 chunks for re-merging
base_dir = r"c:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform"
output_dir = os.path.join(base_dir, "unified_outputs")

v2_json_files = [
    os.path.join(output_dir, "Mutual Of Omaha- Greener Acres- March 26 _chunk_1_invoice_v2.xlsx"),
    os.path.join(output_dir, "Mutual Of Omaha- Greener Acres- March 26 _chunk_2_invoice_v2.xlsx"),
    os.path.join(output_dir, "Mutual Of Omaha- Greener Acres- March 26 _chunk_3_invoice_v2.xlsx")
]

print(f"Merging {len(v2_json_files)} context-aware V2 chunks...")

router = UnifiedRouter()
output_xlsx = os.path.join(output_dir, "Mutual Of Omaha- Greener Acres- March 26 _merged_v3_context_aware.xlsx")
merged_df = router._merge_invoice_results(v2_json_files, output_xlsx)
merged_df.to_excel(output_xlsx, index=False)

print(f"\nFinal Merged Document: {output_xlsx}")
print(f"Total Rows: {len(merged_df)}")

# VERIFICATION: Check for Jackson Milien and Edward Romero
print("-" * 30)
search_names = ["ROMERO", "MILIEN", "JACKSON", "EDWARD"]
matches = merged_df[merged_df.apply(lambda row: any(name in str(row['LASTNAME']).upper() or name in str(row['FIRSTNAME']).upper() for name in search_names), axis=1)]

if not matches.empty:
    print("Verification Matches Found:")
    print(matches[['FIRSTNAME', 'LASTNAME', 'MEMBERID', 'PLAN_NAME', 'CURRENT_PREMIUM', 'ADJUSTMENT_PREMIUM', 'SOURCE_FILE']])
else:
    print("NO MATCHES FOUND for target names. Check re-extraction quality.")

# Check for residual placeholders
placeholders = merged_df[merged_df['FIRSTNAME'] == "FROM_PREVIOUS_PAGE"]
if not placeholders.empty:
    print(f"\nWARNING: Found {len(placeholders)} UNREPAIRED placeholder rows.")
    print(placeholders[['PLAN_NAME', 'CURRENT_PREMIUM', 'SOURCE_FILE']])
else:
    print("\nSUCCESS: 0 placeholder rows remaining (all repaired or context-inherited).")
