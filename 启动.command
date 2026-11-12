#!/bin/bash
# Main system launcher, including the embedded disclosure ledger.
set -u
export PYTHONUNBUFFERED=1
cd "$(dirname "$0")" || exit 1

CARBONTIDE_PYTHON=""
if [ -x ".venv/bin/python" ] && .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
    CARBONTIDE_PYTHON="$PWD/.venv/bin/python"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ] && "$VIRTUAL_ENV/bin/python" -c 'import sys; sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
    CARBONTIDE_PYTHON="$VIRTUAL_ENV/bin/python"
else
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
            CARBONTIDE_PYTHON="$candidate"
            break
        fi
    done
fi
if [ -z "$CARBONTIDE_PYTHON" ]; then
    printf '%s\n' '[X] 请先安装 Python 3.10 或以上版本，再重新启动。'
    exit 1
fi

"$CARBONTIDE_PYTHON" -c "import pathlib,sys; r=pathlib.Path.cwd(); needed=['carbon_disclosure/dist/core.js','carbon_disclosure/dist/platform.css','app/components/carbon_disclosure_panel.py','app/main_views.py']; missing=[n for n in needed if not (r/n).is_file()]; v=r/'app/main_views.py'; connected=v.is_file() and 'carbon_disclosure_panel.render_carbon_disclosure_panel()' in v.read_text(encoding='utf-8-sig'); ok=not missing and connected; print('[X] Incomplete project: update app/, tools/ and carbon_disclosure/ folders, not only root files.') if not ok else None; sys.exit(0 if ok else 1)" || exit 1

printf '%s\n' '主系统统一启动，包含披露碳账。' '碳账入口：碳核算与台账 - 披露碳账（范围一二三）'
"$CARBONTIDE_PYTHON" -m tools.launch --open --no-recalc "$@"
CARBONTIDE_EXIT=$?
if [ "$#" -eq 0 ] && [ -t 0 ]; then
    read -r -p '服务已停止，按回车键退出。' || true
fi
exit "$CARBONTIDE_EXIT"
