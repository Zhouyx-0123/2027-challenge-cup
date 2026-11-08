# -*- coding: utf-8 -*-
"""生成「答辩 PPT 逻辑稿（交接版）」——只给逻辑，不给版式/画面建议。
执行方：负责插图、排版、配色、动画。本文只规定：讲什么、凭什么、不能说什么。
输出：docs/69_PPT逻辑稿_给执行方.md / .docx
"""
from pathlib import Path
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

ROOT = Path(r"D:\oceanbase02\2027-challenge-cup")
MD = ROOT / "docs" / "69_PPT逻辑稿_给执行方.md"
DOCX = ROOT / "docs" / "69_PPT逻辑稿_给执行方.docx"

B = []
B.append(("h1", "答辩 PPT 逻辑稿（交接版）"))
B.append(("quote", "这份文件只给逻辑：讲什么、凭什么、不能说什么。插图、排版、配色、动画由执行方决定；数字与口径不可改。"))

B.append(("h2", "0 整场一句话"))
B.append(("p", "我们不是又做了一张碳报表，而是把一吨碳从运单、油票、车上三处对齐，再用它去选路线。"))
B.append(("p", "五个词记住就行：算得出、测得着、核得动、选得对、守得住。"))

B.append(("h2", "1 讲法：三幕，不按模块"))
B.append(("p", "不按「系统有哪些模块」讲，按「评委心里的下一个问题」讲。每页标题就是那个问题的答案，页尾那句转场直接引出下一页。"))
B.append(("table", [
    ["幕", "页码", "任务", "为什么放这里"],
    ["一 立信", "P1–P6", "先定边界，再讲账怎么算、怎么证明", "边界不清，后面所有数字都没有意义"],
    ["二 交底", "P7–P8", "主动说破两本账与三源差额", "自己先说破，比被问出来强"],
    ["三 兑现", "P9–P12", "碳怎么变成决策、闭环边界、下一步", "先证明可信，再谈价值"],
]))

B.append(("h2", "2 逐页逻辑卡（12 页主线）"))
B.append(("p", "每页五格：标题句 / 这页回答的问题 / 论据 / 必须说 / 不能说。「转场」是讲完这页的最后一句，直接接到下一页。"))

