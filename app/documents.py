"""Builds documents and reports as a language-neutral content model:
a list of blocks (headings, paragraphs, tables, charts). The same model is rendered
to HTML (preview + PDF) and to Word. Facts and numbers come from the project data,
so every language version contains exactly the same information."""
from __future__ import annotations

import datetime as dt
import json

from . import ai, analytics as A, charts, db, i18n, library as L
from .planner import add_workdays, workdays_between

T = i18n.t


# ---------------------------------------------------------------- helpers

def _h1(text):
    return {"t": "h1", "text": text}


def _h2(text):
    return {"t": "h2", "text": text}


def _p(text):
    return {"t": "p", "text": text}


def _ul(items):
    return {"t": "bullets", "items": [i for i in items if i]}


def _ol(items):
    return {"t": "steps", "items": [i for i in items if i]}


def _table(cols, rows, num=(), widths=None, status_col=None):
    return {"t": "table", "cols": cols, "rows": rows, "num": list(num), "widths": widths, "status_col": status_col}


def _kv(rows):
    return {"t": "kv", "rows": rows}


def _chart(svg, caption=""):
    return {"t": "chart", "svg": svg, "caption": caption}


def _note(text):
    return {"t": "note", "text": text}


class Ctx:
    def __init__(self, data: dict, loc: str):
        self.d, self.loc = data, loc
        self.p = data["project"]
        self.m = data["metrics"]
        self.cur = self.p["currency"]
        self.type = self.p["project_type"]
        self.pm = next((x for x in data["members"] if x["role_key"] == "project_manager"), data["members"][0] if data["members"] else None)

    def t(self, key, **kw):
        return T(key, self.loc, **kw)

    def money(self, minor):
        return i18n.money_minor(int(minor), self.cur, self.loc)

    def date(self, d):
        return i18n.date(d, self.loc)

    def pct(self, v):
        return i18n.pct(v, self.loc)

    def num(self, v, dec=0):
        return i18n.num(v, self.loc, dec)

    def status(self, s):
        return self.t(f"status.{s}")

    def prio(self, s):
        return self.t(f"priority.{s}")

    @property
    def params(self):
        p = self.p
        return dict(project=p["display_name"], start=self.date(p["start_date"]), end=self.date(p["end_date"]),
                    budget=self.money(p["budget_minor"]), team=self.num(len(self.d["members"])),
                    weeks=self.num(round(workdays_between(dt.date.fromisoformat(p["start_date"]), dt.date.fromisoformat(p["end_date"])) / 5)),
                    type=self.t(f"ptype.{self.type}"), pm=self.pm["name"] if self.pm else "—",
                    tasks=self.num(self.m["tasks_total"]), progress=self.pct(self.m["progress"]))

    def para(self, key, **extra):
        return _p(self.t(key, **{**self.params, **extra}))


def _context_text(c: Ctx) -> str:
    """Compact project facts for the Claude API (documents + chat)."""
    p, m = c.p, c.m
    lines = [f"Project: {p['display_name']}", f"Description: {p['display_description']}",
             f"Type: {c.type}; start {p['start_date']}; end {p['end_date']}; budget {c.money(p['budget_minor'])}",
             f"Progress {m['progress']:.0f}% (planned {m['planned']:.0f}%); tasks done {m['tasks_done']}/{m['tasks_total']}; overdue {len(m['overdue'])}",
             "Team: " + "; ".join(f"{x['name']} ({x['role']})" for x in c.d["members"]),
             "Phases: " + "; ".join(ph["name"] for ph in c.d["phases"]),
             "Tasks: " + "; ".join(f"{t['wbs']} {t['title']} [{t['start']}→{t['end']}, {t['progress']}%, {t['assignee']}]" for t in c.d["tasks"][:60]),
             "Risks: " + "; ".join(f"{r['code']} {r['title']} (P{r['probability']} I{r['impact']}, {r['status']})" for r in c.d["risks"]),
             "Budget: " + "; ".join(f"{b['label']} planned {c.money(b['planned'])} actual {c.money(b['actual'])}" for b in c.d["budget"])]
    return "\n".join(lines)


# ---------------------------------------------------------------- shared sections

def sec_overview(c: Ctx):
    p = c.p
    rows = [[c.t("doc.f.project"), p["display_name"]], [c.t("doc.f.type"), c.t(f"ptype.{c.type}")],
            [c.t("doc.f.pm"), c.pm["name"] if c.pm else "—"], [c.t("doc.f.start"), c.date(p["start_date"])],
            [c.t("doc.f.end"), c.date(p["end_date"])], [c.t("doc.f.budget"), c.money(p["budget_minor"])],
            [c.t("doc.f.team"), c.num(len(c.d["members"]))], [c.t("doc.f.progress"), c.pct(c.m["progress"])]]
    out = [_kv(rows)]
    if p["display_description"]:
        out.append(_p(p["display_description"]))
    return out


