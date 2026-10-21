"""Project loading, localized display text, and tracking metrics (progress, plan vs
actual, critical path, workload, earned value). All numbers are computed from
language-independent data, so they are identical in every language."""
from __future__ import annotations

import datetime as dt
import hashlib

from . import db, i18n
from .planner import add_workdays, is_workday, workdays_between

D = dt.date.fromisoformat


def src_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- display text

def translations_for(entity_type: str, ids: list[int], locale: str) -> dict:
    if not ids:
        return {}
    qs = ",".join("?" * len(ids))
    rows = db.q(f"SELECT * FROM content_translations WHERE entity_type=? AND locale=? AND entity_id IN ({qs})",
                (entity_type, locale, *ids))
    return {(r["entity_id"], r["field"]): r for r in rows}


def text(row, field: str, locale: str, canonical: str, tr: dict | None = None, entity: str = "") -> tuple[str, str]:
    """Returns (display_text, state). state: key | canonical | machine | reviewed | approved | outdated"""
    key = row[f"{field}_key"] if f"{field}_key" in row.keys() else None
    if key:
        return i18n.t(key, locale), "key"
    canon = row[f"{field}_text"] if f"{field}_text" in row.keys() else row[field]
    if locale == canonical or not canon:
        return canon, "canonical"
    tr = tr or {}
    r = tr.get((row["id"], field))
    if r is not None:
        state = r["status"]
        if r["source_hash"] != src_hash(canon):
            state = "outdated"
        return r["text"], state
    return canon, "untranslated"


def member_name(m, locale: str) -> str:
    if m is None:
        return "—"
    return m["name"] or i18n.t("content.member_n", locale, n=m["seq"])


def project_name(p, locale: str) -> str:
    if p["name_key"]:
        return i18n.t(p["name_key"], locale)
    return p["name"]


def project_description(p, locale: str) -> str:
    if p["description_key"]:
        return i18n.t(p["description_key"], locale)
    return p["description"]


# ---------------------------------------------------------------- loading

def load(pid: int, locale: str) -> dict | None:
    p = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        return None
    canon = p["canonical_locale"]
    members = db.q("SELECT * FROM members WHERE project_id=? ORDER BY seq", (pid,))
    mem = {m["id"]: m for m in members}
    phases = db.q("SELECT * FROM phases WHERE project_id=? ORDER BY seq", (pid,))
    tasks = db.q("SELECT * FROM tasks WHERE project_id=? ORDER BY seq", (pid,))
    deps = db.q("SELECT * FROM dependencies WHERE project_id=?", (pid,))
    risks = db.q("SELECT * FROM risks WHERE project_id=? ORDER BY seq", (pid,))
    budget = db.q("SELECT * FROM budget_lines WHERE project_id=? ORDER BY seq", (pid,))
    trt = translations_for("task", [t["id"] for t in tasks], locale)
    trp = translations_for("phase", [x["id"] for x in phases], locale)
    trr = translations_for("risk", [r["id"] for r in risks], locale)

    ph_out = []
    for ph in phases:
        name, st = text(ph, "name", locale, canon, trp)
        ph_out.append(dict(id=ph["id"], seq=ph["seq"], code=ph["code"], name=name, tr_state=st))
    ph_by = {p_["id"]: p_ for p_ in ph_out}

    preds: dict[int, list[int]] = {}
    succs: dict[int, list[int]] = {}
    for d in deps:
        preds.setdefault(d["succ_id"], []).append(d["pred_id"])
        succs.setdefault(d["pred_id"], []).append(d["succ_id"])

    t_out = []
    for t in tasks:
        title, st = text(t, "title", locale, canon, trt)
        desc, _ = text(t, "desc", locale, canon, trt)
        a = mem.get(t["assignee_id"])
        days = t["duration_days"]
        t_out.append(dict(
            id=t["id"], seq=t["seq"], wbs=t["wbs"], code=t["code"], title=title, desc=desc, tr_state=st,
            canonical_title=t["title_text"], has_key=bool(t["title_key"]),
            phase_id=t["phase_id"], phase=ph_by[t["phase_id"]]["name"], phase_code=ph_by[t["phase_id"]]["code"],
            start=D(t["start_date"]), end=D(t["end_date"]), days=days, progress=t["progress"], status=t["status"],
            priority=t["priority"], milestone=bool(t["is_milestone"]), assignee_id=t["assignee_id"],
            assignee=member_name(a, locale), effort_hours=t["effort_hours"], deliverable=t["deliverable"],
            preds=preds.get(t["id"], []), succs=succs.get(t["id"], []),
            done_days=round(days * t["progress"] / 100, 1), left_days=round(days * (100 - t["progress"]) / 100, 1),
        ))
    m_out = [dict(id=m["id"], seq=m["seq"], name=member_name(m, locale), role_key=m["role_key"],
                  role=i18n.t(f"role.{m['role_key']}", locale), capacity=m["capacity_pct"],
                  rate_minor=m["daily_rate_minor"], email=m["email"]) for m in members]
    mname = {m["id"]: m["name"] for m in m_out}
    r_out = []
    for r in risks:
        title, st = text(r, "title", locale, canon, trr)
        mit, _ = text(r, "mitigation", locale, canon, trr)
        r_out.append(dict(id=r["id"], seq=r["seq"], code=r["code"], title=title, mitigation=mit, tr_state=st,
                          category=r["category"], probability=r["probability"], impact=r["impact"],
                          score=r["probability"] * r["impact"], owner=mname.get(r["owner_id"], "—"),
                          owner_id=r["owner_id"], status=r["status"]))
    b_out = [dict(id=b["id"], category=b["category"],
                  label=b["label_text"] or i18n.t(f"budget.{b['category']}", locale),
                  planned=b["planned_minor"], actual=b["actual_minor"],
                  variance=b["planned_minor"] - b["actual_minor"]) for b in budget]
    proj = dict(p)
    proj["display_name"] = project_name(p, locale)
    proj["display_description"] = project_description(p, locale)
    data = dict(project=proj, members=m_out, phases=ph_out, tasks=t_out, risks=r_out, budget=b_out)
    data["metrics"] = metrics(data)
    return data


