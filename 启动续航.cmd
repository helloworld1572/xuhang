@echo off
chcp 65001 >nul
cd /d "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. See README.md for setup.
  pause
  exit /b 1
)
if not exist "dist\index.html" (
  echo Web files not found. Run npm ci and npm run build in this folder.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 "%~dp0start.py"
pause