def sec_objectives(c: Ctx):
    return [_ul([c.t("doc.obj.deliver", **c.params), c.t(f"doc.obj.type.{c.type}"), c.t("doc.obj.time", **c.params),
                 c.t("doc.obj.budget", **c.params), c.t("doc.obj.quality")])]


def sec_scope(c: Ctx):
    return [_h2(c.t("doc.s.in_scope")), _ul([c.t(f"feature.{f}") for f in L.FEATURES[c.type]]),
            _h2(c.t("doc.s.out_scope")), _ul(i18n.tl("doc.out_scope", c.loc))]


def sec_team(c: Ctx):
    rows = []
    for m in c.d["members"]:
        mine = [t for t in c.d["tasks"] if t["assignee_id"] == m["id"] and not t["milestone"]]
        rows.append([m["name"], m["role"], c.num(len(mine)), c.num(sum(t["days"] for t in mine)), c.t(f"rolefocus.{m['role_key']}")])
    return [_table([c.t("doc.c.member"), c.t("doc.c.role"), c.t("doc.c.tasks"), c.t("doc.c.effort_days"), c.t("doc.c.responsibility")], rows, num=(2, 3))]


def sec_milestones(c: Ctx):
    rows = [[t["title"], c.date(t["end"]), c.status("done" if t["progress"] >= 100 else ("overdue" if t["end"] < c.m["today"] else "todo"))]
            for t in c.d["tasks"] if t["milestone"]]
    return [_table([c.t("doc.c.milestone"), c.t("doc.c.date"), c.t("doc.c.status")], rows, status_col=2)]


def sec_phases(c: Ctx):
    rows = []
    for ph in c.d["phases"]:
        ts = [t for t in c.d["tasks"] if t["phase_id"] == ph["id"]]
        if not ts:
            continue
        work = [t for t in ts if not t["milestone"]]
        prog = sum(t["days"] * t["progress"] for t in work) / max(1, sum(t["days"] for t in work))
        rows.append([f'{ph["seq"]}. {ph["name"]}', c.date(min(t["start"] for t in ts)), c.date(max(t["end"] for t in ts)),
                     c.num(sum(t["days"] for t in work)), c.pct(prog)])
    return [_table([c.t("doc.c.phase"), c.t("doc.c.start"), c.t("doc.c.end"), c.t("doc.c.days"), c.t("doc.c.progress")], rows, num=(3, 4))]


def sec_budget(c: Ctx, actuals=True):
    rows = []
    for b in c.d["budget"]:
        r = [b["label"], c.money(b["planned"])]
        if actuals:
            r += [c.money(b["actual"]), c.money(b["variance"])]
        rows.append(r)
    tot = [c.t("doc.c.total"), c.money(c.m["budget_planned"])]
    if actuals:
        tot += [c.money(c.m["budget_actual"]), c.money(c.m["budget_left"])]
    rows.append(tot)
    cols = [c.t("doc.c.category"), c.t("doc.c.planned")] + ([c.t("doc.c.actual"), c.t("doc.c.variance")] if actuals else [])
    return [_table(cols, rows, num=tuple(range(1, len(cols))))]


def sec_risks(c: Ctx, limit=None, with_matrix=False):
    rs = sorted(c.d["risks"], key=lambda r: -r["score"])[:limit] if limit else sorted(c.d["risks"], key=lambda r: -r["score"])
    rows = [[r["code"], r["title"], c.num(r["probability"]), c.num(r["impact"]), c.num(r["score"]), r["owner"], r["mitigation"],
             c.t(f"riskstatus.{r['status']}")] for r in rs]
    out = [_table([c.t("doc.c.id"), c.t("doc.c.risk"), c.t("doc.c.p"), c.t("doc.c.i"), c.t("doc.c.score"), c.t("doc.c.owner"),
                   c.t("doc.c.mitigation"), c.t("doc.c.status")], rows, num=(2, 3, 4), widths=[6, 22, 5, 5, 6, 12, 32, 10])]
    if with_matrix:
        out.insert(0, _chart(charts.risk_matrix(c.d["risks"], c.loc), c.t("risks.matrix_title")))
    return out


def sec_wbs(c: Ctx):
    rows = []
    for ph in c.d["phases"]:
        for t in [t for t in c.d["tasks"] if t["phase_id"] == ph["id"]]:
            rows.append([t["wbs"], ("◆ " if t["milestone"] else "") + t["title"], t["assignee"], c.date(t["start"]), c.date(t["end"]),
                         c.num(t["days"]), c.t(f"docname.{t['deliverable']}") if t["deliverable"] else ""])
    return [_table([c.t("doc.c.wbs"), c.t("doc.c.task"), c.t("doc.c.owner"), c.t("doc.c.start"), c.t("doc.c.end"),
                    c.t("doc.c.days"), c.t("doc.c.deliverable")], rows, num=(5,), widths=[7, 34, 14, 12, 12, 6, 15])]


