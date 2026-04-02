import pandas as pd

def verify_split_members():
    dfm = pd.read_excel(r'unified_outputs\Mutual Of Omaha- Greener Acres- March 26 _merged_report_v2.xlsx')
    
    # List of members identified as split across pages in the images
    split_members = [
        ("Jackson", "Milien"),
        ("Edward", "Romero"),
        ("Ruben", "Vega"),
        ("Jacquelyn", "Kleinhoffer"),
        ("Louis", "Lamacchia")
    ]
    
    print("="*80)
    print("AUDIT OF SPLIT-PAGE MEMBERS")
    print("="*80)
    
    for fn, ln in split_members:
        mask = (dfm['LASTNAME'].str.contains(ln, na=False, case=False)) & \
               (dfm['FIRSTNAME'].str.contains(fn, na=False, case=False))
        rows = dfm[mask].sort_values(by='PLAN_NAME')
        
        print(f"\nMember: {fn} {ln}")
        if rows.empty:
            print("  [!] NOT FOUND IN MERGED REPORT")
        else:
            print(f"  Plans Found: {len(rows)}")
            for _, r in rows.iterrows():
                print(f"    - {r['PLAN_NAME']:20} | curr={r['CURRENT_PREMIUM']:6} | adj={r['ADJUSTMENT_PREMIUM']}")
            
            total = rows['CURRENT_PREMIUM'].sum()
            print(f"  Total Current Premium: ${total:.2f}")

if __name__ == "__main__":
    verify_split_members()
