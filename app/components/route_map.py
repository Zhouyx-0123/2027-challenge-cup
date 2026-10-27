# -*- coding: utf-8 -*-
"""高德 JS API 2.0 精细路线地图：WebGL 视口、实时图层与路线交互。"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Sequence

import streamlit as st
from streamlit.components import v1 as components

import live_services

MAP_HEIGHT = 590
MAX_POINTS = 1800
COLORS = ("#FF6B6B", "#46B7FF", "#5FE3D2", "#F5B04C")


def _distance_sq(point: Sequence[float], start: Sequence[float],
                 end: Sequence[float]) -> float:
    px, py = float(point[0]), float(point[1])
    ax, ay = float(start[0]), float(start[1])
    bx, by = float(end[0]), float(end[1])
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    qx, qy = ax + t * dx, ay + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def _rdp(points: Sequence[Sequence[float]], tolerance: float) -> List[List[float]]:
    """迭代 Douglas-Peucker，避免长路线递归过深。"""
    if len(points) <= 2:
        return [[float(p[0]), float(p[1])] for p in points]
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    threshold = tolerance * tolerance
    while stack:
        start, end = stack.pop()
        best, index = -1.0, None
        for i in range(start + 1, end):
            distance = _distance_sq(points[i], points[start], points[end])
            if distance > best:
                best, index = distance, i
        if index is not None and best > threshold:
            keep.add(index)
            stack.extend(((start, index), (index, end)))
    return [[float(points[i][0]), float(points[i][1])] for i in sorted(keep)]


def simplify_polyline(points: Sequence[Sequence[float]],
                      limit: int = MAX_POINTS) -> List[List[float]]:
    clean: List[List[float]] = []
    for point in points or []:
        try:
            lng, lat = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        if -180 <= lng <= 180 and -90 <= lat <= 90:
            current = [round(lng, 6), round(lat, 6)]
            if not clean or current != clean[-1]:
                clean.append(current)
    if len(clean) <= limit:
        return clean
    tolerance = 0.00001
    for _ in range(14):
        reduced = _rdp(clean, tolerance)
        if len(reduced) <= limit:
            return reduced
        tolerance *= 2
    step = max(1, math.ceil(len(clean) / limit))
    sampled = clean[::step]
    if sampled[-1] != clean[-1]:
        sampled.append(clean[-1])
    return sampled[:limit]


def prepare_routes(routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prepared = []
    ordered = sorted(routes, key=lambda item: float(item.get("total_cost_yuan", 0.0)))
    for index, route in enumerate(ordered[:4]):
        points = simplify_polyline(route.get("polyline") or [])
        if len(points) < 2:
            continue
        prepared.append({
            "id": str(route.get("route_id", index + 1)),
            "color": COLORS[index % len(COLORS)],
            "points": points,
            "cost": round(float(route.get("total_cost_yuan", 0.0)), 2),
            "carbon": round(float(route.get("carbon_kg", 0.0)), 2),
            "duration": round(float(route.get("duration_min", 0.0)), 1),
            "distance": round(float(route.get("distance_km", 0.0)), 1),
            "tolls": round(float(route.get("tolls", 0.0)), 2),
            "speed": round(float(route.get("avg_speed_kmh", 0.0)), 1),
            "congestion": round(float(route.get("congestion_factor", 1.0)), 2),
            "recommended": index == 0,
        })
    return prepared


def _json(payload: Any) -> str:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>高德路线地图</title>
<style>
:root{--ink:#F3F5F8;--ink2:#C7CDD6;--ink3:#8F9AA8;--line:rgba(255,255,255,.11);
  --glass:rgba(13,17,23,.86);--blue:#46B7FF;--mint:#5FE3D2;--red:#FF6B6B;
  --font:'Hanken Grotesk','Inter','Noto Sans SC','PingFang SC','Microsoft YaHei',sans-serif;
  --mono:'JetBrains Mono','SFMono-Regular',Consolas,monospace}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;
  background:#090B0E;color:var(--ink);font-family:var(--font)}
button{font:inherit}
#shell{position:relative;width:100%;height:590px;overflow:hidden;background:
  radial-gradient(circle at 22% 18%,rgba(70,183,255,.10),transparent 34%),
  radial-gradient(circle at 82% 74%,rgba(95,227,210,.06),transparent 30%),#090B0E;
  border-radius:22px}
#map{position:absolute;inset:0;background:#0B1018}
#shell::after{content:'';position:absolute;inset:0;pointer-events:none;z-index:5;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.12),
  inset 0 120px 110px -80px rgba(5,7,10,.86),
  inset 0 -120px 100px -90px rgba(5,7,10,.92)}
.loading{position:absolute;inset:0;z-index:50;display:flex;flex-direction:column;
  align-items:center;justify-content:center;background:#090B0E;transition:opacity .45s ease}
.loading.hide{opacity:0;pointer-events:none}
.spinner{width:42px;height:42px;border-radius:50%;border:2px solid rgba(255,255,255,.10);
  border-top-color:var(--blue);border-right-color:var(--mint);animation:spin .9s linear infinite;
  margin-bottom:16px}
.loading strong{font-size:15px;font-weight:650;letter-spacing:.01em}
.loading span{margin-top:7px;font:10px var(--mono);color:var(--ink3);letter-spacing:.12em}
@keyframes spin{to{transform:rotate(360deg)}}
.fatal{position:absolute;inset:0;z-index:60;display:none;flex-direction:column;
  align-items:center;justify-content:center;text-align:center;padding:36px;background:#090B0E}
.fatal.show{display:flex}.fatal b{font-size:16px;margin-bottom:9px}
.fatal p{max-width:520px;margin:0;color:var(--ink3);font-size:12px;line-height:1.8;
  word-break:break-word}
.hud{position:absolute;z-index:20;top:16px;left:18px;display:flex;align-items:center;gap:10px;
  pointer-events:none}
.brand{display:flex;align-items:center;gap:11px;padding:10px 13px;border-radius:14px;
  background:var(--glass);border:1px solid var(--line);
  box-shadow:0 14px 46px rgba(0,0,0,.34);backdrop-filter:blur(18px) saturate(140%)}
.mark{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;
  background:linear-gradient(145deg,var(--blue),#2E7DFF);color:#041019;font-weight:900;
  font-size:14px;box-shadow:0 6px 20px rgba(70,183,255,.28)}
.brand h1{font-size:13px;line-height:1.2;margin:0;font-weight:720;letter-spacing:-.01em}
.brand small{display:block;margin-top:3px;color:var(--ink3);font:9px var(--mono);
  letter-spacing:.11em}
.live{margin-left:3px;display:flex;align-items:center;gap:6px;color:var(--mint);
  font:9px var(--mono);letter-spacing:.08em;white-space:nowrap}
.live i{width:5px;height:5px;border-radius:50%;background:var(--mint);
  box-shadow:0 0 0 4px rgba(95,227,210,.12);animation:pulse 2.2s ease infinite}
@keyframes pulse{50%{box-shadow:0 0 0 7px rgba(95,227,210,0)}}
.layers{position:absolute;z-index:22;top:16px;right:18px;display:flex;gap:4px;
  padding:4px;border-radius:13px;background:var(--glass);border:1px solid var(--line);
  backdrop-filter:blur(18px) saturate(140%);box-shadow:0 12px 38px rgba(0,0,0,.32)}
.layers button{border:0;border-radius:9px;padding:7px 11px;background:transparent;
  color:var(--ink3);font-size:11px;font-weight:620;cursor:pointer;
  transition:background .18s,color .18s}
.layers button:hover{color:var(--ink);background:rgba(255,255,255,.06)}
.layers button.on{color:#EAF8FF;background:rgba(70,183,255,.18);
  box-shadow:inset 0 0 0 1px rgba(70,183,255,.26)}
.side{position:absolute;z-index:21;top:70px;right:18px;width:286px;padding:16px;
  border-radius:17px;background:var(--glass);border:1px solid var(--line);
  backdrop-filter:blur(22px) saturate(145%);box-shadow:0 24px 70px rgba(0,0,0,.44)}
.eyebrow{display:flex;align-items:center;justify-content:space-between;
  color:var(--ink3);font:9px var(--mono);letter-spacing:.1em;margin-bottom:11px}
.eyebrow b{color:var(--mint);font-weight:500}
.routes{display:flex;flex-direction:column;gap:5px;margin-bottom:14px}
.route{display:flex;align-items:center;gap:9px;width:100%;padding:8px 9px;border:1px solid transparent;
  border-radius:10px;background:rgba(255,255,255,.025);color:var(--ink2);text-align:left;
  cursor:pointer;transition:background .16s,border-color .16s,transform .16s}
.route:hover{background:rgba(255,255,255,.065);transform:translateX(-2px)}
.route.on{background:rgba(70,183,255,.10);border-color:rgba(70,183,255,.28)}
.route .sw{width:18px;height:3px;border-radius:5px;flex:0 0 auto}
.route .id{font-size:11px;font-weight:680;color:var(--ink);min-width:48px}
.route .sum{margin-left:auto;font:9px var(--mono);color:var(--ink3)}
.route .best{font:8px var(--mono);color:#071014;background:var(--mint);
  padding:2px 5px;border-radius:5px;letter-spacing:.03em}
.detail{border-top:1px solid var(--line);padding-top:14px}
.dtitle{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;
  margin-bottom:12px}
.dtitle h2{margin:0;font-size:15px;letter-spacing:-.02em}
.dtitle p{margin:4px 0 0;font-size:10px;color:var(--ink3)}
.dbadge{font:8px var(--mono);color:var(--mint);border:1px solid rgba(95,227,210,.25);
  background:rgba(95,227,210,.08);padding:4px 7px;border-radius:6px;white-space:nowrap}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.stat{padding:9px 10px;border-radius:9px;background:rgba(255,255,255,.035);
  border:1px solid rgba(255,255,255,.055)}
.stat span{display:block;color:var(--ink3);font-size:9px;margin-bottom:4px}
.stat b{font:600 14px var(--mono);color:var(--ink);letter-spacing:-.02em}
.stat b em{font:8px var(--mono);color:var(--ink3);font-style:normal;margin-left:3px}
.meta{display:flex;justify-content:space-between;margin-top:10px;padding-top:9px;
  border-top:1px solid rgba(255,255,255,.07);color:var(--ink3);font-size:9px}
.meta b{color:var(--ink2);font-weight:600}
.bottom{position:absolute;z-index:20;left:18px;bottom:16px;display:flex;align-items:center;
  gap:12px;padding:8px 11px;border-radius:11px;background:rgba(11,15,21,.80);
  border:1px solid var(--line);backdrop-filter:blur(14px)}
.bottom .key{display:flex;align-items:center;gap:6px;color:var(--ink2);font-size:10px}
.bottom .dot{width:6px;height:6px;border-radius:50%;background:var(--red)}
.bottom .sep{width:1px;height:14px;background:var(--line)}
.bottom .mono{font:9px var(--mono);color:var(--ink3);letter-spacing:.04em}
.zoom{position:absolute;z-index:22;right:18px;bottom:16px;display:flex;flex-direction:column;
  border-radius:11px;overflow:hidden;border:1px solid var(--line);background:var(--glass);
  backdrop-filter:blur(15px)}
.zoom button{width:34px;height:32px;border:0;background:transparent;color:var(--ink2);
  font:17px/1 var(--mono);cursor:pointer}
.zoom button:hover{background:rgba(255,255,255,.08);color:var(--ink)}
.zoom button+button{border-top:1px solid var(--line)}
.startpin,.endpin{position:relative;display:flex;align-items:center;gap:7px;white-space:nowrap;
  font:600 10px var(--font);color:#F4FBFF;text-shadow:0 1px 5px rgba(0,0,0,.8)}
.startpin i,.endpin i{width:11px;height:11px;border-radius:50%;display:block;
  border:2px solid #071018}
.startpin i{background:var(--mint);box-shadow:0 0 0 5px rgba(95,227,210,.18),0 0 18px rgba(95,227,210,.7)}
.endpin i{background:var(--red);box-shadow:0 0 0 5px rgba(255,107,107,.16),0 0 18px rgba(255,107,107,.7)}
.amap-logo,.amap-copyright{opacity:.38!important;filter:grayscale(1)}
.amap-scalecontrol{opacity:.48}
@media(max-width:760px){
 .side{top:auto;left:12px;right:12px;bottom:62px;width:auto;padding:10px}
 .routes{flex-direction:row;overflow-x:auto}.route{min-width:112px;padding:6px}
 .route .sum{display:none}.detail{display:none}.hud{left:12px}
 .layers{right:12px}.brand{padding:8px 10px}.brand small,.live{display:none}
 .bottom{left:12px;bottom:12px}.zoom{right:12px;bottom:12px}
}
</style>
</head>
<body>
<div id="shell">
 <div id="map" aria-label="高德路线地图"></div>
 <div class="loading" id="loading"><div class="spinner"></div>
   <strong>正在构建高德路线场景</strong><span>WEBGL · VECTOR TILES · GCJ-02</span></div>
 <div class="fatal" id="fatal"><b>地图加载失败</b><p id="fatalText"></p></div>

 <div class="hud"><div class="brand"><span class="mark">碳</span><div>
   <h1>真实路线图景</h1><small>AMAP JS API 2.0 · WEBGL</small>
 </div><span class="live"><i></i>LIVE</span></div></div>

 <div class="layers" aria-label="地图图层">
   <button class="on" id="standard">标准</button>
   <button id="satellite">卫星</button>
   <button id="traffic">路况</button>
   <button id="pitch">3D</button>
 </div>

 <aside class="side">
   <div class="eyebrow"><span>ROUTE INTELLIGENCE</span><b id="routeCount"></b></div>
   <div class="routes" id="routeList"></div>
   <div class="detail">
     <div class="dtitle"><div><h2 id="detailId"></h2><p>高德实算几何 · 绿电匹配前</p></div>
       <span class="dbadge" id="detailBadge">BEST COST</span></div>
     <div class="stats">
       <div class="stat"><span>综合成本</span><b>¥<i id="mCost"></i></b></div>
       <div class="stat"><span>碳排放</span><b><i id="mCarbon"></i><em>kg</em></b></div>
       <div class="stat"><span>行驶时间</span><b><i id="mTime"></i><em>min</em></b></div>
       <div class="stat"><span>路线里程</span><b><i id="mDist"></i><em>km</em></b></div>
     </div>
     <div class="meta"><span>过路费 <b id="mToll"></b></span>
       <span>均速 <b id="mSpeed"></b></span><span>拥堵 <b id="mCong"></b></span></div>
   </div>
 </aside>

 <div class="bottom"><span class="key"><i class="dot"></i>最低成本</span>
   <span class="sep"></span><span class="mono" id="coords">GCJ-02</span></div>
 <div class="zoom"><button id="zin" aria-label="放大">+</button>
   <button id="fit" aria-label="适配路线">⌖</button>
   <button id="zout" aria-label="缩小">−</button></div>
</div>

<script src="https://webapi.amap.com/loader.js"></script>
<script>
const BOOT = __BOOTSTRAP__;
const byId = id => document.getElementById(id);
const fmt = (value, digits) => Number(value).toFixed(digits);
let map, lines = [], glows = [], activeId = null, trafficLayer = null;
let satelliteLayer = null, roadLayer = null, satelliteOn = false;

function fatal(message){
  byId('loading').classList.add('hide');
  byId('fatalText').textContent = String(message || '请检查 JS API Key、安全密钥与域名白名单。');
  byId('fatal').classList.add('show');
}

function escapeHtml(value){
  return String(value).replace(/[&<>"']/g, ch => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

function routeLabel(route){ return '路线 ' + route.id; }

function renderRouteList(){
  byId('routeCount').textContent = BOOT.routes.length + ' CANDIDATES';
  byId('routeList').innerHTML = BOOT.routes.map(route => `
    <button class="route" data-route="${escapeHtml(route.id)}">
      <i class="sw" style="background:${route.color}"></i>
      <span class="id">${escapeHtml(routeLabel(route))}</span>
      ${route.recommended ? '<span class="best">BEST</span>' : ''}
      <span class="sum">¥${fmt(route.cost, 0)}</span>
    </button>`).join('');
  document.querySelectorAll('.route').forEach(button => {
    button.addEventListener('click', () => selectRoute(button.dataset.route));
  });
}

function updateDetail(route){
  byId('detailId').textContent = routeLabel(route);
  byId('detailBadge').textContent = route.recommended ? 'BEST COST' : 'ALTERNATIVE';
  byId('detailBadge').style.color = route.recommended ? 'var(--mint)' : 'var(--ink3)';
  byId('mCost').textContent = fmt(route.cost, 0);
  byId('mCarbon').textContent = fmt(route.carbon, 0);
  byId('mTime').textContent = fmt(route.duration, 0);
  byId('mDist').textContent = fmt(route.distance, 0);
  byId('mToll').textContent = '¥' + fmt(route.tolls, 0);
  byId('mSpeed').textContent = fmt(route.speed, 1) + ' km/h';
  byId('mCong').textContent = '×' + fmt(route.congestion, 2);
}

function paintState(){
  BOOT.routes.forEach((route, index) => {
    const active = route.id === activeId;
    const line = lines[index], glow = glows[index];
    if (!line) return;
    line.setOptions({
      strokeWeight: active ? 9 : (route.recommended ? 7 : 4.5),
      strokeOpacity: active ? 1 : (route.recommended ? .92 : .48),
      zIndex: active ? 130 : (route.recommended ? 120 : 100 + index)
    });
    glow.setOptions({
      strokeWeight: active ? 22 : (route.recommended ? 18 : 12),
      strokeOpacity: active ? .20 : (route.recommended ? .12 : .05)
    });
    document.querySelectorAll('.route').forEach(button => {
      button.classList.toggle('on', button.dataset.route === route.id);
    });
  });
  const selected = BOOT.routes.find(route => route.id === activeId) || BOOT.routes[0];
  if (selected) updateDetail(selected);
}

function selectRoute(id){
  activeId = id;
  paintState();
}

function buildMap(AMap){
  const start = BOOT.origin.split(',').map(Number);
  const end = BOOT.destination.split(',').map(Number);
  map = new AMap.Map('map', {
    viewMode:'3D', pitch:48, rotation:-10, zoom:5, zooms:[3,19],
    center:start, resizeEnable:true, dragEnable:true, zoomEnable:true,
    mapStyle:'amap://styles/darkblue', showLabel:true,
    features:['bg','road','building','point'],
    WebGLParams:{antialias:true, preserveDrawingBuffer:false}
  });
  const overlays = [];
  BOOT.routes.forEach((route, index) => {
    const path = route.points;
    const glow = new AMap.Polyline({
      path, strokeColor:route.color, strokeWeight:route.recommended ? 18 : 12,
      strokeOpacity:route.recommended ? .12 : .05, lineJoin:'round',
      lineCap:'round', zIndex:60 + index
    });
    const line = new AMap.Polyline({
      path, strokeColor:route.color,
      strokeWeight:route.recommended ? 7 : 4.5,
      strokeOpacity:route.recommended ? .92 : .48,
      showDir:true, dirColor:route.color, lineJoin:'round', lineCap:'round',
      isOutline:true, outlineColor:'rgba(5,8,12,.86)', borderWeight:2.2,
      zIndex:100 + index
    });
    line.on('mouseover', () => {
      line.setOptions({strokeWeight:9, strokeOpacity:1});
      document.querySelectorAll('.route').forEach(button => {
        button.classList.toggle('on', button.dataset.route === route.id);
      });
      updateDetail(route);
    });
    line.on('mouseout', () => paintState());
    line.on('click', () => selectRoute(route.id));
    glows.push(glow); lines.push(line); overlays.push(glow, line);
  });
  map.add(overlays);

  const pin = (position, type, label) => new AMap.Marker({
    position, anchor:'bottom-center', zIndex:240,
    content:`<div class="${type}"><i></i><span>${escapeHtml(label)}</span></div>`
  });
  const startPin = pin(start, 'startpin', '起点 · ' + BOOT.originLabel);
  const endPin = pin(end, 'endpin', '终点 · ' + BOOT.destinationLabel);
  map.add([startPin, endPin]);

  trafficLayer = new AMap.TileLayer.Traffic({
    autoRefresh:true, interval:180, zIndex:150, opacity:.82
  });
  satelliteLayer = new AMap.TileLayer.Satellite({zIndex:10});
  roadLayer = new AMap.TileLayer.RoadNet({zIndex:20});

  function fitAll(){
    // avoid 顺序为 上、下、左、右；右侧预留路线情报面板。
    map.setFitView(overlays.concat([startPin, endPin]), false,
      [94, 76, 72, 320], 15);
  }
  map.on('complete', () => setTimeout(fitAll, 180));
  map.on('mousemove', event => {
    byId('coords').textContent =
      event.lnglat.getLng().toFixed(5) + ', ' + event.lnglat.getLat().toFixed(5);
  });

  byId('zin').onclick = () => map.zoomIn();
  byId('zout').onclick = () => map.zoomOut();
  byId('fit').onclick = fitAll;
  byId('pitch').onclick = event => {
    const on = event.currentTarget.classList.toggle('on');
    map.setPitch(on ? 0 : 48);
  };
  byId('pitch').classList.add('on');
  byId('traffic').onclick = event => {
    const on = event.currentTarget.classList.toggle('on');
    if (on) map.add(trafficLayer); else map.remove(trafficLayer);
  };
  byId('satellite').onclick = event => {
    satelliteOn = event.currentTarget.classList.toggle('on');
    byId('standard').classList.remove('on');
    if (satelliteOn) { map.add(satelliteLayer); map.add(roadLayer); }
    else { map.remove(satelliteLayer); map.remove(roadLayer); byId('standard').classList.add('on'); }
  };
  byId('standard').onclick = () => {
    satelliteOn = false;
    byId('satellite').classList.remove('on');
    byId('standard').classList.add('on');
    map.remove(satelliteLayer); map.remove(roadLayer);
  };

  renderRouteList();
  activeId = BOOT.routes[0] ? BOOT.routes[0].id : null;
  paintState();
  setTimeout(() => byId('loading').classList.add('hide'), 280);
}

window._AMapSecurityConfig = {securityJsCode: BOOT.security};
if (typeof AMapLoader === 'undefined') {
  fatal('高德 JS API Loader 加载失败，请检查网络或内容安全策略。');
} else {
  AMapLoader.load({
    key: BOOT.key,
    version:'2.0',
    plugins:[]
  }).then(buildMap).catch(error => {
    fatal((error && (error.message || error.info)) || '高德 JS API 加载失败');
  });
  setTimeout(() => {
    if (!map) fatal('地图加载超时，请检查网络、Key 类型与域名白名单。');
  }, 15000);
}
</script>
</body>
</html>
"""


