import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"

def build_wide_table():
    print("  [Preprocess] 正在处理原始数据...")
    # 模拟数据
    mock_data = {
        "运单号": ["JDVB001", "JDVB002", "JDVB003"],
        "里程_km": [100.0, 150.5, 200.0],
        "车型": ["重型", "中型", "轻型"],
        "载重_t": [30.0, 15.0, 5.0]
    }
    wide_df = pd.DataFrame(mock_data)
    
    # 确保目录存在并保存
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / "wide_data.parquet"
    wide_df.to_parquet(out_path, index=False)
    
    print(f"  [Preprocess] 宽表已保存: {out_path.name}")
    return wide_df