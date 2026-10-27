# -*- coding: utf-8 -*-
"""数据质量与估算值留痕：只读结果表，脏数据不阻断主流程。"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

import services

STATIC_PATH = Path(__file__).resolve().parents[1] / "static" / "demo_quality.json"
STYLE = """
<style>
.st-key-estimate_audit{border-radius:18px;padding:16px 18px 12px;
  background:linear-gradient(180deg,rgba(245,176,76,.10),rgba(245,176,76,.035));
  box-shadow:inset 0 0 0 1.5px rgba(245,176,76,.48),0 14px 40px rgba(0,0,0,.22);
  margin:8px 0 18px}
.est-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;
  margin-bottom:8px}
.est-kicker{font:9px 'JetBrains Mono',monospace;letter-spacing:.12em;color:#F5B04C}
.est-title{font-size:17px;font-weight:740;color:#F3F5F8;margin:5px 0 3px;letter-spacing:-.02em}
.est-sub{font-size:11px;color:#9AA2AD;line-height:1.65}
.est-count{font:700 25px 'JetBrains Mono',monospace;color:#FFC46B;line-height:1}
.est-count span{display:block;font:9px 'JetBrains Mono',monospace;color:#9AA2AD;
  text-align:right;margin-top:5px;letter-spacing:.06em}
.est-reasons{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 6px}
.est-reason{font-size:10px;color:#FFD89B;padding:5px 8px;border-radius:7px;
  background:rgba(245,176,76,.10);box-shadow:inset 0 0 0 1px rgba(245,176,76,.22)}
@media(max-width:760px){.est-head{flex-direction:column}.est-count span{text-align:left}}
</style>
"""


NOTICE_STYLE = """
<style>
.fg-estimate{border-radius:16px;padding:14px 16px;margin:10px 0;
  background:rgba(245,176,76,.10);box-shadow:inset 0 0 0 1.5px rgba(245,176,76,.52)}
.fg-estimate b{display:block;color:#FFC46B;font-size:13px;margin-bottom:6px}
.fg-estimate p{margin:4px 0;color:#C7CDD6;font-size:12px;line-height:1.7}
.fg-estimate code{color:#FFD89B;background:rgba(245,176,76,.10);padding:1px 5px;border-radius:4px}
</style>
"""


def render_estimate_notice(title: str, lines: List[str]) -> None:
    body = "".join("<p>%s</p>" % html.escape(str(line)) for line in lines)
    st.markdown(
        NOTICE_STYLE + "<div class='fg-estimate'><b>%s</b>%s</div>"
        % (html.escape(title), body), unsafe_allow_html=True)


def _static_quality() -> Dict[str, Any]:
    try:
        return json.loads(STATIC_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"counts": {}, "samples": []}


def estimate_rows(limit: int = 30) -> pd.DataFrame:
    """先过滤再截断，绝不对 3.6 万行绘 Styler。"""
    frame = services.wide()
    if frame.empty or "flag_estimated" not in frame.columns:
        return pd.DataFrame()
    mask = pd.to_numeric(frame["flag_estimated"], errors="coerce").fillna(0) == 1
    picked = frame.loc[mask]
    columns = ["waybill_no", "vehicle_type", "load_t", "dist_km", "dist_source",
               "speed_method", "estimate_note"]
    present = [col for col in columns if col in picked.columns]
    out = picked[present].head(limit).copy()
    return out.rename(columns={
        "waybill_no": "运单号", "vehicle_type": "车型", "load_t": "载重(t)",
        "dist_km": "里程(km)", "dist_source": "里程来源",
        "speed_method": "速度来源", "estimate_note": "估算原因",
    })


def _style_estimates(frame: pd.DataFrame):
    return frame.style.set_properties(
        **{"background-color": "rgba(245,176,76,.12)",
           "box-shadow": "inset 0 0 0 1px rgba(245,176,76,.36)"}
    )


def render_estimate_audit() -> None:
    st.markdown(STYLE, unsafe_allow_html=True)
    counts = services.flag_counts()
    reasons = services.quality().get("estimate_reason_counts") or {}
    rows_wide = int(counts.get("rows_wide") or 0)
    rows_dedup = int(counts.get("rows_dedup") or 0)
    estimated = int(counts.get("estimated_rows") or 0)
    frame = estimate_rows()

    static = False
    if frame.empty and not services.available().get("quality", False):
        snapshot = _static_quality()
        counts = snapshot.get("counts") or {}
        reasons = {}
        rows_wide = int(counts.get("rows_wide") or 0)
        rows_dedup = int(counts.get("rows_dedup") or 0)
        estimated = int(counts.get("estimated_rows") or 0)
        frame = pd.DataFrame(snapshot.get("samples") or [])
        static = True

    reason_html = "".join(
        "<span class='est-reason'>%s × %s</span>" % (str(name), int(number))
        for name, number in list(reasons.items())[:4])
    with st.container(key="estimate_audit"):
        st.markdown(
            "<div class='est-head'><div><div class='est-kicker'>ESTIMATION AUDIT</div>"
            "<div class='est-title'>估算值留痕与数据质量</div>"
            "<div class='est-sub'>%s</div></div>"
            "<div class='est-count'>%d<span>VALID SEGMENTS</span></div></div>%s" % (
                "结果表未生成，当前展示静态演示快照" if static else
                "所有被历史均值、行业均值或车型额定值填补的字段都在此留痕",
                estimated,
                "<div class='est-reasons'>" + reason_html + "</div>" if reason_html else "",
            ),
            unsafe_allow_html=True)
        if not frame.empty:
            st.dataframe(_style_estimates(frame), width="stretch", hide_index=True)
        else:
            st.caption("当前结果表没有估算行。")
        st.caption(
            "宽表 %d 行，进入基准核算 %d 行；黄框仅标记估算，不等同于数据错误。"
            "匹配不到发票、车型或里程时，数据层自动填补并留痕，不中断整条流水线。"
            % (rows_wide, rows_dedup))


__all__ = ["estimate_rows", "render_estimate_audit", "render_estimate_notice"]