pages = [
 dict(no="P1", head="封面",
   title="让每一吨碳，都有据可查。",
   q="这场汇报想让评委记住什么？",
   ev=["三条数据入口 · 一套主账 · 两把尺子核验 · 一条决策出口"],
   must="我们做的是可核对的碳账，不是一张漂亮的碳报表。",
   never="「全国领先 / 首创」——没有对照物。",
   next="先给结论：30 秒说清我们做成了什么。"),
 dict(no="P2", head="30 秒结论",
   title="先用 30 秒说清：我们做成了什么。",
   q="到底做成了什么？",
   ev=["算得出：E0 2,258.438 t，28 项闸门全过",
       "测得到：10,000 点原始流可复算（8 台车 / 40 趟）",
       "核得动：发票 1,028.41 L vs 实测 959.99 L，合计 +7.13%",
       "选得出：310 条 OD → 1,240 候选 → 287 条可演示",
       "守得住：A4 交付台账正式行 0"],
   must="最后一条是主动交底：我们知道边界在哪。",
   never="把 4,502 说成总排放（那是毛口径，同趟多运单重复计入）。",
   next="把这条线展开成一张地图。"),
 dict(no="P3", head="整体地图",
   title="思维链条：从「算得清」到「选得对」。",
   q="这套东西的整体形状是什么？",
   ev=["三入口（运单 / 发票 / OBD）→ 一套主账 → 两把尺子（G2 对账、A4 交付）→ 一条决策出口",
       "底部一条虚线：执行 → OBD 实测 → 复盘 → 下一版 EF"],
   must="虚线是回灌，不是回写：EF 更新不改变账本。",
   never="「AI 全链路自动优化」——Q 只在 287 条 OD 上有策略。",
   next="地图第一步：先说清算的是哪些趟。"),
 dict(no="P4", head="边界",
   title="先定「算哪些趟」，否则数字没有意义。",
   q="你算的是谁家的碳？边界在哪？",
   ev=["36,329 运单明细行 → 12,098 个运单号 → 去重 3,115 → 33,214 段 → 12,167 个物理趟",
       "里程 2,174,174 km"],
   must="这是京东一家、2023 一年的口径，不是全市场。",
   never="「覆盖全国物流」。",
   next="边界定了，再看这一吨怎么算出来。"),
 dict(no="P5", head="主账",
   title="算得出：一条主链，五步全部可复现。",
   q="这个数是怎么算出来的？",
   ev=["× 2.63 kgCO2/L（TTW，只算尾气管）", "2,258,438.376 kg = 2,258.438 t"],
   must="TTW 不含油井到油箱的 0.53，那部分在 A4 的 WTW 里。",
   never="把 2.63 与 3.16 混用，或说「两个数差不多」。",
   next="算得出不等于可信，接着看怎么证明。"),
 dict(no="P6", head="证据",
   title="28 项闸门全过，任何一项不过就拒绝出结果。",
   q="凭什么信这个数？",
   ev=["28 = 10 项目标 + 18 项结构",
       "run 20261027-152051-71d711d6｜code edc1202｜输入指纹 71d711d6b409",
       "data/raw 为空时主链直接拒跑，防止 demo 覆盖真实结果"],
   must="闸门是拒绝执行的硬门，不是事后勾选。",
   never="「100% 准确」——闸门证明的是口径与结构自洽。",
   next="主账可信了，那交付账呢？"),
 dict(no="P7", head="两本账（交底）",
   title="E0 已闭环，A4 还没批准。",
   q="交付台账现在能不能用？",
   ev=["E0 2,258,438.376 kg（TTW，工程口径）",
       "A4 2.439298 t（WTW 3.16，交付口径）；12 行全 simulated，正式行 0",
       "9 条谓词里 5 条实质项 0/12 阻断；4 条结构项虽过，值是 EVID-SIM-* 占位"],
   must="我们不把「字段齐备」说成「已批准」。",
   never="「A4 基本完成 / 马上能批」。",
   next="两本账不能相减，那还能怎么互相验证？"),
 dict(no="P8", head="三源对账",
   title="核得动：不追求「相等」，只追求「差额可解释」。",
   q="有没有第二种独立证据？",
   ev=["发票 1,028.41 L（买油）vs 实测 959.99 L（用油）= 合计 +7.13%",
       "逐车 −9.35% ~ +34.94%；苏A12346 +34.94%、苏A12348 +17.78% 是测试注入的异常车",
       "三源共同覆盖期现在是空集（运单 2023 年、OBD 2026-11、发票 0 条）"],
   must="差额能被库存、非道路用油、记账滞后解释，解释不了才是问题。",
   never="「三源完全对上」，或给一个编出来的偏差百分比。",
   next="能核之后，这套东西拿来干什么？"),
 dict(no="P9", head="决策",
   title="帕累托负责「不劣」，Q 负责「选一个」。",
   q="碳怎么变成决策？",
   ev=["K=3、silhouette 0.425、6 个可用 EF 格覆盖 11,964 / 12,036 趟（99.4%）",
       "310 条真实 OD → 1,240 条候选 → 帕累托非劣 → 287 条可演示",
       "Q 表 34,440 次更新 / 30 轮，策略落在 config/rl_policy_real.json"],
   must="K-means 给的是碳强度参数，不是碳账。",
   never="「K-means 算出了碳」。",
   next="决策会反过来改碳吗？"),
 dict(no="P10", head="闭环边界",
   title="跑得越多，EF 越准 —— 但只准在 EF 这一层。",
   q="模型会不会污染账本？",
   ev=["EF-PRIOR → EF-CAL v0.1 → …，每版留版本号，可追溯到哪一批 OBD",
       "校准样本与验证样本按车辆或时间隔离，避免自己校准自己验证"],
   must="如果 EF 能改账，账就永远「越跑越好看」。",
   never="「系统会自我进化 / 自动变准」。",
   next="逻辑讲完，看它实际长什么样。"),
 dict(no="P11", head="演示",
   title="六个页面，一条动线，90 秒走完。",
   q="有能看的系统吗？",
   ev=["首页 2,258.4384 t → 数据链总览 → 硬件监控（OBD 实测 Tab）→ 路线沙盘 → 碳核算台账（双口径）→ 发票感知（空态）"],
   must="页面数字全部来自 data/processed 的文件快照，页面不重算。",
   never="解释控件、现场改参数、点进空态里翻。",
   next="看完系统，最后说还差什么。"),
 dict(no="P12", head="收口",
   title="价值三段，下一步三件。",
   q="下一步做什么，谁在什么时候交什么？",
   ev=["① 同期试点：1 台车 ≥7 天 ≥500 点、accept_rate ≥95%、≥1 张含油量发票、≥1 趟运单重叠 → G2 出第一张真偏差卡",
       "② A4 首条正式行：9 条谓词全绿 + Owner 签字",
       "③ 商业闭环：BOM + 以三源对账为切入点的商业计划书"],
   must="我们缺的不是算法，是同期真实数据。",
   never="承诺具体日期（排期还没定）。",
   next="（结束）"),
]
for d in pages:
    B.append(("h3", d["no"] + "  " + d["head"]))
    B.append(("table", [
        ["项", "内容"],
        ["标题句", d["title"]],
        ["这页回答", d["q"]],
        ["论据", "\n".join("· " + x for x in d["ev"])],
        ["必须说", d["must"]],
        ["不能说", d["never"]],
        ["转场", d["next"]],
    ]))