# ---------------------------------------------------------------- metrics

def overall_progress(tasks) -> float:
    work = [t for t in tasks if not t["milestone"]]
    total = sum(t["days"] for t in work) or 1
    return sum(t["days"] * t["progress"] for t in work) / total


def planned_progress(tasks, on: dt.date) -> float:
    work = [t for t in tasks if not t["milestone"]]
    total = sum(t["days"] for t in work) or 1
    acc = 0.0
    for t in work:
        if on >= t["end"]:
            acc += t["days"]
        elif on >= t["start"]:
            acc += t["days"] * workdays_between(t["start"], on) / max(1, workdays_between(t["start"], t["end"]))
    return 100 * acc / total


def critical_path(tasks) -> set[int]:
    """Tasks with zero slack (in working days)."""
    if not tasks:
        return set()
    start = min(t["start"] for t in tasks)

    def finish(t):  # end of the task's last working day, in working-day units
        return workdays_between(start, t["end"])

    def dur(t):
        return 0 if t["milestone"] else t["days"]

    by = {t["id"]: t for t in tasks}
    project_finish = max(finish(t) for t in tasks)
    order, seen = [], set()

    def visit(t):
        if t["id"] in seen:
            return
        seen.add(t["id"])
        for p in t["preds"]:
            if p in by:
                visit(by[p])
        order.append(t)

    for t in tasks:
        visit(t)
    lf: dict[int, int] = {}
    for t in reversed(order):
        succ_ls = [lf.get(s, project_finish) - dur(by[s]) for s in t["succs"] if s in by]
        lf[t["id"]] = min(succ_ls) if succ_ls else project_finish
    return {t["id"] for t in tasks if lf[t["id"]] - finish(t) <= 0}


def metrics(data: dict) -> dict:
    tasks, budget = data["tasks"], data["budget"]
    today = db.today()
    work = [t for t in tasks if not t["milestone"]]
    prog = overall_progress(tasks)
    plan = planned_progress(tasks, today)
    overdue = [t for t in tasks if t["end"] < today and t["progress"] < 100]
    week_end = today + dt.timedelta(days=6 - today.weekday())
    due_week = [t for t in tasks if today <= t["end"] <= week_end and t["progress"] < 100]
    planned_total = sum(b["planned"] for b in budget)
    actual_total = sum(b["actual"] for b in budget)
    contingency = sum(b["planned"] for b in budget if b["category"] == "contingency")
    bac = planned_total - contingency
    ev = bac * prog / 100
    pv = bac * plan / 100
    cpi = ev / actual_total if actual_total else None
    spi = ev / pv if pv else None
    open_risks = [r for r in data["risks"] if r["status"] == "open"]
    high_risks = [r for r in open_risks if r["score"] >= 12]
    p = data["project"]
    end = D(p["end_date"])
    milestones = [t for t in tasks if t["milestone"]]
    next_ms = next((m for m in sorted(milestones, key=lambda x: x["end"]) if m["progress"] < 100), None)
    health = "green"
    if (spi is not None and spi < 0.9) or len(overdue) >= 3 or (cpi is not None and cpi < 0.9):
        health = "amber"
    if (spi is not None and spi < 0.75) or len(overdue) >= 6 or (cpi is not None and cpi < 0.75):
        health = "red"
    return dict(
        progress=prog, planned=plan, variance_pts=prog - plan, overdue=overdue, due_week=due_week,
        tasks_total=len(work), tasks_done=sum(1 for t in work if t["progress"] >= 100),
        tasks_active=sum(1 for t in work if 0 < t["progress"] < 100), tasks_blocked=sum(1 for t in work if t["status"] == "blocked"),
        days_total=sum(t["days"] for t in work), days_done=round(sum(t["done_days"] for t in work), 1),
        days_left=round(sum(t["left_days"] for t in work), 1),
        budget_planned=planned_total, budget_actual=actual_total, budget_left=planned_total - actual_total,
        bac=bac, ev=ev, pv=pv, cpi=cpi, spi=spi, eac=(bac / cpi if cpi else None),
        open_risks=len(open_risks), high_risks=len(high_risks), next_milestone=next_ms,
        days_to_end=workdays_between(today, end) if end >= today else 0, health=health,
        critical=critical_path(tasks), today=today,
    )