def sec_requirements(c: Ctx):
    fr = [[f"FR-{i:02d}", c.t(f"feature.{f}"), c.t("doc.must" if i <= 3 else "doc.should")] for i, f in enumerate(L.FEATURES[c.type], 1)]
    nfr = [[f"NFR-{i:02d}", c.t(f"nfr.{n}"), c.t("doc.must")] for i, n in enumerate(L.NFRS, 1)]
    cols = [c.t("doc.c.id"), c.t("doc.c.requirement"), c.t("doc.c.priority")]
    return [_h2(c.t("doc.s.functional")), _table(cols, fr, widths=[10, 75, 15]),
            _h2(c.t("doc.s.non_functional")), _table(cols, nfr, widths=[10, 75, 15])]


def sec_components(c: Ctx):
    rows = [[c.t(f"comp.{x}.n"), c.t(f"comp.{x}.d")] for x in L.COMPONENTS[c.type]]
    return [_table([c.t("doc.c.component"), c.t("doc.c.description")], rows, widths=[30, 70])]


def sec_gantt(c: Ctx):
    return [_chart(charts.gantt(c.d, c.loc, "week"), c.t("gantt.title")), _chart(charts.gantt_legend(c.loc))]


def _ai_paragraphs(c: Ctx, doc_type: str, ids: list[str]) -> dict:
    if not ai.available():
        return {}
    return ai.doc_sections(doc_type, _context_text(c), ids, c.loc) or {}


def _narr(c: Ctx, aitext: dict, sid: str, key: str):
    return _p(aitext[sid]) if aitext.get(sid) else c.para(key)


# ---------------------------------------------------------------- documents

