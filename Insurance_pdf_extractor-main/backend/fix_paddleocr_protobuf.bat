@echo off
REM Fix PaddleOCR Protobuf Conflict Script
REM This script fixes the protobuf version conflict for PaddleOCR

echo ================================================================
echo PaddleOCR Protobuf Fix Script
echo ================================================================
echo.

REM Activate virtual environment
echo Step 1: Activating virtual environment...
call C:\Users\Intern\gpu\Gpu_server\venv\Scripts\activate
echo.

REM Reinstall pip to fix _socket issue
echo Step 2: Reinstalling pip...
python -m ensurepip --upgrade
python -m pip install --upgrade pip
echo.

REM Uninstall conflicting protobuf
echo Step 3: Removing conflicting protobuf version...
pip uninstall -y protobuf
echo.

REM Install correct protobuf version for PaddlePaddle
echo Step 4: Installing protobuf 3.20.2 (required by PaddlePaddle)...
pip install protobuf==3.20.2 --force-reinstall
echo.

REM Verify installation
echo Step 5: Verifying PaddleOCR installation...
python -c "import paddle; print('✅ PaddlePaddle:', paddle.__version__)"
python -c "import paddleocr; print('✅ PaddleOCR installed')"
python -c "from paddleocr import PPStructure; print('✅ PPStructure available')"
python -c "import protobuf; print('✅ Protobuf:', protobuf.__version__)"
echo.

echo ================================================================
echo ✅ Fix complete! Now you can run PaddleOCR
echo ================================================================
echo.
echo Test PaddleOCR with:
echo    python paddleocr_enhancer.py
echo.

pause
