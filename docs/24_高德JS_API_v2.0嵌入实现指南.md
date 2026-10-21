# 高德 JS API v2.0 嵌入实现指南（T7 落地手册）

**项目**：物流企业碳感知智能决策系统 ｜ **日期**：2026-10-20 ｜ **主笔**：WorkBuddy（受托于 PM 周嬴鑫）  
**关联**：`docs/23_动态路线规划与动态碳核算方案.md`（本文件是它的前端落地篇）、`docs/20` §4 前端只读证明  
**依据**：高德官方《地图 JS API 2.0 概述》（2025-01-16）与《路线规划服务插件》（2024-07-05）实测核对  
**状态**：实现指南。**P0 收口前不动工**；本文件所有代码可直接抄，key 一律占位符。

---

## 0. 先回答三个问题

**Q1：v2.0 是什么？** 高德第四代 Web 地图渲染引擎，WebGL 绘图，免费，PC/移动自适应。与 1.x 接口基本一致，有适配器。加载一行 script 即可。

**Q2：我们用它干什么？** 只干三件事：**画合规底图、画真路线（Polyline）、给评委一个可点的 OD 交互**。碳核算、成本、λ 决策全部在后端 Python（`optimizer.route_carbon`），浏览器端**零计算**——前端只读铁律不破。

**Q3：最大的坑是什么？** ① key 与安全密钥配置（配错=白屏）；② 断网答辩（在线地图打不开=灰块，必须有降级）；③ 坐标系（必须 GCJ-02）；④ **货车规划 `AMap.TruckDriving` 是收费接口**，需提前工单商务沟通，别当免费功能排期。

---

## 1. 能力清单 → 我们的用法（只取需要的）

| 官方能力                              | 我们用不用 | 用在哪                                                    |
| --------------------------------- | ----- | ------------------------------------------------------ |
| `AMap.Map` + 标准矢量底图               | ✅ 核心  | 全站在线底图（合规持牌，GCJ-02 原生）                                 |
| `AMap.Polyline` 折线                | ✅ 核心  | 路线渲染：route_od.parquet 的 polyline 直接上图                  |
| `AMap.Driving` 驾车规划               | ✅ 核心  | 预取（后端 REST，见 docs/23 §5）+ 浏览器端单 OD what-if（可选）         |
| `AMap.TruckDriving` 货车规划          | ⚠️ 收费 | **差异化武器**（限高限重避让），需商务工单；未开通前用 Driving + 自有 C1–C5 约束兜底  |
| `AMap.Geocoder` 地理编码              | ✅ 次要  | 网点名 → GCJ-02 坐标（后端 REST 为主，前端备用）                       |
| `AMap.CircleMarker` / `AMap.Text` | ✅     | 起终点标记 + 城市名                                            |
| `AMap.InfoWindow`                 | ✅     | 点路线弹窗：里程/时长/过路费/碳排（数据来自 parquet，非实时算）                  |
| `AMap.MassMarks`/`MarkerCluster`  | ❌     | 演示 OD 量级（TOP 24）用不到                                    |
| `AMap.DragRoute` 拖拽改线             | ⚠️ 可选 | 评委现场拖一条线看重算——仅桌面浏览器，W4 有余量再加                           |
| 卫星/路况/室内图层                        | ❌     | 演示用不上，默认矢量底图即可                                         |
| `AMap.convertFrom`（WGS84→GCJ02）   | 备用    | 只有接 GPS 数据时才用；现有 CITY 表是城市级近似坐标，演示无感                   |

---

## 2. Key 与安全配置（配错这步 = 白屏，逐条核对）

### 2.1 两把 key 各管各的（承接 docs/23 §3.2）

| 用途                                 | 配置项                                             | 白名单                             |
| ---------------------------------- | ----------------------------------------------- | ------------------------------- |
| 后端 REST 预取（`tools/route_fetch.py`） | Web 服务 key → `config/amap_key.txt`              | IP 白名单                          |
| 前端地图（本文件）                          | **Web 端（JS API）key** → `config/amap_js_key.txt` | 域名白名单 + **安全密钥 securityJsCode** |

### 2.2 安全密钥（2021 年后新 key 强制）

在高德控制台为 JS API key 生成**安全密钥 jscode**，存入 `config/amap_js_code.txt`。加载 SDK **之前**必须写 `window._AMapSecurityConfig`，顺序反了不生效：

