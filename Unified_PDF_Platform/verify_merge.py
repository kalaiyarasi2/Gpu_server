import pandas as pd

df = pd.read_excel(r'unified_outputs\Mutual Of Omaha- Greener Acres- March 26 _merged_report_v2.xlsx')
print(f'Total rows: {len(df)}')

# Check for orphans
orphans = df[df['FIRSTNAME'].str.contains('FROM_PREVIOUS', case=False, na=False)]
print(f'Orphan rows remaining: {len(orphans)}')

# Check for aggregates
agg_kws = ['PARTICIPANT PREMIUM', 'PARTICIPANT ADJUSTMENT', 'CURRENT PREMIUM', 'LIFE INSURANCE BENEFITS']
agg = df[df['PLAN_NAME'].apply(lambda x: any(kw in str(x).upper() for kw in agg_kws))]
print(f'Aggregate rows remaining: {len(agg)}')

# Check Zelenak
z = df[df['LASTNAME'].str.contains('Zelenak', case=False, na=False)]
print()
print(f'Zelenak rows: {len(z)}')
for _, r in z.iterrows():
    print(f"  {r['PLAN_NAME']} | curr={r['CURRENT_PREMIUM']} | adj={r['ADJUSTMENT_PREMIUM']}")

# Check Pina
p = df[df['LASTNAME'].str.contains('Pina', case=False, na=False)]
print()
print(f'Pina rows: {len(p)}')
for _, r in p.iterrows():
    print(f"  {r['PLAN_NAME']} | curr={r['CURRENT_PREMIUM']} | adj={r['ADJUSTMENT_PREMIUM']}")

print()
print('=== GRAND TOTAL ROW ===')
total_row = df[df['PLAN_NAME'].str.contains('TOTAL', case=False, na=False)]
for _, r in total_row.iterrows():
    print(f"  {r['PLAN_NAME']} | curr={r['CURRENT_PREMIUM']}")
