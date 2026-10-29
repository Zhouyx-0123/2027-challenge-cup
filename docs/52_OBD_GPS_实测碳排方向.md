# 52 · OBD + GPS 实测碳排通道（工科测量方向）

> 状态：**已实现并可演示**（2026-10-29）
> 边界：**不改动 E₀ 主口径、不改动主闸门 28 条判据**。本通道是并列的新证据链。
> 来源：与吴老师的项目对接会（录音整理）—— "先测出来"，工科方向接 OBD + GPS。

---

## 1. 为什么改方向

原项目两条线都是**间接算碳**：

| 线 | 数据 | 性质 | 短板 |
|---|---|---|---|
| 发票油耗 | 财务报销油单（DeepSeek OCR） | 实测但滞后、可对账 | 不是燃烧的实时读数；票额与实际消耗可能对不上 |
| VSP 估算 | 载重 + 速度 + 排放因子表 | 模型拟合 | 没人说得清准不准；没有实测锚点 |

吴老师的判断很直接：**"你们算出来了，但谁不知道准不准"**。会计口径上，
算出的油耗没有交叉验证就失去可信度。所以要引入工科侧的**实测**作为第三条腿。

同时老师给了一个关键的工程判断，把技术路线一下收窄了：

> "测尾气流量那套设备很大、不可携。**但其实测油耗就行**——柴油和汽油的
> 油耗里边的碳是很稳定的，消耗一升燃油排多少 CO₂ 有具体的系数，不会很大波动。"

于是路线成立：**不测尾气，测油耗；碳由守恒法推出来。**

> "你测的和这些估的能不能对得上？如果对不上，那你这个估的可能就不用了。"

—— 这条是**下一阶段**的事（本轮只算实测碳，见 §8）。

---

## 2. 边界（最要紧的一条）

本通道是**独立轨道**，不是主账的替代：

- **不改** `src/pipeline.py` 的任何口径、闸门目标与对拍值（28 条判据保持冻结）；
- **不写** 主链路结果表（`wide_data` / `baseline_carbon` / `final_report` 一律不碰）；
- 结果表单独落在 `data/processed/obd_*.parquet`，前端只读；
- 流水线失败**不阻断**主链路（与可选段 T7 同一套降级纪律）；
- 页面顶部常驻声明「**不进 E₀ 主账 · 不参与主闸门**」，避免评委误以为主账被改过。

> 测试里有一条专门守这条线：
> `tests/test_obd_gps.py::test_obd_pipeline_writes_its_own_tables_only`
> 会在跑 OBD 流水线前后对三个主结果表做 sha256 比对，有一个字节变了就红。

---

## 3. 科学路径：燃料守恒法

```
CO₂(kg) = V(L) × ρ(kg/L) × w_C(质量含碳率) × (M_CO₂ / M_C)
M_CO₂ / M_C = 44.01 / 12.011 ≈ 3.6640
```

柴油：ρ = 0.835 kg/L，w_C = 0.865 → 推导值 **2.6465 kgCO₂/L**
权威因子：**2.63 kgCO₂/L**（GB/T 2589 / IPACC，出自 `config/calc_factors.json`）
两者偏差 **+0.628%**（容差 3%）—— 差异来自参考条件不同：推导用元素组成与常温密度，
权威因子用标准燃料。这个偏差会作为 `factor_consistency_pct` 列**随每条数据一起落表**，
可核查、可审计。

### 出账因子唯一真源

本通道**不自造任何因子**。CO₂/L 只从 `config/calc_factors.json` 的 `co2_factors` 读。
`config/obd_carbon_factors.json` 只放守恒推导所需的**基础物理量**（密度、含碳率、
摩尔比）与派生校验值，两处不重复维护同一个数值。

---

## 4. 数据契约

契约文件：`config/obd_gps_contract.json`（`obd-gps-contract-v1`）。
设备端与后端共用这一份，**两边不各写一套**。

### 硬要求

> **必须有油耗字段。** `instant_fuel_lph`（瞬时油耗 L/h）与 `total_fuel_l`
> （累计油耗 L）**至少要有一个**；两个都有时优先用累计油耗差分（仪表累计更稳）。
> 没有油耗的记录直接判**不可用**，进 rejects 表并记原因 —— **不填 0、不猜**。

### 字段