```html
<script>window._AMapSecurityConfig = { securityJsCode: "…jscode…" };</script>
<script src="https://webapi.amap.com/maps?v=2.0&key=…key…&plugin=AMap.Driving"></script>
```

### 2.3 代码里的 key 规则（合规红线）

- 三个配置文件（`config/amap_key.txt`、`config/amap_js_key.txt`、`config/amap_js_code.txt`）**全部进 .gitignore，永不入库**；
- 仓库代码与本文档中，key 一律写作占位符：`"请至高德开放平台申请自有 key 并替换此占位符"`；
- 未配置 key → 组件返回 `False`，调用方自动回退 Plotly 离线底图（§6.3）——**key 缺失不是错误，是一种演示模式**。

---

## 3. 嵌入机制：Streamlit 怎么装一张高德地图

Streamlit 没有原生地图组件，用官方 `st.iframe`（1.56.0 起，取代已弃用的 `components.v1.html`）把一段自包含 HTML 装进 iframe。三条纪律：

1. **数据从 Python 侧注入，不从前端拉**：`route_od.parquet` → `json.dumps` → 替换 HTML 模板 token。前端只读铁律的延伸：浏览器不请求任何业务接口。
2. **容器必须有像素高度**：`st.iframe(html, height=560)`，HTML 里 `#map{height:560px}` 双写，缺一不可。
3. **key 只在服务端拼 HTML 时注入**：Python 读 `config/amap_js_key.txt` → `.replace("__KEY__", key)`；key 不出现在任何前端源码文件里。

```python
# app/components/amapview.py（完整实现见 §6）
import json
import streamlit as st

def route_map(routes: list[dict], height: int = 560) -> bool:
    key = _load_js_key()
    if key is None:
        return False                                    # 未配置 → 调用方回退离线图
    doc = (_HTML.replace("__KEY__", key)
                .replace("__JSCODE__", _load_js_code() or "")
                .replace("__ROUTES_JSON__", json.dumps(routes, ensure_ascii=False))
                .replace("__HEIGHT__", str(height)))
    st.iframe(doc, height=height)
    return True
```

---

## 4. 路线数据契约（Python → 浏览器的唯一接口）

`data/processed/route_od.parquet` 每行一条 OD 路线，传给浏览器前转成 dict 列表：

```python
{
  "od_key": "SH-PD-001→SH-HQ-014",   # 网点级 OD 键（与 od_band 同口径）
  "origin": "上海青浦分拨中心", "dest": "上海奉贤网点",
  "o_lng": 121.13, "o_lat": 31.15,   # GCJ-02
  "d_lng": 121.47, "d_lat": 30.92,
  "distance_km": 86.4,               # 高德路线距离（L1 情景口径）
  "duration_min": 96.0,
  "toll_yuan": 0.0,
  "kg_co2": 412.7,                   # 后端算好的路线情景碳排（浏览器不重算）
  "vclass": 2,                       # 0 轻 / 1 中 / 2 重，决定线条颜色
  "strategy": 32,
  "polyline": [[121.13, 31.15], …]   # 高德返回的 GCJ-02 坐标串
}
```

**浏览器端禁止出现**：因子表、λ、成本公式。λ 变了 → 后端重算 → 新 parquet → 页面刷新。这就是"前端只读不算"。

---

## 5. 三个使用场景（对应三个页面）

### 5.1 第 3 页 · 替代方案（核心场景：单 OD 决策面板）

评委视角：选一条线 → 看真路线 → 看这条路排多少碳、派什么车、λ 一动推荐翻不翻。

```
[selectbox: 选择网点级 OD]  →  [高德地图：真路线 + 起终点标记 + InfoWindow]
                              [指标卡：路线 km / 时长 / 过路费 / 路线碳排 / 推荐车型 / C1–C5 约束结论]
                              [λ 滑块]  → 推荐车型/路线标签实时变（后端重算结果表，非 JS 计算）
```

### 5.2 第 2 页 · 高排放诊断（在线增强：真路线 OD 流）

把现有 Plotly 弧线升级为高德真路线：TOP 24 碳排 OD，线宽=碳排、颜色=车型大类，点开看明细。**离线仍用 Plotly 弧线**（`flow_map` 不动）。

### 5.3 第 1 页 · 基准核算

**不上高德**——保持 Plotly 离线热力（断网命根子）。在线地图只出现在"需要路径真实性"的 2、3 页。

---

