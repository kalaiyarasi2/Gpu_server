import os
import sys
import json
from pathlib import Path

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from dotenv import load_dotenv
load_dotenv() # Load from .env file

from chunked_extractor import ChunkedInsuranceExtractor

def test_pipeline_coverage(pdf_path):
    print(f"\n🧪 Testing Optimized Pipeline for: {os.path.basename(pdf_path)}")
    
    extractor = ChunkedInsuranceExtractor()
    
    try:
        # Run process_pdf_with_verification
        # This will trigger the new extract_text_from_pdf logic
        result = extractor.process_pdf_with_verification(pdf_path)
        
        # Verify result structure
        if not result or 'extracted_schema' not in result:
            print("❌ Error: No schema results returned")
            return
            
        print("✅ Pipeline executed successfully")
        
        # Check for metadata
        metadata = result.get('summary', {})
        print(f"📊 Summary: {metadata}")
        
        # Check extracted text for recovery blocks
        combined_text = result.get('combined_text', '')
        if "FORM FIELD DATA" in combined_text:
            print("✅ Detected Form Field Data extraction")
        if "OCR RECOVERY DATA" in combined_text:
            print("✅ Detected OCR Recovery Data extraction")
            
        # Check extraction methods per page
        methods = metadata.get('extraction_methods', [])
        print(f"📄 Page Methods: {methods}")
        
        # Basic schema check
        schema_data = result.get('extracted_schema', {}).get('data', {})
        demographics = schema_data.get('demographics', {})
        print(f"👤 Applicant: {demographics.get('applicantName', 'Not found')}")
        
    except Exception as e:
        print(f"❌ Pipeline failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # Test with known file
    base_dir = r"c:\Users\Intern\pdf_extractor\work_compenstaion\backend"
    test_pdf = os.path.join(base_dir, "sources", "A1 Escort Services, LLC - Acord.pdf")
    if os.path.exists(test_pdf):
        test_pipeline_coverage(test_pdf)
    else:
        print(f"⚠️ Test file not found: {test_pdf}")
        # List sources to find another one
        sources_dir = os.path.join(base_dir, "sources")
        if os.path.exists(sources_dir):
            files = os.listdir(sources_dir)
            if files:
                first_pdf = os.path.join(sources_dir, files[0])
                test_pipeline_coverage(first_pdf)
