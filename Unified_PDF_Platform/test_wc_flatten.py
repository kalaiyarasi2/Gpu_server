import sys
import os
from pathlib import Path

# Add the project directory to sys.path to import UnifiedRouter
project_dir = r"c:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform"
sys.path.append(project_dir)

from unified_router import UnifiedRouter

def test_flattening():
    router = UnifiedRouter()
    
    # Path to a sample Workers' Comp JSON
    sample_json = r"c:\Users\INTERN\main_project\Main--main\work_compenstaion\backend\outputs\extraction_20260304_032535_3737_A_Total_Solutions,_I\extracted_schema.json"
    
    if not os.path.exists(sample_json):
        print(f"Sample JSON not found at {sample_json}")
        return

    print(f"Testing flattening for: {sample_json}")
    excel_path = router.flatten_workers_comp_to_excel(Path(sample_json))
    
    if excel_path and os.path.exists(excel_path):
        print(f"SUCCESS: Excel file created at {excel_path}")
        
        # Verify sheets using pandas
        import pandas as pd
        xl = pd.ExcelFile(excel_path)
        print(f"Sheets found: {xl.sheet_names}")
        
        expected_sheets = ['Demographics_Summary', 'Rating_by_State', 'Prior_Carriers', 'Individuals', 'Questions']
        missing_sheets = [s for s in expected_sheets if s not in xl.sheet_names]
        
        if not missing_sheets:
            print("All expected sheets are present.")
        else:
            print(f"MISSING SHEETS: {missing_sheets}")
    else:
        print("FAILURE: Excel file was not created.")

if __name__ == "__main__":
    test_flattening()
