# -*- coding: utf-8 -*-
"""四维进化路线沙盘：Plotly、空态、会话结果与采纳交互测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _records():
    return [
        {"route_id": "1", "distance": 100.0, "duration": 100.0, "tolls": 30.0,
         "carbon": 90.0, "cost": 120.0, "is_exploration": False,
         "is_recommended": True, "exploration_strategy": "exploitation"},
        {"route_id": "evo_1", "distance": 95.0, "duration": 105.0, "tolls": 28.0,
         "carbon": 85.0, "cost": 118.0, "is_exploration": True,
         "is_recommended": False, "exploration_strategy": "nsga2_q_learning"},
    ]


def test_plotly_figures_group_and_highlight_routes():
    from components.route_sandbox import build_parallel_figure, build_scatter_figure
    frame = pd.DataFrame(_records())
    scatter = build_scatter_figure(frame, "碳排放", "综合成本")
    parallel = build_parallel_figure(frame)
    assert len(scatter.data) == 2
    # 视觉编码：探索路线 = 星形 + 青绿；推荐路线 = 大号 + 红。
    # 色值取自暗底电影感色板（docs/47 §2.2），断言跟着视觉设计走。
    assert scatter.data[0].marker.symbol == "star"
    assert scatter.data[0].marker.color == "#5FE3D2"
    assert scatter.data[1].marker.size == 19
    assert parallel.data[1].line.color == "#FF6B6B"
    assert len(parallel.data[0].dimensions) == 4


def test_empty_state_does_not_crash(monkeypatch):
    from components import route_sandbox
    monkeypatch.setattr(route_sandbox.services, "amap_key_available", lambda: False)

    def script():
        from components.route_sandbox import render
        render(100)

    at = AppTest.from_function(script, default_timeout=40)
    at.run()
    assert not at.exception
    assert any("请在左侧输入参数并生成方案" in item.value for item in at.markdown)


def test_generate_persists_results_and_accept_saves_memory(monkeypatch):
    from components import route_sandbox
    saved = []
    monkeypatch.setattr(route_sandbox.services, "amap_key_available", lambda: True)
    monkeypatch.setattr(route_sandbox.services, "amap_driving_routes", lambda *args: [{}])
    monkeypatch.setattr(route_sandbox.services, "evaluate_route_records", lambda *args: [{}])

    def fake_evolve(routes, **kwargs):
        kwargs["decision_log"].append("检测到多样性下降，执行上调变异率动作")
        return _records()

    monkeypatch.setattr(route_sandbox.services, "evolve_route_options", fake_evolve)
    monkeypatch.setattr(route_sandbox.services, "save_route_memory",
                        lambda route: saved.append(route) or route)
    monkeypatch.setattr(route_sandbox.services, "route_memory", lambda: pd.DataFrame())

    def script():
        from components.route_sandbox import render
        render(100)

    at = AppTest.from_function(script, default_timeout=40)
    at.run()
    submit = next(b for b in at.button if "生成四维碳感知调度方案" in b.label)
    submit.click().run()
    assert not at.exception
    assert len(at.session_state["route_evolution_records"]) == 2
    assert at.session_state["route_decision_log"]
    assert len(at.dataframe) >= 1

    accept = next(b for b in at.button if b.label == "✅ 采纳推荐路线")
    accept.click().run()
    assert not at.exception
    assert saved and saved[0]["route_id"] == "1"
