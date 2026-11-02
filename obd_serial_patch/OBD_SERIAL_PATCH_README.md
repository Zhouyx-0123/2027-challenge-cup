# OBD 文本帧接口改造（patch 包）

> 针对设备侧新的 J1939 网关文本行输出。原接口只吃 JSON（`POST /api/upload_obd_gps`），
> 吃不下这种定宽文本帧。

## 一句话

**不动 `server.py` 的鉴权/限流/审计内核，在它前面加一层"文本帧 → 契约记录"的解析。**
设备仍然走同一个端点、同一套令牌，没有新开免校验入口。

```
COM 口文本行
   │  src/obd_line.py        解析 · --- → null · uptime 锚定 · 质控打标
   ▼
契约 JSON 记录
   │  tools/obd_serial_bridge.py   批量 POST（复用 X-Device-Token）
   ▼
POST /api/upload_obd_gps  →  src/obd_gps.py（归一 + 守恒法算碳，口径不变）
```

## 文件

| 文件 | 作用 | 要不要改 |
|---|---|---|
`src/obd_line.py` | 文本帧解析、时间锚定、质控标记（纯函数，无第三方依赖） | 新增，直接放 |
`tools/obd_serial_bridge.py` | 串口/回放 → HTTP 上报 | 新增，直接放 |
`tests/test_obd_line.py` | 45 项断言 | 新增 |
`tests/test_bridge_e2e.py` | 7 项端到端断言（含令牌头校验） | 新增 |
`samples/frames_sample.txt` | 无硬件时的回放样本 | 新增 |
`config/obd_gps_contract.additions.json` | 契约增量（5 新字段 + 别名 + 6 新 qc_flags + serial_frame 段） | **手工并入** `config/obd_gps_contract.json` |

## 落地步骤

1. 复制 `src/` `tools/` `tests/` `samples/` 四个目录进仓库同名位置。
2. 把 `config/obd_gps_contract.additions.json` 的 `serial_frame` / `fields_add` /
   `aliases_add` / `qc_flags_add` / `delivery_add` 五段增量并入
   `config/obd_gps_contract.json`（已有字段一个都不改）。
3. **在 `src/obd_gps.py` 的 `CANONICAL_FIELDS` 里登记 5 个新字段**
   （`engine_hours` / `torque_req_nm` / `torque_actual_nm` / `fuel_temp_c` / `oil_temp_c`）。
   ⚠️ 这步不能漏：`server.py` 落盘前按 `obd_gps.CANONICAL_FIELDS` 过滤，
   未登记的字段会被**静默丢弃**，CSV 里就看不到。
4. 串口直连时：`pip install pyserial`。
5. 跑验证（见下）。

## 验证

```bash
python tests/test_obd_line.py       # PASSED 45 / FAILED 0
python tests/test_bridge_e2e.py     # PASSED 7  / FAILED 0

# 无硬件跑通链路
python -m tools.obd_serial_bridge --replay samples/frames_sample.txt \
       --vehicle-id TRK-GZ-001 --dry-run

# 有硬件
python -m tools.obd_serial_bridge --port COM5 --baud 115200 \
       --vehicle-id TRK-GZ-001 --token <明文令牌>
```

## 已处理的设计陷阱

| 陷阱 | 处理 |
|---|---|
`%5lu` 到 100000 s（≈27.8 h）后括号变 6 位，定宽切片整体错列 | 按「键名 + `=`」切区间，不做定宽 |
`FB= 10.020km/L(=  4.5)` 值里含空格、`/`、`=` | 区间切分天然容忍；`FB` 单独正则取两段 |
`TQ=  30/  28`、`T= 88.0/ 45.0/ 90.0` | 按 `/` 分列 |
`---` 无值 | 一律 → `null`，**绝不填 0** |
`LINK=OK` 被误当有效性判据 | 明确不作为判据；只看逐字段 `---` |
设备只有相对开机时间 | 桥接端锚定 `boot_wall = 到达 − uptime`；uptime 回退 >5 s 判重启重锚 |
`FA` 是仪表显示报文（65276），非 ECM 真实燃油率 | 主口径走 `FC` 差分；`FA` 只作旁证 |
`FB` 括号里的反算 L/h | 与 `FA` 比对，偏差 >5% 置 `fuel_rate_mismatch`（正是"反证口径"的落点） |

## 三个需要你拍板的（我按最保守的默认实现了）

1. **`T=a/b/c` 的第 2、3 位**。你表头写「冷却液/燃油/机油」，说明写「水·油·柴」，两者对不上。
   PGN 65262 ET1 的标准顺序是 冷却液/燃油/机油，我按这个实现。**首次接车实测确认一次**即可锁死。
2. **这套帧没有 GPS**。所以恒置 `no_gps_source`，前端轨迹区块应隐藏。
   若另有 GPS 模块，加 `--has-gps-source` 并把它接到另一个串口/字段。
3. **`vehicle_id` 不在帧里**。必须由桥接端给定；多车共用一根总线时，需要设备侧把车号打进帧头，
   否则无法分车——这会直接影响 `src/obd_pipeline.py` 的分车聚合。
