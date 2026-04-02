"""
Comprehensive verification of MOO extraction: chunks vs merged report.
"""
import pandas as pd
import numpy as np

BASE = r"unified_outputs"
CHUNKS = [
    f"{BASE}\\Mutual Of Omaha- Greener Acres- March 26 _chunk_1_invoice.xlsx",
    f"{BASE}\\Mutual Of Omaha- Greener Acres- March 26 _chunk_2_invoice.xlsx",
    f"{BASE}\\Mutual Of Omaha- Greener Acres- March 26 _chunk_3_invoice.xlsx",
]
MERGED = f"{BASE}\\Mutual Of Omaha- Greener Acres- March 26 _merged_report_v2.xlsx"

def clean_df(df):
    """Remove TOTAL/aggregate/placeholder rows."""
    agg_kws = ["PARTICIPANT PREMIUM", "PARTICIPANT ADJUSTMENT", "CURRENT PREMIUM",
               "LIFE INSURANCE BENEFITS", "AMOUNT DUE", "BILL BRANCH", "REPORTED INVOICE"]
    mask_total = df["PLAN_NAME"].str.contains("TOTAL", case=False, na=False)
    mask_agg = df["PLAN_NAME"].apply(lambda x: any(kw in str(x).upper() for kw in agg_kws))
    mask_placeholder = df["FIRSTNAME"].str.contains("FROM_PREVIOUS", case=False, na=False)
    mask_placeholder |= df["LASTNAME"].str.upper().eq("MISSING")
    return df[~(mask_total | mask_agg | mask_placeholder)].copy()

print("=" * 80)
print("MOO EXTRACTION VERIFICATION")
print("=" * 80)

# Load chunks
chunk_dfs = []
for i, path in enumerate(CHUNKS, 1):
    df = pd.read_excel(path)
    print(f"\n--- Chunk {i}: {len(df)} raw rows ---")
    
    # Check for placeholders
    ph = df[df["FIRSTNAME"].str.contains("FROM_PREVIOUS", case=False, na=False)]
    if len(ph) > 0:
        print(f"  [!] {len(ph)} FROM_PREVIOUS_PAGE placeholder rows:")
        for _, r in ph.iterrows():
            print(f"      {r['PLAN_NAME']} | curr={r.get('CURRENT_PREMIUM')} | adj={r.get('ADJUSTMENT_PREMIUM')}")
    
    # Check for TOTAL rows
    tot = df[df["PLAN_NAME"].str.contains("TOTAL", case=False, na=False)]
    if len(tot) > 0:
        print(f"  [i] {len(tot)} TOTAL row(s)")
    
    # Clean
    cdf = clean_df(df)
    print(f"  Cleaned rows: {len(cdf)}")
    
    # Unique members
    members = cdf.groupby(["LASTNAME", "FIRSTNAME"]).size().reset_index(name="plans")
    print(f"  Unique members: {len(members)}")
    
    # Premium sums
    curr_sum = cdf["CURRENT_PREMIUM"].sum()
    adj_sum = cdf["ADJUSTMENT_PREMIUM"].sum()
    print(f"  CURRENT_PREMIUM sum: ${curr_sum:.2f}")
    print(f"  ADJUSTMENT_PREMIUM sum: ${adj_sum:.2f}")
    
    # First and last member
    if len(cdf) > 0:
        first = cdf.iloc[0]
        last = cdf.iloc[-1]
        print(f"  First member: {first['FIRSTNAME']} {first['LASTNAME']} ({first['PLAN_NAME']})")
        print(f"  Last member:  {last['FIRSTNAME']} {last['LASTNAME']} ({last['PLAN_NAME']})")
    
    chunk_dfs.append(cdf)

# Combine all clean chunks
all_chunks = pd.concat(chunk_dfs, ignore_index=True)
print(f"\n{'=' * 80}")
print(f"COMBINED CHUNKS (cleaned): {len(all_chunks)} rows")
all_members = all_chunks.groupby(["LASTNAME", "FIRSTNAME"]).size().reset_index(name="plans")
print(f"  Unique members across all chunks: {len(all_members)}")
print(f"  CURRENT_PREMIUM total: ${all_chunks['CURRENT_PREMIUM'].sum():.2f}")
print(f"  ADJUSTMENT_PREMIUM total: ${all_chunks['ADJUSTMENT_PREMIUM'].sum():.2f}")

# Load merged
print(f"\n{'=' * 80}")
print("MERGED REPORT")
print("=" * 80)
merged = pd.read_excel(MERGED)
print(f"Raw rows: {len(merged)}")

# Check for remaining issues
ph_m = merged[merged["FIRSTNAME"].str.contains("FROM_PREVIOUS", case=False, na=False)]
print(f"FROM_PREVIOUS_PAGE rows: {len(ph_m)}")

agg_kws = ["PARTICIPANT PREMIUM", "PARTICIPANT ADJUSTMENT", "CURRENT PREMIUM", "LIFE INSURANCE BENEFITS"]
agg_m = merged[merged["PLAN_NAME"].apply(lambda x: any(kw in str(x).upper() for kw in agg_kws))]
print(f"Aggregate rows: {len(agg_m)}")
if len(agg_m) > 0:
    for _, r in agg_m.iterrows():
        print(f"  [!] {r['FIRSTNAME']} {r['LASTNAME']} | {r['PLAN_NAME']} | curr={r.get('CURRENT_PREMIUM')}")

