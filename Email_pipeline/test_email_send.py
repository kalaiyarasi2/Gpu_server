import os
import sys
from datetime import datetime

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from tools import send_email_with_results
from universal_trash import move_to_trash

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
        move_to_trash(test_file, module_name="Email_pipeline")

if __name__ == "__main__":
    test_send()
