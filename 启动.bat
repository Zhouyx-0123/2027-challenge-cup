@echo off
chcp 65001 >nul 2>&1
setlocal
set "PYTHONUNBUFFERED=1"
cd /d "%~dp0"
title CarbonTide - Main System

REM Prefer this project's environment, then an activated environment.
set "CARBONTIDE_PYTHON="
set "CARBONTIDE_PY_ARGS="
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
    if not errorlevel 1 set "CARBONTIDE_PYTHON=%CD%\.venv\Scripts\python.exe"
)
if not defined CARBONTIDE_PYTHON if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
    "%VIRTUAL_ENV%\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
    if not errorlevel 1 set "CARBONTIDE_PYTHON=%VIRTUAL_ENV%\Scripts\python.exe"
)
if not defined CARBONTIDE_PYTHON (
    python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
    if not errorlevel 1 set "CARBONTIDE_PYTHON=python"
)
if not defined CARBONTIDE_PYTHON (
    py -3 -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
    if not errorlevel 1 (
        set "CARBONTIDE_PYTHON=py"
        set "CARBONTIDE_PY_ARGS=-3"
    )
)
if not defined CARBONTIDE_PYTHON (
    echo [X] Python 3.10 or newer was not found.
    echo     Install Python and select Add Python to PATH, then start again.
    if "%~1"=="" pause
    exit /b 1
)

"%CARBONTIDE_PYTHON%" %CARBONTIDE_PY_ARGS% -c "import pathlib,sys; r=pathlib.Path.cwd(); needed=['carbon_disclosure/dist/core.js','carbon_disclosure/dist/platform.css','app/components/carbon_disclosure_panel.py','app/main_views.py']; missing=[n for n in needed if not (r/n).is_file()]; v=r/'app/main_views.py'; view=v.read_text(encoding='utf-8-sig') if v.is_file() else ''; connected='carbon_disclosure_panel.render_carbon_disclosure_panel()' in view and 'def _render_ledger_e0' not in view and not (r/'app/components/carbon_ledger_panel.py').exists(); ok=not missing and connected; print('[X] Incomplete project or obsolete ledger: replace app/, tools/, carbon_disclosure/ and remove carbon_ledger_panel.py.') if not ok else None; sys.exit(0 if ok else 1)"
if errorlevel 1 (
    if "%~1"=="" pause
    exit /b 1
)

echo CarbonTide main system - includes the disclosure ledger.
echo.
REM Keep the original Windows launcher's raw-data recalculation policy.
set "CARBONTIDE_RECALC=--no-recalc"
if exist "data\raw\*.xlsx" set "CARBONTIDE_RECALC="
if exist "data\raw\*.xls" set "CARBONTIDE_RECALC="
if exist "data\raw\*.csv" set "CARBONTIDE_RECALC="
"%CARBONTIDE_PYTHON%" %CARBONTIDE_PY_ARGS% -m tools.launch --open --no-routes %CARBONTIDE_RECALC% %*
set "CARBONTIDE_EXIT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %CARBONTIDE_EXIT%
