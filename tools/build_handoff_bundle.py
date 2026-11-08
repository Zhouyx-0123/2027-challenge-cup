"""生成对外交付包：财务发票 + OBD 实测算碳结果（2026-11-08）。

输入（全部只读）：
  data/sim/excel_10000/fuel_invoices.csv      39 张加油发票（模拟）
  data/sim/excel_10000/obd_stream_10000.csv   板子格式原始流 10,000 条（模拟）
  data/processed/obd_{points,trips,vehicle_carbon}.parquet + obd_summary.json
输出：
  <repo>/交付_20261108_财务与碳账/
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "交付_20261108_财务与碳账"
SIM = ROOT / "data" / "sim" / "excel_10000"
PROC = ROOT / "data" / "processed"

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _style(ws, widths, freeze="A2", filt=True):
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = freeze
    if filt and ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = BORDER


def _write(ws, frame, widths, number_formats=None, filt=True):
    for j, col in enumerate(frame.columns, start=1):
        ws.cell(row=1, column=j, value=col)
    for i, values in enumerate(frame.itertuples(index=False), start=2):
        for j, value in enumerate(values, start=1):
            ws.cell(row=i, column=j, value=value)
    _style(ws, widths, filt=filt)
    for key, fmt in (number_formats or {}).items():
        if key in list(frame.columns):
            idx = list(frame.columns).index(key) + 1
            for row in range(2, ws.max_row + 1):
                ws.cell(row=row, column=idx).number_format = fmt


def _notes(ws, title, lines, width=110):
    ws.column_dimensions["A"].width = width
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=13, color="1F4E79")
    for i, line in enumerate(lines, start=3):
        ws.cell(row=i, column=1, value=line).alignment = Alignment(wrap_text=True, vertical="top")
    ws.sheet_view.showGridLines = False


def main() -> None:
    OUT.mkdir(exist_ok=True)
    inv = pd.read_csv(SIM / "fuel_invoices.csv", encoding="utf-8-sig")
    pts = pd.read_parquet(PROC / "obd_points.parquet")
    trips = pd.read_parquet(PROC / "obd_trips.parquet")
    veh = pd.read_parquet(PROC / "obd_vehicle_carbon.parquet")

    # ---------- 财务：发票 ----------
    inv_cn = inv.rename(columns={
        "invoice_no": "发票号码", "invoice_date": "开票日期", "invoice_time": "开票时间戳",
        "vehicle_id": "车辆编号", "plate": "车牌", "item_name": "项目", "unit": "单位",
        "fuel_liters": "数量(L)", "unit_price": "单价(元/L)",
        "amount_excluding_tax": "不含税金额(元)", "tax_rate": "税率",
        "tax_amount": "税额(元)", "invoice_amount": "价税合计(元)",
        "buyer_name": "购买方", "seller_name": "销售方", "invoice_type": "发票类型",
        "source": "数据来源",
    })[["发票号码", "开票日期", "开票时间戳", "车辆编号", "车牌", "项目", "单位",
        "数量(L)", "单价(元/L)", "不含税金额(元)", "税率", "税额(元)", "价税合计(元)",
        "购买方", "销售方", "发票类型", "数据来源"]]

    inv_plate = (inv.groupby(["vehicle_id", "plate"], as_index=False)
                 .agg(发票张数=("invoice_no", "count"), 加油量_L=("fuel_liters", "sum"),
                      价税合计_元=("invoice_amount", "sum"),
                      首次开票=("invoice_date", "min"), 末次开票=("invoice_date", "max")))
    inv_plate = inv_plate.rename(columns={"vehicle_id": "车辆编号", "plate": "车牌"})
    inv_plate["加油量_L"] = inv_plate["加油量_L"].round(3)
    inv_plate["价税合计_元"] = inv_plate["价税合计_元"].round(2)
    total_row = pd.DataFrame([{
        "车辆编号": "合计", "车牌": "", "发票张数": int(len(inv)),
        "加油量_L": round(float(inv.fuel_liters.sum()), 3),
        "价税合计_元": round(float(inv.invoice_amount.sum()), 2),
        "首次开票": inv.invoice_date.min(), "末次开票": inv.invoice_date.max(),
    }])
    inv_plate = pd.concat([inv_plate, total_row], ignore_index=True)

    inv_file = OUT / "01_财务同学_加油发票_39张.xlsx"
    with pd.ExcelWriter(inv_file, engine="openpyxl") as writer:
        _write(writer.book.create_sheet("发票明细"), inv_cn,
               [15, 12, 24, 14, 11, 8, 6, 10, 11, 14, 8, 10, 13, 26, 34, 14, 12],
               {"数量(L)": "0.00", "单价(元/L)": "0.00", "不含税金额(元)": "#,##0.00",
                "税额(元)": "0.00", "价税合计(元)": "#,##0.00"})
        _write(writer.book.create_sheet("按车牌汇总"), inv_plate,
               [14, 11, 10, 12, 14, 12, 12],
               {"加油量_L": "0.000", "价税合计_元": "#,##0.00"})
        _notes(writer.book.create_sheet("字段说明"), "加油发票 · 字段说明与来源", [
            "1. 本表是模拟数据（source=sim_excel_v1），用于财务同学测试「发票 → 加油量 → 油耗/成本」链路，不是真实票据。",
            "2. 39 张票，覆盖 8 台车、2026-10-31 至 2026-11-05；税率 13%，购买方「上海碳感知物流有限公司」。",
            "3. 开票时间戳带 +08:00；数量单位统一为 L（升），不是 kg，也不是元。",
            "4. 价税合计 = 不含税金额 + 税额；单价 = 不含税金额 / 数量（2 位小数四舍五入，回算可能有 ±0.01 元误差）。",
            "5. 已知构造偏差（测试答案）：苏A12346 多开票约 35%、苏A12348 多开票约 18%、苏A12350 少一张 11-01 的票。",
            "6. 与项目现有财务主表（33 列）字段对应：发票号码→invoice_no、车牌→plate、数量(L)→fuel_liters、价税合计→invoice_amount。",
        ])

    inv.to_csv(OUT / "01_财务同学_加油发票_39张.csv", index=False, encoding="utf-8-sig")

    # ---------- 碳账：结果 ----------
    veh_cn = veh.rename(columns={
        "vehicle_id": "车辆编号", "plate": "车牌", "fuel_type": "燃料",
        "n_readings": "读数点数", "n_valid": "有效点数", "duration_h": "时长(h)",
        "dist_km": "里程(km)", "fuel_l": "油耗(L)", "kg_co2": "碳排(kgCO2e)",
        "t_co2e": "碳排(tCO2e)", "co2_gkm": "碳强度(g/km)", "co2_g_tkm": "碳强度(g/t·km)",
        "tonne_km": "周转量(t·km)", "idle_ratio": "怠速占比", "er_gs_mean": "平均排放率(g/s)",
        "er_gs_max": "峰值排放率(g/s)", "factor_kg_per_l": "因子(kgCO2/L)",
        "factor_source": "因子来源", "factor_consistency_pct": "因子一致性(%)",
        "valid_rate": "有效率", "n_flagged": "质控标记数", "n_trips": "行程数",
        "first_ts": "首点时间", "last_ts": "末点时间",
    })
    veh_cn = veh_cn[["车辆编号", "车牌", "燃料", "读数点数", "有效点数", "行程数", "时长(h)",
                     "里程(km)", "油耗(L)", "碳排(kgCO2e)", "碳排(tCO2e)", "碳强度(g/km)",
                     "碳强度(g/t·km)", "周转量(t·km)", "怠速占比", "平均排放率(g/s)",
                     "峰值排放率(g/s)", "因子(kgCO2/L)", "因子来源", "因子一致性(%)",
                     "有效率", "质控标记数", "首点时间", "末点时间"]].copy()
    for col in ("首点时间", "末点时间"):
        veh_cn[col] = pd.to_datetime(veh_cn[col], errors="coerce", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")
    for col in ("里程(km)", "油耗(L)", "碳排(kgCO2e)", "碳排(tCO2e)", "碳强度(g/km)",
                "碳强度(g/t·km)", "周转量(t·km)", "平均排放率(g/s)", "峰值排放率(g/s)",
                "因子(kgCO2/L)", "因子一致性(%)", "怠速占比", "有效率"):
        veh_cn[col] = pd.to_numeric(veh_cn[col], errors="coerce").round(4)

    trips_cn = trips.rename(columns={
        "vehicle_id": "车辆编号", "trip_id": "行程号", "start_ts": "开始时间(UTC)",
        "end_ts": "结束时间(UTC)", "n_readings": "点数", "dist_km": "里程(km)",
        "fuel_l": "油耗(L)", "kg_co2": "碳排(kgCO2e)", "co2_gkm": "碳强度(g/km)",
    }).copy()
    for col in ("里程(km)", "油耗(L)", "碳排(kgCO2e)", "碳强度(g/km)"):
        trips_cn[col] = pd.to_numeric(trips_cn[col], errors="coerce").round(4)
    for col in ("开始时间(UTC)", "结束时间(UTC)"):
        trips_cn[col] = pd.to_datetime(trips_cn[col], errors="coerce", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")

    pts_cn = pts.rename(columns={
        "ts": "时间(UTC)", "vehicle_id": "车辆编号", "plate": "车牌",
        "instant_fuel_lph": "瞬时油耗(L/h)", "total_fuel_l": "累计油耗(L)",
        "vehicle_speed_kmh": "车速(km/h)", "engine_rpm": "转速(rpm)",
        "engine_load_pct": "发动机负荷(%)", "load_kg": "载重(kg)", "odo_km": "总里程(km)",
        "dist_step_km": "本步里程(km)", "fuel_l_step": "本步油耗(L)",
        "kg_co2": "本步碳排(kgCO2e)", "co2_gkm": "本步碳强度(g/km)",
        "factor_kg_per_l": "因子(kgCO2/L)", "qc_flags": "质控标记", "trip_id": "行程号",
    })[["时间(UTC)", "车辆编号", "车牌", "瞬时油耗(L/h)", "累计油耗(L)", "车速(km/h)",
        "转速(rpm)", "发动机负荷(%)", "载重(kg)", "总里程(km)", "本步里程(km)",
        "本步油耗(L)", "本步碳排(kgCO2e)", "本步碳强度(g/km)", "因子(kgCO2/L)",
        "质控标记", "行程号"]].copy()
    for col in ("瞬时油耗(L/h)", "累计油耗(L)", "本步里程(km)", "本步油耗(L)",
                "本步碳排(kgCO2e)", "本步碳强度(g/km)"):
        pts_cn[col] = pd.to_numeric(pts_cn[col], errors="coerce").round(5)
    pts_cn["时间(UTC)"] = pd.to_datetime(pts_cn["时间(UTC)"], errors="coerce", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S")

    carbon_file = OUT / "02_碳账同学_OBD碳排结果.xlsx"
    with pd.ExcelWriter(carbon_file, engine="openpyxl") as writer:
        _write(writer.book.create_sheet("① 车级碳排_8台"), veh_cn,
               [13, 10, 7, 10, 10, 8, 9, 11, 10, 13, 11, 13, 14, 13, 10, 13, 13, 12, 30, 12, 9, 10, 22, 22],
               {"里程(km)": "0.00", "油耗(L)": "0.000", "碳排(kgCO2e)": "0.000",
                "碳排(tCO2e)": "0.000000", "碳强度(g/km)": "0.0", "因子(kgCO2/L)": "0.00",
                "怠速占比": "0.0%", "有效率": "0.0%"})
        _write(writer.book.create_sheet("② 趟级碳排_40趟"), trips_cn,
               [13, 26, 22, 22, 8, 10, 10, 12, 12],
               {"里程(km)": "0.00", "油耗(L)": "0.000", "碳排(kgCO2e)": "0.000", "碳强度(g/km)": "0.0"})
        _write(writer.book.create_sheet("③ 逐点碳排_10000条"), pts_cn,
               [22, 13, 10, 13, 12, 11, 9, 12, 9, 12, 11, 11, 13, 14, 12, 18, 12],
               {"瞬时油耗(L/h)": "0.00", "累计油耗(L)": "0.000", "本步里程(km)": "0.000",
                "本步油耗(L)": "0.00000", "本步碳排(kgCO2e)": "0.00000",
                "本步碳强度(g/km)": "0.0"})
        _notes(writer.book.create_sheet("④ 口径与验算"), "OBD 实测碳排 · 口径与验算步骤", [
            "1. 本表是模拟数据（8 台车 × 5 天 × 250 点，30 s 采样，共 10,000 条），由 tools/gen_obd_excel.py 生成，不是现场读数。",
            "2. 算法：燃料守恒法 CO2(kg) = 油耗(L) × ρ × w_C × (M_CO2/M_C)，本批直接用因子 2.63 kgCO2/L（柴油 TTW，GB/T 2589 / IPACC）。",
            "3. 油耗优先取「累计油耗差分」（total_fuel_l 的步间增量），无累计值时用「瞬时油耗积分」（instant_fuel_lph × dt/3600）。",
            "4. 验算：③ 表「本步碳排」= 本步油耗 × 2.63，逐行成立；按车求和应等于 ① 表「碳排(kgCO2e)」：",
            "     合计 2,524.7679 kgCO2e / 3,921.34 km / 959.988 L / 643.85 g·km-1。",
            "5. 时间列统一为 UTC（原始报文为 +08:00，网关落盘时就地归一），做日报/月报时注意 +8 h 偏移。",
            "6. 边界：本轨不写入 E0 主账、不参与 28 条闸门；E0 2,258,438.376 kg 来自京东运单主链，与本表不可相加。",
            "7. 与财务发票对比时：发票是「买油」，本表是「用油」，两者只能在同车同期同口径（L 或同为 TTW）下比；发票若用 3.16 kgCO2/L（TTW+WTT）会天然高出 20.2%。",
        ])

    pts_cn.to_csv(OUT / "02_碳账同学_逐点碳排_10000条.csv", index=False, encoding="utf-8-sig")
    veh_cn.to_csv(OUT / "02_碳账同学_车级碳排_8台.csv", index=False, encoding="utf-8-sig")
    trips_cn.to_csv(OUT / "02_碳账同学_趟级碳排_40趟.csv", index=False, encoding="utf-8-sig")

    # ---------- 对账样例 ----------
    rec = (inv.groupby("plate", as_index=False)
           .agg(发票张数=("invoice_no", "count"), 发票加油量_L=("fuel_liters", "sum"),
                发票金额_元=("invoice_amount", "sum")))
    obd = veh[["plate", "dist_km", "fuel_l", "kg_co2"]].rename(columns={
        "plate": "车牌", "dist_km": "实测里程_km", "fuel_l": "实测油耗_L", "kg_co2": "实测碳_kg"})
    rec = rec.rename(columns={"plate": "车牌"}).merge(obd, on="车牌", how="outer")
    rec["差额_L"] = (rec["发票加油量_L"] - rec["实测油耗_L"]).round(3)
    rec["偏差_%"] = (rec["差额_L"] / rec["实测油耗_L"] * 100).round(2)
    rec["实测碳强度_g_km"] = (rec["实测碳_kg"] * 1000 / rec["实测里程_km"]).round(1)
    rec = rec[["车牌", "发票张数", "发票加油量_L", "实测油耗_L", "差额_L", "偏差_%",
               "发票金额_元", "实测里程_km", "实测碳_kg", "实测碳强度_g_km"]]
    for col in ("发票加油量_L", "实测油耗_L", "实测碳_kg"):
        rec[col] = pd.to_numeric(rec[col], errors="coerce").round(3)
    rec = rec.sort_values("车牌")

    rec_file = OUT / "03_对账样例_发票vs实测.xlsx"
    with pd.ExcelWriter(rec_file, engine="openpyxl") as writer:
        _write(writer.book.create_sheet("按车牌对账"), rec,
               [10, 9, 14, 12, 10, 9, 12, 13, 12, 16],
               {"发票加油量_L": "0.000", "实测油耗_L": "0.000", "差额_L": "0.000",
                "偏差_%": "0.00", "发票金额_元": "#,##0.00", "实测里程_km": "0.00",
                "实测碳_kg": "0.000", "实测碳强度_g_km": "0.0"})
        _notes(writer.book.create_sheet("已知答案（测试用）"), "对账样例 · 构造偏差答案", [
            "这批数据在生成时故意注入了三种偏差，用来验证对账逻辑能否发现——「答案」如下，测试时先别看：",
            "",
            "  · 苏A12346：发票加油量比实测高约 34.9%  -> 应判「异常·偏高」（多开票 / 代加油 / 记账错误）",
            "  · 苏A12348：发票加油量比实测高约 17.8%  -> 应判「偏高」",
            "  · 苏A12350：少一张 2026-11-01 的发票      -> 偏差约 -9.4%，属「缺覆盖」而非「偷油」",
            "  · 其余 5 台：偏差在 ±5% 内，应判「正常」",
            "",
            "对账口径（重要）：",
            "  1. 先比 L（升），不要直接比元或比碳——单价和税率会引入噪声。",
            "  2. 发票=买油、实测=用油；库存变化、非道路用油、记账滞后都会造成差额，所以判据是「差额能否解释」，不是「必须相等」。",
            "  3. 要折算碳时两条线必须同口径：发票若用 3.16（TTW+WTT）、实测是 2.63（TTW），差值里 20.2% 是口径不是泄漏。",
            "  4. 覆盖期必须重叠：本样例发票 2026-10-31~11-05、实测 11-01~11-05，已基本同窗；跨月对比要先截共同覆盖期并披露覆盖率。",
        ])
    rec.to_csv(OUT / "03_对账样例_发票vs实测.csv", index=False, encoding="utf-8-sig")

    # ---------- 原始流 ----------
    shutil.copyfile(SIM / "obd_stream_10000.csv", OUT / "00_原始流_板子格式_10000条.csv")

    # ---------- README ----------
    readme = """# 交付包：财务发票 + OBD 实测算碳（2026-11-08）

