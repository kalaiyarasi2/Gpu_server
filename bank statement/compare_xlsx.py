import openpyxl
from pathlib import Path

def compare_excel(file1, file2):
    print(f"Comparing:\n  1: {file1}\n  2: {file2}\n")
    
    def get_data(filepath):
        wb = openpyxl.load_workbook(filepath, data_only=True)
        sheet = wb.active
        data = []
        for row in sheet.iter_rows(values_only=True):
            if any(row): # Skip truly empty rows
                data.append(row)
        return data

    data1 = get_data(file1)
    data2 = get_data(file2)

    print(f"File 1 rows: {len(data1)}")
    print(f"File 2 rows: {len(data2)}")

    # Basic diff
    set1 = set(tuple(map(str, r)) for r in data1)
    set2 = set(tuple(map(str, r)) for r in data2)

    only_in_1 = set1 - set2
    only_in_2 = set2 - set1

    if only_in_1:
        print(f"\nUnique to File 1 ({len(only_in_1)} rows):")
        for r in list(only_in_1)[:5]: print(f"  {r}")
    
    if only_in_2:
        print(f"\nUnique to File 2 ({len(only_in_2)} rows):")
        for r in list(only_in_2)[:5]: print(f"  {r}")

    if not only_in_1 and not only_in_2:
        if len(data1) == len(data2):
            print("\nBoth files have identical content (though order might differ).")
        else:
            print("\nContent sets are identical, but row counts differ (likely duplicates).")

if __name__ == "__main__":
    dir_path = Path(r"c:\Users\Intern\bank statement\outputs\statement_extraction_20260311_185736_4597_tmptkoq86ir_pdf")
    f_orig = dir_path / "extracted_statement.xlsx"
    f_sorted = dir_path / "extracted_statement_sorted.xlsx"
    
    compare_excel(f_orig, f_sorted)
