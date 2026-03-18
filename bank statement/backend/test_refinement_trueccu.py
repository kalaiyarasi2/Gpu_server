import os
import json
from pathlib import Path
from dotenv import load_dotenv
from statement_extractor import StatementExtractor

# Load environment variables
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

def test_trueccu_extraction():
    pdf_path = r"c:\Users\INTERN\main_project\Main--main\bank statement\backend\sources\Bank statement (1).pdf"
    
    print(f"🚀 Starting extraction for: {pdf_path}")
    extractor = StatementExtractor()
    result = extractor.process_pdf(pdf_path)
    
    print(f"\n✅ Extraction Complete!")
    print(f"📂 Session Dir: {result['session_dir']}")
    
    data = result['data']
    deposits = data['deposits_and_credits']
    debits = data['checks_and_other_debits']
    
    print(f"📈 Results:")
    print(f"   - Deposits: {len(deposits)}")
    print(f"   - Debits: {len(debits)}")
    
    # Check if pages 3-5 are still blank
    extracted_text_file = result['extracted_text_file']
    with open(extracted_text_file, 'r', encoding='utf-8') as f:
        text = f.read()
        
    for p in range(3, 6):
        marker = f"PAGE {p}"
        if marker in text:
            page_content = text.split(marker)[1].split("PAGE")[0].strip()
            if "[No text detected on this page]" in page_content:
                print(f"❌ Page {p} is still reported as blank!")
            else:
                print(f"✅ Page {p} has content now!")

if __name__ == "__main__":
    test_trueccu_extraction()