> **本包全部为模拟数据**，用于跑通与测试口径，不是现场票据/读数。
> 生成器：tools/gen_obd_excel.py（种子 20261108）；碳结果由 python -m src.obd_pipeline 实测产出。

## 给财务同学
| 文件 | 内容 |
|---|---|
| 01_财务同学_加油发票_39张.xlsx | 3 个 sheet：发票明细（39 行 × 17 列）、按车牌汇总（含合计）、字段说明 |
| 01_财务同学_加油发票_39张.csv | 同上明细，UTF-8 BOM，Excel 双击不乱码 |

- 39 张票 / 8 台车 / 2026-10-31~11-05，税率 13%，数量单位 **L**。
- 合计：**1,028.41 L，价税合计 7,563.53 元**。
- 建议测：发票号唯一性、价税勾稽（不含税+税额=价税合计）、按车按月归集、与实测油耗对账。

## 给碳账同学
| 文件 | 内容 |
|---|---|
| 02_碳账同学_OBD碳排结果.xlsx | 4 个 sheet：车级 8 台 / 趟级 40 趟 / 逐点 10,000 条 / 口径与验算 |
| 02_碳账同学_车级碳排_8台.csv | 车级结果，程序读取用 |
| 02_碳账同学_趟级碳排_40趟.csv | 趟级结果 |
| 02_碳账同学_逐点碳排_10000条.csv | 逐点结果（含本步油耗、本步碳排，可逐行复算） |
| 00_原始流_板子格式_10000条.csv | 26 列原始报文（ESP32 应输出的格式），可从零复跑 |

