@echo off
setlocal

cd /d "%~dp0"

if "%~1"=="--check" (
    echo run_app.bat is ready.
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating local Python environment...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        python -m venv .venv
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Could not create .venv. Install Python 3.11 or newer and try again.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo Installing or updating required packages...
python -m pip install --upgrade pip
if errorlevel 1 goto error

python -m pip install -r requirements.txt
if errorlevel 1 goto error

echo Starting dashboard at http://localhost:8502
python -m streamlit run dashboard.py --server.port 8502
if errorlevel 1 goto error

exit /b 0

:error
echo.
echo The app could not start. Check the error above.
pause
exit /b 1
