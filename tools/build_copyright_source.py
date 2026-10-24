#!/usr/bin/env python3
"""Generate soft-copyright source-code pages (50 lines/page, front/back 30)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "软著" / "源代码"
NAME = "碳慧智算物流碳感知智能决策系统"
VERSION = "V1.0"
LINES_PER_PAGE = 50
PAGES = 30

ORDER = [
    "app/app.py", "app/home_view.py", "app/services.py",
    "app/pages/0_概览.py", "app/pages/1_基准核算.py", "app/pages/2_高排放诊断.py",
    "app/pages/3_替代方案.py", "app/pages/4_成本效益.py", "app/pages/5_碳会计台账.py",
    "app/pages/6_路线实验室.py", "app/pages/7_电动化测算.py", "app/pages/8_车队TCO沙盘.py",
]


def collect():
    files = []
    for rel in ORDER:
        p = ROOT / rel
        if p.exists():
            files.append(p)
    for folder in ("app/components", "src", "tools"):
        for p in sorted((ROOT / folder).rglob("*.py")):
            if p.name == "build_copyright_source.py":
                continue
            if any(x in p.parts for x in ("__pycache__", "_scratch")):
                continue
            files.append(p)
    seen, out = set(), []
    for p in files:
        if p in seen:
            continue
        seen.add(p)
        out.append((p, p.read_text(encoding="utf-8", errors="replace").splitlines()))
    return out


def page_header(n):
    return f"{NAME} {VERSION}    第 {n:03d} 页"


def paginate(lines):
    pages = []
    for i in range(0, len(lines), LINES_PER_PAGE):
        chunk = lines[i:i + LINES_PER_PAGE]
        while len(chunk) < LINES_PER_PAGE:
            chunk.append("")
        pages.append(chunk)
    return pages


def main():
    files = collect()
    OUT.mkdir(parents=True, exist_ok=True)
    all_pages = []
    manifest = []
    for p, lines in files:
        manifest.append(f"{p.relative_to(ROOT)}\t{len(lines)}")
        all_pages.extend(paginate(lines))
    all_pages = [p for p in all_pages if any(line.strip() for line in p)]
    full = []
    for i, page in enumerate(all_pages, 1):
        full.append(page_header(i))
        full.extend(page)
    full_text = "\n".join(full) + "\n"
    (OUT / "源代码_完整.txt").write_text(full_text, encoding="utf-8")
    (OUT / "源代码_文件清单.txt").write_text(
        "软件名称：" + NAME + "\n版本：" + VERSION + "\n"
        + "文件数：%d\n总页数：%d\n\n" % (len(files), len(all_pages))
        + "\n".join(manifest) + "\n", encoding="utf-8")
    for label, pages in (("前30页", all_pages[:PAGES]), ("后30页", all_pages[-PAGES:])):
        rows = []
        for offset, page in enumerate(pages):
            n = offset + 1 if label == "前30页" else len(all_pages) - PAGES + offset + 1
            rows.append(page_header(n))
            rows.extend(page)
        (OUT / f"源代码_{label}.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print("files", len(files), "lines", sum(len(x[1]) for x in files), "pages", len(all_pages))
    print("out", OUT)


if __name__ == "__main__":
    main()