B.append(("h2", "3 红线：五句不能说、三个数不能改"))
B.append(("table", [
    ["不能说", "正确说法"],
    ["两个数差不多", "TTW 2.63 与 WTW 3.16 是边界不同，差 20.2%，可关联、不可相减"],
    ["A4 已批准 / 基本完成", "A4 正式行 0，5 条实质谓词 0/12"],
    ["K-means 算出了碳", "K-means 只给碳强度参数 EF，不产生主账碳值"],
    ["三源完全对上", "合计 +7.13%，判据是差额可解释；共同覆盖期仍是空集"],
    ["100% 准确 / 全国领先", "闸门 28/28 证明口径自洽，不代表实测准确度"],
]))
B.append(("p", "三个数不能改：E0 2,258,438.376 kg ｜ 闸门 28/28（10 目标 + 18 结构）｜ A4 正式行 0。4,502 只能作为「毛口径」出现并标注。"))

B.append(("h2", "4 评委高频 12 问（一句话答法）"))
B.append(("table", [
    ["问题", "一句话答法"],
    ["为什么不用满箱法？", "我们用的是油箱周期分摊法（相邻两次加油之间按里程摊）；没有满箱标记、没有库存校验，期末未满箱会系统性高估，这是已知限制。"],
    ["TTW 和 WTW 差多少？", "2.63 vs 3.16，差 20.2%，是排放边界不同，不是误差。"],
    ["A4 为什么是 0？", "12 行全是 simulated，5 条实质谓词 0/12；正式行要 9 条全绿 + Owner 签字。"],
    ["碳和财务对得上吗？", "合计 +7.13%，逐车 −9.35%~+34.94%；两台是注入的异常车，其余在 ±1.2% 内。"],
    ["数据是真的吗？", "运单是京东真实运单；OBD 目前是台架 + 模拟流，实车还没接；发票是带水印的测试票面。"],
    ["为什么只有京东？", "只有这一家有运单级数据；换客户要重接口径。"],
    ["K 为什么是 3？", "silhouette 0.425；K=3 是确认过的设定，K=2~6 的扫描只留在审计里。"],
    ["Q 收敛吗？", "34,440 次更新 / 30 轮；置信度低时回退四策略枚举，保证永远给得出方案。"],
    ["能省多少碳/钱？", "目前只能给候选方案的相对差，不能给绝对节省；还没有执行后的实测复盘。"],
    ["硬件多少钱？", "BOM 待补（这是缺口，不编数）。"],
    ["怎么和 TMS/ERP 接？", "网关只做校验落盘，不侵入业务系统；发票走 OCR 进主表。"],
    ["最大的风险是什么？", "同期真实数据拿不到，G2 就只能停在「方法可复核」。"],
]))