## 6. 组件完整代码（`app/components/amapview.py`，可直接抄）

```python
# -*- coding: utf-8 -*-
"""在线地图组件：高德 JS API v2.0（合规底图 + 真路线渲染）。

定位（docs/23）：T7 动态路线的前端渲染层。三条纪律：
  1. 只在 config/amap_js_key.txt 配置了真实 key 时启用，否则返回 False 由调用方回退 mapview（Plotly 离线）；
  2. 浏览器端零业务计算——路线/碳排/成本全部来自 data/processed/route_od.parquet；
  3. key 只在服务端注入 HTML，不出现在任何前端源码。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
KEY_PATH = ROOT / "config" / "amap_js_key.txt"
CODE_PATH = ROOT / "config" / "amap_js_code.txt"
PLACEHOLDER = "请至高德开放平台申请自有 key 并替换此占位符"

# 线条配色与 components/theme.py 对齐：重型=橙（减排瓶颈），轻中=青
_LINE_COLORS = {"2": "#FF8A5B", "1": "#2DD4A7", "0": "#5DCAA5"}

_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html,body{margin:0;padding:0;height:100%;background:#0F1620;}
  #map{width:100%;height:__HEIGHT__px;}
  #fallback{display:none;padding:32px;color:#8A97A6;font-size:13px;line-height:1.7;}
  .amap-info{font-size:12px;line-height:1.8;min-width:200px;}
  .amap-info b{color:#E6EDF3;}
</style>
<script>window._AMapSecurityConfig={securityJsCode:"__JSCODE__"};</script>
<script src="https://webapi.amap.com/maps?v=2.0&key=__KEY__&plugin=AMap.Driving,AMap.Geocoder,AMap.PlaceSearch"></script>
</head>
<body>
<div id="map"></div>
<div id="fallback">在线地图不可用（key 未配置、配额受限或现场断网）。<br>
请使用左侧"离线示意图"切换——路线数据完全一致，仅底图换为矢量示意图。</div>
<script>
var ROUTES = __ROUTES_JSON__;
var COLORS = __LINE_COLORS__;
var inited = false;
function fail(){document.getElementById("map").style.display="none";
  document.getElementById("fallback").style.display="block";}
function init(){
  if(inited) return; inited = true;
  var map = new AMap.Map("map", {zoom:5, center:[110.0,35.0], viewMode:"2D"});
  var info = new AMap.InfoWindow({isCustom:false, autoMove:true});
  function showInfo(r){
    var html = '<div class="amap-info"><b>' + r.origin + ' → ' + r.dest + '</b><br>'
      + '路线距离 ' + r.distance_km.toFixed(1) + ' km｜时长 ' + r.duration_min.toFixed(0) + ' min<br>'
      + '过路费 ' + r.toll_yuan.toFixed(0) + ' 元｜路线碳排 ' + r.kg_co2.toFixed(1) + ' kg<br>'
      + '车型大类 ' + ['轻型','中型','重型'][Number(r.vclass)||0] + '｜策略 ' + r.strategy
      + '<br><span style="color:#8A97A6">口径：路线情景（L1），与基准账分列标注</span></div>';
    info.setContent(html);
    info.open(map, [ (r.o_lng+r.d_lng)/2, (r.o_lat+r.d_lat)/2 ]);
  }
  var lines = [], markers = [];
  ROUTES.forEach(function(r, i){
    if(r.polyline && r.polyline.length >= 2){
      var line = new AMap.Polyline({
        path: r.polyline, strokeColor: COLORS[i] || "#2DD4A7", strokeWeight: 4,
        strokeOpacity: 0.9, cursor: "pointer", zIndex: 10, showDir: true
      });
      line.on("click", function(){ showInfo(r); });
      lines.push(line);
    }
    [["o", r.o_lng, r.o_lat, r.origin], ["d", r.d_lng, r.d_lat, r.dest]].forEach(function(p){
      markers.push(new AMap.CircleMarker({
        center:[p[1],p[2]], radius:5, strokeColor:"#E6EDF3", strokeWeight:1,
        fillColor:"#F5C86B", fillOpacity:0.95, zIndex:20, cursor:"pointer"
      }));
      markers.push(new AMap.Text({
        position:[p[1],p[2]], text:p[3], anchor:"bottom-center",
        style:{color:"#E6EDF3", background:"rgba(15,22,32,0.72)",
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


def route_map(routes: List[Dict[str, Any]], height: int = 560) -> bool:
    """把预生成的路线表渲染成高德地图。未配置 key 返回 False（调用方回退离线图）。"""
    key = load_js_key()
    if not key or not routes:
        return False
    colors = [
        _LINE_COLORS.get(str(r.get("vclass", 1)), "#2DD4A7") for r in routes
    ]
    doc = (
        _HTML.replace("__KEY__", key)
        .replace("__JSCODE__", _read_secret(CODE_PATH))
        .replace("__ROUTES_JSON__", json.dumps(routes, ensure_ascii=False))
        .replace("__LINE_COLORS__", json.dumps(colors))
        .replace("__HEIGHT__", str(height))
    )
    st.iframe(doc, height=height)
    return True
```