tot_m = merged[merged["PLAN_NAME"].str.contains("TOTAL", case=False, na=False)]
print(f"TOTAL rows: {len(tot_m)}")
for _, r in tot_m.iterrows():
    print(f"  {r['PLAN_NAME']} | curr={r.get('CURRENT_PREMIUM')}")

# Clean merged
merged_clean = clean_df(merged)
print(f"Cleaned rows: {len(merged_clean)}")
merged_members = merged_clean.groupby(["LASTNAME", "FIRSTNAME"]).size().reset_index(name="plans")
print(f"Unique members: {len(merged_members)}")
print(f"CURRENT_PREMIUM total: ${merged_clean['CURRENT_PREMIUM'].sum():.2f}")
print(f"ADJUSTMENT_PREMIUM total: ${merged_clean['ADJUSTMENT_PREMIUM'].sum():.2f}")

# Cross-check: members in chunks but not in merged
print(f"\n{'=' * 80}")
print("CROSS-CHECK: CHUNKS vs MERGED")
print("=" * 80)

chunk_member_set = set(zip(all_members["LASTNAME"].str.lower(), all_members["FIRSTNAME"].str.lower()))
merged_member_set = set(zip(merged_members["LASTNAME"].str.lower(), merged_members["FIRSTNAME"].str.lower()))

missing_from_merged = chunk_member_set - merged_member_set
extra_in_merged = merged_member_set - chunk_member_set

if missing_from_merged:
    print(f"\n[!!!] {len(missing_from_merged)} MEMBERS IN CHUNKS BUT MISSING FROM MERGED:")
    for ln, fn in sorted(missing_from_merged):
        # Find their plans in chunks
        mask = (all_chunks["LASTNAME"].str.lower() == ln) & (all_chunks["FIRSTNAME"].str.lower() == fn)
        plans = all_chunks[mask]
        total = plans["CURRENT_PREMIUM"].sum()
        print(f"  {fn.title()} {ln.title()} - {len(plans)} plans, ${total:.2f}")
else:
    print("\n[OK] All chunk members found in merged report.")

if extra_in_merged:
    print(f"\n[?] {len(extra_in_merged)} MEMBERS IN MERGED BUT NOT IN CHUNKS:")
    for ln, fn in sorted(extra_in_merged):
        print(f"  {fn.title()} {ln.title()}")
else:
    print("[OK] No extra members in merged report.")

# Duplicate detection
print(f"\n{'=' * 80}")
print("DUPLICATE DETECTION (merged)")
print("=" * 80)
dup_key = merged_clean.groupby(["LASTNAME", "FIRSTNAME", "PLAN_NAME"]).size().reset_index(name="count")
dups = dup_key[dup_key["count"] > 1]
if len(dups) > 0:
    print(f"[!] {len(dups)} duplicate member+plan combinations:")
    for _, r in dups.iterrows():
        print(f"  {r['FIRSTNAME']} {r['LASTNAME']} | {r['PLAN_NAME']} x{r['count']}")
else:
    print("[OK] No duplicate member+plan combinations.")

# Premium comparison
print(f"\n{'=' * 80}")
print("PREMIUM TOTALS COMPARISON")
print("=" * 80)
c_curr = all_chunks["CURRENT_PREMIUM"].sum()
m_curr = merged_clean["CURRENT_PREMIUM"].sum()
c_adj = all_chunks["ADJUSTMENT_PREMIUM"].sum()
m_adj = merged_clean["ADJUSTMENT_PREMIUM"].sum()

print(f"  CURRENT_PREMIUM  - Chunks: ${c_curr:.2f}  Merged: ${m_curr:.2f}  Diff: ${m_curr - c_curr:.2f}")
print(f"  ADJUSTMENT_PREMIUM - Chunks: ${c_adj:.2f}  Merged: ${m_adj:.2f}  Diff: ${m_adj - c_adj:.2f}")

if abs(m_curr - c_curr) < 0.01 and abs(m_adj - c_adj) < 0.01:
    print("  [OK] Premium totals match!")
else:
    print("  [!!!] PREMIUM MISMATCH - investigating...")
    # Find per-member differences
    for (ln, fn), grp in all_chunks.groupby(["LASTNAME", "FIRSTNAME"]):
        chunk_total = grp["CURRENT_PREMIUM"].sum()
        m_mask = (merged_clean["LASTNAME"] == ln) & (merged_clean["FIRSTNAME"] == fn)
        merged_total = merged_clean[m_mask]["CURRENT_PREMIUM"].sum()
        diff = merged_total - chunk_total
        if abs(diff) > 0.01:
            print(f"    {fn} {ln}: chunks=${chunk_total:.2f} merged=${merged_total:.2f} diff=${diff:.2f}")

print(f"\n{'=' * 80}")
print("VERIFICATION COMPLETE")
print("=" * 80)
