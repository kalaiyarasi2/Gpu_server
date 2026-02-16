@echo off
echo ==========================================
echo Updating PDF Invoice Extractor
echo ==========================================
echo.

echo Pulling latest code from GitHub...
git pull origin main
if %errorlevel% neq 0 (
    echo Error: Git pull failed.
    pause
    exit /b %errorlevel%
)

echo.
echo Restarting application via PM2...
pm2 restart pdf-extractor
if %errorlevel% neq 0 (
    echo Note: PM2 restart failed. Did you start it with 'pm2 start' first?
    echo Attempting to start normally for local check...
)

echo.
echo Update successful!
pause
