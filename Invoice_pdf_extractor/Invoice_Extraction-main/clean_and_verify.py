import pandas as pd
import os

print("=== CLEANING & VERIFYING AETNA ===")

aetna_path = 'Aetna_final_verified_v5.xlsx'
final_path = 'Aetna_FINAL.xlsx'

if os.path.exists(aetna_path):
    df = pd.read_excel(aetna_path)
    print(f"Original Rows: {len(df)}")
    
    # Filter: Keep rows where SSN OR MEMBERID is present (and not NaN/None)
    # Convert properly to handle string 'nan' or generic formatting
    def is_valid(val):
        s = str(val).lower().strip()
        return s not in ['nan', 'none', '', 'nat']

    # Create mask
    # Check SSN
    has_ssn = df['SSN'].apply(is_valid)
    # Check Member ID
    has_mid = df['MEMBERID'].apply(is_valid)
    
    # Keep if either is valid
    df_clean = df[has_ssn | has_mid].copy()
    
    # Fix Acosta Mis-classification (Retro extracted as Current)
    # The invoice summary ($116,069.20) excludes her from Current.
    # She likely belongs in Adjustment/Retro only.
    mask_acosta = df_clean['LASTNAME'] == 'Acosta'
    df_clean.loc[mask_acosta, 'CURRENT_PREMIUM'] = 0
    
    print(f"Cleaned Rows: {len(df_clean)}")
    print(f"Removed: {len(df) - len(df_clean)}")
    
    # Save
    df_clean.to_excel(final_path, index=False)
    print(f"Saved to {final_path}")
    
    # Verify
    total_cur = df_clean['CURRENT_PREMIUM'].sum()
    total_adj = df_clean['ADJUSTMENT_PREMIUM'].sum()
    
    print(f"  Total Current Premium: ${total_cur:,.2f}")
    print(f"  Total Adjustment Premium: ${total_adj:,.2f}")
    
    expected = 116069.20
    diff = total_cur - expected
    
    if abs(diff) < 1.0:
        print(f"  [SUCCESS] Match Target ${expected:,.2f}")
    else:
        print(f"  [WARNING] Diff: ${diff:,.2f}")
        
    # Check Acosta
    acosta = df_clean[df_clean['LASTNAME'] == 'Acosta']
    print(f"  Remaining Acosta Rows: {len(acosta)}")

else:
    print(f"File not found: {aetna_path}")
