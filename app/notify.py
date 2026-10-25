"""Real-time e-mail notifications and the weekly status report.

Who receives what
  * real accounts: the project owner (if e-mails are on in Settings) + the team member concerned
    (assignee / overloaded person) when an e-mail address is filled in on Team & resources.
  * demo workspaces: only the site administrator address (EMAIL_TO), so visitors can never
    make the server send mail to addresses they type in.
Automatic alerts (overdue, overload, new risks) are de-duplicated in the `notified` table, so
the same alert is never sent twice.
"""
from __future__ import annotations

import datetime as dt
import html
import os

from . import analytics as A, db, i18n, mailer, riskengine

_base_url = ""


def set_base_url(url: str) -> None:
    global _base_url
    if url and not _base_url:
        _base_url = url.rstrip("/")


def base_url() -> str:
    return (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or _base_url or "http://localhost:8000").rstrip("/")


# ---------------------------------------------------------------- recipients

def _context(pid: int):
    p = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        return None, None, "en"
    owner = db.q1("SELECT * FROM users WHERE id=?", (p["owner_id"],))
    loc = i18n.norm((owner["report_locale"] or owner["interface_locale"]) if owner else "en")
    return p, owner, loc


def recipients(pid: int, member_ids=()) -> list[str]:
    p, owner, _ = _context(pid)
    if not p or not owner:
        return []
    if owner["is_demo"]:
        return [mailer.admin_to()] if mailer.admin_to() else []
    out = []
    if owner["email_notify"]:
        out.append(owner["email"])
    for mid in member_ids:
        m = db.q1("SELECT email FROM members WHERE id=? AND project_id=?", (mid, pid))
        if m and m["email"]:
            out.append(m["email"])
    return [a for a in out if mailer.valid(a)] or ([mailer.admin_to()] if mailer.admin_to() and owner["email_notify"] else [])


# ---------------------------------------------------------------- formatting

def _page(title: str, lines: list[str], link: str, loc: str, demo: bool, extra_html: str = "") -> tuple[str, str]:
    t = lambda k, **kw: i18n.t(k, loc, **kw)
    body = "".join(f"<p style='margin:0 0 10px'>{html.escape(x)}</p>" if not x.startswith("• ") else
                   f"<div style='margin:0 0 6px 8px'>{html.escape(x)}</div>" for x in lines)
    url = base_url() + link
    foot = t("mail.footer_demo") if demo else t("mail.footer")
    h = (f"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:620px;margin:auto;color:#1f2533'>"
         f"<div style='background:#4053d6;color:#fff;padding:14px 18px;border-radius:10px 10px 0 0;font-weight:600'>✈ MissionPilot AI</div>"
         f"<div style='border:1px solid #e3e6ef;border-top:0;padding:18px;border-radius:0 0 10px 10px'>"
         f"<h2 style='font-size:18px;margin:0 0 12px'>{html.escape(title)}</h2>{body}{extra_html}"
         f"<p style='margin:18px 0 6px'><a href='{html.escape(url)}' style='background:#4053d6;color:#fff;padding:9px 16px;"
         f"border-radius:8px;text-decoration:none;display:inline-block'>{html.escape(t('mail.open'))}</a></p>"
         f"<p style='color:#6b7280;font-size:12px;margin-top:16px'>{html.escape(foot)}</p></div></div>")
    txt = title + "\n\n" + "\n".join(lines) + f"\n\n{t('mail.open')}: {url}\n\n{foot}"
    return h, txt


def _send(pid: int, event: str, title_key: str, lines: list[str], link: str, *, member_ids=(), user_id=None,
          attachments=None, extra_html: str = "", to=None, **params) -> int | None:
    p, owner, loc = _context(pid)
    if not p:
        return None
    data_name = A.project_name(p, loc)
    subject = i18n.t(title_key, loc, project=data_name, **params)
    h, txt = _page(subject, lines, link, loc, bool(owner and owner["is_demo"]), extra_html)
    return mailer.send(to if to is not None else recipients(pid, member_ids), subject, h, txt, event=event,
                       project_id=pid, user_id=user_id, attachments=attachments)


def _task(pid: int, tid: int, loc: str):
    data = A.load(pid, loc)
    return data, next((x for x in data["tasks"] if x["id"] == tid), None)


# ---------------------------------------------------------------- events triggered by a person

