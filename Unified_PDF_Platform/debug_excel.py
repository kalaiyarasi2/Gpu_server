import pandas as pd
import sys
from pathlib import Path

def check_excel(file_path):
    if not Path(file_path).exists():
        print(f"Error: {file_path} does not exist.")
        return
    
    df = pd.read_excel(file_path)
    print(f"Total rows: {len(df)}")
    print("\nFirst 3 rows:")
    print(df[['FIRSTNAME', 'LASTNAME', 'CURRENT_PREMIUM']].head(3).to_string())
    print("\nLast 3 rows:")
    print(df[['FIRSTNAME', 'LASTNAME', 'CURRENT_PREMIUM']].tail(3).to_string())

if __name__ == "__main__":
    if len(sys.argv) > 1:
        check_excel(sys.argv[1])
    else:
        print("Usage: python check_excel.py <file_path>")
