# 62 OBD 稀疏校准与试点方案

版本：OBD-CAL-v1 · 2026-11-06

适用：硬件刚落地、暂时没有大量真实 OBD 数据，但需要给出可解释、可审计的碳计量口径。

## 1. 一句话结论

主账 E0 不依赖 OBD。E0 仍由油票、里程、法定因子计算；OBD 只做校准轨。先验用现有 12,036 趟历史数据，按 5 档速度 × 2 档载重分成 10 层；每层用经验贝叶斯收缩吸收 OBD 样本；样本不足时因子自动退回 1，不伪造减排，也不改 E0。

## 2. 为什么不能等 OBD 铺开

当前只有 2 条 demo 轨迹，data/processed/obd_summary.json 标了 is_demo=true，不能做统计分档。若把 demo 当实测，会同时犯三个错：样本量不足、选择偏差、把验证轨冒充主账。

可行的做法是把问题拆成两件事：

1. 计量：用已有油票和里程算 E0。这条链已经跑通，33,214 运单 → 12,167 趟 → 2,258,438.376 kg。
2. 校准：用少量 OBD 检查历史 EF 画像是否系统性偏高或偏低。OBD 是抽检，不是主账的前提。

这样，硬件没铺开也能先交付；等 OBD 回来，只更新校准因子和置信区间，不重写历史账。

## 3. 证据阶梯

| 层级 | 数据 | 用途 | 当前状态 |
|---|---|---|---|
| L0 | 油票、运单、里程、法定因子 | 主账 E0 | 已跑通 |
| L1 | 12,036 趟历史速度/载重/碳 | 5×2 先验 EF | 已跑通 |
| L2 | 少量 OBD 段级样本 | 校准因子与区间 | 待采集 |
| L3 | 车队连续 OBD | 替换或滚动校准 | 硬件阶段 |

L2 不是有了才能算，而是有了更准。没有 L2 时，报告写先验口径、未校准；有 L2 时，写校准口径、样本量、区间。两种口径不能混着说。

## 4. 先验：5 档速度 × 2 档载重

只取 passed=True 且速度、载重、里程、碳有限的 12,036 趟。EF 用里程加权，不用档位内简单平均：

    prior_ef = sum(carbon_kg) / sum(distance_km) × 1000   [g/km]

| 速度档 | 载重层 | 趟数 | 里程 km | 碳 kg | 先验 EF g/km | 碳占比 |
|---|---|---:|---:|---:|---:|---:|
| <20 | light | 2,736 | 78,369.95 | 36,039.79 | 459.867 | 1.63% |
| <20 | heavy | 57 | 27,407.94 | 37,194.57 | 1,357.073 | 1.68% |
| 20-40 | light | 3,742 | 162,938.47 | 84,480.73 | 518.482 | 3.81% |
| 20-40 | heavy | 125 | 94,898.76 | 126,969.10 | 1,337.943 | 5.73% |
| 40-60 | light | 2,703 | 248,277.37 | 154,600.10 | 622.691 | 6.98% |
| 40-60 | heavy | 324 | 251,816.82 | 341,451.50 | 1,355.952 | 15.41% |
| 60-80 | light | 1,333 | 301,447.11 | 208,351.40 | 691.171 | 9.41% |
| 60-80 | heavy | 811 | 832,556.43 | 1,146,987.00 | 1,377.668 | 51.78% |
| >=80 | light | 187 | 98,424.56 | 58,428.05 | 593.633 | 2.64% |
| >=80 | heavy | 18 | 15,682.05 | 20,431.62 | 1,302.867 | 0.92% |
| 合计 |  | 12,036 | 2,111,819.46 | 2,214,933.44 | 1,048.827 | 100% |

E0 全量是 2,258,438.376 kg；上表只含 passed 的 2,214,933.440 kg，差 43,504.936 kg 是 131 条 invalid，不能悄悄摊进先验。

关键事实：载重比速度更能解释 EF。light 层在 460–691 g/km，heavy 层在 1,303–1,378 g/km；速度从 <20 到 >=80 的变化远小于载重从 light 到 heavy 的变化。因此试点不能只挑快车或慢车，必须覆盖载重层。

## 5. 校准：经验贝叶斯收缩

对每一层，先算 OBD 观测 EF：

    obs_ef = sum(carbon_kg) / sum(distance_km) × 1000

权重：

    w = n / (n + 30)

后验：

    post_ef = w × obs_ef + (1 - w) × prior_ef
    factor = post_ef / prior_ef

区间用近似 95%：

    post_se = sqrt((w × obs_se)^2 + ((1 - w) × prior_sd)^2)
    ci = post_ef ± 1.96 × post_se

其中 prior_sd = 0.15 × prior_ef，obs_se = obs_sd / sqrt(n)。n=10 时 w=25%，n=25 时 w=45.5%，n=50 时 w=62.5%。30 是先验相当于多少趟的强度参数，不是从数据估计出来的；它的作用是防止 3–5 单把因子拉飞。这个参数必须写进报告，不能藏起来。

