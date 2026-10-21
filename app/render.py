"""Content model -> HTML (on-screen preview and the page printed to PDF)."""
from __future__ import annotations

from html import escape

from . import i18n

DOC_CSS = """
.doc{--ink:#1f2937;--muted:#6b7280;--line:#e5e7eb;--brand:#4053d6;--soft:#f5f6fb;color:var(--ink);font-size:13.5px;line-height:1.55}
.doc .cover{border-bottom:3px solid var(--brand);padding-bottom:14px;margin-bottom:18px}
.doc .cover .kicker{color:var(--brand);font-weight:700;font-size:12px;letter-spacing:.04em;text-transform:uppercase}
.doc .cover h1{font-size:26px;margin:4px 0 2px;line-height:1.25}
.doc .cover .sub{color:var(--muted);font-size:15px}
.doc table{border-collapse:collapse;width:100%;margin:8px 0 14px;font-size:12.5px}
.doc th,.doc td{border:1px solid var(--line);padding:6px 8px;text-align:start;vertical-align:top}
.doc th{background:var(--soft);font-weight:600}
.doc td.num,.doc th.num{text-align:end;white-space:nowrap;font-variant-numeric:tabular-nums}
.doc table.meta td:first-child,.doc table.kv td:first-child{width:32%;background:var(--soft);font-weight:600}
.doc h2.h1{font-size:18px;margin:22px 0 8px;color:var(--brand);break-after:avoid}
.doc h3.h2{font-size:15px;margin:14px 0 6px;break-after:avoid}
.doc p{margin:6px 0 10px}
.doc ul,.doc ol{margin:6px 0 12px;padding-inline-start:22px}
.doc li{margin:3px 0}
.doc figure{margin:10px 0 16px;break-inside:avoid;overflow-x:auto}
.doc figure svg{max-width:100%;height:auto}
.doc figcaption{color:var(--muted);font-size:11.5px;margin-top:4px}
.doc .note{background:var(--soft);border-inline-start:3px solid var(--brand);padding:8px 12px;color:var(--muted);margin:8px 0 14px}
.doc .st{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11.5px;background:#eef0f3}
.doc .st-done{background:#d3f9d8;color:#1b6b2e}.doc .st-in_progress{background:#dbe4ff;color:#2b3cad}
.doc .st-overdue{background:#ffe3e3;color:#a61e1e}.doc .st-todo{background:#f1f3f5;color:#495057}
.doc tr{break-inside:avoid}
.doc bdi{unicode-bidi:isolate}
"""


def _cell(v, num=False, status=False, statuses=None):
    s = escape(str(v))
    if status and statuses:
        cls = statuses.get(str(v), "todo")
        s = f'<span class="st st-{cls}">{s}</span>'
    return f'<td class="num">{s}</td>' if num else f"<td>{s}</td>"


def blocks_html(content: dict, status_classes: dict | None = None) -> str:
    out = []
    for b in content["blocks"]:
        t = b["t"]
        if t == "h1":
            out.append(f'<h2 class="h1">{escape(b["text"])}</h2>')
        elif t == "h2":
            out.append(f'<h3 class="h2">{escape(b["text"])}</h3>')
        elif t == "p":
            out.append(f"<p>{escape(b['text'])}</p>")
        elif t == "note":
            out.append(f'<div class="note">{escape(b["text"])}</div>')
        elif t in ("bullets", "steps"):
            tag = "ul" if t == "bullets" else "ol"
            out.append(f"<{tag}>" + "".join(f"<li>{escape(i)}</li>" for i in b["items"]) + f"</{tag}>")
        elif t == "kv":
            out.append('<table class="kv">' + "".join(f"<tr><td>{escape(k)}</td><td>{escape(str(v))}</td></tr>" for k, v in b["rows"]) + "</table>")
        elif t == "table":
            num = set(b.get("num") or [])
            widths = b.get("widths")
            cg = ""
            if widths:
                cg = "<colgroup>" + "".join(f'<col style="width:{w}%">' for w in widths) + "</colgroup>"
            head = "".join(f'<th class="{"num" if i in num else ""}">{escape(c)}</th>' for i, c in enumerate(b["cols"]))
            rows = "".join("<tr>" + "".join(_cell(v, i in num, i == b.get("status_col"), status_classes) for i, v in enumerate(r)) + "</tr>"
                           for r in b["rows"])
            out.append(f"<table>{cg}<thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
        elif t == "chart":
            cap = f"<figcaption>{escape(b['caption'])}</figcaption>" if b.get("caption") else ""
            out.append(f"<figure>{b['svg']}{cap}</figure>")
    return "\n".join(out)


def status_classes(loc: str) -> dict:
    return {i18n.t(f"status.{k}", loc): k for k in ("done", "in_progress", "overdue", "todo", "blocked")}


def document_html(content: dict) -> str:
    loc = content["locale"]
    meta = "".join(f"<tr><td>{escape(k)}</td><td>{escape(str(v))}</td></tr>" for k, v in content["meta"])
    sub = f'<div class="sub">{escape(content.get("subtitle") or content["project"])}</div>'
    kicker = escape(i18n.t("doc.kicker_report" if content["kind"] == "report" else "doc.kicker_doc", loc))
    return (f'<article class="doc" dir="{content["dir"]}" lang="{loc}">'
            f'<div class="cover"><div class="kicker">{kicker}</div><h1>{escape(content["title"])}</h1>{sub}</div>'
            f'<table class="meta">{meta}</table>{blocks_html(content, status_classes(loc))}</article>')


def standalone(content: dict, for_print: bool = True, auto_print: bool = False) -> str:
    """Full HTML page used to print the PDF (or opened by the browser as a print fallback)."""
    info = i18n.info(content["locale"])
    fonts = "&family=".join(["Noto+Sans:wght@400;600;700"] + info["gfonts"])
    script = "<script>window.addEventListener('load',()=>setTimeout(()=>window.print(),400))</script>" if auto_print else ""
    # The server prints with locally installed Noto fonts; browsers viewing the page load web fonts.
    webfonts = "" if (for_print and not auto_print) else (
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link href="https://fonts.googleapis.com/css2?family={fonts}&display=swap" rel="stylesheet">')
    return f"""<!doctype html><html lang="{content['locale']}" dir="{content['dir']}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(content['title'])} — {escape(content['project'])}</title>
{webfonts}
<style>@page{{size:A4;margin:18mm 14mm 18mm 14mm}}
body{{margin:0;font-family:{info['font_css']};background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
{'' if for_print else '.wrap{max-width:900px;margin:24px auto;padding:0 16px}'}
{DOC_CSS}</style></head><body><div class="wrap">{document_html(content)}</div>{script}</body></html>"""
