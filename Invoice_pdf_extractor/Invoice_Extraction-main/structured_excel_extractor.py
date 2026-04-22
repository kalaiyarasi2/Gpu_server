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
        TARGET FIELDS: {REQUIRED_FIELDS} + ['MEMBER_NAME', 'FIRST_NAME', 'LAST_NAME', 'EMPLOYEE_ID']
        
        RULES:
        - Return ONLY JSON: {{"SourceColumn": "TargetField"}}
        - Identify columns that contain premium amounts or billed amounts.
        - Mapping tips:
          'Member Name' or 'Enrollee Name' -> 'MEMBER_NAME'
          'First Name' or 'Enrollee First Name' -> 'FIRST_NAME'
          'Last Name' or 'Enrollee Last Name' -> 'LAST_NAME'
          'Member Id' or 'Enrollee ID' -> 'MEMBERID'
          'Employee ID' -> 'EMPLOYEE_ID'
          'Amount Due', 'Amount', 'Premium', 'Total Charge' -> 'TOTAL_PREMIUM'
          'Coverage Option', 'Coverage', 'Enrolled Option' -> 'COVERAGE'
          'Plan Name', 'Plan Description' -> 'PLAN_NAME'
          'Accident' or 'Accident Premium' -> 'ACCIDENT_PREMIUM'
          'Dental' or 'Dental Premium' -> 'DENTAL_PREMIUM'
          'Vision' or 'Vision Premium' -> 'VISION_PREMIUM'
          'STD' or 'STD Premium' -> 'STD_PREMIUM'
          'LTD' or 'LTD Premium' -> 'LTD_PREMIUM'
          'Basic Term Life' or 'Life Premium' -> 'LIFE_PREMIUM'
          '.* Indicator' -> 'COVERAGE'
        - NEGATIVE CONSTRAINTS: DO NOT map columns containing 'Header', 'Summary', 'Total', or 'Category' to name fields.
        """
        try:

            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)

        except Exception as e: 
            print(f"  [ERR] AI Mapping failed: {e}")
            return {}

    def _extract_from_dataframe(self, df: pd.DataFrame, file_path: Path, sheet_name: Optional[str] = None) -> List[Dict]:
        """Core extraction logic for a single sheet/dataframe."""
        if sheet_name:
            print(f"  [Sheet] Extracting from: {sheet_name}")

        # 1. Find the header row
        header_idx = -1
        primary_keywords = ["participant name", "member name", "subscriber name", "employee name", "enrollee name", "enrollee last name"]
        secondary_keywords = ["member id", "participant id", "employee id", "enrollee id", "ssn", "dob"]

        # Pass 1: Look for primary anchors
        for i, row in df.iterrows():
            row_str = " ".join(row.fillna("").astype(str)).lower()
            if any(k in row_str for k in primary_keywords):
                header_idx = i
                break
        
        # Pass 2: Fallback to secondary keywords if no primary found
        if header_idx == -1:
            for i, row in df.iterrows():
                row_str = " ".join(row.fillna("").astype(str)).lower()
                if any(k in row_str for k in secondary_keywords):
                    if row.count() >= 3:
                        header_idx = i
                        break
        
        if header_idx == -1:
            if sheet_name:
                print(f"    [SKIP] Header not found in sheet: {sheet_name}")
            return []
        
        if header_idx >= len(df):
            return []
        
        # Extract global metadata (Billing Period/Inv Date) from above header
        global_billing_period = None
        global_inv_number = None
        global_inv_date = None
        
        match_inv = re.search(r'(\d+\.\d+-\d+\.\d+)', file_path.name)
        if match_inv:
            global_inv_number = match_inv.group(1)

        for i in range(header_idx):
            row = df.iloc[i].fillna("").astype(str).tolist()
            row_str = " ".join(row).lower()
            if not global_billing_period:
                match_bp = re.search(r'\d{1,2}/\d{1,2}/\d{2,4}\s*-\s*\d{1,2}/\d{1,2}/\d{2,4}', row_str)
                if match_bp:
                    global_billing_period = match_bp.group(0)
            
            for idx, cell in enumerate(row):
                cell_lower = cell.strip().lower()
                if not global_inv_number and ("invoice_number" in cell_lower or "inv_number" in cell_lower or "invoice number" in cell_lower):
                    for next_cell in row[idx+1:]:
                        val = next_cell.strip().replace("=", "").replace('"', "")
                        if val:
                            global_inv_number = val
                            break
                if not global_inv_date and ("invoice_date" in cell_lower or "inv_date" in cell_lower or "invoice date" in cell_lower):
                    for next_cell in row[idx+1:]:
                        val = next_cell.strip()
                        if val:
                            global_inv_date = val
                            break

        # 2. Set columns and slice data
        df.columns = [str(c).strip() for c in df.iloc[header_idx]]
        df = df.iloc[header_idx+1:].reset_index(drop=True)
        
        # 3. Get Semantic Mapping
        mapping = self.get_ai_mapping(df.columns.tolist())
        print(f"  [DEBUG] Final Mapping: {mapping}")

        # 4. Forward Fill identifiers
        id_cols = [c for c, t in mapping.items() if t in ["MEMBER_NAME", "MEMBERID", "EMPLOYEE_ID", "BILLING_PERIOD"]]
        target_id_keywords = ["name", "id", "period", "date", "type", "ssn", "policy"]
        for col in df.columns:
            col_lower = col.lower()
            if "premium" in col_lower or "amount" in col_lower:
                continue
            if any(re.search(rf"\b{re.escape(k)}\b", col_lower) for k in target_id_keywords + ["enrollee"]):
                id_cols.append(col)
        
        id_cols = list(set(id_cols))
        for col in id_cols:
            if col in df.columns:
                df[col] = df[col].replace("", pd.NA).replace("nan", pd.NA).replace("None", pd.NA).ffill()
        
        # 5. Flatten multi-plan columns
        premium_map = {c: t for c, t in mapping.items() if t and t.endswith("_PREMIUM")}
        benefit_keywords = ["ACCIDENT", "DENTAL", "VISION", "LIFE", "LTD", "STD", "MEDICAL", "CRITICAL", "HOSPITAL", "AD&D", "AMOUNT", "DUE", "CHARGE"]

        if not premium_map:
            for col in df.columns:
                col_upper = col.upper()
                if "TOTAL" in col_upper or "TYPE" in col_upper:
                    continue
                if "PREMIUM" in col_upper:
                    premium_map[col] = col.replace("Premium", "").strip().upper() + "_PREMIUM"
                elif any(re.search(rf"\b{re.escape(k)}\b", col_upper) for k in benefit_keywords):
                    premium_map[col] = col.strip().upper() + "_PREMIUM"

        benefit_desc_col = next((c for c in df.columns if c.strip().lower() == "benefit description"), None)
        is_row_per_benefit = benefit_desc_col is not None and any(
            c.strip().lower() in ("premium", "current premium") for c in df.columns
        )

        sheet_rows = []
        for _, row in df.iterrows():
            has_premium = False
            name_col = next((c for c, t in mapping.items() if t == "MEMBER_NAME"), None)
            if name_col is None or name_col not in df.columns:
                name_col = next((c for c in df.columns if c.strip().lower() in ("name", "member name", "employee name", "subscriber name", "participant name", "enrollee name")), None)
            
            first_name_col = next((c for c, t in mapping.items() if t == "FIRST_NAME"), None)
            if first_name_col is None or first_name_col not in df.columns:
                first_name_col = next((c for c in df.columns if c.strip().lower() in ("first name", "firstname", "enrollee first name", "given name")), None)
            last_name_col = next((c for c, t in mapping.items() if t == "LAST_NAME"), None)
            if last_name_col is None or last_name_col not in df.columns:
                last_name_col = next((c for c in df.columns if c.strip().lower() in ("last name", "lastname", "enrollee last name", "surname")), None)

            fullname = None
            if first_name_col and last_name_col:
                f_val = str(row.get(first_name_col, "")).strip()
                l_val = str(row.get(last_name_col, "")).strip()
                if f_val and l_val and f_val.lower() != "nan" and l_val.lower() != "nan":
                    if not any(kw in f_val.lower() or kw in l_val.lower() for kw in ("header", "summary", "total", "adj")):
                        fullname = f"{l_val}, {f_val}"

            if not fullname:
                fullname = str(row.get(name_col, "")) if name_col else ""
                if fullname and any(kw in fullname.lower() for kw in ("header", "summary", "total", "adj")):
                    fullname = ""
            
            if not fullname or fullname.lower() == "nan":
                continue

            last, first, mid = self.split_fullname(fullname)
            id_col = next((c for c, t in mapping.items() if t == "MEMBERID"), None)
            if id_col is None or id_col not in df.columns:
                id_col = next((c for c in df.columns if c.strip().lower() in ("member id", "memberid", "member_id", "employee id", "subscriber id", "enrollee id")), None)
            emp_id_col = next((c for c, t in mapping.items() if t == "EMPLOYEE_ID"), None)
            if emp_id_col is None or emp_id_col not in df.columns:
                emp_id_col = next((c for c in df.columns if c.strip().lower() in ("employee id", "employeeid", "client policy", "enrollee id")), None)

            bp = row.get("Billing Period", global_billing_period)
            from_date = None
            if bp and isinstance(bp, str):
                date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', bp)
                if date_match:
                    from_date = date_match.group(1)

            if is_row_per_benefit:
                prem_col = next((c for c in df.columns if c.strip().lower() in ("premium", "current premium")), None)
                val = self.clean_currency(row.get(prem_col, 0)) if prem_col else 0.0
                benefit_desc = str(row.get(benefit_desc_col, "")).strip()
                if not benefit_desc or benefit_desc.lower() in ("nan", "benefit description", "total"):
                    continue
                desc_upper = benefit_desc.upper()
                if "ACCIDENT" in desc_upper:   plan_type = "ACCIDENT"
                elif "DENTAL" in desc_upper:   plan_type = "DENTAL"
                elif "VISION" in desc_upper:   plan_type = "VISION"
                elif "LIFE" in desc_upper:     plan_type = "LIFE"
                elif "LTD" in desc_upper:     plan_type = "LTD"
                elif "STD" in desc_upper:     plan_type = "STD"
                elif "MEDICAL" in desc_upper:     plan_type = "MEDICAL"
                else:                          plan_type = "OTHER"
                item = {
                    "INV_DATE": global_inv_date, "INV_NUMBER": global_inv_number, "BILLING_PERIOD": from_date or bp,
                    "LASTNAME": last, "FIRSTNAME": first, "MIDDLENAME": mid, "SSN": None,
                    "POLICYID": row.get(emp_id_col, None) if emp_id_col else None,
                    "MEMBERID": row.get(id_col, "") if id_col else "",
                    "PLAN_NAME": benefit_desc, "PLAN_TYPE": plan_type, "COVERAGE": None,
                    "CURRENT_PREMIUM": val, "ADJUSTMENT_PREMIUM": 0.0
                }
                sheet_rows.append(item)
                continue

            for p_col, p_type in premium_map.items():
                val = self.clean_currency(row.get(p_col, 0))
                if val != 0:
                    has_premium = True
                    benefit_prefix = p_col.replace("Premium", "").strip()
                    benefit_type = p_type.replace("_PREMIUM", "").upper()
                    
                    cov_col = f"{benefit_prefix} Family Indicator"
                    coverage = row.get(cov_col, None)
                    if not coverage or str(coverage).lower() == "nan":
                        bp_word = benefit_prefix.split()[0] if benefit_prefix.strip() else None
                        coverage = next((row[c] for c, t in mapping.items() if t == "COVERAGE" and (bp_word is None or bp_word in c) and "volume" not in c.lower()), None)
                    if not coverage or str(coverage).lower() == "nan":
                        coverage = next((row[c] for c in df.columns if c.strip().lower() in ("coverage option", "coverage", "plan option")), None)

                    cov_str = str(coverage).upper() if coverage else ""
                    if cov_str.replace(".", "").isdigit():
                        coverage = None
                    else:
                        if "EMP" in cov_str and "CH" in cov_str: coverage = "EC"
                        elif "EMP" in cov_str and "SP" in cov_str: coverage = "ES"
                        elif "EE" in cov_str and "ONLY" in cov_str: coverage = "EE"
                        elif "FAM" in cov_str: coverage = "FAM"
                        elif "EE + SP" in cov_str: coverage = "ES"
                        elif "EE + CH" in cov_str: coverage = "EC"
                        elif "EMP" in cov_str: coverage = "EE"
                        elif "CH" in cov_str: coverage = "EC"
                        elif "SP" in cov_str: coverage = "ES"

                    if benefit_type in ("CURRENT", "TOTAL", "", "_"):
                        search_str = benefit_prefix.upper()
                        if not search_str and benefit_desc_col: search_str = str(row.get(benefit_desc_col, "")).upper()
                        fn_upper = file_path.name.upper()
                        if not search_str or search_str in ("TOTAL", "AMOUNT DUE"): search_str += " " + fn_upper
                        if "ACCIDENT" in search_str:   benefit_type = "ACCIDENT"
                        elif "DENTAL" in search_str:   benefit_type = "DENTAL"
                        elif "VISION" in search_str:   benefit_type = "VISION"
                        elif "LIFE" in search_str:     benefit_type = "LIFE"
                        elif "LTD" in search_str:     benefit_type = "LTD"
                        elif "STD" in search_str:     benefit_type = "STD"
                        elif "MEDICAL" in search_str:     benefit_type = "MEDICAL"
                        elif benefit_desc_col: benefit_type = str(row.get(benefit_desc_col, "OTHER")).strip().upper() or "OTHER"

                    is_adj_sheet = sheet_name and any(x in sheet_name.lower() for x in ("adj", "change", "term"))
                    
                    item = {
                        "INV_DATE": row.get("Billing Due Date", global_inv_date),
                        "INV_NUMBER": global_inv_number,
                        "BILLING_PERIOD": from_date or bp,
                        "LASTNAME": last, "FIRSTNAME": first, "MIDDLENAME": mid, "SSN": None,
                        "POLICYID": row.get(emp_id_col, None) if emp_id_col else None,
                        "MEMBERID": row.get(id_col, "") if id_col else "",
                        "PLAN_NAME": str(row.get(benefit_desc_col, p_col)).strip() if (benefit_desc_col and benefit_prefix == "") else p_col,
                        "PLAN_TYPE": benefit_type,
                        "COVERAGE": coverage,
                        "CURRENT_PREMIUM": 0.0 if is_adj_sheet else val,
                        "ADJUSTMENT_PREMIUM": val if is_adj_sheet else 0.0
                    }
                    if not is_adj_sheet and str(row.get("Premium Type", "")).lower() == "premium adjustment":
                        item["ADJUSTMENT_PREMIUM"] = val
                        item["CURRENT_PREMIUM"] = 0.0
                    sheet_rows.append(item)
        return sheet_rows

    def process_file(self, file_path: str) -> str:
        print(f"\n[StructuredExcelExtractor] Processing: {file_path}")
        file_path = Path(file_path)
        ext = file_path.suffix.lower()
        
        all_rows = []
        if ext == ".csv":
            try:
                df = pd.read_csv(file_path, header=None, engine='python', names=range(100), on_bad_lines='skip', encoding='utf-8-sig')
            except:
                df = pd.read_csv(file_path, header=None, engine='python', names=range(100), on_bad_lines='skip', encoding='latin-1')
            df = df.dropna(axis=1, how='all')
            all_rows = self._extract_from_dataframe(df, file_path)
        else:
            xl = pd.ExcelFile(file_path)
            for sheet in xl.sheet_names:
                df = xl.parse(sheet, header=None)
                all_rows.extend(self._extract_from_dataframe(df, file_path, sheet_name=sheet))

        if not all_rows:
            print("  [ERR] No records extracted from any sheet.")
            return None

        result_df = pd.DataFrame(all_rows)
        for field in REQUIRED_FIELDS:
            if field not in result_df.columns: result_df[field] = None
        result_df = result_df[REQUIRED_FIELDS]

        # Add Totals
        sum_current = result_df["CURRENT_PREMIUM"].sum()
        sum_adj = result_df["ADJUSTMENT_PREMIUM"].sum()
        total_rows = [
            {col: None for col in REQUIRED_FIELDS},
            {**{col: None for col in REQUIRED_FIELDS}, "PLAN_NAME": "TOTAL CURRENT PREMIUM", "CURRENT_PREMIUM": sum_current},
            {**{col: None for col in REQUIRED_FIELDS}, "PLAN_NAME": "TOTAL ADJUSTMENTS", "ADJUSTMENT_PREMIUM": sum_adj},
            {**{col: None for col in REQUIRED_FIELDS}, "PLAN_NAME": "GRAND TOTAL", "CURRENT_PREMIUM": sum_current + sum_adj}
        ]
        result_df = pd.concat([result_df, pd.DataFrame(total_rows)], ignore_index=True)

        output_xlsx = self.output_dir / f"{file_path.stem}_v2.xlsx"
        output_json = self.output_dir / f"{file_path.stem}_v2.json"
        
        result_df.to_excel(output_xlsx, index=False)
        with open(output_json, 'w') as f:
            json.dump(all_rows, f, indent=4)
            
        print(f"  [OK] Extraction successful: {output_xlsx.name} and {output_json.name}")
        return str(output_xlsx)

if __name__ == "__main__":
    import sys
    env_path = Path(__file__).parent.parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)
    if len(sys.argv) < 2:
        print("Usage: python structured_excel_extractor.py <file_path>")
    else:
        extractor = StructuredExcelExtractor("outputs")
        extractor.process_file(sys.argv[1])
