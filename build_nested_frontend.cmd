@echo off
echo Building Nested Frontend (document-insight-engine-main)...
cd Unified_PDF_Platform\frontend\document-insight-engine-main
echo Installing dependencies...
call npm.cmd install
echo Building...
call npm.cmd run build
echo.
echo Nested Frontend build complete!
pause