状态机：

| 状态 | 条件 | 报告写法 |
|---|---|---|
| prior_only | n=0 | 未校准，因子 1.0 |
| provisional | 0<n<10 或 obs_km<200 | 初步校准，样本不足 |
| calibrated | n>=10 且 obs_km>=200 | 校准，附 n 与区间 |
| prior_missing | 先验 EF 缺失 | 回退全局或载重层，单独标注 |
| demo_fixture | 仅 include_demo=True 且全为 demo | 只验证管道，禁止对外 |

calibrated 只是达到运营使用的最低门槛，不是统计上已经证明。25 单对应 CV=25%、相对误差 10% 的目标；如果实际 CV 更大，25 单不够。

## 6. 样本量：每层 25 单，完整试点 250 单

每层样本量：

    n = ceil( (z × CV / margin)^2 )

z=1.96，CV=0.25，margin=0.10：

    n = ceil( (1.96 × 0.25 / 0.10)^2 ) = ceil(24.01) = 25

不同 CV 下的每层样本量：

| 层内 CV | 每层 n | 10 层合计 |
|---:|---:|---:|
| 0.15 | 9 | 90 |
| 0.20 | 16 | 160 |
| 0.25 | 25 | 250 |
| 0.35 | 47 | 470 |

两档试点：

- 最小试点 60 单：每层 6 单。目的不是定稿，而是估出层内 CV、验证采集链路、发现异常层。6 单的因子只能标 provisional。
- 完整试点 250 单：每层 25 单。按 CV=25%、margin=10% 设计，10 层全覆盖。
- 预算受限时可用 carbon 模式：按先验碳量比例分配，高碳层优先。代价是低碳层可能只有 2 单，仍是 provisional；报告必须列出哪些层没校准。

balanced 模式（推荐）的 250 单分配就是每层 25 单；60 单就是每层 6 单。carbon 模式适合只能做 60–100 单、但想先把 60–80 heavy 这 51.78% 的碳校准掉的场景。

## 7. 采集协议

### 7.1 一条样本是什么

一条样本 = 一个 trip 内、按 5×2 分层后的一段有效行驶；不是一帧 OBD，也不是整趟混在一起。

最小字段：

| 字段 | 说明 |
|---|---|
| device_id | 设备编号 |
| vehicle_id / plate_id | 车辆；车牌必须归一化后哈希 |
| ts_utc | 绝对 UTC 时间；固件只有开机秒数时，必须由主机或 GPS 补绝对时间 |
| lat / lon | WGS84 或 GCJ-02，明确坐标系 |
| speed_kmh | 车速 |
| rpm | 发动机转速 |
| torque_pct | 实际扭矩百分比；没有就留空，不猜 |
| fuel_rate_lph | 瞬时燃油率 L/h；没有就留空 |
| odometer_km | 总里程；用于和 GPS 里程互校 |
| load_t | 载重；来自运单，不来自 OBD |
| link_ok | 总线连接状态 |

如果固件只输出 [ 12 s] 这类开机秒数，不能直接和运单 join；必须有采集主机的绝对时间或 GPS 时间。这是当前固件包最大的缺口。

### 7.2 分层与清洗

1. 按 trip 切分：点火/熄火、起终点 geofence、连续停车；不要按自然日切。
2. 只保留行驶段：speed>0 且 dt 在 1–5 s；怠速单独记，不混入行驶 EF。
3. 油耗积分：fuel_l = Σ(fuel_rate_lph × dt / 3600)；没有 fuel_rate 时用 MAF/MAP 估算并标 fuel_source=estimated。
4. 碳：carbon_kg = fuel_l × 2.63（TTW，和 E0 同口径）。
5. 距离：GPS 里程与 ODO 差值互校；偏差 >5% 的段标 distance_suspect。
6. 质量门禁：有效帧覆盖率 <80%、fuel_rate 缺失 >20%、GPS 精度差、全程怠速的段，不进校准。
7. 每层至少 5 台车、3 条不同 OD；不能只测一台车一条线。

### 7.3 选择偏差

OBD 试点最容易犯的错，是只测好开的线或新车。应按层做配额，而不是先到先得；每层记录拒绝或缺失原因，期末做一次已采样 vs 未采样的里程、载重、路线对比。若偏差大，校准因子只能用于同分布层，不能外推全车队。

## 8. 接入与报告

### 8.1 不改 E0

src/obd_calibration.py 的输出是并列列，不是替换：

- carbon_kg：主账，原样保留。
- carbon_kg_calibrated：场景列，等于 carbon_kg × factor。
- calibration_factor、calibration_status、calibration_speed_bin、calibration_load_group：审计列。

