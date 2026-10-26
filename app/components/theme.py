# -*- coding: utf-8 -*-
"""视觉系统：Coolors Palette 玻璃拟态数据驾驶舱。

设计来源：https://coolors.co/ 的逆向拆解（见 docs/46_前端重设计_Coolors.md）。
从 coolors.co 实际样式表里提取的令牌，再映射到碳核算业务语义：

  coolors 原生                    →  本系统
  ---------------------------------------------------------------
  neutral-50  #FAFAFA 画布         →  --ct-canvas
  neutral-950 #0A0A0A 文字         →  --ct-ink
  primary-600 #2563EB 品牌蓝        →  --ct-blue（系统/数据）
  pink-600    #DB2777 强调粉        →  --ct-pink（热点/高排放）
  border: none + box-shadow 环     →  无边界：--ct-ring（0 0 0 1px 假描边）
  radius 14/15/100px              →  --ct-r / --ct-r-pill
  0 8px 48px rgba(0,0,0,.15)      →  --ct-shadow
  cubic-bezier(.3,.4,0,1)          →  --ct-ease
  大色块马赛克                      →  .cm-palette 色板条

设计原则（答辩现场约束）：
  1. 所有类名带 cm- 前缀，避免与 Streamlit 内部类冲突；
  2. 数字一律 tabular-nums，对拍表格才能逐位对齐；
  3. 语义色：blue=系统/数据，pink=热点，mint=达标，amber=估算/警示，
     coral=高排放，red=未通过，灰=已去重；
  4. 前端只做展示，不新增任何业务计算；
  5. 字体走 Google Fonts 渐进增强（Outfit / Manrope / JetBrains Mono），
     断网时自动回落到系统栈，版式不塌。
"""

from __future__ import annotations

import html
from typing import Any, Iterable, Optional

import streamlit as st

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600;12..96,700;12..96,800&family=Hanken+Grotesk:wght@400;500;600;700;800&family=Inter:wght@300;400;500;600;700&family=Space+Mono:wght@400;700&family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&family=Noto+Sans+SC:wght@400;500;700&display=swap');

/* ======================================================================
   1. 令牌
   ====================================================================== */
:root{
  /* 底与墨色 —— 取自 scrolltide 的 ink / bone / mist / dim 色阶 */
  --ct-canvas:#07080A;          /* ink 底 */
  --ct-canvas-2:#0B0D10;        /* ink-2 */
  --ct-surface:#101318;         /* surface */
  --ct-ink:#F3F5F8;             /* bone 主文字 */
  --ct-ink-2:#C7CDD6;           /* 正文 */
  --ct-ink-3:#9AA2AD;           /* mist 次要说明 */
  --ct-ink-4:#5B636E;           /* dim 弱化文字 */

  /* 品牌 —— tide 蓝 / foam 青 */
  --ct-blue:#46B7FF;
  --ct-blue-500:#7CCBFF;
  --ct-blue-700:#A9DDFF;        /* 暗底上的强调文字 */
  --ct-blue-deep:#2E7DFF;       /* 渐变深处 */
  --ct-blue-tint:rgba(70,183,255,.14);
  --ct-pink:#FF6FA5;
  --ct-pink-500:#FF8FBE;
  --ct-pink-tint:rgba(255,111,165,.14);

  /* 业务语义色（暗底提亮，保证对比度） */
  --ct-mint:#8BF3E6;
  --ct-mint-500:#5FE3D2;
  --ct-mint-tint:rgba(139,243,230,.13);
  --ct-amber:#F5B04C;
  --ct-amber-500:#FFC46B;
  --ct-amber-tint:rgba(245,176,76,.13);
  --ct-coral:#FF8A5B;
  --ct-coral-tint:rgba(255,138,91,.13);
  --ct-violet:#7C93FF;
  --ct-cyan:#4FD8E8;
  --ct-red:#FF6B6B;

  /* 无边界的关键：用 box-shadow 环代替 border（暗底 = 白 8%） */
  --ct-ring:rgba(255,255,255,.08);
  --ct-ring-2:rgba(255,255,255,.14);
  --ct-ring-3:rgba(255,255,255,.22);

  /* 圆角 —— coolors 原生 10 / 14 / 15 / 100 */
  --ct-r-xs:8px;
  --ct-r-sm:10px;
  --ct-r:14px;
  --ct-r-lg:20px;
  --ct-r-xl:28px;
  --ct-r-pill:100px;

  /* 阴影 —— 暗底用深黑扩散 + 白环 */
  --ct-shadow-xs:0 1px 3px rgba(0,0,0,.34), 0 0 0 1px var(--ct-ring);
  --ct-shadow-sm:0 6px 22px rgba(0,0,0,.44), 0 0 0 1px var(--ct-ring);
  --ct-shadow:0 12px 44px rgba(0,0,0,.55), 0 0 0 1px var(--ct-ring);
  --ct-shadow-lg:0 30px 84px rgba(0,0,0,.66), 0 0 0 1px var(--ct-ring);
  --ct-shadow-blue:0 10px 34px rgba(70,183,255,.26);
  --ct-shadow-pink:0 10px 34px rgba(255,111,165,.22);

  /* 玻璃 —— 暗底玻璃 = 白 6~9% + 强模糊 */
  --ct-glass:rgba(255,255,255,.055);
  --ct-glass-2:rgba(255,255,255,.085);
  --ct-glass-3:rgba(13,16,21,.94);
  --ct-blur:blur(22px) saturate(140%);
  --ct-blur-sm:blur(12px) saturate(130%);
  --ct-blur-lg:blur(40px) saturate(150%);

  /* 动效 —— coolors 两段 + scrolltide 弹簧曲线 */
  --ct-ease:cubic-bezier(.3,.4,0,1);
  --ct-ease-2:cubic-bezier(.6,.4,0,1);
  --ct-ease-spring:cubic-bezier(.22,1,.36,1);
  --ct-fast:180ms;
  --ct-mid:340ms;
  --ct-slow:640ms;

  /* 字体
     展示字用 Hanken Grotesk —— threejs-journey 用的是商用字体 Greycliff CF，
     Hanken Grotesk 是同族（neo-grotesque、大 x 高度、方终端）的免费替代，
     导航与标题的字面气质对得上。断网时自动回落到系统栈。 */
  --ct-font-display:'Bricolage Grotesque','Hanken Grotesk','Noto Sans SC','PingFang SC','Microsoft YaHei',sans-serif;
  --ct-font-body:'Inter','Manrope','Noto Sans SC','PingFang SC','Microsoft YaHei',sans-serif;
  --ct-font-mono:'Space Mono','JetBrains Mono',ui-monospace,'SFMono-Regular',Consolas,monospace;
}

/* ======================================================================
   2. 画布：潮汐极光暗底 + 颗粒噪点（玻璃的“被折射物”）

   ⚠️ 铁律：.stApp 绝对不能加 position / transform / filter / contain /
   will-change 这类会「成为绝对定位后代包含块」的属性。
   Streamlit 的 .stAppViewContainer 是 position:absolute; inset:0，
   它的包含块默认是视口；一旦 .stApp 变成定位祖先，它的 inset 就相对
   .stApp 解析，而 .stApp 自身高度是 0（唯一的子容器是绝对定位），
   再叠加 .stApp 自带的 overflow:hidden —— 整页内容会被裁成 0 高，
   结果就是「DOM 有、布局有、就是画不出来」。
   （v2 改造踩过这个坑，见 docs/47 §6）
   ====================================================================== */
