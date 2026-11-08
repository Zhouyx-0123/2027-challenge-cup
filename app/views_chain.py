# -*- coding: utf-8 -*-
"""🔗 数据链总览：六条链的现状 + G2 三源对账 + 最近一次接收 + 口径红线。

定位：**只读接线层**。本页不 import 任何算法、不写 data/processed，
只把 app/services.py 已经读成事实的文件拼成一张「哪条链走到哪一步」的看板。

三条纪律（页面自己也要守住，否则就是新的口径）：
    1. 状态词与颜色沿用 outputs/report/mind_map_data_chain.py 的语义色，
       不新造「完成度百分比」这种没有来源的指标；
    2. 三源（E₀ 运单 / 财务发票 / OBD 实测）没有共同覆盖期时，只写「暂不可比」，
       **不生成任何偏差百分比**；
    3. 设备实流落在 data/raw，不进 E₀ —— 页面上必须与 demo 基线分开写。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

import streamlit as st

import services
from components import theme

#: 语义色 / 状态词：与数据链思维图（outputs/report/mind_map_data_chain.py）一致
CHAIN_COLORS: Dict[str, str] = {
    "done": "#5FE3D2",     # 已闭环
    "pending": "#E9C46A",  # 未批准
    "wip": "#FF8A5B",      # 联调中
    "empty": "#A78BFA",    # 数据空
    "demo": "#7ED957",     # 演示
}
STATUS_LABELS: Dict[str, str] = {
    "done": "已闭环",
    "pending": "未批准",
    "wip": "联调中",
    "empty": "数据空",
    "demo": "演示",
}

CSS = """<style>
/* ---------- 页头状态条 ---------- */
.ch-status{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;
  padding:12px 18px;border-radius:14px;background:rgba(255,255,255,.055);
  box-shadow:0 0 0 1px rgba(255,255,255,.09);margin:2px 0 4px;
  font:12px/1.7 'JetBrains Mono',monospace;color:#9AA2AD;
  font-variant-numeric:tabular-nums}
