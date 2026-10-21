"""Turns the user's prompt + popup answers into a full project: phases, tasks with
dependencies and dates, resources, budget, risks. Works from the template library,
or from a plan returned by the Claude API (same structure, free text instead of keys).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re

from . import db, library as L

# ---------------------------------------------------------------- working days

def is_workday(d: dt.date) -> bool:
    return d.weekday() < 5


def next_workday(d: dt.date) -> dt.date:
    while not is_workday(d):
        d += dt.timedelta(days=1)
    return d


def add_workdays(d: dt.date, n: int) -> dt.date:
    """Date that is n working days after d (n=0 -> d itself, moved to a workday)."""
    d = next_workday(d)
    while n > 0:
        d += dt.timedelta(days=1)
        if is_workday(d):
            n -= 1
    return d


def workdays_between(a: dt.date, b: dt.date) -> int:
    """Inclusive count of working days from a to b."""
    if b < a:
        return 0
    n, d = 0, a
    while d <= b:
        if is_workday(d):
            n += 1
        d += dt.timedelta(days=1)
    return n


# ---------------------------------------------------------------- type detection

def detect_type(text: str) -> str:
    low = f" {text.lower()} "
    scores = {}
    for typ, words in L.TYPE_KEYWORDS.items():
        scores[typ] = sum(1 for w in words if w in low)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "generic"


def split_name(prompt: str) -> tuple[str, str]:
    """First line / sentence = name, rest = description."""
    prompt = (prompt or "").strip()
    if not prompt:
        return "", ""
    first, _, rest = prompt.partition("\n")
    if not rest:
        m = re.match(r"^(.{3,80}?)[\.:—–\-|]\s+(.+)$", first)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        if len(first) > 70:
            words = first.split()
            return " ".join(words[:7]), first
    return first.strip()[:120], rest.strip()


# ---------------------------------------------------------------- template plan

def template_plan(ptype: str) -> dict:
    ptype = ptype if ptype in L.TYPE_TASKS else "generic"
    extra = L.TYPE_TASKS[ptype]
    tasks = []
    by_phase = {"design": [], "build": [], "testing": []}
    for code, phase, days, role in extra:
        by_phase[phase].append(code)

    for code, phase, days, role, deps, deliv, prio in L.COMMON_TASKS:
        deps2 = []
        for dname in deps:
            if dname.startswith("@"):
                deps2.extend(by_phase[dname[1:]])
            else:
                deps2.append(dname)
        tasks.append(dict(code=code, phase=phase, days=days, role=role, deps=deps2, deliverable=deliv,
                          priority=prio, milestone=days == 0,
                          title_key=f"task.{code}.t", desc_key=None if days == 0 else f"task.{code}.d",
                          title_text="", desc_text=""))
        # insert type tasks right after their anchors
        if code == "test_strategy":
            for c in by_phase["design"]:
                tasks.append(_type_task(ptype, c, ["architecture"]))
        if code == "env_setup":
            prev = "env_setup"
            for c in by_phase["build"]:
                tasks.append(_type_task(ptype, c, [prev] if c != by_phase["build"][-1] else ["env_setup"]))
                prev = c
        if code == "functional_tests":
            for c in by_phase["testing"]:
                tasks.append(_type_task(ptype, c, ["ms_build"]))
    risks = [dict(code=c, category=cat, p=p, i=i, owner_role=o, title_key=f"risk.{c}.t", mitigation_key=f"risk.{c}.m",
                  title_text="", mitigation_text="") for c, cat, p, i, o in L.COMMON_RISKS + L.TYPE_RISKS[ptype]]
    budget = [dict(category=c, pct=p) for c, p in zip(L.BUDGET_CATEGORIES, L.BUDGET_SPLIT[ptype])]
    return dict(type=ptype, engine="template",
                phases=[dict(code=p, name_key=f"phase.{p}", name_text="") for p in L.PHASES],
                tasks=tasks, risks=risks, budget=budget)


def _type_task(ptype, code, deps):
    spec = {c: (ph, d, r) for c, ph, d, r in L.TYPE_TASKS[ptype]}[code]
    return dict(code=code, phase=spec[0], days=spec[1], role=spec[2], deps=deps, deliverable=None,
                priority="high", milestone=False, title_key=f"task.{code}.t", desc_key=f"task.{code}.d",
                title_text="", desc_text="")


# ---------------------------------------------------------------- scheduling

def _topo(tasks: list[dict]) -> list[dict]:
    by = {t["code"]: t for t in tasks}
    seen, out = set(), []

    def visit(t, stack=()):
        if t["code"] in seen:
            return
        if t["code"] in stack:
            raise ValueError("dependency cycle at " + t["code"])
        for d in t["deps"]:
            if d in by:
                visit(by[d], stack + (t["code"],))
        seen.add(t["code"])
        out.append(t)

    for t in tasks:
        visit(t)
    return out


def _schedule(tasks, start: dt.date):
    by = {t["code"]: t for t in tasks}
    for t in _topo(tasks):
        preds = [by[d] for d in t["deps"] if d in by]
        if t["milestone"]:
            t["start"] = t["end"] = max((p["end"] for p in preds), default=next_workday(start))
            continue
        s = add_workdays(max(p["end"] for p in preds), 1) if preds else next_workday(start)
        if preds and all(p["milestone"] for p in preds) and max(p["end"] for p in preds) < next_workday(start):
            s = next_workday(start)
        t["start"] = s
        t["end"] = add_workdays(s, t["days"] - 1)
    return max(t["end"] for t in tasks)


def schedule(plan: dict, start: dt.date, duration_weeks: int) -> None:
    tasks = plan["tasks"]
    base = {t["code"]: t["days"] for t in tasks}
    for t in tasks:
        t["deps"] = [d for d in t["deps"] if d in base and d != t["code"]]
    end = _schedule(tasks, start)
    current = workdays_between(next_workday(start), end)
    target = max(10, duration_weeks * 5)
    factor = target / max(current, 1)
    for _ in range(6):
        for t in tasks:
            if not t["milestone"]:
                t["days"] = max(1, round(base[t["code"]] * factor))
        end = _schedule(tasks, start)
        current = workdays_between(next_workday(start), end)
        if abs(current - target) <= max(1, target * 0.03):
            break
        factor *= target / max(current, 1)


# ---------------------------------------------------------------- resources

def suggested_roles(ptype: str, size: int) -> list[str]:
    order = L.TEAM_ORDER.get(ptype, L.TEAM_ORDER["generic"])
    return [order[i] if i < len(order) else order[2 + (i % (len(order) - 2))] for i in range(max(1, size))]


def build_team(ptype: str, size: int, names: list[str], roles: list[str] | None = None) -> list[dict]:
    """Team from the popup answers: names and roles typed by the user; missing roles are suggested by project type."""
    size = max(1, min(int(size or 1), 40))
    sugg = suggested_roles(ptype, size)
    roles = roles or []
    members = []
    for i in range(size):
        role = roles[i] if i < len(roles) and roles[i] in L.ROLES else sugg[i]
        name = names[i].strip() if i < len(names) and names[i] and names[i].strip() else ""
        members.append(dict(seq=i + 1, role=role, name=name))
    # if nobody is project manager, PM tasks fall back to the tech lead / developer (see ROLE_FALLBACK)
    return members


def assign(plan: dict, members: list[dict]) -> None:
    load = {m["seq"]: 0 for m in members}
    by_role: dict[str, list] = {}
    for m in members:
        by_role.setdefault(m["role"], []).append(m)
    for t in plan["tasks"]:
        role = t["role"]
        cands = by_role.get(role)
        if not cands:
            for alt in L.ROLE_FALLBACK.get(role, []):
                if by_role.get(alt):
                    cands = by_role[alt]
                    break
        cands = cands or members
        m = min(cands, key=lambda x: load[x["seq"]])
        t["member_seq"] = m["seq"]
        load[m["seq"]] += t["days"]


# ---------------------------------------------------------------- persistence

def to_minor(amount: float, currency: str) -> int:
    return int(round(float(amount) * (1 if currency == "JPY" else 100)))


def create_project(owner_id: int, answers: dict, plan: dict | None = None) -> int:
    ptype = answers.get("type") or "generic"
    plan = plan or template_plan(ptype)
    start = dt.date.fromisoformat(answers["start_date"])
    weeks = int(answers.get("duration_weeks") or 8)
    schedule(plan, start, weeks)
    members = build_team(ptype, answers.get("team_size", 3), answers.get("member_names") or [], answers.get("member_roles"))
    assign(plan, members)
    currency = answers.get("currency", "EUR")
    budget_minor = to_minor(answers.get("budget") or 0, currency)
    end = max(t["end"] for t in plan["tasks"])
    now = db.now()

    pid = db.ex("""INSERT INTO projects(owner_id,name,name_key,description,description_key,project_type,canonical_locale,currency,
                   budget_minor,team_size,start_date,end_date,engine,answers_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (owner_id, answers.get("name") or "", answers.get("name_key"), answers.get("description") or "",
                 answers.get("description_key"), ptype, answers.get("locale", "en"), currency, budget_minor,
                 len(members), next_workday(start).isoformat(), end.isoformat(), plan.get("engine", "template"),
                 json.dumps(answers, ensure_ascii=False), now))

    # members with daily rates scaled so personnel cost matches the personnel budget line
    pers_pct = next((b["pct"] for b in plan["budget"] if b["category"] == "personnel"), 60)
    pers_budget = budget_minor * pers_pct / 100
    days_by_member = {m["seq"]: 0 for m in members}
    for t in plan["tasks"]:
        days_by_member[t["member_seq"]] += t["days"]
    weighted = sum(days_by_member[m["seq"]] * L.DAILY_RATE_EUR.get(m["role"], 450) for m in members) or 1
    scale = pers_budget / weighted
    mid = {}
    for m in members:
        rate = int(round(L.DAILY_RATE_EUR.get(m["role"], 450) * scale))
        mid[m["seq"]] = db.ex("INSERT INTO members(project_id,seq,name,role_key,daily_rate_minor) VALUES(?,?,?,?,?)",
                              (pid, m["seq"], m["name"], L.ROLES.get(m["role"], m["role"]), rate))
        m["id"] = mid[m["seq"]]

    phase_id = {}
    for i, ph in enumerate(plan["phases"], 1):
        phase_id[ph["code"]] = db.ex("INSERT INTO phases(project_id,seq,code,name_key,name_text) VALUES(?,?,?,?,?)",
                                     (pid, i, ph["code"], ph.get("name_key"), ph.get("name_text", "")))
    task_id = {}
    counters: dict[str, int] = {}
    phase_seq = {ph["code"]: i for i, ph in enumerate(plan["phases"], 1)}
    for i, t in enumerate(sorted(plan["tasks"], key=lambda x: (phase_seq.get(x["phase"], 99), x["start"], x["end"])), 1):
        counters[t["phase"]] = counters.get(t["phase"], 0) + 1
        wbs = f"{phase_seq.get(t['phase'], 9)}.{counters[t['phase']]}"
        task_id[t["code"]] = db.ex(
            """INSERT INTO tasks(project_id,phase_id,seq,wbs,code,title_key,title_text,desc_key,desc_text,start_date,end_date,
               duration_days,progress,status,priority,assignee_id,is_milestone,effort_hours,deliverable,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,'todo',?,?,?,?,?,?)""",
            (pid, phase_id[t["phase"]], i, wbs, t["code"], t.get("title_key"), t.get("title_text", ""), t.get("desc_key"),
             t.get("desc_text", ""), t["start"].isoformat(), t["end"].isoformat(), 0 if t["milestone"] else t["days"],
             t["priority"], mid[t["member_seq"]], 1 if t["milestone"] else 0, 0 if t["milestone"] else t["days"] * 8,
             t.get("deliverable"), now))
    for t in plan["tasks"]:
        for d in t["deps"]:
            db.ex("INSERT INTO dependencies(project_id,pred_id,succ_id) VALUES(?,?,?)", (pid, task_id[d], task_id[t["code"]]))

    role_member = {}
    for m in members:
        role_member.setdefault(m["role"], m["id"])
    for i, r in enumerate(plan["risks"], 1):
        owner = role_member.get(r.get("owner_role")) or role_member.get("pm") or members[0]["id"]
        db.ex("""INSERT INTO risks(project_id,seq,code,title_key,title_text,mitigation_key,mitigation_text,category,probability,impact,owner_id)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (pid, i, f"R{i:02d}", r.get("title_key"), r.get("title_text", ""), r.get("mitigation_key"),
               r.get("mitigation_text", ""), r.get("category", "technical"), int(r["p"]), int(r["i"]), owner))
    total_pct = sum(b["pct"] for b in plan["budget"]) or 100
    allocated = 0
    for i, b in enumerate(plan["budget"], 1):
        amt = int(round(budget_minor * b["pct"] / total_pct)) if i < len(plan["budget"]) else budget_minor - allocated
        allocated += amt
        db.ex("INSERT INTO budget_lines(project_id,seq,category,label_text,planned_minor) VALUES(?,?,?,?,?)",
              (pid, i, b["category"], b.get("label_text", ""), amt))
    db.ex("INSERT INTO activity(project_id,user_id,at,kind,new_value) VALUES(?,?,?,?,?)",
          (pid, owner_id, now, "created", plan.get("engine", "template")))
    return pid


# ---------------------------------------------------------------- demo project

DEMO_NAMES = ["Anaya Patil", "Lucas Martin", "Sofia Rossi", "Omar Haddad", "Mei Chen"]


def seed_demo(owner_id: int, locale: str) -> int:
    today = db.today()
    start = today
    back = 0
    while back < 27:  # ~5.5 weeks ago
        start -= dt.timedelta(days=1)
        if is_workday(start):
            back += 1
    answers = dict(name="", name_key="demo.name", description="", description_key="demo.description",
                   type="cybersecurity", budget=120000, currency="EUR", team_size=5, member_names=DEMO_NAMES,
                   duration_weeks=14, start_date=start.isoformat(), locale=locale)
    pid = create_project(owner_id, answers)
    tasks = db.q("SELECT * FROM tasks WHERE project_id=? ORDER BY start_date, seq", (pid,))
    late = {"risk_plan": 70, "test_strategy": 40}
    for t in tasks:
        s, e = dt.date.fromisoformat(t["start_date"]), dt.date.fromisoformat(t["end_date"])
        if t["code"] in late and e < today:
            prog = late[t["code"]]
        elif e < today:
            prog = 100
        elif s <= today:
            total = max(1, workdays_between(s, e))
            done = workdays_between(s, today) - 1
            prog = max(10, min(90, int(round(100 * done / total / 10.0)) * 10))
            if t["code"] in ("siem",):
                prog = max(10, prog - 20)
        else:
            prog = 0
        if prog == 0:
            continue
        status = "done" if prog >= 100 else "in_progress"
        if t["code"] == "detection" and prog < 100:
            status = "blocked"
        db.ex("UPDATE tasks SET progress=?, status=? WHERE id=?", (prog, status, t["id"]))
        # history: halfway update + final/current update
        mid_day = s + (min(e, today) - s) / 2
        when_mid = dt.datetime.combine(mid_day, dt.time(16, 0)).isoformat()
        when_end = dt.datetime.combine(min(e, today - dt.timedelta(days=0 if prog < 100 else 0)), dt.time(17, 30)).isoformat()
        if t["is_milestone"]:
            db.ex("INSERT INTO activity(project_id,user_id,at,kind,task_id,old_value,new_value) VALUES(?,?,?,?,?,?,?)",
                  (pid, owner_id, when_end, "progress", t["id"], "0", str(prog)))
            continue
        half = min(prog, 50) if prog >= 100 else max(10, prog // 2)
        db.ex("INSERT INTO activity(project_id,user_id,at,kind,task_id,old_value,new_value) VALUES(?,?,?,?,?,?,?)",
              (pid, owner_id, when_mid, "progress", t["id"], "0", str(half)))
        db.ex("INSERT INTO activity(project_id,user_id,at,kind,task_id,old_value,new_value) VALUES(?,?,?,?,?,?,?)",
              (pid, owner_id, when_end, "progress", t["id"], str(half), str(prog)))
    # actual spend so far
    spend = {"personnel": 0.34, "software_licences": 0.55, "cloud_hosting": 0.30, "hardware": 0.80,
             "training": 0.0, "security_testing": 0.05, "contingency": 0.06}
    for b in db.q("SELECT * FROM budget_lines WHERE project_id=?", (pid,)):
        db.ex("UPDATE budget_lines SET actual_minor=? WHERE id=?", (int(b["planned_minor"] * spend.get(b["category"], 0)), b["id"]))
    for code, st in (("R01", "open"), ("R03", "mitigated"), ("R09", "open")):
        db.ex("UPDATE risks SET status=? WHERE project_id=? AND code=?", (st, pid, code))
    return pid
