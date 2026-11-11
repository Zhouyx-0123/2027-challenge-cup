@echo off
setlocal
cd /d "%~dp0"
python -c "import sys;sys.exit(sys.version_info < (3,10))" >nul 2>&1
if not errorlevel 1 (
    python -m carbon_disclosure %*
    goto end
)
py -c "import sys;sys.exit(sys.version_info < (3,10))" >nul 2>&1
if not errorlevel 1 (
    py -m carbon_disclosure %*
    goto end
)
where node >nul 2>&1
if not errorlevel 1 (
    node carbon_disclosure/server.cjs
    goto end
)
echo Python 3.10+ or Node.js 18+ was not found.
echo You can also open the offline HTML file in this folder.
:end
pause
