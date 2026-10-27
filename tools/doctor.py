# -*- coding: utf-8 -*-
"""全链路体检：一次跑完，把"还差什么"说清楚。

用法::

    python -m tools.doctor              # 全量体检
    python -m tools.doctor --quick      # 跳过慢检查（不连外部服务）

体检分七组，逐组给结论：

===  ==========================  ============================================
组   检查什么                    关心的问题
===  ==========================  ============================================
1    Python 与依赖              环境能不能跑起来
2    密钥就绪                    地图/发票识别能不能用（**只报来源不报值**）
3    数据层与发布状态            页面读的是哪一版数据、闸门过没过
4    容器与部署形态              Docker 能不能用；不能用时给替代路径
5    传感器接入                  设备能不能连上、令牌对不对、读数进没进
6    备份与恢复                  原始证据有没有备份、能不能恢复
7    硬件与固件                  ESP32 固件在不在、串口接没接、令牌配没配
===  ==========================  ============================================

退出码：0 = 全部就绪（可能有 WARN）；1 = 存在必须修的项。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，装不下「¥」这类符号，强制 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - 老解释器
        pass

#: 关键 Python 依赖（缺失会挡住某一块功能）
REQUIRED_PACKAGES = (
    ("streamlit", "决策台（五页）"),
    ("pandas", "数据层"),
    ("numpy", "数据层"),
    ("pyarrow", "Parquet 结果表"),
    ("plotly", "图表"),
    ("flask", "传感器接入服务"),
    ("requests", "高德 / DeepSeek 联网"),
    ("pymoo", "NSGA-II 路线进化"),
    ("yaml", "compose 配置校验"),
)

#: 可选依赖（缺了只影响某一块，要提示但不致命）
OPTIONAL_PACKAGES = (
    ("flask_cors", "浏览器跨域访问传感器接口（ESP32 直连不需要）"),
    ("cryptography", "证书相关"),
)


class Report:
    """体检结果收集器。"""

    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str, str]] = []

    def add(self, group: str, name: str, status: str, detail: str = "") -> None:
        self.rows.append((group, name, status, detail))
        mark = {"OK": "OK  ", "WARN": "WARN", "FAIL": "FAIL", "SKIP": "SKIP"}.get(status, "??  ")
        print("%s %-16s %-26s %s" % (mark, group, name, detail))

    @property
    def failures(self) -> List[Tuple[str, str, str, str]]:
        return [row for row in self.rows if row[2] == "FAIL"]

    @property
    def warnings(self) -> List[Tuple[str, str, str, str]]:
        return [row for row in self.rows if row[2] == "WARN"]


def _run(cmd: List[str], timeout: int = 60) -> Tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "%s: %s" % (type(exc).__name__, exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _http(url: str, timeout: float = 4.0) -> Tuple[int, str]:
    """GET 一个 URL，返回 (状态码, 文本)。连不上返回 (0, 原因)。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return 0, str(exc)


def _local_ip() -> str:
    """取本机**内网** IP（给 ESP32 用）。

    优先私有网段（192.168.x / 10.x / 172.16–31.x）；
    169.254.x 是链路本地地址，ESP32 跨设备用不了，必须排除。
    """
    import ipaddress

    candidates: List[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except OSError:
        pass
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } "
             "| Select-Object -ExpandProperty IPAddress"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        candidates.extend(line.strip() for line in (proc.stdout or "").splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError):
        pass

    private = []
    for item in candidates:
        try:
            address = ipaddress.ip_address(item)
        except ValueError:
            continue
        if address.is_private and not address.is_link_local and not address.is_loopback:
            private.append(item)
    if private:
        return private[0]
    return candidates[0] if candidates else "127.0.0.1"


# ---------------------------------------------------------------------------
# 1) Python 与依赖
# ---------------------------------------------------------------------------

