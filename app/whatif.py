"""What-if simulation: "if <person> is absent from <date> to <date>".

Nothing is saved while simulating. The person's unfinished tasks that overlap the absence are
extended by the number of working days lost; dependent tasks are pushed (finish-to-start), the
same way the Gantt chart moves them. Replacement suggestions rank the other team members by
role match first, then by free hours during the task.
"""
from __future__ import annotations

import datetime as dt

from . import analytics as A, library as L
from .planner import add_workdays, workdays_between


def _topo(tasks: list[dict]) -> list[dict]:
    by = {t["id"]: t for t in tasks}
    seen, out = set(), []

    def visit(t):
        if t["id"] in seen:
            return
        seen.add(t["id"])
        for p in t["preds"]:
            if p in by:
                visit(by[p])
        out.append(t)

    for t in sorted(tasks, key=lambda x: (x["start"], x["seq"])):
        visit(t)
    return out


def _role_code(role_key: str) -> str:
    return next((k for k, v in L.ROLES.items() if v == role_key), role_key)


def _free_hours(data: dict, member: dict, a: dt.date, b: dt.date, skip_task: int | None = None) -> float:
    cap = 8 * member["capacity"] / 100
    days = workdays_between(a, b)
    booked = 0.0
    for t in data["tasks"]:
        if t["assignee_id"] != member["id"] or t["milestone"] or t["id"] == skip_task or t["progress"] >= 100:
            continue
        x, y = max(t["start"], a), min(t["end"], b)
        if x <= y:
            booked += 8 * workdays_between(x, y)
    return max(0.0, cap * days - booked)


def simulate(data: dict, member_id: int, d1: dt.date, d2: dt.date) -> dict:
    today = data["metrics"]["today"]
    if d2 < d1:
        d1, d2 = d2, d1
    member = next((m for m in data["members"] if m["id"] == member_id), None)
    if not member:
        return dict(error="member")
    tasks = data["tasks"]
    new: dict[int, dict] = {}
    direct = []
    for t in _topo(tasks):
        start, end = t["start"], t["end"]
        preds = [new[p] for p in t["preds"] if p in new]
        if preds:
            pend = max(p["end"] for p in preds)
            need = pend if t["milestone"] else add_workdays(pend, 1)
            if start < need and t["progress"] < 100:
                start = need
                end = start if t["milestone"] else add_workdays(start, max(1, t["days"]) - 1)
        lost = 0
        if t["assignee_id"] == member_id and not t["milestone"] and t["progress"] < 100:
            x, y = max(start, d1, today), min(end, d2)
            if x <= y:
                lost = workdays_between(x, y)
                end = add_workdays(end, lost)
                direct.append(t["id"])
        new[t["id"]] = dict(start=start, end=end, lost=lost)
    moved = []
    for t in tasks:
        n = new[t["id"]]
        if n["end"] != t["end"]:
            moved.append(dict(task=t, new_start=n["start"], new_end=n["end"], lost=n["lost"],
                              delay=workdays_between(t["end"], n["end"]) - 1, direct=t["id"] in direct,
                              critical=t["id"] in data["metrics"]["critical"]))
    moved.sort(key=lambda x: (not x["direct"], x["task"]["start"]))
    old_end = max(t["end"] for t in tasks) if tasks else today
    new_end = max(n["end"] for n in new.values()) if new else today
    # replacements for the directly affected tasks
    rc = _role_code(member["role_key"])
    fallback = L.ROLE_FALLBACK.get(rc, [])
    repl = {}
    for tid in direct:
        t = next(x for x in tasks if x["id"] == tid)
        a, b = max(t["start"], d1, today), min(t["end"], d2)
        need_h = 8 * workdays_between(a, b)
        cands = []
        for m in data["members"]:
            if m["id"] == member_id:
                continue
            mc = _role_code(m["role_key"])
            match = 3 if mc == rc else (2 if mc in fallback else 1)
            free = _free_hours(data, m, a, b, skip_task=tid)
            cands.append(dict(member=m, match=match, free=round(free), enough=free >= need_h))
        cands.sort(key=lambda c: (-c["match"], -c["enough"], -c["free"]))
        repl[tid] = dict(need=need_h, cands=cands[:3])
    return dict(member=member, d1=d1, d2=d2, lost_days=workdays_between(max(d1, today), d2), moved=moved, direct=direct,
                old_end=old_end, new_end=new_end, delay=max(0, workdays_between(old_end, new_end) - 1),
                critical_moved=sum(1 for x in moved if x["critical"]), replacements=repl)


def apply_dates(pid: int, sim: dict) -> int:
    """Saves the simulated end dates of the directly affected tasks (dependents follow automatically)."""
    n = 0
    for x in sim["moved"]:
        if x["direct"]:
            A.reschedule_from(pid, x["task"]["id"], x["new_end"])
            n += 1
    return n
