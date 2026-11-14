"""Disclosure ledger tab: embeds trusted, local UI assets without a second server."""
from pathlib import Path
import inspect
import sys

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from carbon_disclosure import build_html


def render_carbon_disclosure_panel() -> None:
    st.caption("披露碳账 v0.9 · 查看来源与范围占比，按车辆汇总并追溯原始记录。")
    try:
        html = build_html(embedded=True)
    except (OSError, ValueError) as exc:
        st.error("披露碳账资源不完整，请按仓库根目录的《碳账快速开始》补齐 carbon_disclosure 文件夹。")
        st.caption(str(exc))
        return
    # Newer runtimes measure HTML content natively; older supported runtimes
    # retain a fixed viewport. Never interpolate uploaded CSV into this HTML.
    params = inspect.signature(st.iframe).parameters
    if params["height"].default == "content" and "alt" in params:
        st.iframe(html, width="stretch", height="content",
                  alt="范围一二三披露碳账：导入CSV、查看排放与披露依据")
    else:
        st.iframe(html, width="stretch", height=1280)
    with st.expander("离线使用与数据备份"):
        st.download_button(
            "下载离线碳账网页", build_html().encode("utf-8"),
            file_name="碳账_离线工作台.html", mime="text/html", key="disclosure_offline_html"
        )
        st.caption("导入与附录保存在当前浏览器；换设备前请在碳账内备份附录。主系统重新运行后可从本机历史批次恢复。")
