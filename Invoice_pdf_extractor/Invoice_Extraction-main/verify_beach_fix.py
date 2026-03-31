import os
from structured_excel_extractor import StructuredExcelExtractor

def verify_beach_omaha():
    file_path = r"C:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform\uploads\Beach Mutual of Omaha Mar 2026.xls"
    output_dir = "outputs_test"
    
    if not os.path.exists(file_path):
        print(f"[ERR] File not found: {file_path}")
        return
        
    extractor = StructuredExcelExtractor(output_dir)
    result = extractor.process_file(file_path)
    
    if result:
        print(f"\n[OK] Extraction successful: {result}")
        import pandas as pd
        df = pd.read_excel(result)
        print(f"Total rows extracted: {len(df)}")
        print("\nColumn Mapping Preview:")
        print(df.head(5).to_string())
        
        # Basic assertions
        assert len(df) > 3, "Too few rows extracted"
        assert any("Altidor" in str(x) for x in df["LASTNAME"]), "Expected participant 'Altidor' not found"
    else:
        print("\n[FAIL] Extraction failed.")

if __name__ == "__main__":
    verify_beach_omaha()
