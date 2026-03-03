import os
import sys
import argparse
import json
from dotenv import load_dotenv

# Fix Windows console encoding for Unicode (e.g. checkmarks, emoji)
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
from chunked_extractor import ChunkedInsuranceExtractor

def process_files(extractor, pdf_paths, target_claim):
    results = []
    for pdf_path in pdf_paths:
        print(f"\n{'='*60}")
        print(f"🚀 PROCESSING: {os.path.basename(pdf_path)}")
        print(f"{'='*60}")
        
        try:
            result = extractor.process_pdf_with_verification(pdf_path, target_claim_number=target_claim)
            success = "extracted_schema" in result
            print(f"✅ Completed: {os.path.basename(pdf_path)}")
            results.append({"file": pdf_path, "status": "success"})
        except Exception as e:
            print(f"❌ Failed: {os.path.basename(pdf_path)} - {e}")
            results.append({"file": pdf_path, "status": "failed", "error": str(e)})
    
    return results

def main():
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="Extract insurance data using chunked processing.")
    parser.add_argument("path", help="Path to the PDF file or directory containing PDFs")
    parser.add_argument("--output", help="Directory to save outputs", default="outputs")
    parser.add_argument("--claim", help="Target specific claim number", default=None)
    
    args = parser.parse_args()
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("❌ Error: OPENAI_API_KEY not found in environment.")
        return

    # Initialize Chunked Extractor
    extractor = ChunkedInsuranceExtractor(api_key=api_key, output_dir=args.output)
    
    # Check if path is directory or file
    if os.path.isdir(args.path):
        print(f"📂 Scanning directory: {args.path}")
        pdf_files = [os.path.join(args.path, f) for f in os.listdir(args.path) if f.lower().endswith(".pdf")]
        if not pdf_files:
            print("⚠️ No PDF files found in directory.")
            return
        print(f"📋 Found {len(pdf_files)} PDF files.")
        process_files(extractor, pdf_files, args.claim)
    elif os.path.isfile(args.path):
        process_files(extractor, [args.path], args.claim)
    else:
        print(f"❌ Error: Path '{args.path}' not found.")

if __name__ == "__main__":
    main()
