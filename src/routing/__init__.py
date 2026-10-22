# -*- coding: utf-8 -*-
"""src.routing · OSM 基线路由包。

三块能力，边界清楚：

* :mod:`src.routing.coord` —— 坐标基准转换（GCJ-02 ↔ WGS-84）。
  **只做坐标**，不碰网络。
* :mod:`src.routing.osrm_client` —— 路线获取（Provider 接口 + OSRM 实现）。
  **只做取路**，不碰碳账。
* :mod:`src.routing.road_class` —— 道路等级 proxy 分类。
  **只做解释**，不参与碳排计算（见 docs/35 §9 第 7 条）。
* :mod:`src.routing.carbon_baseline` —— 固定因子碳排基线。
  **只做碳账**，factor_mode=fixed_50。

纪律（docs/35 §1 / §9）：
- 不读、不写、不提交任何高德 key；本包不 import ``config.amap_*``；
- 公共 OSRM 只用于 spike，**不得写进生产循环**（正式版换本地 GraphHopper）；
- OSRM 无实时路况 → traffic_mode 恒为 ``freeflow``，页面/文档必须写"非实时"；
- OSRM 无过路费金额 → toll_yuan 必须为空，不得编造。
"""

from __future__ import annotations

__all__ = ["coord", "osrm_client", "road_class", "carbon_baseline"]
