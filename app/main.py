"""MissionPilot AI — web application (Starlette)."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlparse

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from . import ai, analytics as A, charts, db, documents as DOCS, exporters as X, i18n, library as L, planner, render
from . import importer as IMP, mailer, ml, notes as NOTES, notify, riskengine, whatif

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
SECRET = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
VERSION = "1.1.0"
CRON_SECRET = os.environ.get("CRON_SECRET", "")


# ---------------------------------------------------------------- auth helpers

def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 240_000).hex()
    return f"pbkdf2${salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 240_000).hex(), h)


def current_user(request: Request):
    uid = request.session.get("uid")
    return db.q1("SELECT * FROM users WHERE id=?", (uid,)) if uid else None


def locale_of(request: Request, user=None) -> str:
    q = request.query_params.get("lang")
    if q and i18n.norm(q) == q:
        request.session["lang"] = q
        if user is not None:
            db.ex("UPDATE users SET interface_locale=? WHERE id=?", (q, user["id"]))
        return q
    if user is not None:
        return i18n.norm(user["interface_locale"])
    if request.session.get("lang"):
        return i18n.norm(request.session["lang"])
    return i18n.from_accept_language(request.headers.get("accept-language"))


def pref(user, field: str, fallback: str) -> str:
    v = user[field] if user is not None else None
    return i18n.norm(v) if v else fallback


def owned_project(user, pid: int):
    p = db.q1("SELECT * FROM projects WHERE id=? AND owner_id=?", (pid, user["id"]))
    return p


def flush_missing():
    for (key, loc), n in i18n.drain_missing().items():
        db.ex("""INSERT INTO missing_translations(key,locale,first_seen,count) VALUES(?,?,?,?)
                 ON CONFLICT(key,locale) DO UPDATE SET count=count+excluded.count""", (key, loc, db.now(), n))


# ---------------------------------------------------------------- page rendering

NAV = [("home", "nav.home", "home"), ("projects", "nav.projects", "folder"), ("tasks", "nav.tasks", "check"),
       ("gantt", "nav.gantt", "gantt"), ("progress", "nav.progress", "bars"), ("budget", "nav.budget", "coin"),
       ("risks", "nav.risks", "alert"), ("team", "nav.team", "users"), ("whatif", "nav.whatif", "whatif"),
       ("notes", "nav.notes", "notes"), ("reports", "nav.reports", "report"),
       ("documents", "nav.documents", "doc"), ("assistant", "nav.assistant", "spark"), ("settings", "nav.settings", "gear"),
       ("help", "nav.help", "help")]
PROJECT_PAGES = {"home", "tasks", "gantt", "progress", "budget", "risks", "team", "whatif", "notes", "reports", "documents", "assistant"}


def page(request: Request, name: str, user, loc: str, **kw):
    notify.set_base_url(f"{request.url.scheme}://{request.url.netloc}")
    info = i18n.info(loc)
    projects = db.q("SELECT id,name,name_key FROM projects WHERE owner_id=? ORDER BY id DESC", (user["id"],)) if user else []
    pid = kw.get("pid") or (user["current_project_id"] if user else None)
    if user and pid and not any(p["id"] == pid for p in projects):
        pid = projects[0]["id"] if projects else None
    nav = []
    for key, label, icon in NAV:
        if key in PROJECT_PAGES:
            href = f"/app/p/{pid}/{key}" if pid else "/app/new"
        else:
            href = {"projects": "/app/projects", "settings": "/app/settings", "help": "/app/help"}[key]
        nav.append(dict(key=key, label=i18n.t(label, loc), icon=icon, href=href))
    tour = None
    if user:
        tour = dict(step=user["onboarding_step"], done=bool(user["onboarding_completed_at"]),
                    autostart=(not user["onboarding_completed_at"] and user["onboarding_step"] == 0) or request.query_params.get("tour"),
                    voice=bool(user["voice_enabled"]), rate=user["voice_rate"], voice_name=user["voice_name"] or "")
    ctx = dict(request=request, user=user, L=loc, info=info, t=lambda k, **p: i18n.t(k, loc, **p), tl=lambda k: i18n.tl(k, loc),
               vr=lambda k, p: A.reason_text(k, p, loc),
               i18n=i18n, locales=[(c, i18n.LOCALES[c]["name"]) for c in i18n.CODES], nav=nav, active=kw.get("active", ""),
               projects=[dict(id=p["id"], name=i18n.t(p["name_key"], loc) if p["name_key"] else p["name"]) for p in projects],
               pid=pid, ai_on=ai.available(), ml_on=ml.available(), mail_on=mailer.configured(), tour=tour, version=VERSION,
               js=json.dumps(dict(strings=i18n.js_bundle(loc), locale=loc, tts=info["tts"], dir=info["dir"], pid=pid,
                                  tour=tour, page=kw.get("active", ""), demo=bool(user and user["is_demo"]),
                                  types=L.PROJECT_TYPES, currencies=i18n.CURRENCIES), ensure_ascii=False))
    ctx.update(kw)
    resp = templates.TemplateResponse(request, name, ctx)
    flush_missing()
    return resp


def login_required(fn):
    async def wrapper(request: Request):
        user = current_user(request)
        if not user:
            return RedirectResponse("/login?next=" + quote(str(request.url.path)), 303)
        return await fn(request, user)
    wrapper.__name__ = fn.__name__
    return wrapper


def project_page(fn):
    async def wrapper(request: Request):
        user = current_user(request)
        if not user:
            return RedirectResponse("/login?next=" + quote(str(request.url.path)), 303)
        pid = int(request.path_params["pid"])
        if not owned_project(user, pid):
            return RedirectResponse("/app/projects", 303)
        if user["current_project_id"] != pid:
            db.ex("UPDATE users SET current_project_id=? WHERE id=?", (pid, user["id"]))
            user = current_user(request)
        loc = locale_of(request, user)
        _check(pid)
        data = A.load(pid, loc)
        return await fn(request, user, loc, pid, data)
    wrapper.__name__ = fn.__name__
    return wrapper


def _check(pid: int, send: bool = True):
    """Risk engine + e-mail alerts. Never breaks a page or an API call."""
    try:
        return notify.check(pid, send=send)
    except Exception as e:  # pragma: no cover - logged, not shown
        print("check failed", pid, e)
        return {}


# ---------------------------------------------------------------- public pages

async def landing(request: Request):
    user = current_user(request)
    loc = locale_of(request, user)
    return page(request, "landing.html", user, loc)


async def login(request: Request):
    user = current_user(request)
    loc = locale_of(request, user)
    error = None
    if request.method == "POST":
        f = await request.form()
        u = db.q1("SELECT * FROM users WHERE email=? AND is_demo=0", (str(f.get("email", "")).strip().lower(),))
        if u and check_pw(str(f.get("password", "")), u["password_hash"]):
            request.session.clear()
            request.session["uid"] = u["id"]
            nxt = request.query_params.get("next") or "/app"
            return RedirectResponse(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/app", 303)
        error = i18n.t("auth.bad_login", loc)
    return page(request, "login.html", None, loc, error=error, mode="login")


async def register(request: Request):
    loc = locale_of(request)
    error = None
    if request.method == "POST":
        f = await request.form()
        email = str(f.get("email", "")).strip().lower()
        name = str(f.get("name", "")).strip()[:80]
        pw = str(f.get("password", ""))
        lang = i18n.norm(str(f.get("lang", loc)))
        if "@" not in email or len(pw) < 8 or not name:
            error = i18n.t("auth.invalid", lang)
        elif db.q1("SELECT 1 FROM users WHERE email=?", (email,)):
            error = i18n.t("auth.exists", lang)
        else:
            uid = db.ex("""INSERT INTO users(email,name,password_hash,interface_locale,ai_locale,doc_locale,report_locale,created_at)
                           VALUES(?,?,?,?,?,?,?,?)""", (email, name, hash_pw(pw), lang, lang, lang, lang, db.now()))
            request.session.clear()
            request.session["uid"] = uid
            return RedirectResponse("/app/new?welcome=1", 303)
        loc = lang
    return page(request, "login.html", None, loc, error=error, mode="register")


async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", 303)


async def demo(request: Request):
    """A private demo workspace per visitor, filled with a sample project; tour starts automatically."""
    me = current_user(request)
    loc = locale_of(request, me)
    purge_demos()
    if me and me["is_demo"]:  # same visitor clicks "Try the demo" again: reopen the same workspace, no new project
        p = db.q1("SELECT id FROM projects WHERE owner_id=? ORDER BY id DESC", (me["id"],))
        if p:
            return RedirectResponse(f"/app/p/{p['id']}/home", 303)
    email = f"demo-{secrets.token_hex(6)}@demo.missionpilot"
    uid = db.ex("""INSERT INTO users(email,name,password_hash,interface_locale,ai_locale,doc_locale,report_locale,is_demo,created_at)
                   VALUES(?,?,?,?,?,?,?,1,?)""", (email, i18n.t("demo.user", loc), hash_pw(secrets.token_hex(8)), loc, loc, loc, loc, db.now()))
    pid = planner.seed_demo(uid, loc)
    _check(pid, send=False)
    db.ex("UPDATE users SET current_project_id=? WHERE id=?", (pid, uid))
    request.session.clear()
    request.session["uid"] = uid
    request.session["lang"] = loc
    return RedirectResponse(f"/app/p/{pid}/home?tour=1", 303)


# ---------------------------------------------------------------- app pages

@login_required
async def app_home(request: Request, user):
    pid = user["current_project_id"]
    if pid and owned_project(user, pid):
        return RedirectResponse(f"/app/p/{pid}/home", 303)
    last = db.q1("SELECT id FROM projects WHERE owner_id=? ORDER BY id DESC", (user["id"],))
    if last:
        return RedirectResponse(f"/app/p/{last['id']}/home", 303)
    return RedirectResponse("/app/new", 303)


@login_required
async def projects_page(request: Request, user):
    loc = locale_of(request, user)
    rows = []
    for p in db.q("SELECT * FROM projects WHERE owner_id=? ORDER BY id DESC", (user["id"],)):
        d = A.load(p["id"], loc)
        rows.append(d)
    return page(request, "projects.html", user, loc, active="projects", items=rows)


@login_required
async def new_page(request: Request, user):
    loc = locale_of(request, user)
    return page(request, "new.html", user, loc, active="projects", examples=i18n.tl("new.examples", loc))


@project_page
async def p_home(request, user, loc, pid, data):
    acts = db.q("SELECT a.*, t.title_key, t.title_text, t.id tid FROM activity a LEFT JOIN tasks t ON t.id=a.task_id "
                "WHERE a.project_id=? ORDER BY a.at DESC LIMIT 8", (pid,))
    tmap = {t["id"]: t for t in data["tasks"]}
    recent = [dict(at=a["at"], kind=a["kind"], task=tmap.get(a["task_id"]), old=a["old_value"], new=a["new_value"]) for a in acts]
    active = sorted([t for t in data["tasks"] if not t["milestone"] and t["start"] <= data["metrics"]["today"] and t["progress"] < 100],
                    key=lambda t: t["end"])[:8]
    bars = charts.progress_bars([dict(label=t["title"], progress=t["progress"], days=t["days"],
                                      overdue=t["end"] < data["metrics"]["today"]) for t in active], loc, width=620) if active else ""
    hist = A.history(pid, data)
    return page(request, "home.html", user, loc, pid=pid, active="home", d=data, m=data["metrics"], recent=recent, v=data["verdict"],
                bars=bars, scurve=charts.scurve(hist, loc, width=600), status_svg=charts.status_bar(data["metrics"], loc),
                created=request.query_params.get("created"))


@project_page
async def p_tasks(request, user, loc, pid, data):
    view = request.query_params.get("view", "list")
    flt = request.query_params.get("f", "all")
    today = data["metrics"]["today"]
    tasks = data["tasks"]
    if flt == "active":
        tasks = [t for t in tasks if 0 < t["progress"] < 100 or (t["start"] <= today and t["progress"] < 100)]
    elif flt == "overdue":
        tasks = [t for t in tasks if t["end"] < today and t["progress"] < 100]
    elif flt == "week":
        tasks = data["metrics"]["due_week"]
    cats = {x["id"]: ml.suggest_category(x["canonical_title"] or x["title"]) for x in tasks if not x["milestone"]} if ml.available() else {}
    return page(request, "tasks.html", user, loc, pid=pid, active="tasks", d=data, tasks=tasks, view=view, f=flt, cats=cats,
                can_translate=ai.available() and loc != data["project"]["canonical_locale"])


@project_page
async def p_gantt(request, user, loc, pid, data):
    scale = request.query_params.get("scale", "week")
    if scale not in ("day", "week", "month"):
        scale = "week"
    return page(request, "gantt.html", user, loc, pid=pid, active="gantt", d=data, scale=scale,
                svg=charts.gantt(data, loc, scale), legend=charts.gantt_legend(loc))


@project_page
async def p_progress(request, user, loc, pid, data):
    flt = request.query_params.get("f", "active")
    today = data["metrics"]["today"]
    work = [t for t in data["tasks"] if not t["milestone"]]
    if flt == "active":
        sel = [t for t in work if t["start"] <= today and t["progress"] < 100] or work[:12]
    elif flt == "overdue":
        sel = [t for t in work if t["end"] < today and t["progress"] < 100]
    elif flt == "done":
        sel = [t for t in work if t["progress"] >= 100]
    else:
        sel = work
    rows = [dict(label=f'{t["wbs"]} {t["title"]}', progress=t["progress"], days=t["days"],
                 overdue=t["end"] < today and t["progress"] < 100) for t in sel]
    phase_rows = []
    for ph in data["phases"]:
        pts = [t for t in work if t["phase_id"] == ph["id"]]
        if pts:
            days = sum(t["days"] for t in pts)
            prog = sum(t["days"] * t["progress"] for t in pts) / max(1, days)
            phase_rows.append(dict(label=f'{ph["seq"]}. {ph["name"]}', progress=prog, days=days, bold=True,
                                   overdue=max(t["end"] for t in pts) < today and prog < 100))
    m = data["metrics"]
    overall = charts.progress_bars([dict(label=i18n.t("progress.whole_project", loc), progress=m["progress"], days=m["days_total"], bold=True)], loc)
    # tracking lists
    finished_on = {}
    for r in db.q("SELECT task_id, MAX(at) at FROM activity WHERE project_id=? AND kind='progress' AND new_value='100' GROUP BY task_id", (pid,)):
        finished_on[r["task_id"]] = dt.date.fromisoformat(r["at"][:10])
    p_start = dt.date.fromisoformat(data["project"]["start_date"])
    p_end = max([t["end"] for t in data["tasks"]] + [p_start])
    day_of = lambda d: max(1, planner.workdays_between(p_start, d))
    total_wd = planner.workdays_between(p_start, p_end)
    today_day = min(total_wd, day_of(today)) if today >= p_start else 0
    ongoing = sorted([t for t in work if 0 < t["progress"] < 100], key=lambda t: t["end"])
    completed = sorted([dict(t, finished=finished_on.get(t["id"], t["end"]), day=day_of(finished_on.get(t["id"], t["end"])))
                        for t in work if t["progress"] >= 100], key=lambda t: t["finished"], reverse=True)
    overdue = sorted([t for t in work if t["end"] < today and t["progress"] < 100], key=lambda t: t["end"])
    upcoming = sorted([t for t in work if t["progress"] == 0 and t["end"] >= today], key=lambda t: t["start"])
    # tasks completed per week + cumulative % (from the activity log)
    hist = A.history(pid, data)
    start = min(t["start"] for t in data["tasks"]) if data["tasks"] else today
    wk = start - dt.timedelta(days=start.weekday())
    end = max(t["end"] for t in data["tasks"]) if data["tasks"] else today
    weeks = []
    while wk <= end:
        we = wk + dt.timedelta(days=6)
        done_n = sum(1 for c in completed if wk <= c["finished"] <= we)
        past = [h for h in hist if h["date"] <= we and h["actual"] is not None]
        weeks.append(dict(start=wk, done=done_n, cum_pct=past[-1]["actual"] if past and wk <= today else None, future=wk > today))
        wk += dt.timedelta(days=7)
    # per-day data for the burndown day picker (plan for every day, actual for days already passed)
    planned_end_day = {t["id"]: day_of(t["end"]) for t in work}
    finished_day = {c["id"]: c["day"] for c in completed}
    bd = dict(days=[dict(day=i + 1, date=h["date"].isoformat(), label=i18n.date(h["date"], loc),
                         plan_left=round(m["days_total"] * (1 - h["planned"] / 100), 1),
                         act_left=None if h["actual"] is None else round(m["days_total"] * (1 - h["actual"] / 100), 1))
                    for i, h in enumerate(hist)],
              tasks=[dict(id=t["id"], title=f'{t["wbs"]} {t["title"]}', who=t["assignee"], plan_day=planned_end_day[t["id"]],
                          done_day=finished_day.get(t["id"]), prio=t["priority"], progress=t["progress"]) for t in work],
              today=today_day, total=m["days_total"])
    return page(request, "progress.html", user, loc, pid=pid, active="progress", d=data, m=m, f=flt, bd_json=json.dumps(bd, ensure_ascii=False),
                bars=charts.progress_bars(rows, loc) if rows else "", phase_bars=charts.progress_bars(phase_rows, loc, width=620),
                overall=overall, scurve=charts.scurve(hist, loc, width=600), sel=sel, ongoing=ongoing, completed=completed,
                overdue=overdue, upcoming=upcoming, weekly=charts.weekly_completions(weeks, loc),
                status_svg=charts.status_bar(m, loc), burndown=charts.burndown(hist, m["days_total"], loc),
                today_day=today_day, total_wd=total_wd)


@project_page
async def p_budget(request, user, loc, pid, data):
    return page(request, "budget.html", user, loc, pid=pid, active="budget", d=data, m=data["metrics"],
                chart=charts.budget_bars(data["budget"], data["project"]["currency"], loc))


@project_page
async def p_risks(request, user, loc, pid, data):
    live = [r for r in data["risks"] if r["status"] != "cleared"]
    sugg = {r["id"]: ml.suggest_category(r["canonical_title"] or r["title"]) for r in data["risks"] if r["source"] != "auto"}
    dups = ml.duplicates([dict(id=r["id"], code=r["code"], title=r["title"], text=r["canonical_title"] or r["title"])
                          for r in data["risks"] if r["status"] not in ("closed", "cleared")])
    order = {"open": 0, "mitigated": 1, "closed": 2, "cleared": 3}
    return page(request, "risks.html", user, loc, pid=pid, active="risks", d=data,
                matrix=charts.risk_matrix(live, loc), risks=sorted(data["risks"], key=lambda r: (order.get(r["status"], 9), -r["score"])),
                sugg=sugg, dups=dups, ml_status=ml.status())


@project_page
async def p_team(request, user, loc, pid, data):
    wl = A.workload(data)
    over = [(r["member"], wl["weeks"][i], h) for r in wl["rows"] for i, h in enumerate(r["hours"]) if r["over"][i]]
    return page(request, "team.html", user, loc, pid=pid, active="team", d=data, wl=wl, over=over,
                heat=charts.workload_heatmap(wl, loc))


def _history(pid, kind):
    return db.q("SELECT id,doc_type,locale,version,engine,created_at,share_token FROM documents WHERE project_id=? AND kind=? ORDER BY id DESC LIMIT 60",
                (pid, kind))


@project_page
async def p_reports(request, user, loc, pid, data):
    return page(request, "reports.html", user, loc, pid=pid, active="reports", d=data, types=L.REPORT_TYPES,
                history=_history(pid, "report"), out_loc=pref(user, "report_locale", loc), today=db.today().isoformat())


@project_page
async def p_documents(request, user, loc, pid, data):
    register = []
    today = data["metrics"]["today"]
    for dtp in L.DOC_TYPES:
        task = next((t for t in data["tasks"] if t["deliverable"] == dtp), None)
        st = "todo"
        if task:
            st = "done" if task["progress"] >= 100 else ("overdue" if task["end"] < today else ("in_progress" if task["progress"] else "todo"))
        last = db.q1("SELECT id,locale,version FROM documents WHERE project_id=? AND kind='doc' AND doc_type=? ORDER BY id DESC", (pid, dtp))
        register.append(dict(type=dtp, task=task, status=st, last=last))
    return page(request, "documents.html", user, loc, pid=pid, active="documents", d=data, register=register,
                history=_history(pid, "doc"), out_loc=pref(user, "doc_locale", loc))


@project_page
async def p_assistant(request, user, loc, pid, data):
    msgs = db.q("SELECT * FROM chat_messages WHERE project_id=? ORDER BY id DESC LIMIT 40", (pid,))[::-1]
    return page(request, "assistant.html", user, loc, pid=pid, active="assistant", d=data, msgs=msgs,
                out_loc=pref(user, "ai_locale", loc), examples=i18n.tl("assistant.examples", loc))


@login_required
async def settings_page(request: Request, user):
    loc = locale_of(request, user)
    saved = False
    if request.method == "POST":
        f = await request.form()
        vals = dict(
            name=str(f.get("name", user["name"]))[:80] or user["name"],
            interface_locale=i18n.norm(f.get("interface_locale")), ai_locale=i18n.norm(f.get("ai_locale")),
            doc_locale=i18n.norm(f.get("doc_locale")), report_locale=i18n.norm(f.get("report_locale")),
            date_style=f.get("date_style") if f.get("date_style") in ("short", "medium", "long") else "medium",
            time_zone=str(f.get("time_zone", "Europe/Paris"))[:60], week_start=1 if f.get("week_start") == "1" else 0,
            voice_enabled=1 if f.get("voice_enabled") else 0, voice_name=str(f.get("voice_name", ""))[:120],
            voice_rate=min(1.5, max(0.6, float(f.get("voice_rate") or 1))),
            email_notify=1 if f.get("email_notify") else 0, weekly_report=1 if f.get("weekly_report") else 0)
        db.ex("UPDATE users SET " + ",".join(f"{k}=?" for k in vals) + " WHERE id=?", (*vals.values(), user["id"]))
        if f.get("restart_tour"):
            db.ex("UPDATE users SET onboarding_completed_at=NULL, onboarding_step=0 WHERE id=?", (user["id"],))
        request.session["lang"] = vals["interface_locale"]
        user = current_user(request)
        loc = vals["interface_locale"]
        saved = True
    cov = []
    en_keys = set(i18n.catalog("en"))
    for c in i18n.CODES:
        keys = set(i18n.catalog(c))
        cov.append(dict(code=c, name=i18n.LOCALES[c]["name"], pct=100 * len(keys & en_keys) / max(1, len(en_keys)),
                        status=i18n.meta(c).get("status", "machine")))
    missing = db.q("SELECT * FROM missing_translations ORDER BY count DESC LIMIT 30")
    pids = [p["id"] for p in db.q("SELECT id FROM projects WHERE owner_id=?", (user["id"],))]
    return page(request, "settings.html", user, loc, active="settings", saved=saved, coverage=cov, missing=missing,
                ai_model=ai.MODEL if ai.available() else None, pdf_error=X.pdf_engine_error,
                mail_cfg=dict(on=mailer.configured(), to=mailer.admin_to() if user["is_demo"] else user["email"]),
                mail_log=mailer.recent(25, pids, user["id"]), ml_status=ml.status())


@login_required
async def help_page(request: Request, user):
    loc = locale_of(request, user)
    return page(request, "help.html", user, loc, active="help")


# ---------------------------------------------------------------- JSON API

async def body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return dict(await request.form())


def jerr(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


async def api_lang(request: Request):
    b = await body(request)
    lang = i18n.norm(b.get("lang"))
    request.session["lang"] = lang
    user = current_user(request)
    if user:
        db.ex("UPDATE users SET interface_locale=? WHERE id=?", (lang, user["id"]))
    return JSONResponse({"ok": True, "lang": lang})


def _budget_presets(currency: str, loc: str):
    base = {"EUR": [5000, 20000, 50000, 120000], "USD": [5000, 20000, 50000, 120000], "GBP": [5000, 20000, 50000, 100000],
            "CHF": [5000, 20000, 50000, 120000], "INR": [200000, 500000, 1500000, 5000000], "JPY": [500000, 2000000, 5000000, 15000000],
            "CNY": [30000, 150000, 400000, 1000000], "AED": [20000, 80000, 200000, 500000], "BRL": [20000, 100000, 250000, 600000]}
    return [dict(value=v, label=i18n.money_minor(planner.to_minor(v, currency), currency, loc)) for v in base.get(currency, base["EUR"])]


async def api_questions(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    b = await body(request)
    loc = i18n.norm(b.get("lang") or user["interface_locale"])
    prompt = str(b.get("prompt", "")).strip()[:4000]
    if len(prompt) < 3:
        return jerr(i18n.t("new.too_short", loc))
    name, desc = planner.split_name(prompt)
    ptype = planner.detect_type(prompt)
    currency = b.get("currency") if b.get("currency") in i18n.CURRENCIES else i18n.LOCALES[loc]["currency"]
    t = lambda k, **kw: i18n.t(k, loc, **kw)
    nxt_mon = planner.next_workday(db.today() + dt.timedelta(days=(7 - db.today().weekday()) % 7 or 7))
    qs = [
        dict(id="budget", header=t("q.budget_h"), question=t("q.budget"), kind="money", currency=currency,
             options=[dict(value=o["value"], label=o["label"]) for o in _budget_presets(currency, loc)], allow_other=True),
        dict(id="team_size", header=t("q.team_h"), question=t("q.team"), kind="int",
             options=[dict(value=v, label=t("q.people", n=v)) for v in (1, 2, 3, 4, 5, 6)], allow_other=True),
        dict(id="members", header=t("q.members_h"), question=t("q.members"), kind="team", options=[], allow_other=False,
             roles=[dict(value=k, label=t(f"role.{full}")) for k, full in L.ROLES.items()],
             suggested={pt: planner.suggested_roles(pt, 40) for pt in L.PROJECT_TYPES}),
        dict(id="duration_weeks", header=t("q.duration_h"), question=t("q.duration"), kind="int",
             options=[dict(value=v, label=t("q.weeks", n=v)) for v in (4, 8, 12, 24)], allow_other=True, other_unit=t("q.weeks_unit")),
        dict(id="type", header=t("q.type_h"), question=t("q.type"), kind="choice",
             options=[dict(value=pt, label=t(f"ptype.{pt}"), recommended=pt == ptype) for pt in
                      ([ptype] + [x for x in L.PROJECT_TYPES if x != ptype])], allow_other=False),
        dict(id="start_date", header=t("q.start_h"), question=t("q.start"), kind="date",
             options=[dict(value=planner.next_workday(db.today()).isoformat(), label=t("q.today")),
                      dict(value=nxt_mon.isoformat(), label=t("q.next_monday")),
                      dict(value=planner.next_workday(db.today() + dt.timedelta(days=14)).isoformat(), label=t("q.in_two_weeks"))],
             allow_other=True),
    ]
    extra = ai.questions(prompt, loc) if ai.available() else None
    for q in extra or []:
        q["options"] = [dict(value=o, label=o) for o in q["options"]]
        qs.append(q)
    return JSONResponse({"name": name, "description": desc, "type": ptype, "currency": currency, "questions": qs,
                         "engine": "ai" if extra else "template"})


async def api_create(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    b = await body(request)
    loc = i18n.norm(b.get("lang") or user["interface_locale"])
    try:
        start = dt.date.fromisoformat(str(b.get("start_date"))[:10])
    except ValueError:
        start = db.today()
    members_in = b.get("members") if isinstance(b.get("members"), list) else []
    if members_in:
        names = [str(m.get("name", "")).strip()[:80] for m in members_in if isinstance(m, dict)]
        roles = [str(m.get("role", "")) for m in members_in if isinstance(m, dict)]
    else:
        names = [n.strip() for n in str(b.get("member_names", "")).replace(";", ",").replace("\n", ",").split(",") if n.strip()]
        roles = []
    answers = dict(
        name=str(b.get("name", "")).strip()[:120] or i18n.t("new.untitled", loc),
        description=str(b.get("description", "")).strip()[:2000],
        type=b.get("type") if b.get("type") in L.PROJECT_TYPES else "generic",
        budget=max(0.0, float(b.get("budget") or 0)), currency=b.get("currency") if b.get("currency") in i18n.CURRENCIES else "EUR",
        team_size=max(1, min(40, int(float(b.get("team_size") or 3)))), member_names=names[:40], member_roles=roles[:40],
        duration_weeks=max(2, min(104, int(float(b.get("duration_weeks") or 8)))), start_date=start.isoformat(), locale=loc,
        extra={k: v for k, v in b.items() if str(k).startswith("x")})
    plan = None
    if ai.available() and b.get("use_ai", True):
        plan = ai.plan(answers, loc)
        if plan and not plan.get("risks"):
            plan["risks"] = planner.template_plan(answers["type"])["risks"]
    pid = planner.create_project(user["id"], answers, plan)
    _check(pid, send=False)
    db.ex("UPDATE users SET current_project_id=? WHERE id=?", (pid, user["id"]))
    return JSONResponse({"project_id": pid, "url": f"/app/p/{pid}/home?created=1", "engine": "ai" if plan else "template"})


def _task_owned(user, tid):
    return db.q1("SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.id=? AND p.owner_id=?", (tid, user["id"]))


async def api_task(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    t = _task_owned(user, int(request.path_params["tid"]))
    if not t:
        return jerr("not found", 404)
    b = await body(request)
    prog, status = t["progress"], t["status"]
    old_status, old_end, old_assignee = t["status"], dt.date.fromisoformat(t["end_date"]), t["assignee_id"]
    before = {r["id"]: r["end_date"] for r in db.q("SELECT id,end_date FROM tasks WHERE project_id=?", (t["project_id"],))}
    if "progress" in b:
        prog = max(0, min(100, int(float(b["progress"]))))
        status = A.status_for(prog, t["status"])
    if b.get("status") in ("todo", "in_progress", "done", "blocked"):
        status = b["status"]
        if status == "done":
            prog = 100
        elif status == "todo":
            prog = 0
        elif status == "in_progress" and prog in (0, 100):
            prog = 10 if prog == 0 else 90
    if b.get("priority") in ("low", "medium", "high", "critical"):
        db.ex("UPDATE tasks SET priority=? WHERE id=?", (b["priority"], t["id"]))
    if prog != t["progress"]:
        A.log_progress(t["project_id"], user["id"], t["id"], t["progress"], prog)
    db.ex("UPDATE tasks SET progress=?, status=?, updated_at=? WHERE id=?", (prog, status, db.now(), t["id"]))
    if b.get("end_date"):
        try:
            A.reschedule_from(t["project_id"], t["id"], dt.date.fromisoformat(str(b["end_date"])[:10]))
        except ValueError:
            pass
    if "title" in b and str(b["title"]).strip():
        db.ex("UPDATE tasks SET title_text=?, title_key=NULL WHERE id=?", (str(b["title"]).strip()[:200], t["id"]))
    assigned = False
    if b.get("assignee_id"):
        if db.q1("SELECT 1 FROM members WHERE id=? AND project_id=?", (int(b["assignee_id"]), t["project_id"])):
            db.ex("UPDATE tasks SET assignee_id=? WHERE id=?", (int(b["assignee_id"]), t["id"]))
            assigned = int(b["assignee_id"]) != old_assignee
            if assigned:
                db.ex("INSERT INTO activity(project_id,user_id,at,kind,task_id,old_value,new_value) VALUES(?,?,?,?,?,?,?)",
                      (t["project_id"], user["id"], db.now(), "assigned", t["id"], str(old_assignee or ""), str(b["assignee_id"])))
    try:  # real-time e-mails (sent in the background)
        pid_ = t["project_id"]
        if assigned:
            notify.task_assigned(pid_, t["id"], old_assignee, user["id"])
        new_end = db.q1("SELECT end_date FROM tasks WHERE id=?", (t["id"],))["end_date"]
        if new_end != old_end.isoformat():
            after = {r["id"]: r["end_date"] for r in db.q("SELECT id,end_date FROM tasks WHERE project_id=?", (pid_,))}
            moved = sum(1 for k, v in after.items() if k != t["id"] and before.get(k) != v)
            db.ex("INSERT INTO activity(project_id,user_id,at,kind,task_id,old_value,new_value) VALUES(?,?,?,?,?,?,?)",
                  (pid_, user["id"], db.now(), "deadline", t["id"], old_end.isoformat(), new_end))
            notify.deadline_changed(pid_, t["id"], old_end, moved, user["id"])
        if status == "blocked" and old_status != "blocked":
            notify.task_blocked(pid_, t["id"], user["id"])
    except Exception as e:  # pragma: no cover
        print("notify failed", e)
    _check(t["project_id"])
    loc = locale_of(request, user)
    m = A.load(t["project_id"], loc)["metrics"]
    return JSONResponse({"ok": True, "progress": prog, "status": status, "status_label": i18n.t(f"status.{status}", loc),
                         "project_progress": round(m["progress"], 1), "project_progress_label": i18n.pct(m["progress"], loc)})


async def api_budget(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    b = await body(request)
    line = db.q1("SELECT b.*, p.currency FROM budget_lines b JOIN projects p ON p.id=b.project_id WHERE b.id=? AND p.owner_id=?",
                 (int(request.path_params["bid"]), user["id"]))
    if not line:
        return jerr("not found", 404)
    for f in ("actual", "planned"):
        if f in b:
            try:
                db.ex(f"UPDATE budget_lines SET {f}_minor=? WHERE id=?", (planner.to_minor(max(0.0, float(b[f])), line["currency"]), line["id"]))
            except (TypeError, ValueError):
                return jerr("bad number")
    db.ex("INSERT INTO activity(project_id,user_id,at,kind,new_value) VALUES(?,?,?,?,?)", (line["project_id"], user["id"], db.now(), "budget", line["category"]))
    _check(line["project_id"])
    return JSONResponse({"ok": True})


async def api_risk(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    b = await body(request)
    r = db.q1("SELECT r.* FROM risks r JOIN projects p ON p.id=r.project_id WHERE r.id=? AND p.owner_id=?", (int(request.path_params["rid"]), user["id"]))
    if not r:
        return jerr("not found", 404)
    if b.get("status") in ("open", "mitigated", "closed"):
        db.ex("UPDATE risks SET status=?, updated_at=? WHERE id=?", (b["status"], db.now(), r["id"]))
    if b.get("evidence") in ("hypothesis", "signal", "confirmed"):  # only a person can confirm a risk
        db.ex("UPDATE risks SET evidence=? WHERE id=?", (b["evidence"], r["id"]))
    if b.get("category") in ("scope", "schedule", "cost", "resource", "technical", "security", "compliance", "external"):
        db.ex("UPDATE risks SET category=? WHERE id=?", (b["category"], r["id"]))
    old_score = r["probability"] * r["impact"]
    for f in ("probability", "impact"):
        if f in b:
            db.ex(f"UPDATE risks SET {f}=? WHERE id=?", (max(1, min(5, int(b[f]))), r["id"]))
    n = db.q1("SELECT probability*impact s FROM risks WHERE id=?", (r["id"],))["s"]
    if n >= 12 > old_score:
        notify.risk_raised(r["project_id"], r["id"], user["id"])
    return JSONResponse({"ok": True})


async def api_member(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    b = await body(request)
    m = db.q1("SELECT m.* FROM members m JOIN projects p ON p.id=m.project_id WHERE m.id=? AND p.owner_id=?", (int(request.path_params["mid"]), user["id"]))
    if not m:
        return jerr("not found", 404)
    if "name" in b:
        db.ex("UPDATE members SET name=? WHERE id=?", (str(b["name"]).strip()[:80], m["id"]))
    if "capacity" in b:
        db.ex("UPDATE members SET capacity_pct=? WHERE id=?", (max(10, min(100, int(b["capacity"]))), m["id"]))
    if "email" in b:
        em = str(b["email"]).strip()[:120]
        if em and not mailer.valid(em):
            return jerr(i18n.t("mail.bad_address", locale_of(request, user)))
        db.ex("UPDATE members SET email=? WHERE id=?", (em, m["id"]))
    if "capacity" in b:
        _check(m["project_id"])
    return JSONResponse({"ok": True})


async def api_project_delete(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    pid = int(request.path_params["pid"])
    if not owned_project(user, pid):
        return jerr("not found", 404)
    db.ex("DELETE FROM projects WHERE id=?", (pid,))
    db.ex("UPDATE users SET current_project_id=NULL WHERE id=?", (user["id"],))
    return JSONResponse({"ok": True})


async def api_tour(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    b = await body(request)
    if b.get("done"):
        db.ex("UPDATE users SET onboarding_completed_at=?, onboarding_step=0 WHERE id=?", (db.now(), user["id"]))
    elif "step" in b:
        db.ex("UPDATE users SET onboarding_step=? WHERE id=?", (max(0, int(b["step"])), user["id"]))
    if b.get("restart"):
        db.ex("UPDATE users SET onboarding_completed_at=NULL, onboarding_step=0 WHERE id=?", (user["id"],))
    return JSONResponse({"ok": True})


async def api_demo_reset(request: Request):
    user = current_user(request)
    if not user or not user["is_demo"]:
        return jerr("demo only", 403)
    loc = locale_of(request, user)
    db.ex("DELETE FROM projects WHERE owner_id=?", (user["id"],))
    pid = planner.seed_demo(user["id"], loc)
    _check(pid, send=False)
    db.ex("UPDATE users SET current_project_id=? WHERE id=?", (pid, user["id"]))
    return JSONResponse({"ok": True, "url": f"/app/p/{pid}/home"})


# ---------------------------------------------------------------- AI assistant

def _local_answer(data: dict, msg: str, loc: str) -> str:
    """Answers from project data when the Claude API is not configured."""
    t = lambda k, **kw: i18n.t(k, loc, **kw)
    m = data["metrics"]
    low = msg.lower()
    words = lambda key: [w.lower() for w in i18n.tl(key, loc)] + [w.lower() for w in i18n.tl(key, "en")]
    has = lambda key: any(w in low for w in words(key))
    lines = []
    if has("assistant.kw.overdue"):
        od = m["overdue"]
        lines.append(t("assistant.a.overdue", n=len(od)) if od else t("report.none_missed"))
        lines += [f"• {x['title']} — {x['assignee']} ({i18n.date(x['end'], loc)}, {i18n.pct(x['progress'], loc)})" for x in od[:8]]
    if has("assistant.kw.budget"):
        lines.append(t("assistant.a.budget", used=i18n.money_minor(m["budget_actual"], data["project"]["currency"], loc),
                       total=i18n.money_minor(m["budget_planned"], data["project"]["currency"], loc),
                       cpi=i18n.num(m["cpi"], loc, 2) if m["cpi"] else "—"))
    if has("assistant.kw.risk"):
        top = sorted([r for r in data["risks"] if r["status"] == "open"], key=lambda r: -r["score"])[:5]
        lines.append(t("assistant.a.risks", n=m["open_risks"]))
        lines += [f"• {r['code']} {r['title']} — {r['mitigation']}" for r in top]
    if has("assistant.kw.next"):
        nxt = sorted([x for x in data["tasks"] if x["progress"] < 100 and not x["milestone"]], key=lambda x: x["start"])[:6]
        lines.append(t("assistant.a.next"))
        lines += [f"• {x['title']} — {x['assignee']} ({i18n.date(x['start'], loc)} → {i18n.date(x['end'], loc)})" for x in nxt]
    if has("assistant.kw.team"):
        wl = A.workload(data)
        lines.append(t("assistant.a.team"))
        lines += [f"• {r['member']['name']} ({r['member']['role']}): {i18n.num(r['total'], loc)} h" for r in wl["rows"]]
    if has("assistant.kw.docs"):
        lines.append(t("assistant.a.docs"))
    if not lines or has("assistant.kw.status"):
        ms = m["next_milestone"]
        lines.insert(0, t("assistant.a.status", progress=i18n.pct(m["progress"], loc), planned=i18n.pct(m["planned"], loc),
                          done=m["tasks_done"], total=m["tasks_total"], overdue=len(m["overdue"]),
                          health=t(f"health.{m['health']}"),
                          milestone=(ms["title"] + " (" + i18n.date(ms["end"], loc) + ")") if ms else "—"))
    if not ai.available():
        lines.append("")
        lines.append(t("assistant.local_note"))
    return "\n".join(lines)


async def api_chat(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    pid = int(request.path_params["pid"])
    if not owned_project(user, pid):
        return jerr("not found", 404)
    b = await body(request)
    msg = str(b.get("message", "")).strip()[:4000]
    if not msg:
        return jerr("empty")
    out_loc = i18n.norm(b.get("lang") or user["ai_locale"] or user["interface_locale"])
    data = A.load(pid, out_loc)
    db.ex("INSERT INTO chat_messages(project_id,user_id,role,content,locale,created_at) VALUES(?,?,?,?,?,?)", (pid, user["id"], "user", msg, out_loc, db.now()))
    reply, engine = None, "local"
    if ai.available():
        hist = [dict(role=r["role"], content=r["content"]) for r in
                db.q("SELECT role,content FROM chat_messages WHERE project_id=? ORDER BY id DESC LIMIT 11", (pid,))][::-1][:-1]
        reply = ai.chat(DOCS._context_text(DOCS.Ctx(data, out_loc)), hist, msg, out_loc)
        engine = "ai" if reply else "local"
    if not reply:
        reply = _local_answer(data, msg, out_loc)
    db.ex("INSERT INTO chat_messages(project_id,user_id,role,content,locale,created_at) VALUES(?,?,?,?,?,?)", (pid, user["id"], "assistant", reply, out_loc, db.now()))
    flush_missing()
    return JSONResponse({"reply": reply, "engine": engine, "dir": i18n.info(out_loc)["dir"], "lang": out_loc})


async def api_translate(request: Request):
    """Machine-translate the project's free text (AI-written or user-written) into a language; never edits the original."""
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    pid = int(request.path_params["pid"])
    p = owned_project(user, pid)
    if not p:
        return jerr("not found", 404)
    b = await body(request)
    target = i18n.norm(b.get("lang"))
    if target == p["canonical_locale"]:
        return JSONResponse({"ok": True, "count": 0})
    if not ai.available():
        return jerr(i18n.t("assistant.needs_key", target))
    items = []
    for table, etype, fields in (("tasks", "task", ("title", "desc")), ("phases", "phase", ("name",)), ("risks", "risk", ("title", "mitigation"))):
        for r in db.q(f"SELECT * FROM {table} WHERE project_id=?", (pid,)):
            for f in fields:
                if r[f"{f}_key"] if f"{f}_key" in r.keys() else False:
                    continue
                txt = r[f"{f}_text"]
                if not txt:
                    continue
                ex = db.q1("SELECT source_hash,status FROM content_translations WHERE entity_type=? AND entity_id=? AND field=? AND locale=?",
                           (etype, r["id"], f, target))
                if ex and ex["source_hash"] == A.src_hash(txt):
                    continue
                items.append((etype, r["id"], f, txt))
    done = 0
    for i in range(0, len(items), 40):
        chunk = items[i:i + 40]
        out = ai.translate([x[3] for x in chunk], target)
        if not out:
            break
        for (etype, eid, f, txt), tr in zip(chunk, out):
            db.ex("""INSERT INTO content_translations(entity_type,entity_id,field,locale,text,source_hash,status,translated_by,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,'machine','machine',?,?)
                     ON CONFLICT(entity_type,entity_id,field,locale) DO UPDATE SET text=excluded.text, source_hash=excluded.source_hash,
                     status='machine', updated_at=excluded.updated_at""", (etype, eid, f, target, tr, A.src_hash(txt), db.now(), db.now()))
            done += 1
    return JSONResponse({"ok": True, "count": done, "total": len(items)})


