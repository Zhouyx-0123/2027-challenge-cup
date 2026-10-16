import streamlit as st

st.title("2027 挑战杯项目")
st.write("合作开发环境配置成功！")

name = st.text_input("请输入你的名字：")
if name:
    st.success(f"你好，{name}！")

if st.button("点我测试"):  # 注意这行是顶格的，和上面的 if name 平级
    st.write("按钮被点击了！")