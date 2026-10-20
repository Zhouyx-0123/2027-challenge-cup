#!/bin/bash
# 一键启动（macOS / Linux）· 物流企业碳感知智能决策系统
# 双击运行；或在终端执行 bash 启动.command

cd "$(dirname "$0")" || exit 1

echo ""
echo "=============================================================="
echo "  物流企业碳感知智能决策系统"
echo "  碳感知 · 智能决策 · 可审计碳账"
echo "=============================================================="
echo ""

# ---------- 找 Python ----------
PY=""
if command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif command -v python >/dev/null 2>&1; then
    PY="python"
fi
if [ -z "$PY" ]; then
    echo "[X] 没找到 Python3。"
    echo "    请先安装 Python 3.8 及以上：brew install python"
    echo ""
    read -r -p "按回车键退出..."
    exit 1
fi

echo "[1/4] 检查 Python 版本..."
if ! $PY -c "import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)"; then
    echo "    [X] Python 版本过低，需要 3.8 及以上。"
    exit 1
fi
$PY --version

echo ""
echo "[2/4] 检查依赖包（缺什么装什么）..."
if ! $PY -c "import streamlit, pandas, numpy, pyarrow, plotly" >/dev/null 2>&1; then
    echo "    正在安装依赖，首次约需 1-3 分钟..."
    if ! $PY -m pip install -r requirements.txt --quiet --disable-pip-version-check; then
        echo "    [X] 依赖安装失败。可手动执行："
        echo "        $PY -m pip install -r requirements.txt"
        echo ""
        read -r -p "按回车键退出..."
        exit 1
    fi
    echo "    依赖安装完成。"
else
    echo "    依赖齐备。"
fi

echo ""
echo "[3/4] 环境自检..."
$PY -m tools.launch --check || echo "    [!] 自检有未通过项，继续启动（页面会提示，不白屏）。"

echo ""
echo "[4/4] 启动五页..."
echo "    浏览器地址：http://localhost:8501"
echo "    停止服务：在此窗口按 Ctrl+C"
echo "    --------------------------------------------------------------"
echo ""

$PY -m streamlit run app/app.py --server.port 8501 --browser.gatherUsageStats false

echo ""
echo "服务已停止。"
read -r -p "按回车键退出..."