def build_document(doc_type: str, data: dict, loc: str, use_ai: bool = False) -> dict:
    c = Ctx(data, loc)
    aitext = _ai_paragraphs(c, doc_type, ["purpose", "background", "approach", "summary"]) if use_ai else {}
    b: list = []
    n = [0]

    def H(key):
        n[0] += 1
        b.append(_h1(f"{n[0]}. {c.t(key)}"))

    if doc_type == "documents_needed":
        H("doc.s.purpose"); b.append(c.para("doc.p.register_purpose"))
        H("doc.s.register")
        rows = []
        for i, dtp in enumerate(L.DOC_TYPES, 1):
            task = next((t for t in data["tasks"] if t["deliverable"] == dtp), None)
            st = "todo"
            if task:
                st = "done" if task["progress"] >= 100 else ("overdue" if task["end"] < c.m["today"] else ("in_progress" if task["progress"] else "todo"))
            rows.append([c.num(i), c.t(f"docname.{dtp}"), c.t(f"docpurpose.{dtp}"), task["assignee"] if task else (c.pm["name"] if c.pm else "—"),
                         c.date(task["end"]) if task else c.date(c.p["start_date"]), c.status(st), c.t("doc.formats")])
        b.append(_table([c.t("doc.c.no"), c.t("doc.c.document"), c.t("doc.c.purpose"), c.t("doc.c.owner"), c.t("doc.c.due"),
                         c.t("doc.c.status"), c.t("doc.c.format")], rows, num=(0,), widths=[4, 20, 32, 13, 12, 10, 9], status_col=5))
        H("doc.s.rules"); b.append(_ul(i18n.tl("doc.register_rules", loc)))
    elif doc_type == "charter":
        H("doc.s.purpose"); b.append(_narr(c, aitext, "purpose", "doc.p.charter_purpose"))
        H("doc.s.overview"); b += sec_overview(c)
        H("doc.s.objectives"); b += sec_objectives(c)
        H("doc.s.scope"); b += sec_scope(c)
        H("doc.s.milestones"); b += sec_milestones(c)
        H("doc.s.budget"); b += sec_budget(c, actuals=False)
        H("doc.s.team"); b += sec_team(c)
        H("doc.s.key_risks"); b += sec_risks(c, limit=5)
        H("doc.s.approvals"); b.append(_sign(c))
    elif doc_type == "proposal":
        H("doc.s.summary"); b.append(_narr(c, aitext, "summary", "doc.p.proposal_summary"))
        H("doc.s.problem"); b.append(_narr(c, aitext, "background", f"doc.p.problem.{c.type}"))
        H("doc.s.solution"); b.append(_narr(c, aitext, "approach", "doc.p.solution")); b.append(_ul([c.t(f"feature.{f}") for f in L.FEATURES[c.type]]))
        H("doc.s.benefits"); b.append(_ul(i18n.tl(f"doc.benefits.{c.type}", loc)))
        H("doc.s.timeline"); b += sec_phases(c)
        H("doc.s.budget"); b += sec_budget(c, actuals=False)
        H("doc.s.team"); b += sec_team(c)
        H("doc.s.next_steps"); b.append(_ul(i18n.tl("doc.proposal_next", loc)))
    elif doc_type == "brd":
        H("doc.s.purpose"); b.append(c.para("doc.p.brd_purpose"))
        H("doc.s.background"); b.append(_narr(c, aitext, "background", f"doc.p.problem.{c.type}"))
        H("doc.s.objectives"); b += sec_objectives(c)
        H("doc.s.stakeholders"); b += sec_team(c)
        H("doc.s.business_req")
        rows = [[f"BR-{i:02d}", c.t(f"feature.{f}"), c.t("doc.must" if i <= 3 else "doc.should")] for i, f in enumerate(L.FEATURES[c.type], 1)]
        b.append(_table([c.t("doc.c.id"), c.t("doc.c.requirement"), c.t("doc.c.priority")], rows, widths=[10, 75, 15]))
        H("doc.s.scope"); b += sec_scope(c)
        H("doc.s.assumptions"); b.append(_ul(i18n.tl("doc.assumptions", loc)))
        H("doc.s.approvals"); b.append(_sign(c))
    elif doc_type == "srs":
        H("doc.s.introduction"); b.append(c.para("doc.p.srs_intro"))
        H("doc.s.overall"); b.append(_narr(c, aitext, "approach", "doc.p.srs_overall"))
        H("doc.s.requirements"); b += sec_requirements(c)
        H("doc.s.interfaces"); b += sec_components(c)
        H("doc.s.acceptance"); b.append(_ul([c.t("doc.accept_pattern", feature=c.t(f"feature.{f}")) for f in L.FEATURES[c.type]]))
        H("doc.s.traceability")
        rows = [[f"FR-{i:02d}", c.t(f"comp.{L.COMPONENTS[c.type][(i - 1) % 4]}.n"), f"TC-{i:02d}"] for i in range(1, 6)]
        b.append(_table([c.t("doc.c.requirement"), c.t("doc.c.component"), c.t("doc.c.test")], rows))
    elif doc_type == "tdd":
        H("doc.s.introduction"); b.append(c.para("doc.p.tdd_intro"))
        H("doc.s.design_overview"); b.append(_narr(c, aitext, "approach", f"doc.p.design.{c.type}"))
        H("doc.s.components"); b += sec_components(c)
        H("doc.s.data_design"); b.append(_ul(i18n.tl("doc.data_design", loc)))
        H("doc.s.security_design"); b.append(_ul(i18n.tl("doc.security_design", loc)))
        H("doc.s.tech_decisions"); b.append(_table([c.t("doc.c.decision"), c.t("doc.c.reason")],
                                                   [[c.t(f"decision.{c.type}.{k}.n"), c.t(f"decision.{c.type}.{k}.r")] for k in ("1", "2", "3")]))
        H("doc.s.nfr"); b.append(_ul([c.t(f"nfr.{x}") for x in L.NFRS]))
    elif doc_type == "architecture":
        H("doc.s.introduction"); b.append(c.para("doc.p.sad_intro"))
        H("doc.s.context"); b.append(_narr(c, aitext, "approach", f"doc.p.design.{c.type}"))
        H("doc.s.components"); b += sec_components(c)
        H("doc.s.diagram"); b.append(_chart(_arch_svg(c), c.t("doc.s.diagram")))
        H("doc.s.deployment_view"); b.append(_ul(i18n.tl("doc.deployment_view", loc)))
        H("doc.s.quality"); b.append(_ul([c.t(f"nfr.{x}") for x in L.NFRS]))
    elif doc_type == "wbs":
        H("doc.s.purpose"); b.append(c.para("doc.p.wbs_purpose"))
        H("doc.s.phases"); b += sec_phases(c)
        H("doc.s.wbs"); b += sec_wbs(c)
        H("doc.s.schedule"); b += sec_gantt(c)
    elif doc_type == "implementation_plan":
        H("doc.s.purpose"); b.append(c.para("doc.p.impl_purpose"))
        H("doc.s.approach"); b.append(_narr(c, aitext, "approach", "doc.p.impl_approach"))
        H("doc.s.phases"); b += sec_phases(c)
        H("doc.s.milestones"); b += sec_milestones(c)
        H("doc.s.resources"); b += sec_team(c)
        H("doc.s.schedule"); b += sec_gantt(c)
        H("doc.s.communication"); b.append(_ul(i18n.tl("doc.communication", loc)))
    elif doc_type == "test_plan":
        H("doc.s.purpose"); b.append(c.para("doc.p.test_purpose"))
        H("doc.s.test_types"); b.append(_table([c.t("doc.c.test_type"), c.t("doc.c.description")],
                                              [[c.t(f"testtype.{k}.n"), c.t(f"testtype.{k}.d")] for k in ("unit", "integration", "system", "security", "performance", "uat")]))
        H("doc.s.test_cases")
        rows = [[f"TC-{i:02d}", c.t("doc.test_pattern", feature=c.t(f"feature.{f}")), c.t("doc.expected_pass"), f"FR-{i:02d}"] for i, f in enumerate(L.FEATURES[c.type], 1)]
        b.append(_table([c.t("doc.c.id"), c.t("doc.c.test"), c.t("doc.c.expected"), c.t("doc.c.requirement")], rows, widths=[8, 52, 28, 12]))
        H("doc.s.entry_exit"); b.append(_ul(i18n.tl("doc.entry_exit", loc)))
        H("doc.s.schedule"); b.append(_table([c.t("doc.c.task"), c.t("doc.c.owner"), c.t("doc.c.start"), c.t("doc.c.end")],
                                             [[t["title"], t["assignee"], c.date(t["start"]), c.date(t["end"])] for t in data["tasks"] if t["phase_code"] == "testing" and not t["milestone"]]))
    elif doc_type == "risk_assessment":
        H("doc.s.purpose"); b.append(c.para("doc.p.risk_purpose"))
        H("doc.s.method"); b.append(_ul(i18n.tl("doc.risk_method", loc)))
        H("doc.s.matrix"); b.append(_chart(charts.risk_matrix(data["risks"], loc), c.t("risks.matrix_title")))
        H("doc.s.register"); b += sec_risks(c)
        H("doc.s.monitoring"); b.append(_ul(i18n.tl("doc.risk_monitoring", loc)))
    elif doc_type == "deployment_guide":
        H("doc.s.purpose"); b.append(c.para("doc.p.deploy_purpose"))
        H("doc.s.prerequisites"); b.append(_ul(i18n.tl("doc.deploy_prereq", loc)))
        H("doc.s.steps"); b.append(_ol(i18n.tl("doc.deploy_steps", loc)))
        H("doc.s.verification"); b.append(_ul(i18n.tl("doc.deploy_verify", loc)))
        H("doc.s.rollback"); b.append(_ol(i18n.tl("doc.deploy_rollback", loc)))
        H("doc.s.contacts"); b += sec_team(c)
    elif doc_type == "user_manual":
        H("doc.s.introduction"); b.append(c.para("doc.p.manual_intro"))
        H("doc.s.getting_started"); b.append(_ol(i18n.tl("doc.manual_start", loc)))
        H("doc.s.features")
        for f in L.FEATURES[c.type]:
            b.append(_h2(c.t(f"feature.{f}")))
            b.append(_p(c.t("doc.manual_feature_how")))
        H("doc.s.faq"); b.append(_ul(i18n.tl("doc.manual_faq", loc)))
        H("doc.s.support"); b.append(c.para("doc.p.manual_support"))
    elif doc_type == "closure_report":
        H("doc.s.summary"); b.append(_narr(c, aitext, "summary", "doc.p.closure_summary"))
        H("doc.s.results"); b.append(_kv([[c.t("doc.f.progress"), c.pct(c.m["progress"])],
                                          [c.t("doc.f.tasks_done"), f'{c.num(c.m["tasks_done"])} / {c.num(c.m["tasks_total"])}'],
                                          [c.t("doc.f.budget_used"), f'{c.money(c.m["budget_actual"])} / {c.money(c.m["budget_planned"])}'],
                                          [c.t("doc.f.end"), c.date(c.p["end_date"])]]))
        H("doc.s.milestones"); b += sec_milestones(c)
        H("doc.s.budget"); b += sec_budget(c)
        H("doc.s.lessons"); b.append(_ul(i18n.tl("doc.lessons", loc)))
        H("doc.s.handover"); b.append(_ul(i18n.tl("doc.handover", loc)))
        H("doc.s.approvals"); b.append(_sign(c))
    else:
        raise ValueError(doc_type)
    return _wrap(c, "doc", doc_type, c.t(f"docname.{doc_type}"), b, "ai" if aitext else "template")


