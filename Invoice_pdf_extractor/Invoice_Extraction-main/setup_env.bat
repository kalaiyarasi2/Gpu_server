@echo off
echo ==========================================
echo PDF Extractor: Environment Setup
echo ==========================================
echo.
echo Creating Python Virtual Environment...
python -m venv venv
if %errorlevel% neq 0 (
    echo Error: Python not found. Please install Python.
    pause
    exit /b %errorlevel%
)

echo Activating environment...
call venv\Scripts\activate

echo Installing dependencies from requirements.txt...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo Error: Failed to install dependencies.
    pause
    exit /b %errorlevel%
)

echo.
echo Setup complete! 
echo 1. Create your .env file with OPENAI_API_KEY.
echo 2. Run start_app.bat to begin.
echo.
pause
