"""Data-driven risks (explainable rules, no black box).

Every automatic risk comes from one rule in analytics.conditions() and stores the numbers
behind it (params_json), so the page can say *why* it was raised. Rules:

  overload:<member>    a member is planned above capacity (40 h × availability) in a current or future week
  overdue_crit         tasks on the critical path are overdue
  behind               progress is more than 3 points behind plan
  cpi / spi            cost or schedule performance index below 0.95
  milestone:<task>     a milestone depends on late/blocked work, or the remaining work does not fit before it

Automatic risks are always "warning signal" evidence; they are never confirmed automatically.
When the cause disappears the risk becomes "Condition cleared"; if it comes back it is re-opened.
"""
from __future__ import annotations

import json
import sqlite3

from . import analytics as A, db

THRESHOLD_BEHIND = 3.0
THRESHOLD_INDEX = 0.95


def _pm(data) -> int | None:
    pm = next((m for m in data["members"] if m["role_key"] == "project_manager"), None)
    return (pm or (data["members"][0] if data["members"] else {"id": None}))["id"]


def active_rules(data: dict) -> dict:
    """rule_key -> (category, probability, impact, params) for every condition that is true now."""
    c, m = data["conditions"], data["metrics"]
    by = {t["id"]: t for t in data["tasks"]}
    out = {}
    for o in c["overload"]:
        mem = o["member"]
        on_crit = any(t["assignee_id"] == mem["id"] and t["id"] in m["critical"] and t["progress"] < 100 for t in data["tasks"])
        out[f"overload:{mem['id']}"] = ("resource", 4 if o["peak"] >= 1.25 * o["capacity"] else 3, 4 if on_crit else 3,
                                        dict(name=mem["name"], h=int(o["peak"]), cap=int(o["capacity"]),
                                             week=o["weeks"][0][0].isoformat(), weeks=len(o["weeks"])))
    if c["overdue_crit"]:
        late_days = max(A.workdays_between(t["end"], m["today"]) - 1 for t in c["overdue_crit"])
        out["overdue_crit"] = ("schedule", 5 if late_days > 5 else 4, 4,
                               dict(n=len(c["overdue_crit"]), tasks=[t["wbs"] for t in c["overdue_crit"][:4]], days=late_days))
    if c["behind"] < -THRESHOLD_BEHIND:
        pts = round(-c["behind"], 1)
        out["behind"] = ("schedule", 4 if pts > 10 else 3, 4 if pts > 10 else 3,
                         dict(pts=pts, actual=round(m["progress"], 1), planned=round(m["planned"], 1)))
    if c["cpi"] is not None and c["cpi"] < THRESHOLD_INDEX:
        out["cpi"] = ("cost", 4 if c["cpi"] < 0.85 else 3, 4, dict(v=round(c["cpi"], 2)))
    if c["spi"] is not None and c["spi"] < THRESHOLD_INDEX:
        out["spi"] = ("schedule", 4 if c["spi"] < 0.85 else 3, 4 if c["spi"] < 0.85 else 3, dict(v=round(c["spi"], 2)))
    for x in c["milestones"][:3]:
        ms = x["task"]
        out[f"milestone:{ms['id']}"] = ("schedule", 4 if x["late"] else 3, 4,
                                        dict(wbs=ms["wbs"], date=ms["end"].isoformat(), n=len(x["late"]),
                                             need=x["need"], avail=x["avail"]))
    return out


def evaluate(pid: int, data: dict | None = None) -> list[dict]:
    """Create / update / clear the automatic risks of a project. Returns risks that were opened or re-opened."""
    data = data or A.load(pid, "en")
    if not data:
        return []
    rules = active_rules(data)
    existing = {r["rule_key"]: r for r in db.q("SELECT * FROM risks WHERE project_id=? AND source='auto'", (pid,))}
    owner = _pm(data)
    now = db.now()
    opened = []
    for key, (cat, p, i, params) in rules.items():
        pj = json.dumps(params, ensure_ascii=False, sort_keys=True)
        r = existing.get(key)
        if r is None:
            n = db.q1("SELECT COUNT(*) n, COALESCE(MAX(seq),0) s FROM risks WHERE project_id=? AND source='auto'", (pid,))
            seq = db.q1("SELECT COALESCE(MAX(seq),0) s FROM risks WHERE project_id=?", (pid,))["s"] + 1
            try:
                rid = db.ex("""INSERT INTO risks(project_id,seq,code,title_text,mitigation_text,category,probability,impact,owner_id,status,
                               source,rule_key,params_json,evidence,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'open','auto',?,?,'signal',?)""",
                            (pid, seq, f"A{n['n'] + 1:02d}", "", "", cat, p, i, owner, key, pj, now))
            except sqlite3.IntegrityError:  # created by a parallel request
                continue
            opened.append(dict(id=rid, rule_key=key, score=p * i, reopened=False))
        else:
            reopen = r["status"] == "cleared"
            if reopen or r["params_json"] != pj or r["probability"] != p or r["impact"] != i:
                db.ex("UPDATE risks SET probability=?, impact=?, params_json=?, updated_at=?, status=?, cleared_at=? WHERE id=?",
                      (p, i, pj, now, "open" if reopen else r["status"], None if reopen else r["cleared_at"], r["id"]))
            if reopen:
                opened.append(dict(id=r["id"], rule_key=key, score=p * i, reopened=True))
    for key, r in existing.items():
        if key not in rules and r["status"] in ("open", "mitigated"):
            db.ex("UPDATE risks SET status='cleared', cleared_at=?, updated_at=? WHERE id=?", (now, now, r["id"]))
    return opened
