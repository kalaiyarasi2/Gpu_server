import os
import pandas as pd
import re
from pathlib import Path
from typing import Dict, List, Optional
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Required fields for the final output
REQUIRED_FIELDS = [
    "INV_DATE",
    "INV_NUMBER",
    "BILLING_PERIOD",
    "LASTNAME",
    "FIRSTNAME",
    "MIDDLENAME",
    "SSN",
    "POLICYID",
    "MEMBERID",
    "PLAN_NAME",
    "PLAN_TYPE",
    "COVERAGE",
    "CURRENT_PREMIUM",
    "ADJUSTMENT_PREMIUM"
]

class StructuredExcelExtractor:
    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def clean_currency(self, val) -> float:
        if pd.isna(val) or val == "":
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        
        # Remove $, commas, and handle parentheses for negative numbers
        s = str(val).strip().replace("$", "").replace(",", "")
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        
        try:
            return float(s)
        except ValueError:
            return 0.0

    def split_fullname(self, name: str):
        if not isinstance(name, str) or not name.strip() or name.lower() == "nan":
            return None, None, None
        
        name = name.strip()
        if "," in name:
            parts = [p.strip() for p in name.split(",", 1)]
            last = parts[0]
            first_mid = parts[1] if len(parts) > 1 else ""
            fm_parts = first_mid.split()
            first = fm_parts[0] if fm_parts else ""
            mid = " ".join(fm_parts[1:]) if len(fm_parts) > 1 else None
            return last, first, mid
        else:
            parts = name.split()
            if len(parts) == 1:
                return parts[0], None, None
            if len(parts) == 2:
                return parts[1], parts[0], None
            return parts[-1], parts[0], " ".join(parts[1:-1])

    def get_ai_mapping(self, columns: List[str]) -> Dict[str, str]:
        """Use AI to map source columns to standard internal fields."""
        print(f"  [AI] Mapping columns: {columns}")
        prompt = f"""Map these CSV/Excel columns to our target fields.
        COLUMNS: {columns}
        TARGET FIELDS: {REQUIRED_FIELDS} + ['MEMBER_NAME', 'EMPLOYEE_ID']
        
        RULES:
        - Return ONLY JSON: {{"SourceColumn": "TargetField"}}
        - Mapping tips:
          'Member Name' -> 'MEMBER_NAME'
          'Member Id' -> 'MEMBERID'
          'Employee ID' -> 'EMPLOYEE_ID'
          'Accident Premium' -> 'ACCIDENT_PREMIUM'
          'Dental Premium' -> 'DENTAL_PREMIUM'
          'Vision Premium' -> 'VISION_PREMIUM'
          'STD Premium' -> 'STD_PREMIUM'
          'LTD Premium' -> 'LTD_PREMIUM'
          'Basic Term Life Premium' -> 'LIFE_PREMIUM'
          'Total Premium' -> 'TOTAL_PREMIUM'
          '.* Indicator' -> 'COVERAGE'
        """
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"  [ERR] AI Mapping failed: {e}")
            return {}

    def process_file(self, file_path: str) -> str:
        print(f"\n[StructuredExcelExtractor] Processing: {file_path}")
        file_path = Path(file_path)
        ext = file_path.suffix.lower()
        
        if ext == ".csv":
            try:
                # Use engine='python' for better robustness with varying field counts
                # Use utf-8-sig to automatically handle Byte Order Mark (BOM)
                df = pd.read_csv(file_path, header=None, engine='python', on_bad_lines='skip', encoding='utf-8-sig')
            except Exception as e:
                print(f"  [WARN] UTF-8 read failed ({e}). Trying latin-1 fallback...")
                df = pd.read_csv(file_path, header=None, engine='python', on_bad_lines='skip', encoding='latin-1')
        else:
            df = pd.read_excel(file_path, header=None)

        # 1. Find the header row
        header_idx = -1
        for i, row in df.iterrows():
            row_str = " ".join(row.fillna("").astype(str)).lower()
            if "member name" in row_str or "member id" in row_str:
                header_idx = i
                break
        
        if header_idx == -1:
            print("  [WARN] Header not found by keywords. Defaulting to row 1.")
            header_idx = 1 # Fallback
        
        # Extract global metadata (Billing Period/Inv Date) from above header
        global_billing_period = None
        global_inv_number = None
        
        # Try to find Invoice Number in filename
        # Pattern: "Tacton Guardian 2.1-2.28" -> Maybe capture something
        match_inv = re.search(r'(\d+\.\d+-\d+\.\d+)', file_path.name)
        if match_inv:
            global_inv_number = match_inv.group(1)

        for i in range(header_idx):
            row_str = " ".join(df.iloc[i].fillna("").astype(str))
            if not global_billing_period:
                match_bp = re.search(r'\d{1,2}/\d{1,2}/\d{2,4}\s*-\s*\d{1,2}/\d{1,2}/\d{2,4}', row_str)
                if match_bp:
                    global_billing_period = match_bp.group(0)

        # 2. Set columns and slice data
        df.columns = [str(c).strip() for c in df.iloc[header_idx]]
        df = df.iloc[header_idx+1:].reset_index(drop=True)
        
        # 3. Get Semantic Mapping
        mapping = self.get_ai_mapping(df.columns.tolist())
        
        # 4. Forward Fill identifiers for multi-line records
        # Identify columns that are likely identifiers to fill
        id_cols = [c for c, t in mapping.items() if t in ["MEMBER_NAME", "MEMBERID", "EMPLOYEE_ID", "BILLING_PERIOD"]]
        
        # Standard keywords if AI missed them - using more specific matching
        # Avoid matching 'id' inside 'Accident' or other premium columns
        target_id_keywords = ["name", "id", "period", "date", "type", "ssn", "policy"]
        for col in df.columns:
            col_lower = col.lower()
            # Explicitly exclude premium and amount columns from being used as identifiers for ffill
            if "premium" in col_lower or "amount" in col_lower:
                continue
            
            # Check for identifiers using word boundaries or specific checks
            if any(re.search(rf"\b{re.escape(k)}\b", col_lower) for k in target_id_keywords):
                id_cols.append(col)
        
        id_cols = list(set(id_cols))
        print(f"  [DEBUG] ID Columns for ffill: {id_cols}")
        
        for col in id_cols:
            if col in df.columns:
                df[col] = df[col].replace("", pd.NA).replace("nan", pd.NA).replace("None", pd.NA).ffill()

        # 5. Flatten multi-plan columns
        # Identify premium columns from mapping
        premium_map = {c: t for c, t in mapping.items() if t.endswith("_PREMIUM") and t != "TOTAL_PREMIUM"}
        # Fallback to keyword search if AI mapping is sparse
        if not premium_map:
            premium_map = {c: c.replace("Premium", "").strip().upper() + "_PREMIUM" 
                          for c in df.columns if "premium" in c.lower() and "total" not in c.lower() and "type" not in c.lower()}

        rows = []
        for _, row in df.iterrows():
            has_premium = False
            
            # Find Member Name and Id using mapping
            name_col = next((c for c, t in mapping.items() if t == "MEMBER_NAME"), "Member Name")
            id_col = next((c for c, t in mapping.items() if t == "MEMBERID"), "Member Id")
            emp_id_col = next((c for c, t in mapping.items() if t == "EMPLOYEE_ID"), "Employee ID")
            
            fullname = str(row.get(name_col, ""))
            if not fullname or fullname.lower() == "nan":
                continue # Skip truly empty rows

            last, first, mid = self.split_fullname(fullname)
            
            # Billing Period extraction
            bp = row.get("Billing Period", global_billing_period)
            from_date = None
            if bp and isinstance(bp, str):
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', bp)
                if date_match:
                    from_date = date_match.group(1)

            for p_col, p_type in premium_map.items():
                val = self.clean_currency(row.get(p_col, 0))
                if val != 0:
                    has_premium = True
                    
                    # Benefit prefix (e.g., "Dental", "Vision", "Accident")
                    benefit_prefix = p_col.replace("Premium", "").strip()
                    benefit_type = p_type.replace("_PREMIUM", "").upper()
                    
                    # Coverage extraction: check indicator column next to it
                    # Usually "p_col Family Indicator"
                    cov_col = f"{benefit_prefix} Family Indicator"
                    coverage = row.get(cov_col, None)
                    
                    if not coverage or str(coverage).lower() == "nan":
                        # Fallback to AI mapping for coverage, but IGNORE Volumes
                        coverage = next((row[c] for c, t in mapping.items() 
                                       if t == "COVERAGE" and benefit_prefix.split()[0] in c 
                                       and "volume" not in c.lower()), None)

                    # Normalize coverage
                    cov_str = str(coverage).upper() if coverage else ""
                    # Handle cases where Volume (like 50000) might have leaked in
                    if cov_str.replace(".", "").isdigit():
                        coverage = None # Likely a Volume, not a Coverage Indicator
                    else:
                        if "EMP" in cov_str and "CH" in cov_str: coverage = "EC"
                        elif "EMP" in cov_str and "SP" in cov_str: coverage = "ES"
                        elif "FAM" in cov_str: coverage = "FAM"
                        elif "EMP" in cov_str: coverage = "EE"
                        elif "CH" in cov_str: coverage = "EC"
                        elif "SP" in cov_str: coverage = "ES"

                    # Fix Plan Type if it's too generic (like "CURRENT")
                    if benefit_type == "CURRENT":
                        # Use benefit_prefix to guess
                        if "ACCIDENT" in benefit_prefix.upper(): benefit_type = "ACCIDENT"
                        elif "DENTAL" in benefit_prefix.upper(): benefit_type = "DENTAL"
                        elif "VISION" in benefit_prefix.upper(): benefit_type = "VISION"

                    item = {
                        "INV_DATE": row.get("Billing Due Date", None),
                        "INV_NUMBER": global_inv_number,
                        "BILLING_PERIOD": from_date or bp,
                        "LASTNAME": last,
                        "FIRSTNAME": first,
                        "MIDDLENAME": mid,
                        "SSN": None,
                        "POLICYID": row.get(emp_id_col, None),
                        "MEMBERID": row.get(id_col, ""),
                        "PLAN_NAME": p_col,
                        "PLAN_TYPE": benefit_type,
                        "COVERAGE": coverage,
                        "CURRENT_PREMIUM": val,
                        "ADJUSTMENT_PREMIUM": 0.0
                    }
                    
                    # Handle Adjustments
                    if str(row.get("Premium Type", "")).lower() == "premium adjustment":
                        item["ADJUSTMENT_PREMIUM"] = val
                        item["CURRENT_PREMIUM"] = 0.0
                    
                    rows.append(item)
            
            # Special case for "Premium Adjustment" row specifically if it missed standard columns
            if not has_premium and str(row.get("Premium Type", "")).lower() == "premium adjustment":
                # Look for ANY non-zero value that might be an adjustment
                for col in df.columns:
                    if "premium" in col.lower() and "total" not in col.lower():
                        val = self.clean_currency(row.get(col, 0))
                        if val != 0:
                            # Add specifically as adjustment
                            # (already handled in loop above if p_col was in premium_map)
                            pass

        if not rows:
            print("  [ERR] No records extracted.")
            return None

        # 6. Final Standardization
        result_df = pd.DataFrame(rows)
        for field in REQUIRED_FIELDS:
            if field not in result_df.columns:
                result_df[field] = None
        
        result_df = result_df[REQUIRED_FIELDS]

        # Add Totals
        total_rows = []
        sum_current = result_df["CURRENT_PREMIUM"].sum()
        sum_adj = result_df["ADJUSTMENT_PREMIUM"].sum()
        
        total_rows.append({col: None for col in REQUIRED_FIELDS})
        total_rows[-1]["PLAN_NAME"] = "TOTAL CURRENT PREMIUM"
        total_rows[-1]["CURRENT_PREMIUM"] = sum_current
        total_rows.append({col: None for col in REQUIRED_FIELDS})
        total_rows[-1]["PLAN_NAME"] = "TOTAL ADJUSTMENTS"
        total_rows[-1]["ADJUSTMENT_PREMIUM"] = sum_adj
        total_rows.append({col: None for col in REQUIRED_FIELDS})
        total_rows[-1]["PLAN_NAME"] = "GRAND TOTAL"
        total_rows[-1]["CURRENT_PREMIUM"] = sum_current + sum_adj
        
        result_df = pd.concat([result_df, pd.DataFrame(total_rows)], ignore_index=True)

        output_file = self.output_dir / f"{file_path.stem}_v2.xlsx"
        result_df.to_excel(output_file, index=False)
        print(f"  [OK] Extraction successful: {output_file.name}")
        return str(output_file)

if __name__ == "__main__":
    import sys
    # Load .env explicitly if needed
    env_path = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)
    
    if len(sys.argv) < 2:
        print("Usage: python structured_excel_extractor.py <file_path>")
    else:
        extractor = StructuredExcelExtractor("outputs")
        extractor.process_file(sys.argv[1])
