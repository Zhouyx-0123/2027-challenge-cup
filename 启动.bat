@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
cd /d "%~dp0"

title 物流企业碳感知智能决策系统 - 一键启动

echo.
echo ==============================================================
echo   物流企业碳感知智能决策系统
echo   碳感知 · 智能决策 · 可审计碳账
echo ==============================================================
echo.

REM ---------- 找 Python ----------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [X] 没找到 Python。
    echo.
    echo     请先安装 Python 3.8 及以上版本，并在安装时勾选
    echo     "Add Python to PATH"。下载地址：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo [1/4] 检查 Python 版本...
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>&1
if errorlevel 1 (
    echo     [X] Python 版本过低，需要 3.8 及以上。
    %PY% --version
    pause
    exit /b 1
)
%PY% --version

echo.
echo [2/4] 检查依赖包（缺什么装什么）...
%PY% -c "import streamlit, pandas, numpy, pyarrow, plotly" >nul 2>&1
if errorlevel 1 (
    echo     正在安装依赖，首次约需 1-3 分钟...
    REM 优先离线 wheels/：断网的答辩现场也能装；没有 wheels 或离线装不上再联网
    if exist "wheels\*.whl" (
        echo     发现 wheels\ 离线包，先试离线安装...
        %PY% -m pip install --no-index --find-links=wheels -r requirements.txt --quiet --disable-pip-version-check
        if errorlevel 1 (
            echo     离线安装失败，回退联网安装...
            %PY% -m pip install -r requirements.txt --quiet --disable-pip-version-check
        )
    ) else (
        %PY% -m pip install -r requirements.txt --quiet --disable-pip-version-check
    )
    if errorlevel 1 (
        echo     [X] 依赖安装失败。可手动执行：
        echo         %PY% -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo     依赖安装完成。
) else (
    echo     依赖齐备。
)

echo.
echo [3/4] 环境自检 + 可选复算...
%PY% -m tools.launch --check
if errorlevel 1 (
    echo.
    echo     [!] 自检有未通过项，继续启动（页面会提示，不白屏）。
)

set "HAS_RAW="
if exist "data\raw\*.xlsx" set "HAS_RAW=1"
if exist "data\raw\*.xls" set "HAS_RAW=1"
if exist "data\raw\*.csv" set "HAS_RAW=1"
if defined HAS_RAW (
    echo     检测到 data\raw 附件，开始复算...
    %PY% -m src.pipeline
) else (
    echo     data\raw 为空：跳过复算，直接使用现有结果表。
    echo     （避免真实结果被合成演示样本覆盖）
)

echo.
echo [4/4] 启动应用...
echo     浏览器地址：http://localhost:8501
echo     停止服务：在此窗口按 Ctrl+C
echo     --------------------------------------------------------------
echo.

%PY% -m streamlit run app/app.py --server.port 8501 --browser.gatherUsageStats false --client.showSidebarNavigation false --client.toolbarMode minimal

echo.
echo 服务已停止。
pause
