"""Core tests — run from the project folder:  python -m unittest -v
(no extra packages needed beyond requirements.txt)"""
import asyncio
import datetime as dt
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from app import analytics as A, charts, check_locales, db, documents as D, exporters as X, i18n, library as L, planner

TMP = tempfile.mkdtemp()
db.set_path(Path(TMP) / "test.db")
db.init()
UID = db.ex("INSERT INTO users(email,name,password_hash,created_at) VALUES('t@t.t','T','x',?)", (db.now(),))


def make_project(ptype="cybersecurity", weeks=10, team=4, locale="en"):
    answers = dict(name="Test project", description="A test", type=ptype, budget=80000, currency="EUR", team_size=team,
                   member_names=["Asha", "Ben"], member_roles=["pm", "sec"], duration_weeks=weeks,
                   start_date="2026-11-02", locale=locale)
    return planner.create_project(UID, answers)


class TestLocales(unittest.TestCase):
    def test_every_enabled_locale_matches_english(self):
        for code in i18n.CODES:
            self.assertEqual(check_locales.check(code), [], code)

    def test_fallback_never_shows_raw_key(self):
        self.assertEqual(i18n.t("nav.home", "fr"), "Accueil")
        self.assertEqual(i18n.t("does.not.exist_here", "fr"), "Exist here")
        self.assertIn(("does.not.exist_here", "fr"), i18n.drain_missing())

    def test_plurals_and_formats(self):
        self.assertEqual(i18n.t("q.people", "en", n=1), "1 person")
        self.assertEqual(i18n.t("q.people", "en", n=4), "4 people")
        self.assertIn("250,50", i18n.money_minor(125050, "EUR", "fr"))
        self.assertIn("1", i18n.money_minor(125000, "EUR", "en"))
        self.assertEqual(i18n.norm("fr-FR"), "fr")
        self.assertEqual(i18n.norm("de"), "en")  # standby language falls back to English


class TestPlanner(unittest.TestCase):
    def test_every_type_schedules_without_dependency_violations(self):
        for ptype in L.PROJECT_TYPES:
            plan = planner.template_plan(ptype)
            planner.schedule(plan, dt.date(2026, 11, 2), 12)
            by = {t["code"]: t for t in plan["tasks"]}
            for t in plan["tasks"]:
                for p in t["deps"]:
                    if t["milestone"]:
                        self.assertGreaterEqual(t["start"], by[p]["end"], (ptype, t["code"], p))
                    else:
                        self.assertGreater(t["start"], by[p]["end"], (ptype, t["code"], p))
            days = planner.workdays_between(dt.date(2026, 11, 2), max(t["end"] for t in plan["tasks"]))
            self.assertLess(abs(days - 60), 6, ptype)

    def test_team_names_and_roles_from_popup(self):
        team = planner.build_team("software", 3, ["Vijay", "", "Karan"], ["pm", "dev", "qa"])
        self.assertEqual([m["role"] for m in team], ["pm", "dev", "qa"])
        self.assertEqual(team[0]["name"], "Vijay")

    def test_type_detection(self):
        self.assertEqual(planner.detect_type("SOC platform for a bank to detect UPI fraud"), "cybersecurity")
        self.assertEqual(planner.detect_type("Mobile app for clinic appointments"), "mobile")
        self.assertEqual(planner.detect_type("बँकेसाठी सायबरसुरक्षा SOC"), "cybersecurity")


class TestProjectData(unittest.TestCase):
    def test_numbers_identical_in_every_language(self):
        pid = make_project()
        t = db.q1("SELECT id FROM tasks WHERE project_id=? AND is_milestone=0 ORDER BY seq", (pid,))
        db.ex("UPDATE tasks SET progress=80, status='in_progress' WHERE id=?", (t["id"],))
        ref = None
        for code in i18n.CODES:
            m = A.load(pid, code)["metrics"]
            vals = (round(m["progress"], 6), m["tasks_total"], m["budget_planned"], m["days_total"])
            ref = ref or vals
            self.assertEqual(vals, ref, code)
        self.assertEqual(A.load(pid, "fr")["tasks"][0]["title"], i18n.t("task.kickoff.t", "fr"))

    def test_reschedule_moves_dependants(self):
        pid = make_project()
        t = db.q1("SELECT * FROM tasks WHERE project_id=? AND code='requirements'", (pid,))
        before = db.q1("SELECT start_date FROM tasks WHERE project_id=? AND code='srs'", (pid,))["start_date"]
        A.reschedule_from(pid, t["id"], dt.date.fromisoformat(t["end_date"]) + dt.timedelta(days=7))
        after = db.q1("SELECT start_date FROM tasks WHERE project_id=? AND code='srs'", (pid,))["start_date"]
        self.assertGreater(after, before)

    def test_demo_project(self):
        pid = planner.seed_demo(UID, "mr")
        data = A.load(pid, "mr")
        self.assertGreater(data["metrics"]["progress"], 10)
        self.assertTrue(A.history(pid, data))
        self.assertEqual(data["project"]["display_name"], i18n.t("demo.name", "mr"))


class TestDocumentsAndExports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pid = make_project("software")

    def test_every_document_and_report_in_every_language(self):
        for code in i18n.CODES:
            data = A.load(self.pid, code)
            for dtp in L.DOC_TYPES:
                c = D.build_document(dtp, data, code)
                self.assertTrue(c["blocks"], (dtp, code))
            for r in L.REPORT_TYPES:
                c = D.build_report(r, data, code, dt.date(2026, 11, 20))
                self.assertTrue(c["blocks"], (r, code))

    def test_word_file(self):
        data = A.load(self.pid, "mr")
        c = D.build_document("charter", data, "mr")
        c["version"] = 1
        b = asyncio.run(X.docx_bytes(c))
        z = zipfile.ZipFile(io.BytesIO(b))
        xml = z.read("word/document.xml").decode()
        self.assertIn("प्रकल्प", xml)
        self.assertIn('w:bidi="mr-IN"', xml)
        self.assertIn("mr-IN", z.read("docProps/core.xml").decode())

    def test_ms_project_xml(self):
        data = A.load(self.pid, "fr")
        root = ET.fromstring(X.mspdi(data, "fr"))
        ns = {"p": "http://schemas.microsoft.com/project"}
        tasks = root.findall("p:Tasks/p:Task", ns)
        self.assertGreater(len(tasks), len(data["tasks"]))
        self.assertTrue(root.findall("p:Resources/p:Resource", ns))
        self.assertTrue(root.findall("p:Assignments/p:Assignment", ns))

    def test_excel(self):
        data = A.load(self.pid, "en")
        b = X.xlsx(data, "en", "all")
        self.assertEqual(b[:2], b"PK")

    def test_chart_ids_are_unique(self):
        data = A.load(self.pid, "en")
        rows = [dict(label=t["title"], progress=50, days=t["days"]) for t in data["tasks"][:3]]
        a, b = charts.progress_bars(rows, "en"), charts.progress_bars(rows, "en")
        import re
        self.assertNotEqual(re.search(r'clipPath id="(\w+)"', a).group(1), re.search(r'clipPath id="(\w+)"', b).group(1))

    def test_pdf_if_chromium_available(self):
        data = A.load(self.pid, "mr")
        c = D.build_document("documents_needed", data, "mr")
        pdf = asyncio.run(X.pdf_bytes(c))
        if pdf is None:
            self.skipTest("Chromium not installed — run: python -m playwright install chromium")
        self.assertEqual(pdf[:4], b"%PDF")


if __name__ == "__main__":
    unittest.main()
