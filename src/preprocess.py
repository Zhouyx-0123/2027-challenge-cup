import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = ROOT / "data" / "processed"

def build_wide_table():
    print("  [Preprocess] 正在处理原始数据...")
    mock_data = {
        "运单号": ["JDVB001", "JDVB002", "JDVB003", "JDVB004"],
        "起运地": ["上海", "义乌", "北京", "广州"],  # 新加的
        "目的地": ["义乌", "上海", "广州", "北京"],  # 新加的
        "里程_km": [100.0, 150.5, 200.0, 250.0],
        "车型": ["重型", "中型", "轻型", "重型"],
        "载重_t": [30.0, 15.0, 5.0, 20.0]
    }
    wide_df = pd.DataFrame(mock_data)
    
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / "wide_data.parquet"
    wide_df.to_parquet(out_path, index=False)
    print(f"  [Preprocess] 宽表已保存: {out_path.name}")
    return wide_df