@echo off
rem Run the map UI with the project's virtualenv: ui.cmd
setlocal
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Virtualenv not found: %PYTHON%
    echo Create it first: py -3.10 -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    exit /b 1
)

"%PYTHON%" "%ROOT%ui.py" %*
exit /b %ERRORLEVEL%
