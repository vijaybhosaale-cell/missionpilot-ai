"""Server-side SVG charts. The same SVG is shown on screen, printed into PDFs and
rasterised into Word files, so a chart looks identical everywhere. Every label is
localized; Arabic mirrors the charts (right-to-left)."""
from __future__ import annotations

import datetime as dt
from xml.sax.saxutils import escape

from . import i18n
from .planner import is_workday

C = dict(ink="#1f2937", muted="#6b7280", grid="#e5e7eb", soft="#f3f4f6", brand="#4053d6", brand_soft="#c7cdfa",
         done="#2f9e44", left="#ffd8a8", left_ink="#8a4b00", late="#ffc9c9", late_ink="#a61e1e", crit="#e03131",
         ms="#7048e8", amber="#f59f00", red="#e03131", green="#2f9e44", blue="#1c7ed6", white="#ffffff", todo="#dee2e6")
# colour code used everywhere (screen, PDF, Word, Excel)
STATUS_COLOR = {"done": "#2f9e44", "in_progress": "#4053d6", "blocked": "#e03131", "overdue": "#f03e3e", "todo": "#adb5bd"}
PRIORITY_COLOR = {"critical": "#e03131", "high": "#f76707", "medium": "#1c7ed6", "low": "#adb5bd"}


import itertools
_ids = itertools.count(1)


def _uid(prefix: str) -> str:
    return f"{prefix}{next(_ids)}"


def _e(s) -> str:
    return escape(str(s), {'"': "&quot;"})


def _svg(w, h, body, locale, title=""):
    font = i18n.info(locale)["font_css"].replace('"', "'")
    direction = i18n.info(locale)["dir"]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" height="{h:.0f}" '
            f'role="img" aria-label="{_e(title)}" style="font-family:{font};max-width:100%;height:auto" direction="{direction}">'
            f'<title>{_e(title)}</title>{body}</svg>')


class _Mirror:
    """Maps x positions for right-to-left layouts."""

    def __init__(self, width, rtl):
        self.w, self.rtl = width, rtl

    def x(self, x, w=0):
        return self.w - x - w if self.rtl else x

    def anchor(self, a):
        if not self.rtl:
            return a
        return {"start": "end", "end": "start"}.get(a, a)


# ---------------------------------------------------------------- Gantt