| 字段 | 单位 | 必填 | 说明 |
|---|---|---|---|
| `ts` | ISO8601+tz | ✅ | 采样时刻，必须带时区偏移 |
| `vehicle_id` | — | ✅ | 车辆唯一标识（如 TRK-GZ-001） |
| `plate` | — | | 车牌，属可识别信息，台账/报告须脱敏 |
| `instant_fuel_lph` | L/h | ⭕¹ | 瞬时油耗（守恒法主输入） |
| `total_fuel_l` | L | ⭕¹ | 累计油耗（优先用其差分） |
| `fuel_type` | — | | diesel / gasoline / lpg / cng / electric / hybrid |
| `engine_rpm` | rpm | | 发动机转速 |
| `vehicle_speed_kmh` | km/h | | 车速（与 GPS 速度互为旁证） |
| `throttle_pos_pct` | % | | 节气门开度 |
| `engine_load_pct` | % | | 发动机负荷 |
| `fuel_level_pct` | % | | 燃油液位（仅旁证，**不用于算碳**） |
| `engine_temp_c` | °C | | 冷却液温度（冷态读数单独标记） |
| `intake_press_kpa` / `atm_press_kpa` | kPa | | 进气压力 / 大气压力 |
| `odo_km` | km | | 总里程（**里程的最优来源**） |
| `lat` / `lng` | 十进制度 | | WGS-84 位置 |
| `gps_speed_kmh` | km/h | | GPS 速度 |
| `heading_deg` | ° | | 航向角 |
| `altitude_m` | m | | 海拔 |
| `satellites` / `fix_quality` | — | | 定位质量（<4 星或 fix=0 记 `gps_weak`） |
| `load_kg` | kg | | 载重；**有它才能算 gCO₂/t·km**（车载给不了，靠运单/地磅回填） |
| `ignition` | bool | | 点火状态（false 的时段不计油耗） |
| `fault_codes` | — | | ECU 故障码 |
| `qc_flags` | 数组 | | 质控标记位 |

¹ 两者至少要有一个，见「硬要求」。

### 别名映射

设备侧命名五花八门，契约里 `aliases` 段做了统一，**规范名优先级最高**：

| 设备侧可能写法 | 规范名 |
|---|---|
| `car_id` / `vehicleid` / `设备编号` | `vehicle_id` |
| `rpm` / `engine_speed` / `转速` | `engine_rpm` |
| `speed` / `spd` / `车速` | `vehicle_speed_kmh` |
| `fuel_rate` / `lph` / `瞬时油耗` | `instant_fuel_lph` |
| `total_fuel` / `累计油耗` | `total_fuel_l` |
| `odo` / `mileage` / `总里程` | `odo_km` |
| `latitude` / `纬度` | `lat` |
| `gps_speed` / `航向` | `gps_speed_kmh` / `heading_deg` |

归一规则：字段名转小写、去掉所有非字母数字与非中文字符后比对，
所以 `instant_fuel_lph` / `instant fuel L/h` / `InstantFuelLph` 落到同一个键。

### 质控标记

`clock_drift` · `gps_weak` · `speed_mismatch` · `fuel_type_default` · `fuel_negative` ·
`odometer_reset` · `ecu_fault` · `engine_cold` · `impossible_fuel` · `duplicate` ·
`gap_fill` · `speed_reset`

---

## 5. 计算口径

`src/obd_gps.py`（纯函数、可单测、不算账）：

| 环节 | 规则 |
|---|---|
| 油耗来源 | 优先 `total_fuel_l` 差分；无累计值时用 `instant_fuel_lph × Δt` 积分 |
| 里程来源 | 优先 `odo_km` 差分；无总里程时用 GPS 轨迹积分（复用 `src.routing.coord.haversine_km`） |
| 累计值回退 | 仪表复位/换表 → 记 `fuel_negative` / `odometer_reset` 并断开该步，**不产生负油耗** |
| 缺样 | 相邻间隔 > 120 s 视为缺样，该步不参与积分 |
| 熄火 | `ignition=false` 的时段不计油耗 |
| 电车 | `fuel_type=electric` **不产碳**（电网因子是另一条通道），只留轨迹里程 |
| 越界值 | 置空 + 记 `out_of_range`，**不静默夹到边界** |
| 行程切分 | 相邻间隔 > 15 min 视为新行程 |