def check_runtime(report: Report) -> None:
    version = sys.version_info
    if version >= (3, 10):
        report.add("运行时", "Python 版本", "OK",
                   "%d.%d.%d" % (version.major, version.minor, version.micro))
    else:
        report.add("运行时", "Python 版本", "FAIL",
                   "%d.%d.%d（需 ≥3.10）" % (version.major, version.minor, version.micro))

    missing: List[str] = []
    for module, purpose in REQUIRED_PACKAGES:
        try:
            __import__(module)
        except Exception:
            missing.append("%s（%s）" % (module, purpose))
    if missing:
        report.add("运行时", "必需依赖", "FAIL",
                   "缺 %d 个：%s → pip install -r requirements.txt" % (len(missing), "、".join(missing)))
    else:
        report.add("运行时", "必需依赖", "OK", "全部就位")

    optional_missing = []
    for module, purpose in OPTIONAL_PACKAGES:
        try:
            __import__(module)
        except Exception:
            optional_missing.append("%s（%s）" % (module, purpose))
    if optional_missing:
        report.add("运行时", "可选依赖", "WARN", "缺：" + "、".join(optional_missing))
    else:
        report.add("运行时", "可选依赖", "OK", "全部就位")

    wheels = ROOT / "wheels"
    if wheels.is_dir() and any(wheels.glob("*.whl")):
        report.add("运行时", "离线 wheels", "OK", "%d 个包（断网可装）" % len(list(wheels.glob("*.whl"))))
    else:
        report.add("运行时", "离线 wheels", "SKIP", "没有 wheels 目录（断网时需联网装依赖）")


# ---------------------------------------------------------------------------
# 2) 密钥
# ---------------------------------------------------------------------------

def check_secrets(report: Report) -> None:
    from src import config as app_config

    for item in app_config.known_secrets():
        if item.get("configured"):
            report.add("密钥", str(item["label"]), "OK", "来源 %s" % item["source"])
        else:
            report.add("密钥", str(item["label"]), "WARN",
                       "未配置（%s）→ 地图/发票识别会降级" % item["env"])

    tracked_code, tracked = _run(["git", "ls-files", "config/"])
    if tracked_code == 0:
        leaked = [name for name in tracked.splitlines()
                  if name.strip() and (name.endswith(".txt") or "key" in name.lower()
                                       or "secret" in name.lower())]
        if leaked:
            report.add("密钥", "入库检查", "FAIL", "被 git 跟踪的密钥文件：%s" % "、".join(leaked))
        else:
            report.add("密钥", "入库检查", "OK", "git 未跟踪任何密钥文件")


# ---------------------------------------------------------------------------
# 3) 数据层与发布状态
# ---------------------------------------------------------------------------

def check_data_layer(report: Report) -> None:
    from src import run_manifest
    from src import schema

    processed = ROOT / "data" / "processed"
    missing = [name for name in schema.RESULT_FILES.values()
               if not (processed / name).exists()]
    if missing:
        report.add("数据层", "结果表", "WARN",
                   "缺 %d 张：%s → python -m src.pipeline" % (len(missing), "、".join(missing[:4])))
    else:
        report.add("数据层", "结果表", "OK", "%d 张齐备" % len(schema.RESULT_FILES))

    pointer = run_manifest.read_current()
    if not pointer:
        report.add("数据层", "生效版本", "WARN",
                   "未发布 → python -m src.pipeline --adopt-current")
    else:
        counts = pointer.get("gate_counts") or {}
        if pointer.get("gate_passed") and not counts.get("fail"):
            report.add("数据层", "生效版本", "OK",
                       "run %s｜闸门 %s PASS" % (pointer.get("run_id"),
                                              counts.get("pass")))
        else:
            report.add("数据层", "生效版本", "FAIL",
                       "指针指向的 run 闸门未通过（fail=%s）" % counts.get("fail"))

    history = run_manifest.read_index(limit=5)
    if history:
        latest = history[-1]
        report.add("数据层", "run 历史", "OK",
                   "最近 %d 次｜最后一次 %s" % (len(history), latest.get("status")))