def gantt(data: dict, locale: str, scale: str = "week", show_table: bool = True, critical: set | None = None,
          max_rows: int | None = None) -> str:
    tasks = sorted(data["tasks"], key=lambda t: (t["seq"]))
    if max_rows:
        tasks = tasks[:max_rows]
    if not tasks:
        return ""
    rtl = i18n.info(locale)["dir"] == "rtl"
    critical = critical if critical is not None else data["metrics"]["critical"]
    start = min(t["start"] for t in tasks)
    end = max(t["end"] for t in tasks)
    start -= dt.timedelta(days=start.weekday())
    end += dt.timedelta(days=6 - end.weekday())
    ndays = (end - start).days + 1
    px = {"day": 26, "week": 9, "month": 3.2}.get(scale, 9)
    row_h, head_h = 26, 46
    cols = [("wbs", 42), ("task", 250), ("res", 120), ("start", 74), ("end", 74), ("days", 40), ("pct", 46)] if show_table else []
    table_w = sum(w for _, w in cols)
    chart_w = ndays * px
    W = table_w + chart_w + 16
    phases = data["phases"]
    rows = []
    for ph in phases:
        pts = [t for t in tasks if t["phase_id"] == ph["id"]]
        if not pts:
            continue
        rows.append(("phase", ph, pts))
        rows.extend(("task", t, None) for t in pts)
    H = head_h + len(rows) * row_h + 8
    M = _Mirror(W, rtl)
    out = []
    x0 = table_w + 8  # chart origin (LTR)

    def X(d: dt.date) -> float:
        return x0 + (d - start).days * px

    # header background
    out.append(f'<rect x="0" y="0" width="{W}" height="{head_h}" fill="{C["soft"]}"/>')
    # table header
    labels = {"wbs": i18n.t("gantt.col_wbs", locale), "task": i18n.t("gantt.col_task", locale),
              "res": i18n.t("gantt.col_resource", locale), "start": i18n.t("gantt.col_start", locale),
              "end": i18n.t("gantt.col_end", locale), "days": i18n.t("gantt.col_days", locale),
              "pct": i18n.t("gantt.col_pct", locale)}
    cx = 0
    col_x = {}
    for key, w in cols:
        col_x[key] = (cx, w)
        out.append(f'<text x="{M.x(cx + 6)}" y="{head_h - 12}" font-size="11" font-weight="600" fill="{C["muted"]}" '
                   f'text-anchor="{M.anchor("start")}">{_e(labels[key])}</text>')
        cx += w
    # clip for task name column
    ct, cr, ca = _uid("ct"), _uid("cr"), _uid("ar")
    if show_table:
        tx, tw = col_x["task"]
        out.append(f'<defs><clipPath id="{ct}"><rect x="{M.x(tx, tw - 6)}" y="0" width="{tw - 6}" height="{H}"/></clipPath>'
                   f'<clipPath id="{cr}"><rect x="{M.x(col_x["res"][0], col_x["res"][1] - 6)}" y="0" width="{col_x["res"][1] - 6}" height="{H}"/></clipPath>'
                   f'<marker id="{ca}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
                   f'<path d="M0,0 L8,4 L0,8 z" fill="{C["muted"]}"/></marker></defs>')
    # timeline header: months + ticks
    d = start
    while d <= end:
        if d.day == 1 or d == start:
            xx = X(d)
            out.append(f'<text x="{M.x(xx + 4)}" y="16" font-size="11" font-weight="600" fill="{C["ink"]}" '
                       f'text-anchor="{M.anchor("start")}">{_e(i18n.month_label(d, locale))}</text>')
            out.append(f'<line x1="{M.x(xx)}" y1="0" x2="{M.x(xx)}" y2="{H}" stroke="{C["grid"]}"/>')
        if scale == "day":
            if not is_workday(d):
                out.append(f'<rect x="{M.x(X(d), px)}" y="{head_h}" width="{px}" height="{H - head_h}" fill="{C["soft"]}"/>')
            out.append(f'<text x="{M.x(X(d) + px / 2)}" y="{head_h - 10}" font-size="9" fill="{C["muted"]}" text-anchor="middle">{d.day}</text>')
        elif scale == "week" and d.weekday() == 0:
            out.append(f'<line x1="{M.x(X(d))}" y1="{head_h - 8}" x2="{M.x(X(d))}" y2="{H}" stroke="{C["grid"]}" stroke-dasharray="2,3"/>')
            out.append(f'<text x="{M.x(X(d) + 3)}" y="{head_h - 10}" font-size="9" fill="{C["muted"]}" text-anchor="{M.anchor("start")}">'
                       f'{_e(i18n.short_date(d, locale))}</text>')
        d += dt.timedelta(days=1)

    ypos = {}
    for i, (kind, obj, pts) in enumerate(rows):
        y = head_h + i * row_h
        if kind == "task":
            ypos[obj["id"]] = y + row_h / 2
        if i % 2 == 1:
            out.append(f'<rect x="0" y="{y}" width="{W}" height="{row_h}" fill="#fafafa"/>')
    out.append(f'<line x1="0" y1="{head_h}" x2="{W}" y2="{head_h}" stroke="{C["grid"]}"/>')
    if show_table:
        out.append(f'<line x1="{M.x(table_w + 4)}" y1="0" x2="{M.x(table_w + 4)}" y2="{H}" stroke="{C["grid"]}"/>')

    # dependency arrows (drawn under bars)
    by = {t["id"]: t for t in tasks}
    for t in tasks:
        for p in t["preds"]:
            if p not in by or p not in ypos or t["id"] not in ypos:
                continue
            pt = by[p]
            xa = X(pt["end"]) + (px if not pt["milestone"] else 0)
            xb = X(t["start"]) + (0 if not t["milestone"] else 0)
            ya, yb = ypos[p], ypos[t["id"]]
            mx = xa + 6
            path = f'M{M.x(xa)},{ya} L{M.x(mx)},{ya} L{M.x(mx)},{yb} L{M.x(max(xb, mx + 4) - 1)},{yb}'
            out.append(f'<path d="{path}" fill="none" stroke="{C["muted"]}" stroke-width="0.8" opacity="0.55" marker-end="url(#{ca})"/>')

    for i, (kind, obj, pts) in enumerate(rows):
        y = head_h + i * row_h
        cy = y + row_h / 2
        if kind == "phase":
            s, e = min(t["start"] for t in pts), max(t["end"] for t in pts)
            work = [t for t in pts if not t["milestone"]]
            prog = sum(t["days"] * t["progress"] for t in work) / max(1, sum(t["days"] for t in work))
            if show_table:
                tx, tw = col_x["task"]
                out.append(f'<text x="{M.x(6)}" y="{cy + 4}" font-size="11.5" font-weight="700" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">'
                           f'{_e(obj["seq"])}</text>')
                out.append(f'<text x="{M.x(tx + 6)}" y="{cy + 4}" font-size="12" font-weight="700" fill="{C["ink"]}" '
                           f'text-anchor="{M.anchor("start")}" clip-path="url(#{ct})">{_e(obj["name"])}</text>')
                out.append(_cell(M, col_x["start"], cy, i18n.short_date(s, locale), True))
                out.append(_cell(M, col_x["end"], cy, i18n.short_date(e, locale), True))
                out.append(_cell(M, col_x["pct"], cy, i18n.pct(prog, locale), True))
            xs, xe = X(s), X(e) + px
            out.append(f'<rect x="{M.x(xs, xe - xs)}" y="{cy - 4}" width="{xe - xs}" height="7" fill="{C["ink"]}" rx="1"/>')
            pdone = (xe - xs) * prog / 100
            if pdone:
                out.append(f'<rect x="{M.x(xs, pdone)}" y="{cy - 4}" width="{pdone}" height="7" fill="{C["done"]}" rx="1"/>')
            continue
        t = obj
        crit = t["id"] in critical
        if show_table:
            out.append(_cell(M, col_x["wbs"], cy, t["wbs"]))
            tx, tw = col_x["task"]
            weight = "600" if t["milestone"] or t["priority"] in ("critical", "high") else "400"
            out.append(f'<circle cx="{M.x(tx + 7)}" cy="{cy}" r="4" fill="{PRIORITY_COLOR[t["priority"]]}"><title>{_e(i18n.t("priority." + t["priority"], locale))}</title></circle>')
            out.append(f'<text x="{M.x(tx + 16)}" y="{cy + 4}" font-size="11.5" font-weight="{weight}" fill="{C["ink"]}" '
                       f'text-anchor="{M.anchor("start")}" clip-path="url(#{ct})"><title>{_e(t["title"])}</title>{_e(t["title"])}</text>')
            rx_, rw = col_x["res"]
            out.append(f'<text x="{M.x(rx_ + 6)}" y="{cy + 4}" font-size="11" fill="{C["muted"]}" text-anchor="{M.anchor("start")}" '
                       f'clip-path="url(#{cr})">{_e(t["assignee"])}</text>')
            out.append(_cell(M, col_x["start"], cy, i18n.short_date(t["start"], locale)))
            out.append(_cell(M, col_x["end"], cy, i18n.short_date(t["end"], locale)))
            out.append(_cell(M, col_x["days"], cy, i18n.num(t["days"], locale)))
            out.append(_cell(M, col_x["pct"], cy, i18n.pct(t["progress"], locale)))
        if t["milestone"]:
            xx = X(t["end"]) + px
            col = C["done"] if t["progress"] >= 100 else C["ms"]
            out.append(f'<path d="M{M.x(xx)},{cy - 7} l7,7 l-7,7 l-7,-7 z" fill="{col}"><title>{_e(t["title"])}</title></path>')
            continue
        xs, xe = X(t["start"]), X(t["end"]) + px
        w = max(xe - xs, 2)
        overdue = t["end"] < data["metrics"]["today"] and t["progress"] < 100
        blocked = t["status"] == "blocked"
        base = C["late"] if overdue or blocked else (C["todo"] if t["progress"] == 0 else C["brand_soft"])
        stroke = f' stroke="{C["crit"]}" stroke-width="1.4"' if crit else ""
        out.append(f'<rect x="{M.x(xs, w)}" y="{cy - 8}" width="{w}" height="16" rx="3" fill="{base}"{stroke}>'
                   f'<title>{_e(t["title"])} — {_e(i18n.t("status." + ("overdue" if overdue else t["status"]), locale))} — {_e(i18n.pct(t["progress"], locale))}</title></rect>')
        if t["progress"]:
            pw = w * t["progress"] / 100
            fill = C["done"] if t["progress"] >= 100 else (C["red"] if blocked else C["brand"])
            out.append(f'<rect x="{M.x(xs, pw)}" y="{cy - 8}" width="{pw}" height="16" rx="3" fill="{fill}"/>')
            if w > 34 and t["progress"] < 100:
                out.append(f'<text x="{M.x(xs + 4)}" y="{cy + 3.5}" font-size="9" font-weight="700" fill="{C["white"] if pw > 26 else C["ink"]}" '
                           f'text-anchor="{M.anchor("start")}">{_e(i18n.pct(t["progress"], locale))}</text>')
        label = t["assignee"]
        out.append(f'<text x="{M.x(xe + 5)}" y="{cy + 4}" font-size="10" fill="{C["muted"]}" text-anchor="{M.anchor("start")}">{_e(label)}</text>')

    # today line
    today = data["metrics"]["today"]
    if start <= today <= end:
        xt = X(today) + px / 2
        out.append(f'<line x1="{M.x(xt)}" y1="{head_h - 4}" x2="{M.x(xt)}" y2="{H}" stroke="{C["red"]}" stroke-width="1.5"/>')
        out.append(f'<text x="{M.x(xt + 3)}" y="{head_h - 30}" font-size="10" font-weight="600" fill="{C["red"]}" '
                   f'text-anchor="{M.anchor("start")}">{_e(i18n.t("gantt.today", locale))}</text>')
    return _svg(W, H, "".join(out), locale, i18n.t("gantt.title", locale))


