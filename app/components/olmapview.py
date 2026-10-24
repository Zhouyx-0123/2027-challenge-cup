# -*- coding: utf-8 -*-
"""OpenLayers 地图组件：支持 WMS / WMTS / XYZ 底图 + 矢量叠加。

定位
----
第 6 页「路线实验室」的**第二种地图引擎**，与 :mod:`app.components.amapview`
（高德实时路线）并存：

* ``amapview``  → 高德 JS API，**实时**路况、贴公路动画、策略切换（**完全保留不动**）
* ``olmapview``  → OpenLayers，**非实时** OSM/OSRM 基线路线，WMS/WMTS/XYZ 底图，逐 step 可查

底图与坐标基准（**本组件最容易踩的坑，必须理解再用**）
---------------------------------------------------
路线几何来自 OSM/OSRM，基准是 **WGS-84**；不同底图基准不同：

=============== ============== ==========================================
底图             数据基准         怎么处理
=============== ============== ==========================================
天地图 WMTS      CGCS2000        直接叠，差 < 1 m，无可见偏移
高德栅格 XYZ     GCJ-02          **必须**先把几何转成 GCJ-02（否则偏 500–800 m）
=============== ============== ==========================================

关键：**不是"不能用高德底图"，而是"用之前必须先对齐基准"**。
转换由服务端在 :mod:`app.components.basemap_align` 完成，
**只改坐标不改数值**——距离/时长/碳排一个字节都不动。
详见该模块 docstring。

合规
----
按国内地图数据规范，底图只使用**持牌服务**：天地图 / 高德 / 百度 / 腾讯。
默认配置写天地图 WMTS（需 token，占位符模式）；
若未配置 token，则退到**高德栅格**（公开端点，无需 key）。

断网与缺 token
--------------
答辩现场可能断网。本组件的三条纪律（对齐 amapview）：

1. **缺底图 → 不留灰块**，在 iframe 内显示可读提示；
2. **路线矢量永远画得出来**——它来自本地 parquet，不依赖网络；
3. **key 只在服务端拼 HTML 时注入**，不出现在任何前端源码与文档里。

CDN 版本固定
------------
OpenLayers 走 CDN，但**版本必须钉死**：``ol@10.6.1``。
不用 ``@latest``——大版本会改 API（比如 v7 的 ``ol/source/ImageWMS`` 参数、
v10 的 ``VectorLayer`` 样式写法），答辩当天拉到一个新版本就可能白屏。

参考：docs/35_上海北京基线_任务书.md D6；docs/36 交付说明。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
TIANDITU_TOKEN_PATH = ROOT / "config" / "tianditu_token.txt"

#: 天地图 token 占位符（未申请时文件里放这个）
TIANDITU_PLACEHOLDER = "请至天地图开放平台申请自有 token 并替换此占位符"

#: OpenLayers CDN（版本钉死，禁止 @latest）
OL_JS_URL = "https://cdn.jsdelivr.net/npm/ol@10.6.1/dist/ol.js"
OL_CSS_URL = "https://cdn.jsdelivr.net/npm/ol@10.6.1/ol.css"

#: 道路等级 → 颜色（与前端 Carbon Tide 配色一致）
ROAD_COLORS: Dict[str, str] = {
    "motorway": "#FF8A5B",
    "trunk": "#FFC46B",
    "secondary": "#5EE7FF",
    "urban": "#2DD4A7",
    "local": "#8A97A6",
    "ramp": "#C77DFF",
    "unknown": "#5D6A78",
}

#: 道路等级中文名
ROAD_LABELS: Dict[str, str] = {
    "motorway": "高速",
    "trunk": "国道",
    "secondary": "省道",
    "urban": "城市道路",
    "local": "支路",
    "ramp": "匝道",
    "unknown": "未分类",
}


def load_tianditu_token() -> str:
    """读天地图 token；未配置或仍是占位符时返回空串。

    空串 = 不可用 = 组件回退"高德栅格底图"，**不是错误**。
    """
    if not TIANDITU_TOKEN_PATH.exists():
        return ""
    text = TIANDITU_TOKEN_PATH.read_text(encoding="utf-8").strip()
    if not text or text.startswith(TIANDITU_PLACEHOLDER):
        return ""
    return text


def tianditu_available() -> bool:
    """天地图底图是否可用。"""
    return bool(load_tianditu_token())


def build_layer_config(layers: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """规范化图层配置（供前端 JS 消费）。

    每层至少要有 ``kind``（wms / wmts / xyz）与 ``url``。
    天地图 WMTS 的 URL 模板在服务端拼好，token 不进前端源码。
    """
    out: List[Dict[str, Any]] = []
    for layer in layers or []:
        kind = str(layer.get("kind", "")).lower()
        if kind not in ("wms", "wmts", "xyz"):
            continue
        item: Dict[str, Any] = {
            "kind": kind,
            "url": str(layer.get("url", "")),
            "name": str(layer.get("name", kind)),
            "visible": bool(layer.get("visible", True)),
            "opacity": float(layer.get("opacity", 1.0)),
        }
        if layer.get("attribution"):
            item["attribution"] = str(layer["attribution"])
        if kind == "wms":
            item["layers"] = str(layer.get("layers", ""))
            item["format"] = str(layer.get("format", "image/png"))
            item["transparent"] = bool(layer.get("transparent", True))
            item["version"] = str(layer.get("version", "1.3.0"))
            if layer.get("params"):
                item["params"] = dict(layer["params"])
        elif kind == "wmts":
            item["layer"] = str(layer.get("layer", ""))
            item["matrixSet"] = str(layer.get("matrixSet", "w"))
            item["format"] = str(layer.get("format", "tiles"))
            item["style"] = str(layer.get("style", "default"))
            item["version"] = str(layer.get("version", "1.0.0"))
            item["tileSize"] = int(layer.get("tileSize", 256))
            item["originX"] = float(layer.get("originX", -180.0))
            item["originY"] = float(layer.get("originY", -90.0))
            item["resolutions"] = list(layer.get("resolutions", []))
        out.append(item)
    return out


def default_tianditu_layers(token: str, kind: str = "vec_w") -> List[Dict[str, Any]]:
    """天地图 WMTS 图层配置。

    ``kind``: ``vec_w``（矢量路网）/ ``img_w``（影像）/ ``cva_w``（影像注记）。

    天地图 WMTS 用 ``w`` 矩阵集（Web Mercator，EPSG:3857），
    与 OpenLayers 默认投影一致，**不需要任何坐标转换**。
    """
    if not token:
        return []
    layer_id = {"vec_w": "vec", "img_w": "img", "cva_w": "cva"}.get(kind, "vec")
    return [{
        "kind": "wmts",
        "name": "天地图",
        "layer": layer_id,
        "matrixSet": "w",
        "format": "tiles",
        "style": "default",
        "version": "1.0.0",
        "tileSize": 256,
        "url": ("https://t{s}.tianditu.gov.cn/%s/wmts?SERVICE=WMTS&REQUEST=GetTile"
                "&VERSION=1.0.0&LAYER=%s&STYLE=default&TILEMATRIXSET=w"
                "&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=%s"
                % (kind, layer_id, token)),
    }]


def build_payload(features: Sequence[Dict[str, Any]],
                  layers: Sequence[Dict[str, Any]],
                  center: Sequence[float],
                  zoom: float = 6.0,
                  attribution: str = "",
                  geom_crs: str = "wgs84",
                  markers: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """组装前端 payload（只放展示数据，不放任何 key）。"""
    return {
        "features": list(features),
        "layers": build_layer_config(layers),
        "center": [float(center[0]), float(center[1])],
        "zoom": float(zoom),
        "attribution": attribution,
        "colors": ROAD_COLORS,
        "labels": ROAD_LABELS,
        "geomCrs": geom_crs,
        "markers": list(markers or []),
    }


# ---------------------------------------------------------------- HTML 模板

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="__OLCSS__">
<script src="__OLJS__"></script>
<style>
  :root{
    --bg:#080C11; --panel:rgba(13,20,29,.92); --line:rgba(255,255,255,.11);
    --text:#E9F0F7; --muted:#8A97A6; --mint:#2DD4A7; --amber:#F5C86B; --coral:#FF8A5B;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;
    --mono:"SFMono-Regular",Consolas,"Cascadia Code",monospace;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;height:100%;background:var(--bg);color:var(--text);font-family:var(--sans)}
  #wrap{position:relative;height:__HEIGHT__px;width:100%;overflow:hidden;background:var(--bg)}
  #map{position:absolute;inset:0;background:var(--bg)}
  /* 暗色化：只对**底图瓦片层**反相，矢量层不受影响。
     关键：不能写成 `#map.darkmode .ol-layer canvas`——那会连矢量层一起反相，
     路线颜色（橙/青/紫）被 invert 后全部失真，整条线看起来发白。
     本项目实测踩过：修正后路线才恢复成等级本色。
     底图层用 className 单独标记（ol.layer.Base 的 className 选项）。 */
  #map.darkmode .basemap-tile canvas{
    filter:invert(1) hue-rotate(180deg) brightness(.88) contrast(1.22) saturate(.78);
  }
  /* 断网/无底图时的可读提示，绝不留灰块 */
  #fallback{position:absolute;inset:0;display:none;flex-direction:column;align-items:center;
    justify-content:center;gap:10px;padding:24px;text-align:center;
    background:radial-gradient(700px 320px at 50% 40%,rgba(45,212,167,.07),transparent 70%),var(--bg);z-index:5}
  #fallback.on{display:flex}
  #fallback .t{font-size:15px;font-weight:600;color:var(--text)}
  #fallback .d{font-size:12px;color:var(--muted);max-width:520px;line-height:1.7}
  #fallback .b{font-size:11px;color:var(--amber);border:1px solid rgba(245,200,107,.3);
    border-radius:6px;padding:5px 10px;font-family:var(--mono)}
  /* 顶部声明条：非实时 · 固定因子 · 道路等级 proxy */
  #banner{position:absolute;left:0;right:0;top:0;z-index:20;display:flex;gap:8px;align-items:center;
    flex-wrap:wrap;padding:9px 14px;background:linear-gradient(180deg,rgba(8,12,17,.94),rgba(8,12,17,.62));
    backdrop-filter:blur(7px);border-bottom:1px solid var(--line);pointer-events:none}
  #banner .k{font-family:var(--mono);font-size:9px;letter-spacing:.22em;color:var(--mint)}
  #banner .chip{font-size:10.5px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);
    color:var(--muted);background:rgba(255,255,255,.03)}
  #banner .chip.warn{color:var(--amber);border-color:rgba(245,200,107,.34);background:rgba(245,200,107,.07)}
  #banner .chip.coral{color:var(--coral);border-color:rgba(255,138,91,.34);background:rgba(255,138,91,.07)}
  /* 底图切换器 */
  #switch{position:absolute;right:12px;top:46px;z-index:22;background:var(--panel);
    border:1px solid var(--line);border-radius:8px;overflow:hidden;backdrop-filter:blur(7px);max-width:210px}
  #switch .hd{padding:7px 11px;font-size:10px;color:var(--muted);border-bottom:1px solid var(--line);
    letter-spacing:.06em}
  #switch .it{padding:6px 11px;font-size:11px;cursor:pointer;display:flex;gap:7px;align-items:center;
    color:var(--text);transition:background .12s}
  #switch .it:hover{background:rgba(255,255,255,.06)}
  #switch .it.on{color:var(--mint);background:rgba(45,212,167,.10)}
  #switch .dot{width:7px;height:7px;border-radius:50%;background:currentColor;flex:none;opacity:.5}
  #switch .it.on .dot{opacity:1}
  #switch .crs{margin-left:auto;font-family:var(--mono);font-size:9px;color:var(--muted)}
  /* 图例 */
  #legend{position:absolute;left:14px;bottom:14px;z-index:20;background:var(--panel);
    border:1px solid var(--line);border-radius:9px;padding:10px 12px;backdrop-filter:blur(7px);
    min-width:140px}
  #legend .h{font-size:10px;color:var(--muted);margin-bottom:7px;letter-spacing:.08em}
  #legend .row{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--text);
    margin:4px 0;cursor:pointer;user-select:none}
  #legend .row.off{opacity:.35}
  #legend .sw{width:20px;height:3px;border-radius:2px;flex:none}
  #legend .pc{margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--muted)}
  /* 右侧 step 列表 */
  #side{position:absolute;right:0;top:38px;bottom:0;width:318px;z-index:15;
    background:var(--panel);border-left:1px solid var(--line);display:flex;flex-direction:column;
    backdrop-filter:blur(7px)}
  #side .hd{padding:12px 14px 9px;border-bottom:1px solid var(--line);flex:none}
  #side .hd .t{font-size:12.5px;font-weight:600}
  #side .hd .d{font-size:10.5px;color:var(--muted);margin-top:3px;line-height:1.6}
  #side .lst{flex:1;overflow-y:auto}
  #side .it{padding:8px 14px;border-bottom:1px solid rgba(255,255,255,.045);cursor:pointer;
    transition:background .12s}
  #side .it:hover{background:rgba(255,255,255,.045)}
  #side .it.on{background:rgba(45,212,167,.10)}
  #side .r1{display:flex;align-items:center;gap:7px}
  #side .nm{font-size:11.5px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #side .no{font-family:var(--mono);font-size:9.5px;color:var(--muted)}
  #side .r2{display:flex;gap:9px;margin-top:3px;font-family:var(--mono);font-size:9.5px;color:var(--muted)}
  #side .pill{font-size:9px;padding:1px 6px;border-radius:999px;border:1px solid var(--line)}
  #side .foot{padding:9px 14px;border-top:1px solid var(--line);font-size:10px;color:var(--muted);
    line-height:1.6}
  #side .foot b{color:var(--text);font-weight:600}
  /* 悬浮提示 */
  #tip{position:absolute;z-index:30;display:none;pointer-events:none;background:rgba(8,12,17,.96);
    border:1px solid var(--line);border-radius:8px;padding:8px 11px;font-size:11px;max-width:250px;
    box-shadow:0 8px 24px rgba(0,0,0,.55)}
  #tip .n{font-weight:600;margin-bottom:4px;color:var(--text)}
  #tip .r{font-family:var(--mono);font-size:10px;color:var(--muted);line-height:1.65}
  .ol-zoom{top:auto!important;bottom:auto!important}
  .ol-attribution{font-size:9px!important;background:rgba(8,12,17,.7)!important;color:#8A97A6!important}
  .ol-attribution button{color:#8A97A6!important}
  @media (max-width:900px){ #side{width:260px} }
</style>
</head>
<body>
<div id="wrap">
  <div id="map"></div>
  <div id="banner">
    <span class="k">CARBON BASELINE</span>
    <span class="chip warn">非实时</span>
    <span class="chip warn">固定因子 fixed_50</span>
    <span class="chip coral">道路等级 = proxy</span>
    <span class="chip">OSM/OSRM · 非高德口径</span>
    <span class="chip" id="crs-chip">几何 WGS-84</span>
  </div>
  <div id="switch"><div class="hd">底图</div><div id="sw-items"></div></div>
  <div id="legend"><div class="h">道路等级（里程占比）</div><div id="lg-rows"></div></div>
  <div id="side">
    <div class="hd">
      <div class="t">逐 step 明细</div>
      <div class="d">点击任一 step 定位；道路等级为 OSM 名称/编号 proxy 推断</div>
    </div>
    <div class="lst" id="lst"></div>
    <div class="foot" id="foot"></div>
  </div>
  <div id="tip"></div>
  <div id="fallback">
    <div class="t">底图不可用</div>
    <div class="d" id="fb-d"></div>
    <div class="b" id="fb-b"></div>
  </div>
</div>
<script>
  var PAYLOAD = __PAYLOAD__;
  var FALLBACK = __FALLBACK__;
  var FONT = '"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif';
(function(){
  var map=null, haloLayer=null, coreLayer=null, markerLayer=null;
  var labelLayer=null, mileLayer=null;
  var baseLayers={}, baseOrder=[];
  var feats=[], layerById={}, shown={}, haloById={};
  var baseKey=null;

  function $(id){return document.getElementById(id);}
  function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':String(s));return d.innerHTML;}

  function showFallback(msg, badge){
    $("fallback").classList.add("on");
    $("fb-d").innerHTML=msg||"";
    $("fb-b").innerHTML=badge||"";
  }
  function hideFallback(){ $("fallback").classList.remove("on"); }

  function fmt(v, d){ if(v==null||isNaN(v)) return "—"; return Number(v).toFixed(d==null?1:d); }

  function makeStyle(width, color, opacity){
    return new ol.style.Style({
      stroke: new ol.style.Stroke({color: color, width: width, opacity: (opacity==null?1:opacity)})
    });
  }

  function makeFeature(props){
    var geom = props.geometry;
    // OL v10 移除了 ol.geom.GeoJSON（v7 起就没了），只有 ol.format.GeoJSON。
    // 几何坐标已是目标基准的经纬（服务端转好），再经 ol.proj.fromLonLat 投影到 EPSG:3857。
    var coords = (geom && geom.coordinates) ? geom.coordinates : [];
    var projected = [];
    for (var i=0;i<coords.length;i++){
      projected.push(ol.proj.fromLonLat([coords[i][0], coords[i][1]]));
    }
    var f = new ol.Feature({
      geometry: new ol.geom.LineString(projected),
      props: props
    });
    return f;
  }

  function buildVector(){
    feats = PAYLOAD.features || [];
    layerById = {}; haloById = {};
    var coreSrc = new ol.source.Vector();
    var haloSrc = new ol.source.Vector();
    for (var i=0;i<feats.length;i++){
      var p = feats[i];
      var f = makeFeature(p);
      // halo：深色描边打底，保证亮色底图上路线依然清晰
      var hf = new ol.Feature({geometry: f.getGeometry(), props: p});
      hf.setStyle(makeStyle(p.halo||((p.width||3)+1.5), p.haloColor||"rgba(4,9,14,.55)", 0.8));
      f.setStyle(makeStyle(p.width||3, p.color||"#5EE7FF", p.opacity==null?0.95:p.opacity));
      coreSrc.addFeature(f);
      haloSrc.addFeature(hf);
      layerById[p.id] = f;
      haloById[p.id] = hf;
      shown[p.id] = true;
    }
    haloLayer = new ol.layer.Vector({source: haloSrc, declutter:false});
    coreLayer = new ol.layer.Vector({source: coreSrc, declutter:false});
    map.addLayer(haloLayer);
    map.addLayer(coreLayer);
    buildMarkers();
    buildLabels();
    buildMileage();
  }

  function buildMarkers(){
    var markers = PAYLOAD.markers || [];
    if (!markers.length){ markerLayer = null; return; }
    var src = new ol.source.Vector();
    for (var i=0;i<markers.length;i++){
      var m = markers[i];
      var f = new ol.Feature({
        geometry: new ol.geom.Point(ol.proj.fromLonLat([m.lng, m.lat])),
        props: m
      });
      var img = new ol.style.Icon({
        src: m.src, width: m.width||38, height: m.height||38,
        anchor:[0.5,0.5], anchorXUnits:'fraction', anchorYUnits:'fraction'
      });
      // 起终点加文字标签（起 / 终），小圆点太不起眼。
      // 注意：text 必须在 Style 构造时一起传，
      // 只给 image 的 Style 调 getText() 会返回 null —— 实测踩过，
      // 报错 "Cannot read properties of null (reading 'setText')"，
      // 整个 buildVector 中断，路线层根本没建起来。
      var tone = (m.kind === "start") ? "#2DD4A7" : "#FF8A5B";
      var st = new ol.style.Style({
        image: img,
        text: new ol.style.Text({
          text: m.label || "",
          font: "500 12px " + FONT,
          fill: new ol.style.Fill({color: "#0B1218"}),
          padding: [2, 3, 2, 3],
          backgroundFill: new ol.style.Fill({color: tone}),
          backgroundStroke: new ol.style.Stroke({color: tone, width: 1})
        })
      });
      f.setStyle(st);
      src.addFeature(f);
    }
    markerLayer = new ol.layer.Vector({source: src, declutter:true});
    map.addLayer(markerLayer);
  }

  // 道路名标注层：把路名打在路线上（高德那张图有这个，这里补上）。
  //
  // 按 tier 分成**三个独立图层**，各自 declutter —— OL 的 declutter 只在
  // 同一图层内避让，跨图层会互相挤掉：
  //   long   （≥120 km）→ 一直显示
  //   medium （≥8 km）  → zoom ≥ 8 才显示
  //   short  （≥18 km 门槛内更短的）→ zoom ≥ 10 才显示
  // 这样全国视图干净，放大后信息逐级浮现。
  function buildLabels(){
    var labels = PAYLOAD.roadNames || [];
    if (!labels.length){ labelLayer = null; return; }
    var tiers = {long: [], medium: [], short: []};
    for (var i=0;i<labels.length;i++){
      var t = labels[i].tier || "medium";
      if (!tiers[t]) tiers[t] = [];
      tiers[t].push(labels[i]);
    }
    var groups = [];
    for (var key in tiers){
      if (!tiers[key] || !tiers[key].length) continue;
      var src = new ol.source.Vector();
      for (var j=0;j<tiers[key].length;j++){
        var L = tiers[key][j];
        var f = new ol.Feature({
          geometry: new ol.geom.Point(ol.proj.fromLonLat([L.lng, L.lat])),
          props: L
        });
        var st = new ol.style.Style({
          text: new ol.style.Text({
            text: L.text,
            font: "500 12px " + FONT,
            fill: new ol.style.Fill({color: "#E9F0F7"}),
            stroke: new ol.style.Stroke({color: "rgba(4,9,14,.92)", width: 3}),
            offsetY: -9,
            overflow: true,
            padding: [2, 4, 2, 4],
            backgroundFill: new ol.style.Fill({color: "rgba(8,12,17,.72)"})
          })
        });
        f.setStyle(st);
        src.addFeature(f);
      }
      var lyr = new ol.layer.Vector({source: src, declutter: true});
      lyr.set('tier', key);
      map.addLayer(lyr);
      groups.push(lyr);
    }
    labelLayer = groups;
  }

  // 按当前缩放显示对应档位的路名标注
  function applyLabelTier(){
    if (!labelLayer) return;
    var z = map.getView().getZoom();
    var want = (z >= 10) ? "short" : (z >= 8 ? "medium" : "long");
    for (var i=0;i<labelLayer.length;i++){
      var lyr = labelLayer[i];
      var tier = lyr.get('tier');
      // long 档在所有缩放都显示；其余按门槛显示
      lyr.setVisible(tier === "long" ? true : tier === want || (want === "short" && tier === "medium"));
    }
  }

  // 里程刻度层：每 100 km 一个圆点 + 里程数字。
  // 这是"这趟路有多长、我在第几百公里"的直观读数。
  function buildMileage(){
    var miles = PAYLOAD.mileage || [];
    if (!miles.length){ mileLayer = null; return; }
    var src = new ol.source.Vector();
    for (var i=0;i<miles.length;i++){
      var m = miles[i];
      var f = new ol.Feature({
        geometry: new ol.geom.Point(ol.proj.fromLonLat([m.lng, m.lat])),
        props: m
      });
      var st = new ol.style.Style({
        image: new ol.style.Circle({
          radius: 4.5,
          fill: new ol.style.Fill({color: "#0B1218", opacity: 0.9}),
          stroke: new ol.style.Stroke({color: "#F5C86B", width: 2})
        }),
        text: new ol.style.Text({
          text: Math.round(m.km) + " km",
          font: "500 10px " + FONT,
          fill: new ol.style.Fill({color: "#F5C86B"}),
          stroke: new ol.style.Stroke({color: "rgba(4,9,14,.92)", width: 3}),
          offsetY: -12,
          overflow: true,
          padding: [1, 3, 1, 3],
          backgroundFill: new ol.style.Fill({color: "rgba(8,12,17,.78)"})
        })
      });
      f.setStyle(st);
      src.addFeature(f);
    }
    mileLayer = new ol.layer.Vector({source: src, declutter: true});
    map.addLayer(mileLayer);
  }

  function buildBase(){
    var layers = PAYLOAD.layers || [];
    baseOrder = []; baseLayers = {};
    for (var i=0;i<layers.length;i++){
      var L = layers[i];
      var src = null;
      try{
        if (L.kind === "wms"){
          src = new ol.source.TileWMS({
            url: L.url,
            params: {"LAYERS": L.layers, "FORMAT": L.format, "TRANSPARENT": L.transparent?"TRUE":"FALSE"},
            crossOrigin: "anonymous",
            serverType: (L.version === "1.1.1") ? ol.source.serverType.OGS : ol.source.serverType.WMS,
            transition: 120
          });
        } else if (L.kind === "wmts"){
          var res = (L.resolutions && L.resolutions.length) ? L.resolutions : null;
          src = new ol.source.WMTS({
            url: L.url,
            layer: L.layer, matrixSet: L.matrixSet, format: L.format,
            style: L.style, version: L.version,
            tileSize: L.tileSize, originX: L.originX, originY: L.originY,
            resolutions: res || undefined,
            requestEncoding: "REST"
          });
        } else {
          // XYZ：{1-4} 是 OL 支持的子域占位符写法。
          // 注意不能写 {s}——OL 的 XYZ 源不认，实测会全部落到同一个主机上拿空瓦片。
          src = new ol.source.XYZ({url: L.url, crossOrigin:"anonymous", attributions: L.attribution || undefined});
        }
      }catch(err){
        continue;
      }
      // className: 把底图瓦片层单独标记出来，
      // 暗色滤镜只作用于它，矢量层颜色保持原样。
      var tl = new ol.layer.Tile({source: src, opacity: L.opacity, visible: L.visible,
                                 className: "basemap-tile"});
      tl.set('baseName', L.name);
      tl.set('baseCrs', L.crs || 'wgs84');
      // 亮色栅格底图（高德/天地图）反相成暗色，融入 Carbon Tide 主题。
      // 暗色化的开关由底图本身决定，初始就要生效——不能只在用户手动切换时才触发。
      tl.set('needDark', (L.crs === 'gcj02' || /高德|tianditu|autonavi/i.test(L.url||'')) ? 1 : 0);
      baseLayers[L.name] = tl;
      baseOrder.push(tl);
    }
    // 底图必须在最下层：先全部加，再把矢量层置顶
    for (var j=0;j<baseOrder.length;j++){ map.addLayer(baseOrder[j]); }
    if (baseOrder.length){ baseKey = baseOrder[0].get('baseName'); }
    applyDark();
    renderSwitch();
  }

  // 用 zIndex 排图层顺序（OL 官方方式，不依赖已移除的 map.getLayers()）
  function applyZIndex(){
    var z = 0;
    for (var i=0;i<baseOrder.length;i++){ baseOrder[i].setZIndex(z++); }
    if (haloLayer) haloLayer.setZIndex(z++);
    if (coreLayer) coreLayer.setZIndex(z++);
    // 里程刻度优先于路名标注：数字读数比路名更重要，
    // 让它独占一层、独占 declutter，避免被路名挤掉（实测 700 km 后刻度消失）。
    if (mileLayer) mileLayer.setZIndex(z++);
    // labelLayer 现在是数组（按 tier 分层）
    if (labelLayer){
      if (labelLayer.length){
        for (var k=0;k<labelLayer.length;k++){ labelLayer[k].setZIndex(z++); }
      } else {
        labelLayer.setZIndex(z++);
      }
    }
    if (markerLayer) markerLayer.setZIndex(z++);
  }

  // 当前底图是否需要暗色化
  function currentNeedDark(){
    for (var i=0;i<baseOrder.length;i++){
      if (baseOrder[i].get('baseName')===baseKey) return !!baseOrder[i].get('needDark');
    }
    return false;
  }

  function applyDark(){
    $("map").classList.toggle("darkmode", currentNeedDark());
  }

  function renderSwitch(){
    var box = $("sw-items");
    if (!box){ return; }
    if (!baseOrder.length){
      box.innerHTML = '<div class="it" style="cursor:default;color:#8A97A6">无可用底图</div>';
      return;
    }
    var html = "";
    for (var i=0;i<baseOrder.length;i++){
      var L = baseOrder[i];
      var nm = L.get('baseName');
      var crs = L.get('baseCrs');
      html += '<div class="it" data-n="'+esc(nm)+'"><span class="dot"></span>'+esc(nm)
        +'<span class="crs">'+esc(crs)+'</span></div>';
    }
    box.innerHTML = html;
    var items = box.querySelectorAll(".it");
    for (var j=0;j<items.length;j++){
      items[j].addEventListener("click", function(ev){
        var nm = ev.currentTarget.getAttribute("data-n");
        switchBase(nm);
      });
    }
    markActive();
  }

  function markActive(){
    var items = $("sw-items").querySelectorAll(".it");
    for (var j=0;j<items.length;j++){
      items[j].classList.toggle("on", items[j].getAttribute("data-n")===baseKey);
    }
  }

  function switchBase(name){
    for (var i=0;i<baseOrder.length;i++){
      baseOrder[i].setVisible(baseOrder[i].get('baseName')===name);
    }
    baseKey = name;
    markActive();
    hideFallback();
    applyDark();
  }

  function fitToRoute(){
    if (!coreLayer) return;
    var src = coreLayer.getSource();
    if (!src) return;
    var ext = src.getExtent();
    if (!ext || (isNaN(ext[0]) && isNaN(ext[1]))){ return; }
    try{
      map.getView().fit(ext, {padding:[70, 340, 60, 60], duration:0, maxZoom:11});
    }catch(e){}
  }

  function renderLegend(){
    var agg = PAYLOAD.road_class_share || {};
    var colors = PAYLOAD.colors || {}, labels = PAYLOAD.labels || {};
    var html = "";
    var keys = Object.keys(agg).sort(function(a,b){return agg[b]-agg[a];});
    for (var i=0;i<keys.length;i++){
      var k = keys[i];
      html += '<div class="row" data-k="'+esc(k)+'"><span class="sw" style="background:'
        +esc(colors[k]||"#888")+'"></span><span>'+esc(labels[k]||k)
        +'</span><span class="pc">'+fmt(agg[k],1)+'%</span></div>';
    }
    $("lg-rows").innerHTML = html;
    var rows = $("lg-rows").querySelectorAll(".row");
    for (var j=0;j<rows.length;j++){
      rows[j].addEventListener("click", function(ev){
        var k = ev.currentTarget.getAttribute("data-k");
        var on = !ev.currentTarget.classList.contains("off");
        ev.currentTarget.classList.toggle("off", on);
        for (var m=0;m<feats.length;m++){
          var p = feats[m];
          if (p.road_class !== k) continue;
          var f = layerById[p.id], hf = haloById[p.id];
          if (!f) continue;
          if (on){
            f.setStyle(makeStyle(p.width||3, p.color||"#5EE7FF", 0.95));
            if (hf) hf.setStyle(makeStyle(p.halo||((p.width||3)+1.5), p.haloColor||"rgba(4,9,14,.55)", 0.8));
          } else {
            f.setStyle(makeStyle(1.0, "rgba(140,150,165,.22)", 0.5));
            if (hf) hf.setStyle(makeStyle(1.0, "rgba(140,150,165,.10)", 0.4));
          }
        }
      });
    }
  }

  function renderList(){
    var steps = PAYLOAD.steps || [];
    var labels = PAYLOAD.labels || {}, colors = PAYLOAD.colors || {};
    var html = "";
    for (var i=0;i<steps.length;i++){
      var s = steps[i];
      html += '<div class="it" data-i="'+i+'">'
        + '<div class="r1"><span class="nm">'+esc(s.road_name||"（无名路段）")
        + (s.road_ref? ' <span class="no">'+esc(s.road_ref)+'</span>':'')
        + '</span><span class="pill" style="color:'+esc(colors[s.road_class]||"#888")
        + ';border-color:'+esc(colors[s.road_class]||"#888")+'55">'
        + esc(labels[s.road_class]||s.road_class)+'</span></div>'
        + '<div class="r2"><span>'+fmt(s.distance_km,1)+' km</span><span>'+fmt(s.speed_kmh,0)
        + ' km/h</span><span>'+fmt(s.duration_min,1)+' min</span><span>'+fmt(s.carbon_kg,1)
        + ' kg</span></div></div>';
    }
    $("lst").innerHTML = html;
    var items = $("lst").querySelectorAll(".it");
    for (var j=0;j<items.length;j++){
      items[j].addEventListener("click", function(ev){
        var i = parseInt(ev.currentTarget.getAttribute("data-i"),10);
        focusStep(i);
      });
    }
  }

  function focusStep(i){
    var s = (PAYLOAD.steps||[])[i];
    if (!s) return;
    var items = $("lst").querySelectorAll(".it");
    for (var j=0;j<items.length;j++){ items[j].classList.toggle("on", j===i); }
    var f = layerById[s.id];
    if (!f) return;
    try{
      map.getView().animate({center: f.getGeometry().getCoordinates()[0], zoom: Math.max(map.getView().getZoom(), 9), duration: 420});
    }catch(e){}
  }

  function renderFoot(){
    var p = PAYLOAD.summary || {};
    var html = "";
    html += "距离 <b>"+fmt(p.distance_km,1)+" km</b> · 时长 <b>"+fmt(p.duration_min,0)+" min</b><br>";
    html += "碳排 <b>"+fmt(p.carbon_kg,0)+" kg</b>（"+fmt(p.carbon_t,3)+" t）· step <b>"
      + (p.step_count!=null? p.step_count : "—") + "</b><br>";
    html += "因子 <b>"+esc(p.factor_version||"—")+"</b> / "+esc(p.factor_mode||"—")
      +" · 车型 <b>"+esc(p.vehicle||"—")+"</b>";
    $("foot").innerHTML = html;
  }

  function bindTooltip(){
    var tip = $("tip");
    map.on("pointermove", function(e){
      if (e.dragging){ tip.style.display="none"; return; }
      var hit = map.hasFeatureAtPixel(e.pixel, {layerFilter: function(l){return l===coreLayer;}});
      if (!hit || !hit.length){ tip.style.display="none"; return; }
      var p = hit[0].get("props") || {};
      var labels = PAYLOAD.labels || {}, colors = PAYLOAD.colors || {};
      var px = map.getPixelFromCoordinate(hit[0].getGeometry().getCoordinates()[0]);
      if (!px){ tip.style.display="none"; return; }
      var nm = p.road_name || "（无名路段）";
      if (p.road_ref){ nm = nm + " " + p.road_ref; }
      var html = '<div class="n">'+esc(nm)+'</div><div class="r">'
        + '等级 '+esc(labels[p.road_class]||p.road_class||"—")+' · proxy<br>'
        + '里程 '+fmt(p.distance_km,1)+' km · '+fmt(p.speed_kmh,0)+' km/h<br>'
        + '时长 '+fmt(p.duration_min,1)+' min · 碳排 '+fmt(p.carbon_kg,1)+' kg</div>';
      tip.innerHTML = html;
      tip.style.display = "block";
      tip.style.left = (px[0]+14)+"px";
      tip.style.top = (px[1]-12)+"px";
    });
    map.on("pointerleave", function(){ $("tip").style.display="none"; });
  }

  // 单步渲染的异常隔离。
  // 为什么需要：渲染链是 buildBase → buildVector → applyZIndex → renderLegend
  // → renderList → renderFoot 的串行调用，任何一步抛错都会让**后面全部不执行**。
  // 本项目实测踩过：map.getLayers()（OL v10 已移除）抛 TypeError，
  // 结果图例、逐 step 列表、页脚三块全空，页面看上去"地图能画，就是没内容"。
  // 现在每一步单独 try，失败只记日志，不影响其余部分。
  function safe(fn, name){
    try{ fn(); }
    catch(err){
      if (window.console && console.warn) console.warn("[olmapview] " + name + " 失败: " + (err && err.message ? err.message : err));
    }
  }

  function boot(){
    // ---- 第一段：完全不依赖 OpenLayers 的内容 ----
    // 逐 step 列表、图例、页脚、声明条都只用 PAYLOAD 里的数据，
    // **不需要 OL**。所以必须在 OL 加载检查之前渲染，
    // 否则 CDN 一挂（断网 / 抖动），整页就只剩一句"底图不可用"，
    // 连 65 条 step 明细都没了 —— 实测踩过：CDN 偶发失败时列表全空。
    safe(renderLegend, "renderLegend");
    safe(renderList, "renderList");
    safe(renderFoot, "renderFoot");
    var chip = $("crs-chip");
    if (chip){
      chip.textContent = "几何 " + (PAYLOAD.geomCrs||"wgs84").toUpperCase()
        + (PAYLOAD.geomCrs==="gcj02" ? "（已对齐底图）" : "");
    }

    if (typeof ol === "undefined"){
      showFallback("OpenLayers 未能从 CDN 加载（可能断网）。路线几何来自本地结果表，但底图与渲染依赖 JS 库。",
        "CDN: "+esc(FALLBACK.oljs));
      return;
    }
    try{
      // OL v10 的破坏性变更：ol.control.defaults 从"函数"变成了"对象"，
      // 默认控件集合挪到了 ol.control.defaults.defaults。
      // v7 及更早是 ol.control.defaults({...})，v10 起必须用下面这个写法。
      var defaultsFn = (ol.control.defaults && typeof ol.control.defaults === "function")
        ? ol.control.defaults
        : (ol.control.defaults && ol.control.defaults.defaults);
      map = new ol.Map({
        target: "map",
        layers: [],
        view: new ol.View({center: ol.proj.fromLonLat(PAYLOAD.center), zoom: PAYLOAD.zoom}),
        controls: defaultsFn({attribution:true, zoom:true, rotate:false})
      });
    }catch(err){
      showFallback("地图初始化失败："+esc(err.message||err), "");
      return;
    }
    safe(buildBase, "buildBase");
    safe(buildVector, "buildVector");
    // 层级最终校正：底图 zIndex 最低，halo 次之，路线与 marker 在最上。
    // 用 setZIndex 而不是 map.getLayers().setAt()——
    // OL v10 移除了 map.getLayers()，用它会直接抛 TypeError，
    // 把后续的图例/列表/页脚渲染全部打断（本项目实测踩过）。
    safe(applyZIndex, "applyZIndex");
    safe(fitToRoute, "fitToRoute");
    safe(bindTooltip, "bindTooltip");
    safe(applyLabelTier, "applyLabelTier");
    // 缩放变化时切换路名标注档位（全国视图只显示超长段，放大后逐级浮现）
    map.on("change:resolution", function(){ safe(applyLabelTier, "applyLabelTier"); });

    // 底图瓦片加载失败 → 明确提示（不留灰块）
    var t0 = Date.now();
    var probe = setTimeout(function(){
      if (!baseOrder.length){
        showFallback("未配置可用底图。路线矢量已渲染，但缺少底图参照。", FALLBACK.note||"");
      }
    }, 2500);
    map.on("rendercomplete", function(){
      clearTimeout(probe);
      if (baseOrder.length){
        var src = baseOrder[0].getSource();
        if (src){
          var failed = false;
          src.on("tileloaderror", function(){ failed = true; });
          setTimeout(function(){
            if (failed){
              showFallback("底图瓦片加载失败（token 无效或断网）。路线矢量仍可正常查看，"
                +"可在右上角切换其他底图。", FALLBACK.note||"");
            }
          }, 2600);
        }
      }
    });
  }

  if (document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", boot);
  } else { boot(); }
})();
</script>
</body>
</html>
"""


def build_doc(payload: Dict[str, Any], height: int = 760,
              title: str = "碳基线 · OSM 路线（OpenLayers）",
              fallback_note: str = "") -> str:
    """拼装自包含 HTML。payload 里只放展示数据，不放 key。"""
    safe_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    doc = (_TEMPLATE
           .replace("__TITLE__", html.escape(title))
           .replace("__OLJS__", OL_JS_URL)
           .replace("__OLCSS__", OL_CSS_URL)
           .replace("__HEIGHT__", str(int(height)))
           .replace("__PAYLOAD__", safe_payload)
           .replace("__FALLBACK__", json.dumps({
               "oljs": OL_JS_URL,
               "note": fallback_note,
           }, ensure_ascii=False)))
    return doc


def render(payload: Dict[str, Any], height: int = 760,
           title: str = "碳基线 · OSM 路线（OpenLayers）",
           fallback_note: str = "") -> None:
    """在 Streamlit 里渲染地图。"""
    doc = build_doc(payload, height=height, title=title, fallback_note=fallback_note)
    st.iframe(doc, height=int(height))
