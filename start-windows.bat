@echo off
rem Beyond ERP launcher for Windows. Double-click to start.
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python 3.9+ is required. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
%PY% -c "import openpyxl" >nul 2>nul
if errorlevel 1 (
  echo Installing optional Excel support ^(openpyxl^)...
  %PY% -m pip install --user --quiet openpyxl
)
%PY% run.py %*
pause