def task_assigned(pid: int, tid: int, old_member_id=None, user_id=None) -> None:
    _, _, loc = _context(pid)
    data, task = _task(pid, tid, loc)
    if not task:
        return
    t = lambda k, **kw: i18n.t(k, loc, **kw)
    names = {m["id"]: m["name"] for m in data["members"]}
    lines = [t("mail.assigned", name=task["assignee"], task=task["title"], start=i18n.date(task["start"], loc),
               end=i18n.date(task["end"], loc))]
    if old_member_id:
        lines.append(t("mail.reassigned_from", old=names.get(old_member_id, "—")))
    _send(pid, "task_assigned", "mail.subject.assigned", lines, f"/app/p/{pid}/tasks", member_ids=[task["assignee_id"]],
          user_id=user_id, name=task["assignee"], task=task["title"])


def deadline_changed(pid: int, tid: int, old_end: dt.date, moved: int, user_id=None) -> None:
    _, _, loc = _context(pid)
    data, task = _task(pid, tid, loc)
    if not task or task["end"] == old_end:
        return
    t = lambda k, **kw: i18n.t(k, loc, **kw)
    lines = [t("mail.deadline", task=task["title"], old=i18n.date(old_end, loc), new=i18n.date(task["end"], loc), n=moved)]
    if task["end"] > old_end:
        lines.append(t("mail.new_end", date=i18n.date(data["project"]["end_date"], loc)))
    _send(pid, "deadline_changed", "mail.subject.deadline", lines, f"/app/p/{pid}/gantt", member_ids=[task["assignee_id"]],
          user_id=user_id, task=task["title"])


def task_blocked(pid: int, tid: int, user_id=None) -> None:
    _, _, loc = _context(pid)
    _, task = _task(pid, tid, loc)
    if not task:
        return
    _send(pid, "task_blocked", "mail.subject.blocked", [i18n.t("mail.blocked", loc, task=task["title"], name=task["assignee"])],
          f"/app/p/{pid}/tasks", member_ids=[task["assignee_id"]], user_id=user_id, task=task["title"])


def risk_raised(pid: int, rid: int, user_id=None) -> None:
    _, _, loc = _context(pid)
    data = A.load(pid, loc)
    r = next((x for x in data["risks"] if x["id"] == rid), None)
    if not r or r["score"] < 12:
        return
    t = lambda k, **kw: i18n.t(k, loc, **kw)
    band = t(f"band.{r['band']}")
    lines = [t("mail.risk", rid=r["code"], risk=r["title"], score=r["score"], band=band)]
    if r["why"]:
        lines.append(t("mail.why", why=r["why"]))
    if r["mitigation"]:
        lines.append(t("mail.action", action=r["mitigation"]))
    _send(pid, "risk_raised", "mail.subject.risk", lines, f"/app/p/{pid}/risks", user_id=user_id, band=band, risk=r["title"])


def _once(pid: int, key: str) -> bool:
    """True the first time a key is seen for this project."""
    try:
        db.ex("INSERT INTO notified(project_id,key,created_at) VALUES(?,?,?)", (pid, key, db.now()))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- automatic checks (after every change + daily job)

def check(pid: int, send: bool = True) -> dict:
    """Run the risk engine and e-mail what is new: new high risks, newly overdue tasks, new overload weeks.
    send=False only records the current state (used when a project is created, so day-one facts are not e-mailed)."""
    p, owner, loc = _context(pid)
    if not p:
        return {}
    data = A.load(pid, "en")
    opened = riskengine.evaluate(pid, data)
    data = A.load(pid, loc)
    m, c = data["metrics"], data["conditions"]
    out = dict(opened=len(opened), overdue=0, overload=0)
    t = lambda k, **kw: i18n.t(k, loc, **kw)
    # newly overdue tasks -> one digest e-mail
    new_od = [x for x in m["overdue"] if _once(pid, f"overdue:{x['id']}:{x['end'].isoformat()}")]
    # new overload weeks -> one e-mail per member
    new_ol = []
    for o in c["overload"]:
        fresh = [(w, h) for w, h in o["weeks"] if _once(pid, f"overload:{o['member']['id']}:{w.isoformat()}")]
        if fresh:
            new_ol.append((o, fresh))
    if not send:
        for r in opened:
            _once(pid, f"risk:{r['id']}:{db.now()[:10]}")
        return out
    for r in opened:
        if r["score"] >= 12 and _once(pid, f"risk:{r['id']}:{db.now()[:10]}"):
            risk_raised(pid, r["id"])
    if new_od:
        lines = [t("mail.overdue_intro", n=len(new_od))]
        lines += [f"• {x['wbs']} {x['title']} — {x['assignee']}, {t('mail.due')} {i18n.date(x['end'], loc)}, {i18n.pct(x['progress'], loc)}"
                  for x in new_od[:15]]
        _send(pid, "overdue", "mail.subject.overdue", lines, f"/app/p/{pid}/tasks?f=overdue",
              member_ids=sorted({x["assignee_id"] for x in new_od if x["assignee_id"]}), n=len(new_od))
        out["overdue"] = len(new_od)
    for o, fresh in new_ol:
        w, h = max(fresh, key=lambda x: x[1])
        lines = [t("mail.overload", name=o["member"]["name"], h=int(h), week=i18n.short_date(w, loc), cap=int(o["capacity"])),
                 t("mail.overload_fix")]
        _send(pid, "overload", "mail.subject.overload", lines, f"/app/p/{pid}/whatif", member_ids=[o["member"]["id"]],
              name=o["member"]["name"])
        out["overload"] += 1
    return out