# ---------------------------------------------------------------- documents & reports

async def api_generate(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    pid = int(request.path_params["pid"])
    if not owned_project(user, pid):
        return jerr("not found", 404)
    b = await body(request)
    kind = b.get("kind", "doc")
    dtype = b.get("type")
    out_loc = i18n.norm(b.get("lang"))
    data = A.load(pid, out_loc)
    if kind == "doc" and dtype in L.DOC_TYPES:
        content = DOCS.build_document(dtype, data, out_loc, use_ai=bool(b.get("ai")) and ai.available())
    elif kind == "report" and dtype in L.REPORT_TYPES:
        try:
            on = dt.date.fromisoformat(str(b.get("date"))[:10])
        except ValueError:
            on = db.today()
        content = DOCS.build_report(dtype, data, out_loc, on)
    else:
        return jerr("bad type")
    doc_id = DOCS.save(pid, user["id"], content)
    flush_missing()
    return JSONResponse({"doc_id": doc_id, "url": f"/app/doc/{doc_id}"})


def _doc_access(request: Request, doc_id: int):
    r = DOCS.get(doc_id)
    if not r:
        return None
    row, content = r
    tok = request.query_params.get("token")
    if tok and row["share_token"] and hmac.compare_digest(tok, row["share_token"]):
        return row, content
    user = current_user(request)
    if user and owned_project(user, row["project_id"]):
        return row, content
    return None


@login_required
async def doc_view(request: Request, user):
    doc_id = int(request.path_params["doc_id"])
    r = _doc_access(request, doc_id)
    if not r:
        return RedirectResponse("/app", 303)
    row, content = r
    loc = locale_of(request, user)
    others = db.q("SELECT id,locale,version,created_at FROM documents WHERE project_id=? AND kind=? AND doc_type=? ORDER BY id DESC LIMIT 20",
                  (row["project_id"], row["kind"], row["doc_type"]))
    return page(request, "doc_view.html", user, loc, pid=row["project_id"], active="documents" if row["kind"] == "doc" else "reports",
                row=row, content=content, html=render.document_html(content), css=render.DOC_CSS, others=others,
                share_url=_share_url(request, row) if row["share_token"] else "")


def _base_url(request: Request) -> str:
    return PUBLIC_URL or f"{request.url.scheme}://{request.url.netloc}"


def _share_url(request, row):
    return f"{_base_url(request)}/s/{row['share_token']}"


def _filename(content: dict, ext: str) -> str:
    return f"{X.safe_name(content['project'])}_{content['type']}_{content['locale']}_v{content.get('version', 1)}.{ext}"


def _attach(data: bytes, filename: str, media: str) -> Response:
    ascii_name = filename.encode("ascii", "ignore").decode() or "file"
    return Response(data, media_type=media, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"})


async def doc_file(request: Request):
    doc_id = int(request.path_params["doc_id"])
    ext = request.path_params["ext"]
    r = _doc_access(request, doc_id)
    if not r:
        return PlainTextResponse("Not found", 404)
    row, content = r
    if ext == "pdf":
        data = await X.pdf_bytes(content)
        if data is None:  # no Chromium on this machine: let the browser print it
            return HTMLResponse(render.standalone(content, for_print=False, auto_print=True))
        return _attach(data, _filename(content, "pdf"), "application/pdf")
    if ext == "docx":
        return _attach(await X.docx_bytes(content), _filename(content, "docx"),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    if ext == "html":
        return HTMLResponse(render.standalone(content, for_print=False))
    return PlainTextResponse("Not found", 404)


async def api_share(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    doc_id = int(request.path_params["doc_id"])
    r = DOCS.get(doc_id)
    if not r or not owned_project(user, r[0]["project_id"]):
        return jerr("not found", 404)
    row = r[0]
    tok = row["share_token"] or secrets.token_urlsafe(18)
    if not row["share_token"]:
        db.ex("UPDATE documents SET share_token=? WHERE id=?", (tok, doc_id))
    url = f"{_base_url(request)}/s/{tok}"
    return JSONResponse({"url": url, "title": r[1]["title"], "project": r[1]["project"]})


async def api_unshare(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    doc_id = int(request.path_params["doc_id"])
    r = DOCS.get(doc_id)
    if not r or not owned_project(user, r[0]["project_id"]):
        return jerr("not found", 404)
    db.ex("UPDATE documents SET share_token=NULL WHERE id=?", (doc_id,))
    return JSONResponse({"ok": True})


async def shared(request: Request):
    tok = request.path_params["token"]
    row = db.q1("SELECT * FROM documents WHERE share_token=?", (tok,))
    if not row:
        return PlainTextResponse("This link is no longer shared.", 404)
    content = json.loads(row["content_json"])
    loc = content["locale"]
    return page(request, "share.html", None, loc, content=content, html=render.document_html(content), css=render.DOC_CSS,
                doc_id=row["id"], token=tok)


async def project_file(request: Request):
    user = current_user(request)
    if not user:
        return PlainTextResponse("Login required", 401)
    pid = int(request.path_params["pid"])
    if not owned_project(user, pid):
        return PlainTextResponse("Not found", 404)
    name = request.path_params["name"]
    loc = i18n.norm(request.query_params.get("lang") or user["interface_locale"])
    data = A.load(pid, loc)
    pname = X.safe_name(data["project"]["display_name"])
    xl = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if name == "gantt.xml":
        return _attach(X.mspdi(data, loc), f"{pname}_MSProject_{loc}.xml", "application/xml")
    if name in ("gantt.xlsx", "tasks.xlsx", "budget.xlsx", "risks.xlsx", "team.xlsx", "project.xlsx"):
        what = {"gantt.xlsx": "gantt", "tasks.xlsx": "tasks", "budget.xlsx": "budget", "risks.xlsx": "risks", "team.xlsx": "team",
                "project.xlsx": "all"}[name]
        return _attach(X.xlsx(data, loc, what), f"{pname}_{what}_{loc}.xlsx", xl)
    if name in ("gantt.pdf", "progress.pdf"):
        t = lambda k: i18n.t(k, loc)
        if name == "gantt.pdf":
            blocks = [{"t": "chart", "svg": charts.gantt(data, loc, request.query_params.get("scale", "week")), "caption": ""},
                      {"t": "chart", "svg": charts.gantt_legend(loc), "caption": ""}]
            title = t("gantt.title")
        else:
            work = [x for x in data["tasks"] if not x["milestone"]]
            rows = [dict(label=f'{x["wbs"]} {x["title"]}', progress=x["progress"], days=x["days"],
                         overdue=x["end"] < data["metrics"]["today"] and x["progress"] < 100) for x in work]
            blocks = [{"t": "h1", "text": t("progress.overall")},
                      {"t": "chart", "svg": charts.progress_bars([dict(label=t("progress.whole_project"), progress=data["metrics"]["progress"],
                                                                       days=data["metrics"]["days_total"], bold=True)], loc), "caption": ""},
                      {"t": "h1", "text": t("progress.burndown_title")},
                      {"t": "chart", "svg": charts.burndown(A.history(pid, data), data["metrics"]["days_total"], loc), "caption": ""},
                      {"t": "h1", "text": t("progress.scurve_title")},
                      {"t": "chart", "svg": charts.scurve(A.history(pid, data), loc), "caption": ""},
                      {"t": "h1", "text": t("progress.by_task")},
                      {"t": "chart", "svg": charts.progress_bars(rows, loc), "caption": ""}]
            title = t("progress.title")
        content = DOCS._wrap(DOCS.Ctx(data, loc), "report", name.split(".")[0], title, blocks, "template")
        pdf = await X.pdf_bytes(content)
        if pdf is None:
            return HTMLResponse(render.standalone(content, for_print=False, auto_print=True))
        return _attach(pdf, f"{pname}_{name.split('.')[0]}_{loc}.pdf", "application/pdf")
    if name == "documents.zip":
        files = []
        for dtp in L.DOC_TYPES:
            content = DOCS.build_document(dtp, data, loc)
            content["version"] = 1
            files.append((f"{pname}_{dtp}_{loc}.docx", await X.docx_bytes(content)))
            pdf = await X.pdf_bytes(content)
            if pdf:
                files.append((f"{pname}_{dtp}_{loc}.pdf", pdf))
        files.append((f"{pname}_MSProject_{loc}.xml", X.mspdi(data, loc)))
        files.append((f"{pname}_project_{loc}.xlsx", X.xlsx(data, loc, "all")))
        flush_missing()
        return _attach(X.zip_bytes(files), f"{pname}_all_documents_{loc}.zip", "application/zip")
    return PlainTextResponse("Not found", 404)


async def healthz(request: Request):
    return JSONResponse({"ok": True, "version": VERSION, "ai": ai.available(), "locales": i18n.CODES})



# ---------------------------------------------------------------- what-if simulation

def _date_param(v, default):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return default


@project_page
async def p_whatif(request, user, loc, pid, data):
    qp = request.query_params
    today = data["metrics"]["today"]
    sim = None
    mid = int(qp["member"]) if (qp.get("member") or "").isdigit() else None
    d1 = _date_param(qp.get("from"), planner.next_workday(today + dt.timedelta(days=7 - today.weekday())))
    d2 = _date_param(qp.get("to"), planner.add_workdays(d1, 4))
    if mid:
        sim = whatif.simulate(data, mid, d1, d2)
        if sim.get("error"):
            sim = None
    return page(request, "whatif.html", user, loc, pid=pid, active="whatif", d=data, sim=sim, mid=mid, d1=d1, d2=d2)


async def api_whatif_apply(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    pid = int(request.path_params["pid"])
    if not owned_project(user, pid):
        return jerr("not found", 404)
    b = await body(request)
    data = A.load(pid, "en")
    sim = whatif.simulate(data, int(b.get("member")), _date_param(b.get("from"), db.today()), _date_param(b.get("to"), db.today()))
    if sim.get("error"):
        return jerr("bad member")
    old_end = data["project"]["end_date"]
    n = whatif.apply_dates(pid, sim)
    db.ex("INSERT INTO activity(project_id,user_id,at,kind,old_value,new_value) VALUES(?,?,?,?,?,?)",
          (pid, user["id"], db.now(), "absence", sim["member"]["name"], f"{sim['d1']}→{sim['d2']}"))
    try:
        _, _, mloc = notify._context(pid)
        new_end = db.q1("SELECT end_date FROM projects WHERE id=?", (pid,))["end_date"]
        lines = [i18n.t("mail.absence", mloc, name=sim["member"]["name"], start=i18n.date(sim["d1"], mloc), end=i18n.date(sim["d2"], mloc), n=n),
                 i18n.t("mail.absence_end", mloc, old=i18n.date(old_end, mloc), new=i18n.date(new_end, mloc))]
        notify._send(pid, "absence", "mail.subject.absence", lines, f"/app/p/{pid}/gantt", member_ids=[sim["member"]["id"]],
                     user_id=user["id"], name=sim["member"]["name"])
    except Exception as e:  # pragma: no cover
        print("notify failed", e)
    _check(pid)
    return JSONResponse({"ok": True, "moved": n})


# ---------------------------------------------------------------- meeting notes -> tasks

def _current_phase(data) -> str:
    today = data["metrics"]["today"]
    act = [t for t in data["tasks"] if t["start"] <= today and t["progress"] < 100 and not t["milestone"]]
    return (min(act, key=lambda t: t["start"])["phase_code"] if act else (data["phases"][-1]["code"] if data["phases"] else "build"))


@project_page
async def p_notes(request, user, loc, pid, data):
    return page(request, "notes.html", user, loc, pid=pid, active="notes", d=data, phases=data["phases"],
                engine="claude" if ai.available() else ("rules+model" if ml.available() else "rules"))


async def api_notes_parse(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    pid = int(request.path_params["pid"])
    if not owned_project(user, pid):
        return jerr("not found", 404)
    b = await body(request)
    text = str(b.get("text", ""))[:20000]
    loc = locale_of(request, user)
    if len(text.strip()) < 5:
        return jerr(i18n.t("notes.empty", loc))
    data = A.load(pid, loc)
    items, engine = NOTES.extract(text, data["members"], db.today(), loc, _current_phase(data))
    return JSONResponse({"items": items, "engine": engine})


async def api_notes_create(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    pid = int(request.path_params["pid"])
    if not owned_project(user, pid):
        return jerr("not found", 404)
    b = await body(request)
    items = b.get("items") if isinstance(b.get("items"), list) else []
    members = {m["id"] for m in db.q("SELECT id FROM members WHERE project_id=?", (pid,))}
    created = []
    for it in items[:60]:
        if not isinstance(it, dict) or not str(it.get("title", "")).strip():
            continue
        start = planner.next_workday(db.today())
        end = _date_param(it.get("deadline"), planner.add_workdays(start, 2))
        if end < start:
            end = start
        aid = int(it["owner_id"]) if str(it.get("owner_id", "")).isdigit() and int(it["owner_id"]) in members else None
        prio = it.get("priority") if it.get("priority") in ("low", "medium", "high", "critical") else "medium"
        phase = it.get("phase") if it.get("phase") in L.PHASES else "build"
        tid = planner.insert_task(pid, phase, str(it["title"]).strip(), start, end, aid, prio)
        db.ex("INSERT INTO activity(project_id,user_id,at,kind,task_id,new_value) VALUES(?,?,?,?,?,?)",
              (pid, user["id"], db.now(), "notes_task", tid, "notes"))
        created.append(tid)
        if aid:
            try:
                notify.task_assigned(pid, tid, None, user["id"])
            except Exception as e:  # pragma: no cover
                print("notify failed", e)
    _check(pid)
    return JSONResponse({"ok": True, "created": len(created), "url": f"/app/p/{pid}/tasks"})


# ---------------------------------------------------------------- client share link (read-only)

def _link_state(row) -> str:
    if row["revoked_at"]:
        return "revoked"
    if row["expires_at"] and row["expires_at"] < db.now():
        return "expired"
    return "active"


@project_page
async def p_client(request, user, loc, pid, data):
    links = []
    for r in db.q("SELECT * FROM client_links WHERE project_id=? ORDER BY id DESC", (pid,)):
        views = db.q("SELECT * FROM client_link_views WHERE link_id=? ORDER BY id DESC LIMIT 20", (r["id"],))
        n = db.q1("SELECT COUNT(*) n FROM client_link_views WHERE link_id=?", (r["id"],))["n"]
        links.append(dict(row=r, state=_link_state(r), url=f"{_base_url(request)}/c/{r['token']}", views=views, n=n))
    return page(request, "client.html", user, loc, pid=pid, active="home", d=data, links=links)


async def api_client_link(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    pid = int(request.path_params["pid"])
    if not owned_project(user, pid):
        return jerr("not found", 404)
    b = await body(request)
    if b.get("revoke"):
        db.ex("UPDATE client_links SET revoked_at=? WHERE id=? AND project_id=?", (db.now(), int(b["revoke"]), pid))
        return JSONResponse({"ok": True})
    days = int(b.get("days") or 30)
    exp = None if days <= 0 else (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=min(days, 365))).replace(microsecond=0).isoformat()
    tok = secrets.token_urlsafe(24)
    db.ex("INSERT INTO client_links(project_id,token,label,created_by,created_at,expires_at) VALUES(?,?,?,?,?,?)",
          (pid, tok, str(b.get("label", ""))[:80], user["id"], db.now(), exp))
    return JSONResponse({"ok": True, "url": f"{_base_url(request)}/c/{tok}"})


async def client_view(request: Request):
    tok = request.path_params["token"]
    row = db.q1("SELECT * FROM client_links WHERE token=?", (tok,))
    loc = locale_of(request, None)
    if not row or _link_state(row) != "active":
        resp = page(request, "client_view.html", None, loc, gone=True)
        resp.status_code = 404
        return resp
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    visitor = hashlib.sha256((SECRET[:16] + ip).encode()).hexdigest()[:10]  # pseudonymous: no raw IP stored
    db.ex("INSERT INTO client_link_views(link_id,at,visitor,agent) VALUES(?,?,?,?)",
          (row["id"], db.now(), visitor, (request.headers.get("user-agent") or "")[:120]))
    data = A.load(row["project_id"], loc)
    ms = [t for t in data["tasks"] if t["milestone"]]
    return page(request, "client_view.html", None, loc, gone=False, d=data, m=data["metrics"], v=data["verdict"], milestones=ms,
                gantt=charts.gantt(data, loc, "week"), legend=charts.gantt_legend(loc), token=tok)


# ---------------------------------------------------------------- import existing projects

@login_required
async def import_page(request: Request, user):
    loc = locale_of(request, user)
    imp = None
    iid = request.query_params.get("id")
    if iid and iid.isdigit():
        imp = db.q1("SELECT * FROM imports WHERE id=? AND user_id=?", (int(iid), user["id"]))
    ctx = dict(active="projects", imp=None, fields=IMP.FIELDS)
    if imp:
        cols = json.loads(imp["columns_json"])
        rows = json.loads(imp["rows_json"])
        mp = {f: request.query_params.get("m_" + f) for f in IMP.FIELDS if request.query_params.get("m_" + f) in cols}
        if not any(request.query_params.get("m_" + f) is not None for f in IMP.FIELDS):
            mp = IMP.guess_mapping(cols, imp["source"])
        items, errors = IMP.validate(rows, mp, db.today()) if mp.get("title") else ([], [])
        for it in items[:200]:
            it["phase"] = IMP.phase_for(it["group"], it["title"])
        ctx.update(imp=imp, cols=cols, sample=rows[:5], mapping=mp, items=items, errors=errors, n_rows=len(rows),
                   proj_name=request.query_params.get("name") or imp["filename"].rsplit(".", 1)[0])
    return page(request, "import.html", user, loc, **ctx)


async def api_import_upload(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    loc = locale_of(request, user)
    form = await request.form()
    f = form.get("file")
    if f is None or not getattr(f, "filename", ""):
        return jerr(i18n.t("imp.err.nofile", loc))
    raw = await f.read()
    try:
        parsed = IMP.parse_file(f.filename, raw)
    except ValueError as e:
        return jerr(i18n.t(str(e), loc) if str(e).startswith("imp.") else i18n.t("imp.err.read", loc))
    except Exception:
        return jerr(i18n.t("imp.err.read", loc))
    db.ex("DELETE FROM imports WHERE user_id=?", (user["id"],))
    iid = db.ex("INSERT INTO imports(user_id,created_at,filename,source,columns_json,rows_json) VALUES(?,?,?,?,?,?)",
                (user["id"], db.now(), (parsed["name"] or f.filename)[:120], parsed["source"], json.dumps(parsed["columns"], ensure_ascii=False),
                 json.dumps(parsed["rows"], ensure_ascii=False)))
    return JSONResponse({"ok": True, "url": f"/app/import?id={iid}", "source": parsed["source"], "rows": len(parsed["rows"])})


async def api_import_create(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    loc = locale_of(request, user)
    b = await body(request)
    imp = db.q1("SELECT * FROM imports WHERE id=? AND user_id=?", (int(b.get("id") or 0), user["id"]))
    if not imp:
        return jerr("not found", 404)
    cols = json.loads(imp["columns_json"])
    mp = {f: b.get("m_" + f) for f in IMP.FIELDS if b.get("m_" + f) in cols}
    if not mp.get("title"):
        return jerr(i18n.t("imp.err.map_title", loc))
    items, errors = IMP.validate(json.loads(imp["rows_json"]), mp, db.today())
    if not items:
        return jerr(i18n.t("imp.err.empty", loc))
    currency = b.get("currency") if b.get("currency") in i18n.CURRENCIES else "EUR"
    try:
        budget = max(0.0, float(b.get("budget") or 0))
    except ValueError:
        budget = 0.0
    pid = IMP.create(user["id"], str(b.get("name") or imp["filename"])[:120], items, budget=budget, currency=currency, locale=loc)
    db.ex("DELETE FROM imports WHERE id=?", (imp["id"],))
    db.ex("UPDATE users SET current_project_id=? WHERE id=?", (pid, user["id"]))
    _check(pid, send=False)
    return JSONResponse({"ok": True, "url": f"/app/p/{pid}/home?imported={len(items)}", "skipped": len(errors)})


async def import_template(request: Request):
    ext = request.path_params["ext"]
    if ext == "csv":
        return _attach(IMP.template_csv(), "missionpilot_import_template.csv", "text/csv")
    if ext == "xlsx":
        return _attach(IMP.template_xlsx(), "missionpilot_import_template.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return PlainTextResponse("Not found", 404)


# ---------------------------------------------------------------- e-mail: test, weekly report now, scheduled jobs

async def api_test_email(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    loc = locale_of(request, user)
    notify.set_base_url(_base_url(request))
    lid = notify.test_email(user, loc)
    row = db.q1("SELECT * FROM email_log WHERE id=?", (lid,))
    return JSONResponse({"ok": row["status"] in ("queued", "sent"), "status": row["status"], "error": row["error"],
                         "message": i18n.t(f"mail.st.{row['status']}", loc)})


async def api_weekly_now(request: Request):
    user = current_user(request)
    if not user:
        return jerr("auth", 401)
    pid = int(request.path_params["pid"])
    if not owned_project(user, pid):
        return jerr("not found", 404)
    loc = locale_of(request, user)
    notify.set_base_url(_base_url(request))
    lid = await notify.weekly_report(pid, user_id=user["id"])
    row = db.q1("SELECT * FROM email_log WHERE id=?", (lid,)) if lid else None
    st = row["status"] if row else "failed"
    return JSONResponse({"ok": st in ("queued", "sent"), "status": st, "error": row["error"] if row else None,
                         "message": i18n.t(f"mail.st.{st}", loc), "to": row["recipients"] if row else ""})


def purge_demos():
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()
    db.ex("DELETE FROM users WHERE is_demo=1 AND created_at<?", (cutoff,))
    db.ex("DELETE FROM email_log WHERE created_at<?", ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=60)).isoformat(),))


async def api_cron(request: Request):
    """Called by the scheduled GitHub Action (or any cron service). If CRON_SECRET is set, the X-Cron-Key header must match."""
    if CRON_SECRET and not hmac.compare_digest(request.headers.get("x-cron-key", ""), CRON_SECRET):
        return jerr("forbidden", 403)
    job = request.path_params["job"]
    if job == "daily":
        purge_demos()
        n = 0
        for p in db.q("SELECT p.id FROM projects p JOIN users u ON u.id=p.owner_id WHERE u.is_demo=0 AND p.status='active'"):
            _check(p["id"])
            n += 1
        return JSONResponse({"ok": True, "projects_checked": n})
    if job == "weekly":
        return JSONResponse({"ok": True, **(await notify.weekly_all(force=request.query_params.get("force") == "1" and bool(CRON_SECRET)))})
    return jerr("unknown job", 404)


# ---------------------------------------------------------------- app

class SameOriginPost(BaseHTTPMiddleware):
    """Rejects cross-site form/JSON posts (CSRF) — the session cookie is SameSite=Lax too."""

    async def dispatch(self, request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                host = urlparse(origin).netloc
                if host and host != request.headers.get("host") and host != urlparse(PUBLIC_URL).netloc:
                    return PlainTextResponse("Cross-site request blocked", 403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return resp


@asynccontextmanager
async def lifespan(app):
    db.init()
    yield
    await X.shutdown()


routes = [
    Route("/", landing), Route("/login", login, methods=["GET", "POST"]), Route("/register", register, methods=["GET", "POST"]),
    Route("/logout", logout), Route("/demo", demo), Route("/healthz", healthz),
    Route("/app", app_home), Route("/app/projects", projects_page), Route("/app/new", new_page),
    Route("/app/settings", settings_page, methods=["GET", "POST"]), Route("/app/help", help_page),
    Route("/app/p/{pid:int}/home", p_home), Route("/app/p/{pid:int}/tasks", p_tasks), Route("/app/p/{pid:int}/gantt", p_gantt),
    Route("/app/p/{pid:int}/progress", p_progress), Route("/app/p/{pid:int}/budget", p_budget), Route("/app/p/{pid:int}/risks", p_risks),
    Route("/app/p/{pid:int}/team", p_team), Route("/app/p/{pid:int}/reports", p_reports),
    Route("/app/p/{pid:int}/documents", p_documents), Route("/app/p/{pid:int}/assistant", p_assistant),
    Route("/app/p/{pid:int}/whatif", p_whatif), Route("/app/p/{pid:int}/notes", p_notes), Route("/app/p/{pid:int}/client", p_client),
    Route("/app/import", import_page), Route("/c/{token}", client_view), Route("/files/import-template.{ext}", import_template),
    Route("/api/projects/{pid:int}/whatif", api_whatif_apply, methods=["POST"]),
    Route("/api/projects/{pid:int}/notes/parse", api_notes_parse, methods=["POST"]),
    Route("/api/projects/{pid:int}/notes/create", api_notes_create, methods=["POST"]),
    Route("/api/projects/{pid:int}/client-link", api_client_link, methods=["POST"]),
    Route("/api/projects/{pid:int}/weekly-now", api_weekly_now, methods=["POST"]),
    Route("/api/import/upload", api_import_upload, methods=["POST"]), Route("/api/import/create", api_import_create, methods=["POST"]),
    Route("/api/settings/test-email", api_test_email, methods=["POST"]),
    Route("/api/cron/{job}", api_cron, methods=["GET", "POST"]),
    Route("/app/doc/{doc_id:int}", doc_view),
    Route("/files/doc/{doc_id:int}.{ext}", doc_file), Route("/files/p/{pid:int}/{name}", project_file),
    Route("/s/{token}", shared),
    Route("/api/lang", api_lang, methods=["POST"]), Route("/api/plan/questions", api_questions, methods=["POST"]),
    Route("/api/plan/create", api_create, methods=["POST"]), Route("/api/tasks/{tid:int}", api_task, methods=["POST"]),
    Route("/api/budget/{bid:int}", api_budget, methods=["POST"]), Route("/api/risks/{rid:int}", api_risk, methods=["POST"]),
    Route("/api/members/{mid:int}", api_member, methods=["POST"]),
    Route("/api/projects/{pid:int}/delete", api_project_delete, methods=["POST"]),
    Route("/api/projects/{pid:int}/chat", api_chat, methods=["POST"]),
    Route("/api/projects/{pid:int}/translate", api_translate, methods=["POST"]),
    Route("/api/projects/{pid:int}/generate", api_generate, methods=["POST"]),
    Route("/api/docs/{doc_id:int}/share", api_share, methods=["POST"]), Route("/api/docs/{doc_id:int}/unshare", api_unshare, methods=["POST"]),
    Route("/api/tour", api_tour, methods=["POST"]), Route("/api/demo/reset", api_demo_reset, methods=["POST"]),
    Mount("/static", StaticFiles(directory=str(BASE / "static")), name="static"),
]

app = Starlette(routes=routes, lifespan=lifespan, middleware=[
    Middleware(SessionMiddleware, secret_key=SECRET, same_site="lax", https_only=os.environ.get("HTTPS_ONLY", "0") == "1",
               max_age=60 * 60 * 24 * 30),
    Middleware(SameOriginPost),
])