任何报表都必须同时写主账口径和校准口径。不能只放校准后的数，也不能把因子 1.0 说成已验证。

### 8.2 判定规则

- 区间跨 1：没有证据表明先验有系统偏差，继续用先验，因子记 1.0。
- 区间不跨 1，且 n>=25：登记为疑似系统偏差，先查油票、里程、载重、怠速，不直接改 E0。
- 因子偏离 >10% 且复核成立：下一年度或下一报告期更新 EF 画像；历史 E0 不追溯改，除非油票本身错。
- 因子偏离 >20%：暂停该层对外结论，回查采集链路。

### 8.3 与 Q-learning 的关系

Q-learning 现在的 HistoricalProfile 用的是主账 carbon_kg 的 5×4 EF 画像。OBD 校准后，若要用于路线决策，应：

1. 用 carbon_kg_calibrated 重建 EF 画像（场景版，不覆盖主账）。
2. 重训或重放 Q 策略，比较选中路线是否变化。
3. 报告主账策略和校准情景策略的差异；不能把情景当已实现减排。

## 9. 当前运行结果

没有真实 OBD 样本，因此：

- 10 层全部 prior_only，factor=1.0，weighted_factor=1.0。
- carbon_kg_calibrated 合计（passed）= 2,214,933.439918 kg，与原 carbon_kg 一致。
- 全量（含 131 invalid）仍是 2,258,438.3762076 kg；invalid 不参与校准，也不写 0。
- pilot 默认 balanced 250 单：每层 25 单，合计 250。

输出目录 outputs/obd_calibration/：

| 文件 | 内容 |
|---|---|
| prior_5x2.csv | 10 层先验：趟数、里程、碳、EF |
| calibration_5x2.csv | 10 层后验：n、obs EF、权重、factor、CI、状态 |
| pilot_allocation.csv | 试点分配：carbon_share、target_n、allocated_n、margin |
| trip_carbon_calibrated.parquet | 趟级 5 列：trip_id、carbon_kg、carbon_kg_calibrated、factor、status |
| audit.json | 校准、试点、应用三段审计 |

运行：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONPATH=. python -m src.obd_calibration --out outputs/obd_calibration

有样本时：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONPATH=. python -m src.obd_calibration --samples path/to/obd_segments.parquet --out outputs/obd_calibration

预算受限时加 --mode carbon --total 100；60 单最小试点用 --total 60。

## 10. 验证

tests/test_obd_calibration.py 9 项：

1. 先验 EF 是里程加权，不是档位平均。
2. 缺列报错。
3. 无样本时 factor=1，状态 prior_only。
4. 10 单样本按 w=n/(n+30) 收缩，post=1250，factor=1.25。
5. 4 单样本标 provisional。
6. demo 默认剔除；include_demo=True 才标 demo_fixture。
7. 每层 25 单、60/250 分配与 carbon 模式。
8. fuel_l 按 2.63 转 carbon_kg。
9. apply_calibration 不改原 carbon_kg，invalid 不写 0。

运行：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 PYTHONPATH=. python -m pytest tests/test_obd_calibration.py -q

## 11. 边界与风险

- 当前没有真实 OBD，所有 factor=1.0；这是未校准，不是已验证无偏差。
- demo 轨迹只能验证管道，不能进统计。
- 30 是先验强度参数，不是从 OBD 数据估计的；换数据集要重新论证。
- 95% 区间是正态近似，不是严格贝叶斯后验；小 n 时区间偏窄。
- 速度是 trip 均速，不是逐段速度；同趟多段仍切不出来。
- 载重来自运单，不是称重；载重错分会直接错层。
- 采集协议依赖绝对时间；当前固件只有开机秒数，必须先补主机或 GPS 时间。
- 校准因子只对同分布层有效，不能把 25 单的结论外推到全部 5,868 台车。
- 不改 E0、不进 28 闸门、不写 data/processed；这是场景和审计模块。

## 12. 下一步

1. 用 60 单最小试点跑通采集、切段、积分、清洗、校准全链路；每层 6 单，先估 CV。
2. 若 CV<=25%，补到 250 单；若 CV>35%，样本量按 470 单重算，或先查采集误差。
3. 把 calibration_5x2.csv 接进答辩材料：先验表、校准表、区间图、未校准声明。
4. 若要把校准用于 Q-learning，先做场景版 EF 画像和策略对比，不覆盖主账。
5. 等硬件铺开，再把 L2 升级为 L3 连续校准；届时 E0 仍由油票主账，OBD 负责验证。

## 13. 相关文件

- 代码：src/obd_calibration.py
- 测试：tests/test_obd_calibration.py
- 先验/校准口径：docs/60_路段主表与聚合口径.md
- 真实路网 Q 演示：docs/61_真实路网Q学习演示.md
- 单页图：D:\oceanbase02\可视化_OBD校准方案_20261106.png；脚本 outputs/report/obd_calibration_overview.py
