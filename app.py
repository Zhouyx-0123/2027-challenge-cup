import streamlit as st

st.title("2027 挑战杯项目")
st.write("合作开发环境配置成功！")

name = st.text_input("请输入你的名字：")
if name:
    st.success(f"你好，{name}！")