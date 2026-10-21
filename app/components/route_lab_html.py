# -*- coding: utf-8 -*-
"""路线实验室 HTML 模板：高德 JS API v2.0 实时路线 + 平滑动画。

设计目标（docs/28）：
  1. 路线必须贴合公路：几何来自 AMap.Driving 实时规划（约 0.1 km 一个点），
     不再使用 route_od.parquet 里按 400 点抽稀的缓存折线做主视觉；
  2. 动态、丝滑：路线生长动画、镜头缓动、方向箭头、在途车辆回放、数字滚动；
  3. 前端仍然不碰业务：碳排/运营成本/碳成本/推荐车型全部来自结果表，
     浏览器只接收已经算好的数字，不做任何碳或成本公式；
  4. 无 key / 断网时由调用方回退离线图；本模板内部也有超时与错误提示。

这个文件只负责拼 HTML，不 import streamlit，方便单测与 Playwright 截图。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script>window._AMapSecurityConfig={securityJsCode:"__JSCODE__"};</script>
<script src="https://webapi.amap.com/maps?v=2.0&key=__KEY__&plugin=AMap.Driving,AMap.TileLayer.Traffic,AMap.Scale"></script>
<style>
  :root{
    --bg:#080C11; --panel:#0D141D; --panel-2:#111A25;
    --line:rgba(255,255,255,.075); --line-2:rgba(255,255,255,.13);
    --text:#E9F0F7; --muted:#8A97A6; --muted-2:#5D6A78;
    --mint:#2DD4A7; --mint-2:#7CF5D3; --amber:#F5C86B; --coral:#FF8A5B;
    --info:#6FB1FF; --danger:#FF7A7A;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC",sans-serif;
    --mono:"SFMono-Regular",Consolas,"Cascadia Code","Liberation Mono",monospace;
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;height:100%;overflow:hidden;background:var(--bg);color:var(--text);font-family:var(--sans)}
  button{font-family:inherit}
  #app{display:grid;grid-template-columns:376px minmax(0,1fr);height:__HEIGHT__px;width:100%;overflow:hidden;background:var(--bg)}
  #panel{
    position:relative;z-index:20;display:flex;flex-direction:column;min-width:0;
    background:linear-gradient(180deg,#0F1721 0%,#0B1119 55%,#090E14 100%);
    border-right:1px solid var(--line);
  }
  #panel:before{content:"";position:absolute;inset:0;pointer-events:none;
    background:radial-gradient(420px 220px at 20% -10%,rgba(45,212,167,.10),transparent 65%),
               radial-gradient(320px 220px at 110% 20%,rgba(245,200,107,.05),transparent 60%);}
  #panel>*{position:relative}
  .brand{display:flex;align-items:center;gap:11px;padding:16px 20px 11px 20px}
  .brand .mark{width:34px;height:34px;border-radius:9px;border:1px solid rgba(45,212,167,.55);
    display:flex;align-items:center;justify-content:center;color:var(--mint);font:700 11px/1 var(--mono);
    background:rgba(45,212,167,.09);box-shadow:0 0 0 4px rgba(45,212,167,.04)}
  .brand .k{font-size:9.5px;letter-spacing:.24em;color:var(--mint);font-family:var(--mono)}
  .brand .v{font-size:15px;font-weight:600;margin-top:2px;letter-spacing:.02em;line-height:1.5}
  .brand .dot{margin-left:auto;width:7px;height:7px;border-radius:50%;background:var(--mint);
    box-shadow:0 0 0 4px rgba(45,212,167,.12);animation:breathe 2.4s ease-in-out infinite}
  @keyframes breathe{0%,100%{opacity:1;box-shadow:0 0 0 4px rgba(45,212,167,.10)}50%{opacity:.45;box-shadow:0 0 0 7px rgba(45,212,167,.03)}}
  .od{padding:0 20px 8px 20px}
  .od-kicker{font-size:9.5px;letter-spacing:.18em;color:var(--muted-2);font-family:var(--mono);margin-bottom:5px}
  .od h1{margin:0;font-size:16px;line-height:1.36;font-weight:700;letter-spacing:.005em;min-height:42px}
  .od h1 .arrow{color:var(--mint);padding:0 5px;font-weight:400}
  .od h1 .sub{display:block;color:var(--text);font-size:16.5px}
  .od-sub{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:7px;font-size:10.5px;color:var(--muted)}
  .badge{display:inline-flex;align-items:center;gap:5px;padding:2px 7px;border-radius:999px;
    border:1px solid var(--line-2);background:rgba(255,255,255,.025);font-size:10px;color:var(--muted)}
  .badge.real{color:#A7F3DE;border-color:rgba(45,212,167,.35);background:rgba(45,212,167,.08)}
  .badge.est{color:#FFE0A0;border-color:rgba(245,200,107,.38);background:rgba(245,200,107,.08)}
  .search{display:flex;align-items:center;gap:8px;margin:2px 20px 6px 20px;padding:7px 10px;
    border:1px solid var(--line);border-radius:9px;background:rgba(255,255,255,.025)}
  .search svg{flex:none;opacity:.55}
  .search input{flex:1;min-width:0;border:0;outline:0;background:transparent;color:var(--text);font-size:12px}
  .search input::placeholder{color:var(--muted-2)}
  .search .n{font-size:10px;color:var(--muted-2);font-family:var(--mono);white-space:nowrap}
  .list{flex:0 0 178px;max-height:178px;min-height:76px;overflow-y:auto;padding:0 10px 6px 10px;scrollbar-width:thin}
  .item{position:relative;display:flex;align-items:center;gap:9px;width:100%;padding:8px 9px 8px 12px;
    border:1px solid transparent;border-radius:9px;background:transparent;color:var(--text);
    text-align:left;cursor:pointer;transition:background .18s,border-color .18s,transform .18s}
  .item:hover{background:rgba(255,255,255,.035);transform:translateX(1px)}
  .item.on{background:linear-gradient(90deg,rgba(45,212,167,.12),rgba(45,212,167,.025));
    border-color:rgba(45,212,167,.25)}
  .item .rail{position:absolute;left:3px;top:12px;bottom:12px;width:2px;border-radius:2px;background:transparent}
  .item.on .rail{background:var(--mint);box-shadow:0 0 8px rgba(45,212,167,.7)}
  .item .names{min-width:0;flex:1}
  .item .names b{display:block;font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .item .names span{display:block;font-size:10px;color:var(--muted-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
  .item .meta{flex:none;text-align:right;font-family:var(--mono);font-size:10px;color:var(--muted);line-height:1.45}
  .item .meta i{display:block;font-style:normal;color:var(--mint);font-size:9.5px}
  .sec{display:flex;align-items:center;gap:7px;padding:0 20px;margin:8px 0 5px 0;
    font-size:9.5px;letter-spacing:.17em;color:var(--muted-2);font-family:var(--mono);text-transform:uppercase}
  .sec:after{content:"";flex:1;height:1px;background:var(--line)}
  .pills{display:flex;gap:5px;padding:0 20px 4px 20px;flex-wrap:wrap}
  .pill{flex:1;min-width:72px;border:1px solid var(--line);background:rgba(255,255,255,.02);
    color:var(--muted);font-size:10.5px;padding:6px 4px;border-radius:7px;cursor:pointer;
    transition:all .18s;white-space:nowrap}
  .pill:hover{color:var(--text);border-color:var(--line-2)}
  .pill.on{color:#062019;background:var(--mint);border-color:var(--mint);font-weight:700;
    box-shadow:0 4px 14px rgba(45,212,167,.18)}
  .live-head{display:flex;align-items:center;gap:8px;padding:0 20px;margin:9px 0 5px 0}
  .live-head .mini{margin-left:auto;border:1px solid var(--line);background:rgba(255,255,255,.02);color:var(--muted);font-size:9px;padding:3px 7px;border-radius:5px;cursor:pointer}
  .live-head .mini:hover{color:var(--text);border-color:var(--line-2)}
  .live-head .t{font-size:9.5px;letter-spacing:.17em;color:var(--muted-2);font-family:var(--mono)}
  .status{display:inline-flex;align-items:center;gap:5px;font-size:9.5px;color:var(--muted);font-family:var(--mono)}
  .status:before{content:"";width:5px;height:5px;border-radius:50%;background:var(--muted-2)}
  .status.live{color:var(--mint)} .status.live:before{background:var(--mint);box-shadow:0 0 6px var(--mint)}
  .status.planning{color:var(--amber)} .status.planning:before{background:var(--amber);animation:breathe 1s infinite}
  .status.cached{color:var(--info)} .status.cached:before{background:var(--info)}
  .status.error{color:var(--danger)} .status.error:before{background:var(--danger)}
  .tri{display:grid;grid-template-columns:1.15fr 1fr 1fr;gap:0;margin:0 20px 8px 20px;
    border:1px solid var(--line);border-radius:10px;overflow:hidden;background:rgba(255,255,255,.018)}
  .tri>div{padding:7px 9px 6px 9px;border-right:1px solid var(--line);min-width:0}
  .tri>div:last-child{border-right:0}
  .tri span{display:block;font-size:9px;color:var(--muted-2);letter-spacing:.05em}
  .tri b{display:inline-block;font-family:var(--mono);font-size:17px;line-height:1.2;font-variant-numeric:tabular-nums;margin-top:1px;white-space:nowrap}
  .tri i{font-style:normal;font-size:9px;color:var(--muted-2);margin-left:2px}
  .tri .flash b{animation:flash .55s ease}
  @keyframes flash{0%{color:var(--mint-2);transform:translateY(-1px)}100%{color:var(--text);transform:none}}
  .ledger{margin:0 20px 6px 20px;padding:8px 10px 7px 10px;border:1px solid var(--line);border-radius:10px;
    background:linear-gradient(160deg,rgba(245,200,107,.055),rgba(255,255,255,.012))}
  .ledger-head{display:flex;align-items:center;gap:7px;margin-bottom:6px}
  .ledger-head .t{font-size:9.5px;letter-spacing:.16em;color:var(--amber);font-family:var(--mono)}
  .ledger-head .tag{margin-left:auto;font-size:8.5px;color:var(--muted-2);border:1px solid var(--line);
    padding:1px 5px;border-radius:999px}
  .quad{display:grid;grid-template-columns:1fr 1fr;gap:5px 10px}
  .quad .k{font-size:9px;color:var(--muted-2)}
  .quad .v{font-family:var(--mono);font-size:13px;font-variant-numeric:tabular-nums;color:var(--text);line-height:1.25}
  .quad .v em{font-style:normal;font-size:9px;color:var(--muted-2);margin-left:2px}
  .quad .v.hl{color:var(--mint)}
  .veh{display:flex;align-items:baseline;gap:6px;margin-top:7px;padding-top:6px;border-top:1px dashed var(--line)}
  .veh span{font-size:9px;color:var(--muted-2)} .veh b{font-size:11px;font-weight:600;color:var(--text)}
  .ledger-note{margin-top:4px;font-size:8px;line-height:1.45;color:var(--muted-2)}
  .ledger-note.warn{color:var(--amber)}
  .steps{min-height:28px;max-height:64px;overflow-y:auto;padding:0 20px 5px 20px;scrollbar-width:thin}
  .step{display:flex;gap:8px;padding:3px 0;font-size:10px;line-height:1.45;color:var(--muted)}
  .step .n{flex:none;width:14px;height:14px;border-radius:50%;border:1px solid var(--line-2);
    display:flex;align-items:center;justify-content:center;font-size:8px;color:var(--muted-2);margin-top:1px}
  .step .x{min-width:0;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .step .x b{color:var(--text);font-weight:500}
  .step .d{flex:none;font-family:var(--mono);font-size:9px;color:var(--muted-2)}
  .panel-foot{padding:6px 18px 9px 18px;border-top:1px solid var(--line);font-size:8px;line-height:1.5;color:var(--muted-2)}
  .panel-foot b{color:var(--muted)}

  #stage{position:relative;min-width:0;height:100%;background:#0B1118;overflow:hidden}
  #map{position:absolute;inset:0;background:#0B1118}
  .amap-logo{opacity:.72}
  .amap-copyright{background:transparent!important;color:rgba(255,255,255,.38)!important;font-size:9px!important}
  .hud{position:absolute;z-index:30;pointer-events:none}
  .hud>*{pointer-events:auto}
  .hud.top{left:18px;top:18px;max-width:calc(100% - 220px)}
  .hud-title{display:inline-flex;align-items:center;gap:7px;padding:6px 10px;border:1px solid var(--line);
    border-radius:8px;background:rgba(8,12,17,.74);backdrop-filter:blur(10px);font-size:10.5px;color:var(--muted)}
  .hud-title .p{width:6px;height:6px;border-radius:50%;background:var(--mint);box-shadow:0 0 8px var(--mint)}
  .hud-title b{color:var(--text);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:330px;display:inline-block;vertical-align:bottom}
  .hud-sub{margin-top:6px;display:inline-flex;align-items:center;gap:6px;padding:4px 8px;border-radius:6px;
    background:rgba(8,12,17,.55);font-size:9.5px;color:var(--muted-2);font-family:var(--mono);backdrop-filter:blur(6px)}
  .hud.tools{right:18px;top:18px;display:flex;gap:6px}
  .tool{border:1px solid var(--line);background:rgba(8,12,17,.72);backdrop-filter:blur(10px);
    color:var(--muted);font-size:10px;padding:6px 9px;border-radius:7px;cursor:pointer;transition:all .18s}
  .tool:hover{color:var(--text);border-color:var(--line-2);background:rgba(13,20,29,.92)}
  .tool.on{color:#062019;background:var(--mint);border-color:var(--mint);font-weight:700}
  .tool:disabled{opacity:.38;cursor:not-allowed}
  .tool.icon{width:28px;padding:6px 0;text-align:center;font-family:var(--mono);font-size:13px;line-height:1}
  .alts{position:absolute;z-index:30;left:50%;bottom:54px;transform:translateX(-50%);display:none;gap:5px;
    padding:4px;border:1px solid var(--line);border-radius:9px;background:rgba(8,12,17,.78);backdrop-filter:blur(10px)}
  .alts.on{display:flex}
  .alt{border:0;background:transparent;color:var(--muted);font-size:9.5px;padding:4px 8px;border-radius:6px;cursor:pointer;white-space:nowrap}
  .alt.on{background:rgba(45,212,167,.15);color:#A7F3DE}
  .hud.bottom{left:18px;right:18px;bottom:34px;display:flex;align-items:flex-end;justify-content:space-between;gap:12px}
  .legend{display:flex;align-items:center;gap:7px;font-size:9px;color:var(--muted-2);
    background:rgba(8,12,17,.58);border:1px solid var(--line);padding:5px 8px;border-radius:6px;backdrop-filter:blur(6px)}
  .lg{width:6px;height:6px;border-radius:50%;display:inline-block}
  .lg.start{background:var(--mint)} .lg.end{background:var(--coral)}
  .lg.line{width:14px;height:2px;border-radius:2px;background:var(--mint);box-shadow:0 0 6px rgba(45,212,167,.7)}
  .attr{font-size:8.5px;color:rgba(255,255,255,.34);font-family:var(--mono);text-align:right;line-height:1.5}
  .progress{position:absolute;z-index:31;left:18px;bottom:86px;width:260px;padding:7px 9px;border:1px solid var(--line);
    border-radius:8px;background:rgba(8,12,17,.78);backdrop-filter:blur(10px);display:none}
  .progress.on{display:block}
  .progress .bar{height:3px;background:rgba(255,255,255,.1);border-radius:3px;overflow:hidden}
  .progress .bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--mint),var(--mint-2));
    box-shadow:0 0 8px rgba(45,212,167,.7);transition:width .08s linear}
  .progress .tx{display:flex;justify-content:space-between;gap:8px;margin-top:5px;font-size:8.5px;color:var(--muted-2);font-family:var(--mono)}
  .progress .tx b{color:var(--text);font-weight:500}
  .offline{position:absolute;z-index:60;inset:0;display:none;align-items:center;justify-content:center;
    background:radial-gradient(600px 320px at 50% 40%,rgba(255,138,91,.08),transparent 70%),rgba(8,12,17,.92)}
  .offline.on{display:flex}
  .offline .box{max-width:360px;padding:22px 24px;border:1px solid rgba(245,200,107,.3);border-radius:14px;
    background:linear-gradient(160deg,rgba(245,200,107,.08),rgba(13,20,29,.96));text-align:left}
  .offline .box .k{font-size:9.5px;letter-spacing:.18em;color:var(--amber);font-family:var(--mono)}
  .offline .box h3{margin:7px 0 6px 0;font-size:15px}
  .offline .box p{margin:0;color:var(--muted);font-size:11px;line-height:1.7}
  .offline .box button{margin-top:13px;border:1px solid rgba(245,200,107,.45);background:rgba(245,200,107,.1);
    color:#FFE0A0;font-size:11px;padding:7px 12px;border-radius:8px;cursor:pointer}
  #boot{position:absolute;z-index:70;inset:0;display:flex;align-items:center;justify-content:center;
    background:#080C11;transition:opacity .45s}
  #boot.off{opacity:0;pointer-events:none}
  .boot-in{display:flex;align-items:center;gap:11px;color:var(--muted);font-size:11px;font-family:var(--mono);letter-spacing:.08em}
  .spinner{width:18px;height:18px;border:2px solid rgba(45,212,167,.2);border-top-color:var(--mint);
    border-radius:50%;animation:spin .9s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}

  .pin{position:relative;display:flex;align-items:center;gap:0;transform:translateY(-2px)}
  .pin .dot{width:24px;height:24px;border-radius:50% 50% 50% 4px;transform:rotate(-45deg);
    display:flex;align-items:center;justify-content:center;border:2px solid #07120F;
    box-shadow:0 0 0 5px rgba(45,212,167,.12),0 4px 12px rgba(0,0,0,.45)}
  .pin .dot span{transform:rotate(45deg);font:700 9px/1 var(--mono);color:#04110D}
  .pin.start .dot{background:var(--mint)}
  .pin.end .dot{background:var(--coral);box-shadow:0 0 0 5px rgba(255,138,91,.12),0 4px 12px rgba(0,0,0,.45)}
  .pin .lbl{margin-left:6px;padding:3px 7px;border-radius:5px;background:rgba(8,12,17,.82);
    border:1px solid rgba(255,255,255,.12);color:var(--text);font-size:10px;white-space:nowrap;
    box-shadow:0 4px 12px rgba(0,0,0,.35);backdrop-filter:blur(6px)}
  .pin .ring{position:absolute;left:-5px;top:-5px;width:34px;height:34px;border-radius:50%;
    border:1px solid rgba(45,212,167,.5);animation:pulse 2.2s ease-out infinite}
  .pin.end .ring{border-color:rgba(255,138,91,.5)}
  @keyframes pulse{0%{transform:scale(.65);opacity:.9}100%{transform:scale(1.35);opacity:0}}
  .dir{width:13px;height:13px;display:flex;align-items:center;justify-content:center;color:#EAFFF8;
    font-size:8px;line-height:1;text-shadow:0 0 3px rgba(0,0,0,.9);opacity:.9}
  .truck{width:30px;height:30px;border-radius:50%;background:#0A1512;border:1.5px solid var(--mint);
    display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 5px rgba(45,212,167,.12),0 0 18px rgba(45,212,167,.55)}
  .truck svg{width:17px;height:17px;display:block}
  .compact-card{display:none}
  .compact .compact-card{display:block;position:absolute;z-index:35;left:18px;bottom:92px;
    width:310px;padding:10px 13px 13px 13px;border:1px solid var(--line);border-radius:11px;
    background:rgba(8,12,17,.82);backdrop-filter:blur(12px);box-shadow:0 10px 30px rgba(0,0,0,.35)}
  .compact .compact-card .t{font-size:10.5px;font-weight:600;line-height:1.4;margin-bottom:6px}
  .compact .compact-card .row{display:flex;gap:12px;flex-wrap:wrap;font-family:var(--mono);font-size:10px;color:var(--muted)}
  .compact .compact-card .row b{color:var(--text);font-size:14px;font-weight:600;margin-right:2px}
  .compact .compact-card .row i{font-style:normal;color:var(--muted-2);font-size:9px}
  .compact .compact-card .co2{margin-top:4px;font-size:9px;line-height:1.55;color:var(--amber)}
  body.compact .hud.bottom .legend{display:none}
  body.compact .progress{bottom:66px}

  body.compact #app{grid-template-columns:1fr}
  body.compact #panel{display:none}
  body.compact .hud.top{max-width:calc(100% - 180px)}

  @media (max-width: 1100px){
    #app{grid-template-columns:352px minmax(0,1fr)}
    .hud-sub{display:none}
    .hud-title b{max-width:190px}
    .hud-title > span:not(.p){display:none}
  }
  @media (max-width: 720px){
    #app{grid-template-columns:1fr;grid-template-rows:1fr auto;height:__HEIGHT__px}
    #panel{grid-row:2;max-height:300px;border-right:0;border-top:1px solid var(--line)}
    #stage{grid-row:1;min-height:420px}
    .list{max-height:80px}
    .steps,.panel-foot{display:none}
    .hud.top{max-width:calc(100% - 180px)}
    .hud.tools .tool[data-wide]{display:none}
  }
</style>
</head>
<body class="__MODE__">
<div id="app">
  <aside id="panel">
    <div class="brand">
      <div class="mark">RL</div>
      <div><div class="k">ROUTE LAB / 06</div><div class="v">路线实验室</div></div>
      <div class="dot"></div>
    </div>
    <div class="od">
      <div class="od-kicker">CURRENT CORRIDOR</div>
      <h1 id="odTitle">—</h1>
      <div class="od-sub" id="odSub"></div>
    </div>
    <div class="search">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-3.5-3.5"></path></svg>
      <input id="q" autocomplete="off" placeholder="搜索网点 / 城市 / 线路">
      <span class="n" id="qCount"></span>
    </div>
    <div class="list" id="list"></div>
    <div class="sec">规划策略</div>
    <div class="pills" id="policies">
      <button class="pill on" data-policy="0">综合最优</button>
      <button class="pill" data-policy="4">躲避拥堵</button>
      <button class="pill" data-policy="1">避开收费</button>
      <button class="pill" data-policy="6">不走高速</button>
    </div>
    <div class="live-head"><span class="t">实时路线</span><span class="status" id="status">准备中</span><button class="mini" id="btnReplan" title="重新向高德请求当前线路">重算</button></div>
    <div class="tri" id="liveMetrics">
      <div><span>距离</span><b id="mKm">—</b><i>km</i></div>
      <div><span>时长</span><b id="mMin">—</b><i>min</i></div>
      <div><span>过路费</span><b id="mToll">—</b><i>元</i></div>
    </div>
    <div class="ledger">
      <div class="ledger-head"><span class="t">碳账情景</span><span class="tag">结果表口径</span></div>
      <div class="quad">
        <div><div class="k">路线碳排</div><div class="v hl"><span id="mCo2">—</span><em>t</em></div></div>
        <div><div class="k">运营成本</div><div class="v"><span id="mOpex">—</span><em>元</em></div></div>
        <div><div class="k">碳成本 λ=100</div><div class="v"><span id="mCc">—</span><em>元</em></div></div>
        <div><div class="k">综合得分</div><div class="v"><span id="mScore">—</span><em>元</em></div></div>
      </div>
      <div class="veh"><span>推荐车型</span><b id="mVeh">—</b></div>
      <div class="ledger-note" id="ledgerNote">综合策略缓存 · λ=100</div>
    </div>
    <div class="sec">途经道路 <span id="stepCount" style="color:var(--muted-2)"></span></div>
    <div class="steps" id="steps"></div>
    <div class="panel-foot"><b>几何/距离/时长/过路费</b>：高德实时规划　<b>碳排/成本</b>：后端结果表<br>浏览器只展示，不重算碳账。L1 路线情景，与 L0 基准账分列。</div>
  </aside>
  <main id="stage">
    <div id="map"></div>
    <div class="hud top">
      <div class="hud-title"><span class="p"></span><b id="hudTitle">路线实验室</b><span>· 高德实时路网</span></div>
      <div class="hud-sub" id="hudSub">正在加载路网…</div>
    </div>
    <div class="hud tools">
      <button class="tool" id="btnBase" title="切换亮色路网底图，检查路线是否贴合道路">路网</button>
      <button class="tool" id="btnTraffic" title="显示实时路况">路况</button>
      <button class="tool" id="btnReplay" title="重播路线生长动画">重播</button>
      <button class="tool" id="btnPlay" title="演示车辆沿路线行驶">在途演示</button>
      <button class="tool icon" id="btnZoomIn" title="放大">+</button>
      <button class="tool icon" id="btnZoomOut" title="缩小">−</button>
    </div>
    <div class="alts" id="alts"></div>
    <div class="progress" id="progress">
      <div class="bar"><i id="progressBar"></i></div>
      <div class="tx"><span id="progressText">准备中</span><b id="progressPct">0%</b></div>
    </div>
    <div class="compact-card" id="compactCard">
      <div class="t" id="ccTitle">—</div>
      <div class="row">
        <span><b id="ccKm">—</b><i>km</i></span>
        <span><b id="ccMin">—</b><i>min</i></span>
        <span><b id="ccToll">—</b><i>元</i></span>
      </div>
      <div class="co2">碳账 <span id="ccCo2">—</span> t · 运营 <span id="ccOpex">—</span> 元</div>
    </div>
    <div class="hud bottom">
      <div class="legend"><span class="lg start"></span>起点 <span class="lg end"></span>终点 <span class="lg line"></span>推荐路线</div>
      <div class="attr">高德地图 · 路线几何实时规划<br>碳账数字来自结果表，浏览器不重算</div>
    </div>
    <div class="offline" id="offline">
      <div class="box">
        <div class="k">OFFLINE / 在线地图未就绪</div>
        <h3>高德路网没有加载成功</h3>
        <p id="offlineMsg">请检查网络、JS API key 与域名白名单。左侧碳账数字仍可查看；也可以回到离线示意图。</p>
        <button onclick="location.reload()">重新加载地图</button>
      </div>
    </div>
    <div id="boot"><div class="boot-in"><span class="spinner"></span><span>正在接入高德实时路网…</span></div></div>
  </main>
</div>
<script>
(function(){
  "use strict";
  var DATA = __PAYLOAD__;
  var MODE = "__MODE__";
  var ROUTES = (DATA.routes || []).filter(function(r){ return r && r.o && r.d && r.o.length === 2 && r.d.length === 2; });
  var BY_ID = {};
  ROUTES.forEach(function(r){ BY_ID[r.id] = r; });

  var $ = function(s){ return document.querySelector(s); };
  var state = {
    id: DATA.default || (ROUTES[0] && ROUTES[0].id),
    policy: 0,
    live: null,
    alts: [],
    altIndex: 0,
    cache: {},
    map: null,
    overlays: [],
    traffic: null,
    playing: false,
    baseNormal: false,
    progressKm: 0,
    totalKm: 0,
    cum: null,
    path: null,
    truck: null,
    playRaf: 0,
    seq: 0,
    info: null,
    booted: false
  };
  window.__ROUTE_LAB__ = {ready:false,error:null,live:null,points:0,routeId:state.id,mode:MODE};
  window.__ROUTE_LAB__.setStyle = function(s){ try{ state.map.setMapStyle(s); return true; }catch(e){ return String(e); } };
  window.__ROUTE_LAB__.zoomTo = function(lng,lat,zoom){ try{ state.map.setZoomAndCenter(zoom,[lng,lat]); return true; }catch(e){ return String(e); } };

  function esc(s){
    return String(s == null ? "" : s).replace(/[&<>"']/g, function(c){
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];
    });
  }
  function num(v, d){
    if(v === null || v === undefined || v === "" || isNaN(Number(v))) return "—";
    return Number(v).toLocaleString("zh-CN", {minimumFractionDigits:d||0, maximumFractionDigits:d||0});
  }
  function fixed(v, d){
    if(v === null || v === undefined || v === "" || isNaN(Number(v))) return "—";
    return Number(v).toFixed(d||0);
  }
  function toLL(p){
    if(!p) return null;
    if(Array.isArray(p)){
      var a = Number(p[0]), b = Number(p[1]);
      return (isFinite(a) && isFinite(b)) ? [a,b] : null;
    }
    if(typeof p.getLng === "function") return [Number(p.getLng()), Number(p.getLat())];
    if(p.lng !== undefined) return [Number(p.lng), Number(p.lat)];
    return null;
  }
  function routeById(id){ return BY_ID[id] || ROUTES[0]; }
  function fmtPct(v){ return Math.max(0, Math.min(100, Math.round(v*100))) + "%"; }

  function renderHeader(r){
    $("#odTitle").innerHTML = "<span class='sub'>" + esc(r.on) + "</span><span class='arrow'>→</span>" + esc(r.dn);
    var b = r.est
      ? "<span class='badge est'>离线兜底</span>"
      : "<span class='badge real'>高德真路线</span>";
    $("#odSub").innerHTML = b + "<span class='badge'>网点级 OD</span><span class='badge'>L1 路线情景</span>";
    var shortOn = (r.on.length > 12) ? (r.on.slice(0, 11) + "…") : r.on;
    var shortDn = (r.dn.length > 12) ? (r.dn.slice(0, 11) + "…") : r.dn;
    $("#hudTitle").textContent = shortOn + " → " + shortDn;
    $("#hudSub").textContent = "高德实时路网 · " + (r.est ? "该 OD 为离线坐标，实时路线可能偏差" : "路线几何实时规划");
    $("#ccTitle").textContent = r.on + " → " + r.dn;
  }
  function renderStatic(r){
    $("#mKm").textContent = num(r.km, 1);
    $("#mMin").textContent = num(r.min, 0);
    $("#mToll").textContent = num(r.toll, 0);
    $("#mCo2").textContent = (r.co2 == null ? "—" : fixed(r.co2, 2));
    $("#mOpex").textContent = (r.opex == null ? "—" : num(r.opex, 0));
    $("#mCc").textContent = (r.cc == null ? "—" : num(r.cc, 0));
    $("#mScore").textContent = (r.score == null ? "—" : num(r.score, 0));
    $("#mVeh").textContent = r.veh || "—";
    $("#ccKm").textContent = num(r.km, 1);
    $("#ccMin").textContent = num(r.min, 0);
    $("#ccToll").textContent = num(r.toll, 0);
    $("#ccCo2").textContent = (r.co2 == null ? "—" : fixed(r.co2, 2));
    $("#ccOpex").textContent = (r.opex == null ? "—" : num(r.opex, 0));
  }
  function setStatus(kind, text){
    var el = $("#status");
    el.className = "status " + (kind || "");
    el.textContent = text || "—";
  }
  function updateLedgerNote(r, live){
    var note = $("#ledgerNote");
    if(!note) return;
    var parts = [];
    if(state.policy !== 0) parts.push("当前策略仅重算几何/距离/时长/过路费");
    if(live && r.km && Math.abs(live.km - r.km) / r.km > 0.03){
      parts.push("实时里程与缓存相差 " + Math.round(Math.abs(live.km - r.km) / r.km * 100) + "%");
    }
    if(parts.length){
      note.className = "ledger-note warn";
      note.textContent = parts.join("；") + "；碳账仍按综合策略缓存（λ=100）";
    }else{
      note.className = "ledger-note";
      note.textContent = "综合策略缓存 · λ=100";
    }
  }
  function flashMetric(){
    var el = $("#liveMetrics");
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
  }
  function setLiveMetrics(km, min, toll, animate){
    $("#mKm").textContent = num(km, 1);
    $("#mMin").textContent = num(min, 0);
    $("#mToll").textContent = num(toll, 0);
    $("#ccKm").textContent = num(km, 1);
    $("#ccMin").textContent = num(min, 0);
    $("#ccToll").textContent = num(toll, 0);
    if(animate) flashMetric();
  }
  function renderSteps(steps){
    var box = $("#steps");
    box.innerHTML = "";
    var list = (steps || []).slice(0, 8);
    $("#stepCount").textContent = steps && steps.length ? "(" + steps.length + " 段)" : "";
    if(!list.length){ box.innerHTML = "<div class='step'><div class='x'>暂无转向信息</div></div>"; return; }
    list.forEach(function(s, i){
      var d = document.createElement("div"); d.className = "step";
      var body = s.road ? ("<b>" + esc(s.road) + "</b>　" + esc(s.ins || "")) : esc(s.ins || s.road || "—");
      d.innerHTML = "<span class='n'>" + (i+1) + "</span><span class='x'>" + body + "</span><span class='d'>" + fixed(s.km,1) + " km</span>";
      box.appendChild(d);
    });
  }
  function renderList(){
    var q = ($("#q").value || "").trim().toLowerCase();
    var list = ROUTES.filter(function(r){
      if(!q) return true;
      return (r.on + " " + r.dn + " " + (r.od || "")).toLowerCase().indexOf(q) >= 0;
    });
    var limited = list.slice(0, 60);
    $("#qCount").textContent = limited.length + " / " + list.length;
    var box = $("#list");
    box.innerHTML = "";
    limited.forEach(function(r){
      var b = document.createElement("button");
      b.className = "item" + (r.id === state.id ? " on" : "");
      b.type = "button";
      b.innerHTML = "<span class='rail'></span><span class='names'><b>" + esc(r.on) + "</b><span>→ " + esc(r.dn) + "</span></span>"
        + "<span class='meta'>" + num(r.km,0) + " km<i>" + (r.co2 == null ? "—" : fixed(r.co2,2) + " t") + "</i></span>";
      b.addEventListener("click", function(){ selectRoute(r.id, false); });
      box.appendChild(b);
    });
    if(!limited.length){
      box.innerHTML = "<div style='padding:14px 12px;color:var(--muted-2);font-size:11px'>没有匹配的线路</div>";
    }
    var on = box.querySelector(".item.on");
    if(on && on.scrollIntoView) on.scrollIntoView({block:"nearest"});
  }

  function clearOverlays(){
    if(state.map && state.overlays.length){
      try{ state.map.remove(state.overlays); }catch(e){}
    }
    state.overlays = [];
    state.truck = null;
    state.info = null;
    state.lines = null;
    state.path = null;
    state.cum = null;
    stopPlay(true);
  }
  function addOverlay(x){ state.overlays.push(x); if(state.map) state.map.add(x); }

  function parseResult(result){
    var routes = (result.routes || []).map(function(rt){
      var path = [], last = null;
      (rt.steps || []).forEach(function(st){
        (st.path || []).forEach(function(p){
          var q = toLL(p);
          if(q && (!last || Math.abs(q[0]-last[0]) > 1e-9 || Math.abs(q[1]-last[1]) > 1e-9)){
            path.push(q); last = q;
          }
        });
      });
      var steps = (rt.steps || []).map(function(st){
        return {road: st.road || "", ins: st.instruction || "", km: (+st.distance || 0)/1000, min: (+st.time || 0)/60};
      });
      return {path:path, km:(+rt.distance||0)/1000, min:(+rt.time||0)/60, toll:+(rt.tolls || rt.toll || 0), steps:steps};
    }).filter(function(x){ return x.path.length > 1; });
    return {routes:routes, route:routes[0] || null};
  }

  function plan(r, force){
    var key = r.id + "|" + state.policy;
    if(!force && state.cache[key]){
      applyLive(state.cache[key], r, true);
      return;
    }
    setStatus("planning", "实时规划中");
    $("#hudSub").textContent = "正在向高德请求 " + (r.on || "") + " → " + (r.dn || "") + " 的实时路线…";
    var seq = ++state.seq;
    var done = false;
    var timer = setTimeout(function(){
      if(done) return;
      done = true;
      if(seq === state.seq) fallback(r, "高德路线请求超时");
    }, 14000);
    try{
      var driving = new AMap.Driving({policy: state.policy, hideMarkers: true, showTraffic: false});
      driving.search(r.o, r.d, function(status, result){
        if(done) return;
        done = true;
        clearTimeout(timer);
        if(seq !== state.seq) return;
        if(status !== "complete" || !result || !result.routes || !result.routes.length){
          fallback(r, "高德未返回可用路线");
          return;
        }
        var parsed = parseResult(result);
        if(!parsed.route){ fallback(r, "高德路线几何为空"); return; }
        state.cache[key] = parsed;
        applyLive(parsed, r, false);
      });
    }catch(err){
      clearTimeout(timer);
      fallback(r, "高德路线规划异常：" + (err && err.message ? err.message : err));
    }
  }

  function fallback(r, msg){
    if(!state.map) return;
    clearOverlays();
    var color = "#F5C86B";
    var path = (r.poly && r.poly.length > 1) ? r.poly : [r.o, r.d];
    state.path = path;
    buildCum();
    var dashed = !(r.poly && r.poly.length > 1);
    var glow = new AMap.Polyline({path:path, strokeColor:color, strokeWeight:14, strokeOpacity:.12,
      lineJoin:"round", lineCap:"round", zIndex:35, bubble:true});
    var main = new AMap.Polyline({path:path, strokeColor:color, strokeWeight:4, strokeOpacity:.9,
      strokeStyle:dashed ? "dashed" : "solid", lineJoin:"round", lineCap:"round", zIndex:40, bubble:true});
    addOverlay(glow); addOverlay(main);
    addEndpoints(r, color);
    setLiveMetrics(r.km, r.min, r.toll, true);
    updateLedgerNote(r, null);
    $("#btnPlay").disabled = false;
    setStatus("cached", dashed ? "缓存端点" : "缓存几何");
    $("#hudSub").textContent = msg + " · 已回退" + (dashed ? "起终点示意" : "缓存路线几何");
    renderSteps([]);
    fitView(path, 700);
    window.__ROUTE_LAB__.error = msg;
    window.__ROUTE_LAB__.live = {km:r.km,min:r.min,toll:r.toll,points:path.length,source:"fallback"};
    hideBoot();
  }

  function applyLive(parsed, r, cached){
    if(!state.map) return;
    clearOverlays();
    state.live = parsed.route;
    state.alts = parsed.routes || [];
    state.altIndex = 0;
    state.path = parsed.route.path;
    buildCum();
    renderSteps(parsed.route.steps);
    setLiveMetrics(parsed.route.km, parsed.route.min, parsed.route.toll, true);
    drawRoute(r, parsed.route, false);
    addEndpoints(r, "#2DD4A7");
    addTruck();
    renderAlts();
    updateLedgerNote(r, parsed.route);
    $("#btnPlay").disabled = false;
    setStatus("live", cached ? "已规划" : "高德实时路线");
    $("#hudSub").textContent = "实时路线 " + num(parsed.route.km,1) + " km · " + num(parsed.route.min,0) + " min · 过路费 " + num(parsed.route.toll,0) + " 元";
    window.__ROUTE_LAB__.ready = true;
    window.__ROUTE_LAB__.error = null;
    window.__ROUTE_LAB__.live = {km:parsed.route.km,min:parsed.route.min,toll:parsed.route.toll,points:parsed.route.path.length,source:cached?"cache":"live"};
    window.__ROUTE_LAB__.routeId = r.id;
    hideBoot();
  }

  function lineWeights(){
    var z = state.map ? state.map.getZoom() : 6;
    if(z >= 11) return {glow:8, main:3.6, core:1.1};
    if(z >= 9) return {glow:11, main:4.2, core:1.3};
    return {glow:16, main:5, core:1.6};
  }
  function applyLineWeights(){
    if(!state.lines) return;
    var w = lineWeights();
    state.lines.glow.setOptions({strokeWeight:w.glow});
    state.lines.main.setOptions({strokeWeight:w.main});
    state.lines.core.setOptions({strokeWeight:w.core});
  }

  function drawRoute(r, route, keepCamera){
    var color = r.est ? "#F5C86B" : "#2DD4A7";
    var path = route.path;
    var w = lineWeights();
    var glow = new AMap.Polyline({path:path, strokeColor:color, strokeWeight:w.glow, strokeOpacity:.13,
      lineJoin:"round", lineCap:"round", zIndex:30, bubble:true});
    var main = new AMap.Polyline({path:path, strokeColor:color, strokeWeight:w.main, strokeOpacity:.94,
      lineJoin:"round", lineCap:"round", zIndex:40, bubble:true});
    var core = new AMap.Polyline({path:path, strokeColor:"#E9FFF8", strokeWeight:w.core, strokeOpacity:.7,
      lineJoin:"round", lineCap:"round", zIndex:41, bubble:true});
    state.lines = {glow:glow, main:main, core:core};
    addOverlay(glow); addOverlay(main); addOverlay(core);
    if(!keepCamera) fitView(path, 950);
    addArrows(path, color);
    // 生长动画：用重采样点做 700 个点的推进，最后回填全量几何。
    if(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches){
      return;
    }
    var sample = resample(path, 640);
    if(sample.length < 3) return;
    main.setPath(sample.slice(0,2)); core.setPath(sample.slice(0,2));
    glow.setOptions({strokeOpacity:0});
    var t0 = performance.now(), dur = 1050;
    function frame(t){
      var p = Math.min(1, (t - t0) / dur);
      var e = 1 - Math.pow(1 - p, 3);
      var n = Math.max(2, Math.floor(e * sample.length));
      main.setPath(sample.slice(0, n));
      core.setPath(sample.slice(0, n));
      glow.setOptions({strokeOpacity: 0.14 * e});
      if(p < 1){ requestAnimationFrame(frame); }
      else { main.setPath(path); core.setPath(path); glow.setOptions({strokeOpacity:.14}); }
    }
    requestAnimationFrame(frame);
    main.on("click", function(){ showInfo(r, route); });
    main.on("mouseover", function(){ main.setOptions({strokeWeight:lineWeights().main + 2}); });
    main.on("mouseout", function(){ main.setOptions({strokeWeight:lineWeights().main}); });
  }

  function resample(path, n){
    if(path.length <= n) return path.slice();
    var step = Math.max(1, Math.floor(path.length / n));
    var out = [];
    for(var i=0;i<path.length;i+=step) out.push(path[i]);
    if(out[out.length-1] !== path[path.length-1]) out.push(path[path.length-1]);
    return out;
  }

  function addArrows(path, color){
    var total = path.length;
    if(total < 8) return;
    var count = Math.min(26, Math.max(6, Math.floor(state.totalKm / 55)));
    var step = Math.max(1, Math.floor(total / count));
    for(var i = step; i < total - 2; i += step){
      var a = path[i], b = path[Math.min(total-1, i+2)];
      var ang = bearing(a,b);
      var m = new AMap.Marker({
        position: a, anchor:"center", zIndex:45, bubble:true,
        content: "<div class='dir' style='transform:rotate(" + ang + "deg)'>▲</div>"
      });
      addOverlay(m);
    }
  }

  function bearing(a,b){
    var dx = b[0]-a[0], dy = b[1]-a[1];
    return Math.atan2(dx, dy) * 180 / Math.PI;
  }

  function addEndpoints(r, color){
    var start = state.path[0], end = state.path[state.path.length-1];
    var s = new AMap.Marker({
      position:start, anchor:"bottom-center", zIndex:80, bubble:true,
      content:"<div class='pin start'><span class='ring'></span><span class='dot'><span>A</span></span><span class='lbl'>" + esc(r.on) + "</span></div>"
    });
    var e = new AMap.Marker({
      position:end, anchor:"bottom-center", zIndex:80, bubble:true,
      content:"<div class='pin end'><span class='ring'></span><span class='dot'><span>B</span></span><span class='lbl'>" + esc(r.dn) + "</span></div>"
    });
    addOverlay(s); addOverlay(e);
  }

  function addTruck(){
    var m = new AMap.Marker({
      position: state.path[0], anchor:"center", zIndex:90, bubble:true, visible:false,
      content: "<div class='truck'><svg viewBox='0 0 24 24' fill='none' stroke='#7CF5D3' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'><path d='M1 7h13v9H1z'></path><path d='M14 10h4l3 3v3h-7z'></path><circle cx='6' cy='18' r='1.6'></circle><circle cx='18' cy='18' r='1.6'></circle></svg></div>"
    });
    state.truck = m;
    addOverlay(m);
  }

  function fitView(path, duration){
    if(!state.map || !path || path.length < 2) return;
    var minLng=180,maxLng=-180,minLat=90,maxLat=-90;
    path.forEach(function(p){
      if(p[0]<minLng) minLng=p[0]; if(p[0]>maxLng) maxLng=p[0];
      if(p[1]<minLat) minLat=p[1]; if(p[1]>maxLat) maxLat=p[1];
    });
    var mode = "none";
    try{
      // setFitView 对手上的 overlay 数组最稳；immediately=false 让高德自己做缓动。
      state.map.setFitView(state.overlays, false, [90,90,90,90], 16);
      mode = "fit";
    }catch(e){
      try{
        var bounds = new AMap.Bounds([minLng,minLat],[maxLng,maxLat]);
        state.map.setBounds(bounds, true, [90,90,90,90]);
        mode = "bounds";
      }catch(e2){
        window.__ROUTE_LAB__.fitError = String((e2 && e2.message) || e2 || e);
      }
    }
    setTimeout(function(){
      if(!state.map) return;
      try{
        var c = state.map.getCenter();
        window.__ROUTE_LAB__.debug = {zoom: state.map.getZoom(), lng: c.getLng(), lat: c.getLat(),
          fit: mode, bounds: [minLng,minLat,maxLng,maxLat], overlays: state.overlays.length};
      }catch(_){}
    }, Math.max(200, (duration || 0) + 200));
  }

  function buildCum(){
    if(!state.path || state.path.length < 2){ state.cum=null; state.totalKm=0; return; }
    var cum = [0], total = 0;
    for(var i=1;i<state.path.length;i++){
      total += haversine(state.path[i-1], state.path[i]);
      cum.push(total);
    }
    state.cum = cum; state.totalKm = total;
  }
  function haversine(a,b){
    var R = 6371.0088;
    var p1 = a[1]*Math.PI/180, p2 = b[1]*Math.PI/180;
    var dp = (b[1]-a[1])*Math.PI/180, dl = (b[0]-a[0])*Math.PI/180;
    var x = Math.sin(dp/2)*Math.sin(dp/2) + Math.cos(p1)*Math.cos(p2)*Math.sin(dl/2)*Math.sin(dl/2);
    return 2*R*Math.asin(Math.sqrt(Math.min(1,x)));
  }
  function pointAt(km){
    if(!state.cum || !state.path) return null;
    var lo=0, hi=state.cum.length-1;
    while(lo<hi){ var mid=(lo+hi)>>1; if(state.cum[mid] < km) lo=mid+1; else hi=mid; }
    if(lo===0) return state.path[0];
    var a=state.path[lo-1], b=state.path[lo];
    var seg=state.cum[lo]-state.cum[lo-1] || 1;
    var t=(km-state.cum[lo-1])/seg;
    return [a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t];
  }
  function angleAt(km){
    if(!state.cum || !state.path) return 0;
    var lo=0, hi=state.cum.length-1;
    while(lo<hi){ var mid=(lo+hi)>>1; if(state.cum[mid] < km) lo=mid+1; else hi=mid; }
    var a=state.path[Math.max(0,lo-1)], b=state.path[Math.min(state.path.length-1,lo+1)];
    return bearing(a,b);
  }

  function renderAlts(){
    var box = $("#alts");
    if(!state.alts || state.alts.length < 2){ box.className="alts"; box.innerHTML=""; return; }
    box.className = "alts on";
    box.innerHTML = "";
    state.alts.forEach(function(a,i){
      var b = document.createElement("button");
      b.className = "alt" + (i===state.altIndex ? " on" : "");
      b.textContent = (i===0?"推荐":("方案 "+(i+1))) + " · " + fixed(a.km,0) + "km";
      b.onclick = function(){
        clearOverlays();
        state.altIndex = i; state.live = a; state.path = a.path; buildCum();
        setLiveMetrics(a.km, a.min, a.toll, true);
        drawRoute(routeById(state.id), a, true);
        addEndpoints(routeById(state.id), "#2DD4A7"); addTruck();
        renderAlts();
      };
      box.appendChild(b);
    });
  }

  function showInfo(r, route){
    if(!state.map) return;
    if(!state.info){
      state.info = new AMap.InfoWindow({anchor:"bottom-center", offset:new AMap.Pixel(0,-10)});
    }
    var html = "<div style='padding:10px 12px;font-size:11px;line-height:1.7;color:#E9F0F7;background:#0D141D;border:1px solid rgba(255,255,255,.12);border-radius:8px;min-width:200px'>"
      + "<b>" + esc(r.on) + " → " + esc(r.dn) + "</b><br>"
      + "实时 " + fixed(route.km,1) + " km · " + fixed(route.min,0) + " min · " + fixed(route.toll,0) + " 元<br>"
      + "碳账 " + (r.co2==null?"—":fixed(r.co2,2)+" t") + " · 运营 " + (r.opex==null?"—":num(r.opex,0)+" 元") + "<br>"
      + "<span style='color:#8A97A6'>几何由高德实时规划；碳账来自结果表</span></div>";
    state.info.setContent(html);
    state.info.open(state.map, state.path[Math.floor(state.path.length/2)]);
  }

  function toggleBase(){
    if(!state.map) return;
    state.baseNormal = !state.baseNormal;
    try{
      state.map.setMapStyle(state.baseNormal ? "amap://styles/normal" : "amap://styles/grey");
      $("#btnBase").classList.toggle("on", state.baseNormal);
    }catch(e){}
  }

  function toggleTraffic(){
    if(!state.map) return;
    if(state.traffic){
      state.map.remove(state.traffic); state.traffic = null;
      $("#btnTraffic").classList.remove("on");
      return;
    }
    try{
      state.traffic = new AMap.TileLayer.Traffic({zIndex:10, autoRefresh:true, interval:180});
      state.map.add(state.traffic);
      $("#btnTraffic").classList.add("on");
    }catch(e){ $("#btnTraffic").textContent = "路况不可用"; }
  }

  function stopPlay(reset){
    state.playing = false;
    if(state.playRaf) cancelAnimationFrame(state.playRaf);
    state.playRaf = 0;
    if(state.truck) state.truck.hide();
    if(reset){
      state.progressKm = 0;
      $("#progress").classList.remove("on");
      $("#progressBar").style.width = "0%";
      $("#progressText").textContent = "准备中";
      $("#progressPct").textContent = "0%";
      $("#btnPlay").textContent = "在途演示";
    } else {
      $("#btnPlay").textContent = "继续在途";
    }
  }
  function togglePlay(){
    if(!state.path || !state.totalKm) return;
    if(state.playing){ stopPlay(false); return; }
    state.playing = true;
    $("#btnPlay").textContent = "暂停";
    $("#progress").classList.add("on");
    if(state.progressKm >= state.totalKm) state.progressKm = 0;
    var startKm = state.progressKm, total = state.totalKm;
    var t0 = performance.now();
    var dur = Math.max(3500, 16000 * (1 - startKm / total));
    function frame(t){
      if(!state.playing) return;
      var p = Math.min(1, (t - t0) / dur);
      var km = startKm + (total - startKm) * p;
      state.progressKm = km;
      var pos = pointAt(km);
      if(state.truck && pos){
        state.truck.show(); state.truck.setPosition(pos); state.truck.setAngle(angleAt(km));
      }
      $("#progressBar").style.width = fmtPct(km/total);
      $("#progressPct").textContent = fmtPct(km/total);
      $("#progressText").textContent = "已行驶 " + fixed(km,0) + " km · 剩余 " + fixed(Math.max(0,total-km),0) + " km";
      if(p < 1){ state.playRaf = requestAnimationFrame(frame); }
      else { state.playing = false; $("#btnPlay").textContent = "重播在途"; $("#progressText").textContent = "已到达 · " + fixed(total,0) + " km"; }
    }
    state.playRaf = requestAnimationFrame(frame);
  }

  function selectRoute(id, force){
    var r = routeById(id);
    if(!r) return;
    state.id = id;
    state.policy = state.policy || 0;
    state.live = null; state.alts = []; state.altIndex = 0;
    window.__ROUTE_LAB__.live = null;
    window.__ROUTE_LAB__.ready = false;
    $("#btnPlay").disabled = true;
    clearOverlays();
    renderAlts();
    renderList(); renderHeader(r); renderStatic(r); renderSteps([]);
    updateLedgerNote(r, null);
    setLiveMetrics(r.km, r.min, r.toll, false);
    if(state.map){
      setStatus("planning", "实时规划中");
      plan(r, force);
    }
  }

  function hideBoot(){
    var b = $("#boot");
    if(!b || state.booted) return;
    state.booted = true;
    b.classList.add("off");
    setTimeout(function(){ b.style.display = "none"; }, 500);
  }
  function fail(msg){
    window.__ROUTE_LAB__.error = msg;
    $("#boot").style.display = "none";
    $("#offline").classList.add("on");
    $("#offlineMsg").textContent = msg || "请检查网络、JS API key 与域名白名单。左侧碳账数字仍可查看。";
    setStatus("error", "在线地图未就绪");
  }
  window.__amapFail = function(){ fail("高德 JS API 加载失败：检查 key、securityJsCode 与域名白名单。"); };

  function bind(){
    $("#q").addEventListener("input", renderList);
    document.querySelectorAll(".pill").forEach(function(b){
      b.addEventListener("click", function(){
        document.querySelectorAll(".pill").forEach(function(x){ x.classList.remove("on"); });
        b.classList.add("on");
        state.policy = Number(b.getAttribute("data-policy")) || 0;
        selectRoute(state.id, false);
      });
    });
    $("#btnBase").addEventListener("click", toggleBase);
    $("#btnTraffic").addEventListener("click", toggleTraffic);
    $("#btnReplan").addEventListener("click", function(){ selectRoute(state.id, true); });
    $("#btnReplay").addEventListener("click", function(){
      if(!state.path || !state.live) return;
      var r = routeById(state.id), live = state.live;
      clearOverlays();
      state.path = live.path; buildCum();
      drawRoute(r, live, false);
      addEndpoints(r, "#2DD4A7"); addTruck();
    });
    $("#btnPlay").addEventListener("click", togglePlay);
    $("#btnZoomIn").addEventListener("click", function(){ if(state.map) state.map.zoomIn(); });
    $("#btnZoomOut").addEventListener("click", function(){ if(state.map) state.map.zoomOut(); });
    window.addEventListener("resize", function(){ if(state.map) state.map.resize(); });
    window.addEventListener("keydown", function(e){
      if(e.target && /INPUT|TEXTAREA/.test(e.target.tagName)) return;
      if(e.key === " "){ e.preventDefault(); togglePlay(); }
    });
  }

  function initMap(){
    if(typeof AMap === "undefined"){ fail("高德 JS API 未加载：检查 key、securityJsCode 与域名白名单。"); return; }
    try{
      state.map = new AMap.Map("map", {
        zoom: 5, center:[108,34], viewMode:"2D", mapStyle:"amap://styles/grey",
        showLabel:true, resizeEnable:true, features:["bg","road","building","point"]
      });
    }catch(e){ fail("高德地图初始化失败：" + (e && e.message ? e.message : e)); return; }
    try{ state.map.addControl(new AMap.Scale({position:{left:"18px",bottom:"44px"}})); }catch(e){}
    state.map.on("zoomend", applyLineWeights);
    // 让底图先稳定一帧，再接路线，避免首屏抖动。
    setTimeout(function(){ selectRoute(state.id, false); }, 80);
    setTimeout(function(){ if(!window.__ROUTE_LAB__.ready && !window.__ROUTE_LAB__.error) hideBoot(); }, 9000);
  }

  function boot(){
    if(!ROUTES.length){
      fail("没有可展示的路线数据：先运行 python -m tools.route_fetch。");
      return;
    }
    bind();
    renderHeader(routeById(state.id));
    renderStatic(routeById(state.id));
    renderList();
    initMap();
  }

  if(document.readyState === "loading"){ document.addEventListener("DOMContentLoaded", boot); }
  else { boot(); }
})();
</script>
</body>
</html>
"""


def build_doc(payload: Dict[str, Any], key: str, jscode: str = "",
              mode: str = "lab", height: int = 820,
              title: str = "路线实验室 · 高德实时路线") -> str:
    """拼装自包含 HTML。payload 里只放展示数据，不放 key。"""
    safe_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    doc = (TEMPLATE
           .replace("__TITLE__", str(title))
           .replace("__KEY__", str(key))
           .replace("__JSCODE__", str(jscode or ""))
           .replace("__HEIGHT__", str(int(height)))
           .replace("__MODE__", str(mode))
           .replace("__PAYLOAD__", safe_payload))
    return doc
