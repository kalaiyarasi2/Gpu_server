import requests
import os

url = "http://localhost:8007/cognethro"
file_path = r"c:\Users\INTERN\main_project\Main--main\work_compenstaion\sources\A1 Escort Services, LLC - Acord.pdf"

if not os.path.exists(file_path):
    print(f"Error: File not found at {file_path}")
    exit(1)

with open(file_path, "rb") as f:
    files = {"file": f}
    print(f"Sending POST request to {url}...")
    response = requests.post(url, files=files)

print(f"Status Code: {response.status_code}")
print("Response JSON:")
print(response.json())
