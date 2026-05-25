@echo off
REM ===========================================================================
REM  run.bat — Start Greenpack Inspector (web app) and open it in the browser
REM ===========================================================================
REM
REM  First time only:
REM     pip install -r requirements.txt
REM
REM  Then just double-click this file.
REM ===========================================================================

echo.
echo ============================================
echo   Greenpack Inspector v4.0  (Web App)
echo ============================================
echo.

cd /d "%~dp0"

REM Check dependencies are installed
python -c "import fastapi, uvicorn, cv2, skimage, reportlab" 2>nul
if errorlevel 1 (
    echo Installing dependencies (first run)...
    python -m pip install -r requirements.txt
)

echo Starting server at http://127.0.0.1:8000  ...
echo (Close this window to stop the server.)
echo.

REM Open the browser after a short delay
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"

REM Launch the server
cd backend
python -m uvicorn server:app --host 127.0.0.1 --port 8000

pause