def _cell(M, col, cy, text, bold=False):
    x, w = col
    weight = "700" if bold else "400"
    return (f'<text x="{M.x(x + 6)}" y="{cy + 4}" font-size="11" font-weight="{weight}" fill="{C["ink"]}" '
            f'text-anchor="{M.anchor("start")}">{_e(text)}</text>')


def gantt_legend(locale) -> str:
    items = [(C["todo"], "gantt.legend_not_started"), (C["brand_soft"], "gantt.legend_planned"), (C["brand"], "gantt.legend_progress"),
             (C["done"], "gantt.legend_done"), (C["late"], "gantt.legend_overdue"), ("crit", "gantt.legend_critical"),
             ("ms", "gantt.legend_milestone"), ("today", "gantt.today"), None] + \
            [("dot:" + PRIORITY_COLOR[p], "priority." + p) for p in ("critical", "high", "medium", "low")]
    out, x, y = [], 0, 0
    rtl = i18n.info(locale)["dir"] == "rtl"
    W = 900
    M = _Mirror(W, rtl)
    for item in items:
        if item is None:
            x, y = 0, y + 24
            out.append(f'<text x="{M.x(x)}" y="{y + 14}" font-size="11" font-weight="600" fill="{C["muted"]}" text-anchor="{M.anchor("start")}">'
                       f'{_e(i18n.t("doc.c.priority", locale))}:</text>')
            x += 20 + 7.5 * len(i18n.t("doc.c.priority", locale))
            continue
        color, key = item
        label = i18n.t(key, locale)
        if color.startswith("dot:"):
            out.append(f'<circle cx="{M.x(x + 7)}" cy="{y + 10}" r="5" fill="{color[4:]}"/>')
            out.append(f'<text x="{M.x(x + 18)}" y="{y + 14}" font-size="11" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(label)}</text>')
            x += 30 + 7.2 * len(label)
            continue
        if color == "crit":
            out.append(f'<rect x="{M.x(x, 18)}" y="{y + 4}" width="18" height="12" rx="2" fill="{C["brand_soft"]}" stroke="{C["crit"]}" stroke-width="1.4"/>')
        elif color == "ms":
            out.append(f'<path d="M{M.x(x + 9)},{y + 3} l7,7 l-7,7 l-7,-7 z" fill="{C["ms"]}"/>')
        elif color == "today":
            out.append(f'<line x1="{M.x(x + 9)}" y1="{y + 2}" x2="{M.x(x + 9)}" y2="{y + 18}" stroke="{C["red"]}" stroke-width="2"/>')
        else:
            out.append(f'<rect x="{M.x(x, 18)}" y="{y + 4}" width="18" height="12" rx="2" fill="{color}"/>')
        out.append(f'<text x="{M.x(x + 24)}" y="{y + 14}" font-size="11" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(label)}</text>')
        x += 34 + 7.2 * len(label)
        if x > W - 120:
            x, y = 0, y + 24
    return _svg(W, y + 24, "".join(out), locale, "legend")


