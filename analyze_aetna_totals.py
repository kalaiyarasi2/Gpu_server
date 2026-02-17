import chardet
import re

json_path = "aetna_raw_text.txt"
with open(json_path, "rb") as f:
    rawdata = f.read()
    result = chardet.detect(rawdata)
    encoding = result['encoding']

text = rawdata.decode(encoding)

# Find subtotals
matches = re.findall(r'\$([0-9,]+\.[0-9]{2})\s+ACTIVE Subtotal', text)
print(f"Subtotals found: {matches}")
vals = [float(m.replace(",", "")) for m in matches]
print(f"Sum of ACTIVE Subtotals: {sum(vals):,.2f}")

# Find other subtotals (COBRA, etc)
matches_cobra = re.findall(r'\$([0-9,]+\.[0-9]{2})\s+COBRA Subtotal', text)
print(f"COBRA Subtotals found: {matches_cobra}")
vals_cobra = [float(m.replace(",", "")) for m in matches_cobra]
print(f"Sum of COBRA Subtotals: {sum(vals_cobra):,.2f}")

# Total check
total_current = sum(vals) + sum(vals_cobra)
print(f"Total current charges from subtotals: {total_current:,.2f}")
