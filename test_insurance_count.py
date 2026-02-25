import requests
import os

url = "http://localhost:8007/cognethro"
file_path = r"c:\Users\INTERN\main_project\Main--main\Unified_PDF_Platform\uploads\Atlas 20-21.pdf"

if not os.path.exists(file_path):
    # Try alternate location if not in uploads
    file_path = r"c:\Users\INTERN\main_project\Main--main\Insurance_pdf_extractor-main\backend\sources\Atlas 20-21.pdf"

if not os.path.exists(file_path):
    print(f"Error: File not found at {file_path}")
    exit(1)

with open(file_path, "rb") as f:
    files = {"file": f}
    print(f"Sending POST request to {url} with {os.path.basename(file_path)}...")
    response = requests.post(url, files=files)

print(f"Status Code: {response.status_code}")
print("Response JSON:")
print(response.json())
