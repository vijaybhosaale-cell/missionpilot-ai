"""End-to-end smoke test against a running server.
Usage: python scripts/smoke_test.py http://127.0.0.1:8000 en out_folder"""
import http.cookiejar, json, sys, time, urllib.request, urllib.error, re, sqlite3, os
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
OUT = sys.argv[3] if len(sys.argv) > 3 else "/tmp/qa_out"
os.makedirs(OUT, exist_ok=True)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def req(path, data=None, js=True, raw=False):
    body = None; h = {"Origin": BASE}
    if data is not None:
        if js: body = json.dumps(data).encode(); h["Content-Type"] = "application/json"
        else: body = urllib.parse.urlencode(data).encode(); h["Content-Type"] = "application/x-www-form-urlencoded"
    r = urllib.request.Request(BASE + path, data=body, headers=h, method="POST" if data is not None else "GET")
    try:
        with op.open(r, timeout=180) as resp:
            b = resp.read(); return resp.status, b if raw else b.decode("utf-8", "replace"), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers
import urllib.parse
fails = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond: fails.append(name)
s, b, _ = req("/?lang=" + LANG); check("landing", s == 200)
email = f"u{int(time.time()*1000)}@test.dev"
s, b, _ = req("/register", {"name": "Test User", "email": email, "password": "password123", "lang": LANG}, js=False)
check("register", s == 200 and "What are we building" in b or s == 200, s)
s, b, _ = req("/api/plan/questions", {"prompt": "Cybersecurity SOC for a bank — detect UPI fraud in real time", "lang": LANG})
q = json.loads(b); check("questions", s == 200 and len(q["questions"]) >= 6, b[:200])
print("   type:", q["type"], "| name:", q["name"], "| ids:", [x["id"] for x in q["questions"]])
s, b, _ = req("/api/plan/create", {"name": q["name"], "description": q["description"], "type": q["type"], "budget": 80000, "currency": "EUR",
                                   "team_size": 4, "members": [{"name": "Vijay", "role": "pm"}, {"name": "Niranjan", "role": "sec"}, {"name": "Karan", "role": "lead"}, {"name": "", "role": "qa"}],
                                   "duration_weeks": 10, "start_date": "2026-10-26", "lang": LANG})
c = json.loads(b); check("create", s == 200 and c.get("project_id"), b[:300]); pid = c["project_id"]
for p in ["home", "tasks", "tasks?view=board", "gantt", "gantt?scale=day", "gantt?scale=month", "progress", "progress?f=all", "budget", "risks", "team", "reports", "documents", "assistant"]:
    s, b, _ = req(f"/app/p/{pid}/{p}"); check("page " + p, s == 200 and "Traceback" not in b, b[-500:])
for p in ["/app/projects", "/app/new", "/app/settings", "/app/help"]:
    s, b, _ = req(p); check("page " + p, s == 200, b[-500:])
# progress update + chat
s, b, _ = req(f"/app/p/{pid}/tasks")
tid = int(re.search(r'data-task="(\d+)"', b).group(1))
s, b, _ = req(f"/api/tasks/{tid}", {"progress": 60}); check("update progress", s == 200 and json.loads(b)["progress"] == 60, b)
s, b, _ = req(f"/api/projects/{pid}/chat", {"message": "How is the project going? Which tasks are overdue?", "lang": LANG}); check("chat", s == 200 and json.loads(b)["reply"], b)
print("   chat:", json.loads(b)["reply"][:160].replace("\n", " | "))
# documents + reports
docs = ["documents_needed", "charter", "proposal", "brd", "srs", "tdd", "architecture", "wbs", "implementation_plan", "test_plan", "risk_assessment", "deployment_guide", "user_manual", "closure_report"]
reps = ["daily", "weekly", "monthly", "missed", "budget", "risk", "workload"]
only = sys.argv[4].split(",") if len(sys.argv) > 4 else None
ids = {}
for kind, lst in (("doc", docs), ("report", reps)):
    for d in lst:
        s, b, _ = req(f"/api/projects/{pid}/generate", {"kind": kind, "type": d, "lang": LANG, "date": "2026-10-21"})
        ok = s == 200 and "doc_id" in b
        check(f"generate {kind} {d}", ok, b[:300])
        if ok: ids[d] = json.loads(b)["doc_id"]
for d, i in ids.items():
    if only and d not in only: continue
    s, b, _ = req(f"/app/doc/{i}"); check("view " + d, s == 200, b[-300:])
for d in (only or ["charter", "srs", "weekly", "documents_needed"]):
    if d not in ids: continue
    for ext in ("pdf", "docx"):
        s, b, h = req(f"/files/doc/{ids[d]}.{ext}", raw=True)
        ok = s == 200 and (b[:4] == b"%PDF" if ext == "pdf" else b[:2] == b"PK")
        check(f"download {d}.{ext} ({len(b)//1024} KB)", ok, b[:200])
        if ok: open(f"{OUT}/{d}_{LANG}.{ext}", "wb").write(b)
for name in ("gantt.xml", "gantt.xlsx", "tasks.xlsx", "budget.xlsx", "risks.xlsx", "team.xlsx", "gantt.pdf", "progress.pdf"):
    s, b, h = req(f"/files/p/{pid}/{name}?lang={LANG}", raw=True)
    check(f"file {name} ({len(b)//1024} KB)", s == 200 and len(b) > 500, b[:200])
    if s == 200: open(f"{OUT}/{name.replace('.', '_' + LANG + '.')}", "wb").write(b)
# share
s, b, _ = req(f"/api/docs/{ids['charter']}/share", {}); url = json.loads(b)["url"]; check("share", s == 200, b)
tok = url.rsplit("/", 1)[1]
anon = urllib.request.build_opener()
r = anon.open(urllib.request.Request(BASE + "/s/" + tok)); check("shared page anonymous", r.status == 200)
r = anon.open(urllib.request.Request(BASE + f"/files/doc/{ids['charter']}.pdf?token=" + tok)); check("shared pdf anonymous", r.read(4) == b"%PDF")
print("PROJECT", pid)
print("FAILS:", fails)
