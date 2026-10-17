import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"

def evaluate_schemes(carbon_df):
    print("  [Evaluator] 正在进行成本效益分析...")
    carbon_df["柴油成本_元"] = carbon_df["里程_km"] * 3.0
    carbon_df["新能源成本_元"] = carbon_df["里程_km"] * 1.5
    
    out_path = PROCESSED_DIR / "final_report.parquet"
    carbon_df.to_parquet(out_path, index=False)
    
    print(f"  [Evaluator] 最终结果已保存: {out_path.name}")
    return carbon_df