html, body, .stApp{
  background-color:var(--ct-canvas);
}
.stApp{
  /* 底图：AI 生成的潮汐极光（29KB）+ 墨蓝渐变兜底。
     它是玻璃层要「折射」的东西——没有它，backdrop-filter 的模糊就无从谈起。
     background-attachment:fixed 是必须的：.stApp 高度为 0，
     只有固定背景才会铺满视口（内容靠 .block-container 的 z-index 压在上面）。 */
  background-image:
    linear-gradient(rgba(6,8,12,.80), rgba(6,8,12,.90)),
    url('/app/static/bg_tide_glow.jpg'),
    linear-gradient(180deg, #07080A 0%, #080B11 52%, #0A0E16 100%);
  background-size:cover, cover, cover;
  background-position:center top, center, center;
  background-attachment:fixed;
  background-repeat:no-repeat;
  color:var(--ct-ink);
  font-family:var(--ct-font-body);
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
}
/* 潮汐层：四团光晕缓慢漂移（scrolltide tide-glow 的 CSS 化） */
.stApp::before{
  content:'';
  position:fixed; inset:-18%; z-index:0; pointer-events:none;
  background:
    radial-gradient(38% 34% at 22% 18%, rgba(70,183,255,.14), transparent 70%),
    radial-gradient(34% 30% at 78% 24%, rgba(139,243,230,.09), transparent 70%),
    radial-gradient(44% 38% at 64% 86%, rgba(124,147,255,.10), transparent 72%),
    radial-gradient(30% 28% at 10% 76%, rgba(255,111,165,.07), transparent 70%);
  filter:blur(60px);
  animation:cm-tide 42s var(--ct-ease) infinite alternate;
  will-change:transform;
}
/* 颗粒：scrolltide 的 .grain —— 叠加混合，让暗底有胶片质感 */
.stApp::after{
  content:'';
  position:fixed; inset:0; z-index:9998; pointer-events:none;
  opacity:.045; mix-blend-mode:overlay;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/%3E%3C/svg%3E");
}

.block-container{
  position:relative; z-index:1;
  padding-top:1.1rem !important;
  padding-bottom:4.5rem !important;
  max-width:1480px !important;
}
[data-testid="stHeader"]{ background:transparent; }
[data-testid="stToolbar"]{ display:none; }
#MainMenu, footer, [data-testid="stDecoration"] { visibility:hidden; }
[data-testid="stSidebar"]{ display:none; }

/* 滚动条 */
::-webkit-scrollbar{ width:11px; height:11px; }
::-webkit-scrollbar-track{ background:transparent; }
::-webkit-scrollbar-thumb{
  background:rgba(255,255,255,.14); border-radius:100px;
  border:3px solid transparent; background-clip:content-box;
}
::-webkit-scrollbar-thumb:hover{ background:rgba(255,255,255,.26); background-clip:content-box; }

/* ======================================================================
   3. 排版
   ====================================================================== */
h1,h2,h3,h4,h5,h6,.cm-display,.cm-h{
  font-family:var(--ct-font-display);
  color:var(--ct-ink);
  letter-spacing:-.02em;
  line-height:1.12;
}
h1{ font-size:2.1rem; font-weight:700; }
h2{ font-size:1.55rem; font-weight:700; }
h3{ font-size:1.15rem; font-weight:600; }
p,span,div,label,li,td,th{
  font-family:var(--ct-font-body);
  color:var(--ct-ink-2);
}
strong,b{ color:var(--ct-ink); font-weight:700; }
code,pre,[data-testid="stCode"]{
  font-family:var(--ct-font-mono) !important;
  font-variant-numeric:tabular-nums;
}
hr{
  border:none !important; height:1px !important;
  background:var(--ct-ring) !important; margin:1.6rem 0 !important;
}
a{ color:var(--ct-blue); text-decoration:none; }
a:hover{ color:var(--ct-blue-700); }
[dir="rtl"] p, [dir="rtl"] span{ font-feature-settings:"tnum"; }
.cm-num{ font-family:var(--ct-font-mono); font-variant-numeric:tabular-nums; letter-spacing:-.02em; }
.cm-caps{
  font-family:var(--ct-font-display); text-transform:uppercase;
  letter-spacing:.16em; font-size:10.5px; font-weight:600; color:var(--ct-ink-4);
}

/* ======================================================================
   4. 顶栏：无边界（参考 threejs-journey）
   ----------------------------------------------------------------------
   threejs-journey 的导航有三条关键设计语言，这里逐条搬过来：

     ① **无容器**：header 本身没有圆角、没有描边、没有阴影，就是一层薄薄的
        渐隐遮罩贴在画布上——内容像浮在页面里，而不是装在一个盒子里。
     ② **高亮块不是胶囊**：激活项不是实心圆角按钮，而是
        「3px 顶部色条 + 自左向右展开的渐变水洗」（原站
        .link .highlight 的做法：transform:scaleX(0→1) + opacity）。
     ③ **状态不装盒**：REAL / GATE PASS 是纯文字，不是胶囊徽章；
        唯一的实心件是右侧那颗 CTA 药丸。
   ====================================================================== */
div[class*="st-key-cm_header_card"], .st-key-cm_header_card{
  position:sticky; top:0; z-index:900;
  /* 无边界：没有圆角、没有描边、没有投影 */
  border-radius:0 !important;
  box-shadow:none !important;
  background:linear-gradient(180deg,
    rgba(7,8,10,.92) 0%,
    rgba(7,8,10,.72) 58%,
    rgba(7,8,10,0) 100%) !important;
  -webkit-backdrop-filter:blur(14px) saturate(130%);
  backdrop-filter:blur(14px) saturate(130%);
  padding:.75rem .1rem .55rem !important;
  margin-bottom:.5rem !important;
}
div[class*="st-key-cm_header_card"] > *, .st-key-cm_header_card > *{ position:relative; z-index:1; }
/* 三列改成一行横排：品牌 | 导航 | 状态+控制台 */
div[class*="st-key-cm_header_card"] [data-testid="stHorizontalBlock"],
.st-key-cm_header_card [data-testid="stHorizontalBlock"]{ align-items:center; }
/* min-width:0 是关键：Streamlit 的列默认 min-width:auto，
   导航 10 项的最小内容宽度会把三列挤到换行（窄屏时整条顶栏会塌成三行）。 */
div[class*="st-key-cm_header_card"] [data-testid="stColumn"],
.st-key-cm_header_card [data-testid="stColumn"]{ min-width:0 !important; }
/* 只有**右列**改成横排（状态文字 + λ 药丸并排）。
   品牌列与导航列保持 Streamlit 原生的纵向块——把整列改成行布局会让
   导航行的基准宽度按内容算，链接被挤到列的右侧、压到状态区上。 */
div[class*="st-key-cm_header_card"] [data-testid="stColumn"]:last-child > div,
.st-key-cm_header_card [data-testid="stColumn"]:last-child > div{
  display:flex !important; flex-direction:row-reverse !important;
  align-items:center !important; justify-content:flex-start !important;
  gap:.55rem !important; min-width:0;
}

/* Streamlit 的 markdown 容器默认 width:fit-content（文字不撑满），
   但导航行是超宽的 flex 容器，fit-content 会把它的 max-content 宽度
   顶到列外面，三列加起来比容器宽 → 整页出现横向滚动条、右侧 CTA 被切。
   这里统一改成 width:100% + min-width:0，让内容在该列内部自己滚动。 */
div[class*="st-key-cm_header_card"] [data-testid="stColumn"] [data-testid="stMarkdownContainer"],
.st-key-cm_header_card [data-testid="stColumn"] [data-testid="stMarkdownContainer"]{
  width:100% !important; min-width:0 !important;
}
div[class*="st-key-cm_header_card"] [data-testid="stColumn"] [data-testid="stElementContainer"],
.st-key-cm_header_card [data-testid="stColumn"] [data-testid="stElementContainer"]{
  min-width:0 !important; max-width:100% !important;
}

/* 可见导航：自绘链接；原生 page_link 只做路由锚点 */
div[class*="st-key-cm_nav_source"], .st-key-cm_nav_source{ display:none !important; }
.cm-nav-row{
  display:flex !important; flex-wrap:nowrap !important;
  justify-content:flex-start !important; align-items:center !important;
  gap:.1rem !important; width:100%; min-width:0 !important;
  overflow-x:auto; overflow-y:hidden; scrollbar-width:none;
}
.cm-nav-row::-webkit-scrollbar{ display:none; }
.cm-nav-link{
  position:relative; display:inline-flex !important; align-items:center;
  flex:0 0 auto !important; margin:0 !important;
  padding:.5rem .5rem .42rem !important;
  font-family:var(--ct-font-display) !important;
  font-size:13px !important; font-weight:600 !important;
  color:var(--ct-ink-2) !important; text-decoration:none !important;
  white-space:nowrap; opacity:.55;
  transition:opacity var(--ct-mid) var(--ct-ease), color var(--ct-mid) var(--ct-ease);
}
.cm-nav-link:hover{ opacity:1; color:var(--ct-ink) !important; }
/* ③ 高亮块：3px 顶线 + 自左展开的渐变水洗（原站 .link .highlight 的机制） */
.cm-nav-hl{
  position:absolute; inset:0; border-radius:var(--ct-r);
  pointer-events:none; opacity:0; transform:scaleX(.15); transform-origin:left center;
  transition:opacity var(--ct-mid) var(--ct-ease), transform var(--ct-mid) var(--ct-ease);
  background:linear-gradient(180deg, rgba(70,183,255,.22), rgba(70,183,255,0) 88%);
}
.cm-nav-hl::before{
  content:''; position:absolute; top:.05rem; left:.6rem; right:.6rem; height:3px;
  border-radius:100px; background:linear-gradient(90deg,var(--ct-blue),var(--ct-mint));
}
.cm-nav-link:hover .cm-nav-hl{ opacity:.75; transform:scaleX(1); }
.cm-nav-link.is-active{ opacity:1; color:var(--ct-ink) !important; }
.cm-nav-link.is-active .cm-nav-hl{ opacity:1; transform:scaleX(1); }
.cm-nav-label{ position:relative; z-index:1; }

/* 品牌 */
.cm-brand{ display:flex; align-items:center; gap:.55rem; overflow:hidden; }
.cm-brand .mark{
  display:inline-flex; align-items:center; justify-content:center; flex:none;
  width:32px; height:32px; border-radius:10px;
  background:linear-gradient(145deg,var(--ct-blue-500),var(--ct-blue-deep));
  color:#06121F; font-family:var(--ct-font-display); font-weight:700; font-size:16px;
  box-shadow:0 6px 20px rgba(70,183,255,.30);
}
.cm-brand .word{
  display:block; font-family:var(--ct-font-display); font-weight:700;
  font-size:15.5px; color:var(--ct-ink); line-height:1.12; letter-spacing:-.015em;
  white-space:nowrap;
}
.cm-brand .word em{ font-style:normal; color:var(--ct-blue); }
.cm-brand .sub{
  display:block; font-family:var(--ct-font-mono); font-size:7px;
  letter-spacing:.08em; color:var(--ct-ink-4); text-transform:uppercase; margin-top:2px;
  white-space:nowrap; overflow:hidden; text-overflow:clip;
}
.cm-brand-text{ min-width:0; }

/* 状态：纯文字，不装盒（原站 Login 的处理方式） */
.cm-head-status{ display:flex; align-items:center; gap:.4rem; flex-wrap:nowrap; min-width:0; }
.cm-head-pill{
  display:inline-flex; align-items:center; gap:.28rem;
  font-family:var(--ct-font-mono); font-size:9px; font-weight:600;
  letter-spacing:.06em; color:var(--ct-ink-4); white-space:nowrap;
}
.cm-head-pill.live{ color:var(--ct-mint); }
.cm-head-pill.live .cm-dot{
  width:6px; height:6px; border-radius:50%; background:var(--ct-mint);
  box-shadow:0 0 0 3px rgba(16,185,129,.18);
  animation:cm-pulse 2.4s var(--ct-ease) infinite;
}
.cm-head-pill.ok{ color:var(--ct-ink-3); }
.cm-head-sep{ color:var(--ct-ink-4); opacity:.5; font-family:var(--ct-font-mono); }

/* 实时 λ 数值：纯文字，排在状态行里（不是药丸的 label——
   label 在 popover 创建时就定死了，写数字会慢一帧）。 */
.cm-head-lam{
  font-family:var(--ct-font-mono); font-size:11px; font-weight:600;
  letter-spacing:.04em; color:var(--ct-blue-700); white-space:nowrap;
  padding:.2rem .45rem; border-radius:var(--ct-r-sm);
  background:rgba(70,183,255,.12); box-shadow:0 0 0 1px rgba(70,183,255,.26);
}
/* 右列：状态行 + 药丸。
   DOM 顺序是「药丸先、状态行后」——因为药丸必须在状态行之前渲染，
   才能拿到用户刚改的 λ（st.popover 的 label 在创建时就定死，数字只能后补）。
   所以用 flex-direction:row-reverse 把视觉顺序翻回来：状态在左、药丸在右。
   （试过用 order + :has()，命中的元素不是真正的弹性项，布局会塌，不采用。） */
div[class*="st-key-cm_header_card"] [data-testid="stColumn"]:last-child > div,
.st-key-cm_header_card [data-testid="stColumn"]:last-child > div{
  display:flex !important; flex-direction:row-reverse !important;
  align-items:center !important; justify-content:flex-start !important;
  gap:.55rem !important; min-width:0;
}
div[class*="st-key-cm_header_card"] [data-testid="stPopover"],
.st-key-cm_header_card [data-testid="stPopover"]{
  flex:0 0 auto !important; width:auto !important; min-width:0 !important;
}

/* 右侧 CTA：整条顶栏里唯一的实心件（原站的 “Start learning now”）。
   Streamlit 的 popover 按钮默认宽度 100%，在窄列里会把标签压成「···」，
   所以这里给容器一个固定宽度，并让标签的省略号失效。
   注意真实节点是 [data-testid="stPopoverButton"]（stPopover > div > button）。 */
div[class*="st-key-cm_header_card"] [data-testid="stPopover"],
.st-key-cm_header_card [data-testid="stPopover"]{
  flex:0 0 auto !important; width:100% !important; min-width:0 !important;
}
div[class*="st-key-cm_header_card"] [data-testid="stPopover"] > div,
.st-key-cm_header_card [data-testid="stPopover"] > div{ width:100% !important; }
div[class*="st-key-cm_header_card"] [data-testid="stPopover"] .stMarkdownEllipsis,
.st-key-cm_header_card [data-testid="stPopover"] .stMarkdownEllipsis{
  max-width:none !important; overflow:visible !important; text-overflow:clip !important;
}
div[class*="st-key-cm_header_card"] [data-testid="stPopoverButton"],
.st-key-cm_header_card [data-testid="stPopoverButton"]{
  border-radius:var(--ct-r-pill) !important;
  border:none !important;
  width:100% !important; max-width:none !important; white-space:nowrap !important;
  background:var(--ct-ink) !important;
  color:#fff !important;
  font-family:var(--ct-font-mono) !important;
  font-size:10.5px !important; font-weight:600 !important; letter-spacing:.02em !important;
  padding:.48rem .8rem !important;
  box-shadow:0 8px 22px rgba(10,10,10,.18) !important;
  transition:transform var(--ct-fast) var(--ct-ease), box-shadow var(--ct-fast) var(--ct-ease),
             background var(--ct-fast) var(--ct-ease) !important;
}
div[class*="st-key-cm_header_card"] [data-testid="stPopoverButton"]:hover,
.st-key-cm_header_card [data-testid="stPopoverButton"]:hover{
  background:#000 !important; color:#fff !important;
  transform:translateY(-2px);
  box-shadow:0 12px 28px rgba(10,10,10,.26) !important;
}
div[class*="st-key-cm_header_card"] [data-testid="stPopoverButton"] p,
.st-key-cm_header_card [data-testid="stPopoverButton"] p{
  color:#fff !important; font-family:var(--ct-font-mono) !important;
  font-size:10.5px !important; font-weight:600 !important; letter-spacing:.02em !important;
}
/* 弹出的面板：给一个舒服的宽度，别把页面撑出横向滚动条 */
div[data-baseweb="popover"] > div{
  background:var(--ct-glass-3) !important;
  -webkit-backdrop-filter:var(--ct-blur); backdrop-filter:var(--ct-blur);
  border-radius:var(--ct-r-lg) !important; box-shadow:var(--ct-shadow-lg) !important;
  min-width:280px !important; max-width:min(88vw,380px) !important;
  width:max-content !important;
}
div[class*="st-key-cm_header_card"] [data-testid="stPopover"] > button:hover,
.st-key-cm_header_card [data-testid="stPopover"] > button:hover{
  background:#000 !important; color:#fff !important;
  transform:translateY(-2px);
  box-shadow:0 12px 28px rgba(10,10,10,.26) !important;
}
div[class*="st-key-cm_header_card"] [data-testid="stPopover"] [data-testid="stMarkdownContainer"] p,
.st-key-cm_header_card [data-testid="stPopover"] [data-testid="stMarkdownContainer"] p{
  color:#fff !important; font-family:var(--ct-font-mono) !important;
}

/* ======================================================================
   5. Hero —— coolors 式：色板条 + 超大字 + 高亮标记
   ====================================================================== */
.cm-hero{
  position:relative; overflow:hidden;
  border-radius:var(--ct-r-xl);
  background:var(--ct-glass-2);
  -webkit-backdrop-filter:var(--ct-blur-lg);
  backdrop-filter:var(--ct-blur-lg);
  box-shadow:var(--ct-shadow-lg);
  padding:2.6rem 2.8rem 2.4rem;
  margin-bottom:1.6rem;
  animation:cm-rise var(--ct-slow) var(--ct-ease) backwards;
}
.cm-hero.compact{ padding:1.9rem 2.2rem 1.7rem; }
.cm-hero::before{
  content:''; position:absolute; inset:0; pointer-events:none; z-index:0;
  background:
    radial-gradient(620px 320px at 88% -20%, rgba(70,183,255,.20), transparent 70%),
    radial-gradient(520px 300px at 8% 118%,  rgba(255,111,165,.13), transparent 70%);
}
/* 渐变网格底图（AI 生成的柔和色斑，压缩到 46KB）：
   它是玻璃层要「折射」的东西——没有它，backdrop-filter 的模糊就无从谈起。
   走 Streamlit 静态服务（/app/static/…）；取不到时 ::before 的 CSS 渐变兜底。 */
.cm-hero::after{
  content:''; position:absolute; inset:0; pointer-events:none; z-index:0;
  background-image:url('/app/static/hero_carbon_mesh.jpg');
  background-size:cover; background-position:center 20%;
  opacity:.42; mix-blend-mode:screen;
  -webkit-mask-image:linear-gradient(180deg,rgba(0,0,0,.95),rgba(0,0,0,.30));
  mask-image:linear-gradient(180deg,rgba(0,0,0,.95),rgba(0,0,0,.30));
}
.cm-hero-inner{ position:relative; z-index:2; max-width:min(980px, 100%); }
.cm-kicker{
  display:inline-flex; align-items:center; gap:.5rem;
  font-family:var(--ct-font-display); font-size:10.5px; font-weight:600;
  letter-spacing:.2em; text-transform:uppercase; color:var(--ct-ink-3);
  margin-bottom:.9rem;
}
.cm-kicker .dot{
  width:9px; height:9px; border-radius:50%;
  background:var(--ct-pink-500); box-shadow:0 0 0 4px rgba(255,143,190,.16);
}
.cm-display{
  font-family:var(--ct-font-display); font-weight:700;
  font-size:clamp(2.1rem,4.4vw,3.65rem); line-height:1.04;
  letter-spacing:-.035em; color:var(--ct-ink); margin-bottom:.85rem;
}
.cm-display.long{ font-size:clamp(1.75rem,3.3vw,2.85rem); }
.cm-mark{
  background:linear-gradient(180deg, rgba(245,176,76,.26) 0%, rgba(245,176,76,.26) 58%, transparent 58%);
  padding:0 .12em; border-radius:4px; color:inherit;
}
.cm-sub{
  font-size:16.5px; line-height:1.72; color:var(--ct-ink-2);
  max-width:740px; margin-bottom:1.1rem;
}
.cm-hero-index{
  position:absolute; top:1.2rem; right:1.6rem; z-index:2;
  font-family:var(--ct-font-mono); font-size:52px; font-weight:700;
  color:rgba(255,255,255,.06); letter-spacing:-.06em; line-height:1;
}
.cm-chip{
  display:inline-flex; align-items:center; margin:0 .4rem .5rem 0;
  padding:.34rem .78rem; border-radius:var(--ct-r-pill);
  background:rgba(255,255,255,.07); color:var(--ct-ink-2);
  font-family:var(--ct-font-body); font-size:12px; font-weight:600;
  box-shadow:0 0 0 1px var(--ct-ring);
}
.cm-chip.ok, .cm-chip.mint{ background:rgba(139,243,230,.13); color:var(--ct-mint); box-shadow:0 0 0 1px rgba(139,243,230,.28); }
.cm-chip.warn, .cm-chip.amber{ background:rgba(245,176,76,.14); color:var(--ct-amber-500); box-shadow:0 0 0 1px rgba(245,176,76,.32); }
.cm-chip.bad, .cm-chip.red{ background:rgba(255,107,107,.12); color:#FF9A9A; box-shadow:0 0 0 1px rgba(255,107,107,.26); }
.cm-chip.skip, .cm-chip.plain{ background:rgba(255,255,255,.06); color:var(--ct-ink-3); box-shadow:0 0 0 1px var(--ct-ring); }
.cm-chip.info, .cm-chip.blue{ background:rgba(70,183,255,.14); color:var(--ct-blue-700); box-shadow:0 0 0 1px rgba(70,183,255,.26); }
.cm-chip.main{ background:rgba(245,176,76,.16); color:var(--ct-amber-500); font-weight:700; box-shadow:0 0 0 1px rgba(245,176,76,.38); }
.cm-chip.coral{ background:rgba(255,138,91,.12); color:#FFB08A; box-shadow:0 0 0 1px rgba(255,138,91,.26); }
.cm-hero-actions{ display:flex; flex-wrap:wrap; gap:.6rem; margin-top:1.15rem; }

/* coolors 标志性的色板条 */
.cm-palette{
  position:relative; z-index:2;
  display:flex; gap:10px; height:118px; margin-top:1.7rem;
}
.cm-palette-bar{
  flex:1; border-radius:var(--ct-r); position:relative;
  box-shadow:0 6px 22px rgba(0,0,0,.45), 0 0 0 1px rgba(255,255,255,.08);
  transform-origin:bottom center;
  animation:cm-grow .95s var(--ct-ease) backwards;
  transition:transform var(--ct-mid) var(--ct-ease), filter var(--ct-mid) var(--ct-ease);
}
.cm-palette-bar:hover{ transform:translateY(-8px) scaleY(1.06); filter:saturate(1.15); }
.cm-palette-bar span{
  position:absolute; left:10px; bottom:8px;
  font-family:var(--ct-font-mono); font-size:9.5px; font-weight:600;
  letter-spacing:.06em; color:rgba(255,255,255,.92);
  text-shadow:0 1px 3px rgba(0,0,0,.35);
}
.cm-palette-bar.dark span{ color:rgba(255,255,255,.75); }

/* ======================================================================
   6. 按钮
   ====================================================================== */
.cm-btn{
  display:inline-flex; align-items:center; gap:.45rem;
  padding:.72rem 1.4rem; border-radius:var(--ct-r-pill);
  font-family:var(--ct-font-body); font-size:14px; font-weight:700;
  letter-spacing:.005em; text-decoration:none !important;
  transition:transform var(--ct-fast) var(--ct-ease), box-shadow var(--ct-fast) var(--ct-ease),
             background var(--ct-fast) var(--ct-ease);
  white-space:nowrap;
}
.cm-btn span{ transition:transform var(--ct-fast) var(--ct-ease); display:inline-block; }
.cm-btn:hover{ transform:translateY(-2px); }
.cm-btn:hover span{ transform:translateX(4px); }
.cm-btn.primary{
  background:var(--ct-blue); color:#06121F !important;
  box-shadow:var(--ct-shadow-blue);
}
.cm-btn.primary:hover{ background:var(--ct-blue-500); color:#06121F !important; }
.cm-btn.ghost{
  background:rgba(255,255,255,.06); color:var(--ct-ink) !important;
  box-shadow:0 0 0 1px var(--ct-ring), 0 6px 18px rgba(0,0,0,.35);
  -webkit-backdrop-filter:var(--ct-blur-sm);
  backdrop-filter:var(--ct-blur-sm);
}
.cm-btn.ghost:hover{ background:rgba(255,255,255,.12); color:var(--ct-ink) !important; }

/* Streamlit 原生按钮同款语言 */
.stButton > button, .stDownloadButton > button{
  border-radius:var(--ct-r-pill) !important;
  border:none !important;
  background:var(--ct-glass-2) !important;
  color:var(--ct-ink) !important;
  font-family:var(--ct-font-body) !important;
  font-weight:700 !important; font-size:13.5px !important;
  padding:.55rem 1.15rem !important;
  box-shadow:0 0 0 1px var(--ct-ring-2), 0 4px 14px rgba(0,0,0,.35) !important;
  transition:transform var(--ct-fast) var(--ct-ease), box-shadow var(--ct-fast) var(--ct-ease),
             background-color var(--ct-fast) var(--ct-ease) !important;
}
.stButton > button:hover, .stDownloadButton > button:hover{
  transform:translateY(-2px);
  box-shadow:0 0 0 1px var(--ct-ring-2), 0 10px 26px rgba(0,0,0,.50) !important;
}
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"]{
  background:var(--ct-blue) !important; color:#06121F !important;
  box-shadow:var(--ct-shadow-blue) !important;
}
/* Streamlit 的按钮文案是内层 <p>，不继承 button 的 color —— 要单独压 */
.stButton > button p, .stDownloadButton > button p,
.stButton > button span, .stDownloadButton > button span{
  color:inherit !important; font-family:var(--ct-font-body) !important;
  font-weight:700 !important; font-size:13.5px !important;
}

/* ======================================================================
   7. 数据带 / 区块标题
   ====================================================================== */
.cm-marquee{
  overflow:hidden; border-radius:var(--ct-r-pill);
  background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur);
  backdrop-filter:var(--ct-blur);
  box-shadow:var(--ct-shadow-sm);
  padding:.7rem 0; margin:.2rem 0 2rem;
}
.cm-marquee-track{
  display:inline-flex; gap:2.4rem; white-space:nowrap;
  animation:cm-slide 34s linear infinite;
}
.cm-marquee-track span{ display:inline-flex; align-items:center; gap:1.1rem; }
.cm-marquee-track b{
  font-family:var(--ct-font-mono); font-size:12px; font-weight:600;
  letter-spacing:.1em; color:var(--ct-ink-2);
}
.cm-marquee-track i{ color:var(--ct-mint); font-style:normal; font-size:11px; }

.cm-sec{
  display:flex; align-items:baseline; gap:1rem; flex-wrap:wrap;
  margin:2.9rem 0 1.35rem; padding-bottom:.85rem;
  border-bottom:1px solid var(--ct-ring);
  animation:cm-rise var(--ct-slow) var(--ct-ease) backwards;
}
.cm-sec .t{
  font-family:var(--ct-font-display); font-size:clamp(1.3rem,2vw,1.75rem);
  font-weight:700; letter-spacing:-.025em; color:var(--ct-ink);
  position:relative; padding-left:.9rem;
}
.cm-sec .t::before{
  content:''; position:absolute; left:0; top:.22em; bottom:.18em;
  width:5px; border-radius:100px;
  background:linear-gradient(180deg,var(--ct-blue),var(--ct-mint));
}
.cm-sec .d{ font-size:13.5px; color:var(--ct-ink-3); }

/* ======================================================================
   8. 卡片：玻璃面板
   ====================================================================== */
.cm-stats{
  display:grid; gap:14px; margin:.2rem 0 1.1rem;
}
.cm-stat{
  position:relative; overflow:hidden;
  background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur);
  backdrop-filter:var(--ct-blur);
  border-radius:var(--ct-r-lg);
  box-shadow:var(--ct-shadow-sm);
  padding:1.05rem 1.15rem 1rem;
  min-height:112px;
  transition:transform var(--ct-mid) var(--ct-ease), box-shadow var(--ct-mid) var(--ct-ease);
  animation:cm-rise var(--ct-slow) var(--ct-ease) backwards;
}
.cm-stat:hover{ transform:translateY(-4px); box-shadow:var(--ct-shadow); }
.cm-stat::before{
  content:''; position:absolute; left:0; right:0; top:0; height:3px;
  background:var(--ct-accent, var(--ct-blue)); opacity:.92;
}
.cm-stat .l{
  display:block; font-family:var(--ct-font-display); font-size:10px; font-weight:600;
  letter-spacing:.14em; text-transform:uppercase; color:var(--ct-ink-4);
}
.cm-stat .v{
  display:block; font-family:var(--ct-font-mono); font-size:27px; font-weight:700;
  letter-spacing:-.03em; color:var(--ct-ink); line-height:1.12; margin-top:.45rem;
  font-variant-numeric:tabular-nums;
}
.cm-stat .s{ display:block; font-size:11.5px; line-height:1.6; color:var(--ct-ink-3); margin-top:.42rem; }
.cm-stat.tone-mint{ --ct-accent:var(--ct-mint-500); }
.cm-stat.tone-info{ --ct-accent:var(--ct-blue-500); }
.cm-stat.tone-amber{ --ct-accent:var(--ct-amber-500); }
.cm-stat.tone-coral{ --ct-accent:var(--ct-coral); }
.cm-stat.tone-pink{ --ct-accent:var(--ct-pink-500); }
.cm-stat.tone-plain{ --ct-accent:var(--ct-ink-4); }
.cm-stat.mint .v, .cm-stat.ok .v{ color:var(--ct-mint); }
.cm-stat.amber .v, .cm-stat.warn .v{ color:var(--ct-amber); }
.cm-stat.coral .v{ color:var(--ct-coral); }
.cm-stat.red .v, .cm-stat.bad .v{ color:var(--ct-red); }
.cm-stat.info .v, .cm-stat.blue .v{ color:var(--ct-blue); }

/* ======================================================================
   9. Bento 模块卡
   ====================================================================== */
.cm-bento{
  display:grid; grid-template-columns:repeat(4,1fr); gap:16px;
}
.cm-bento-card{
  display:flex; flex-direction:column; overflow:hidden; text-decoration:none !important;
  background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur);
  backdrop-filter:var(--ct-blur);
  border-radius:var(--ct-r-xl);
  box-shadow:var(--ct-shadow-sm);
  transition:transform var(--ct-mid) var(--ct-ease), box-shadow var(--ct-mid) var(--ct-ease);
  animation:cm-rise var(--ct-slow) var(--ct-ease) backwards;
}
.cm-bento-card:hover{ transform:translateY(-6px); box-shadow:var(--ct-shadow-lg); }
.cm-bento-card.span2{ grid-column:span 2; }
.cm-bento-card.span4{ grid-column:span 4; }
.cm-card-visual{
  position:relative; height:126px; padding:14px 16px;
  background:
    linear-gradient(135deg, rgba(70,183,255,.14), rgba(255,111,165,.10)),
    var(--ct-canvas-2);
  box-shadow:inset 0 -1px 0 var(--ct-ring);
}
.cm-v-line{
  position:absolute; left:16px; right:16px; bottom:18px; height:1px;
  background:rgba(255,255,255,.08);
}
.cm-v-tag{
  position:absolute; top:12px; right:14px; z-index:2;
  font-family:var(--ct-font-mono); font-size:9.5px; font-weight:600; letter-spacing:.08em;
  color:var(--ct-ink-3); background:rgba(255,255,255,.08);
  padding:.24rem .55rem; border-radius:var(--ct-r-pill);
  box-shadow:0 0 0 1px var(--ct-ring);
}
.cm-v-bars{ display:flex; align-items:flex-end; gap:6px; height:92px; padding-top:10px; }
.cm-v-bars i{
  flex:1; border-radius:6px 6px 3px 3px; min-height:6px;
  background:linear-gradient(180deg,var(--ct-blue-500),var(--ct-blue-700));
  opacity:.9;
  animation:cm-grow .8s var(--ct-ease) backwards;
}
.cm-v-bars i:nth-child(3n+1){ background:linear-gradient(180deg,var(--ct-mint-500),var(--ct-mint)); }
.cm-v-bars i:nth-child(4n+2){ background:linear-gradient(180deg,var(--ct-amber-500),var(--ct-amber)); }
.cm-v-bars i:nth-child(5n+3){ background:linear-gradient(180deg,var(--ct-pink-500),var(--ct-pink)); }
.cm-v-grid{ display:grid; grid-template-columns:repeat(9,1fr); gap:5px; height:100%; }
.cm-v-grid i{ border-radius:4px; background:rgba(255,111,165,.28); }
.cm-v-grid i:nth-child(4n+1){ background:rgba(70,183,255,.42); }
.cm-v-grid i:nth-child(7n+3){ background:rgba(245,176,76,.42); }
.cm-v-rows{ display:flex; flex-direction:column; gap:7px; height:100%; justify-content:center; }
.cm-v-rows i{ height:9px; border-radius:100px; background:rgba(255,255,255,.10); }
.cm-v-rows i:nth-child(2){ width:78%; } .cm-v-rows i:nth-child(4){ width:62%; }
.cm-v-compare{ display:flex; align-items:flex-end; gap:8px; height:100%; padding-top:14px; }
.cm-v-compare i{
  flex:1; border-radius:6px 6px 3px 3px; min-height:6px;
  background:linear-gradient(180deg,var(--ct-mint-500),var(--ct-mint));
}
.cm-v-compare i:nth-child(1){ height:78%; background:linear-gradient(180deg,#FFB07A,var(--ct-coral)); }
.cm-v-compare i:nth-child(2){ height:42%; background:linear-gradient(180deg,var(--ct-blue-500),var(--ct-blue-700)); }
.cm-v-compare i:nth-child(3){ height:58%; background:linear-gradient(180deg,var(--ct-amber-500),var(--ct-amber)); }
.cm-v-compare i:nth-child(4){ height:30%; background:linear-gradient(180deg,var(--ct-pink-500),var(--ct-pink)); }
.cm-v-compare i:nth-child(5){ height:20%; background:rgba(255,255,255,.12); }
.cm-v-route{ width:100%; height:100%; }
.cm-v-route path{ fill:none; stroke:url(#cmGrad); stroke-width:3; stroke-linecap:round; }
.cm-v-route circle{ fill:var(--ct-blue); }
.cm-card-body{ padding:1rem 1.15rem 1.15rem; }
.cm-card-eyebrow{
  font-family:var(--ct-font-display); font-size:9.5px; font-weight:600;
  letter-spacing:.18em; color:var(--ct-ink-4); text-transform:uppercase;
}
.cm-card-title{
  display:flex; align-items:center; gap:.45rem;
  font-family:var(--ct-font-display); font-size:19px; font-weight:700;
  letter-spacing:-.02em; color:var(--ct-ink); margin:.28rem 0 .4rem;
}
.cm-card-title span{ color:var(--ct-blue); transition:transform var(--ct-fast) var(--ct-ease); }
.cm-bento-card:hover .cm-card-title span{ transform:translateX(5px); }
.cm-card-desc{ font-size:12.8px; line-height:1.68; color:var(--ct-ink-3); }
.cm-card-foot{
  margin-top:.85rem; padding-top:.6rem;
  font-family:var(--ct-font-mono); font-size:9.5px; letter-spacing:.14em;
  color:var(--ct-ink-4); border-top:1px solid var(--ct-ring);
}

/* ======================================================================
   10. 结论带 / CTA / 演示提示
   ====================================================================== */
.cm-conclusion{
  position:relative; overflow:hidden;
  background:linear-gradient(120deg, rgba(70,183,255,.14), rgba(255,111,165,.10)), var(--ct-glass-2);
  -webkit-backdrop-filter:var(--ct-blur);
  backdrop-filter:var(--ct-blur);
  border-radius:var(--ct-r-lg);
  box-shadow:var(--ct-shadow);
  padding:1.15rem 1.5rem; margin:2.2rem 0 .6rem;
  font-size:14.5px; line-height:1.78; color:var(--ct-ink-2);
  border-left:4px solid var(--ct-blue);
}
.cm-cta{
  position:relative; overflow:hidden; text-align:center;
  background:linear-gradient(140deg, rgba(70,183,255,.16), rgba(255,111,165,.13) 55%, rgba(139,243,230,.12));
  border-radius:var(--ct-r-xl);
  box-shadow:var(--ct-shadow-lg);
  padding:2.8rem 2rem;
  margin:2.8rem 0 1rem;
}
/* CTA 复用同一张色斑底图，和 hero 视觉同源 */
.cm-cta::before{
  content:''; position:absolute; inset:0; pointer-events:none;
  background-image:url('/app/static/hero_carbon_mesh.jpg');
  background-size:cover; background-position:center 40%;
  opacity:.34; mix-blend-mode:screen;
}
.cm-cta > *{ position:relative; z-index:1; }
.cm-cta .k{
  font-family:var(--ct-font-display); font-size:10px; font-weight:700; letter-spacing:.24em;
  color:var(--ct-blue); text-transform:uppercase; margin-bottom:.7rem;
}
.cm-cta h2{
  font-family:var(--ct-font-display); font-size:clamp(1.5rem,3vw,2.35rem);
  font-weight:700; letter-spacing:-.03em; color:var(--ct-ink); margin:0 0 .7rem;
}
.cm-cta p{ font-size:14.5px; line-height:1.75; color:var(--ct-ink-2); max-width:660px; margin:0 auto 1.4rem; }
.cm-cta .cm-hero-actions{ justify-content:center; }
.cm-demo{
  border-radius:var(--ct-r); padding:.8rem 1.1rem; margin:.4rem 0 1.4rem;
  background:rgba(245,176,76,.12);
  box-shadow:0 0 0 1px rgba(245,176,76,.32);
  font-size:13px; line-height:1.7; color:var(--ct-amber-500);
}
.cm-demo b{ color:#FFD89B; }

/* ======================================================================
   11. Streamlit 控件同款语言
   ====================================================================== */
[data-testid="stMetric"]{
  background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur);
  backdrop-filter:var(--ct-blur);
  border-radius:var(--ct-r-lg) !important;
  box-shadow:var(--ct-shadow-sm) !important;
  padding:.95rem 1.1rem !important;
  border:none !important;
}
[data-testid="stMetricLabel"] p{ font-family:var(--ct-font-display) !important; font-size:10.5px !important;
  letter-spacing:.13em !important; text-transform:uppercase; color:var(--ct-ink-4) !important; }
[data-testid="stMetricValue"]{
  font-family:var(--ct-font-mono) !important; font-weight:700 !important;
  font-size:25px !important; letter-spacing:-.03em !important; color:var(--ct-ink) !important;
}
[data-testid="stMetricDelta"]{ font-size:11.5px !important; }

div[data-testid="stDataFrame"]{ border-radius:var(--ct-r-lg); overflow:hidden;
  box-shadow:var(--ct-shadow-sm); background:var(--ct-surface); }
div[data-testid="stDataFrame"] > div{ border-radius:var(--ct-r-lg) !important; }
[data-testid="stDataFrame"] *{ font-family:var(--ct-font-body) !important; }
[data-testid="stDataFrame"] [role="columnheader"]{
  background:var(--ct-canvas) !important; color:var(--ct-ink-3) !important;
  font-size:11px !important; font-weight:700 !important; letter-spacing:.06em !important;
  text-transform:uppercase;
}
[data-testid="stDataFrame"] [role="gridcell"]{
  font-family:var(--ct-font-mono) !important; font-size:12px !important;
  font-variant-numeric:tabular-nums;
}
[data-testid="stExpander"]{
  background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur);
  backdrop-filter:var(--ct-blur);
  border-radius:var(--ct-r-lg) !important;
  box-shadow:var(--ct-shadow-sm) !important;
  overflow:hidden;
}
[data-testid="stExpander"] summary{ font-family:var(--ct-font-display) !important; font-weight:600 !important;
  color:var(--ct-ink) !important; }
[data-testid="stTabs"] [role="tablist"]{
  background:var(--ct-glass); border-radius:var(--ct-r-pill);
  box-shadow:0 0 0 1px var(--ct-ring); padding:.25rem; gap:.15rem;
}
[data-testid="stTabs"] [role="tab"]{
  border-radius:var(--ct-r-pill) !important; font-family:var(--ct-font-body) !important;
  font-weight:600 !important; font-size:13px !important; color:var(--ct-ink-3) !important;
}
[data-testid="stTabs"] [aria-selected="true"]{
  background:var(--ct-glass-2) !important; color:var(--ct-blue) !important;
  box-shadow:var(--ct-shadow-xs) !important;
}
[data-testid="stPopover"] > button{
  border-radius:var(--ct-r-pill) !important; border:none !important;
  background:var(--ct-surface) !important; color:var(--ct-ink) !important;
  font-family:var(--ct-font-body) !important; font-weight:700 !important; font-size:13px !important;
  box-shadow:0 0 0 1px var(--ct-ring), 0 4px 14px rgba(10,10,10,.05) !important;
  padding:.5rem 1rem !important;
}
div[data-baseweb="popover"] > div{
  background:var(--ct-glass-3) !important;
  -webkit-backdrop-filter:var(--ct-blur); backdrop-filter:var(--ct-blur);
  border-radius:var(--ct-r-lg) !important; box-shadow:var(--ct-shadow-lg) !important;
}
input, textarea, [data-baseweb="select"] > div{
  border-radius:var(--ct-r-sm) !important;
  border:none !important; box-shadow:0 0 0 1px var(--ct-ring) !important;
  background:var(--ct-canvas-2) !important;
  font-family:var(--ct-font-body) !important; color:var(--ct-ink) !important;
}
input:focus, textarea:focus, [data-baseweb="select"] > div:focus-within{
  box-shadow:0 0 0 2px rgba(70,183,255,.55) !important;
}
[data-testid="stRadio"] label{ font-family:var(--ct-font-body); font-weight:600; font-size:13px; color:var(--ct-ink-2); }
[data-testid="stSlider"] [role="slider"]{ background:var(--ct-blue) !important; }
[data-testid="stProgress"] > div > div > div{ background:var(--ct-blue) !important; border-radius:100px !important; }
[data-testid="stProgress"] > div{ background:rgba(255,255,255,.09) !important; border-radius:100px !important; }
[data-testid="stAlert"]{
  border-radius:var(--ct-r) !important; border:none !important;
  box-shadow:var(--ct-shadow-xs) !important; font-size:13.5px !important;
}
pre, [data-testid="stCode"]{
  background:#0F172A !important; color:#E2E8F0 !important;
  border-radius:var(--ct-r) !important; font-size:12.5px !important;
}
[data-testid="stCaptionContainer"] p{ font-size:12px !important; color:var(--ct-ink-3) !important; }
[data-testid="stFullScreenFrame"] > div{ border-radius:var(--ct-r-lg) !important; }
.stPlotlyChart{ border-radius:var(--ct-r-lg); overflow:hidden; }
/* Plotly 文本的数字对齐：Plotly 的 font 没有 font-variant-numeric 选项，只能用 CSS 补 */
.js-plotly-plot text{
  font-variant-numeric:tabular-nums;
  font-feature-settings:'tnum' 1;
}
.js-plotly-plot .gtitle{ font-family:var(--ct-font-display) !important; }

/* ======================================================================
   11.5 组件类：cards.py / flags.py / calcpanel.py 依赖的类
   —— 全部改成「无边框 + 玻璃 + 发丝环」语言
   ====================================================================== */

/* ---------- 语义色板（coolors 式色块讲解色彩语言） ---------- */
.cm-swatch-row{
  display:grid; grid-template-columns:repeat(6,1fr); gap:14px; margin:.3rem 0 1rem;
}
.cm-swatch{ display:flex; flex-direction:column; gap:.6rem; }
.cm-swatch-chip{
  height:76px; border-radius:var(--ct-r-lg);
  box-shadow:0 8px 26px rgba(0,0,0,.45), 0 0 0 1px rgba(255,255,255,.08);
  transition:transform var(--ct-mid) var(--ct-ease);
}
.cm-swatch:hover .cm-swatch-chip{ transform:translateY(-6px) scale(1.03); }
.cm-swatch-body{ min-width:0; }
.cm-swatch-name{
  font-family:var(--ct-font-display); font-size:12.5px; font-weight:700; color:var(--ct-ink);
}
.cm-swatch-desc{ font-size:11px; line-height:1.55; color:var(--ct-ink-3); margin-top:.15rem; }
@media (max-width:1100px){ .cm-swatch-row{ grid-template-columns:repeat(3,1fr); } }
@media (max-width:640px){ .cm-swatch-row{ grid-template-columns:repeat(2,1fr); } }

/* ---------- 页头（layout.page_header） ---------- */
.cm-pagehead{ padding:.2rem 0 .1rem; }
.cm-pagehead-t{
  font-family:var(--ct-font-display); font-size:clamp(1.6rem,2.6vw,2.15rem);
  font-weight:700; letter-spacing:-.03em; color:var(--ct-ink); line-height:1.12;
}
.cm-pagehead-q{ margin-top:.35rem; font-size:13.5px; line-height:1.65; color:var(--ct-ink-3); max-width:820px; }
.cm-pagehead-badge{
  text-align:right; font-family:var(--ct-font-mono); font-size:10.5px; letter-spacing:.08em;
  color:var(--ct-ink-3); background:rgba(255,255,255,.07);
  padding:.32rem .7rem; border-radius:var(--ct-r-pill);
  box-shadow:0 0 0 1px var(--ct-ring); display:inline-block;
}

/* ---------- 面板 ---------- */
.cm-panel{
  background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur);
  backdrop-filter:var(--ct-blur);
  border-radius:var(--ct-r-lg);
  box-shadow:var(--ct-shadow-sm);
  padding:1.1rem 1.25rem 1rem; margin:.3rem 0 1rem;
}
.cm-panel.flat{ background:var(--ct-surface); box-shadow:var(--ct-shadow-xs); }
.cm-panel-h{ display:flex; align-items:baseline; gap:.6rem; flex-wrap:wrap; margin-bottom:.65rem; }
.cm-panel-h .t{ font-family:var(--ct-font-display); font-size:15px; font-weight:700; color:var(--ct-ink); }
.cm-panel-h .d{ color:var(--ct-ink-3); font-size:12px; }
.cm-panel-h .r{ margin-left:auto; color:var(--ct-ink-4); font-family:var(--ct-font-mono); font-size:10.5px; }
.cm-note{ color:var(--ct-ink-3); font-size:12.5px; line-height:1.75; }
.cm-note b{ color:var(--ct-ink); }
.cm-note .em{ color:var(--ct-amber); font-weight:700; }

/* ---------- KV / 公式 / 步骤 / 追溯 ---------- */
.cm-kv{ width:100%; border-collapse:collapse; font-size:13px; }
.cm-kv td{ padding:.55rem .6rem; border-bottom:1px solid var(--ct-ring); vertical-align:top; }
.cm-kv td.k{ color:var(--ct-ink-3); white-space:nowrap; width:122px; }
.cm-kv td.v{ color:var(--ct-ink); font-variant-numeric:tabular-nums; font-family:var(--ct-font-mono); font-size:12.5px; }
.cm-kv tr:last-child td{ border-bottom:none; }
.cm-kv td.v .big{ font-size:17px; font-weight:700; }
.cm-formula{
  font-family:var(--ct-font-mono); font-size:12px; color:var(--ct-ink-2);
  background:rgba(70,183,255,.08);
  box-shadow:0 0 0 1px rgba(70,183,255,.24);
  border-radius:var(--ct-r); padding:.7rem .9rem; margin:.5rem 0; line-height:1.85;
}
.cm-formula .hl{ color:var(--ct-blue); font-weight:700; }
.cm-formula .hl2{ color:var(--ct-amber); font-weight:700; }
.cm-formula .dim{ color:var(--ct-ink-4); }
.cm-steps{ display:flex; align-items:stretch; gap:0; flex-wrap:wrap; margin:.3rem 0 .9rem; }
.cm-step{
  flex:1; min-width:120px; text-align:center; padding:.85rem .55rem .8rem;
  border-radius:var(--ct-r); background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur-sm); backdrop-filter:var(--ct-blur-sm);
  box-shadow:0 0 0 1px var(--ct-ring);
  font-size:12.5px; color:var(--ct-ink-3); position:relative;
  transition:transform var(--ct-fast) var(--ct-ease), box-shadow var(--ct-fast) var(--ct-ease);
}
.cm-step:hover{ transform:translateY(-3px); box-shadow:var(--ct-shadow-sm); }
.cm-step .n{ display:block; font-family:var(--ct-font-mono); font-size:9.5px; color:var(--ct-ink-4); letter-spacing:.1em; }
.cm-step .t{ display:block; font-weight:700; margin-top:.25rem; color:var(--ct-ink); font-family:var(--ct-font-display); }
.cm-step .d{ color:var(--ct-ink-3); font-size:11px; }
.cm-step.on{ background:var(--ct-blue-tint); box-shadow:0 0 0 1px rgba(70,183,255,.34); }
.cm-step.on .n{ color:var(--ct-blue); }
.cm-step.on .t{ color:var(--ct-blue-700); }
.cm-step-arrow{ align-self:center; color:var(--ct-ink-4); padding:0 .45rem; font-size:13px; }
.cm-trace{ margin:.3rem 0; }
.cm-trace .node{ display:flex; gap:.7rem; align-items:flex-start; }
.cm-trace .dot{
  width:9px; height:9px; border-radius:50%; margin-top:.42rem; flex:none;
  background:var(--ct-blue); box-shadow:0 0 0 4px rgba(70,183,255,.18);
}
.cm-trace .dot.amber{ background:var(--ct-amber); box-shadow:0 0 0 4px rgba(245,176,76,.18); }
.cm-trace .dot.muted{ background:var(--ct-ink-4); box-shadow:0 0 0 4px rgba(255,255,255,.08); }
.cm-trace .body{ font-size:13px; line-height:1.65; }
.cm-trace .body .t{ font-weight:700; color:var(--ct-ink); }
.cm-trace .body .d{ color:var(--ct-ink-3); font-size:12px; }
.cm-trace .link{ width:1px; height:14px; background:var(--ct-ring-2); margin:.35rem 0 .35rem .28rem; }

/* ---------- HTML 表格 ---------- */
.cm-htable{
  width:100%; border-collapse:separate; border-spacing:0; overflow:hidden;
  border-radius:var(--ct-r-lg); background:var(--ct-surface);
  box-shadow:var(--ct-shadow-xs); font-size:12.5px; font-variant-numeric:tabular-nums;
}
.cm-htable th{
  text-align:left; color:var(--ct-ink-3); font-family:var(--ct-font-display); font-size:10px;
  letter-spacing:.1em; text-transform:uppercase; font-weight:600; padding:.65rem .75rem;
  border-bottom:1px solid var(--ct-ring); white-space:nowrap; background:var(--ct-canvas);
}
.cm-htable td{ padding:.62rem .75rem; border-bottom:1px solid var(--ct-ring); vertical-align:top; color:var(--ct-ink-2); }
.cm-htable tr:last-child td{ border-bottom:none; }
.cm-htable tr:hover td{ background:rgba(70,183,255,.07); }
.cm-htable tr.base td{ background:rgba(70,183,255,.09); }
.cm-htable tr.main td{ background:rgba(245,176,76,.08); }
.cm-htable tr.skip td{ background:rgba(255,255,255,.03); }
.cm-htable tr.fail td{ background:rgba(255,107,107,.08); }
.cm-htable td.num, .cm-htable th.num{ text-align:right; font-family:var(--ct-font-mono); }
.cm-htable .st-ok{ color:var(--ct-mint); font-weight:700; }
.cm-htable .st-bad{ color:var(--ct-red); font-weight:700; }
.cm-htable .st-skip{ color:var(--ct-ink-4); }
.cm-htable .st-warn{ color:var(--ct-amber); font-weight:700; }

/* ---------- 数据台账卡 ---------- */
.cm-table{
  border-radius:var(--ct-r-lg); overflow:hidden;
  background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur); backdrop-filter:var(--ct-blur);
  box-shadow:var(--ct-shadow-sm); margin:.4rem 0 1.1rem;
}
.cm-table-top{
  display:flex; align-items:baseline; justify-content:space-between; gap:.8rem;
  padding:.85rem 1.1rem .7rem; border-bottom:1px solid var(--ct-ring);
}
.cm-table-top .t{ font-family:var(--ct-font-display); font-size:14.5px; font-weight:700; color:var(--ct-ink); }
.cm-table-top .d{ color:var(--ct-ink-3); font-size:11.5px; margin-left:.55rem; }
.cm-table-top .n{ color:var(--ct-ink-4); font-family:var(--ct-font-mono); font-size:10px; white-space:nowrap; }
.cm-table-scroll{ overflow:auto; max-height:540px; }
.cm-table-grid{ width:100%; border-collapse:separate; border-spacing:0; font-size:12.5px; font-variant-numeric:tabular-nums; }
.cm-table-grid thead th{
  position:sticky; top:0; z-index:2; background:var(--ct-canvas);
  color:var(--ct-ink-3); font-family:var(--ct-font-display); font-size:10px; letter-spacing:.1em;
  text-transform:uppercase; font-weight:600; text-align:left; padding:.7rem .85rem;
  border-bottom:1px solid var(--ct-ring-2); white-space:nowrap;
}
.cm-table-grid thead th.num{ text-align:right; }
.cm-table-grid tbody td{ padding:.68rem .85rem; border-bottom:1px solid var(--ct-ring); color:var(--ct-ink-2); vertical-align:top; }
.cm-table-grid tbody td.num{ text-align:right; font-family:var(--ct-font-mono); }
.cm-table-grid tbody tr:last-child td{ border-bottom:none; }
.cm-table-grid tbody tr:hover td{ background:rgba(70,183,255,.07); }
.cm-table-grid tbody tr.main td{ background:rgba(245,176,76,.08); }
.cm-table-grid tbody tr.main td:first-child{ box-shadow:inset 3px 0 0 var(--ct-amber); }
.cm-table-grid tbody tr.fail td{ background:rgba(255,107,107,.08); }
.cm-table-grid tbody tr.fail td:first-child{ box-shadow:inset 3px 0 0 var(--ct-red); }
.cm-table-foot{ padding:.6rem 1.1rem; border-top:1px solid var(--ct-ring); color:var(--ct-ink-4); font-size:10.5px; }
.cm-table .cm-htable{ border:none; border-radius:0; box-shadow:none; background:transparent; }
.cm-table-plain{ margin-top:.4rem; }
.cm-pill{
  display:inline-flex; align-items:center; padding:.16rem .55rem; border-radius:var(--ct-r-pill);
  font-family:var(--ct-font-mono); font-size:9.5px; font-weight:700; letter-spacing:.02em;
}
.cm-pill.ok{ color:var(--ct-mint); background:rgba(139,243,230,.14); box-shadow:0 0 0 1px rgba(139,243,230,.30); }
.cm-pill.bad{ color:#FF9A9A; background:rgba(255,107,107,.12); box-shadow:0 0 0 1px rgba(255,107,107,.28); }
.cm-pill.skip{ color:var(--ct-ink-3); background:rgba(255,255,255,.06); box-shadow:0 0 0 1px var(--ct-ring); }
.cm-pill.warn{ color:var(--ct-amber-500); background:rgba(245,176,76,.14); box-shadow:0 0 0 1px rgba(245,176,76,.32); }
.cm-mut{ color:var(--ct-ink-4); }

/* ---------- 空状态 / 数据标记 / 估算 ---------- */
.cm-empty{
  border-radius:var(--ct-r); padding:1.2rem 1.1rem;
  color:var(--ct-ink-3); font-size:12.5px;
  box-shadow:0 0 0 1px var(--ct-ring-2);
  background:rgba(255,255,255,.04);
}
.cm-empty .t{ color:var(--ct-ink); font-weight:700; font-size:13.5px; margin-bottom:.25rem; }
.cm-empty code{ color:var(--ct-blue); background:var(--ct-blue-tint); padding:.1rem .35rem; border-radius:5px; font-size:11.5px; }
.cm-flagbar{ display:flex; gap:.5rem; flex-wrap:wrap; margin:.3rem 0 .8rem; }
.cm-flag{
  display:inline-flex; align-items:center; gap:.45rem; padding:.32rem .7rem;
  border-radius:var(--ct-r-pill); font-size:11.5px; color:var(--ct-ink-2);
  background:rgba(255,255,255,.06); box-shadow:0 0 0 1px var(--ct-ring);
}
.cm-flag .dot{ width:9px; height:9px; border-radius:3px; display:inline-block; }
.cm-flag .est{ border:2px solid var(--ct-amber); background:transparent; width:9px; height:9px; }
.cm-est-wrap{
  border-radius:var(--ct-r-lg); padding:.9rem 1rem .5rem; margin:.4rem 0 .9rem;
  background:rgba(245,176,76,.09);
  box-shadow:0 0 0 1px rgba(245,176,76,.30);
}
.cm-est-head{ font-size:12.5px; color:var(--ct-amber-500); font-weight:700; }
.cm-est-head span{ color:var(--ct-ink-3); font-weight:400; margin-left:.5rem; }
.cm-est-table{ width:100%; border-collapse:collapse; margin-top:.6rem; font-size:12px; }
.cm-est-table th{ text-align:left; color:var(--ct-ink-3); font-weight:600; padding:.45rem .55rem; border-bottom:1px solid var(--ct-ring); }
.cm-est-table td{ padding:.45rem .55rem; border-bottom:1px solid var(--ct-ring); color:var(--ct-ink-2); vertical-align:top; }
.cm-est-table tr.est td{ box-shadow:inset 0 0 0 2px rgba(255,196,107,.55); background:rgba(245,176,76,.10); }
.cm-est-table td.reason{ color:var(--ct-amber-500); }
.cm-est-foot{ color:var(--ct-ink-4); font-size:11px; margin:.5rem 0 .3rem; }
.cm-gate-ok{ color:var(--ct-mint); font-weight:700; }
.cm-gate-fail{ color:var(--ct-red); font-weight:700; }
.cm-gate-skip{ color:var(--ct-ink-3); font-weight:700; }

/* ---------- 计算器：节省条 / NPV 瀑布 / 折线 ---------- */
.cm-savings{
  border-radius:var(--ct-r-lg); background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur); backdrop-filter:var(--ct-blur);
  box-shadow:var(--ct-shadow-sm); padding:1rem 1.1rem .9rem; margin:.5rem 0 1rem;
}
.cm-savings-h{
  display:flex; align-items:baseline; justify-content:space-between; gap:.7rem; margin-bottom:.7rem;
  font-family:var(--ct-font-display); font-size:10px; font-weight:600; letter-spacing:.13em;
  color:var(--ct-ink-4); text-transform:uppercase;
}
.cm-savings-row{ display:grid; grid-template-columns:92px 1fr 78px; align-items:center; gap:.8rem; margin:.45rem 0; }
.cm-savings-row .lbl{ color:var(--ct-ink-3); font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.cm-savings-row .track{ height:14px; border-radius:100px; background:rgba(255,255,255,.07); overflow:hidden; }
.cm-savings-row .track i{ display:block; height:100%; border-radius:100px; }
.cm-savings-row .track i.ice{ background:linear-gradient(90deg,rgba(255,138,91,.28),var(--ct-coral)); }
.cm-savings-row .track i.bev{ background:linear-gradient(90deg,rgba(70,183,255,.24),var(--ct-blue)); }
.cm-savings-row .val{ text-align:right; font-family:var(--ct-font-mono); font-size:16px; font-weight:700; color:var(--ct-ink); }
.cm-savings-delta{ margin-top:.7rem; padding-top:.65rem; border-top:1px solid var(--ct-ring); font-size:12px; color:var(--ct-ink-3); }
.cm-savings-delta b{ color:var(--ct-amber); font-size:14px; }
.cm-savings-delta span{ color:var(--ct-ink-4); font-size:10.5px; margin-left:.5rem; }

.cm-npv{
  border-radius:var(--ct-r-lg); background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur); backdrop-filter:var(--ct-blur);
  box-shadow:var(--ct-shadow-sm); padding:1.1rem 1.25rem .9rem; margin:.4rem 0 .9rem;
}
.cm-npv-h{ display:flex; align-items:baseline; justify-content:space-between; gap:.7rem; margin-bottom:.6rem; }
.cm-npv-h span:first-child{ font-family:var(--ct-font-display); font-size:16.5px; font-weight:700; color:var(--ct-ink); }
.cm-npv-h span:last-child{ font-family:var(--ct-font-mono); font-size:10px; color:var(--ct-ink-4); }
.cm-npv-legend{ display:flex; flex-wrap:wrap; gap:.9rem; margin:.3rem 0 .9rem; font-size:11px; color:var(--ct-ink-3); }
.cm-npv-legend span{ display:inline-flex; align-items:center; gap:.4rem; }
.cm-npv-legend i{ width:9px; height:9px; border-radius:3px; display:inline-block; }
.cm-npv-row{ display:grid; grid-template-columns:68px 1fr 78px; align-items:center; gap:.8rem; margin:.55rem 0; }
.cm-npv-name{ color:var(--ct-ink-3); font-size:12px; white-space:nowrap; }
.cm-npv-track{ height:28px; border-radius:100px; background:rgba(255,255,255,.07); overflow:hidden; display:flex; }
.cm-npv-seg{
  display:flex; align-items:center; justify-content:center; min-width:0; height:100%;
  color:#fff; font-family:var(--ct-font-mono); font-size:9px; font-weight:700; overflow:hidden;
  white-space:nowrap; border-right:1px solid rgba(255,255,255,.35);
}
.cm-npv-seg:last-child{ border-right:none; }
.cm-npv-total{ text-align:right; font-family:var(--ct-font-mono); font-size:18px; font-weight:700; color:var(--ct-ink); }
.cm-npv-foot{ margin-top:.7rem; padding-top:.6rem; border-top:1px solid var(--ct-ring); color:var(--ct-ink-4); font-size:10.5px; line-height:1.6; }

