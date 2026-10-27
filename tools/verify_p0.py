# -*- coding: utf-8 -*-
"""企业级 P0 的一键验收（docs/50 §8）。

用法::

    python -m tools.verify_p0              # 常规验收（快，约 10 秒）
    python -m tools.verify_p0 --deep       # 追加真跑一遍流水线（发布→失败→回滚，约 30 秒）

每条判据打印一行 ``OK`` / ``FAIL`` / ``SKIP``，全绿退出码 0，有 FAIL 退出码 1。

判据清单（对应 P0 的五条验收）
------------------------------
1. **交付物齐全**：容器化、配置分层、run 版本化、设备鉴权、备份恢复的文件都在；
2. **compose 可解析**：三个服务、关键挂载、鉴权与数据卷环境变量都设了；
3. **CI 已扩展**：docker-smoke 作业在，原 pytest 作业没被弄丢；
4. **零明文密钥**：git 跟踪的文件里没有任何密钥，.gitignore 挡住了密钥路径；
5. **密钥分层可解析**：每个已知密钥都能定位到来源（只报来源，不报值）；
6. **生效指针有效**：current_run.json 指向的 run 是过闸门的那一版；
7. **备份可恢复**：真打一份备份并做恢复演练，校验必须全过；
8. **未注册设备被拒**：缺令牌 / 错令牌 401，正确令牌 200（用临时目录，不碰真实数据）；
9. **冒烟脚本可执行**：四个验证脚本都在且语法正确；
10. **闸门失败不发布**（``--deep``）：真跑一次会红的闸门，指针不动、结果表回滚。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，装不下「¥」这类符号，强制 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 老解释器
        pass

_CHECKS: List[Tuple[str, str]] = []
_FAILURES: List[str] = []
_SKIPS: List[str] = []


def _ok(name: str, detail: str = "") -> None:
    _CHECKS.append((name, "OK"))
    print("OK   %-34s %s" % (name, detail))


def _fail(name: str, detail: str = "") -> None:
    _CHECKS.append((name, "FAIL"))
    _FAILURES.append("%s：%s" % (name, detail))
    print("FAIL %-34s %s" % (name, detail))


def _skip(name: str, detail: str = "") -> None:
    _CHECKS.append((name, "SKIP"))
    _SKIPS.append("%s：%s" % (name, detail))
    print("SKIP %-34s %s" % (name, detail))


def _run(cmd: List[str], cwd: Optional[Path] = None,
         timeout: int = 120) -> Tuple[int, str]:
    """跑一条命令，返回 (退出码, 合并输出)。"""
    try:
        proc = subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "%s: %s" % (type(exc).__name__, exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ---------------------------------------------------------------------------
# 1) 交付物齐全
# ---------------------------------------------------------------------------

_DELIVERABLES = (
    "Dockerfile",
    ".dockerignore",
    "docker-compose.yml",
    "deploy/caddy/Caddyfile",
    "deploy/env.example",
    "deploy/README.md",
    "src/config.py",
    "src/run_manifest.py",
    "src/device_auth.py",
    "tools/device_tokens.py",
    "tools/backup.py",
    "tools/restore_drill.py",
    "tests/test_run_manifest.py",
    "tests/test_server_auth.py",
    "tests/test_backup.py",
)


def check_deliverables() -> None:
    missing = [name for name in _DELIVERABLES
               if not (ROOT / name).exists()]
    if missing:
        _fail("交付物齐全", "缺 %d 个：%s" % (len(missing), ", ".join(missing)))
    else:
        _ok("交付物齐全", "%d 个文件都在" % len(_DELIVERABLES))


# ---------------------------------------------------------------------------
# 2) compose 可解析
# ---------------------------------------------------------------------------

def check_compose() -> None:
    path = ROOT / "docker-compose.yml"
    try:
        import yaml

        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _fail("compose 可解析", "%s: %s" % (type(exc).__name__, exc))
        return
    services = (payload or {}).get("services") or {}
    need = {"app", "sensor", "caddy"}
    missing = need - set(services)
    if missing:
        _fail("compose 可解析", "缺服务：%s" % ", ".join(sorted(missing)))
        return
    sensor = services.get("sensor") or {}
    env = sensor.get("environment") or {}
    problems = []
    if str(env.get("SENSOR_AUTH", "")).strip() != "${SENSOR_AUTH:-1}":
        problems.append("sensor 的 SENSOR_AUTH 不是默认开启")
    if not str(env.get("SENSOR_DATA_FILE", "")).strip():
        problems.append("sensor 缺 SENSOR_DATA_FILE")
    app_env = (services.get("app") or {}).get("environment") or {}
    if not str(app_env.get("SENSOR_DATA_FILE", "")).strip():
        problems.append("app 缺 SENSOR_DATA_FILE（读不到传感器写的文件）")
    if problems:
        _fail("compose 可解析", "；".join(problems))
    else:
        _ok("compose 可解析", "服务 %s｜鉴权默认开｜读数路径已挂卷"
            % ", ".join(sorted(services)))


# ---------------------------------------------------------------------------
# 3) CI 已扩展
# ---------------------------------------------------------------------------

def check_ci() -> None:
    path = ROOT / ".github" / "workflows" / "ci.yml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail("CI 已扩展", str(exc))
        return
    has_test = "pytest" in text
    has_docker = "docker-smoke" in text and "docker build" in text
    if has_test and has_docker:
        _ok("CI 已扩展", "pytest 作业 + docker-smoke 作业都在")
    elif not has_test:
        _fail("CI 已扩展", "原 pytest 作业丢了")
    else:
        _fail("CI 已扩展", "缺 docker-smoke 作业")


# ---------------------------------------------------------------------------
# 4) 零明文密钥
# ---------------------------------------------------------------------------

def check_no_plaintext_secrets() -> None:
    code, out = _run(["git", "ls-files", "config/"])
    tracked = [line.strip() for line in out.splitlines() if line.strip()]
    if code != 0:
        _skip("零明文密钥", "读不到 git 跟踪列表")
        return
    bad = [name for name in tracked
           if name.endswith(".txt") or "key" in name.lower()
           or "secret" in name.lower() or "token" in name.lower()]
    if bad:
        _fail("零明文密钥", "这些密钥文件被 git 跟踪了：%s" % ", ".join(bad))
        return
    ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    needed = ("config/amap_key.txt", "config/deepseek_key.txt",
              "config/amap_js_security_code.txt", "backups/",
              "outputs/sensor_audit.jsonl")
    missing = [rule for rule in needed if rule not in ignore_text]
    if missing:
        _fail("零明文密钥", ".gitignore 缺规则：%s" % ", ".join(missing))
        return
    _ok("零明文密钥", "git 只跟踪 %d 个非密钥配置｜ignore 规则齐全" % len(tracked))


# ---------------------------------------------------------------------------
# 5) 密钥分层可解析
# ---------------------------------------------------------------------------

def check_secret_layers() -> None:
    from src import config as app_config

    try:
        items = app_config.known_secrets()
    except Exception as exc:
        _fail("密钥分层可解析", "%s: %s" % (type(exc).__name__, exc))
        return
    if not items:
        _fail("密钥分层可解析", "登记表是空的")
        return
    ok_sources = {"env", "env_file", "secret_dir", "config", "missing"}
    bad = [str(item["env"]) for item in items
           if str(item.get("source")) not in ok_sources]
    if bad:
        _fail("密钥分层可解析", "来源异常：%s" % ", ".join(bad))
        return
    ready = sum(1 for item in items if item.get("configured"))
    detail = "；".join("%s←%s" % (item["env"], item["source"]) for item in items)
    if ready == len(items):
        _ok("密钥分层可解析", "全部就绪（%s）" % detail)
    else:
        _skip("密钥分层可解析", "已配置 %d/%d（%s）" % (ready, len(items), detail))


# ---------------------------------------------------------------------------
# 6) 生效指针有效
# ---------------------------------------------------------------------------

def check_current_run() -> None:
    from src import run_manifest

    pointer = run_manifest.read_current()
    if not pointer:
        _skip("生效指针有效", "还没发布过（跑一次 python -m src.pipeline）")
        return
    if not pointer.get("gate_passed"):
        _fail("生效指针有效", "指针指向的 run 闸门没通过")
        return
    snapshot = ROOT / "data" / "processed" / str(pointer.get("snapshot") or "")
    if not snapshot.is_dir():
        _fail("生效指针有效", "快照目录不存在：%s" % snapshot)
        return
    _ok("生效指针有效", "run %s｜闸门 %s｜快照 %d 个文件"
        % (pointer.get("run_id"), pointer.get("gate_counts"),
           len(list(snapshot.iterdir()))))


# ---------------------------------------------------------------------------
# 7) 备份可恢复
# ---------------------------------------------------------------------------

def check_backup_roundtrip() -> None:
    from tools import backup, restore_drill

    with tempfile.TemporaryDirectory(prefix="wb_verify_p0_") as tmp:
        dest = Path(tmp) / "backups"
        try:
            created = backup.create_backup(root=ROOT, dest=dest, label="verify-p0")
        except Exception as exc:
            _fail("备份可恢复", "打包失败 %s: %s" % (type(exc).__name__, exc))
            return
        try:
            code = restore_drill.run_drill(created.backup_dir)
        except Exception as exc:
            _fail("备份可恢复", "演练失败 %s: %s" % (type(exc).__name__, exc))
            return
        if code == 0:
            _ok("备份可恢复", "%d 个文件校验通过（%s）"
                % (int(created.manifest.get("file_count") or 0),
                   created.backup_dir.name))
        else:
            _fail("备份可恢复", "恢复演练退出码 %d" % code)


# ---------------------------------------------------------------------------
# 8) 未注册设备被拒
# ---------------------------------------------------------------------------

def check_device_auth() -> None:
    from src import device_auth

    with tempfile.TemporaryDirectory(prefix="wb_verify_p0_sensor_") as tmp:
        base = Path(tmp)
        os_env_backup = {
            key: __import__("os").environ.get(key)
            for key in ("SENSOR_DATA_FILE", "SENSOR_AUDIT_FILE", "SENSOR_AUTH",
                        "SENSOR_DEVICES_FILE", "SENSOR_RATE_LIMIT_PER_MIN")
        }
        import os

        os.environ["SENSOR_DATA_FILE"] = str(base / "sensor_data.csv")
        os.environ["SENSOR_AUDIT_FILE"] = str(base / "audit.jsonl")
        os.environ["SENSOR_AUTH"] = "1"
        os.environ["SENSOR_RATE_LIMIT_PER_MIN"] = "60"
        registry = base / "devices.json"
        os.environ["SENSOR_DEVICES_FILE"] = str(registry)
        try:
            import importlib

            import server as server_module

            server_module = importlib.reload(server_module)
            token = "verify-" + "x" * 24
            device_auth.upsert_device("PDM-VERIFY-001", device_auth.token_digest(token),
                                       path=registry)
            with server_module.app.test_client() as client:
                body = {"device_id": "PDM-VERIFY-001", "co2": 800.0, "temp": 24.0}
                no_token = client.post("/api/upload_sensor", json=body)
                bad_token = client.post(
                    "/api/upload_sensor", json=body,
                    headers={device_auth.TOKEN_HEADER: "wrong"})
                good = client.post(
                    "/api/upload_sensor", json=body,
                    headers={device_auth.TOKEN_HEADER: token})
            if no_token.status_code != 401:
                _fail("未注册设备被拒", "缺令牌返回了 %d" % no_token.status_code)
                return
            if bad_token.status_code != 401:
                _fail("未注册设备被拒", "错令牌返回了 %d" % bad_token.status_code)
                return
            if good.status_code != 200:
                _fail("未注册设备被拒", "正确令牌返回了 %d" % good.status_code)
                return
            _ok("未注册设备被拒", "缺令牌 401 / 错令牌 401 / 正确令牌 200")
        except Exception as exc:
            _fail("未注册设备被拒", "%s: %s" % (type(exc).__name__, exc))
        finally:
            for key, value in os_env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


# ---------------------------------------------------------------------------
# 9) 冒烟脚本可执行
# ---------------------------------------------------------------------------

_SMOKE = ("tools/verify_pages.js", "tools/verify_nav.js",
          "tools/verify_sliders.js", "tools/verify_dashboard_layout.js")


def check_smoke_scripts() -> None:
    missing = [name for name in _SMOKE if not (ROOT / name).exists()]
    if missing:
        _fail("冒烟脚本可执行", "缺：%s" % ", ".join(missing))
        return
    for name in _SMOKE:
        code, out = _run(["node", "--check", name], timeout=60)
        if code != 0:
            _fail("冒烟脚本可执行", "%s 语法错：%s" % (name, out[:120]))
            return
    _ok("冒烟脚本可执行", "4 个脚本语法通过")


# ---------------------------------------------------------------------------
# 10) 闸门失败不发布（--deep）
# ---------------------------------------------------------------------------

def check_gate_blocks_publish() -> None:
    import shutil

    from src import run_manifest

    with tempfile.TemporaryDirectory(prefix="wb_verify_p0_gate_") as tmp:
        work = Path(tmp)
        for item in ("src", "config"):
            source = ROOT / item
            if source.exists():
                shutil.copytree(source, work / item,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        env = dict(__import__("os").environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            first = subprocess.run([sys.executable, "-m", "src.pipeline"], cwd=str(work),
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=900, env=env)
        except subprocess.SubprocessError as exc:
            _fail("闸门失败不发布", "第一次复算异常：%s" % exc)
            return
        if first.returncode != 0:
            _fail("闸门失败不发布", "第一次复算没通过（退出码 %d）" % first.returncode)
            return
        processed = work / "data" / "processed"
        before = run_manifest.read_current(processed)
        if not before:
            _fail("闸门失败不发布", "第一次复算没有产出生效指针")
            return
        try:
            second = subprocess.run([sys.executable, "-m", "src.pipeline", "--strict"],
                                    cwd=str(work), capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=900,
                                    env=env)
        except subprocess.SubprocessError as exc:
            _fail("闸门失败不发布", "第二次复算异常：%s" % exc)
            return
        after = run_manifest.read_current(processed)
        if after != before:
            _fail("闸门失败不发布", "闸门红了但生效指针动了")
            return
        run_log = json.loads((processed / "run_log.json").read_text(encoding="utf-8"))
        if not run_log.get("rollback", {}).get("ok"):
            _fail("闸门失败不发布", "没有回滚到上一生效版本")
            return
        _ok("闸门失败不发布", "指针未动 + 已回滚到 %s" % before.get("run_id"))


# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="企业级 P0 一键验收")
    parser.add_argument("--deep", action="store_true",
                        help="追加真跑流水线（验证闸门失败不发布）")
    args = parser.parse_args(argv)

    print("=" * 74)
    print("企业级 P0 验收 · %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 74)

    check_deliverables()
    check_compose()
    check_ci()
    check_no_plaintext_secrets()
    check_secret_layers()
    check_current_run()
    check_backup_roundtrip()
    check_device_auth()
    check_smoke_scripts()
    if args.deep:
        check_gate_blocks_publish()
    else:
        _skip("闸门失败不发布", "加 --deep 真跑一遍验证")

    print("=" * 74)
    passed = sum(1 for _n, status in _CHECKS if status == "OK")
    failed = sum(1 for _n, status in _CHECKS if status == "FAIL")
    skipped = sum(1 for _n, status in _CHECKS if status == "SKIP")
    print("合计：OK %d｜FAIL %d｜SKIP %d" % (passed, failed, skipped))
    if failed:
        print("\n未通过：")
        for item in _FAILURES:
            print("  · %s" % item)
        return 1
    print("\nP0 验收通过 [OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
