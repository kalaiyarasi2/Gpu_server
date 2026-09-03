@echo off
echo ==========================================
echo Starting PDF Invoice Extractor (FastAPI)
echo ==========================================
echo.

:: Simple activation and run (no complex IF blocks to avoid syntax errors)
call venv\Scripts\activate
echo Starting Server on port 8006...
uvicorn app_v3:app --host 0.0.0.0 --port 8006 --workers 4

pause