.cm-line{
  border-radius:var(--ct-r-lg); background:var(--ct-glass);
  -webkit-backdrop-filter:var(--ct-blur); backdrop-filter:var(--ct-blur);
  box-shadow:var(--ct-shadow-sm); padding:1.1rem 1.25rem .9rem; margin:.4rem 0 .9rem;
}
.cm-line-h{ display:flex; align-items:baseline; justify-content:space-between; gap:.7rem; margin-bottom:.4rem; }
.cm-line-h span:first-child{ font-family:var(--ct-font-display); font-size:16.5px; font-weight:700; color:var(--ct-ink); }
.cm-line-h span:last-child{ font-family:var(--ct-font-mono); font-size:10px; color:var(--ct-ink-4); }
.cm-line-legend{ display:flex; flex-wrap:wrap; gap:.9rem; margin:.2rem 0 .2rem; font-size:11px; color:var(--ct-ink-3); }
.cm-line-legend span{ display:inline-flex; align-items:center; gap:.4rem; }
.cm-line-legend i{ width:9px; height:9px; border-radius:3px; display:inline-block; }
.cm-line-svg{ width:100%; height:auto; display:block; overflow:visible; margin:.3rem 0 0; }
.cm-line-grid{ stroke:rgba(255,255,255,.09); stroke-width:1; }
.cm-line-ytxt, .cm-line-xtxt{ fill:var(--ct-ink-4); font-family:var(--ct-font-mono); font-size:9px; }
.cm-line-path{ fill:none; stroke-width:2.6; stroke-linecap:round; stroke-linejoin:round; }
.cm-line-area{ opacity:.12; }
.cm-line-dot{ stroke:#0B0D10; stroke-width:1.4; }
.cm-line-last{ font-family:var(--ct-font-mono); font-size:9.5px; font-weight:700; }
.cm-line-foot{ margin-top:.4rem; padding-top:.6rem; border-top:1px solid var(--ct-ring); color:var(--ct-ink-4); font-size:10.5px; }

/* ---------- 侧栏遗留（保留兼容，样式已随浅色化） ---------- */
.cm-side-brand{ display:flex; align-items:center; gap:.6rem; padding:.2rem 0 .9rem; border-bottom:1px solid var(--ct-ring); margin-bottom:.9rem; }
.cm-side-brand .mark{
  width:33px; height:33px; border-radius:11px; display:flex; align-items:center; justify-content:center;
  background:linear-gradient(140deg,var(--ct-blue-500),var(--ct-blue-deep)); color:#06121F;
  font-family:var(--ct-font-display); font-weight:700; font-size:13px;
}
.cm-side-brand .n{ font-family:var(--ct-font-display); font-size:14px; font-weight:700; color:var(--ct-ink); }
.cm-side-brand .s{ font-family:var(--ct-font-mono); font-size:8px; letter-spacing:.14em; color:var(--ct-ink-4); margin-top:2px; }
.cm-side-label{ color:var(--ct-ink-4); font-family:var(--ct-font-display); font-size:9.5px; font-weight:600; letter-spacing:.16em; text-transform:uppercase; margin:.2rem 0 .5rem; }
.cm-side-card{
  border-radius:var(--ct-r); padding:.7rem .8rem; background:var(--ct-glass);
  box-shadow:0 0 0 1px var(--ct-ring); margin-bottom:.8rem;
}
.cm-side-card .k{ color:var(--ct-ink-4); font-family:var(--ct-font-mono); font-size:9px; letter-spacing:.12em; }
.cm-side-card .v{ color:var(--ct-ink); font-family:var(--ct-font-mono); font-size:12px; margin-top:.2rem; }
.cm-side-card .v.ok{ color:var(--ct-mint); }

@media (max-width:760px){
  .cm-savings-row{ grid-template-columns:62px 1fr 58px; gap:.5rem; }
  .cm-npv-row{ grid-template-columns:52px 1fr 58px; gap:.5rem; }
  .cm-npv-seg{ font-size:0; }
  .cm-npv, .cm-line, .cm-savings{ padding:.9rem .9rem .75rem; }
}


@keyframes cm-rise{
  from{ opacity:0; transform:translateY(22px); }
  to{ opacity:1; transform:translateY(0); }
}
@keyframes cm-pulse{
  0%,100%{ box-shadow:0 0 0 3px rgba(16,185,129,.18); }
  50%{ box-shadow:0 0 0 6px rgba(16,185,129,.05); }
}
@keyframes cm-grow{
  from{ opacity:0; transform:scaleY(.05); }
  to{ opacity:1; transform:scaleY(1); }
}
@keyframes cm-slide{
  from{ transform:translateX(0); }
  to{ transform:translateX(-50%); }
}
.cm-stagger > *{ animation:cm-rise var(--ct-slow) var(--ct-ease) backwards; }
.cm-stagger > *:nth-child(1){ animation-delay:.02s; }
.cm-stagger > *:nth-child(2){ animation-delay:.07s; }
.cm-stagger > *:nth-child(3){ animation-delay:.12s; }
.cm-stagger > *:nth-child(4){ animation-delay:.17s; }
.cm-stagger > *:nth-child(5){ animation-delay:.22s; }
.cm-stagger > *:nth-child(6){ animation-delay:.27s; }
.cm-stagger > *:nth-child(7){ animation-delay:.32s; }
.cm-stagger > *:nth-child(8){ animation-delay:.37s; }
.cm-stagger > *:nth-child(9){ animation-delay:.42s; }

@media (prefers-reduced-motion:reduce){
  *, *::before, *::after{ animation-duration:.001ms !important; transition-duration:.001ms !important; }
}

/* ======================================================================
   13. 响应式
   ====================================================================== */
@media (max-width:1200px){
  .cm-bento{ grid-template-columns:repeat(2,1fr); }
  .cm-bento-card.span4{ grid-column:span 2; }
  /* 导航变紧：10 个链接在 1200px 以下要能一屏放下 */
  .cm-nav-link{ font-size:13.5px !important; padding:.55rem .6rem .45rem !important; }
  .cm-brand .word{ font-size:14.5px; }
}
@media (max-width:980px){
  /* 状态只留绿点与 GATE，REAL 让位给导航 */
  .cm-head-pill.live{ font-size:0; gap:0; }
  .cm-head-pill.live .cm-dot{ font-size:9.5px; }
}
@media (max-width:760px){
  .cm-hero{ padding:1.7rem 1.3rem 1.5rem; border-radius:var(--ct-r-lg); }
  .cm-display{ font-size:2rem; }
  .cm-sub{ font-size:14.5px; }
  .cm-palette{ height:82px; gap:6px; }
  .cm-bento{ grid-template-columns:1fr; }
  .cm-bento-card.span2,.cm-bento-card.span4{ grid-column:span 1; }
  .cm-hero-index{ font-size:36px; top:.8rem; right:1rem; }
  .cm-cta{ padding:2rem 1.1rem; }
}

/* ======================================================================
   14. v2 · ScrollTide 暗色电影感增补
   ----------------------------------------------------------------------
   本段是 docs/47 记录的逆向成果落地：
     · tide-glow 四团光晕缓慢漂移（.stApp::before）
     · 页面切换的「电影入场」：opacity + 位移 + 弹簧曲线
     · 玻璃控件在暗底的二次校准（原生控件自带浅色描边要压掉）
   ====================================================================== */

/* ---- 页面入场：scrolltide 的 opacity + 位移 + 弹簧曲线 ----
   注意起始透明度用 .45 而不是 0：浏览器会暂停后台标签页的 CSS 动画，
   若起始态是 opacity:0，动画被暂停时内容会永久停在透明态（整页空白）。
   .45 既有「淡入」的观感，又保证任何情况下内容都可读。 */
.block-container > *{
  animation:cm-page-in .5s var(--ct-ease-spring) both;
}
.block-container > *:nth-child(1){ animation-delay:.02s; }
.block-container > *:nth-child(2){ animation-delay:.06s; }
.block-container > *:nth-child(3){ animation-delay:.10s; }
.block-container > *:nth-child(4){ animation-delay:.14s; }
.block-container > *:nth-child(5){ animation-delay:.18s; }
.block-container > *:nth-child(6){ animation-delay:.22s; }
.block-container > *:nth-child(7){ animation-delay:.26s; }
.block-container > *:nth-child(8){ animation-delay:.30s; }
@keyframes cm-page-in{
  from{ opacity:.45; transform:translateY(14px); }
  to{ opacity:1; transform:translateY(0); }
}
@keyframes cm-tide{
  0%{ transform:translate3d(-2%,1%,0) scale(1.03); }
  50%{ transform:translate3d(2%,-2%,0) scale(1.09); }
  100%{ transform:translate3d(1%,2%,0) scale(1.05); }
}

/* ---- 原生控件在暗底的校准 ---- */
[data-testid="stMetric"]{ background:var(--ct-glass) !important; }
[data-testid="stDataFrame"]{
  background:var(--ct-canvas-2) !important;
}
[data-testid="stDataFrame"] [role="columnheader"]{
  background:var(--ct-surface) !important;
}
[data-testid="stExpander"] summary{ color:var(--ct-ink) !important; }
[data-testid="stExpander"] svg{ fill:var(--ct-ink-3) !important; }
[data-testid="stExpander"] [data-testid="stExpanderIcon"] svg path{ fill:var(--ct-ink-3) !important; }
[data-testid="stSlider"] [role="slider"]{
  background:var(--ct-blue) !important;
  box-shadow:0 0 0 5px rgba(70,183,255,.18) !important;
}
[data-testid="stSlider"] [data-baseweb="slider"] div[role="track"]{
  background:rgba(255,255,255,.12) !important;
}
[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"]{
  border-color:var(--ct-blue) !important;
}
[data-testid="stRadio"] label p{ color:var(--ct-ink-2) !important; }
[data-testid="stCheckbox"] label p{ color:var(--ct-ink-2) !important; }
[data-testid="stFileUploader"]{
  background:var(--ct-glass) !important;
  border-radius:var(--ct-r-lg) !important;
  box-shadow:0 0 0 1px var(--ct-ring) !important;
}
[data-testid="stFileUploader"] small{ color:var(--ct-ink-4) !important; }
[data-testid="stFileUploader"] button{
  background:var(--ct-glass-2) !important; color:var(--ct-ink) !important;
  border-radius:var(--ct-r-pill) !important;
  box-shadow:0 0 0 1px var(--ct-ring) !important;
}
[data-testid="stFileUploader"] button:hover{
  background:rgba(255,255,255,.12) !important;
}
[data-testid="stJson"] pre{ background:var(--ct-canvas-2) !important; }
[data-testid="stDownloadButton"] button{ background:var(--ct-glass-2) !important; }

/* 提示条（success / error / warning）在暗底下的配色 */
[data-testid="stAlert"]{
  background:var(--ct-glass) !important;
  border-radius:var(--ct-r) !important; border:none !important;
  box-shadow:0 0 0 1px var(--ct-ring), 0 6px 18px rgba(0,0,0,.32) !important;
}
[data-testid="stAlert"] [data-testid^="stAlertContent"]{ background:transparent !important; }
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] p{ color:var(--ct-ink-2) !important; }
[data-testid="stAlert"] svg{ fill:var(--ct-ink-3) !important; }

