"""
Fix PaddleOCR Protobuf Version Conflict
This script resolves the protobuf version mismatch between PaddlePaddle and other packages
"""
import subprocess
import sys

def run_command(cmd, description):
    """Run a command and print results"""
    print(f"\n{'='*60}")
    print(f"[*] {description}")
    print(f"{'='*60}")
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            capture_output=True, 
            text=True,
            check=False
        )
        
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        
        if result.returncode == 0:
            print(f"[+] Success")
        else:
            print(f"[-] Warning: Command returned code {result.returncode}")
        
        return result.returncode == 0
    except Exception as e:
        print(f" Error: {e}")
        return False

def main():
    print("\n" + "="*60)
    print("PaddleOCR Protobuf Fix Utility")
    print("="*60)
    
    # Step 1: Upgrade pip
    run_command(
        f"{sys.executable} -m pip install --upgrade pip",
        "Step 1: Upgrading pip"
    )
    
    # Step 2: Uninstall conflicting protobuf
    run_command(
        f"{sys.executable} -m pip uninstall -y protobuf",
        "Step 2: Removing conflicting protobuf"
    )
    
    # Step 3: Install correct protobuf version
    run_command(
        f"{sys.executable} -m pip install protobuf==3.20.2 --force-reinstall",
        "Step 3: Installing protobuf 3.20.2"
    )
    
    # Step 4: Verify installations
    print("\n" + "="*60)
    print(" Verification")
    print("="*60)
    
    checks = [
        ("PaddlePaddle", "import paddle; print(f'Version: {paddle.__version__}')"),
        ("PaddleOCR", "import paddleocr; print('Installed')"),
        ("PPStructure", "from paddleocr import PPStructure; print('Available')"),
        ("Protobuf", "import google.protobuf; print(f'Version: {google.protobuf.__version__}')"),
    ]
    
    all_ok = True
    for name, code in checks:
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                print(f" {name}: {result.stdout.strip()}")
            else:
                print(f" {name}: Failed")
                print(f"   Error: {result.stderr.strip()}")
                all_ok = False
        except Exception as e:
            print(f" {name}: {e}")
            all_ok = False
    
    print("\n" + "="*60)
    if all_ok:
        print(" ALL CHECKS PASSED!")
        print("="*60)
        print("\n PaddleOCR is ready to use!")
        print("\nTest it with:")
        print("   python paddleocr_enhancer.py")
    else:
        print(" SOME CHECKS FAILED")
        print("="*60)
        print("\nYou may need to:")
        print("1. Reinstall PaddlePaddle: pip install paddlepaddle-gpu --force-reinstall")
        print("2. Reinstall PaddleOCR: pip install paddleocr --force-reinstall")
    
    print()

if __name__ == "__main__":
    main()
