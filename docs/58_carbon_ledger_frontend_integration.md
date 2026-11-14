# 碳台账接入与交付说明 v0.9

本版新增来源条数及范围排放两个饼图，排放台账默认按车辆 / 对象汇总；展开后可查原始记录。Excel共13张表。详细统计口径与操作见根目录《v0.9使用说明.md》。

唯一调用链：主启动.bat / 启动.command → tools.launch → app/app.py → 主导航 ledger → app/main_views.py::render_ledger → app/components/carbon_disclosure_panel.py → carbon_disclosure。

主导航仍为六个页面。碳台账直接渲染披露系统，旧 `_render_ledger_e0`、`_e0_disclosure_text`、`_sync_ledger_carbon_params` 及旧页面组件已删除，不作为隐藏页签保留。

独立域逻辑和资料位于 carbon_disclosure。主系统可信HTML在本地内嵌，CSV在浏览器内处理，不把上传内容作为脚本插入。离线HTML为同一模块构建的备用页，不需要独立服务器。

入账、精确汇总与附录规则沿用此前已验证版本。标准说明册不因页面替换而重新计算排放。新的主启动检查会拒绝混有旧页面代码或旧组件文件的目录；同项目重复启动复用既有服务，其他程序占用端口时选择空闲端口。

Windows已使用用户Python 3.13 / Streamlit 1.67实际验证。运行与验收结果见根目录《碳账交付验收》。
