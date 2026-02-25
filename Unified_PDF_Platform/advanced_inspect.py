import pandas as pd
from pathlib import Path
import xlrd

def advanced_inspect(file_path):
    print(f"\n--- Advanced Inspect: {file_path} ---")
    
    with open(file_path, 'rb') as f:
        header = f.read(16)
        print(f"Header bytes: {header.hex(' ')}")

    try:
        print("Trying pd.read_excel(engine='openpyxl')...")
        df = pd.read_excel(file_path, engine='openpyxl', nrows=5)
        print("Success with openpyxl!")
        print(df)
    except Exception as e:
        print(f"openpyxl failed: {e}")

    try:
        print("Trying pd.read_excel(engine='xlrd')...")
        df = pd.read_excel(file_path, engine='xlrd', nrows=5)
        print("Success with xlrd!")
        print(df)
    except Exception as e:
        print(f"xlrd failed: {e}")

    try:
        print("Trying pd.read_csv(sep=None, engine='python')...")
        df = pd.read_csv(file_path, sep=None, engine='python', nrows=20)
        print("Success with read_csv (auto-detect)!")
        print(df.head())
    except Exception as e:
        print(f"read_csv failed: {e}")

if __name__ == "__main__":
    advanced_inspect(r"c:\Users\INTERN\main_project\Lake Country Anthem 1.1-1.31.xlsx")
    advanced_inspect(r"c:\Users\INTERN\main_project\LifeLoop Unum 1.1-1.31.csv")