def history(pid: int, data: dict) -> list[dict]:
    """Actual vs planned progress per working day, from the activity log."""
    tasks = [t for t in data["tasks"] if not t["milestone"]]
    if not tasks:
        return []
    weight = {t["id"]: t["days"] for t in tasks}
    total = sum(weight.values()) or 1
    rows = db.q("SELECT at, task_id, new_value FROM activity WHERE project_id=? AND kind='progress' ORDER BY at", (pid,))
    start = min(t["start"] for t in data["tasks"])
    end = max(t["end"] for t in data["tasks"])
    today = db.today()
    state = {t: 0 for t in weight}
    out, i, d = [], 0, start
    while d <= end:
        if is_workday(d):
            while i < len(rows) and rows[i]["at"][:10] <= d.isoformat():
                tid = rows[i]["task_id"]
                if tid in state:
                    state[tid] = int(rows[i]["new_value"])
                i += 1
            actual = sum(weight[t] * state[t] for t in state) / total if d <= today else None
            out.append(dict(date=d, planned=planned_progress(data["tasks"], d), actual=actual))
        d += dt.timedelta(days=1)
    return out


def workload(data: dict) -> dict:
    """Hours per member per ISO week, plus capacity."""
    tasks = [t for t in data["tasks"] if not t["milestone"]]
    if not tasks:
        return dict(weeks=[], rows=[])
    start = min(t["start"] for t in tasks)
    end = max(t["end"] for t in tasks)
    wk0 = start - dt.timedelta(days=start.weekday())
    weeks = []
    w = wk0
    while w <= end:
        weeks.append(w)
        w += dt.timedelta(days=7)
    rows = []
    for m in data["members"]:
        hours = []
        for w in weeks:
            h = 0
            for t in tasks:
                if t["assignee_id"] != m["id"]:
                    continue
                a, b = max(t["start"], w), min(t["end"], w + dt.timedelta(days=4))
                if a <= b:
                    h += 8 * workdays_between(a, b)
            hours.append(h)
        cap = 40 * m["capacity"] / 100
        rows.append(dict(member=m, hours=hours, capacity=cap, over=[h > cap for h in hours],
                         total=sum(hours), peak=max(hours) if hours else 0))
    return dict(weeks=weeks, rows=rows)


def log_progress(pid: int, uid: int, task_id: int, old: int, new: int):
    db.ex("INSERT INTO activity(project_id,user_id,at,kind,task_id,old_value,new_value) VALUES(?,?,?,?,?,?,?)",
          (pid, uid, dt.datetime.now().replace(microsecond=0).isoformat(), "progress", task_id, str(old), str(new)))


def status_for(progress: int, current: str) -> str:
    if progress >= 100:
        return "done"
    if current == "blocked":
        return "blocked"
    return "in_progress" if progress > 0 else "todo"


def reschedule_from(pid: int, task_id: int, new_end: dt.date):
    """Move a task's end date and push dependent tasks forward (keeps durations)."""
    t = db.q1("SELECT * FROM tasks WHERE id=? AND project_id=?", (task_id, pid))
    if not t:
        return
    db.ex("UPDATE tasks SET end_date=?, duration_days=? WHERE id=?",
          (new_end.isoformat(), 0 if t["is_milestone"] else max(1, workdays_between(D(t["start_date"]), new_end)), task_id))
    queue = [task_id]
    while queue:
        cur = queue.pop(0)
        cur_end = D(db.q1("SELECT end_date FROM tasks WHERE id=?", (cur,))["end_date"])
        for s in db.q("SELECT t.* FROM dependencies d JOIN tasks t ON t.id=d.succ_id WHERE d.pred_id=?", (cur,)):
            s_start = D(s["start_date"])
            need = cur_end if s["is_milestone"] else add_workdays(cur_end, 1)
            if s_start < need:
                dur = s["duration_days"]
                ns = need
                ne = ns if s["is_milestone"] else add_workdays(ns, dur - 1)
                db.ex("UPDATE tasks SET start_date=?, end_date=? WHERE id=?", (ns.isoformat(), ne.isoformat(), s["id"]))
                queue.append(s["id"])
    end = db.q1("SELECT MAX(end_date) e FROM tasks WHERE project_id=?", (pid,))["e"]
    db.ex("UPDATE projects SET end_date=? WHERE id=?", (end, pid))
