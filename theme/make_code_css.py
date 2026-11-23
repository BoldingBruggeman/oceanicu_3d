#!/usr/bin/env python3
"""Write code.css: pygments ``friendly`` (light) and ``github-dark`` (dark mode).

    python3 make_code_css.py

Dark applies with the system setting unless the reader chose light, or when the reader chose dark
(mode.js sets data-theme on <html>).  The rules apply to Python-Markdown's ``.codehilite`` blocks (the bbdocs builders) and to Hugo's
``.highlight`` blocks (chroma with ``noClasses: false`` uses the same token classes).  The block
background is left to style.css (``--code``), so only the token colours change with the mode.
"""
import re
from pathlib import Path

from pygments.formatters import HtmlFormatter

SELECTORS = [".codehilite", ".highlight"]
SYSTEM_DARK = ':root:not([data-theme="light"]) '   # inside @media (prefers-color-scheme: dark)
CHOSEN_DARK = ':root[data-theme="dark"] '          # the reader chose dark (mode.js)
LIGHT, DARK = "friendly", "github-dark"


def rules(style: str, prefix: str = "") -> str:
    css = HtmlFormatter(style=style).get_style_defs([prefix + s for s in SELECTORS])
    keep = []
    for ln in css.splitlines():
        if re.match(r"^(pre|td\.linenos|span\.linenos)\b", ln):          # line numbers: unused
            continue
        if re.match(r"^[^{]*\.highlight\s*\{", ln):                       # the block: text colour only
            ln = re.sub(r"\s*background: #\w+;?", "", ln)
            if re.fullmatch(r"[^{]*\{\s*\}", ln):
                continue
        keep.append(ln)
    return "\n".join(keep)


def main():
    system = "\n".join("  " + ln for ln in rules(DARK, SYSTEM_DARK).splitlines())
    text = (f"/* Code highlighting: pygments {LIGHT}, {DARK} in dark mode (written by make_code_css.py) */\n"
            f"{rules(LIGHT)}\n\n@media (prefers-color-scheme: dark) {{\n{system}\n}}\n"
            f"{rules(DARK, CHOSEN_DARK)}\n")
    (Path(__file__).parent / "code.css").write_text(text)


if __name__ == "__main__":
    main()
