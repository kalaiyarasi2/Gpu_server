import pandas as pd
import sys

def audit_member(name):
    df1 = pd.read_excel(r'unified_outputs\Mutual Of Omaha- Greener Acres- March 26 _chunk_1_invoice.xlsx')
    df2 = pd.read_excel(r'unified_outputs\Mutual Of Omaha- Greener Acres- March 26 _chunk_2_invoice.xlsx')
    df3 = pd.read_excel(r'unified_outputs\Mutual Of Omaha- Greener Acres- March 26 _chunk_3_invoice.xlsx')
    dfm = pd.read_excel(r'unified_outputs\Mutual Of Omaha- Greener Acres- March 26 _merged_report_v2.xlsx')

    print(f"AUDIT FOR: {name}\n")
    for i, df in enumerate([df1, df2, df3], 1):
        found = df[df['LASTNAME'].str.contains(name, na=False, case=False)]
        if not found.empty:
            print(f"CHUNK {i} ROWS:")
            print(found[['FIRSTNAME', 'LASTNAME', 'PLAN_NAME', 'CURRENT_PREMIUM', 'ADJUSTMENT_PREMIUM', 'COVERAGE', 'MEMBERID']].to_string())
            print(f"Subtotal: {found['CURRENT_PREMIUM'].sum():.2f}")
            print("-" * 40)

    found_m = dfm[dfm['LASTNAME'].str.contains(name, na=False, case=False)]
    if not found_m.empty:
        print("MERGED ROWS:")
        print(found_m[['FIRSTNAME', 'LASTNAME', 'PLAN_NAME', 'CURRENT_PREMIUM', 'ADJUSTMENT_PREMIUM', 'COVERAGE', 'MEMBERID']].to_string())
        print(f"Grand Total: {found_m['CURRENT_PREMIUM'].sum():.2f}")

if __name__ == "__main__":
    audit_member("Milien")
    print("\n" + "="*80 + "\n")
    audit_member("Zelenak")
