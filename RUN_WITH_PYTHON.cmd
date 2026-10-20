@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto :error
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :error
".venv\Scripts\python.exe" app.py
exit /b %errorlevel%
:error
echo.
echo Не удалось запустить. Проверьте Python 3.12 и интернет.
pause
exit /b 1