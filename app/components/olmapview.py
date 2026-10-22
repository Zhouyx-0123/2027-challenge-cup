# -*- coding: utf-8 -*-
"""OpenLayers 地图组件：支持 WMS / WMTS / XYZ 底图 + 矢量叠加。

定位
----
第 6 页「路线实验室」的**第二种地图引擎**，与 :mod:`app.components.amapview`
（高德实时路线）并存：

* ``amapview``  → 高德 JS API，**实时**路况、贴公路动画、策略切换（**完全保留不动**）
* ``olmapview``  → OpenLayers，**非实时** OSM/OSRM 基线路线，WMS/WMTS 底图，逐 step 可查

底图源为什么按基准分
--------------------
这是本组件最容易踩的坑，**必须理解再用**：

=============== =============== ======================================
底图             数据基准         与 OSM 路线（WGS-84）叠加
=============== =============== ======================================
天地图 WMTS      CGCS2000        ✅ 同基准，差异 < 1 m，无可见偏移
高德栅格/矢量     GCJ-02          ❌ 差 500–800 m，路线会明显错位
=============== =============== ======================================

所以：**基线模式默认用天地图 WMTS**。高德底图只能配在"高德实时"那一侧。

合规
----
按国内地图数据规范，底图只使用**持牌服务**：天地图 / 高德 / 百度 / 腾讯。
默认配置写天地图 WMTS（需 token，占位符模式），
**不内置任何可用 key**；token 由服务端读取后注入 HTML，不进版本库。

断网与缺 token
--------------
答辩现场可能断网。本组件的三条纪律（对齐 amapview）：

1. **缺 token / 加载失败 → 不留灰块**，在 iframe 内显示可读提示；
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

    空串 = 不可用 = 组件回退"无底图矢量模式"，**不是错误**。
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
                  attribution: str = "") -> Dict[str, Any]:
    """组装前端 payload（只放展示数据，不放任何 key）。"""
    return {
        "features": list(features),
        "layers": build_layer_config(layers),
        "center": [float(center[0]), float(center[1])],
        "zoom": float(zoom),
        "attribution": attribution,
        "colors": ROAD_COLORS,
        "labels": ROAD_LABELS,
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
  /* 图例 */
  #legend{position:absolute;left:14px;bottom:14px;z-index:20;background:var(--panel);
    border:1px solid var(--line);border-radius:9px;padding:10px 12px;backdrop-filter:blur(7px);
    min-width:132px}
  #legend .h{font-size:10px;color:var(--muted);margin-bottom:7px;letter-spacing:.08em}
  #legend .row{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--text);
    margin:4px 0;cursor:pointer;user-select:none}
  #legend .row.off{opacity:.35}
  #legend .sw{width:16px;height:3px;border-radius:2px;flex:none}
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
  .ol-zoom{top:auto!important;bottom:auto!important}
  /* 高德底图模式下隐藏 OL 自带 attribution 位置冲突 */
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
  </div>
  <div id="legend"><div class="h">道路等级（里程占比）</div><div id="lg-rows"></div></div>
  <div id="side">
    <div class="hd">
      <div class="t">逐 step 明细</div>
      <div class="d">点击任一 step 定位；道路等级为 OSM 名称/编号 proxy 推断</div>
    </div>
    <div class="lst" id="lst"></div>
    <div class="foot" id="foot"></div>
  </div>
  <div id="fallback">
    <div class="t">底图不可用</div>
    <div class="d" id="fb-d"></div>
    <div class="b" id="fb-b"></div>
  </div>
</div>
<script>
var PAYLOAD = __PAYLOAD__;
var FALLBACK = __FALLBACK__;
(function(){
  var map=null, vectorLayer=null, baseLayers={}, baseVisible=true;
  var feats=[], layerById={}, shown={};

  function $(id){return document.getElementById(id);}
  function esc(s){var d=document.createElement('div');d.textContent=(s==null?'':String(s));return d.innerHTML;}

  function showFallback(msg, badge){
    $("fallback").classList.add("on");
    $("fb-d").innerHTML=msg||"";
    $("fb-b").innerHTML=badge||"";
  }
  function hideFallback(){ $("fallback").classList.remove("on"); }

  function fmt(v, d){ if(v==null||isNaN(v)) return "—"; return Number(v).toFixed(d==null?1:d); }

  function styleOf(color, width, fillOpacity){
    return new ol.style.Style({
      stroke: new ol.style.Stroke({color: color, width: width}),
      fill: new ol.style.Fill({color: color, opacity: fillOpacity==null?0.16:fillOpacity})
    });
  }

  function makeFeature(props){
    var geom = props.geometry;
    // OL v10 移除了 ol.geom.GeoJSON（v7 起就没了），只有 ol.format.GeoJSON。
    // 这里直接用 LineString 构造：几何坐标本来就是 WGS-84 经纬（EPSG:4326），
    // 再经 ol.proj.fromLonLat 投影到视图投影（EPSG:3857）。
    var coords = (geom && geom.coordinates) ? geom.coordinates : [];
    var projected = [];
    for (var i=0;i<coords.length;i++){
      projected.push(ol.proj.fromLonLat([coords[i][0], coords[i][1]]));
    }
    var f = new ol.Feature({
      geometry: new ol.geom.LineString(projected),
      props: props
    });
    // OL v10 的 setStyle 只接受 ol.style.Style 实例（或其数组），
    // 传普通对象会抛 "Expected an `ol/style/Style`"。
    f.setStyle(styleOf(props.color||"#5EE7FF", props.width||3.2, props.opacity));
    return f;
  }

  function buildVector(){
    feats = PAYLOAD.features || [];
    layerById = {};
    var src = new ol.source.Vector();
    for (var i=0;i<feats.length;i++){
      var p = feats[i];
      var f = makeFeature(p);
      src.addFeature(f);
      layerById[p.id] = f;
      shown[p.id] = true;
    }
    vectorLayer = new ol.layer.Vector({source: src, declutter:false});
    map.addLayer(vectorLayer);
  }

  function buildBase(){
    var layers = PAYLOAD.layers || [];
    var first = null;
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
          src = new ol.source.XYZ({url: L.url, crossOrigin:"anonymous"});
        }
      }catch(err){
        continue;
      }
      var tl = new ol.layer.Tile({source: src, opacity: L.opacity, visible: L.visible});
      baseLayers[L.name] = tl;
      map.addLayer(tl);
      if (first===null) first = tl;
    }
    if (first===null){
      baseVisible = false;
    } else {
      baseVisible = true;
    }
  }

  function fitToRoute(){
    var src = vectorLayer.getSource();
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
          var f = layerById[p.id];
          if (!f) continue;
          if (on){ f.setStyle(styleOf(p.color||"#5EE7FF", p.width||3.2, 0.03)); }
          else { f.setStyle(styleOf("rgba(140,150,165,.22)", 1.1, 0)); }
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

  function boot(){
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
    buildBase();
    buildVector();
    fitToRoute();
    renderLegend();
    renderList();
    renderFoot();

    // 底图瓦片加载失败 → 明确提示（不留灰块）
    var t0 = Date.now();
    var probe = setTimeout(function(){
      if (!baseVisible){
        showFallback("未配置可用底图。路线矢量已渲染，但缺少底图参照。", FALLBACK.note||"");
      }
    }, 2500);
    map.on("rendercomplete", function(){
      clearTimeout(probe);
      if (baseVisible){
        var src = null;
        var names = Object.keys(baseLayers);
        for (var i=0;i<names.length;i++){ src = baseLayers[names[i]].getSource(); break; }
        if (src){
          var failed = false;
          src.on("tileloaderror", function(){ failed = true; });
          setTimeout(function(){
            if (failed){
              showFallback("底图瓦片加载失败（token 无效或断网）。路线矢量仍可正常查看。",
                FALLBACK.note||"");
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
