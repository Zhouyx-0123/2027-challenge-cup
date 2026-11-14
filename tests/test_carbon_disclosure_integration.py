"""Verify the real Streamlit adapter, not a stub of the rendering API."""
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def test_disclosure_panel_renders_embedded_app_and_offline_download():
    at = AppTest.from_string('''
from components.carbon_disclosure_panel import render_carbon_disclosure_panel
render_carbon_disclosure_panel()
''').run(timeout=30)
    assert not at.exception
    assert any("披露碳账 v0.9" in item.value for item in at.caption)
    frames = at.get("iframe")
    assert len(frames) == 1
    assert "LEDGER_SAMPLE" in frames[0].proto.srcdoc
    assert "LEDGER_MANUAL_PDF" in frames[0].proto.srcdoc
    assert 'data-presentation="embedded"' in frames[0].proto.srcdoc
    assert '--ct-canvas:#07080A' in frames[0].proto.srcdoc
    assert "<script src=" not in frames[0].proto.srcdoc
    assert len(at.get("download_button")) == 1


def test_main_ledger_is_only_the_replacement_disclosure_page():
    at = AppTest.from_string('''
from demo_state import init_state
from main_views import render_ledger
init_state()
render_ledger()
''').run(timeout=60)
    assert not at.exception
    assert len(at.tabs) == 0
    assert len(at.get("iframe")) == 1
    assert len(at.number_input) == 0
    assert not any("碳账参数设置" in item.label for item in at.expander)
    source = (APP / "main_views.py").read_text(encoding="utf-8")
    assert "_render_ledger_e0" not in source
    assert "carbon_ledger_panel" not in source
    assert not (APP / "components/carbon_ledger_panel.py").exists()


def test_current_main_navigation_is_clickable():
    at = AppTest.from_file(str(APP / "app.py")).run(timeout=60)
    assert not at.exception
    links = at.get("page_link")
    assert [item.proto.label for item in links[:6]] == ["首页", "数据链总览", "发票感知", "硬件监控", "路线沙盘", "碳台账"]
    assert all(not item.proto.disabled for item in links[:6])
