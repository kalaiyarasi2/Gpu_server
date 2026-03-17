from pathlib import Path
import sys
import os

# Set up paths
backend_dir = Path("c:/Users/INTERN/main_project/Main--main/bank statement/backend")
sys.path.insert(0, str(backend_dir))

from statement_extractor import StatementExtractor

extractor = StatementExtractor()
res = extractor.process_pdf(r"C:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform\uploads\Abel I_Operating Statement - Jan 2026.pdf")
print("FINISHED.")
