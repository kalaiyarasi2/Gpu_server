import os
import sys
from datetime import datetime

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from tools import send_email_with_results

def test_send():
    recipient = "Saleemy@gmail.com"
    subject = f"TEST: Extraction Results - {datetime.now().strftime('%H:%M:%S')}"
    body = "This is a test email to verify the automated extraction result delivery."
    
    # Create a dummy test file
    test_file = "test_extraction_result.txt"
    with open(test_file, "w") as f:
        f.write("This is a dummy extraction result for testing.")
    
    abs_path = os.path.abspath(test_file)
    success = send_email_with_results(recipient, subject, body, [abs_path])
    
    if success:
        print("[SUCCESS] Test email sent.")
    else:
        print("[FAIL] Test email failed.")
        
    # Cleanup
    if os.path.exists(test_file):
        os.remove(test_file)

if __name__ == "__main__":
    test_send()
