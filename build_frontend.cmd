@echo off
echo Building Frontend...
cd Unified_PDF_Platform\frontend
echo Installing dependencies...
call npm.cmd install
echo Building...
call npm.cmd run build
echo.
echo Frontend build complete! You can now start the app.
pause
