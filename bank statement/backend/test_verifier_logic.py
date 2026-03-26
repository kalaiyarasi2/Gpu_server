import importlib.util
from pathlib import Path

# Robust dynamic import
mod_path = Path(__file__).parent / "text_quality_verifier.py"
spec = importlib.util.spec_from_file_location("bank_verifier_test_local", str(mod_path))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
BankTextQualityVerifier = mod.BankTextQualityVerifier

TextQualityVerifier = BankTextQualityVerifier

verifier = TextQualityVerifier()

# Case 1: Good text (Mock of CC.pdf as it is now)
good_text = """
POST TRANS TRANSACTION DESCRIPTION REFERENCE AMOUNT
2/01 2/01 GOOGLE*ADS1262996254 SUPPORT.GOOGL CA 24803946033910006749109 44.48
2/02 2/03 IN *ASCENDEUS, LLC 734-2550575 MI 24692166033100048726153 180.00
2/02 2/03 LOWES #00734* YPSILANTI MI 24692166033100076942532 41.08
"""

# Case 2: Truncated text (Amounts missing)
bad_text = """
POST TRANS TRANSACTION DESCRIPTION REFERENCE AMOUNT
2/01 2/01 GOOGLE*ADS1262996254 SUPPORT.GOOGL CA
2/02 2/03 IN *ASCENDEUS, LLC 734-2550575 MI
2/02 2/03 LOWES #00734* YPSILANTI MI
"""

print(f"Good text detected as missing columns: {verifier.detect_missing_columns(good_text)}")
print(f"Bad text detected as missing columns: {verifier.detect_missing_columns(bad_text)}")
