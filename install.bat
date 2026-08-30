@echo off
rem SkillTrove installer: create venv + install deps + self-check.
rem ASCII-only on purpose: UTF-8 Chinese breaks on GBK consoles.
cd /d %~dp0
echo [1/3] creating venv (.venv)...
python -m venv .venv
if errorlevel 1 ( echo venv creation failed. Python 3.10+ required. & pause & exit /b 1 )
if exist .venv\Lib\site-packages\fastapi (
  echo .venv already has deps, skipping install.
) else (
  echo [2/3] installing web deps...
  .venv\Scripts\python -m pip install -r requirements-web.txt
  if errorlevel 1 ( echo pip install failed. & pause & exit /b 1 )
)
echo [3/3] environment check...
.venv\Scripts\python check.py --web
echo Done. Run start.bat to launch the dashboard.
pause
