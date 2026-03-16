import requests
import os

def test_extraction():
    url = "http://localhost:8007/api/extract"
    # Use the file that was already uploaded if it still exists
    file_path = r"C:\Users\Intern\pdf_extractor\Unified_PDF_Platform\uploads\Abel I - Disbursement bank statement - Jan 2026.pdf"
    
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}. Creating a dummy PDF for testing.")
        # Minimal PDF creation (just enough for the identifier to score it)
        with open("test_bank.pdf", "wb") as f:
            # We can just write some text that looks like a bank statement
            # But it needs to be a PDF. Let's just use an existing PDF if possible.
            # For now, I'll assume the file exists since the app just saved it.
            pass
        file_path = "test_bank.pdf"

    if os.path.exists(file_path):
        with open(file_path, "rb") as f:
            files = {"file": f}
            print(f"Sending request to {url} with {file_path}...")
            response = requests.post(url, files=files)
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
    else:
        print("Test file not found and dummy creation skipped.")

if __name__ == "__main__":
    test_extraction()
