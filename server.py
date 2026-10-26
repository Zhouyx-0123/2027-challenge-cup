# -*- coding: utf-8 -*-
"""ESP32 边缘数据接收服务：POST /api/upload_sensor -> sensor_data.csv。"""
from __future__ import annotations

import csv
import math
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

ROOT = Path(__file__).resolve().parent
SENSOR_FILE = ROOT / "sensor_data.csv"
FIELDNAMES = ["timestamp", "device_id", "co2", "temp"]
_file_lock = threading.Lock()

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})


def _valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


@app.post("/api/upload_sensor")
def upload_sensor():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "请求体必须是 JSON 对象"}), 400

    device_id = str(payload.get("device_id") or "").strip()
    co2, temp = payload.get("co2"), payload.get("temp")
    if not device_id or len(device_id) > 64:
        return jsonify({"status": "error", "message": "device_id 缺失或过长"}), 400
    if not _valid_number(co2) or not _valid_number(temp):
        return jsonify({"status": "error", "message": "co2 和 temp 必须是有限数值"}), 400
    if not 0 <= float(co2) <= 100000 or not -50 <= float(temp) <= 100:
        return jsonify({"status": "error", "message": "传感器数值超出合理范围"}), 400

    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "device_id": device_id,
        "co2": float(co2),
        "temp": float(temp),
    }
    try:
        with _file_lock:
            need_header = not SENSOR_FILE.exists() or SENSOR_FILE.stat().st_size == 0
            with SENSOR_FILE.open("a", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
                if need_header:
                    writer.writeheader()
                writer.writerow(row)
    except OSError as exc:
        return jsonify({"status": "error", "message": "写入失败：%s" % exc}), 500
    return jsonify({"status": "success"})


@app.get("/api/health")
def health():
    return jsonify({"status": "success"})


if __name__ == "__main__":
    # ESP32 与电脑须在同一局域网；host=0.0.0.0 允许局域网设备访问。
    app.run(host="0.0.0.0", port=5000, debug=False)