# ---------------------------------------------------------------------------
# 4) 容器与部署形态
# ---------------------------------------------------------------------------

def check_container(report: Report) -> None:
    # 已安装 ≠ 能用：Docker Desktop 装了但没重启/没起 daemon 是最常见的状态
    docker_exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"
    code, out = _run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30)
    if code == 0 and out.strip():
        report.add("部署", "Docker", "OK", "daemon 版本 %s" % out.strip().splitlines()[0])
    elif docker_exe.exists():
        report.add("部署", "Docker", "WARN",
                   "已安装但 daemon 没起来（多半是 WSL2 刚启用还没重启）"
                   " → 重启后跑 deploy\\setup_windows.ps1 -Finish")
    else:
        report.add("部署", "Docker", "WARN",
                   "未安装 → deploy\\install_docker.ps1（需管理员+重启）"
                   "；或直接用原生路线 deploy\\windows\\start-native.ps1")

    compose = ROOT / "docker-compose.yml"
    if compose.exists():
        try:
            import yaml

            payload = yaml.safe_load(compose.read_text(encoding="utf-8"))
            services = list((payload or {}).get("services") or {})
            report.add("部署", "compose 配置", "OK", "服务：%s" % "、".join(services))
        except Exception as exc:
            report.add("部署", "compose 配置", "FAIL", "%s: %s" % (type(exc).__name__, exc))
    else:
        report.add("部署", "compose 配置", "WARN", "没有 docker-compose.yml")

    for name in ("Dockerfile", "deploy/caddy/Caddyfile", "deploy/env.example",
                 "deploy/windows/start-native.ps1", "deploy/install_docker.ps1"):
        report.add("部署", name, "OK" if (ROOT / name).exists() else "WARN",
                   "有" if (ROOT / name).exists() else "缺失")

    code, out = _run(["git", "ls-files", ".github/workflows/ci.yml"])
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    if workflow.exists():
        text = workflow.read_text(encoding="utf-8")
        report.add("部署", "CI 流水线", "OK" if "docker-smoke" in text else "WARN",
                   "含 docker-smoke 作业" if "docker-smoke" in text else "缺 docker-smoke 作业")


# ---------------------------------------------------------------------------
# 5) 传感器接入
# ---------------------------------------------------------------------------

def check_sensor(report: Report, quick: bool) -> None:
    from src import device_auth

    registry = device_auth.load_registry()
    devices = device_auth.list_devices(registry)
    report.add("传感器", "设备注册表", "OK" if devices else "WARN",
               "%d 台设备（%s）" % (len(devices),
                                device_auth.registry_path())
               if devices else "还没有登记设备 → python -m tools.device_tokens add --device-id X")

    if quick:
        report.add("传感器", "接口连通", "SKIP", "--quick 模式跳过")
        return

    for base in ("http://127.0.0.1:5000", "http://127.0.0.1:8501"):
        code, text = _http(base + ("/api/health" if "5000" in base else "/_stcore/health"))
        if code == 200:
            report.add("传感器", "服务 %s" % base, "OK", "健康检查通过")
            if "5000" in base:
                try:
                    payload = json.loads(text)
                    auth = payload.get("auth") or {}
                    report.add("传感器", "鉴权模式", "OK" if auth.get("enabled") else "WARN",
                               "开启（%s）" % auth.get("header") if auth.get("enabled")
                               else "已关闭（SENSOR_AUTH=0）——仅限演示")
                except ValueError:
                    pass
        else:
            report.add("传感器", "服务 %s" % base, "WARN",
                       "没起来（%s）→ deploy\\windows\\start-native.ps1" % (text[:60] or code))

    from src import sensor as sensor_kernel

    path = sensor_kernel.sensor_data_path()
    if path.exists():
        report.add("传感器", "读数文件", "OK", "%s（%.1f KB）"
                   % (path.name, path.stat().st_size / 1024))
    else:
        report.add("传感器", "读数文件", "WARN",
                   "%s 还没有数据 → 先跑 tools/sensor_probe.py --register" % path.name)

    report.add("传感器", "设备接入地址", "OK",
               "http://%s:5000/api/upload_sensor（ESP32 用这个）" % _local_ip())


