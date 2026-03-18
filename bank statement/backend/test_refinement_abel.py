
import sys
import os

# Load from the project root .env manually
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))
print(f"Loading .env from: {dotenv_path}")
if os.path.exists(dotenv_path):
    with open(dotenv_path, 'r') as f:
        for line in f:
            if line.startswith('OPENAI_API_KEY='):
                os.environ['OPENAI_API_KEY'] = line.split('=', 1)[1].strip()
                print("OPENAI_API_KEY found and set.")

from statement_extractor import StatementExtractor

def run_test():
    try:
        # Testing a disbursement statement that had massive over-counting
        pdf_path = r"c:\Users\INTERN\main_project\Main--main\bank statement\backend\sources\Bank statememts\Abel I - Disbursement bank statement - Jan 2026.pdf"
        extractor = StatementExtractor()
        result = extractor.process_pdf(pdf_path)
        print(f"Extraction complete: {result['session_dir']}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_test()