def _sign(c: Ctx):
    rows = [[c.t("role.sponsor"), "", "", ""], [c.t("role.project_manager"), c.pm["name"] if c.pm else "", "", ""]]
    return _table([c.t("doc.c.role"), c.t("doc.c.name"), c.t("doc.c.signature"), c.t("doc.c.date")], rows, widths=[25, 30, 25, 20])


def _arch_svg(c: Ctx) -> str:
    comps = [c.t(f"comp.{x}.n") for x in L.COMPONENTS[c.type]]
    users = c.t("doc.users")
    from .charts import _svg, _e, C as K
    W, H = 760, 230
    rtl = i18n.info(c.loc)["dir"] == "rtl"
    boxes = [(20, 90, users)] + [(170 + i * 145, 90, comps[i]) for i in range(4)]
    if rtl:
        boxes = [(W - x - 130, y, s) for x, y, s in boxes]
    out = []
    for i, (x, y, s) in enumerate(boxes):
        fill = K["soft"] if i == 0 else "#eef0ff"
        out.append(f'<rect x="{x}" y="{y}" width="130" height="56" rx="8" fill="{fill}" stroke="{K["brand"]}" stroke-width="1.2"/>')
        out.append(f'<foreignObject x="{x + 4}" y="{y + 4}" width="122" height="48"><div xmlns="http://www.w3.org/1999/xhtml" '
                   f'style="font-size:11.5px;line-height:1.25;display:flex;align-items:center;justify-content:center;height:48px;text-align:center;color:#1f2937">{_e(s)}</div></foreignObject>')
        if i:
            px_ = boxes[i - 1][0] + (130 if not rtl else 0)
            nx = x + (0 if not rtl else 130)
            out.append(f'<line x1="{px_}" y1="{y + 28}" x2="{nx}" y2="{y + 28}" stroke="{K["muted"]}" stroke-width="1.4"/>')
    out.append(f'<text x="{W / 2}" y="40" font-size="13" font-weight="700" text-anchor="middle" fill="{K["ink"]}">{_e(c.p["display_name"])}</text>')
    return _svg(W, H, "".join(out), c.loc, c.t("doc.s.diagram"))


