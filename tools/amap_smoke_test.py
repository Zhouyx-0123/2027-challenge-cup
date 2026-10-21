# -*- coding: utf-8 -*-
"""高德 JS API v2.0 接通性冒烟测试（T7 前置验证，5 分钟）。

用法：
  1. 到 lbs.amap.com 申请 Web 端（JS API）key + 安全密钥（securityJsCode）；
  2. 域名白名单加：localhost:8501 和 127.0.0.1:8501；
  3. 把 key 写进 config/amap_js_key.txt，jscode 写进 config/amap_js_code.txt（均不入库）；
  4. streamlit run tools/amap_smoke_test.py

预期结果：地图上画出「上海 → 北京」的真实驾车路线，下方路线面板列出距离/耗时/过路费。
看到这条路线 = key、安全密钥、域名白名单、Driving 插件、Polyline 渲染整条链路全部打通。

失败排查见文件末尾 FAILURES 表。本脚本不参与业务主流程，仅验证链路，可随时删除。
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
KEY_PATH = ROOT / "config" / "amap_js_key.txt"
CODE_PATH = ROOT / "config" / "amap_js_code.txt"
PLACEHOLDER = "请至高德开放平台申请自有 key 并替换此占位符"

# 冒烟测试固定 OD：上海（人民广场一带）→ 北京（天安门一带），GCJ-02
ORIGIN = [121.471, 31.233]
DEST = [116.404, 39.915]

st.set_page_config(page_title="高德 API 连通性冒烟测试", layout="centered")
st.title("高德 JS API v2.0 · 连通性冒烟测试")
st.caption("验证 Streamlit 嵌入高德地图的整条链路；通过后再接入 docs/24 的 amapview 组件")


def _read_secret(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return "" if text.startswith(PLACEHOLDER) else text


key = _read_secret(KEY_PATH)
jscode = _read_secret(CODE_PATH)

if not key:
    st.warning(
        "**未检测到可用 key。** 请按以下四步配置后刷新本页：\n"
        "1. lbs.amap.com → 控制台 → 创建应用 → 添加 **Web 端（JS API）** key；\n"
        "2. 同一页面生成**安全密钥（securityJsCode）**；\n"
        "3. 域名白名单加 `localhost:8501` 和 `127.0.0.1:8501`；\n"
        "4. key 写入 `config/amap_js_key.txt`，jscode 写入 `config/amap_js_code.txt`。\n\n"
        "配置前本页不渲染地图——这正是线上组件的降级行为（未配 key 即回退）。"
    )
    st.stop()

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html,body{margin:0;padding:0;height:100%;background:#0F1620;}
  #map{width:100%;height:420px;}
  #panel{width:100%;height:180px;overflow:auto;background:#131A24;color:#C9D4E0;
         font-size:12px;line-height:1.7;padding:8px 12px;box-sizing:border-box;}
  #meta{color:#8A97A6;font-size:12px;padding:6px 2px 0;}
</style>
<script>window._AMapSecurityConfig={securityJsCode:"__JSCODE__"};</script>
<script src="https://webapi.amap.com/maps?v=2.0&key=__KEY__&plugin=AMap.Driving"></script>
</head>
<body>
<div id="map"></div>
<div id="meta">正在向高德请求上海 → 北京的驾车路线…</div>
<div id="panel"></div>
<script>
var ORIGIN = __ORIGIN__, DEST = __DEST__;
function boot(){
  var map = new AMap.Map("map", {zoom:5, center:[118.0,35.0], viewMode:"2D"});
  AMap.plugin("AMap.Driving", function(){
    var driving = new AMap.Driving({policy: 0, map: map, panel: "panel"});
    driving.search(ORIGIN, DEST, function(status, result){
      var meta = document.getElementById("meta");
      if(status !== "complete" || !result.routes || !result.routes.length){
        meta.textContent = "查询失败 status=" + status + "（坐标或策略问题，链路本身可能已通）";
        return;
      }
      var r = result.routes[0];
      var km = (typeof r.distance === "number") ? (r.distance/1000).toFixed(1) + " km" : "—";
      var min = (typeof r.time === "number") ? (r.time/60).toFixed(0) + " min" : "—";
      meta.textContent = "链路打通 ✓ 上海→北京 距离 " + km + "｜耗时 " + min
        + "｜方案数 " + result.routes.length + "（左侧面板为高德默认 UI 渲染）";
      meta.style.color = "#2DD4A7";
    });
  });
}
if(typeof AMap !== "undefined"){ boot(); }
else{ window.addEventListener("load", function(){ if(typeof AMap!=="undefined"){boot();} }); }
setTimeout(function(){
  if(!document.getElementById("meta").textContent.match("✓")){
    var m = document.getElementById("meta");
    if(!m.textContent.match("失败")){
      m.textContent = "高德 SDK 8 秒未加载：检查网络 / key 是否为 JS API 类型 / 安全密钥顺序";
    }
  }
}, 8000);
</script>
</body>
</html>
"""

doc = (
    HTML.replace("__KEY__", key)
    .replace("__JSCODE__", jscode)
    .replace("__ORIGIN__", json.dumps(ORIGIN))
    .replace("__DEST__", json.dumps(DEST))
)
st.iframe(doc, height=640)

with st.expander("失败排查表", expanded=True):
    st.markdown(
        "| 现象 | 原因 | 处理 |\n|---|---|---|\n"
        "| 地图区域全白/全灰 | securityJsCode 写在 SDK 脚本**之后**（顺序错）、或 key 类型选成 Web 服务 | `_AMapSecurityConfig` 必须在 script 之前；key 必须选 **Web 端（JS API）** |\n"
        "| 提示 INVALID_USER_KEY | key 复制错/未绑定域名 | 重新复制 key；白名单加 `localhost:8501` |\n"
        "| SDK 8 秒未加载 | 现场断网或 webapi.amap.com 被拦 | 演示机提前测网；业务上走 Plotly 降级（docs/24 §6.3）|\n"
        "| 查到路线但面板空 | panel 容器高度不够 | 拉高本页 iframe（height=640）|\n"
        "| 全部通过 | — | 把 key 三件套配齐，按 docs/24 U3 落 `app/components/amapview.py` |"
    )

st.caption(
    "说明：本测试用浏览器端 AMap.Driving 实时算路（官方默认 UI 渲染）。"
    "业务版不这么用——正式架构是后端 REST 预取路线存 route_od.parquet，"
    "前端只读渲染（docs/23 §5 / docs/24 §4），保证答辩现场零 API 调用、断网可演示。"
)