# ---------------------------------------------------------------------------
# 6) 备份与恢复
# ---------------------------------------------------------------------------

def check_backup(report: Report, quick: bool) -> None:
    backups = ROOT / "backups"
    if not backups.is_dir():
        report.add("备份", "备份目录", "WARN",
                   "没有 backups/ → python -m tools.backup --label manual")
        return
    items = sorted([p for p in backups.iterdir() if p.is_dir()])
    if not items:
        report.add("备份", "备份目录", "WARN", "目录是空的 → python -m tools.backup --label manual")
        return
    latest = items[-1]
    report.add("备份", "最近备份", "OK", "%s（%d 份）" % (latest.name, len(items)))

    manifest = latest / "manifest.json"
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            report.add("备份", "备份清单", "OK",
                       "%d 个文件｜类别 %s" % (int(payload.get("file_count") or 0),
                                            "、".join((payload.get("categories") or {}).keys())))
        except ValueError:
            report.add("备份", "备份清单", "FAIL", "manifest.json 解析失败")
    else:
        report.add("备份", "备份清单", "WARN", "缺 manifest.json")


# ---------------------------------------------------------------------------
# 7) 硬件与固件
# ---------------------------------------------------------------------------

def check_hardware(report: Report) -> None:
    firmware = ROOT / "hardware" / "esp32" / "esp32_sensor_client.ino"
    micropython = ROOT / "hardware" / "esp32" / "micropython_sensor_client.py"
    probe = ROOT / "tools" / "sensor_probe.py"
    for path in (firmware, micropython, probe):
        report.add("硬件", path.name, "OK" if path.exists() else "WARN",
                   "有" if path.exists() else "缺失")

    config_h = ROOT / "hardware" / "esp32" / "config.h"
    if config_h.exists():
        report.add("硬件", "固件令牌配置", "OK", "config.h 已就位（不入库）")
    else:
        report.add("硬件", "固件令牌配置", "WARN",
                   "缺 config.h → 复制 config.example.h 并填入令牌")

    code, out = _run(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_SerialPort | Select-Object -ExpandProperty DeviceID"],
                     timeout=20)
    ports = [line.strip() for line in out.splitlines() if line.strip()]
    real = [p for p in ports if not p.upper().startswith("COM")]
    if real:
        report.add("硬件", "串口设备", "OK", "检测到：%s" % "、".join(real))
    else:
        report.add("硬件", "串口设备", "WARN",
                   "没有检测到真实串口（%s）→ ESP32 未接入或驱动未装" % ("、".join(ports) or "无"))

    readme = ROOT / "hardware" / "README.md"
    if readme.exists():
        report.add("硬件", "接入手册", "OK", "hardware/README.md")
    else:
        report.add("硬件", "接入手册", "WARN", "缺 hardware/README.md")


# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="全链路体检：把还差什么说清楚")
    parser.add_argument("--quick", action="store_true", help="跳过慢检查（不连本地服务）")
    args = parser.parse_args(argv)

    print("=" * 84)
    print("全链路体检 · %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 84)

    report = Report()
    check_runtime(report)
    check_secrets(report)
    check_data_layer(report)
    check_container(report)
    check_sensor(report, args.quick)
    check_backup(report, args.quick)
    check_hardware(report)

    print("=" * 84)
    ok = sum(1 for row in report.rows if row[2] == "OK")
    warn = len(report.warnings)
    fail = len(report.failures)
    print("合计：OK %d｜WARN %d｜FAIL %d" % (ok, warn, fail))
    if report.failures:
        print("\n必须修：")
        for _group, name, _status, detail in report.failures:
            print("  · %s：%s" % (name, detail))
    if report.warnings:
        print("\n建议处理：")
        for _group, name, _status, detail in report.warnings:
            print("  · %s：%s" % (name, detail))
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
