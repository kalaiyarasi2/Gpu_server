import pandas as pd
from pathlib import Path

header_keywords = ["employee id", "member id", "member id no.", "subscriber id", "subscriber name", "last name", "first name", "ssn", "certificate number", "currentcharges", "premium", "charges", "name"]

def diagnostic_scan(file_path):
    print(f"\n--- Diagnostic Scan: {file_path} ---")
    ext = Path(file_path).suffix.lower()
    
    try:
        if ext == '.csv':
            df = pd.read_csv(file_path, header=None, engine='python', on_bad_lines='skip')
        else:
            try:
                xl = pd.ExcelFile(file_path, engine='openpyxl')
                df = pd.read_excel(xl, sheet_name=xl.sheet_names[0], header=None, engine='openpyxl')
            except:
                xl = pd.ExcelFile(file_path, engine='xlrd')
                df = pd.read_excel(xl, sheet_name=xl.sheet_names[0], header=None, engine='xlrd')
        
        print(f"Total Rows: {len(df)}")
        for i in range(min(len(df), 100)):
            row_vals = df.iloc[i].fillna("").astype(str).tolist()
            row_str = " ".join(row_vals).lower()
            matches = [x for x in header_keywords if x in row_str]
            if matches:
                print(f"Row {i}: Matches: {matches} | Text: {row_str[:150]}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    diagnostic_scan(r"c:\Users\INTERN\main_project\Lake Country Anthem 1.1-1.31.xlsx")
    diagnostic_scan(r"c:\Users\INTERN\main_project\LifeLoop Unum 1.1-1.31.csv")
