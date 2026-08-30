@echo off
rem ============================================================
rem  SkillTrove all-in-one launcher (venv -> deps -> check -> web)
rem  ASCII-only on purpose: UTF-8 Chinese breaks on GBK consoles.
rem  Usage: double-click skilltrove.bat or run in terminal.
rem ============================================================
cd /d %~dp0
setlocal

echo.
echo  === SkillTrove ===
echo.

rem 1) venv
if not exist .venv\Scripts\python.exe (
  echo  [1/4] creating venv .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo   !! Python 3.10+ required and on PATH. Install it and retry.
    pause & exit /b 1
  )
) else (
  echo  [1/4] venv ready
)

rem 2) deps
.venv\Scripts\python -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo  [2/4] installing web deps ...
  .venv\Scripts\python -m pip install -r requirements-web.txt
  if errorlevel 1 ( echo   !! pip install failed. Check network/proxy & pause & exit /b 1 )
) else (
  echo  [2/4] deps ready
)

rem 3) self-check
echo  [3/4] environment check ...
.venv\Scripts\python check.py --web

rem 4) start dashboard
echo  [4/4] dashboard: http://127.0.0.1:8000  (close this window to stop)
start "" http://127.0.0.1:8000
.venv\Scripts\python web\app.py

echo.
echo  Server stopped.
pause