# ---------------------------------------------------------------- progress bar graph (done vs left)

def progress_bars(rows: list[dict], locale: str, title: str = "", show_days: bool = True, width: int = 884) -> str:
    """rows: dicts with label, progress (0-100), days, overdue(bool), sub (optional small text).
    Each bar is 100% wide: the green part is what is done, the orange part is what is left —
    both segments are labelled with % and days."""
    rtl = i18n.info(locale)["dir"] == "rtl"
    label_w = int(width * 0.34)
    bar_w, row_h, top = width - label_w - 24, 30, 30
    W = label_w + bar_w + 24
    cl = _uid("cl")
    H = top + len(rows) * row_h + 34
    M = _Mirror(W, rtl)
    out = [f'<defs><clipPath id="{cl}"><rect x="{M.x(0, label_w - 8)}" y="0" width="{label_w - 8}" height="{H}"/></clipPath></defs>']
    # axis ticks
    for p in (0, 25, 50, 75, 100):
        xx = label_w + bar_w * p / 100
        out.append(f'<line x1="{M.x(xx)}" y1="{top - 6}" x2="{M.x(xx)}" y2="{H - 30}" stroke="{C["grid"]}"/>')
        out.append(f'<text x="{M.x(xx)}" y="{top - 10}" font-size="10" fill="{C["muted"]}" text-anchor="middle">{_e(i18n.pct(p, locale))}</text>')
    for i, r in enumerate(rows):
        y = top + i * row_h
        cy = y + row_h / 2
        out.append(f'<text x="{M.x(4)}" y="{cy + 4}" font-size="12" fill="{C["ink"]}" font-weight="{600 if r.get("bold") else 400}" '
                   f'text-anchor="{M.anchor("start")}" clip-path="url(#{cl})"><title>{_e(r["label"])}</title>{_e(r["label"])}</text>')
        p = max(0, min(100, r["progress"]))
        dw = bar_w * p / 100
        lw = bar_w - dw
        days = r.get("days") or 0
        done_d = days * p / 100
        left_d = days - done_d
        x_done = label_w
        if dw > 0:
            out.append(f'<rect x="{M.x(x_done, dw)}" y="{cy - 10}" width="{dw}" height="20" fill="{C["done"]}" rx="2"/>')
        if lw > 0:
            fill = C["late"] if r.get("overdue") else C["left"]
            out.append(f'<rect x="{M.x(x_done + dw, lw)}" y="{cy - 10}" width="{lw}" height="20" fill="{fill}" rx="2"/>')
        done_txt = i18n.t("progress.done_label", locale, pct=i18n.pct(p, locale), days=_d(done_d, locale)) if show_days and days else i18n.pct(p, locale)
        left_txt = i18n.t("progress.left_label", locale, pct=i18n.pct(100 - p, locale), days=_d(left_d, locale)) if show_days and days else i18n.t("progress.left_short", locale, pct=i18n.pct(100 - p, locale))
        # labels inside segments when they fit, otherwise next to the bar
        if dw > 7 * len(done_txt) * 0.75:
            out.append(f'<text x="{M.x(x_done + 6)}" y="{cy + 4}" font-size="11" font-weight="600" fill="{C["white"]}" text-anchor="{M.anchor("start")}">{_e(done_txt)}</text>')
            done_inside = True
        else:
            done_inside = False
        if p < 100:
            ink = C["late_ink"] if r.get("overdue") else C["left_ink"]
            if lw > 7 * len(left_txt) * 0.75:
                out.append(f'<text x="{M.x(label_w + bar_w - 6)}" y="{cy + 4}" font-size="11" font-weight="600" fill="{ink}" text-anchor="{M.anchor("end")}">{_e(left_txt)}</text>')
                if not done_inside and p > 0:
                    out.append(f'<text x="{M.x(x_done + dw + 6)}" y="{cy + 4}" font-size="11" font-weight="600" fill="{C["done"]}" text-anchor="{M.anchor("start")}">{_e(done_txt)}</text>')
            else:
                out.append(f'<text x="{M.x(x_done + 6)}" y="{cy + 4}" font-size="11" font-weight="600" fill="{C["white"]}" text-anchor="{M.anchor("start")}">{_e(done_txt + " · " + left_txt)}</text>')
    # legend
    ly = H - 14
    lx = label_w
    for color, key in ((C["done"], "progress.legend_done"), (C["left"], "progress.legend_left"), (C["late"], "progress.legend_late")):
        lab = i18n.t(key, locale)
        out.append(f'<rect x="{M.x(lx, 14)}" y="{ly - 10}" width="14" height="12" rx="2" fill="{color}"/>')
        out.append(f'<text x="{M.x(lx + 20)}" y="{ly}" font-size="11" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(lab)}</text>')
        lx += 40 + 7 * len(lab)
    return _svg(W, H, "".join(out), locale, title or i18n.t("progress.title", locale))


def _d(days, locale):
    return i18n.t("common.days_short", locale, n=i18n.num(days, locale, 1) if days % 1 else i18n.num(int(days), locale))