def build_route_map_html(routes: List[Dict[str, Any]], origin: str, destination: str,
                         key: str, security: str,
                         origin_label: str = "起点",
                         destination_label: str = "终点") -> str:
    prepared = prepare_routes(routes)
    if not prepared:
        raise ValueError("当前路线没有可绘制的真实几何")
    if not key or not security:
        raise ValueError("缺少高德 Web 端（JS API）Key 或安全密钥")
    payload = {
        "origin": origin.strip(), "destination": destination.strip(),
        "originLabel": str(origin_label).strip() or "起点",
        "destinationLabel": str(destination_label).strip() or "终点",
        "key": key.strip(), "security": security.strip(), "routes": prepared,
    }
    return HTML_TEMPLATE.replace("__BOOTSTRAP__", _json(payload))


def render_setup() -> None:
    st.markdown("""
<div style="padding:24px 26px;border-radius:20px;background:rgba(255,255,255,.055);
  box-shadow:0 0 0 1px rgba(255,255,255,.12);margin:4px 0 12px">
  <div style="font:10px 'JetBrains Mono',monospace;letter-spacing:.12em;color:#F5B04C">
    WEBGL MAP · KEY REQUIRED</div>
  <div style="font-size:20px;font-weight:750;color:#F3F5F8;margin:8px 0 7px">
    精细地图需要 Web 端（JS API）Key</div>
  <div style="font-size:13px;line-height:1.85;color:#9AA2AD">
    现有 Web 服务 Key 已用于路线规划和碳核算，不能加载前端地图。请在高德控制台为同一应用
    添加“Web 端（JS API）”Key，并将 Key 与安全密钥分别写入：
  </div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:13px">
    <code style="padding:7px 10px;border-radius:8px;background:rgba(70,183,255,.10);
      color:#A9DDFF">config/amap_js_key.txt</code>
    <code style="padding:7px 10px;border-radius:8px;background:rgba(95,227,210,.09);
      color:#8BF3E6">config/amap_js_security_code.txt</code>
  </div>
  <div style="margin-top:12px;font-size:11px;color:#8F9AA8">
    域名白名单需包含 localhost 与 127.0.0.1。两个文件已加入 .gitignore，不会进入版本库。
  </div>
</div>""", unsafe_allow_html=True)


def render_route_map(routes: List[Dict[str, Any]], origin: str, destination: str,
                     origin_label: str = "起点", destination_label: str = "终点") -> None:
    if not live_services.amap_js_available():
        render_setup()
        return
    key, security = live_services.amap_js_credentials()
    try:
        html = build_route_map_html(
            routes, origin, destination, key, security,
            origin_label=origin_label, destination_label=destination_label)
    except ValueError as exc:
        st.warning(str(exc))
        return
    with st.container(key="route_map_stage"):
        components.html(html, height=MAP_HEIGHT, scrolling=False)


__all__ = ["build_route_map_html", "prepare_routes", "render_route_map", "simplify_polyline"]
