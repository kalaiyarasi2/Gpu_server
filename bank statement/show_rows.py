import openpyxl
from pathlib import Path

def show_top_rows(filepath, n=10):
    print(f"\n--- Top {n} rows of {Path(filepath).name} ---")
    wb = openpyxl.load_workbook(filepath, data_only=True)
    ws = wb.active
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= n: break
        print(row)

if __name__ == "__main__":
    dir_path = Path(r"c:\Users\Intern\bank statement\outputs\statement_extraction_20260311_185736_4597_tmptkoq86ir_pdf")
    show_top_rows(dir_path / "extracted_statement.xlsx")
    show_top_rows(dir_path / "extracted_statement_sorted.xlsx")
