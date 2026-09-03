import pandas as pd

try:
    df = pd.read_excel(r"c:\Users\INT002\updated_Extractor\pdf_extractor\extracted_data_structural (2) (1).xlsx")
    print(df.to_string(index=False))
except Exception as e:
    print(f"Error reading Excel: {e}")
