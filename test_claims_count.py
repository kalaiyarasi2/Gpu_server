import sys
import os
import json
from pathlib import Path

# Add backend to path
backend_dir = r"c:\Users\Intern\server1\pdf_extractor\Insurance_pdf_extractor-main\backend"
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from chunked_extractor import ChunkedInsuranceExtractor

def test():
    api_key = os.getenv("OPENAI_API_KEY")
    extractor = ChunkedInsuranceExtractor(api_key=api_key, output_dir=os.path.join(backend_dir, "outputs"))
    
    # Check the code of the loaded class
    import inspect
    source = inspect.getsource(extractor.process_pdf_with_verification)
    print("Source contains 'claimsCount':", "claimsCount" in source)
    
    # Find the line with claimsCount
    lines = source.splitlines()
    for i, line in enumerate(lines):
        if "claimsCount" in line:
            print(f"Line {i}: {line.strip()}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(r"c:\Users\Intern\server1\pdf_extractor\.env")
    test()