# ---------------------------------------------------------------- reports

def progress_at(pid: int, upto: dt.datetime) -> dict[int, int]:
    rows = db.q("SELECT task_id, new_value FROM activity WHERE project_id=? AND kind='progress' AND at<=? ORDER BY at",
                (pid, upto.isoformat()))
    out: dict[int, int] = {}
    for r in rows:
        out[r["task_id"]] = int(r["new_value"])
    return out


def build_report(kind: str, data: dict, loc: str, on: dt.date | None = None) -> dict:
    c = Ctx(data, loc)
    on = on or db.today()
    pid = c.p["id"]
    m = c.m
    b: list = []
    tasks = [t for t in data["tasks"] if not t["milestone"]]

    def updates_between(a: dt.datetime, z: dt.datetime):
        rows = db.q("SELECT * FROM activity WHERE project_id=? AND kind='progress' AND at>? AND at<=? ORDER BY at", (pid, a.isoformat(), z.isoformat()))
        by = {t["id"]: t for t in data["tasks"]}
        first, last = {}, {}
        for r in rows:
            first.setdefault(r["task_id"], int(r["old_value"] or 0))
            last[r["task_id"]] = int(r["new_value"])
        return [(by[k], first[k], last[k]) for k in last if k in by]

    def bar_rows(ts):
        return [dict(label=f'{t["wbs"]} {t["title"]}', progress=t["progress"], days=t["days"],
                     overdue=t["end"] < m["today"] and t["progress"] < 100) for t in ts]

    summary = _kv([[c.t("report.k.progress"), f'{c.pct(m["progress"])} ({c.t("report.k.planned_short")} {c.pct(m["planned"])})'],
                   [c.t("report.k.health"), c.t(f"health.{m['health']}")],
                   [c.t("report.k.tasks"), c.t("report.k.tasks_v", done=c.num(m["tasks_done"]), total=c.num(m["tasks_total"]), active=c.num(m["tasks_active"]))],
                   [c.t("report.k.overdue"), c.num(len(m["overdue"]))],
                   [c.t("report.k.budget_used"), f'{c.money(m["budget_actual"])} / {c.money(m["budget_planned"])}']])
    if kind == "daily":
        title = c.t("reportname.daily")
        sub = c.date(on)
        b += [_h1(c.t("report.s.summary")), summary]
        start = dt.datetime.combine(on, dt.time.min)
        ups = updates_between(start - dt.timedelta(seconds=1), dt.datetime.combine(on, dt.time.max))
        b.append(_h1(c.t("report.s.updated_today")))
        b.append(_table([c.t("doc.c.task"), c.t("doc.c.owner"), c.t("report.c.before"), c.t("report.c.now")],
                        [[t["title"], t["assignee"], c.pct(a), c.pct(z)] for t, a, z in ups], num=(2, 3))
                 if ups else _note(c.t("report.none_today")))
        active = [t for t in tasks if t["start"] <= on and t["progress"] < 100]
        b.append(_h1(c.t("report.s.active_progress")))
        b.append(_chart(charts.progress_bars(bar_rows(active), loc), c.t("progress.title")) if active else _note(c.t("report.none")))
        due = [t for t in tasks if on <= t["end"] <= add_workdays(on, 1) and t["progress"] < 100]
        b.append(_h1(c.t("report.s.due_soon")))
        b.append(_table([c.t("doc.c.task"), c.t("doc.c.owner"), c.t("doc.c.end"), c.t("doc.c.progress")],
                        [[t["title"], t["assignee"], c.date(t["end"]), c.pct(t["progress"])] for t in due], num=(3,)) if due else _note(c.t("report.none")))
        blocked = [t for t in tasks if t["status"] == "blocked"]
        b.append(_h1(c.t("report.s.blocked")))
        b.append(_ul([f'{t["title"]} — {t["assignee"]}' for t in blocked]) if blocked else _note(c.t("report.none")))
    elif kind == "weekly":
        ws = on - dt.timedelta(days=on.weekday())
        we = ws + dt.timedelta(days=6)
        title = c.t("reportname.weekly")
        sub = f"{c.date(ws)} – {c.date(we)}"
        before = progress_at(pid, dt.datetime.combine(ws, dt.time.min) - dt.timedelta(seconds=1))
        w = {t["id"]: t["days"] for t in tasks}
        prog_before = sum(w[k] * v for k, v in before.items() if k in w) / max(1, sum(w.values()))
        b += [_h1(c.t("report.s.summary")), summary,
              _p(c.t("report.p.week_gain", before=c.pct(prog_before), now=c.pct(m["progress"]), gain=c.pct(m["progress"] - prog_before)))]
        ups = updates_between(dt.datetime.combine(ws, dt.time.min) - dt.timedelta(seconds=1), dt.datetime.combine(we, dt.time.max))
        done = [(t, a, z) for t, a, z in ups if z >= 100]
        b.append(_h1(c.t("report.s.completed_week")))
        b.append(_ul([f'{t["title"]} — {t["assignee"]}' for t, _, _ in done]) if done else _note(c.t("report.none")))
        b.append(_h1(c.t("report.s.progress_week")))
        b.append(_table([c.t("doc.c.task"), c.t("doc.c.owner"), c.t("report.c.before"), c.t("report.c.now")],
                        [[t["title"], t["assignee"], c.pct(a), c.pct(z)] for t, a, z in ups], num=(2, 3)) if ups else _note(c.t("report.none")))
        hist = A.history(pid, data)
        b.append(_h1(c.t("progress.burndown_title"))); b.append(_chart(charts.burndown(hist, m["days_total"], loc), c.t("progress.burndown_help")))
        b.append(_h1(c.t("report.s.plan_vs_actual"))); b.append(_chart(charts.scurve(hist, loc), c.t("progress.scurve_title")))
        nws, nwe = we + dt.timedelta(days=1), we + dt.timedelta(days=7)
        nxt = [t for t in tasks if t["start"] <= nwe and t["end"] >= nws and t["progress"] < 100]
        b.append(_h1(c.t("report.s.next_week")))
        b.append(_table([c.t("doc.c.task"), c.t("doc.c.owner"), c.t("doc.c.start"), c.t("doc.c.end")],
                        [[t["title"], t["assignee"], c.date(t["start"]), c.date(t["end"])] for t in nxt]) if nxt else _note(c.t("report.none")))
        b.append(_h1(c.t("report.s.top_risks"))); b += sec_risks(c, limit=5)
    elif kind == "monthly":
        ms = on.replace(day=1)
        title = c.t("reportname.monthly")
        sub = i18n.month_label(ms, loc)
        b += [_h1(c.t("report.s.executive")), _p(c.t(f"report.p.health_{m['health']}", **c.params)), summary]
        b.append(_h1(c.t("report.s.kpis")))
        b.append(_table([c.t("report.c.kpi"), c.t("report.c.value"), c.t("report.c.meaning")], _kpi_rows(c), widths=[22, 18, 60]))
        b.append(_h1(c.t("report.s.plan_vs_actual"))); b.append(_chart(charts.scurve(A.history(pid, data), loc), c.t("progress.scurve_title")))
        b.append(_h1(c.t("doc.s.milestones"))); b += sec_milestones(c)
        b.append(_h1(c.t("doc.s.budget"))); b.append(_chart(charts.budget_bars(data["budget"], c.cur, loc), c.t("budget.chart_title")))
        b.append(_h1(c.t("report.s.top_risks"))); b += sec_risks(c, limit=5)
        nm = (ms + dt.timedelta(days=32)).replace(day=1)
        nme = (nm + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        focus = [t for t in data["tasks"] if t["start"] <= nme and t["end"] >= nm and t["progress"] < 100]
        b.append(_h1(c.t("report.s.next_month")))
        b.append(_ul([f'{t["title"]} ({c.date(t["end"])})' for t in focus[:12]]) if focus else _note(c.t("report.none")))
    elif kind == "missed":
        title = c.t("reportname.missed")
        sub = c.date(on)
        od = sorted(m["overdue"], key=lambda t: t["end"])
        b.append(_h1(c.t("report.s.summary")))
        b.append(_p(c.t("report.p.missed", n=len(od), days=c.num(sum(t["left_days"] for t in od), 1))))
        b.append(_h1(c.t("report.s.missed_list")))
        rows = [[t["wbs"], t["title"], t["assignee"], c.date(t["end"]), c.num(max(0, workdays_between(t["end"], on) - 1)),
                 c.pct(t["progress"]), c.num(t["left_days"], 1)] for t in od]
        b.append(_table([c.t("doc.c.wbs"), c.t("doc.c.task"), c.t("doc.c.owner"), c.t("report.c.due"), c.t("report.c.days_late"),
                         c.t("doc.c.progress"), c.t("report.c.days_left")], rows, num=(4, 5, 6)) if od else _note(c.t("report.none_missed")))
        if od:
            b.append(_chart(charts.progress_bars(bar_rows(od), loc), c.t("progress.title")))
        b.append(_h1(c.t("report.s.actions"))); b.append(_ul(i18n.tl("report.missed_actions", loc)))
    elif kind == "budget":
        title = c.t("reportname.budget")
        sub = c.date(on)
        b.append(_h1(c.t("report.s.summary")))
        b.append(_p(c.t("report.p.budget", used=c.money(m["budget_actual"]), total=c.money(m["budget_planned"]),
                        pct=c.pct(100 * m["budget_actual"] / max(1, m["budget_planned"])), progress=c.pct(m["progress"]))))
        b.append(_chart(charts.budget_bars(data["budget"], c.cur, loc), c.t("budget.chart_title")))
        b.append(_h1(c.t("report.s.by_category"))); b += sec_budget(c)
        b.append(_h1(c.t("report.s.earned_value")))
        b.append(_table([c.t("report.c.kpi"), c.t("report.c.value"), c.t("report.c.meaning")], _kpi_rows(c), widths=[22, 18, 60]))
    elif kind == "risk":
        title = c.t("reportname.risk")
        sub = c.date(on)
        b.append(_h1(c.t("report.s.summary")))
        b.append(_p(c.t("report.p.risk", open=c.num(m["open_risks"]), high=c.num(m["high_risks"]), total=c.num(len(data["risks"])))))
        b.append(_h1(c.t("doc.s.register"))); b += sec_risks(c, with_matrix=True)
    elif kind == "workload":
        title = c.t("reportname.workload")
        sub = c.date(on)
        wl = A.workload(data)
        b.append(_h1(c.t("report.s.summary"))); b.append(c.para("report.p.workload"))
        b.append(_chart(charts.workload_heatmap(wl, loc), c.t("team.heatmap_title")))
        rows = [[r["member"]["name"], r["member"]["role"], c.num(r["total"]), c.num(r["peak"]), c.num(sum(r["over"]))] for r in wl["rows"]]
        b.append(_h1(c.t("report.s.by_member")))
        b.append(_table([c.t("doc.c.member"), c.t("doc.c.role"), c.t("report.c.hours"), c.t("report.c.peak"), c.t("report.c.over_weeks")], rows, num=(2, 3, 4)))
    else:
        raise ValueError(kind)
    out = _wrap(c, "report", kind, title, b, "template")
    out["subtitle"] = sub
    return out


def _kpi_rows(c: Ctx):
    m = c.m
    f = lambda v: c.num(v, 2) if v is not None else "—"
    return [[c.t("kpi.pv"), c.money(m["pv"]), c.t("kpi.pv_d")], [c.t("kpi.ev"), c.money(m["ev"]), c.t("kpi.ev_d")],
            [c.t("kpi.ac"), c.money(m["budget_actual"]), c.t("kpi.ac_d")], [c.t("kpi.spi"), f(m["spi"]), c.t("kpi.spi_d")],
            [c.t("kpi.cpi"), f(m["cpi"]), c.t("kpi.cpi_d")],
            [c.t("kpi.eac"), c.money(m["eac"]) if m["eac"] else "—", c.t("kpi.eac_d")]]


def _wrap(c: Ctx, kind, doc_type, title, blocks, engine):
    return {"kind": kind, "type": doc_type, "locale": c.loc, "dir": i18n.info(c.loc)["dir"], "title": title,
            "project": c.p["display_name"], "project_id": c.p["id"], "subtitle": "",
            "meta": [[c.t("doc.f.project"), c.p["display_name"]], [c.t("doc.f.prepared_by"), c.pm["name"] if c.pm else "—"],
                     [c.t("doc.f.date"), c.date(db.today())], [c.t("doc.f.language"), i18n.LOCALES[c.loc]["name"]]],
            "engine": engine, "blocks": blocks, "generated": db.now()}


# ---------------------------------------------------------------- storage

def save(pid: int, uid: int, content: dict) -> int:
    v = db.q1("SELECT COALESCE(MAX(version),0) v FROM documents WHERE project_id=? AND kind=? AND doc_type=? AND locale=?",
              (pid, content["kind"], content["type"], content["locale"]))["v"] + 1
    content["version"] = v
    content["meta"].append([i18n.t("doc.f.version", content["locale"]), f"v{v}"])
    return db.ex("INSERT INTO documents(project_id,kind,doc_type,locale,version,engine,content_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                 (pid, content["kind"], content["type"], content["locale"], v, content["engine"], json.dumps(content, ensure_ascii=False), uid, db.now()))


def get(doc_id: int) -> tuple[dict, dict] | None:
    r = db.q1("SELECT * FROM documents WHERE id=?", (doc_id,))
    if not r:
        return None
    return dict(r), json.loads(r["content_json"])