# ---------------------------------------------------------------- weekly status report

def weekly_lines(data: dict, loc: str) -> list[str]:
    t = lambda k, **kw: i18n.t(k, loc, **kw)
    m, v = data["metrics"], data["verdict"]
    cur = data["project"]["currency"]
    lines = [t("mail.weekly_intro", date=i18n.date(m["today"], loc), verdict=t(f"verdict.{v['level']}")),
             t("mail.weekly_progress", actual=i18n.pct(m["progress"], loc), planned=i18n.pct(m["planned"], loc),
               done=m["tasks_done"], total=m["tasks_total"], overdue=len(m["overdue"])),
             t("mail.weekly_budget", used=i18n.money_minor(m["budget_actual"], cur, loc), total=i18n.money_minor(m["budget_planned"], cur, loc),
               cpi=i18n.num(m["cpi"], loc, 2) if m["cpi"] else "—", spi=i18n.num(m["spi"], loc, 2) if m["spi"] else "—")]
    if v["reasons"]:
        lines.append(t("mail.weekly_reasons"))
        lines += ["• " + A.reason_text(k, p, loc) for k, p, _ in v["reasons"][:3]]
    nxt = sorted([x for x in data["tasks"] if x["progress"] < 100 and not x["milestone"] and x["start"] <= m["today"] + dt.timedelta(days=7)],
                 key=lambda x: x["end"])[:8]
    if nxt:
        lines.append(t("mail.weekly_next"))
        lines += [f"• {x['wbs']} {x['title']} — {x['assignee']} ({i18n.date(x['end'], loc)}, {i18n.pct(x['progress'], loc)})" for x in nxt]
    top = sorted([r for r in data["risks"] if r["status"] == "open"], key=lambda r: -r["score"])[:3]
    if top:
        lines.append(t("mail.weekly_risks"))
        lines += [f"• {r['code']} {r['title']} ({r['score']})" for r in top]
    return lines


async def weekly_report(pid: int, to: list[str] | None = None, user_id=None, attach: bool = True) -> int | None:
    from . import documents as DOCS, exporters as X
    _, _, loc = _context(pid)
    riskengine.evaluate(pid)
    data = A.load(pid, loc)
    lines = weekly_lines(data, loc)
    files = None
    if attach:
        try:
            content = DOCS.build_report("weekly", data, loc, db.today())
            name = f"{X.safe_name(data['project']['display_name'])}_weekly_{db.today().isoformat()}.docx"
            files = [(name, await X.docx_bytes(content),
                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document")]
            lines.append(i18n.t("mail.weekly_attach", loc))
        except Exception:
            files = None
    return _send(pid, "weekly_report", "mail.subject.weekly", lines, f"/app/p/{pid}/home", user_id=user_id,
                 attachments=files, to=to, date=i18n.date(db.today(), loc))


def iso_week(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


async def weekly_all(force: bool = False) -> dict:
    """Monday 08:00 Europe/Paris job: one report per project for every account that has weekly reports on."""
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo("Europe/Paris"))
    if not force and not (now.weekday() == 0 and now.hour >= 8):
        return dict(sent=0, skipped="not Monday 08:00 Paris yet", now=now.isoformat(timespec="minutes"))
    week = iso_week(now.date())
    sent = 0
    for p in db.q("SELECT p.id FROM projects p JOIN users u ON u.id=p.owner_id WHERE u.is_demo=0 AND u.weekly_report=1 AND p.status='active'"):
        if _once(p["id"], f"weekly:{week}"):
            await weekly_report(p["id"])
            sent += 1
    return dict(sent=sent, week=week)


def test_email(user, loc: str) -> int:
    t = lambda k, **kw: i18n.t(k, loc, **kw)
    to = [mailer.admin_to()] if user["is_demo"] else [user["email"]]
    h, txt = _page(t("mail.subject.test"), [t("mail.test", time=i18n.datetime(dt.datetime.now().replace(microsecond=0), loc, "short"))],
                   "/app/settings", loc, bool(user["is_demo"]))
    return mailer.send(to, t("mail.subject.test"), h, txt, event="test", user_id=user["id"])
