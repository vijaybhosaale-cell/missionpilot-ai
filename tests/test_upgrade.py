"""Tests for v1.1: automatic risks, verdict, e-mail, AI model, what-if, meeting notes, import."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from app import analytics as A, db, importer as IMP, mailer, ml, notes, notify, planner, riskengine, whatif

TMP = tempfile.mkdtemp()
db.set_path(Path(TMP) / "up.db")
db.init()
UID = db.ex("INSERT INTO users(email,name,password_hash,created_at) VALUES('o@x.org','Owner','x',?)", (db.now(),))
SENT = []


class FakeSMTP:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def ehlo(self): pass
    def starttls(self, **k): pass
    def login(self, u, p):
        if p == "wrong":
            raise Exception("535 Username and Password not accepted")
    def send_message(self, m): SENT.append(m)


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pid = planner.seed_demo(UID, "en")

    def test_random_project_id(self):
        self.assertGreaterEqual(self.pid, 100_000_000)

    def test_auto_risks_explain_and_clear(self):
        riskengine.evaluate(self.pid)
        d = A.load(self.pid, "en")
        auto = [r for r in d["risks"] if r["source"] == "auto"]
        self.assertTrue(auto)
        for r in auto:
            self.assertTrue(r["why"] and "{" not in r["why"], r["why"])
            self.assertEqual(r["evidence"], "signal")  # never confirmed automatically
        # finish every task: the behind-plan / overdue conditions disappear -> cleared
        db.ex("UPDATE tasks SET progress=100, status='done' WHERE project_id=?", (self.pid,))
        riskengine.evaluate(self.pid)
        st = {r["rule_key"]: r["status"] for r in db.q("SELECT * FROM risks WHERE project_id=? AND source='auto'", (self.pid,))}
        self.assertTrue(st)
        self.assertEqual(set(st.values()), {"cleared"}, st)
        self.assertEqual(A.load(self.pid, "en")["verdict"]["level"] in ("on_track", "at_risk", "critical"), True)

    def test_mail_not_configured_is_logged(self):
        lid = mailer.send(["a@b.org"], "s", "<p>h</p>", "t", event="test")
        self.assertEqual(db.q1("SELECT status FROM email_log WHERE id=?", (lid,))["status"], "not_configured")

    def test_mail_sent_and_demo_goes_to_admin_only(self):
        import os
        os.environ.update(SMTP_USER="bot@gmail.com", SMTP_PASSWORD="abcd efgh ijkl mnop", EMAIL_TO="admin@x.org")
        mailer.SYNC, mailer.SMTP_CLASS = True, FakeSMTP
        try:
            demo = db.ex("INSERT INTO users(email,name,password_hash,is_demo,interface_locale,created_at) VALUES('d@demo.missionpilot','D','x',1,'fr',?)", (db.now(),))
            pid = planner.seed_demo(demo, "fr")
            db.ex("UPDATE members SET email='victim@else.org' WHERE project_id=?", (pid,))
            self.assertEqual(notify.recipients(pid, [m["id"] for m in db.q("SELECT id FROM members WHERE project_id=?", (pid,))]), ["admin@x.org"])
            tid = db.q1("SELECT id FROM tasks WHERE project_id=? AND is_milestone=0", (pid,))["id"]
            notify.task_blocked(pid, tid)
            self.assertEqual(SENT[-1]["To"], "admin@x.org")
            self.assertIn("bloquée", SENT[-1]["Subject"])
            os.environ["SMTP_PASSWORD"] = "wrong"
            lid = mailer.send(["a@b.org"], "s", "h", "t", event="test")
            row = db.q1("SELECT * FROM email_log WHERE id=?", (lid,))
            self.assertEqual(row["status"], "failed")
            self.assertIn("App password", row["error"])
        finally:
            for k in ("SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"):
                os.environ.pop(k, None)
            mailer.SYNC, mailer.SMTP_CLASS = False, mailer.smtplib.SMTP

    def test_ai_model_suggestions(self):
        self.assertTrue(ml.available(), ml.status())
        self.assertEqual(ml.suggest_category("Personal data is exposed (GDPR)")["riskcat"], "compliance")
        self.assertEqual(ml.suggest_category("Les coûts dépassent le budget")["riskcat"], "cost")
        pairs = ml.duplicates([dict(id=1, text="Tasks take longer than planned"), dict(id=2, text="Les tâches prennent plus de temps que prévu"),
                               dict(id=3, text="A supplier delivers late")])
        self.assertEqual([(a["id"], b["id"]) for a, b, _ in pairs], [(1, 2)])

    def test_meeting_notes(self):
        mem = [dict(id=1, name="Lucas Martin"), dict(id=2, name="Sofia Rossi")]
        items = notes.extract_rules("- Lucas will finish the detection rules by Friday (urgent)\nSofia doit corriger les bugs avant mardi.\n"
                                    "The weather was nice.", mem, dt.date(2026, 10, 25))
        self.assertEqual(len(items), 2)
        self.assertEqual((items[0]["owner_id"], items[0]["deadline"], items[0]["priority"]), (1, "2026-10-30", "critical"))
        self.assertEqual(items[0]["title"], "Finish the detection rules")
        self.assertEqual((items[1]["owner_id"], items[1]["deadline"]), (2, "2026-10-27"))
        d, _ = notes.find_date("ओमर शुक्रवारपर्यंत यादी तयार करेल", dt.date(2026, 10, 25))
        self.assertEqual(d, dt.date(2026, 10, 30))

    def test_whatif_moves_tasks_without_saving(self):
        db.ex("UPDATE tasks SET progress=0, status='todo' WHERE project_id=?", (self.pid,))
        before = [tuple(r) for r in db.q("SELECT id,end_date FROM tasks WHERE project_id=?", (self.pid,))]
        d = A.load(self.pid, "en")
        m = next(x for x in d["members"] if any(t["assignee_id"] == x["id"] and not t["milestone"] and t["end"] >= d["metrics"]["today"] for t in d["tasks"]))
        t0 = min((t for t in d["tasks"] if t["assignee_id"] == m["id"] and not t["milestone"] and t["end"] >= d["metrics"]["today"]),
                 key=lambda t: t["start"])
        sim = whatif.simulate(d, m["id"], max(t0["start"], d["metrics"]["today"]), t0["end"])
        self.assertIn(t0["id"], sim["direct"])
        self.assertEqual(before, [tuple(r) for r in db.q("SELECT id,end_date FROM tasks WHERE project_id=?", (self.pid,))])

    def test_import_trello_and_validation(self):
        board = dict(name="Shop", lists=[dict(id="l1", name="Doing"), dict(id="l2", name="Done")], members=[dict(id="m1", fullName="Asha")],
                     cards=[dict(name="Build cart", idList="l1", idMembers=["m1"], due="2026-11-10T10:00:00Z"),
                            dict(name="Kick-off", idList="l2", idMembers=[], due="2026-11-02T10:00:00Z"), dict(name="", idList="l1")])
        p = IMP.parse_file("b.json", json.dumps(board).encode())
        mp = IMP.guess_mapping(p["columns"], p["source"])
        items, errors = IMP.validate(p["rows"], mp, dt.date(2026, 10, 25))
        self.assertEqual(len(items), 2)
        self.assertEqual([e[1] for e in errors], ["imp.err.no_title"])
        self.assertEqual({i["title"]: i["progress"] for i in items}, {"Build cart": 50, "Kick-off": 100})
        pid = IMP.create(UID, p["name"], items, budget=1000, currency="EUR", locale="en")
        self.assertEqual(len(A.load(pid, "en")["tasks"]), 2)


if __name__ == "__main__":
    unittest.main()
