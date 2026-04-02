import os
import json
import pandas as pd
from unified_router import UnifiedRouter

# Final Merge of the high-fidelity v2/v3 chunks
base_dir = r"c:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform"
output_dir = os.path.join(base_dir, "unified_outputs")

v_files = [
    os.path.join(output_dir, "Mutual Of Omaha- Greener Acres- March 26 _chunk_1_invoice_v2.xlsx"),
    os.path.join(output_dir, "Mutual Of Omaha- Greener Acres- March 26 _chunk_2_invoice_v3.xlsx"),
    os.path.join(output_dir, "Mutual Of Omaha- Greener Acres- March 26 _chunk_3_invoice_v3.xlsx")
]

print(f"Merging {len(v_files)} finalized chunks...")

router = UnifiedRouter()
from pathlib import Path
output_xlsx = Path(os.path.join(output_dir, "Mutual Of Omaha- Greener Acres- March 26 _FINAL_DELIVERY.xlsx"))
success = router._merge_invoice_results(v_files, output_xlsx)

if success:
    merged_df = pd.read_excel(output_xlsx)
    print(f"\nFinal Merged Document: {output_xlsx}")
    print(f"Total Rows: {len(merged_df)}")
    
    # Audit for Edward Romero and Jackson Milien
    print("-" * 30)
    search_names = ["ROMERO", "MILIEN", "JACKSON", "EDWARD"]
    matches = merged_df[merged_df.apply(lambda row: any(name in str(row['LASTNAME']).upper() or name in str(row['FIRSTNAME']).upper() for name in search_names), axis=1)]
    
    if not matches.empty:
        print("Verification Matches Found:")
        print(matches[['FIRSTNAME', 'LASTNAME', 'MEMBERID', 'PLAN_NAME', 'CURRENT_PREMIUM', 'SOURCE_FILE']])
    else:
        print("NO MATCHES FOUND for target names.")
else:
    print("Merge failed.")
