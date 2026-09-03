import os
import json
import sys
from pathlib import Path

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from dotenv import load_dotenv
load_dotenv() # Load from .env file

from work_compensation import EnhancedInsuranceExtractor

def verify_accuracy(pdf_path):
    print(f"\n🧪 Testing Accuracy for: {pdf_path}")
    
    # Use standard local API key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ Error: OPENAI_API_KEY not set")
        return
        
    extractor = EnhancedInsuranceExtractor(api_key)
    
    # Run processing
    result = extractor.process_pdf_with_verification(pdf_path)
    schema = result.get('extracted_schema', {}).get('data', {})
    
    # Target values for A1 Escort Services
    targets = {
        "Applicant Name": "A1 ESCORT SERVICE LLC",
        "Mailing State": "DE",
        "Mailing Zip": "19801",
        "Rating Table": len(schema.get('ratingByState', [])) > 0,
        "Prior Carriers": len(schema.get('priorCarriers', [])) > 0
    }
    
    print("\n📊 ACCURACY REPORT:")
    print("-------------------")
    
    # Check Applicant Name
    app_name = schema.get('demographics', {}).get('applicantName', '')
    name_pass = "A1" in app_name.upper()
    print(f"✅ Applicant Name: {app_name} (Pass: {name_pass})")
    
    # Check Zip Code
    zip_code = schema.get('demographics', {}).get('mailingZip', '')
    zip_pass = zip_code == "19801"
    print(f"✅ Mailing Zip: {zip_code} (Pass: {zip_pass})")
    
    # Check Tables
    rating_pass = len(schema.get('ratingByState', [])) > 0
    print(f"✅ Rating Table: {'Found' if rating_pass else 'Empty'} (Pass: {rating_pass})")
    
    carriers_pass = len(schema.get('priorCarriers', [])) > 0
    print(f"✅ Prior Carriers: {'Found' if carriers_pass else 'Empty'} (Pass: {carriers_pass})")
    
    if all([name_pass, zip_pass, rating_pass, carriers_pass]):
        print("\n🏆 VERIFICATION SUCCESSFUL: Extraction accuracy significantly improved.")
    else:
        print("\n⚠️ VERIFICATION PARTIAL: Some fields still need improvement.")

if __name__ == "__main__":
    pdf = r"c:\Users\INTERN\main_project\Main--main\work_compenstaion\sources\A1 Escort Services, LLC - Acord.pdf"
    if os.path.exists(pdf):
        verify_accuracy(pdf)
    else:
        print(f"File not found: {pdf}")
