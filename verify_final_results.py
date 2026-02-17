import pandas as pd
import os

print("=== FINAL VERIFICATION ===")

# GIS 23 Verification
gis_path = 'GIS_23_final_verified_v9.xlsx'
if os.path.exists(gis_path):
    print(f"\n[GIS 23] Checking {gis_path}...")
    try:
        df_gis = pd.read_excel(gis_path)
        total_gis = df_gis['CURRENT_PREMIUM'].fillna(0).sum()
        count_gis = len(df_gis)
        unique_gis = len(df_gis.groupby(['LASTNAME', 'FIRSTNAME']))
        print(f"  Rows: {count_gis}")
        print(f"  Unique Employees: {unique_gis}")
        print(f"  Total Premium: ${total_gis:,.2f}")
        
        # Check for duplicates
        dups_gis = df_gis[df_gis.duplicated(subset=['LASTNAME', 'FIRSTNAME', 'PLAN_NAME'], keep=False)]
        print(f"  Duplicate (Name+Plan) Rows: {len(dups_gis)}")
        
        if 14900 < total_gis < 15100:
            print("  [SUCCESS] Total is within expected range (~$15,005.74)")
        else:
            print(f"  [WARNING] Total deviates from expected $15,005.74")
            
    except Exception as e:
        print(f"  [ERROR] Failed to read GIS 23: {e}")
else:
    print(f"\n[GIS 23] File not found: {gis_path} (Processing...)")

# Aetna Verification
aetna_path = 'Aetna_final_verified_v5.xlsx'
if os.path.exists(aetna_path):
    print(f"\n[Aetna] Checking {aetna_path}...")
    try:
        df_aetna = pd.read_excel(aetna_path)
        cur_aetna = df_aetna['CURRENT_PREMIUM'].sum()
        adj_aetna = df_aetna['ADJUSTMENT_PREMIUM'].sum()
        count_aetna = len(df_aetna[df_aetna['CURRENT_PREMIUM'].notna() & (df_aetna['CURRENT_PREMIUM'] != 0)])
        
        print(f"  Current Premium Rows: {count_aetna}")
        print(f"  Total Current Premium: ${cur_aetna:,.2f}")
        print(f"  Total Adjustment Premium: ${adj_aetna:,.2f}")
        
        # Check specifically for negative adjustments
        neg_adj = df_aetna[df_aetna['ADJUSTMENT_PREMIUM'] < 0]
        print(f"  Negative Adjustment Rows: {len(neg_adj)}")
        if len(neg_adj) > 0:
             print("  [SUCCESS] Negative adjustments detected.")
        else:
             print("  [WARNING] No negative adjustments found (Check sign extraction).")

        # Check for specific known adjustments
        frierson = df_aetna[df_aetna['LASTNAME'] == 'Frierson']
        if not frierson.empty:
            print(f"  Frierson Adjustment: {frierson['ADJUSTMENT_PREMIUM'].values[0]}")
            
        # Check for duplicates in Aetna
        dups_aetna = df_aetna[df_aetna.duplicated(subset=['LASTNAME', 'FIRSTNAME', 'SSN'], keep=False)]
        print(f"  Duplicate (Name+SSN) Rows: {len(dups_aetna)}")
        if len(dups_aetna) > 0:
            print("  [INFO] Duplicate Rows Detail:")
            print(dups_aetna[['LASTNAME', 'FIRSTNAME', 'SSN', 'CURRENT_PREMIUM', 'ADJUSTMENT_PREMIUM']].to_string())

    except Exception as e:
        print(f"  [ERROR] Failed to read Aetna: {e}")
else:
    print(f"\n[Aetna] File not found: {aetna_path} (Processing...)")