# ---------------------------------------------------------------- S-curve (planned vs actual)

def scurve(hist: list[dict], locale: str, width: int = 860) -> str:
    if not hist:
        return ""
    rtl = i18n.info(locale)["dir"] == "rtl"
    W, H, L_, R_, T_, B_ = width, 280, 46, 20, 20, 46
    M = _Mirror(W, rtl)
    n = len(hist)
    pw, ph = W - L_ - R_, H - T_ - B_

    def P(i, v):
        return L_ + pw * i / max(1, n - 1), T_ + ph * (1 - v / 100)

    out = []
    for v in (0, 25, 50, 75, 100):
        _, y = P(0, v)
        out.append(f'<line x1="{M.x(L_)}" y1="{y}" x2="{M.x(W - R_)}" y2="{y}" stroke="{C["grid"]}"/>')
        out.append(f'<text x="{M.x(L_ - 6)}" y="{y + 4}" font-size="10" fill="{C["muted"]}" text-anchor="{M.anchor("end")}">{_e(i18n.pct(v, locale))}</text>')
    step = max(1, n // 7)
    for i in range(0, n, step):
        x, _ = P(i, 0)
        out.append(f'<text x="{M.x(x)}" y="{H - B_ + 16}" font-size="10" fill="{C["muted"]}" text-anchor="middle">{_e(i18n.short_date(hist[i]["date"], locale))}</text>')
    pts = " ".join(f'{M.x(P(i, h["planned"])[0])},{P(i, h["planned"])[1]}' for i, h in enumerate(hist))
    out.append(f'<polyline points="{pts}" fill="none" stroke="{C["brand_soft"]}" stroke-width="3" stroke-dasharray="6,4"/>')
    act = [(i, h["actual"]) for i, h in enumerate(hist) if h["actual"] is not None]
    if act:
        pts = " ".join(f'{M.x(P(i, v)[0])},{P(i, v)[1]}' for i, v in act)
        out.append(f'<polyline points="{pts}" fill="none" stroke="{C["done"]}" stroke-width="3"/>')
        i, v = act[-1]
        x, y = P(i, v)
        out.append(f'<circle cx="{M.x(x)}" cy="{y}" r="4.5" fill="{C["done"]}"/>')
        out.append(f'<text x="{M.x(x + 8)}" y="{y - 6}" font-size="11" font-weight="700" fill="{C["done"]}" text-anchor="{M.anchor("start")}">{_e(i18n.pct(v, locale))}</text>')
    ly = H - 10
    lx = L_
    for color, key, dash in ((C["brand_soft"], "progress.planned", "6,4"), (C["done"], "progress.actual", "")):
        lab = i18n.t(key, locale)
        out.append(f'<line x1="{M.x(lx)}" y1="{ly - 4}" x2="{M.x(lx + 22)}" y2="{ly - 4}" stroke="{color}" stroke-width="3" stroke-dasharray="{dash}"/>')
        out.append(f'<text x="{M.x(lx + 28)}" y="{ly}" font-size="11" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(lab)}</text>')
        lx += 50 + 7 * len(lab)
    return _svg(W, H, "".join(out), locale, i18n.t("progress.scurve_title", locale))


# ---------------------------------------------------------------- budget

def budget_bars(lines: list[dict], currency: str, locale: str) -> str:
    rtl = i18n.info(locale)["dir"] == "rtl"
    label_w, bar_w, row_h, top = 210, 520, 40, 14
    W = label_w + bar_w + 150
    H = top + len(lines) * row_h + 30
    M = _Mirror(W, rtl)
    mx = max([max(b["planned"], b["actual"]) for b in lines] + [1])
    out = []
    for i, b in enumerate(lines):
        y = top + i * row_h
        out.append(f'<text x="{M.x(4)}" y="{y + 20}" font-size="12" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(b["label"])}</text>')
        pw = bar_w * b["planned"] / mx
        aw = bar_w * b["actual"] / mx
        over = b["actual"] > b["planned"]
        out.append(f'<rect x="{M.x(label_w, pw)}" y="{y + 4}" width="{pw}" height="12" rx="2" fill="{C["brand_soft"]}"/>')
        out.append(f'<rect x="{M.x(label_w, aw)}" y="{y + 18}" width="{aw}" height="12" rx="2" fill="{C["red"] if over else C["brand"]}"/>')
        out.append(f'<text x="{M.x(label_w + max(pw, aw) + 8)}" y="{y + 15}" font-size="10.5" fill="{C["muted"]}" text-anchor="{M.anchor("start")}">'
                   f'{_e(i18n.money_minor(b["planned"], currency, locale))}</text>')
        out.append(f'<text x="{M.x(label_w + max(pw, aw) + 8)}" y="{y + 29}" font-size="10.5" font-weight="600" fill="{C["red"] if over else C["ink"]}" '
                   f'text-anchor="{M.anchor("start")}">{_e(i18n.money_minor(b["actual"], currency, locale))}</text>')
    ly, lx = H - 8, label_w
    for color, key in ((C["brand_soft"], "budget.planned"), (C["brand"], "budget.actual"), (C["red"], "budget.over")):
        lab = i18n.t(key, locale)
        out.append(f'<rect x="{M.x(lx, 14)}" y="{ly - 10}" width="14" height="12" rx="2" fill="{color}"/>')
        out.append(f'<text x="{M.x(lx + 20)}" y="{ly}" font-size="11" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(lab)}</text>')
        lx += 40 + 7 * len(lab)
    return _svg(W, H, "".join(out), locale, i18n.t("budget.chart_title", locale))


# ---------------------------------------------------------------- risk matrix

def risk_matrix(risks: list[dict], locale: str) -> str:
    rtl = i18n.info(locale)["dir"] == "rtl"
    cell, L_, T_ = 64, 70, 20
    W, H = L_ + 5 * cell + 20, T_ + 5 * cell + 56
    M = _Mirror(W, rtl)
    out = []
    for p in range(1, 6):
        for imp in range(1, 6):
            score = p * imp
            col = "#d3f9d8" if score < 6 else "#fff3bf" if score < 12 else "#ffd8a8" if score < 16 else "#ffc9c9"
            x = L_ + (imp - 1) * cell
            y = T_ + (5 - p) * cell
            out.append(f'<rect x="{M.x(x, cell - 2)}" y="{y}" width="{cell - 2}" height="{cell - 2}" fill="{col}" rx="4"/>')
            here = [r for r in risks if r["probability"] == p and r["impact"] == imp and r["status"] != "closed"]
            if here:
                codes = ", ".join(r["code"] for r in here[:3]) + ("…" if len(here) > 3 else "")
                out.append(f'<text x="{M.x(x + cell / 2 - 1)}" y="{y + cell / 2 + 4}" font-size="11" font-weight="700" fill="{C["ink"]}" text-anchor="middle">'
                           f'<title>{_e("; ".join(r["title"] for r in here))}</title>{_e(codes)}</text>')
    for v in range(1, 6):
        out.append(f'<text x="{M.x(L_ - 8)}" y="{T_ + (5 - v) * cell + cell / 2 + 4}" font-size="11" fill="{C["muted"]}" text-anchor="{M.anchor("end")}">{v}</text>')
        out.append(f'<text x="{M.x(L_ + (v - 1) * cell + cell / 2)}" y="{T_ + 5 * cell + 16}" font-size="11" fill="{C["muted"]}" text-anchor="middle">{v}</text>')
    out.append(f'<text x="{M.x(L_ + 5 * cell / 2)}" y="{T_ + 5 * cell + 40}" font-size="12" font-weight="600" fill="{C["ink"]}" text-anchor="middle">{_e(i18n.t("risks.impact", locale))}</text>')
    yx = M.x(16)
    out.append(f'<text x="{yx}" y="{T_ + 5 * cell / 2}" font-size="12" font-weight="600" fill="{C["ink"]}" text-anchor="middle" '
               f'transform="rotate(-90 {yx} {T_ + 5 * cell / 2})">{_e(i18n.t("risks.probability", locale))}</text>')
    return _svg(W, H, "".join(out), locale, i18n.t("risks.matrix_title", locale))


# ---------------------------------------------------------------- resource workload heatmap

def workload_heatmap(wl: dict, locale: str) -> str:
    weeks, rows = wl["weeks"], wl["rows"]
    if not weeks:
        return ""
    rtl = i18n.info(locale)["dir"] == "rtl"
    name_w, cw, rh, top = 200, 46, 30, 40
    W = name_w + cw * len(weeks) + 10
    H = top + rh * len(rows) + 34
    M = _Mirror(W, rtl)
    out = []
    for j, w in enumerate(weeks):
        x = name_w + j * cw
        out.append(f'<text x="{M.x(x + cw / 2)}" y="{top - 22}" font-size="9.5" fill="{C["muted"]}" text-anchor="middle">{_e(i18n.t("team.week_n", locale, n=j + 1))}</text>')
        out.append(f'<text x="{M.x(x + cw / 2)}" y="{top - 8}" font-size="9.5" fill="{C["muted"]}" text-anchor="middle">{_e(i18n.short_date(w, locale))}</text>')
    for i, r in enumerate(rows):
        y = top + i * rh
        out.append(f'<text x="{M.x(4)}" y="{y + rh / 2 + 4}" font-size="11.5" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(r["member"]["name"])}</text>')
        for j, h in enumerate(r["hours"]):
            x = name_w + j * cw
            ratio = h / r["capacity"] if r["capacity"] else 0
            col = C["soft"] if h == 0 else "#d0ebff" if ratio <= 0.5 else "#74c0fc" if ratio <= 1.0 else "#ff8787"
            out.append(f'<rect x="{M.x(x + 1, cw - 2)}" y="{y + 2}" width="{cw - 2}" height="{rh - 4}" rx="3" fill="{col}"/>')
            if h:
                out.append(f'<text x="{M.x(x + cw / 2)}" y="{y + rh / 2 + 4}" font-size="10" font-weight="{700 if ratio > 1 else 400}" '
                           f'fill="{C["ink"]}" text-anchor="middle">{_e(i18n.num(h, locale))}</text>')
    ly, lx = H - 10, name_w
    for color, key in (("#d0ebff", "team.legend_light"), ("#74c0fc", "team.legend_full"), ("#ff8787", "team.legend_over")):
        lab = i18n.t(key, locale)
        out.append(f'<rect x="{M.x(lx, 14)}" y="{ly - 10}" width="14" height="12" rx="2" fill="{color}"/>')
        out.append(f'<text x="{M.x(lx + 20)}" y="{ly}" font-size="11" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(lab)}</text>')
        lx += 40 + 7 * len(lab)
    return _svg(W, H, "".join(out), locale, i18n.t("team.heatmap_title", locale))


def burndown(hist: list[dict], total_days: float, locale: str) -> str:
    """Remaining work (in days of effort) for every working day of the project.
    Ideal line = straight from the total to zero; actual line = what is really left; marker = today ("Day N")."""
    if not hist or not total_days:
        return ""
    rtl = i18n.info(locale)["dir"] == "rtl"
    W, H, L_, R_, T_, B_ = 860, 300, 52, 24, 34, 50
    M = _Mirror(W, rtl)
    n = len(hist)
    pw, ph = W - L_ - R_, H - T_ - B_
    top = total_days * 1.05

    def P(i, v):
        return L_ + pw * i / max(1, n - 1), T_ + ph * (1 - v / top)

    out = []
    step_v = max(1, round(total_days / 4 / 5) * 5) if total_days > 20 else max(1, round(total_days / 4))
    v = 0
    while v <= top:
        _, y = P(0, v)
        out.append(f'<line x1="{M.x(L_)}" y1="{y}" x2="{M.x(W - R_)}" y2="{y}" stroke="{C["grid"]}"/>')
        out.append(f'<text x="{M.x(L_ - 6)}" y="{y + 4}" font-size="10" fill="{C["muted"]}" text-anchor="{M.anchor("end")}">{_e(i18n.num(v, locale))}</text>')
        v += step_v
    every = max(1, n // 10)
    for i in range(0, n, every):
        x, _ = P(i, 0)
        out.append(f'<text x="{M.x(x)}" y="{H - B_ + 15}" font-size="10" fill="{C["muted"]}" text-anchor="middle">{_e(i18n.t("progress.day_n", locale, n=i + 1))}</text>')
        out.append(f'<text x="{M.x(x)}" y="{H - B_ + 28}" font-size="9" fill="{C["muted"]}" text-anchor="middle">{_e(i18n.short_date(hist[i]["date"], locale))}</text>')
    # ideal line
    x0, y0 = P(0, total_days)
    x1, y1 = P(n - 1, 0)
    out.append(f'<line x1="{M.x(x0)}" y1="{y0}" x2="{M.x(x1)}" y2="{y1}" stroke="{C["muted"]}" stroke-width="2" stroke-dasharray="6,5"/>')
    # planned (follows the schedule) and actual remaining work
    pts = " ".join(f'{M.x(P(i, total_days * (1 - h["planned"] / 100))[0])},{P(i, total_days * (1 - h["planned"] / 100))[1]}' for i, h in enumerate(hist))
    out.append(f'<polyline points="{pts}" fill="none" stroke="{C["brand_soft"]}" stroke-width="2.5"/>')
    act = [(i, total_days * (1 - h["actual"] / 100)) for i, h in enumerate(hist) if h["actual"] is not None]
    if act:
        area = f'{M.x(P(act[0][0], 0)[0])},{P(0, 0)[1]} ' + " ".join(f'{M.x(P(i, v)[0])},{P(i, v)[1]}' for i, v in act) + f' {M.x(P(act[-1][0], 0)[0])},{P(0, 0)[1]}'
        out.append(f'<polygon points="{area}" fill="{C["left"]}" opacity="0.35"/>')
        out.append(f'<polyline points="{" ".join(f"{M.x(P(i, v)[0])},{P(i, v)[1]}" for i, v in act)}" fill="none" stroke="{C["amber"]}" stroke-width="3"/>')
        i, v = act[-1]
        x, y = P(i, v)
        out.append(f'<line x1="{M.x(x)}" y1="{T_ - 6}" x2="{M.x(x)}" y2="{T_ + ph}" stroke="{C["red"]}" stroke-width="1.5"/>')
        out.append(f'<circle cx="{M.x(x)}" cy="{y}" r="5" fill="{C["amber"]}" stroke="#fff" stroke-width="1.5"/>')
        label = i18n.t("progress.today_day", locale, n=i + 1, total=n, left=i18n.num(v, locale, 1))
        anchor = "end" if i > n * 0.6 else "start"
        dx = -8 if anchor == "end" else 8
        out.append(f'<text x="{M.x(x + dx)}" y="{T_ - 12}" font-size="11.5" font-weight="700" fill="{C["red"]}" text-anchor="{M.anchor(anchor)}">{_e(label)}</text>')
    ly, lx = H - 8, L_
    for color, key, dash, w in ((C["muted"], "progress.bd_ideal", "6,5", 2), (C["brand_soft"], "progress.bd_planned", "", 2.5), (C["amber"], "progress.bd_actual", "", 3)):
        lab = i18n.t(key, locale)
        out.append(f'<line x1="{M.x(lx)}" y1="{ly - 4}" x2="{M.x(lx + 22)}" y2="{ly - 4}" stroke="{color}" stroke-width="{w}" stroke-dasharray="{dash}"/>')
        out.append(f'<text x="{M.x(lx + 28)}" y="{ly}" font-size="11" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(lab)}</text>')
        lx += 52 + 7 * len(lab)
    # movable "chosen day" marker, driven by the day slider on the Progress page
    out.append(f'<g id="bd-marker" style="display:none"><line id="bd-line" x1="0" y1="{T_ - 4}" x2="0" y2="{T_ + ph}" stroke="{C["blue"]}" stroke-width="2" stroke-dasharray="4,3"/>'
               f'<circle id="bd-dot-plan" r="5" fill="{C["brand"]}" stroke="#fff" stroke-width="1.5"/>'
               f'<circle id="bd-dot-act" r="5" fill="{C["amber"]}" stroke="#fff" stroke-width="1.5"/>'
               f'<rect id="bd-label-bg" height="18" rx="4" fill="{C["blue"]}"/><text id="bd-label" font-size="11.5" font-weight="700" fill="#fff" text-anchor="middle"></text></g>')
    svg = _svg(W, H, "".join(out), locale, i18n.t("progress.burndown_title", locale))
    attrs = f'id="bd-svg" data-l="{L_}" data-t="{T_}" data-pw="{pw}" data-ph="{ph}" data-top="{top}" data-n="{n}" data-w="{W}" data-rtl="{1 if rtl else 0}" '
    return svg.replace("<svg ", "<svg " + attrs, 1)


def weekly_completions(weeks: list[dict], locale: str) -> str:
    """weeks: [{start: date, done: int, cum_pct: float, future: bool}] — bars = tasks finished that week,
    line = cumulative % of the project complete."""
    if not weeks:
        return ""
    rtl = i18n.info(locale)["dir"] == "rtl"
    W, H, L_, R_, T_, B_ = 860, 270, 44, 48, 24, 46
    M = _Mirror(W, rtl)
    n = len(weeks)
    pw, ph = W - L_ - R_, H - T_ - B_
    bw = pw / n
    mx = max([w["done"] for w in weeks] + [1])
    out = []
    for v in range(0, mx + 1, max(1, mx // 4)):
        y = T_ + ph * (1 - v / mx)
        out.append(f'<line x1="{M.x(L_)}" y1="{y}" x2="{M.x(W - R_)}" y2="{y}" stroke="{C["grid"]}"/>')
        out.append(f'<text x="{M.x(L_ - 6)}" y="{y + 4}" font-size="10" fill="{C["muted"]}" text-anchor="{M.anchor("end")}">{_e(i18n.num(v, locale))}</text>')
    for p in (0, 50, 100):
        y = T_ + ph * (1 - p / 100)
        out.append(f'<text x="{M.x(W - R_ + 6)}" y="{y + 4}" font-size="10" fill="{C["brand"]}" text-anchor="{M.anchor("start")}">{_e(i18n.pct(p, locale))}</text>')
    pts = []
    for i, w in enumerate(weeks):
        x = L_ + i * bw
        h = ph * w["done"] / mx
        col = C["grid"] if w.get("future") else C["done"]
        if w["done"]:
            out.append(f'<rect x="{M.x(x + bw * 0.18, bw * 0.64)}" y="{T_ + ph - h}" width="{bw * 0.64}" height="{h}" rx="3" fill="{col}">'
                       f'<title>{_e(i18n.t("progress.week_done", locale, n=w["done"]))}</title></rect>')
            out.append(f'<text x="{M.x(x + bw / 2)}" y="{T_ + ph - h - 4}" font-size="10" font-weight="600" fill="{C["ink"]}" text-anchor="middle">{w["done"]}</text>')
        if i % max(1, n // 8) == 0:
            out.append(f'<text x="{M.x(x + bw / 2)}" y="{H - B_ + 16}" font-size="10" fill="{C["muted"]}" text-anchor="middle">{_e(i18n.short_date(w["start"], locale))}</text>')
        if w.get("cum_pct") is not None:
            pts.append((M.x(x + bw / 2), T_ + ph * (1 - w["cum_pct"] / 100)))
    if pts:
        out.append(f'<polyline points="{" ".join(f"{a},{b}" for a, b in pts)}" fill="none" stroke="{C["brand"]}" stroke-width="2.5"/>')
        for a, b in pts:
            out.append(f'<circle cx="{a}" cy="{b}" r="3" fill="{C["brand"]}"/>')
    ly, lx = H - 10, L_
    for color, key, line in ((C["done"], "progress.legend_week_done", False), (C["brand"], "progress.legend_cum", True)):
        lab = i18n.t(key, locale)
        if line:
            out.append(f'<line x1="{M.x(lx)}" y1="{ly - 4}" x2="{M.x(lx + 18)}" y2="{ly - 4}" stroke="{color}" stroke-width="2.5"/>')
        else:
            out.append(f'<rect x="{M.x(lx, 14)}" y="{ly - 10}" width="14" height="12" rx="2" fill="{color}"/>')
        out.append(f'<text x="{M.x(lx + 22)}" y="{ly}" font-size="11" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(lab)}</text>')
        lx += 46 + 7 * len(lab)
    return _svg(W, H, "".join(out), locale, i18n.t("progress.weekly_title", locale))


def status_bar(m: dict, locale: str) -> str:
    """One stacked bar: done / in progress / not started / blocked counts."""
    W, H = 1000, 44
    rtl = i18n.info(locale)["dir"] == "rtl"
    M = _Mirror(W, rtl)
    total = max(1, m["tasks_total"])
    todo = m["tasks_total"] - m["tasks_done"] - m["tasks_active"]
    parts = [(m["tasks_done"], C["done"], "status.done"), (m["tasks_active"] - m["tasks_blocked"], C["brand"], "status.in_progress"),
             (m["tasks_blocked"], C["red"], "status.blocked"), (todo, C["grid"], "status.todo")]
    x, out = 0.0, []
    for n, col, key in parts:
        if n <= 0:
            continue
        w = W * n / total
        out.append(f'<rect x="{M.x(x, w)}" y="0" width="{w}" height="18" fill="{col}"><title>{_e(i18n.t(key, locale))}: {n}</title></rect>')
        x += w
    lx = 0
    for n, col, key in parts:
        lab = f'{i18n.t(key, locale)} {i18n.num(max(n, 0), locale)}'
        out.append(f'<rect x="{M.x(lx, 12)}" y="28" width="12" height="12" rx="2" fill="{col}"/>')
        out.append(f'<text x="{M.x(lx + 17)}" y="38" font-size="11" fill="{C["ink"]}" text-anchor="{M.anchor("start")}">{_e(lab)}</text>')
        lx += 36 + 7 * len(lab)
    return _svg(W, H, "".join(out), locale, i18n.t("home.status_mix", locale))
