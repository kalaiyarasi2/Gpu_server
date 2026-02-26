import pandas as pd
import os

excel_path = r'c:\Users\INTERN\Downloads\BCBS_Final_Verification.xlsx'
if not os.path.exists(excel_path):
    print(f"Error: {excel_path} not found")
    exit(1)

df = pd.read_excel(excel_path)
print(f"Total Rows (excluding header/total): {len(df)}")
print(f"Current Premium Sum: ${df['CURRENT_PREMIUM'].sum():.2f}")

rosario = df[df['LASTNAME'].str.contains('ROSARIO', na=False, case=False)]
if not rosario.empty:
    print("\nROSARIO CELESTE Data:")
    print(rosario[['LASTNAME', 'FIRSTNAME', 'SSN', 'CURRENT_PREMIUM']].to_string())
else:
    print("\nWarning: ROSARIO CELESTE not found in extraction!")

# Check for duplicates or missing names
missing_names = df[df['LASTNAME'].isna() & df['FIRSTNAME'].isna()]
if not missing_names.empty:
    print(f"\nWarning: Found {len(missing_names)} rows with missing names.")

print("\nFull Data Snapshot:")
print(df[['LASTNAME', 'FIRSTNAME', 'SSN', 'CURRENT_PREMIUM']].to_string())
