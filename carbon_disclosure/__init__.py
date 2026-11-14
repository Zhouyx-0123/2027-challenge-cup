"""Read-only packaging helpers for the browser-based disclosure ledger.

No upstream emissions are calculated here. Only repository-owned UI assets are
inlined; CSV uploads are processed inside the browser, never interpolated here.
"""
from pathlib import Path
import re

DIST = Path(__file__).resolve().parent / "dist"
__version__ = "0.9.0"


def build_html(*, embedded: bool = False) -> str:
    """Build the same offline page used by Streamlit and the portable download."""
    html = (DIST / "index.html").read_text(encoding="utf-8")
    for name in ("styles.css", "platform.css"):
        css = (DIST / name).read_text(encoding="utf-8")
        html = html.replace(f'<link rel="stylesheet" href="{name}">',
                            "<style>" + css + "</style>")
    if embedded:
        html = html.replace("<body>", '<body data-presentation="embedded">', 1)

    def inline(match: re.Match) -> str:
        name = match.group(1)
        asset = (DIST / name).resolve()
        if asset.parent != DIST.resolve() or asset.suffix != ".js":
            raise ValueError("Only local JavaScript assets can be embedded")
        script = asset.read_text(encoding="utf-8")
        script = re.sub(r"</script", r"<\\/script", script, flags=re.I)
        return "<script>" + script + "</script>"

    return re.sub(r'<script src="([^"]+)"></script>', inline, html)
