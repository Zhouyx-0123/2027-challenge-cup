import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"

def calculate_carbon(wide_df):
    print("  [Optimizer] 正在计算基准碳排放...")
    # 简单模拟碳排计算
    wide_df["碳排放_kg"] = wide_df["里程_km"] * 0.8 
    
    out_path = PROCESSED_DIR / "baseline_carbon.parquet"
    wide_df.to_parquet(out_path, index=False)
    
    print(f"  [Optimizer] 碳排结果已保存: {out_path.name}")
    return wide_df