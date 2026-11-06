# -*- coding: utf-8 -*-
"""生成高德真实路线单页图（HTML + Chrome headless PNG）。

用途
----
把 data/processed/route_od.parquet 的高德 GCJ-02 polyline 画成汇报级
单页图。几何是**高德缓存实算折线**，不是手绘示意图；里程/时长/收费
来自 route_candidates.parquet 的四策略缓存。四策略共享同一几何时，
本图只画主路线，并在右下角把四策略指标并排列出，避免把一条线画成四条。

运行
----
    python outputs/report/amap_route_figure.py --render

不传 --render 时只写 HTML；传 --render 会起一个本地 HTTP 服务，用
Chrome DevTools Protocol 截 3200x2000 PNG（2x device scale）。CDP 是必须的：
Chrome 的 --screenshot 会漏掉 AMap 的 WebGL 瓦片，只剩路线矢量。

依赖
----
pandas / numpy / websockets / 标准库；Chrome 路径默认从 Windows 常见位置查找。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
OUT_DIR = Path(__file__).resolve().parent
DEFAULT_HTML = OUT_DIR / "amap_route_figure.html"
DEFAULT_PNG = ROOT.parent / "可视化_高德真实路线_20261106.png"

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _read_key(name: str) -> str:
    path = ROOT / "config" / name
    if not path.exists():
        raise SystemExit("缺少 %s；请先放高德 JS key" % path)
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise SystemExit("%s 为空" % path)
    return value


def load_route(origin_kw: str, dest_kw: str) -> Dict[str, Any]:
    route = pd.read_parquet(PROCESSED / "route_od.parquet")
    mask = (route["origin"].astype(str).str.contains(origin_kw, regex=False) &
            route["dest"].astype(str).str.contains(dest_kw, regex=False))
    if not mask.any():
        raise SystemExit("route_od 中找不到 %s → %s" % (origin_kw, dest_kw))
    row = route[mask].iloc[0]
    points: List[List[float]] = []
    for chunk in str(row["polyline"]).split(";"):
        parts = chunk.split(",")
        if len(parts) < 2:
            continue
        try:
            lng, lat = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        if -180 <= lng <= 180 and -90 <= lat <= 90:
            point = [round(lng, 6), round(lat, 6)]
            if not points or point != points[-1]:
                points.append(point)
    if len(points) < 2:
        raise SystemExit("该 OD 没有有效 polyline")
    return {
        "od_key": str(row["od_key"]),
        "origin": str(row["origin"]),
        "dest": str(row["dest"]),
        "o_lng": float(row["o_lng"]), "o_lat": float(row["o_lat"]),
        "d_lng": float(row["d_lng"]), "d_lat": float(row["d_lat"]),
        "distance_km": float(row["distance_km"]),
        "duration_min": float(row["duration_min"]),
        "toll_yuan": float(row.get("toll_yuan", 0.0) or 0.0),
        "traffic_lights": int(row.get("traffic_lights", 0) or 0),
        "polyline": points,
    }


def load_candidates(od_key: str) -> List[Dict[str, Any]]:
    frame = pd.read_parquet(PROCESSED / "route_candidates.parquet")
    rows = frame[frame["od_key"].astype(str) == od_key]
    out: List[Dict[str, Any]] = []
    for _, row in rows.sort_values("strategy").iterrows():
        out.append({
            "strategy": int(row["strategy"]),
            "name": str(row.get("strategy_name") or "S%d" % int(row["strategy"])),
            "distance_km": float(row["distance_km"]),
            "duration_min": float(row["duration_min"]),
            "toll_yuan": float(row.get("toll_yuan", 0.0) or 0.0),
            "traffic_lights": int(row.get("traffic_lights", 0) or 0),
            "source": str(row.get("source", "")),
        })
    return out


def _haversine_km(a: Sequence[float], b: Sequence[float]) -> float:
    lng1, lat1 = math.radians(a[0]), math.radians(a[1])
    lng2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2.0) ** 2
    return 2.0 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))


def distance_markers(points: Sequence[Sequence[float]], step_km: float = 10.0) -> List[Dict[str, Any]]:
    """沿折线每 step_km 插一个里程牌；插值在相邻两点间线性完成。"""
    if len(points) < 2:
        return []
    cumulative = [0.0]
    for prev, cur in zip(points[:-1], points[1:]):
        cumulative.append(cumulative[-1] + _haversine_km(prev, cur))
    total = cumulative[-1]
    markers: List[Dict[str, Any]] = []
    target = step_km
    idx = 1
    while target < total - 0.5:
        while idx < len(cumulative) - 1 and cumulative[idx] < target:
            idx += 1
        left, right = cumulative[idx - 1], cumulative[idx]
        span = max(right - left, 1e-9)
        ratio = (target - left) / span
        a, b = points[idx - 1], points[idx]
        markers.append({
            "km": int(round(target)),
            "pos": [round(a[0] + (b[0] - a[0]) * ratio, 6),
                    round(a[1] + (b[1] - a[1]) * ratio, 6)],
        })
        target += step_km
    return markers


def _ef_for(profile: Any, speed_kmh: float, load_t: float) -> Dict[str, Any]:
    return profile.cell(speed_kmh, load_t)


def build_html(route: Dict[str, Any], candidates: Sequence[Dict[str, Any]],
               profile: Any, *, load_t: float = 12.0,
               js_key: str, security_code: str) -> str:
    speed = route["distance_km"] / max(route["duration_min"] / 60.0, 1e-9)
    cell = _ef_for(profile, speed, load_t)
    carbon_kg = float(cell["ef_g_km"]) * route["distance_km"] / 1000.0
    km_markers = distance_markers(route["polyline"], 10.0)
    payload = {
        "n_points": len(route["polyline"]),
        "points": route["polyline"],
        "origin": [route["o_lng"], route["o_lat"]],
        "dest": [route["d_lng"], route["d_lat"]],
        "origin_label": route["origin"],
        "dest_label": route["dest"],
        "km_markers": km_markers,
        "distance_km": round(route["distance_km"], 3),
        "duration_min": round(route["duration_min"], 2),
        "avg_speed_kmh": round(speed, 2),
        "toll_yuan": round(route["toll_yuan"], 2),
        "traffic_lights": int(route["traffic_lights"]),
        "ef_g_km": round(float(cell["ef_g_km"]), 3),
        "ef_speed_bin": str(cell["speed_bin"]),
        "ef_load_bin": str(cell["load_bin"]),
        "ef_n_trips": int(cell["n_trips"]),
        "carbon_kg": round(carbon_kg, 3),
        "load_t": round(load_t, 1),
        "candidates": [
            {**c, "distance_km": round(c["distance_km"], 3),
             "duration_min": round(c["duration_min"], 2),
             "toll_yuan": round(c["toll_yuan"], 2)}
            for c in candidates
        ],
    }
    return HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False)) \
        .replace("__JS_KEY__", js_key).replace("__SECURITY_CODE__", security_code)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_cdp_target(port: int, needle: str, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=1.0) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
            for item in targets:
                if item.get("type") == "page" and needle in str(item.get("url", "")):
                    return str(item["webSocketDebuggerUrl"])
        except Exception as exc:  # Chrome 还没起来 / 端口还没监听
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError("Chrome DevTools 未就绪：%s" % last_error)


async def _cdp_capture(ws_url: str, png_path: Path, *, width: int, height: int, scale: int) -> None:
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover - 环境依赖
        raise SystemExit("截图需要 websockets：pip install websockets") from exc

    async with websockets.connect(ws_url, max_size=40_000_000) as ws:
        counter = 0

        async def cmd(method: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
            nonlocal counter
            counter += 1
            message: Dict[str, Any] = {"id": counter, "method": method}
            if params:
                message["params"] = params
            await ws.send(json.dumps(message))
            while True:
                data = json.loads(await ws.recv())
                if data.get("id") == counter:
                    if "error" in data:
                        raise RuntimeError(data["error"])
                    return data.get("result", {})

        await cmd("Page.enable")
        await cmd("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": scale, "mobile": False,
        })
        await cmd("Page.reload", {"ignoreCache": True})

        # 标题在 map.on('complete') 末尾置为 READY；再等 3s 让 AMap 瓦片落盘。
        deadline = time.time() + 30.0
        while time.time() < deadline:
            try:
                result = await cmd("Runtime.evaluate", {
                    "expression": "document.title", "returnByValue": True,
                })
                if result.get("result", {}).get("value") == "READY":
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        await asyncio.sleep(3.0)

        shot = await cmd("Page.captureScreenshot", {
            "format": "png", "fromSurface": True, "captureBeyondViewport": False,
        })
        png_path.write_bytes(base64.b64decode(shot["data"]))


def render_png(html_path: Path, png_path: Path, *, chrome: str | None = None,
               width: int = 1600, height: int = 1000, scale: int = 2) -> Path:
    chrome = chrome or next((p for p in CHROME_CANDIDATES if Path(p).exists()), None)
    if not chrome:
        raise SystemExit("找不到 Chrome；可用 --chrome 指定")
    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(*args, directory=str(html_path.parent), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile_dir = html_path.parent / "_chrome_profile_figure"
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)
    cdp_port = _free_port()
    url = "http://127.0.0.1:%d/%s" % (port, html_path.name)
    cmd = [
        chrome, "--headless=new", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check",
        "--remote-debugging-port=%d" % cdp_port,
        "--user-data-dir=%s" % profile_dir,
        "--window-size=%d,%d" % (width, height),
        "--force-device-scale-factor=1",
        url,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = _wait_cdp_target(cdp_port, html_path.name, timeout=20.0)
        asyncio.run(_cdp_capture(ws_url, png_path, width=width, height=height, scale=scale))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        server.shutdown()
        server.server_close()
    if not png_path.exists():
        raise SystemExit("Chrome 没有生成截图：%s" % png_path)
    return png_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="高德真实路线单页图")
    parser.add_argument("--origin", default="句容")
    parser.add_argument("--dest", default="奥南")
    parser.add_argument("--load", type=float, default=12.0)
    parser.add_argument("--html", default=str(DEFAULT_HTML))
    parser.add_argument("--png", default=str(DEFAULT_PNG))
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--chrome", default=None)
    args = parser.parse_args(argv)

    route = load_route(args.origin, args.dest)
    candidates = load_candidates(route["od_key"])
    # 路线事实统一取 S0 综合策略的指标，几何仍用 route_od 的完整 polyline；
    # 避免 route_od 自带时长与 route_candidates 抓取时刻不同造成同图两套数。
    primary = next((c for c in candidates if c["strategy"] == 0), None)
    if primary is not None:
        route.update(distance_km=primary["distance_km"],
                     duration_min=primary["duration_min"],
                     toll_yuan=primary["toll_yuan"],
                     traffic_lights=primary["traffic_lights"])
    # 只读主账 passed 行；与 Q 模块同一 EF 口径。
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.rl.historical import build_profile
    master = pd.read_parquet(PROCESSED / "jd_segment_master.parquet")
    profile = build_profile(master)
    html = build_html(route, candidates, profile, load_t=args.load,
                      js_key=_read_key("amap_js_key.txt"),
                      security_code=_read_key("amap_js_security_code.txt"))
    html_path = Path(args.html)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")
    print("HTML", html_path, len(html), "chars")
    if args.render:
        png = render_png(html_path, Path(args.png), chrome=args.chrome)
        print("PNG", png, png.stat().st_size, "bytes")
    return 0


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>高德真实路线 · 句容 → 奥南</title>
<style>
:root{
  --ink:#F2F5F8;--ink2:#B7C2CE;--ink3:#7E8B99;--line:rgba(255,255,255,.12);
  --glass:rgba(9,15,24,.78);--cyan:#5FE3D2;--blue:#46B7FF;--coral:#FF7A59;--gold:#F6C85F;
  --font:'Noto Sans SC','Microsoft YaHei','PingFang SC',sans-serif;
  --mono:'JetBrains Mono','SFMono-Regular',Consolas,monospace;
}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#080D14;
  color:var(--ink);font-family:var(--font);-webkit-font-smoothing:antialiased}
#shell{position:relative;width:100vw;height:100vh;overflow:hidden;background:#080D14}
#map{position:absolute;inset:0;background:#080D14;filter:brightness(1.32) contrast(1.08) saturate(1.12)}
#shell:after{content:'';position:absolute;inset:0;pointer-events:none;z-index:4;
  background:
    radial-gradient(ellipse 720px 300px at 18% 14%,rgba(6,10,16,.62) 0%,rgba(6,10,16,.24) 55%,rgba(6,10,16,0) 76%),
    linear-gradient(90deg,rgba(6,10,16,.72) 0%,rgba(6,10,16,.32) 17%,rgba(6,10,16,0) 36%),
    linear-gradient(180deg,rgba(6,10,16,.62) 0%,rgba(6,10,16,0) 24%,rgba(6,10,16,0) 64%,rgba(6,10,16,.78) 100%);
  box-shadow:inset 0 0 0 1px rgba(255,255,255,.08),inset 0 0 220px rgba(0,0,0,.35)}
.hud{position:absolute;z-index:20;left:68px;top:54px;max-width:780px;pointer-events:none}
.eyebrow{display:flex;align-items:center;gap:12px;color:var(--cyan);font:11px var(--mono);
  letter-spacing:.20em;text-transform:uppercase;margin-bottom:20px}
.eyebrow:before{content:'';width:34px;height:1px;background:var(--cyan);box-shadow:0 0 12px var(--cyan)}
h1{margin:0;font-size:58px;line-height:1.06;font-weight:800;letter-spacing:-.035em;
  text-shadow:0 8px 40px rgba(0,0,0,.65)}
h1 em{font-style:normal;color:var(--cyan)}
.sub{margin:20px 0 0;color:var(--ink2);font-size:17px;line-height:1.75;max-width:680px;
  text-shadow:0 2px 18px rgba(0,0,0,.7)}
.sub b{color:var(--ink);font-weight:650}
.chips{display:flex;gap:10px;margin-top:26px;flex-wrap:wrap}
.chip{padding:8px 13px;border:1px solid rgba(95,227,210,.28);border-radius:999px;
  background:rgba(8,18,27,.62);color:#CFEDEA;font:11px var(--mono);letter-spacing:.04em;
  backdrop-filter:blur(14px)}
.chip b{color:var(--cyan);font-weight:600}
.card{position:absolute;z-index:20;border:1px solid var(--line);border-radius:18px;
  background:var(--glass);backdrop-filter:blur(22px) saturate(130%);
  box-shadow:0 30px 80px rgba(0,0,0,.45);padding:24px 26px}
.card .k{color:var(--ink3);font:10px var(--mono);letter-spacing:.13em;text-transform:uppercase}
.card .title{font-size:15px;font-weight:700;letter-spacing:.01em;margin-bottom:17px}
.facts{right:68px;top:54px;width:380px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:rgba(255,255,255,.08);
  border-radius:12px;overflow:hidden;border:1px solid rgba(255,255,255,.08)}
.cell{background:rgba(8,14,22,.86);padding:15px 16px 14px}
.cell .v{font:600 27px/1.1 var(--mono);letter-spacing:-.04em;color:var(--ink);margin-top:7px}
.cell .v small{font:500 12px var(--mono);color:var(--ink3);letter-spacing:0;margin-left:4px}
.cell .n{font:10px var(--mono);color:var(--ink3);margin-top:5px}
.cell.hi .v{color:var(--cyan)}
.carbon{left:68px;bottom:58px;width:476px}
.carbon .big{font:700 48px/1 var(--mono);letter-spacing:-.045em;color:var(--cyan);margin:2px 0 8px}
.carbon .big small{font:600 14px var(--mono);color:var(--ink2);letter-spacing:0;margin-left:6px}
.carbon .formula{color:var(--ink2);font:11px/1.8 var(--mono);margin-top:10px}
.carbon .formula b{color:var(--ink);font-weight:600}
.carbon .note{color:var(--ink3);font-size:11px;line-height:1.65;margin-top:14px;
  padding-top:14px;border-top:1px solid rgba(255,255,255,.08)}
.strategies{right:68px;bottom:54px;width:650px;padding:17px 20px 15px}
.shead{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:11px}
.shead .title{margin:0}
.shead .hint{color:var(--ink3);font:9px var(--mono);letter-spacing:.05em}
.sline{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:rgba(255,255,255,.09);
  border:1px solid rgba(255,255,255,.09);border-radius:10px;overflow:hidden}
.sitem{background:rgba(8,14,22,.88);padding:10px 11px 9px}
.sitem .sn{display:flex;align-items:center;gap:6px;color:var(--ink2);font:10px var(--mono)}
.sitem .sn b{color:var(--blue);font-weight:500}
.sitem .sv{color:var(--ink);font:600 14px var(--mono);letter-spacing:-.04em;margin-top:7px}
.sitem .sd{color:var(--ink3);font:9px var(--mono);margin-top:4px}
.sitem.pick{box-shadow:inset 0 2px 0 var(--cyan)}
.sitem.pick .sn b,.sitem.pick .sv{color:var(--cyan)}
.slegend{display:flex;gap:18px;margin-top:11px;color:var(--ink3);font:9px var(--mono)}
.slegend i{display:inline-block;width:16px;height:2px;border-radius:2px;vertical-align:middle;margin-right:6px}
.foot{position:absolute;z-index:20;left:68px;right:68px;bottom:18px;display:flex;
  justify-content:space-between;color:rgba(190,202,214,.55);font:9px var(--mono);
  letter-spacing:.04em;pointer-events:none}
.pin{position:relative;width:1px;height:1px}
.pin .dot{position:absolute;width:22px;height:22px;border-radius:50%;transform:translate(-50%,-50%);
  border:4px solid rgba(5,12,18,.95);box-shadow:0 0 0 8px rgba(95,227,210,.16),0 0 30px rgba(95,227,210,.7)}
.pin.dest .dot{box-shadow:0 0 0 8px rgba(255,122,89,.14),0 0 30px rgba(255,122,89,.55)}
.pin .tag{position:absolute;transform:translate(-50%,-46px);white-space:nowrap;
  padding:7px 11px;border-radius:8px;background:rgba(6,12,19,.9);border:1px solid rgba(255,255,255,.16);
  color:#E9F8F6;font:600 12px var(--font);box-shadow:0 8px 24px rgba(0,0,0,.35)}
.pin.dest .tag{transform:translate(-50%,24px)}
.km{width:30px;height:30px;border-radius:50%;transform:translate(-50%,-50%);
  display:flex;align-items:center;justify-content:center;background:rgba(7,14,22,.88);
  border:1px solid rgba(95,227,210,.42);color:#BFEFEA;font:9px var(--mono);
  box-shadow:0 4px 16px rgba(0,0,0,.35)}
</style>
</head>
<body>
<div id="shell">
  <div id="map"></div>
  <div class="hud">
    <div class="eyebrow">AMAP REAL ROUTE · GCJ-02 · CACHED POLYLINE</div>
    <h1>镇江句容 <em>→</em> 南京奥南</h1>
    <p class="sub">高德缓存实算折线，<b><span id="f-points">—</span> 个有效坐标点</b>逐点绘制；起终点与行政区底图均来自高德。<br>
    这不是示意图，是把真实路线几何、四策略里程/时长与历史碳画像放在同一张图上核对。</p>
    <div class="chips">
      <span class="chip">折线 <b><span id="f-points-chip">—</span> pts</b></span>
      <span class="chip">坐标系 <b>GCJ-02</b></span>
      <span class="chip">数据 <b>2026-11-06</b></span>
      <span class="chip">不含实时路况</span>
    </div>
  </div>

  <div class="card facts">
    <div class="k">ROUTE FACTS</div>
    <div class="title">路线事实</div>
    <div class="grid">
      <div class="cell hi"><div class="k">里程</div><div class="v" id="f-dist">—</div><div class="n">高德缓存距离</div></div>
      <div class="cell"><div class="k">时长</div><div class="v" id="f-dur">—</div><div class="n">高德缓存时长</div></div>
      <div class="cell"><div class="k">均速</div><div class="v" id="f-speed">—</div><div class="n">距离 ÷ 时长</div></div>
      <div class="cell"><div class="k">红绿灯</div><div class="v" id="f-lights">—</div><div class="n">高德路径属性</div></div>
    </div>
  </div>

  <div class="card carbon">
    <div class="k">CARBON CHECK · TTW</div>
    <div class="title">这张图的碳口径</div>
    <div class="big" id="f-carbon">—<small>kg CO₂</small></div>
    <div class="formula">
      <b>EF</b> <span id="f-ef">—</span> g/km &nbsp;·&nbsp; <b>档位</b> <span id="f-cell">—</span><br>
      <b>载重</b> <span id="f-load">—</span> t &nbsp;·&nbsp; <b>样本</b> <span id="f-n">—</span> 趟
    </div>
    <div class="note">碳值 = 高德里程 × 历史 5 档 × 4 载重带 EF；主账 E0 仍按法定轨，本图只做口径核对，不改 E0、不进 28 项闸门。</div>
  </div>

  <div class="card strategies">
    <div class="shead">
      <div class="title">四策略候选</div>
      <div class="hint">SAME OD · DIFFERENT METRICS</div>
    </div>
    <div class="sline" id="f-strategies"></div>
    <div class="slegend"><span><i style="background:var(--cyan)"></i>本图绘制 S0 综合路线</span><span><i style="background:var(--coral)"></i>终点 · 奥南</span><span>里程 km / 时长 min</span></div>
  </div>

  <div class="foot">
    <span>DATA · route_od.parquet / route_candidates.parquet / jd_segment_master.parquet</span>
    <span>SEG-MASTER-v1 · 仅演示，不写 data/processed</span>
  </div>
</div>

<script>
window._AMapSecurityConfig = {securityJsCode: "__SECURITY_CODE__"};
</script>
<script src="https://webapi.amap.com/maps?v=2.0&key=__JS_KEY__"></script>
<script>
const DATA = __PAYLOAD__;
const map = new AMap.Map('map', {
  zoom: 11,
  center: [119.02, 32.02],
  mapStyle: 'amap://styles/dark',
  viewMode: '2D',
  features: ['bg','road','building','point'],
  showLabel: true
});
function fmt(n, d) { return Number(n).toLocaleString('zh-CN', {minimumFractionDigits:d, maximumFractionDigits:d}); }
document.getElementById('f-dist').innerHTML = fmt(DATA.distance_km, 3) + '<small>km</small>';
document.getElementById('f-dur').innerHTML = fmt(DATA.duration_min, 1) + '<small>min</small>';
document.getElementById('f-speed').innerHTML = fmt(DATA.avg_speed_kmh, 1) + '<small>km/h</small>';
document.getElementById('f-lights').innerHTML = DATA.traffic_lights + '<small>个</small>';
document.getElementById('f-carbon').innerHTML = fmt(DATA.carbon_kg, 2) + '<small>kg CO₂</small>';
document.getElementById('f-ef').textContent = fmt(DATA.ef_g_km, 2);
document.getElementById('f-cell').textContent = DATA.ef_speed_bin + ' / ' + DATA.ef_load_bin;
document.getElementById('f-load').textContent = fmt(DATA.load_t, 1);
document.getElementById('f-n').textContent = DATA.ef_n_trips.toLocaleString('zh-CN');
document.getElementById('f-points').textContent = DATA.n_points.toLocaleString('zh-CN');
document.getElementById('f-points-chip').textContent = DATA.n_points.toLocaleString('zh-CN');
const strip = document.getElementById('f-strategies');
DATA.candidates.forEach(c => {
  const item = document.createElement('div');
  item.className = 'sitem' + (c.strategy === 0 ? ' pick' : '');
  item.innerHTML = '<div class="sn"><b>S' + c.strategy + '</b>' + c.name + '</div>' +
    '<div class="sv">' + fmt(c.distance_km, 1) + ' <span style="color:var(--ink3);font-size:10px">km</span></div>' +
    '<div class="sd">' + fmt(c.duration_min, 1) + ' min · ' + fmt(c.toll_yuan, 0) + ' 元</div>';
  strip.appendChild(item);
});
map.on('complete', () => {
  const glow = new AMap.Polyline({path: DATA.points, strokeColor: '#46B7FF', strokeWeight: 20,
    strokeOpacity: 0.14, lineJoin: 'round', lineCap: 'round', zIndex: 40});
  const line = new AMap.Polyline({path: DATA.points, strokeColor: '#5FE3D2', strokeWeight: 7,
    strokeOpacity: 1, lineJoin: 'round', lineCap: 'round', zIndex: 50});
  const halo = new AMap.Polyline({path: DATA.points, strokeColor: '#E9FFFB', strokeWeight: 2,
    strokeOpacity: 0.85, lineJoin: 'round', lineCap: 'round', zIndex: 55});
  map.add([glow, line, halo]);
  new AMap.Marker({position: DATA.origin, offset: new AMap.Pixel(0, 0), zIndex: 100,
    content: '<div class="pin origin"><div class="dot" style="background:#5FE3D2"></div><div class="tag">起点 · ' + DATA.origin_label + '</div></div>'
  }).setMap(map);
  new AMap.Marker({position: DATA.dest, offset: new AMap.Pixel(0, 0), zIndex: 100,
    content: '<div class="pin dest"><div class="dot" style="background:#FF7A59"></div><div class="tag">终点 · ' + DATA.dest_label + '</div></div>'
  }).setMap(map);
  DATA.km_markers.forEach(m => {
    new AMap.Marker({position: m.pos, offset: new AMap.Pixel(0, 0), zIndex: 90,
      content: '<div class="km">' + m.km + '</div>'}).setMap(map);
  });
  map.setFitView([line], false, [160, 160, 160, 160], 15);
  document.title = 'READY';
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    raise SystemExit(main())
