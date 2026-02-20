import os
from dotenv import load_dotenv
load_dotenv()
from insurance_extractor import EnhancedInsuranceExtractor
from config import config

def test_vision_extraction():
    # Ensure vision is enabled in config
    config.OCR_ENGINE = 'vision'
    
    # Path to a sample PDF (scanned)
    # Using one from the uploads folder found in find_by_name
    pdf_path = r"c:\Users\Intern\pdf_extractor\Unified_PDF_Platform\uploads\State Fund Online - 22-23 1.pdf"
    
    if not os.path.exists(pdf_path):
        print(f"❌ Test PDF not found at {pdf_path}")
        # Try a different one
        pdf_path = r"c:\Users\Intern\pdf_extractor\work_compenstaion\backend\sources\Accident Fund 21-25.pdf"
        if not os.path.exists(pdf_path):
            print(f"❌ Alternative PDF not found at {pdf_path}")
            return

    print(f"🚀 Starting Vision Extraction Test for: {pdf_path}")
    
    try:
        extractor = EnhancedInsuranceExtractor()
        text, metadata = extractor.extract_text_from_pdf(pdf_path)
        
        print("\n" + "="*50)
        print("EXTRACTION RESULTS (First 500 chars):")
        print("="*50)
        print(text[:500] + "...")
        print("="*50)
        
        print(f"\n✅ Extracted {len(text)} characters")
        print(f"✅ Metadata pages: {len(metadata)}")
        
        # Check if the output contains expected layout markers
        if "PAGE 1" in text and "====" in text:
            print("✅ Structure preservation markers found")
        else:
            print("⚠️ Structure preservation markers missing")
            
    except Exception as e:
        print(f"❌ Extraction failed: {e}")

if __name__ == "__main__":
    # Make sure we are in the right directory to import
    import sys
    sys.path.append(os.getcwd())
    test_vision_extraction()
