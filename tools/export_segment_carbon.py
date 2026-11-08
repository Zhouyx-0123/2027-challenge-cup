# -*- coding: utf-8 -*-
"""把路段/趟级碳结果导出成「碳账同学可直接录入」的包。

数据源（只读）：
  data/processed/jd_segment_master.parquet   12,167 段（grain=trip_proxy），含簇与 EF
  data/processed/baseline_carbon.parquet     33,214 运单分摊行，含真实车牌
  data/processed/kpi.json / current_run.json 对平用

输出目录：交付_20261108_财务与碳账/05_路段碳_给碳账同学/
  路段碳_12167段.xlsx     6 个 sheet（说明/路段碳/运单分摊/28列入驻版/字段字典/校验）
  路段碳_12167段.csv      路段碳主表 CSV
  emission_fact_12167.csv 按《财务与碳会计功能设计与整改 v2》§1.2 契约的碳会计投递格式
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
OUT = ROOT / "交付_20261108_财务与碳账" / "05_路段碳_给碳账同学"
RUN_ID = "20261027-152051-71d711d6"
BATCH_ID = "BATCH-" + RUN_ID

# 财务主表 28 列（与「字段对应与主表示例.xlsx」逐字一致，用于入驻版对齐）
MASTER_COLS = [
    "记录ID", "活动记录ID", "数据性质", "开票月份", "开票日期", "发票代码", "发票号码",
    "销售方名称", "商品或服务名称", "费用类别", "数量", "单位", "单价（元/单位）",
    "柴油量（L）", "不含税金额（元）", "税额（元）", "价税合计（元）", "确认成本（元）",
    "车牌号", "运单号", "趟或段ID", "核算批次ID", "双验判定", "是否允许入账",
    "审核人", "审核时间", "证据索引", "备注",
]
# 碳侧必需的 3 个补充列（主表没有里程与碳值，必须显式补，不能塞进备注）
EXTRA_COLS = ["里程（km）", "碳排放（kgCO2e）", "计量方法"]


def fmt(value, digits: int) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value):,.{digits}f}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    seg = pd.read_parquet(PROC / "jd_segment_master.parquet")
    base = pd.read_parquet(PROC / "baseline_carbon.parquet")
    kpi = json.loads((PROC / "kpi.json").read_text(encoding="utf-8"))

    trip_plate = (base.groupby("trip_id")
                  .agg(plate=("plate", "first"), n_wb=("waybill_no", "nunique"),
                       wb_first=("waybill_no", "first"))
                  .reset_index())
    seg = seg.merge(trip_plate, on="trip_id", how="left", validate="one_to_one")

    seg = seg.sort_values("segment_id").reset_index(drop=True)
    rows = []
    for i, r in seg.iterrows():
        rows.append({
            "记录ID": "SEG-" + str(r["segment_id"]).replace("TRIP-", ""),
            "段ID": r["segment_id"],
            "趟ID": int(r["trip_id"]),
            "车牌号": r["plate"],
            "车型": r["vehicle_type"],
            "车辆分组": r["vehicle_group"],
            "月份": str(r["month"]),
            "起运时间": str(r["start_ts"]),
            "到达时间": str(r["end_ts"]),
            "起点": r["origin"],
            "运单数": int(r["n_waybills"]),
            "运单号（首张）": r["wb_first"],
            "里程（km）": round(float(r["distance_km"]), 3),
            "时长（h）": round(float(r["duration_h"]), 4),
            "均速（km/h）": round(float(r["speed_kmh"]), 3),
            "载重（t）": round(float(r["load_t"]), 3),
            "论文速度档": r["paper_bin"],
            "质量通过": bool(r["passed"]),
            "簇ID": "" if pd.isna(r["cluster_id"]) else int(r["cluster_id"]),
            "簇特征集": r["cluster_feature_set"],
            "碳排（kgCO2e）": round(float(r["carbon_kg"]), 9),
            "碳排_物理轨（kgCO2e）": round(float(r["carbon_kg_phys"]), 9),
            "碳强度EF（g/km）": round(float(r["ef_g_km"]), 3),
            "碳口径": r["carbon_basis"],
            "计量方法": "legal",
            "因子版本": "JCB2023-BASE-v1",
            "工程边界": "TTW（尾气管）",
            "数据性质": "真实",
            "双验判定": "待核验",
            "是否允许入账": "待确认",
            "核算批次ID": BATCH_ID,
            "证据索引": "data/processed/jd_segment_master.parquet#" + r["segment_id"],
            "备注": "碳值=公里×因子；不参与 A4 正式行（5 条实质谓词 0/12）",
        })
    main_df = pd.DataFrame(rows)

    # 运单分摊（33,214 行）：一段多单时按等分份额分摊，份额之和=1
    alloc = base[["waybill_no", "order_no", "plate", "trip_id", "trip_km", "dist_km",
                  "load_t", "kg_co2", "kg_co2_gross", "allocation_share"]].copy()
    alloc = alloc.rename(columns={
        "waybill_no": "运单号", "order_no": "订单号", "plate": "车牌号", "trip_id": "趟ID",
        "trip_km": "段总里程（km）", "dist_km": "本单里程（km）", "load_t": "载重（t）",
        "kg_co2": "分摊碳（kgCO2e）", "kg_co2_gross": "毛碳（kgCO2e）",
        "allocation_share": "分摊份额",
    })
    alloc.insert(0, "行号", range(1, len(alloc) + 1))

    # 28 列入驻版：一行为一段，碳值放显式补充列
    reg = pd.DataFrame({
        "记录ID": main_df["记录ID"],
        "活动记录ID": "",
        "数据性质": main_df["数据性质"],
        "开票月份": main_df["月份"],
        "开票日期": "",
        "发票代码": "",
        "发票号码": "",
        "销售方名称": "",
        "商品或服务名称": "道路货运（柴油）",
        "费用类别": "运输",
        "数量": "",
        "单位": "",
        "单价（元/单位）": "",
        "柴油量（L）": "",
        "不含税金额（元）": "",
        "税额（元）": "",
        "价税合计（元）": "",
        "确认成本（元）": "",
        "车牌号": main_df["车牌号"],
        "运单号": main_df["运单号（首张）"],
        "趟或段ID": main_df["段ID"],
        "核算批次ID": main_df["核算批次ID"],
        "双验判定": main_df["双验判定"],
        "是否允许入账": main_df["是否允许入账"],
        "审核人": "",
        "审核时间": "",
        "证据索引": main_df["证据索引"],
        "备注": main_df["备注"],
        "里程（km）": main_df["里程（km）"],
        "碳排放（kgCO2e）": main_df["碳排（kgCO2e）"],
        "计量方法": main_df["计量方法"],
    })

    # 碳会计投递契约 emission_fact（§1.2）
    fact = pd.DataFrame({
        "fact_id": "FACT-" + main_df["段ID"] + "-legal-JCB2023-BASE-v1",
        "trip_id": main_df["段ID"],
        "plate": main_df["车牌号"],
        "vehicle_id": "",
        "period": main_df["月份"],
        "scope": "1",
        "activity_type": "柴油",
        "emission_kg": main_df["碳排（kgCO2e）"],
        "method": main_df["计量方法"],
        "factor_id": "diesel_ttw_kg_per_l",
        "factor_version": main_df["因子版本"],
        "computed_at": "2026-10-27T15:20:51+08:00",
        "evidence_ref": main_df["证据索引"],
    })

    dictionary = pd.DataFrame([
        ("记录ID", "段级唯一键（SEG-000001）", "", "程序生成", "是", "fact_id 的一部分"),
        ("段ID", "jd_segment_master.segment_id（TRIP-000001）", "", "段主表", "是", "trip_id"),
        ("趟ID", "物理趟次号（12,167 个之一）", "", "段主表", "是", "trip_id"),
        ("车牌号", "真实车牌（来自 33,214 运单行，非哈希）", "", "baseline_carbon", "是", "plate"),
        ("月份", "所属月份（2023-01..12）", "", "段主表", "是", "period"),
        ("里程（km）", "趟级去重里程（同一趟多运单不重复计）", "km", "段主表", "是", "活动量"),
        ("载重（t）", "核定载质量代理值", "t", "段主表", "否", "load_t"),
        ("论文速度档", "吴睿中/白孝轩 5 档速度分段", "", "vsp_segment", "否", "分箱"),
        ("簇ID", "KMeans3（proxy_v1：均速/ln里程/载重）", "0|1|2", "segment_cluster", "否", "工况簇"),
        ("碳排（kgCO2e）", "法定轨碳排＝里程×因子（TTW）", "kg", "段主表 carbon_kg", "是", "emission_kg"),
        ("碳排_物理轨（kgCO2e）", "物理轨（含载重修正）", "kg", "段主表 carbon_kg_phys", "否", "对照"),
        ("碳强度EF（g/km）", "碳排÷里程×1000", "g/km", "派生", "是", "EF"),
        ("计量方法", "legal / obd / vsp 三轨之一", "", "本项目", "是", "method"),
        ("因子版本", "JCB2023-BASE-v1", "", "config", "是", "factor_version"),
        ("工程边界", "TTW；与财务 3.16（WTW）差 20.2%", "", "口径", "是", "边界"),
        ("运单数", "该趟包含的运单张数（2,995 趟多单）", "张", "baseline_carbon", "否", "明细"),
        ("双验判定", "待核验 / 通过 / 复核", "", "碳会计闸门", "是", "复核状态"),
        ("是否允许入账", "待确认 / 允许 / 拒绝", "", "碳会计闸门", "是", "入账状态"),
        ("核算批次ID", "BATCH-" + RUN_ID, "", "current_run.json", "是", "批次"),
        ("证据索引", "parquet 文件 + 段ID", "", "本项目", "是", "evidence_ref"),
    ], columns=["字段", "含义", "单位", "来源", "必填", "对应 emission_fact"])

    checks = pd.DataFrame([
        ("路段数", len(main_df), "== 12,167", len(main_df) == 12167),
        ("碳排合计（kg）", round(main_df["碳排（kgCO2e）"].sum(), 6), "== kpi.total_kg 2,258,438.376208",
         abs(main_df["碳排（kgCO2e）"].sum() - float(kpi["total_kg"])) < 1e-3),
        ("里程合计（km）", round(main_df["里程（km）"].sum(), 3), "== kpi.total_km 2,174,173.86",
         abs(main_df["里程（km）"].sum() - float(kpi["total_km"])) < 0.01),
        ("运单分摊行数", len(alloc), "== 33,214", len(alloc) == 33214),
        ("运单分摊碳合计（kg）", round(alloc["分摊碳（kgCO2e）"].sum(), 6), "== 2,258,438.376208",
         abs(alloc["分摊碳（kgCO2e）"].sum() - float(kpi["total_kg"])) < 1e-3),
        ("车牌空值", int(main_df["车牌号"].isna().sum()), "== 0", main_df["车牌号"].isna().sum() == 0),
        ("碳排空值", int(main_df["碳排（kgCO2e）"].isna().sum()), "== 0", main_df["碳排（kgCO2e）"].isna().sum() == 0),
        ("负碳排", int((main_df["碳排（kgCO2e）"] < 0).sum()), "== 0", (main_df["碳排（kgCO2e）"] < 0).sum() == 0),
        ("簇ID覆盖率", f"{(main_df['簇ID'] != '').mean():.1%}", "≥ 95%", (main_df["簇ID"] != "").mean() >= 0.95),
        ("记录ID唯一", bool(main_df["记录ID"].is_unique), "True", bool(main_df["记录ID"].is_unique)),
    ], columns=["校验项", "实测", "期望", "通过"])

    notes = pd.DataFrame({"说明": [
        "1. 本包是「路段（趟）级」碳结果：一行 = 一段 = 一趟物理行程（grain=trip_proxy）。",
        "2. 12,167 段不是「空间路段」，是京东运单按 车牌+里程+起止时刻 六元组归并出的趟次；真正带 GPS 轨迹的路段只有 OBD 模拟那 8 台。",
        "3. 碳值口径：法定轨＝里程×因子（TTW），因子版本 JCB2023-BASE-v1；与财务发票侧的 3.16 kgCO2/L（WTW）差 20.2%，可关联不可相减。",
        "4. 33,214 行是运单分发：同一趟多张运单时，碳按 1/n 等分（现默认），明细在 sheet「运单分摊」。",
        "5. 碳守恒轨（OBD 油量×2.63）目前只覆盖 8 台车 / 10,000 点模拟流，不覆盖这 12,167 段；要用守恒轨须先有全程 OBD。",
        "6. 28 列入驻版严格照抄财务主表列名（含「趟或段ID」第 21 列），但主表没有里程与碳值，故补 3 列：里程（km）/碳排放（kgCO2e）/计量方法。",
        "7. 双验判定=待核验、是否允许入账=待确认：A4 台账 5 条实质谓词 0/12，任何行都不得被写成「已批准」。",
        "8. emission_fact CSV 是按《财务与碳会计功能设计与整改 v2》§1.2 的投递格式，fact_id 幂等（段ID+方法+因子版本）。",
    ]})

    xlsx = OUT / "路段碳_12167段.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        notes.to_excel(writer, sheet_name="00_说明", index=False)
        main_df.to_excel(writer, sheet_name="01_路段碳", index=False)
        alloc.rename(columns={"分摊碳（kgCO2e）": "分摊碳（kgCO2e）"}).to_excel(
            writer, sheet_name="02_运单分摊", index=False)
        reg.to_excel(writer, sheet_name="03_28列入驻版", index=False)
        dictionary.to_excel(writer, sheet_name="04_字段字典", index=False)
        checks.to_excel(writer, sheet_name="05_校验", index=False)
        widths = {"01_路段碳": 140, "02_运单分摊": 110, "03_28列入驻版": 120}
        for name, width in widths.items():
            ws = writer.book[name]
            ws.freeze_panes = "B2"
            ws.auto_filter.ref = ws.dimensions
            for col in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                ws.column_dimensions[col].width = 14
            ws.column_dimensions["A"].width = 18
            ws.column_dimensions["B"].width = 16
        writer.book["01_路段碳"].column_dimensions["A"].width = 18

    main_df.to_csv(OUT / "路段碳_12167段.csv", index=False, encoding="utf-8-sig")
    alloc.to_csv(OUT / "运单分摊_33214行.csv", index=False, encoding="utf-8-sig")
    fact.to_csv(OUT / "emission_fact_12167.csv", index=False, encoding="utf-8-sig")

    write_obd_track()

    print("OK", xlsx, len(main_df), "段 /", len(alloc), "运单行")
    print(checks.to_string(index=False))


def write_obd_track() -> None:
    """守恒轨样例：OBD 实测 40 段（8 台 / 10,000 点），与法定轨同结构、不混算。"""
    processed = ROOT / "data" / "processed"
    trips_p = processed / "obd_trips.parquet"
    veh_p = processed / "obd_vehicle_carbon.parquet"
    if not trips_p.exists():
        print("[跳过] 没有 obd_trips.parquet，守恒轨样例未生成")
        return
    trips = pd.read_parquet(trips_p)
    plate_map = {}
    if veh_p.exists():
        veh = pd.read_parquet(veh_p)[["vehicle_id", "plate"]].drop_duplicates("vehicle_id")
        plate_map = dict(zip(veh["vehicle_id"], veh["plate"]))

    def local(ts):
        s = pd.to_datetime(ts, utc=True).tz_convert("Asia/Shanghai")
        return s.strftime("%Y-%m-%d %H:%M:%S")

    obd = pd.DataFrame({
        "记录ID": [f"OBD-SEG-{i:04d}" for i in range(1, len(trips) + 1)],
        "段ID": [f"OBD-TRIP-{v}-{t:03d}" for v, t in zip(trips["vehicle_id"], trips["trip_id"])],
        "车辆编号": trips["vehicle_id"],
        "车牌号": trips["vehicle_id"].map(plate_map).fillna(""),
        "开始时间": trips["start_ts"].map(local),
        "结束时间": trips["end_ts"].map(local),
        "采样点数": trips["n_readings"],
        "里程（km）": trips["dist_km"].round(3),
        "油耗（L）": trips["fuel_l"].round(3),
        "碳排（kgCO2e）": trips["kg_co2"].round(6),
        "碳强度（g/km）": trips["co2_gkm"].round(3),
        "计量方法": "obd",
        "碳口径": "守恒法：油量×2.63（TTW，尾气管）",
        "因子版本": "GB/T 2589 / IPACC 柴油燃烧 CO2 因子",
        "数据性质": "实测（模拟流）",
        "核算月份": "2026-11",
        "双验判定": "待核验",
        "是否允许入账": "待确认",
        "备注": "OBD 模拟流 8 台车 10,000 点；与 12,167 段法定轨不混算、不可相加",
    })

    reg = pd.DataFrame({
        "记录ID": obd["记录ID"],
        "活动记录ID": "",
        "数据性质": obd["数据性质"],
        "开票月份": obd["核算月份"],
        "开票日期": "",
        "发票代码": "",
        "发票号码": "",
        "销售方名称": "",
        "商品或服务名称": "道路货运（柴油）·OBD实测",
        "费用类别": "运输",
        "数量": obd["油耗（L）"],
        "单位": "L",
        "单价（元/单位）": "",
        "柴油量（L）": obd["油耗（L）"],
        "不含税金额（元）": "",
        "税额（元）": "",
        "价税合计（元）": "",
        "确认成本（元）": "",
        "车牌号": obd["车牌号"],
        "运单号": "",
        "趟或段ID": obd["段ID"],
        "核算批次ID": "BATCH-OBD-20261108",
        "双验判定": obd["双验判定"],
        "是否允许入账": obd["是否允许入账"],
        "审核人": "",
        "审核时间": "",
        "证据索引": "data/raw/obd_gps/obd_stream.csv#" + obd["段ID"],
        "备注": obd["备注"],
        "里程（km）": obd["里程（km）"],
        "碳排放（kgCO2e）": obd["碳排（kgCO2e）"],
        "计量方法": obd["计量方法"],
    })

    xlsx = OUT / "路段碳_OBD实测40段.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        pd.DataFrame({"说明": [
            "1. 这是守恒轨样例：每段的碳来自 OBD 逐点油量累加 × 2.63（TTW），不是里程×因子。",
            "2. 8 台车 / 40 段 / 10,000 个采样点，时间是 2026-10-31 22:40 ~ 2026-11-05 09:33（UTC），本地显示 +08:00。",
            "3. 数据来源是模拟板卡流（data/raw/obd_gps/obd_stream.csv），版面与真实板卡一致，但不是真车读数。",
            "4. 与 12,167 段的法定轨（里程×因子）不混算、不可相加：两轨口径不同，只能逐段/逐车对照。",
            "5. 对接财务主表的结构与主包一致（28 列 + 3 列补充），可直接用同一个 录入.py 登记。",
        ]}).to_excel(writer, sheet_name="00_说明", index=False)
        obd.to_excel(writer, sheet_name="01_路段碳_OBD", index=False)
        reg.to_excel(writer, sheet_name="02_28列入驻版", index=False)
        for name in ("01_路段碳_OBD", "02_28列入驻版"):
            ws = writer.book[name]
            ws.freeze_panes = "B2"
            ws.auto_filter.ref = ws.dimensions
            for col in "ABCDEFGHIJKLMNOPQRST":
                ws.column_dimensions[col].width = 15
            ws.column_dimensions["A"].width = 20
            ws.column_dimensions["B"].width = 24
    obd.to_csv(OUT / "路段碳_OBD实测40段.csv", index=False, encoding="utf-8-sig")
    print("OK", xlsx, len(obd), "段；碳", round(float(obd['碳排（kgCO2e）'].sum()), 4), "kg")


if __name__ == "__main__":
    main()
