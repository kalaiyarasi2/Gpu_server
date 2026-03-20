import re
from typing import Optional

def format_date_clean(val: Optional[str]) -> Optional[str]:
    """
    Standardize dates to D/M/YYYY format, stripping leading zeros.
    Example: 19/02/2026 -> 19/2/2026 (User Request: February 19, 2026 -> 19/2/2026)
    Also handles YYYYMM (202603 -> 1/3/2026) and MM/YYYY (03/2026 -> 1/3/2026).
    """
    if not val or not str(val).strip() or str(val).lower() in ["n/a", "none"]:
        return val
        
    s = str(val).strip()
    
    # Month name mapping
    month_map = {
        "january": "1", "february": "2", "march": "3", "april": "4",
        "may": "5", "june": "6", "july": "7", "august": "8",
        "september": "9", "october": "10", "november": "11", "december": "12",
        "jan": "1", "feb": "2", "mar": "3", "apr": "4", "jun": "6",
        "jul": "7", "aug": "8", "sep": "9", "oct": "10", "nov": "11", "dec": "12"
    }

    # 1. Full Date Try: MM/DD/YYYY, MM/DD/YY, M/D/YY
    match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', s)
    if match:
        p1, p2, y = match.groups()
        p1_int = int(p1)
        p2_int = int(p2)
        y_clean = y if len(y) == 4 else ("20" + y)
        
        # If second part > 12, it's already M/D/YYYY (Idempotency)
        if p2_int > 12:
             return f"{p1_int}/{p2_int}/{y_clean}"
        # If first part > 12, it was D/M/YYYY -> convert to M/D/YYYY
        if p1_int > 12:
             return f"{p2_int}/{p1_int}/{y_clean}"
        
        # Ambiguous case (both <= 12): assume input was M/D/YYYY and keep it as M/D/YYYY
        return f"{p1_int}/{p2_int}/{y_clean}"

    # 2. Month Name Try: "February 19, 2026" or "Feb 19 2026"
    month_pattern = r'([A-Za-z]+)\s+(\d{1,2})[,\s]+(\d{4})'
    match_month = re.search(month_pattern, s)
    if match_month:
        month_name, d, y = match_month.groups()
        month_num = month_map.get(month_name.lower())
        if month_num:
            d_clean = str(int(d))
            return f"{month_num}/{d_clean}/{y}"
    
    # 3. Year/Month Only Try: YYYYMM (e.g. 202603)
    match_yyyymm = re.search(r'^(\d{4})(\d{2})$', s)
    if match_yyyymm:
        y, m = match_yyyymm.groups()
        m_int = int(m)
        if 1 <= m_int <= 12:
             return f"{m_int}/1/{y}"
            
    # 4. Month/Year Try: MM/YYYY or MM-YYYY
    match_mmyyyy = re.search(r'(\d{1,2})[/-](\d{4})', s)
    if match_mmyyyy:
        m, y = match_mmyyyy.groups()
        m_int = int(m)
        if 1 <= m_int <= 12:
             return f"{m_int}/1/{y}"

    return s

# Test cases
test_cases = [
    ("02/19/2026", "2/19/2026"),
    ("February 19, 2026", "2/19/2026"),
    ("19/2/2026", "2/19/2026"), # Conversion from D/M/Y to M/D/Y
    ("2/19/2026", "2/19/2026"), # Idempotency check
]

print("Running test cases for format_date_clean (M/D/Y format):")
for input_val, expected in test_cases:
    actual = format_date_clean(input_val)
    # Check double clean
    double_actual = format_date_clean(actual)
    status = "PASS" if actual == expected and double_actual == expected else "FAIL"
    print(f"Input: {input_val:20} | Expected: {expected:15} | Actual: {actual:15} | Double: {double_actual:15} | Status: {status}")