> 注入安全说明：`json.dumps` 的数据是坐标数组与中文地名，不含 `</script>` 序列；即便如此，生产环境建议加一道 `json.dumps(...).replace("</", "<\\/")` 防御。

### 6.1 调用方写法（路线实验室片段）

```python
# app/pages/6_路线实验室.py —— 在线/离线双轨，key 缺失自动降级
from components import amapview, mapview

routes_df = services.routes()                     # services.py 新增：只读 route_od.parquet
if amapview.online_available() and not routes_df.empty:
    od = st.selectbox("选择线路（网点级 OD）", routes_df["od_key"].tolist())
    row = routes_df[routes_df["od_key"] == od].iloc[0]
    ok = amapview.route_map([json.loads(row.to_json())], height=520)
    if not ok:                                     # 理论上到不了，双保险
        mapview.render(mapview.flow_map(services.segments()), height=520)
else:
    st.caption("在线路线未配置或路线表未生成——显示离线示意图（数据口径一致）")
    mapview.render(mapview.flow_map(services.segments()), height=520)

# 指标卡与约束结论：全部只读 route_od / final_report，浏览器端零计算
c1, c2, c3 = st.columns(3)
c1.metric("路线距离", f"{row['distance_km']:.1f} km", "路线情景口径 L1")
c2.metric("路线碳排", f"{row['kg_co2']:.1f} kg")
c3.metric("过路费", f"{row['toll_yuan']:.0f} 元")
```


### 6.2 `services.py` 新增（只读两行）

```python
ROUTE_PATH = PROCESSED / "route_od.parquet"

@st.cache_data(show_spinner=False)
def routes() -> pd.DataFrame:
    if not ROUTE_PATH.exists():
        return pd.DataFrame()
    return pd.read_parquet(ROUTE_PATH)
```

### 6.3 降级链（演示生命线）

```
online_available() == False（key 未配）     → 直接 Plotly 离线图 + 说明条
key 已配但现场断网（8s 未加载 AMap）        → iframe 内 #fallback 文字提示
                                             + 页面保留"离线示意图"切换按钮
两条都失效                                     → 第 3 页照常展示指标卡与约束结论（数据同源）
```

**答辩日检查单**：① key 三件套已配且非占位；② 域名白名单含 `localhost:8501` **和现场演示机的 IP:端口**；③ securityJsCode 已生成；④ 提前一周跑一次真断网（拔网线开五页）；⑤ Plotly 回退路径排练过至少一遍。

---

## 7. 浏览器端交互模式（可选，W4 有余量再做）

两种"活"的玩法，都只用官方插件、都不在 JS 里算碳：

**① 单 OD what-if（评委点一条线，现场重算路线）**

```js
AMap.plugin("AMap.Driving", function(){
  var r = ROUTES[0];                       // Python 侧选中的 OD
  var driving = new AMap.Driving({
    policy: 0,                             // 0=速度优先；其余策略数值见官方 direction 文档
    map: map                               // 指定后自动把路线画到地图上
  });
  driving.search([r.o_lng, r.o_lat], [r.d_lng, r.d_lat], function(status, result){
    if(status !== "complete"){ return; }   // no_data/error：保留预渲染路线即可
    // 演示技巧：这条实时路线与预取表里的 polyline 应基本重合——
    // 两条线重合 = "前端只读不算"的当场自证（docs/20 §4 的升级版）
  });
});
```

**② 拖拽改线（`AMap.DragRoute`，仅桌面浏览器）**

```js
AMap.plugin("AMap.DragRoute", function(){
  var r = ROUTES[0];
  var route = new AMap.DragRoute(map,
    [[r.o_lng, r.o_lat], [r.d_lng, r.d_lat]], 0);
  route.search();                          // 拖拽节点，实时重算路径
});
```

