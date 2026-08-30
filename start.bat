@echo off
rem SkillTrove dashboard launcher: restart web server and open browser.
rem ASCII-only on purpose: UTF-8 Chinese breaks on GBK consoles.
cd /d %~dp0
if not exist .venv (
  echo [SkillTrove] ERROR: .venv not found, run install.bat first.
  pause
  exit /b 1
)
echo [SkillTrove] stopping old instance on :8000 (if any)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do taskkill /F /PID %%p >nul 2>&1
echo [SkillTrove] starting dashboard: http://127.0.0.1:8000
start "SkillTrove Web" .venv\Scripts\python web\app.py
%SystemRoot%\System32\timeout.exe /t 2 /nobreak >nul
start "" http://127.0.0.1:8000
