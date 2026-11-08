# OBD 模拟数据：原始格式、1 万条扩样与合理性约束

> 一句话：板子对外发的是 JSON 批次，后端落盘为 26 列 CSV；本目录给了 30 条基准样本、10,000 条车队模拟集，以及可在 Excel 里向下填充的 RAND 公式。
> 日期：2026-11-07　｜　生成器：tools/gen_obd_sim.py　｜　数据目录：data/sim/

---

## 1. 文件清单

| 文件 | 用途 | 规模 |
|---|---|---|
| data/sim/obd_raw_sample_30.csv | 板子当前输出的 30 条基准样本，UTF-8 BOM，Excel 直接打开 | 30 行 × 26 列 |
| data/sim/obd_raw_sample_30.json | 板子 HTTP 请求体，{"records":[...]} | 30 条 |
| data/sim/obd_raw_sample_30.jsonl | 一行一条 JSON，便于流式回放 | 30 行 |
| data/sim/obd_raw_10000.csv | 10 台车 × 1,000 条 = 10,000 条，可直接灌后端 | 10,000 行 × 26 列 |
| data/sim/obd_raw_10000.xlsx | Excel 版：说明 / 原始数据_30 / 模拟_10000 / 校验 / 公式模板 | 5 个 sheet |
| tools/gen_obd_sim.py | 生成器，可改车辆数、每车行数、随机种子 | Python 3.12 |

---

## 2. 板子到底输出什么

板子当前是模拟固件：生成一批 OBD 记录，通过 WiFi 直接 POST 到：

~~~
POST http://<PC_LAN_IP>:5000/api/upload_obd_gps
Content-Type: application/json

{"records":[ {...}, {...} ]}
~~~

串口只打印调试日志：

~~~
OBD_SIM_BOOT 2026-11-07
[WIFI] IP=192.168.43.11
[HTTP] 200 {"accepted":30,"reasons":{},"rejected":0,"status":"success"}
~~~

后端校验后写 data/raw/obd_gps/obd_stream.csv 与 obd_rejects.csv。CSV 是 26 列，字段顺序固定。total_fuel_l 是累计油耗，不是每行油耗。

---

## 3. 26 列字段与单位

| # | 字段 | 单位/格式 | 说明 |
|---|---|---|---|
| 1 | ts | ISO 8601，+08:00 | 采样时间；本模拟集固定 10s 一条 |
| 2 | vehicle_id | 字符串 | 车辆唯一 ID，如 TRK-LOOP-001 |
| 3 | plate | 字符串 | 车牌，如 苏A12345 |
| 4 | fuel_type | 字符串 | 本集固定 diesel |
| 5 | instant_fuel_lph | L/h | 瞬时燃油率；怠速约 2.5，50 km/h/12t 约 18 |
| 6 | total_fuel_l | L | 累计油耗；单调递增，不是每行油耗 |
| 7 | fuel_level_pct | % | 油箱余量；本集按 600 L 油箱折算 |
| 8 | engine_rpm | rpm | 柴油机 650–2600 |
| 9 | vehicle_speed_kmh | km/h | 0–95；0 表示怠速/停车 |
| 10 | throttle_pos_pct | % | 0–100 |
| 11 | engine_load_pct | % | 5–95 |
| 12 | engine_temp_c | °C | 约 85 |
| 13 | intake_press_kpa | kPa | 约 40 |
| 14 | atm_press_kpa | kPa | 101.3 |
| 15 | odo_km | km | 累计里程；单调递增 |
| 16 | lat | 度 | WGS84；入高德前需转 GCJ-02 |
| 17 | lng | 度 | WGS84 |
| 18 | gps_speed_kmh | km/h | GPS 速度，允许与轮速有小偏差 |
| 19 | heading_deg | 度 | 0–360 |
| 20 | altitude_m | m | 本集约 20 |
| 21 | satellites | 个 | 6–14 |
| 22 | fix_quality | 0/1 | 1=有效定位 |
| 23 | load_kg | kg | 载重；本集 3,000–30,000 |
| 24 | ignition | bool | 本集全 true |
| 25 | fault_codes | 字符串 | 空=无故障；不要随机填 |
| 26 | qc_flags | 字符串 | 空=无质控标记；由规则产生，不要随机填 |

---

## 4. 30 条基准样本

固定工况：50 km/h、18 L/h、12 t、10s 一条、30 个点。

- 首点 total_fuel_l=800.05，末点 801.50；油耗差 1.45 L
- 首点 odo_km=50000.1389，末点 50004.1667；里程 4.0278 km
- 碳排：1.45 × 2.63 = 3.8135 kgCO₂e
- 碳强度：3.8135 × 1000 / 4.0278 = 946.79 gCO₂/km

注意：30 个采样点只有 29 个区间；首点代表起始累计值，不再额外加 0.05 L。否则 EF 会虚高到 979。

---

## 5. 10,000 条车队模拟集

### 5.1 结构

- 10 台车 × 1,000 条 = 10,000 条
- 每台车 10s 一条，约 2.78 小时；避免“单车连续 27 小时”这种不合理场景
- 车型/载重：12 t × 4 台、18 t × 3 台、30 t × 3 台
- 车牌 苏A12345–苏A12354；车辆 TRK-LOOP-001–010
- 每台车独立累计 total_fuel_l 和 odo_km；不要跨车求单调

### 5.2 总体结果（已用 src.obd_pipeline 复算）

| 指标 | 值 |
|---|---|
| 记录 | 10,000 条，可用 10,000 / 不可用 0 |
| 车辆 / 行程 | 10 台 / 10 段 |
| 总里程 | 1,327.53 km |
| 总油耗 | 589.305 L |
| 总碳排 | 1,549.872 kgCO₂e |
| 车队碳强度 | 1,167.49 gCO₂/km |
| 单车 EF 范围 | 937.28–1,486.06 gCO₂/km |