派生列：`dt_s` · `fuel_l_step` · `fuel_source` · `dist_step_km` · `dist_source` ·
`kg_co2` · `er_gs`（g/s）· `co2_gkm`（g/km）· `trip_id`
平滑列（**只供曲线图**，原始值始终保留）：`er_gs_smooth`（60 s 时间窗）·
`co2_gkm_window`（**1 km 距离窗**）

> **为什么碳强度用距离窗而不是逐点**：逐点 g/km 在停车起步时（单点里程趋近 0）
> 会爆到几万 g/km，那是除法噪声不是物理量。距离窗口给出「每跑 1 km 累计排了多少碳」，
> 怠速段不跑里程却持续排碳，会以曲线抬升的形式显出来 —— 这正是怠速浪费的直观证据。

### 每车聚合（**每台车 / 每台发动机的碳排放**）

`vehicle_id` · `plate` · `fuel_type` · `n_readings` · `n_valid` · `duration_h` ·
`dist_km` · `fuel_l` · `kg_co2` · `t_co2e` · `co2_gkm` · `co2_g_tkm` · `tonne_km` ·
`idle_ratio`（怠速时长占比）· `er_gs_mean` / `er_gs_max` · `factor_kg_per_l` ·
`factor_source` · `factor_consistency_pct` · `valid_rate` · `n_flagged` · `n_trips`

---

## 6. 落地位置

| 文件 | 职责 |
|---|---|
| `config/obd_gps_contract.json` | 数据契约（字段、别名、质控、接入方式） |
| `config/obd_carbon_factors.json` | 守恒法基础物理量与派生校验值 |
| `src/obd_gps.py` | 归一校验 + 守恒法算碳 + 分车聚合 + 确定性演示序列 |
| `src/obd_pipeline.py` | 原始流 → 结果表（独立于主 pipeline） |
| `server.py` | `POST /api/upload_obd_gps`（复用令牌鉴权 / 限流 / 审计） |
| `app/services.py` | 只读结果表（**不 import 算法**） |
| `app/components/obd_panel.py` | 前端面板（云边端监控页第二个 tab） |
| `tests/test_obd_gps.py` | 27 个用例：契约 / 守恒 / 聚合 / 边界 / 接收端 / 前端渲染 |

### 接收端

`POST /api/upload_obd_gps`，与传感器通道**同一套四层防护**：

1. **设备身份** —— `X-Device-Token` + 设备注册表，默认 fail-closed；一批里有多台车
   则逐台校验，**任一台未授权则整批拒收**（不部分落盘）；
2. **限流** —— 每设备每分钟 60 次；
3. **契约校验** —— 走 `normalize_records`，缺油耗直接判不可用；
4. **审计** —— 落 JSONL，**绝不记令牌**。

落盘：`data/raw/obd_gps/obd_stream.csv`（规范字段名），
不可用记录另存 `obd_rejects.csv`（带原因），设备端可自查。

---

## 7. 前端

**位置**：云边端硬件监控页的第二个 tab「🚘 OBD+GPS 实测碳排」，
与原有「🌫️ CO₂ 环境浓度」通道并列（口径完全不同，不混在一张图里）。

内容：

- 通道状态条（来源 / 契约版本 / 守恒因子版本 / 口径因子版本 / 与主账的边界）
- 车队指标卡（车辆数、总碳排 tCO₂e、车队碳强度 gCO₂/km、总里程、总油耗）
- 口径说明框（守恒公式 + 出账因子来源 + 边界声明）
- 单车选择器 + 本车指标（碳排 / 里程 / 油耗 / 怠速占比）
- 四张图：累计碳排 + 排放率双轴、工况三联图（车速 / 瞬时油耗 / 转速）、
  碳强度距离窗曲线、GPS 轨迹（高德 JS API 2.0 暗色地图；无 Key 时退回经纬度散点）
- 每车碳排聚合表、行程明细、发动机参数统计、数据质量与质控、接入方式与契约原文

**前端只读不算**：所有数字来自 `data/processed/obd_*.parquet`，
由 `python -m src.obd_pipeline` 预计算。`app/services.py` 的 AST 级测试
确保它**没有 import** 守恒法算法。

### 演示数据

`python -m src.obd_pipeline --demo` 生成**确定性**演示序列（两台车：
重卡 TRK-GZ-001 / 轻卡 TRK-BJ-002），页面显式标注「演示数据」。

