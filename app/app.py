import streamlit as st
import pandas as pd
from pathlib import Path

# 配置页面
st.set_page_config(page_title="碳感知智能决策系统", layout="wide")
st.title("物流企业碳感知智能决策系统")
st.write("本项目采用扁平化流水线架构，前端直接读取静态结果。")

# 定位数据文件路径
ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "processed" / "final_report.parquet"

# 检查文件是否存在
if DATA_PATH.exists():
    df = pd.read_parquet(DATA_PATH)
    
    st.success("✅ 数据加载成功！")
    
    # 顶部指标卡
    col1, col2, col3 = st.columns(3)
    col1.metric("总单量", f"{len(df)} 单")
    col2.metric("总里程", f"{df['里程_km'].sum():,.1f} km")
    col3.metric("总碳排放", f"{df['碳排放_kg'].sum():,.1f} kg")
    
    # 数据表格
    st.subheader("订单明细数据")
    st.dataframe(df)
    
else:
    st.error("⚠️ 未找到数据文件！")
    st.info(f"当前查找路径: {DATA_PATH}")
    st.info("请先在终端运行: `python -m src.pipeline` 生成数据。")