这个范围符合柴油重卡：12 t 高速接近 937，30 t 城市接近 1,486；没有“1,000 g 碳跑 10,000 km”的离谱值。

---

## 6. 生成模型：为什么不会算出离谱值

### 6.1 油耗

柴油车油耗不是独立随机数，而是由速度、载重、怠速共同决定：

~~~
fuel_lph = 2.5
         + 0.18 × speed
         + 0.583 × (load_t × speed / 100)
         + 0.25 × load_t × (speed / 50)
~~~

- 怠速（speed=0）：约 2.5 L/h，不随载重线性膨胀
- 50 km/h、12 t：2.5 + 9 + 3.5 + 3 = 18.0 L/h → 36 L/100km
- 50 km/h、30 t：约 27.7 L/h → 55 L/100km
- 60 km/h、40 t：约 39.3 L/h → 65 L/100km（重载上限附近）

叠加 6% 的乘性噪声，再 clamp 到 1.2–55 L/h；不会出现负油耗或几百升每小时。

### 6.2 速度

- 每 120 个点（20 分钟）换一个目标速度，围绕车辆均值；
- AR(1)：speed = 0.72×prev + 0.28×target + N(0,2.2)；
- 2.5% 概率出现单点怠速，不是把 target 置 0 后拖 120 行；否则 3% 的怠速会吃掉三分之一路程（这是第一版真实踩过的坑）；
- clamp 到 0–95 km/h。

### 6.3 碳排

~~~
distance_km = speed × 10 / 3600
fuel_delta_l = instant_fuel_lph × 10 / 3600
carbon_kg = fuel_delta_l × 2.63
~~~

2.63 kgCO₂/L 是柴油 TTW 因子，与项目 E₀ 主账同口径；不是 3.16 的 WTW。

### 6.4 物理上限

生成后强制校验：

- 每 10s 位移 ≤ 95 × 10 / 3600 = 0.264 km
- 每 10s 油耗 ≤ 55 × 10 / 3600 = 0.153 L
- 每车 EF 必须在 300–3,000 g/km
- 每车 carbon_kg / distance_km 必须在 0.3–3.0 kg/km
- total_fuel_l、odo_km 在每台车内单调递增

不满足就直接报错，不写文件。

---

## 7. Excel 里怎么扩到 1 万条

obd_raw_10000.xlsx 的“公式模板”sheet 已经放好 100 行公式；选中第 3–102 行，向下填充到第 1001 行，就是一台车的 1,000 条。多台车就复制 block，重置 F/O 的起始值。

关键公式（第 3 行，第 2 行是首行）：

| 列 | 公式 | 说明 |
|---|---|---|
| A ts | =A2+TIME(0,0,10) | 10s 一步；格式 yyyy-mm-dd"T"hh:mm:ss"+08:00" |
| E instant_fuel_lph | =MAX(1.2,2.5+0.18*I3+0.583*(W3/1000)*I3/100+0.25*(W3/1000)*(I3/50)+NORM.INV(RAND(),0,0.6)) | 速度 + 载重 + 噪声 |
| F total_fuel_l | =F2+E3*10/3600 | 累计，不是每行 |
| I vehicle_speed_kmh | =MAX(0,MIN(95,0.72*I2+0.28*NORM.INV(RAND(),55,18)+NORM.INV(RAND(),0,2))) | AR(1)，不要独立 RAND |
| O odo_km | =O2+I3*10/3600 | 累计里程 |
| W load_kg | =MAX(3000,MIN(30000,12000+NORM.INV(RAND(),0,300))) | 载重缓慢抖动 |

三条红线：

1. 不要让 speed 和 fuel 各自独立 RAND()；必须让油耗依赖速度和载重。
2. 不要把 total_fuel_l 当每行油耗；它是累计值，碳排要用差分。
3. 不要给 fault_codes / qc_flags 填随机字符串；它们应由规则引擎产生。

---

## 8. 怎么回放

### 8.1 直接灌 CSV 到碳排流水线

~~~
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONPATH=. \
python -m src.obd_pipeline --from-csv data/sim/obd_raw_10000.csv --no-write
~~~

### 8.2 走 HTTP 后端

把 obd_raw_10000.csv 按 30–500 条一批转成 {"records":[...]}，POST 到：

~~~
http://<PC_LAN_IP>:5000/api/upload_obd_gps
~~~

首轮回路可用 SENSOR_AUTH=0；正式环境要登记设备令牌，不要长期裸奔。

### 8.3 板端重放

把 obd_raw_sample_30.json 里的 records 直接写进 firmware_obd_sim.ino 的 build_payload()，或让板子按 CSV 逐行回放；当前固件是“生成 30 条”，不是“回放任意文件”。

---

## 9. 边界

- 本目录全部是模拟数据，不是真实 OBD/CAN 数据；不能当实测证据。
- 不写 data/processed，不进 E₀ 主账，不进 28 项闸门。
- lat/lng 是 WGS84；高德底图是 GCJ-02，直接叠加会有偏移。
- 10,000 条是 10 台车各 2.78 小时，不是一趟 27 小时；跨车累计值不能直接求单调。
- 碳排只算柴油 TTW；没有包含 WTT、DEF、机油、轮胎等。

---

## 10. 复现

~~~
cd D:/oceanbase02/2027-challenge-cup
PYTHONUTF8=1 python -m tools.gen_obd_sim --out data/sim
~~~

输出会打印 10,000 条的里程、油耗、碳排、EF 范围；生成器内置 validate()，不满足物理约束会直接失败。