演示数据**与项目自家因子表对得上**（`tests/test_obd_gps.py` 有断言守着）：

| 指标 | 演示值 | 项目因子基准 |
|---|---|---|
| 重卡碳强度 | 1042 g/km | 30 t / v=50 → 1055.952 g/km |
| 重卡油耗 | 39.6 L/100km | 重载卡车混合工况 |
| 巡航排放率 | ≈ 14.8 g/s | 30 t / VSP=2 → 14.67 g/s |
| 峰值排放率 | 25.0 g/s | 爬坡高 VSP 段 |
| 怠速占比 | 15.2% | 真实车队 10%–25% |

> **2026-10-29 修正**：演示生成器原先用**未缩放的原始速度**累计里程，而报表速度与油耗都乘了
> `scale` —— 轻卡 TRK-BJ-002（scale 0.62）的里程因此虚高 **61.3%**，碳强度被低估 38%
> （646 → 实际 1042 g/km）。已修为 `step_km = speed * scale * Δt`，
> 并加了两条回归断言（`tests/test_obd_vsp_crosscheck.py`）：
> **里程必须与报表速度同口径**、**两台演示车的碳强度须落在同一量级**。
> 上表数字是**重卡**（scale 1.0），未受该缺陷影响，保持成立。

---

## 对照验证轨（docs/57 增补）

演示数据修好后，新增了一条**对照验证轨**回答老师的判据
（「你测的和这些估的能不能对得上？对不上，那估的可能就不用了。」）：

| 轨 | 方法 | 结果表 |
|---|---|---|
| **实测轨** | 碳平衡法（守恒法）—— OBD 只测油耗，碳由守恒系数推出 | `obd_points.parquet`（既有） |
| **模型轨** | VSP：**速度剖面积分** + 平均速度法（对照） | `obd_vsp_crosscheck*.parquet` |

```bash
python -m src.obd_vsp_crosscheck
```

**当前演示数据下的结果**：模型（速度剖面）vs 实测 **+3.75%**，落在 ±15% 容差内 → **对得上**。
但**载重是扫描假设**（演示序列不填 `load_kg`），所以这个结论在**真实载重接入前不构成兜底结论**。

---

## 8. 下一阶段（尚未实现，留好接口）

1. **三轨交叉验证** —— OBD 实测 vs VSP 估算 vs 发票油耗。
   老师原话："测的和估的能不能对得上；对不上，估的可能就不用了。"
   → 需要：同车同路段的实测与估算对齐口径、偏差分布、结论判定。
2. **载重回填** —— `load_kg` 由运单/地磅按时间段回填，才能出 gCO₂/t·km
   （老师强调"视角要放到货物上：拉 10 吨比拉 1 吨更节能"）。
3. **真实 OBD 设备接入** —— 需与工科同学确认：
   柴油车能否取到 **OBD 油耗**（PID 0x01/0x2F 等）、帧率、是否走 J1939/CAN。
   这是本方向能否落地的**唯一关键未知**。
4. **发动机级归因** —— 按转速/负荷分档标定路段系数，替代通用因子表。
5. **规模化存储** —— 企业化后数据量级上去再谈（OceanBase / 分布式），当前文件驱动够用。

---

## 9. 验收

```bash
python -m src.obd_pipeline --demo        # 生成演示结果表
python -m pytest tests/test_obd_gps.py -q # 27 个用例
node tools/verify_obd_tab.js http://localhost:8501 _shots   # tab 视觉与文案验收
```

手工联调：

```bash
python -m tools.device_tokens add --device-id TRK-GZ-001    # 登记设备
python server.py                                           # 起接收端
curl -X POST http://localhost:5000/api/upload_obd_gps \
  -H "Content-Type: application/json" -H "X-Device-Token: <明文>" \
  -d '{"records":[{"timestamp":"2026-10-29T10:00:00+08:00","vehicle_id":"TRK-GZ-001","instant_fuel_lph":24.0,"vehicle_speed_kmh":62.5,"odo_km":100.0,"lat":31.23,"lng":121.47}]}'
python -m src.obd_pipeline        # 读原始流 → 结果表
```

---

## 10. 一句话

**新增一条实测证据链：OBD 实时油耗 → 燃料守恒法 → 每台车（每台发动机）的碳排；
主账口径与 28 条闸门一条没动；交叉验证留给下一阶段。**
