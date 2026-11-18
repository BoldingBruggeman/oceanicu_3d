"""Shared page frame of the B&B documentation sites (nesting-docs, config-docs, ocean-docs).

The theme is a submodule at ``theme/`` of each docs repository; a builder imports this module
from its output checkout::

    sys.path.insert(0, str(out / "theme")); import bbdocs

and every page links ``theme/style.css``, ``theme/code.css``, ``theme/mode.js`` (the light/dark
button) and ``theme/related.js`` (the related-sites list in the footer), so a change of look needs
only a submodule update.
"""
from __future__ import annotations

import html
import posixpath
import re
import shutil
from pathlib import Path

THEME = "theme"
#: kept in the output checkout when a builder clears it
KEEP = {".git", ".gitmodules", THEME, "README.md", ".nojekyll", "CNAME"}


def rel(here: str, target: str) -> str:
    """URL of *target* from page *here* (both relative to the site root)."""
    return posixpath.relpath(target, posixpath.dirname(here) or ".")


def root(here: str) -> str:
    """Prefix from page *here* to the site root ('' or '../')."""
    return "../" * here.count("/")


def head(here: str, title: str, description: str, extra: str = "") -> str:
    r = root(here)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><meta name="description" content="{html.escape(description)}">
<link rel="icon" href="{r}{THEME}/logo_stacked.png">
<link rel="stylesheet" href="{r}{THEME}/style.css"><link rel="stylesheet" href="{r}{THEME}/code.css">
<script src="{r}{THEME}/mode.js"></script>{extra}</head>"""


def topbar(here: str, brand: str, nav: str) -> str:
    """The top bar: the wide logo, the site name, the site's own navigation (html)."""
    r = root(here)
    return (f'<header class="topbar"><div class="topin"><a class="brand" href="{r}index.html">'
            f'<img src="{r}{THEME}/logo_wide.png" alt="Bolding &amp; Bruggeman"><span>{brand}</span></a>'
            f'<nav aria-label="Site">{nav}</nav></div></header>')


def band(title: str, lead: str = "", crumbs: str = "") -> str:
    """The grey title band (the Section slide)."""
    lead_html = f"<p>{html.escape(lead)}</p>" if lead else ""
    return f'<section class="band"><div class="bandin">{crumbs}<h1>{html.escape(title)}</h1>{lead_html}</div></section>'


def footer(here: str, site: str, note: str) -> str:
    """The footer: the stacked logo, B&B, the related sites (theme/related.js), *note* (html)."""
    r = root(here)
    return (f'<footer class="foot"><div class="footin"><img src="{r}{THEME}/logo_stacked.png" alt="Bolding &amp; Bruggeman">'
            f'<div><p>Bolding &amp; Bruggeman · <a href="https://www.bolding-bruggeman.com">www.bolding-bruggeman.com</a></p>'
            f'<p class="bb-related" data-self="{site}"></p><p class="note">{note}</p></div></div></footer>'
            f'<script src="{r}{THEME}/related.js" defer></script>')


def nav_links(here: str, items, current: str | None = None) -> str:
    """Navigation links from (target, label); the one equal to *current* (default *here*) is marked."""
    cur = current or here
    return "".join(f'<a href="{rel(here, t)}"{" aria-current=\"page\"" if t == cur else ""}>{lbl}</a>'
                   for t, lbl in items)


def doc_page(here: str, *, site: str, brand: str, nav: str, title: str, description: str, body: str,
             note: str, lead: str = "", crumbs: str = "", side: str = "") -> str:
    """A complete text page: band, optional side menu (html), the text, footer."""
    layout = (f'<div class="layout">{side}<article class="doc">{body}</article></div>' if side else
              f'<div class="layout single"><article class="doc">{body}</article></div>')
    return (f"{head(here, title, description or lead or title)}\n<body>\n{topbar(here, brand, nav)}\n"
            f"{band(title, lead, crumbs)}\n<main>{layout}</main>\n{footer(here, site, note)}\n</body></html>\n")


def side_menu(heading: str, items) -> str:
    """A side menu from (href, label, current?)."""
    lis = "".join(f'<li><a href="{h}"{" aria-current=\"page\"" if c else ""}>{html.escape(t)}</a></li>'
                  for h, t, c in items)
    return f'<aside class="side"><h2>{html.escape(heading)}</h2><ul>{lis}</ul></aside>'


# ── Markdown ────────────────────────────────────────────────────────────────────
def gh_slug(text: str) -> str:
    """GitHub-style heading anchor, so links like ``#--analyses-dir`` keep working."""
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def markdown_html(text: str):
    """Markdown to html (tables, fenced code with highlighting, GitHub anchors).
    Returns (html, [(anchor, text) of the level-2 headings])."""
    import markdown

    md = markdown.Markdown(extensions=["tables", "fenced_code", "codehilite", "toc", "sane_lists"],
                           extension_configs={"codehilite": {"guess_lang": False},
                                              "toc": {"slugify": lambda v, _sep: gh_slug(v)}})
    body = md.convert(text)
    heads: list[tuple[str, str]] = []

    def walk(tokens):
        for t in tokens:
            if t["level"] == 2:
                heads.append((t["id"], re.sub(r"<[^>]+>", "", html.unescape(t["name"]))))
            walk(t.get("children", []))
    walk(getattr(md, "toc_tokens", []))
    return body, heads


# ── checks and output ───────────────────────────────────────────────────────────
def check(pages: dict, forbidden=(), extra_targets=()) -> list[str]:
    """Problems: a *forbidden* pattern on a page, or a link to a page that is not built."""
    problems = []
    targets = set(pages) | set(extra_targets)
    for out, page in pages.items():
        for pat in forbidden:
            for m in re.finditer(pat, page, re.I):
                problems.append(f'{out}:{page.count(chr(10), 0, m.start()) + 1}: forbidden "{m.group(0)}"')
        for href in re.findall(r'href="([^"#:?]+\.html)', page):
            tgt = posixpath.normpath(posixpath.join(posixpath.dirname(out), href))
            if tgt not in targets:
                problems.append(f"{out}: broken link {href}")
    return problems


def require_theme(out: Path) -> None:
    if not (out / THEME / "style.css").is_file():
        raise SystemExit(f"{out}/{THEME} is missing: git -C {out} submodule update --init")


def clear(out: Path) -> None:
    """Empty the output checkout, keeping git, the theme submodule and the README."""
    for f in out.iterdir():
        if f.name not in KEEP:
            shutil.rmtree(f) if f.is_dir() else f.unlink()
    (out / ".nojekyll").write_text("")