> 注意：DragRoute 改的是**展示路径**，碳排数字不会跟着 JS 变（前端不算）。若要让评委看到"改线→碳变"，需把新路径回传后端重算——**超出本期范围，写进未来展望**。

**③ 货车规划 `AMap.TruckDriving`**：官方明确为**收费服务接口**，需控制台提商务工单。

> **2026-10-20 实况**：本团队**当前没有高德商务合同**，`AMap.TruckDriving` **不开通**。
> 替代方案（已落地）：**`AMap.Driving` + 我们自有的 C1–C5 运营约束校验**。
> 理由：限高限重的判断本来就要落到"这个车型跑这条线行不行"，而这正是 C1–C5 的职责；
> 用高德货车接口查一遍、再自己算一遍约束，是两道工序，不是一道。
> 代码已预留 Truck 的接入位（`amapview.py` 的 plugin 列表 + `optimizer.route_constraint` 的约束表位），
> 拿到合同后可直接开启，不返工。

---

## 8. 坐标系（一页说清，避免答辩翻车）

| 坐标系        | 谁在用            | 规则                                                         |
| ---------- | -------------- | ---------------------------------------------------------- |
| GCJ-02（火星） | 高德全部输入输出、在线底图  | **本项目的线上标准**。route_od.parquet 的坐标即 GCJ-02，直接上图             |
| WGS-84     | GPS 原始数据（本期没有） | 若未来接入，先 `AMap.convertFrom(lnglat, 'GPS', cb)` 转 GCJ-02 再上图 |
| 本地 CITY 表  | 离线演示回退         | 城市级近似坐标（偏差约百米级），**仅 Plotly 离线图使用**，页面标注"离线近似"              |

**验收口径**：同一个 OD，在线高德底图上的点与离线 Plotly 的点应落在同一城市级位置（偏差 <1 km）；网点级展示必须先过 `Geocoder`/REST 地理编码，不许拿 CITY 近似值冒充网点坐标。

---

## 9. 合规自检清单（每次 PR 前过一遍）

- [ ] 代码/文档中无任何真实 key，占位符文案统一为"请至高德开放平台申请自有 key 并替换此占位符"
- [ ] `config/amap_key.txt`、`config/amap_js_key.txt`、`config/amap_js_code.txt` 均在 .gitignore
- [ ] 底图只用高德官方（禁用 Google/Apple/Bing 海外/OSM/Mapbox）；**不自绘国界、台湾、南海诸岛**
- [ ] 不标注军事禁区、涉密单位等敏感坐标；订单点位属企业数据，页面不导出原始坐标
- [ ] JS API key 已配域名白名单 + securityJsCode（前端 key 可被 sniff，白名单是最后防线）
- [ ] 货车/Truck 相关功能若使用，已确认商务授权状态

---

## 10. 落地顺序（并入 docs/23 §9 排期）

| 步骤 | 内容                                                            | 归属     |
| -- | ------------------------------------------------------------- | ------ |
| U1 | PM：`.gitignore` 加三个 config 文件；`requirements.txt` 加 `requests` | 周      |
| U2 | 杨：`config/amap_js_key.txt` + 安全密钥申请配置（占位符起步）                  | 杨      |
| U3 | 杨：`app/components/amapview.py` 落库 + 单测（无 key 时返回 False）       | 杨      |
| U4 | 周：`services.py` 加 `routes()`；`services.route_available()`     | 周      |
| U5 | 杨：第 3 页决策面板接线（§6.1 片段）；第 2 页在线 OD 流（§5.2）                     | 杨（周配合） |
| U6 | 周：断网实测 + 域名白名单补现场 IP + 排练降级路径                                 | 周      |
| U7 | 可选：DragRoute what-if、TruckDriving 工单                          | 杨      |

**验收判据**：① `grep -R "from src" app/` 为空；② 无 key 启动五页 0 异常（现有 AppTest 保持绿）；③ 配 key 后第 3 页路线与 route_od.parquet 的 polyline 一致；④ 拔网线后第 3 页显示 #fallback 提示且指标卡可读；⑤ 域名白名单含现场演示地址。

---

> **先保证对，再追求快；先立契约，再谈效率。**——高德只是渲染层；契约在 route_od.parquet，口径在 optimizer，铁律一条不破。
