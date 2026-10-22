@echo off
rem Run the parser with the project's virtualenv, no activation needed.
rem Usage: run.cmd -q "restaurants in Ocean City, MD" --limit 50 -v
setlocal
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Virtualenv not found: %PYTHON%
    echo Create it first:
    echo   py -3.10 -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -r requirements.txt
    echo   python -m playwright install chromium
    exit /b 1
)

"%PYTHON%" "%ROOT%main.py" %*
exit /b %ERRORLEVEL%
