# -*- coding: utf-8 -*-
"""在线地图组件：高德 JS API v2.0（合规底图 + 真路线渲染）。

定位（docs/23 / docs/24）：T7 动态路线的前端渲染层。四条纪律：

  1. 只在 config/amap_js_key.txt 配置了真实 key 时启用，否则 ``route_map()`` 返回
     False，由调用方回退 mapview（Plotly 离线底图）——**key 缺失不是错误，是演示模式**；
  2. 浏览器端零业务计算——路线/碳排/成本全部来自 data/processed/route_od.parquet；
  3. key 只在服务端拼 HTML 时注入，不出现在任何前端源码；
  4. 断网不留灰块——8 秒内 SDK 未加载就显示文字提示，并保留页面上的"离线示意图"入口。

坐标一律 GCJ-02（高德原生）。本地 CITY 表是城市级近似坐标，仅用于 Plotly 离线回退。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st
from streamlit.components.v1 import html as st_html

ROOT = Path(__file__).resolve().parents[2]
KEY_PATH = ROOT / "config" / "amap_js_key.txt"
CODE_PATH = ROOT / "config" / "amap_js_code.txt"
PLACEHOLDER = "请至高德开放平台申请自有 key 并替换此占位符"

# 线条配色与 components/theme.py 对齐：重型=橙（减排瓶颈），轻中=青
_LINE_COLORS = {"2": "#C98A6B", "1": "#6FD3BF", "0": "#8AA9C9"}
_VCLASS_NAMES = {0: "轻型", 1: "中型", 2: "重型"}

_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html,body{margin:0;padding:0;height:100%;background:#070A0E;}
  #map{width:100%;height:__HEIGHT__px;}
  #fallback{display:none;padding:32px;color:#8A97A6;font-size:13px;line-height:1.7;}
  .amap-info{font-size:12px;line-height:1.8;min-width:220px;}
  .amap-info b{color:#E9EEF4;}
  .amap-note{color:#8A97A6;font-size:11px;}
</style>
<script>window._AMapSecurityConfig={securityJsCode:"__JSCODE__"};</script>
<script src="https://webapi.amap.com/maps?v=2.0&key=__KEY__&plugin=AMap.Driving,AMap.Geocoder,AMap.PlaceSearch"></script>
</head>
<body>
<div id="map"></div>
<div id="fallback">在线地图不可用（key 未配置、配额受限或现场断网）。<br>
请使用下方"离线示意图"——路线数据完全一致，仅底图换为矢量示意图。</div>
<script>
var ROUTES = __ROUTES_JSON__;
var COLORS = __LINE_COLORS__;
var VCLASS = __VCLASS_NAMES__;
var inited = false;
function fail(){
  var m = document.getElementById("map"), f = document.getElementById("fallback");
  if(m) m.style.display = "none";
  if(f) f.style.display = "block";
}
function init(){
  if(inited) return; inited = true;
  var map = new AMap.Map("map", {zoom:5, center:[110.0,35.0], viewMode:"2D"});
  var info = new AMap.InfoWindow({isCustom:false, autoMove:true});
  function showInfo(r){
    var html = '<div class="amap-info"><b>' + r.origin + ' → ' + r.dest + '</b><br>'
      + '路线距离 ' + Number(r.distance_km).toFixed(1) + ' km｜时长 ' + Number(r.duration_min).toFixed(0) + ' min<br>'
      + '过路费 ' + Number(r.toll_yuan).toFixed(0) + ' 元｜路线碳排 ' + Number(r.kg_co2).toFixed(1) + ' kg<br>'
      + '车型大类 ' + (VCLASS[String(r.vclass)] || '—')
      + (r.source === 'amap_rest' ? '' : '｜<span class="amap-note">离线估算路线</span>')
      + '<br><span class="amap-note">口径：路线情景（L1），与基准账分列标注</span></div>';
    info.setContent(html);
    info.open(map, [ (r.o_lng + r.d_lng)/2, (r.o_lat + r.d_lat)/2 ]);
  }
  var lines = [], markers = [];
  ROUTES.forEach(function(r, i){
    if(r.polyline && r.polyline.length >= 2){
      var line = new AMap.Polyline({
        path: r.polyline, strokeColor: COLORS[i] || "#6FD3BF", strokeWeight: 4,
        strokeOpacity: 0.9, cursor: "pointer", zIndex: 10, showDir: true
      });
      line.on("click", function(){ showInfo(r); });
      lines.push(line);
    }
    [["o", r.o_lng, r.o_lat, r.origin], ["d", r.d_lng, r.d_lat, r.dest]].forEach(function(p){
      markers.push(new AMap.CircleMarker({
        center:[p[1],p[2]], radius:5, strokeColor:"#E9EEF4", strokeWeight:1,
        fillColor:"#D2B36F", fillOpacity:0.95, zIndex:20, cursor:"pointer"
      }));
      markers.push(new AMap.Text({
        position:[p[1],p[2]], text:p[3], anchor:"bottom-center",
        style:{color:"#E9EEF4", background:"rgba(15,22,32,0.72)",
               border:"none", fontSize:"11px", padding:"1px 4px"}
      }));
    });
  });
  map.add(lines); map.add(markers);
  if(lines.length){ map.setFitView(lines, false, [60,60,60,60]); }
}
window.addEventListener("load", function(){ if(typeof AMap !== "undefined"){ init(); } });
setTimeout(function(){ if(!inited){ fail(); } }, 8000);
if(typeof AMap !== "undefined"){ init(); }
</script>
</body>
</html>
"""


def _read_secret(path: Path) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    return "" if text.startswith(PLACEHOLDER) else text


def load_js_key() -> str:
    return _read_secret(KEY_PATH)


def online_available() -> bool:
    """在线模式开关：key 存在且非占位。答辩日可用此函数先探测再决定渲染哪张图。"""
    return bool(load_js_key())


def _safe_json(payload: Any) -> str:
    """安全 JSON：转义 "</" 防止注入提前闭合 script 标签。"""
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def route_map(routes: List[Dict[str, Any]], height: int = 560) -> bool:
    """把预生成的路线表渲染成高德地图。未配置 key 返回 False（调用方回退离线图）。

    routes 每条需含：origin/dest/o_lng/o_lat/d_lng/d_lat/distance_km/duration_min/
    toll_yuan/kg_co2/vclass/polyline/source。
    """
    key = load_js_key()
    if not key or not routes:
        return False
    colors = [_LINE_COLORS.get(str(r.get("vclass", 1)), "#6FD3BF") for r in routes]
    doc = (
        _HTML.replace("__KEY__", key)
        .replace("__JSCODE__", _read_secret(CODE_PATH))
        .replace("__ROUTES_JSON__", _safe_json(routes))
        .replace("__LINE_COLORS__", json.dumps(colors))
        .replace("__VCLASS_NAMES__", json.dumps(_VCLASS_NAMES, ensure_ascii=False))
        .replace("__HEIGHT__", str(height))
    )
    st_html(doc, height=height)
    return True


def route_map_single(route: Dict[str, Any], height: int = 520) -> bool:
    """单条路线的地图（评委点一条线就出一张图）。"""
    return route_map([route], height=height)