B.append(("h2", "5 时长版本"))
B.append(("table", [
    ["版本", "页码", "备注"],
    ["12 分钟", "P1–P12 全上", "主线版本"],
    ["8 分钟", "P1 P2 P3 P5 P7 P8 P9 P12", "砍掉边界细节与闭环"],
    ["3 分钟", "一句话 + 三个数字 + 一个缺口", "2,258.438 t ／ 28 项闸门 ／ +7.13%；缺口：同期真实数据"],
]))

B.append(("h2", "6 四个缺口（内容待补，不是版式问题）"))
B.append(("bullets", [
    "硬件 BOM：器件 / 单价 / 数量 / 单车 / 8 台合计 —— 现在没有数，别编。",
    "商业计划书切入点：已定用三源对账（1,028 L vs 960 L、两台异常车 +34.9% / +17.8%），还差成页。",
    "OCR 准确率：只有字段规范与 45 张票面测试集，未评测，不要写百分比。",
    "实车数据：只有 2026-11-07 的 30 点真机冒烟；权威演示用 10,000 点模拟流，需标注「模拟」。",
]))


def to_md():
    out = []
    for kind, payload in B:
        if kind == "h1":
            out.append("# " + payload + "\n")
        elif kind == "h2":
            out.append("\n## " + payload + "\n")
        elif kind == "h3":
            out.append("\n### " + payload + "\n")
        elif kind == "p":
            out.append(payload + "\n")
        elif kind == "quote":
            out.append("> " + payload + "\n")
        elif kind == "bullets":
            out.extend("- " + x for x in payload)
            out.append("")
        elif kind == "table":
            head, *rows = payload
            out.append("| " + " | ".join(head) + " |")
            out.append("| " + " | ".join("---" for _ in head) + " |")
            for r in rows:
                out.append("| " + " | ".join(str(c).replace("|", "／").replace("\n", "<br>") for c in r) + " |")
            out.append("")
    MD.write_text("\n".join(out), encoding="utf-8")


def set_cn(run, size, bold=False, color=None):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = "微软雅黑"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    if color:
        run.font.color.rgb = RGBColor(*color)


def to_docx():
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "微软雅黑"
    st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    for kind, payload in B:
        if kind == "h1":
            p = doc.add_paragraph()
            set_cn(p.add_run(payload), 22, True, (0x1F, 0x3A, 0x5F))
        elif kind == "h2":
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(14)
            set_cn(p.add_run(payload), 14, True, (0x1F, 0x6F, 0xB2))
        elif kind == "h3":
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(10)
            set_cn(p.add_run(payload), 12, True, (0x22, 0x22, 0x22))
        elif kind == "p":
            p = doc.add_paragraph()
            set_cn(p.add_run(payload), 10.5)
        elif kind == "quote":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(12)
            set_cn(p.add_run(payload), 10, False, (0x55, 0x55, 0x55))
        elif kind == "bullets":
            for x in payload:
                p = doc.add_paragraph(style="List Bullet")
                set_cn(p.add_run(x), 10.5)
        elif kind == "table":
            head, *rows = payload
            t = doc.add_table(rows=1, cols=len(head))
            t.style = "Table Grid"
            for i, h in enumerate(head):
                c = t.rows[0].cells[i]
                c.text = ""
                set_cn(c.paragraphs[0].add_run(str(h)), 9.5, True)
            for r in rows:
                cells = t.add_row().cells
                for i, c in enumerate(r):
                    cells[i].text = ""
                    lines = str(c).split("\n")
                    p0 = cells[i].paragraphs[0]
                    set_cn(p0.add_run(lines[0]), 9)
                    for ln in lines[1:]:
                        pp = cells[i].add_paragraph()
                        set_cn(pp.add_run(ln), 9)
            doc.add_paragraph()
    doc.save(str(DOCX))


if __name__ == "__main__":
    to_md()
    to_docx()
    print("md", MD.stat().st_size, "docx", DOCX.stat().st_size)
