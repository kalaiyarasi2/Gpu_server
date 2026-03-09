#!/usr/bin/env python3
"""
Test script to verify the fix for filter_claims_by_claim_year function.
This demonstrates the bug and shows how the fix resolves it.
"""

from typing import List, Dict, Tuple

# Original buggy function
def filter_claims_by_claim_year_buggy(
    claims: List[Dict],
    *,
    min_year_inclusive: int = 2022,
    keep_unknown_year: bool = True,
) -> tuple[list[Dict], list[Dict], list[Dict]]:
    """
    BUGGY VERSION: Incorrectly puts unknown year claims into excluded when keep_unknown_year=False
    """
    included: list[Dict] = []
    excluded: list[Dict] = []
    unknown: list[Dict] = []

    for c in claims or []:
        year = c.get("claim_year")
        if year is None:
            unknown.append(c)
            if keep_unknown_year:
                included.append(c)
            else:
                excluded.append(c)  # ❌ BUG: This is wrong!
            continue

        try:
            y = int(year)
        except Exception:
            # Treat non-parsable year as unknown
            unknown.append(c)
            if keep_unknown_year:
                included.append(c)
            else:
                excluded.append(c)  # ❌ BUG: This is wrong!
            continue

        if y >= int(min_year_inclusive):
            included.append(c)
        else:
            excluded.append(c)

    return included, excluded, unknown

# Fixed function
def filter_claims_by_claim_year_fixed(
    claims: List[Dict],
    *,
    min_year_inclusive: int = 2022,
    keep_unknown_year: bool = True,
) -> tuple[list[Dict], list[Dict], list[Dict]]:
    """
    FIXED VERSION: Correctly handles unknown year claims when keep_unknown_year=False
    """
    included: list[Dict] = []
    excluded: list[Dict] = []
    unknown: list[Dict] = []

    for c in claims or []:
        year = c.get("claim_year")
        if year is None:
            unknown.append(c)
            if keep_unknown_year:
                included.append(c)
            # If keep_unknown_year=False, don't add to included OR excluded
            continue

        try:
            y = int(year)
        except Exception:
            # Treat non-parsable year as unknown
            unknown.append(c)
            if keep_unknown_year:
                included.append(c)
            # If keep_unknown_year=False, don't add to included OR excluded
            continue

        if y >= int(min_year_inclusive):
            included.append(c)
        else:
            excluded.append(c)

    return included, excluded, unknown

def test_function():
    """Test both versions to demonstrate the bug and fix"""
    
    # Test data
    claims = [
        {"claim_number": "C001", "claim_year": 2021},  # Should be excluded (2021 < 2022)
        {"claim_number": "C002", "claim_year": 2022},  # Should be included (2022 >= 2022)
        {"claim_number": "C003", "claim_year": 2023},  # Should be included (2023 >= 2022)
        {"claim_number": "C004", "claim_year": None},  # Unknown year
        {"claim_number": "C005", "claim_year": "invalid"},  # Invalid year
    ]
    
    print("🧪 Testing filter_claims_by_claim_year function")
    print("=" * 60)
    
    # Test Case 1: keep_unknown_year=True (should work correctly in both versions)
    print("\n📋 Test Case 1: keep_unknown_year=True")
    print("-" * 40)
    
    buggy_included, buggy_excluded, buggy_unknown = filter_claims_by_claim_year_buggy(
        claims, keep_unknown_year=True
    )
    fixed_included, fixed_excluded, fixed_unknown = filter_claims_by_claim_year_fixed(
        claims, keep_unknown_year=True
    )
    
    print(f"Buggy version - Included: {[c['claim_number'] for c in buggy_included]}")
    print(f"Fixed version - Included: {[c['claim_number'] for c in fixed_included]}")
    print(f"Buggy version - Excluded: {[c['claim_number'] for c in buggy_excluded]}")
    print(f"Fixed version - Excluded: {[c['claim_number'] for c in fixed_excluded]}")
    print(f"Buggy version - Unknown: {[c['claim_number'] for c in buggy_unknown]}")
    print(f"Fixed version - Unknown: {[c['claim_number'] for c in fixed_unknown]}")
    
    # Test Case 2: keep_unknown_year=False (this is where the bug appears)
    print("\n📋 Test Case 2: keep_unknown_year=False")
    print("-" * 40)
    
    buggy_included, buggy_excluded, buggy_unknown = filter_claims_by_claim_year_buggy(
        claims, keep_unknown_year=False
    )
    fixed_included, fixed_excluded, fixed_unknown = filter_claims_by_claim_year_fixed(
        claims, keep_unknown_year=False
    )
    
    print(f"Buggy version - Included: {[c['claim_number'] for c in buggy_included]}")
    print(f"Fixed version - Included: {[c['claim_number'] for c in fixed_included]}")
    print(f"Buggy version - Excluded: {[c['claim_number'] for c in buggy_excluded]}")
    print(f"Fixed version - Excluded: {[c['claim_number'] for c in fixed_excluded]}")
    print(f"Buggy version - Unknown: {[c['claim_number'] for c in buggy_unknown]}")
    print(f"Fixed version - Unknown: {[c['claim_number'] for c in fixed_unknown]}")
    
    # Analysis
    print("\n🔍 Analysis")
    print("=" * 60)
    
    print("\n✅ Expected behavior when keep_unknown_year=False:")
    print("   - Included: Only claims with year >= 2022 (C002, C003)")
    print("   - Excluded: Only claims with year < 2022 (C001)")
    print("   - Unknown: Claims with unknown/invalid years (C004, C005)")
    
    print("\n❌ Buggy behavior when keep_unknown_year=False:")
    print("   - Unknown year claims (C004, C005) incorrectly appear in Excluded list")
    print("   - This violates the function's contract: excluded should only contain")
    print("     claims that are explicitly excluded due to year criteria")
    
    print("\n✅ Fixed behavior when keep_unknown_year=False:")
    print("   - Unknown year claims (C004, C005) correctly appear ONLY in Unknown list")
    print("   - They are neither included nor excluded, just unknown")
    
    # Verify the fix
    expected_included = ["C002", "C003"]
    expected_excluded = ["C001"]
    expected_unknown = ["C004", "C005"]
    
    fixed_included_names = [c['claim_number'] for c in fixed_included]
    fixed_excluded_names = [c['claim_number'] for c in fixed_excluded]
    fixed_unknown_names = [c['claim_number'] for c in fixed_unknown]
    
    print("\n🧪 Verification")
    print("-" * 40)
    print(f"Included correct: {sorted(fixed_included_names) == sorted(expected_included)}")
    print(f"Excluded correct: {sorted(fixed_excluded_names) == sorted(expected_excluded)}")
    print(f"Unknown correct: {sorted(fixed_unknown_names) == sorted(expected_unknown)}")
    
    if (sorted(fixed_included_names) == sorted(expected_included) and
        sorted(fixed_excluded_names) == sorted(expected_excluded) and
        sorted(fixed_unknown_names) == sorted(expected_unknown)):
        print("\n🎉 FIX VERIFIED: Function now works correctly!")
    else:
        print("\n❌ FIX FAILED: Function still has issues")

if __name__ == "__main__":
    test_function()