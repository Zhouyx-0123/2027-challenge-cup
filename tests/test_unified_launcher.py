"""Checks for the single main-system launch path, including the disclosure tab."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

from tools import launch


def test_declared_streamlit_minimum_is_checked(monkeypatch):
    original = launch.metadata.version
    monkeypatch.setattr(launch.metadata, "version", lambda name: "1.55.0" if name == "streamlit" else original(name))
    ok, detail = launch.check_deps()
    assert not ok and "streamlit>=1.56.0" in detail


def test_missing_browser_module_is_reported_before_starting(tmp_path, monkeypatch):
    import carbon_disclosure
    project = tmp_path / "project"
    shutil.copytree(launch.ROOT / "carbon_disclosure", project / "carbon_disclosure",
                    ignore=shutil.ignore_patterns("__pycache__"))
    adapter = project / "app/components/carbon_disclosure_panel.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_text("# adapter placeholder", encoding="utf-8")
    (project / "app/main_views.py").write_text(
        "def render_ledger():\n    carbon_disclosure_panel.render_carbon_disclosure_panel()\n", encoding="utf-8")
    (project / "carbon_disclosure/dist/downloads.js").unlink()
    monkeypatch.setattr(launch, "ROOT", project)
    monkeypatch.setattr(carbon_disclosure, "DIST", project / "carbon_disclosure/dist")
    ok, detail = launch.check_disclosure()
    assert not ok and "downloads.js" in detail


def test_complete_assets_with_old_frontend_are_rejected(tmp_path, monkeypatch):
    project = tmp_path / "project"
    shutil.copytree(launch.ROOT / "carbon_disclosure", project / "carbon_disclosure",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (project / "app/components").mkdir(parents=True)
    (project / "app/components/carbon_disclosure_panel.py").write_text("# placeholder", encoding="utf-8")
    (project / "app/main_views.py").write_text("def render_ledger():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(launch, "ROOT", project)
    ok, detail = launch.check_disclosure()
    assert not ok and "主前端仍是旧版" in detail


def test_leftover_old_panel_or_hidden_old_page_is_rejected(tmp_path, monkeypatch):
    project = tmp_path / "project"
    shutil.copytree(launch.ROOT / "carbon_disclosure", project / "carbon_disclosure",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (project / "app/components").mkdir(parents=True)
    (project / "app/components/carbon_disclosure_panel.py").write_text("# adapter", encoding="utf-8")
    hook = "def render_ledger():\n    carbon_disclosure_panel.render_carbon_disclosure_panel()\n"
    (project / "app/main_views.py").write_text(hook, encoding="utf-8")
    old_panel = project / "app/components/carbon_ledger_panel.py"
    old_panel.write_text("# obsolete", encoding="utf-8")
    monkeypatch.setattr(launch, "ROOT", project)
    ok, detail = launch.check_disclosure()
    assert not ok and "旧稿组件" in detail
    old_panel.unlink()
    (project / "app/main_views.py").write_text(hook + "\ndef _render_ledger_e0():\n    pass\n", encoding="utf-8")
    ok, detail = launch.check_disclosure()
    assert not ok and "旧碳台账代码" in detail


@pytest.mark.skipif(os.name != "nt", reason="Windows batch entry")
def test_root_only_upload_is_stopped_before_old_tools_can_run(tmp_path):
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "incomplete"
    project.mkdir()
    shutil.copyfile(root / "启动.bat", project / "启动.bat")
    env = dict(os.environ, VIRTUAL_ENV=sys.prefix, PYTHONIOENCODING="utf-8")
    result = subprocess.run(["cmd.exe", "/d", "/c", "call", str(project / "启动.bat"), "--check"],
                            cwd=tmp_path, env=env, capture_output=True, timeout=30)
    assert result.returncode == 1
    assert b"Incomplete project" in result.stdout


def test_dependency_failure_does_not_start_an_incomplete_app(monkeypatch):
    monkeypatch.setattr(launch, "ensure_dependencies", lambda: False)
    monkeypatch.setattr(launch, "start_streamlit", lambda *a, **kw: pytest.fail("Must not start with missing dependencies"))
    assert launch.main(["--no-recalc"]) == 1


def test_readonly_check_never_installs_or_starts(monkeypatch):
    monkeypatch.setattr(launch, "check_disclosure", lambda: (False, "missing disclosure assets"))
    monkeypatch.setattr(launch, "ensure_dependencies", lambda: pytest.fail("Check mode must not install"))
    monkeypatch.setattr(launch, "start_streamlit", lambda *a, **kw: pytest.fail("Check mode must not start"))
    assert launch.main(["--check"]) == 1


def test_main_launch_starts_only_the_existing_streamlit_app(monkeypatch):
    commands = []
    monkeypatch.setattr(launch, "_port_is_free", lambda port: True)
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd, **kw: commands.append(cmd) or 0)
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **kw: pytest.fail("Standalone carbon service must not be started"))
    monkeypatch.setattr(launch, "run_recalc", lambda **kw: pytest.fail("No recalculation was requested"))
    assert launch.main(["--no-recalc", "--open", "--no-browser", "--port", "8517"]) == 0
    assert len(commands) == 1
    assert commands[0][:4] == [sys.executable, "-m", "streamlit", "run"]
    assert commands[0][4] == str(launch.ROOT / "app/app.py")
    assert commands[0][commands[0].index("--server.headless") + 1] == "true"


def test_repeated_start_reuses_only_the_confirmed_project(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(launch, "_port_is_free", lambda port: False)
    monkeypatch.setattr(launch, "_same_project_is_running", lambda port: True)
    monkeypatch.setattr(launch.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(launch.subprocess, "call", lambda *a, **kw: pytest.fail("A second server must not start"))
    assert launch.start_streamlit(8501, open_browser=True) == 0
    assert opened == ["http://localhost:8501/ledger"]
    assert "本项目已经在运行" in capsys.readouterr().out


def test_unknown_port_owner_gets_a_free_port_without_being_stopped(monkeypatch, capsys):
    commands = []
    monkeypatch.setattr(launch, "_port_is_free", lambda port: port == 8503)
    monkeypatch.setattr(launch, "_same_project_is_running", lambda port: False)
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd, **kw: commands.append(cmd) or 0)
    assert launch.start_streamlit(8501) == 0
    assert commands[0][commands[0].index("--server.port") + 1] == "8503"
    assert "自动改用 8503" in capsys.readouterr().out


def test_no_free_port_returns_an_explanation(monkeypatch, capsys):
    monkeypatch.setattr(launch, "_port_is_free", lambda port: False)
    monkeypatch.setattr(launch, "_same_project_is_running", lambda port: False)
    monkeypatch.setattr(launch.subprocess, "call", lambda *a, **kw: pytest.fail("No server should start"))
    assert launch.start_streamlit(65535) == 1
    assert "端口均被占用" in capsys.readouterr().out


def test_other_project_never_passes_same_project_check(monkeypatch):
    monkeypatch.setattr(launch, "_port_owner_command", lambda port: 'python.exe -m streamlit run "C:/other/app.py" --server.port 8501')
    assert not launch._same_project_is_running(8501)


def test_ipv6_listener_is_not_mistaken_for_a_free_port():
    if not socket.has_ipv6:
        pytest.skip("IPv6 is not available")
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as server:
        try:
            server.bind(("::", 0))
        except OSError:
            pytest.skip("IPv6 is disabled on this host")
        server.listen(1)
        assert not launch._port_is_free(server.getsockname()[1])


def test_sensor_argument_keeps_existing_device_gateway(monkeypatch):
    children = []
    monkeypatch.setattr(launch.subprocess, "Popen", lambda cmd, **kw: children.append(cmd))
    monkeypatch.setattr(launch, "start_streamlit", lambda *a, **kw: 0)
    assert launch.main(["--no-recalc", "-Sensor"]) == 0
    assert children == [[sys.executable, str(launch.ROOT / "server.py")]]


@pytest.mark.skipif(os.name != "nt", reason="Windows batch entry")
def test_windows_main_entry_works_from_another_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, VIRTUAL_ENV=sys.prefix, PYTHONIOENCODING="utf-8")
    result = subprocess.run(["cmd.exe", "/d", "/c", "call", str(root / "启动.bat"), "--check"],
                            cwd=tmp_path, env=env, capture_output=True, timeout=60)
    text = result.stdout.decode("utf-8", errors="replace")
    assert result.returncode == 0, text + result.stderr.decode("utf-8", errors="replace")
    assert "主系统统一启动检查" in text and "披露碳账" in text