.ch-status .k{color:#5B636E;letter-spacing:.1em;font-size:10px;text-transform:uppercase}
.ch-status b{color:#C7CDD6;font-weight:600}
.ch-status b.ok{color:#8BF3E6}
.ch-status b.warn{color:#FFC46B}
.ch-status .sep{color:#3A404A}

/* ---------- 六张链状态卡 ---------- */
.ch-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:4px 0 8px}
.ch-card{position:relative;min-width:0;padding:15px 16px 13px;border-radius:14px;
  background:rgba(255,255,255,.05);box-shadow:0 0 0 1px rgba(255,255,255,.08);overflow:hidden}
.ch-card::before{content:'';position:absolute;left:0;top:0;width:100%;height:2px;
  background:var(--ch,#8BF3E6);opacity:.85}
.ch-h{display:flex;align-items:center;gap:8px;min-width:0}
.ch-idx{flex:none;font:10px/1 'JetBrains Mono',monospace;color:#5B636E;letter-spacing:.08em}
.ch-name{flex:1 1 auto;min-width:0;font:600 13.5px/1.3 'Hanken Grotesk',sans-serif;color:#F3F5F8;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch-tag{flex:none;padding:2px 8px;border-radius:100px;font:600 10px/1.7 'Inter',sans-serif;
  color:var(--ch,#8BF3E6);background:rgba(255,255,255,.05);
  box-shadow:0 0 0 1px rgba(255,255,255,.12);white-space:nowrap}
.ch-v{margin-top:11px;font:700 22px/1.18 'Hanken Grotesk',sans-serif;color:var(--ch,#F3F5F8);
  font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch-v small{font:600 11px/1 'Inter',sans-serif;color:#5B636E;margin-left:5px;letter-spacing:.02em}
.ch-meta{margin-top:5px;font:11.5px/1.65 'Inter',sans-serif;color:#9AA2AD;min-height:2.6em}
.ch-src{margin-top:7px;font:10.5px/1.5 'JetBrains Mono',monospace;color:#5B636E;
  white-space:normal;overflow-wrap:normal}
.ch-src b{color:#7C848F;font-weight:500}
.ch-legend{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 6px;
  font:11.5px/1.6 'Inter',sans-serif;color:#5B636E}
.ch-legend span{display:inline-flex;align-items:center;gap:6px}
.ch-legend i{width:8px;height:8px;border-radius:3px;display:inline-block;background:var(--ch,#8BF3E6)}

/* ---------- 面板 ---------- */
.ch-panel{border-radius:14px;background:rgba(255,255,255,.045);
  box-shadow:0 0 0 1px rgba(255,255,255,.08);padding:15px 16px 14px;margin:0 0 14px}
.ch-panel-h{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:11px}
.ch-panel-h span:first-child{font:700 15px/1.3 'Hanken Grotesk',sans-serif;color:#F3F5F8}
.ch-panel-h span:last-child{font:10px/1.5 'JetBrains Mono',monospace;color:#5B636E;letter-spacing:.06em}

/* ---------- G2 三源 ---------- */
.ch-g2{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.ch-col{min-width:0;padding:12px 13px;border-radius:12px;background:rgba(255,255,255,.04);
  box-shadow:0 0 0 1px rgba(255,255,255,.07)}
.ch-col .k{font:10px/1.4 'JetBrains Mono',monospace;letter-spacing:.08em;color:#5B636E;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch-col .v{margin-top:8px;font:700 20px/1.15 'Hanken Grotesk',sans-serif;color:var(--ch,#F3F5F8);
  font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch-col .v small{font:600 11px/1 'Inter',sans-serif;color:#5B636E;margin-left:4px}
.ch-col .s{margin-top:6px;font:11px/1.65 'Inter',sans-serif;color:#9AA2AD;min-height:3.1em}
.ch-col .f{margin-top:7px;font:10px/1.4 'JetBrains Mono',monospace;color:#5B636E;
  white-space:normal;overflow-wrap:normal}
.ch-verdict{display:flex;gap:10px;align-items:flex-start;margin-top:12px;padding:11px 13px;
  border-radius:12px;background:rgba(233,196,106,.09);
  box-shadow:0 0 0 1px rgba(233,196,106,.24);
  font:12.5px/1.8 'Inter',sans-serif;color:#D9DEE6}
.ch-verdict .tag{flex:none;padding:2px 9px;border-radius:100px;background:rgba(233,196,106,.16);
  color:#E9C46A;font:700 10.5px/1.8 'Inter',sans-serif;white-space:nowrap}
.ch-verdict b{color:#E9C46A;font-weight:600}
.ch-note{margin-top:9px;font:11.5px/1.75 'Inter',sans-serif;color:#5B636E}
.ch-note b{color:#9AA2AD;font-weight:600}

/* ---------- 最近一次接收 ---------- */
.ch-rx{display:grid;grid-template-columns:1fr;gap:1px;border-radius:12px;overflow:hidden;
  background:rgba(255,255,255,.07)}
.ch-rx .row{display:flex;align-items:baseline;justify-content:space-between;gap:10px;
  padding:8px 12px;background:#0B0D10;min-width:0}
.ch-rx .row .k{font:11px/1.5 'Inter',sans-serif;color:#5B636E;white-space:nowrap;flex:none}
.ch-rx .row .v{font:600 12px/1.5 'JetBrains Mono',monospace;color:#E6EAF0;text-align:right;
  font-variant-numeric:tabular-nums;min-width:0;white-space:normal;overflow-wrap:anywhere}
.ch-rx .row .v.ok{color:#8BF3E6}
.ch-rx .row .v.warn{color:#FFC46B}
.ch-empty{margin-top:11px;padding:18px 14px;border-radius:12px;background:rgba(255,255,255,.03);
  box-shadow:0 0 0 1px rgba(255,255,255,.07);text-align:center}
.ch-empty .t{font:600 13px/1.5 'Hanken Grotesk',sans-serif;color:#C7CDD6}
.ch-empty .d{margin-top:6px;font:11px/1.8 'Inter',sans-serif;color:#5B636E}

/* ---------- 口径红线 ---------- */
.ch-rules{display:grid;grid-template-columns:repeat(auto-fit,minmax(266px,1fr));gap:10px;margin:4px 0 6px}
.ch-rule{padding:13px 14px;border-radius:12px;background:rgba(255,255,255,.04);
  box-shadow:0 0 0 1px rgba(255,255,255,.07);min-width:0}
.ch-rule .k{display:inline-block;margin-bottom:7px;padding:2px 8px;border-radius:100px;
  background:rgba(255,138,91,.12);color:#FFB08A;
  font:700 10px/1.7 'JetBrains Mono',monospace;letter-spacing:.06em}
.ch-rule .t{font:11.5px/1.8 'Inter',sans-serif;color:#9AA2AD}
.ch-rule .t b{color:#E6EAF0;font-weight:600}
.ch-rule .t em{font-style:normal;font-family:'JetBrains Mono',monospace;font-size:11px;color:#7CCBFF}

@media (max-width:1100px){
  .ch-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .ch-rules{grid-template-columns:repeat(2,minmax(0,1fr))}
}
/* 窄屏：G2 卡与接收卡从并排改为上下堆叠（只影响本页 #chain-g2row 容器） */
@media (max-width:1180px){
  .st-key-chain-g2row div[data-testid="stHorizontalBlock"]{flex-wrap:wrap;gap:14px}
  .st-key-chain-g2row div[data-testid="column"],
  .st-key-chain-g2row div[data-testid="stColumn"]{
    width:100%!important;flex:1 1 100%!important;min-width:100%!important}
}
@media (max-width:900px){
  .ch-g2{grid-template-columns:1fr}
  .ch-v{font-size:20px}
  .ch-col .s{min-height:0}
}
@media (max-width:680px){
  .ch-grid{grid-template-columns:1fr}
  .ch-rules{grid-template-columns:1fr}
}
</style>
"""


# ---------------------------------------------------------------------------
# 格式化：缺失一律破折号/空态，绝不补 0
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _n(value: Any, digits: int = 0) -> str:
    """与 services.fmt 同源：None/NaN 显示破折号，不编数字。"""
    return services.fmt(value, digits)


def _path(value: Any) -> str:
    """文件名：只在 / 与扩展名前留换行点，窄屏折行不把名字切碎。"""
    text = _e(value)
    for ext in (".json", ".csv", ".parquet", ".py"):
        text = text.replace(ext, "<wbr>" + ext)
    return text.replace("/", "/<wbr>")


def _utc(value: Any) -> str:
    """2026-11-07T02:00:00Z → 2026-11-07 02:00:00 UTC。"""
    text = str(value or "").strip()
    if not text:
        return "—"
    return text.replace("T", " ").replace("Z", "") + " UTC"


def _window(first: Any, last: Any) -> str:
    """把 UTC 时间窗压成一行；同一天时省略后一个日期。"""
    head = str(first or "").strip()
    tail = str(last or "").strip()
    if not head or not tail:
        return "—"
    head = head.replace("T", " ").replace("Z", "")
    tail = tail.replace("T", " ").replace("Z", "")
    if head[:10] == tail[:10]:
        return head[:19] + " → " + tail[11:19] + " UTC"
    return head[:19] + " → " + tail[:19] + " UTC"


def _duration(seconds: Any) -> str:
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    if total <= 0:
        return "—"
    if total < 60:
        return str(total) + " 秒"
    if total < 3600:
        return str(total // 60) + " 分 " + str(total % 60) + " 秒"
    return str(total // 3600) + " 时 " + str((total % 3600) // 60) + " 分"


def _size(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    if size <= 0:
        return "—"
    if size < 1024:
        return str(int(size)) + " B"
    if size < 1024 * 1024:
        return "%.1f KB" % (size / 1024.0)
    return "%.1f MB" % (size / 1024.0 / 1024.0)


# ---------------------------------------------------------------------------
# 页头状态条
# ---------------------------------------------------------------------------

def _status_strip(snap: Dict[str, Any]) -> str:
    gate_pass = snap.get("gate_pass") or 0
    gate_total = snap.get("gate_total") or 0
    gate_ok = bool(gate_total) and gate_pass >= gate_total
    parts = [
        "<span><span class='k'>RUN</span> <b>" + _e(snap.get("run_id") or "—") + "</b></span>",
        "<span class='sep'>·</span>",
        "<span><span class='k'>闸门</span> <b class='" + ("ok" if gate_ok else "warn") + "'>"
        + _n(gate_pass) + " / " + _n(gate_total) + "</b></span>",
        "<span class='sep'>·</span>",
        "<span><span class='k'>CODE</span> <b>" + _e(snap.get("code_version") or "—") + "</b></span>",
        "<span class='sep'>·</span>",
        "<span><span class='k'>SNAPSHOT</span> <b>kpi "
        + _e(str(snap.get("e0_generated") or "")[:10] or "—")
        + " · run " + _e(str(snap.get("published_at") or "")[:10] or "—") + "</b></span>",
        "<span class='sep'>·</span>",
        "<span><span class='k'>渲染</span> <b>" + _e(snap.get("snapshot_at") or "—") + "</b></span>",
    ]
    return "<div class='ch-status'>" + "".join(parts) + "</div>"


# ---------------------------------------------------------------------------
# 六张链状态卡
# ---------------------------------------------------------------------------

def _card(index: str, name: str, status: str, value: str, unit: str,
          meta: str, source: str) -> str:
    unit_html = "<small>" + _e(unit) + "</small>" if unit else ""
    return (
        "<div class='ch-card' style='--ch:" + CHAIN_COLORS[status] + "'>"
        "<div class='ch-h'>"
        "<span class='ch-idx'>" + _e(index) + "</span>"
        "<span class='ch-name'>" + _e(name) + "</span>"
        "<span class='ch-tag'>" + _e(STATUS_LABELS[status]) + "</span>"
        "</div>"
        "<div class='ch-v'>" + _e(value) + unit_html + "</div>"
        "<div class='ch-meta'>" + _e(meta) + "</div>"
        "<div class='ch-src'><b>" + _path(source) + "</b></div>"
        "</div>"
    )


def _chain_cards(snap: Dict[str, Any], vsp_delta: Any) -> str:
    cards = [
        _card("01", "主碳链 E₀", "done", _n(snap.get("e0_kg"), 3), "kgCO₂e",
              _n(snap.get("e0_trips")) + " 实趟 · " + _n(snap.get("e0_km")) + " km · "
              + _n(snap.get("gate_pass")) + "/" + _n(snap.get("gate_total")) + " PASS",
              "data/processed/kpi.json"),
        _card("02", "路段 · EF", "done",
              _n(snap.get("seg_passed")), "入簇趟",
              "5 速度档 × KMeans k=3（silhouette 0.425）· 有效碳 "
              + _n(snap.get("seg_carbon_valid_kg")) + " kg",
              "jd_segment_master_audit.json"),
        _card("03", "A4 碳台账", "pending", _n(snap.get("a4_t"), 3), "t CO₂e",
              _n(snap.get("a4_rows")) + " 趟行全 simulated · 正式行 "
              + _n(snap.get("a4_approved")) + " · 实质谓词 5 条全阻断",
              "data/processed/carbon_ledger_summary.json"),
        _card("04", "OBD 实测", "wip", _n(snap.get("obd_raw")), "行实流",
              "demo 基线 " + _n(snap.get("obd_demo_n")) + " 点 / "
              + _n(snap.get("obd_demo_vehicles")) + " 车 · VSP 对拍 +"
              + _n(vsp_delta, 2) + "%（demo）",
              "data/raw/obd_gps/obd_stream.csv"),
        _card("05", "财务发票", "empty", _n(snap.get("invoice_records")), "条",
              "车月 " + _n(snap.get("fleet_rows")) + " 行全部无发票覆盖（with_invoice 0）",
              "outputs/fleet_audit_trail.json"),
        _card("06", "路线 Q", "demo", _n(snap.get("route_od")), "OD 可演示",
              "4 策略 · Q 表 " + _n(snap.get("route_updates")) + " 次更新 / 30 epoch · "
              + _n(snap.get("route_clusters")) + " 簇",
              "config/rl_policy_real.json"),
    ]
    return "<div class='ch-grid'>" + "".join(cards) + "</div>"


def _legend() -> str:
    items = [
        "<span><i style='--ch:" + CHAIN_COLORS[key] + "'></i>" + STATUS_LABELS[key] + "</span>"
        for key in ("done", "pending", "wip", "empty", "demo")
    ]
    return "<div class='ch-legend'>" + "".join(items) + "</div>"


# ---------------------------------------------------------------------------
# G2 三源对账
# ---------------------------------------------------------------------------

def _g2_panel(snap: Dict[str, Any]) -> str:
    demo_n = _n(snap.get("obd_demo_n"))
    raw_n = _n(snap.get("obd_raw"))
    columns = [
        ("done", "① E₀ 主口径 · 运单", _n(snap.get("e0_trips")), "趟",
         _n(snap.get("e0_t"), 2) + " t CO₂e · 覆盖期 2023 全年",
         "data/processed/kpi.json"),
        ("empty", "② 财务发票 · 凭证", _n(snap.get("invoice_records")), "条",
         "待接入：车月 " + _n(snap.get("fleet_rows")) + " 行 with_invoice 0",
         "outputs/fleet_audit_trail.json"),
        ("wip", "③ OBD 实测 · 设备上报", demo_n + " + " + raw_n, "点",
         "demo " + demo_n + " 点 + 实流 " + raw_n + " 行（"
         + _e(str(snap.get("obd_raw_first_ts") or "")[:10] or "无实流") + " UTC）",
         "obd_summary.json · obd_stream.csv"),
    ]
    cells = []
    for status, key, value, unit, sub, source in columns:
        cells.append(
            "<div class='ch-col' style='--ch:" + CHAIN_COLORS[status] + "'>"
            "<div class='k'>" + _e(key) + "</div>"
            "<div class='v'>" + _e(value) + "<small>" + _e(unit) + "</small></div>"
            "<div class='s'>" + _e(sub) + "</div>"
            "<div class='f'>" + _path(source) + "</div>"
            "</div>"
        )
    verdict = (
        "<div class='ch-verdict'><span class='tag'>结论</span><span>"
        "共同覆盖期＝空集：运单 2023 年、OBD 2026-11-07、发票 0 条 → <b>暂不可比</b>。"
        "</span></div>"
        "<div class='ch-note'><b>不给偏差百分比：</b>三源没有一天重叠，任何「对账偏差 x%」都是编出来的。"
        "能核查的只有覆盖缺口 —— 车月 " + _n(snap.get("fleet_rows")) + " 行里实测覆盖 "
        + _n(snap.get("fleet_covered_rows")) + " 行、发票 "
        + _n(snap.get("invoice_records")) + " 条；等发票接入且 OBD 落到同月，这里才允许出现偏差数。</div>"
    )
    return (
        "<div class='ch-panel'>"
        "<div class='ch-panel-h'><span>G2 三源对账</span><span>E₀ × 发票 × OBD</span></div>"
        "<div class='ch-g2'>" + "".join(cells) + "</div>"
        + verdict +
        "</div>"
    )


# ---------------------------------------------------------------------------
# 最近一次接收
# ---------------------------------------------------------------------------

def _rx_rows(rows: List[Any]) -> str:
    out = []
    for label, value, tone in rows:
        out.append(
            "<div class='row'><span class='k'>" + _e(label) + "</span>"
            "<span class='v " + tone + "'>" + _e(value) + "</span></div>"
        )
    return "<div class='ch-rx'>" + "".join(out) + "</div>"


def _rx_panel(stream: Dict[str, Any], rejects: Dict[str, Any]) -> str:
    header = ("<div class='ch-panel-h'><span>最近一次接收</span>"
              "<span>raw · 未进 processed</span></div>")
    if not stream:
        empty = (
            "<div class='ch-empty'><div class='t'>等待设备上报</div>"
            "<div class='d'>data/raw/obd_gps/obd_stream.csv 还没生成。<br>"
            "设备 POST 到接收端后自动落盘，本卡 15 s 内可见。</div></div>"
        )
        return "<div class='ch-panel'>" + header + empty + _rx_note() + "</div>"

    vehicles = " · ".join(stream.get("vehicles") or []) or "—"
    if stream.get("n_vehicles"):
        vehicles += "（" + _n(stream.get("n_vehicles")) + " 台）"
    reject_rows = int(rejects.get("rows") or 0)
    if rejects.get("exists"):
        reject_text = _n(reject_rows) + " 行"
    else:
        reject_text = "0 行 · 文件未生成"

    rows = [
        ("报文行数", _n(stream.get("rows")) + " 行 × " + _n(stream.get("columns")) + " 列", ""),
        ("UTC 时间窗", _window(stream.get("first_ts"), stream.get("last_ts")), "ok"),
        ("窗口时长", _duration(stream.get("window_s")), ""),
        ("车辆", vehicles, ""),
        ("文件 mtime", str(stream.get("mtime") or "—") + "（本地）", ""),
        ("文件大小", _size(stream.get("bytes")), ""),
        ("拒收台账", reject_text, "warn" if rejects.get("exists") else ""),
    ]
    body = _rx_rows(rows)
    reasons = [str(r) for r in (rejects.get("reasons") or []) if str(r).strip()]
    if reasons:
        body += ("<div class='ch-note'><b>最近 3 条拒收原因：</b>"
                 + _e(" ／ ".join(reasons[:3])) + "</div>")
    return "<div class='ch-panel'>" + header + body + _rx_note() + "</div>"


def _rx_note() -> str:
    return ("<div class='ch-note'>实流只落 raw：本页与 OBD 图表读的仍是 data/processed 的 demo 基线；"
            "把 raw 变成结果表要跑 src.obd_pipeline，本轮收口不跑（会覆写 processed）。</div>")


# ---------------------------------------------------------------------------
# 口径红线
# ---------------------------------------------------------------------------

def _redlines(snap: Optional[Dict[str, Any]] = None) -> str:
    snap = snap or {}
    a4_rows = _n(snap.get("a4_rows"))
    a4_t = _n(snap.get("a4_t"), 3)
    a4_ok = _n(snap.get("a4_predicates_passed"))
    a4_all = _n(snap.get("a4_predicates_total"))
    rules = [
        ("TTW vs WTW",
         "柴油因子 <b>2.63</b>（TTW 尾管）vs <b>3.16</b> kgCO₂e/L（WTW 全生命周期），"
         "差 <b>20.2%</b>：两条边界可关联、<b>不可相减</b>，相减出来的「差额」是假精度。"),
        ("A4 · 未批准",
         "碳台账 <b>" + a4_rows + "</b> 趟行 / <b>" + a4_t + " t</b> 全为 simulated，"
         "正式行 <b>0</b>（NO_APPROVED_ROWS）；" + a4_all + " 项谓词里 5 条实质谓词 0/12 阻断"
         "（结构类 " + a4_ok + " 条 12/12 过）→ <b>0 行全过</b>。"
         "结构类只验非空/唯一，占位值（EVID-SIM / RUN-SIM / SYNTHETIC-ROW）同样通过，"
         "故 <b>4/9 只代表字段齐备，不构成真实性证据</b>；"
         "<b>0 行 ≠ 零排放</b>，单趟抽样账也不是 E₀ 全量。"),
        ("DEMO · 不进 E₀",
         "OBD 演示点与设备实流都不进主口径：E₀ 只由运单链给出（2,258,438.376 kg），"
         "演示数只用于打通链路与对拍。"),
        ("δ · 只校 EF",
         "路段系数修正只作用于 <em>EF</em>（g/km），<b>不回写</b> E₀ 与 A4 的结果表；"
         "修正前后两套数都能在文件里对上。"),
    ]
    cards = []
    for key, text in rules:
        cards.append(
            "<div class='ch-rule'><span class='k'>" + _e(key) + "</span>"
            "<div class='t'>" + text + "</div></div>"
        )
    return "<div class='ch-rules'>" + "".join(cards) + "</div>"


# ---------------------------------------------------------------------------
# 页面入口
# ---------------------------------------------------------------------------

def render_chain() -> None:
    st.markdown(CSS, unsafe_allow_html=True)

    snap = services.chain_snapshot()
    stream = services.latest_raw_stream()
    rejects = services.obd_reject_stats()
    cross = services.obd_vsp_crosscheck_summary() or {}
    vsp_delta = (cross.get("totals") or {}).get("delta_pct_profile")

    theme.hero_compact(
        "数据链总览",
        "六条链现在各走到哪一步：哪条已闭环、哪条未批准、哪条还在联调 —— 数字全部取自文件快照，本页不重算。",
        kicker="02 / DATA CHAIN",
        badges=["只读快照", "三源对账", "口径红线"],
    )
    st.markdown(_status_strip(snap), unsafe_allow_html=True)

    theme.section("① 六条链 · 现状台账",
                  "状态词与颜色沿用数据链思维图的语义色；关键数字取自已落盘的结果文件。")
    st.markdown(_chain_cards(snap, vsp_delta), unsafe_allow_html=True)
    st.markdown(_legend(), unsafe_allow_html=True)

    theme.section("② G2 三源对账 · 最近一次接收",
                  "对账只在共同覆盖期非空时才有意义；设备实流落在 raw，不进 E₀。")
    with st.container(key="chain-g2row"):
        left, right = st.columns([1.85, 1.0], gap="large")
        with left:
            st.markdown(_g2_panel(snap), unsafe_allow_html=True)
        with right:
            st.markdown(_rx_panel(stream, rejects), unsafe_allow_html=True)

    theme.section("③ 口径红线", "四条不能越的线 —— 也是答辩现场最容易被追问的四点。")
    st.markdown(_redlines(snap), unsafe_allow_html=True)

    st.caption("本页只读：不 import 算法、不写 data/processed、不动 E₀ / A4 的任何数字；"
               "刷新页面即按文件重新取快照（实流缓存 15 s）。")