/* 滚动条与选中文本 */
::selection{ background:rgba(70,183,255,.28); color:var(--ct-ink); }

/* 链接：暗底上用 tide 蓝 */
a{ color:var(--ct-blue) !important; }
a:hover{ color:var(--ct-blue-500) !important; }

</style>
"""


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def hero_compact(title: str, subtitle: str, kicker: str = "CARBON LEDGER",
                 badges: Optional[Iterable[str]] = None) -> None:
    """紧凑页头：给地图/实验台留出首屏高度，视觉与 hero 同源。"""
    safe_title = _esc(title)
    chips = "".join("<span class='cm-chip'>" + _esc(b) + "</span>" for b in (badges or []))
    first = str(kicker).split("/")[0].strip()
    idx = first if first.isdigit() else "00"
    st.markdown(
        "<section class='cm-hero compact'>"
        "<div class='cm-hero-inner'>"
        "<div class='cm-kicker'><span class='dot'></span>" + _esc(kicker) + "</div>"
        "<div class='cm-display'>" + safe_title + "</div>"
        "<div class='cm-sub'>" + _esc(subtitle) + "</div>"
        + chips
        + "</div>"
        "<div class='cm-hero-index'>" + _esc(idx) + "</div>"
        "</section>",
        unsafe_allow_html=True,
    )


def section(title: str, desc: str = "") -> None:
    st.markdown(
        "<div class='cm-sec'><span class='t'>" + _esc(title) + "</span>"
        + ("<span class='d'>" + _esc(desc) + "</span>" if desc else "")
        + "</div>",
        unsafe_allow_html=True,
    )