**核心数字（本批实测轨，TTW）**
- 10,000 点 / 8 台 / 40 趟
- 3,921.34 km | 959.988 L | **2,524.7679 kgCO2e** | 643.85 g/km
- 因子 2.63 kgCO2/L（柴油 TTW，GB/T 2589 / IPACC）
- 验算：本步碳排 = 本步油耗 × 2.63；按车求和 = 车级表合计

**注意**
1. 时间列为 **UTC**（原始报文 +08:00，网关落盘归一）。
2. 本轨**不进 E0 主账、不参与 28 条闸门**；E0 2,258,438.376 kg 来自京东运单主链，两者不可相加。
3. 若你们的因子表用 3.16 kgCO2/L（TTW+WTT），与 2.63 差 **20.2%**，是边界不同不是误差 —— 对账前先统一。

## 双方联调：对账样例
03_对账样例_发票vs实测.xlsx 按车牌把「发票加油量」与「实测油耗」并排放，含差额与偏差%。
生成时注入了 3 个已知偏差（苏A12346 +34.9%、苏A12348 +17.8%、苏A12350 缺一张票 -9.4%），
答案在 sheet「已知答案（测试用）」里——**先让对方独立判断，再对答案**。

## 复跑命令
~~~powershell
cd D:\\oceanbase02\\2027-challenge-cup
$env:PYTHONIOENCODING='utf-8'
python -m src.obd_pipeline --from-csv 交付_20261108_财务与碳账\\00_原始流_板子格式_10000条.csv --no-write
~~~
"""
    (OUT / "README_交付说明.md").write_text(readme, encoding="utf-8")
    print("OK", OUT)
    for p in sorted(OUT.iterdir()):
        print(f"  {p.name}  {p.stat().st_size:,} B")


if __name__ == "__main__":
    main()
