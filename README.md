# MissionPilot AI

Type a project name and one sentence. Answer a short question popup (budget, team size, names and roles, duration, project type, start date). MissionPilot builds the whole project — tasks with dependencies, Gantt chart, resources, budget, risks and 14 documents — and then tracks progress until the end.

**Languages switched on:** English, Français, मराठी. German, Spanish, Hindi, Portuguese, Italian, Arabic, Japanese and Chinese are configured and on standby (see `docs/I18N.md`).

## What is inside

| Page | What it does |
|---|---|
| 1 Home | KPIs (progress vs plan, tasks done, overdue, work left, budget, end date), status mix, tasks in progress, plan-vs-actual curve, milestones, recent activity |
| 2 Projects | All projects with progress; create / open / delete |
| 3 Tasks | List grouped by phase or Kanban board; progress slider, status, end-date change (moves dependent tasks), colour code for status and priority, critical-path badge |
| 4 Gantt chart | Dependencies, resources, milestones, critical path, today line; day/week/month zoom; download **MS Project (.xml)**, **Excel**, **PDF** |
| 5 Progress tracking | Done vs left (% and days) per task, phase and project; **burndown chart with a day picker** (pick day 5, day 40, or a date → what is done by then and what is still to do); ongoing / completed (with project day) / overdue / upcoming lists; tasks finished per week; plan vs actual |
| 6 Budget | Planned vs actual per category, editable; earned value (PV, EV, AC, CPI, SPI, EAC) |
| 7 Risks | Register with owner, mitigation and status; 5×5 risk matrix |
| 8 Team & resources | Names, roles, availability; weekly workload heatmap; overload warnings |
| 9 Reports | Daily, weekly, monthly, missed-task, budget, risk, workload — in any enabled language — PDF / Word / share link |
| 10 Documents | Documents register (documents needed), charter, proposal, BRD, SRS, technical design, architecture, WBS, implementation plan, test plan, risk assessment, deployment guide, user manual, closure report — Word + PDF, share link, **download everything as ZIP** |
| 11 AI assistant | Chat about the project by typing or microphone; answer language can differ from the question |
| 12 Settings | Separate interface / AI / document / report languages, formats, voice guide, translation status |
| 13 Help & tour | Guides and the **voice-guided tour** for new users; **Try the demo** on the landing page |

Without a Claude API key everything works from a professional template library. With `ANTHROPIC_API_KEY` set, Claude writes the plan for your exact project, asks extra project-specific questions, writes document sections and answers free-form questions.

## New in v1.1

| Feature | Where | How it works |
|---|---|---|
| **Project verdict** | Home | On track / At risk / Critical with the top 3 reasons, recalculated from the data on every visit |
| **Data-driven risks** | Risks (⚙ Automatic) | Explainable rules: member above 40 h × availability in a week, overdue critical-path tasks, > 3 points behind plan, CPI or SPI < 0.95, milestone at risk. Each risk says *why*; it becomes "Condition cleared" when the cause disappears; never confirmed automatically |
| **Free pre-trained AI model** | Risks, Tasks, Meeting notes, Import | `static-similarity-mrl-multilingual-v1` (Apache-2.0, 128 dims, ~27 MB, runs on the server, no paid API): suggests categories (scope, schedule, cost, security, GDPR, people, supplier, technical), finds duplicate risks, scores action items, suggests phases. Shown as "AI suggestion — a person decides" |
| **Real-time e-mail (Gmail)** | Settings → E-mails | Task assigned / reassigned, deadline changed, task blocked, newly overdue, overload, new high risk, absence; background sending; every attempt logged; test button; daily limit; demo workspaces e-mail only `EMAIL_TO` |
| **Weekly status e-mail** | Monday 08:00 Paris | GitHub Actions (`.github/workflows/scheduled-jobs.yml`) calls `/api/cron/weekly`; Word report attached. "E-mail weekly report" button on Home sends it now |
| **What-if** | What-if | "If X is absent from … to …": tasks that move, new end date, critical-path impact, replacements ranked by role and free hours; apply only when you decide |
| **Meeting notes → tasks** | Meeting notes | EN/FR/मराठी: owner, deadline ("by Friday", "avant mardi", "शुक्रवारपर्यंत"…), priority; editable preview; nothing created automatically. Uses Claude if a key is set |
| **Client share link** | Home → Client link | Secret read-only page (verdict, progress, milestones, Gantt — no budget, risks or e-mails); expiry, revoke, view log (no IP stored) |
| **Import** | Projects → Import | Excel/CSV, Trello JSON, Jira CSV; column mapping, preview, per-row errors, downloadable templates |

### Connect Gmail (5 minutes)

1. Google Account → **Security** → turn on **2-Step Verification**.
2. Open https://myaccount.google.com/apppasswords → name it *MissionPilot* → **Create** → copy the 16-character password.
3. Render → the `missionpilot-ai` service → **Environment** → add `SMTP_USER` (the Gmail address), `SMTP_PASSWORD` (the App password), `EMAIL_TO` (where demo/admin mails go) → **Save** (the site restarts).
4. MissionPilot → **Settings → Send a test e-mail**. The log shows *Sent* or the exact error.

Optional: `CRON_SECRET` on Render + the same value as GitHub secret `MP_CRON_SECRET` to protect the scheduled endpoints.

### Honest limits

* Render free plan: the SQLite database is reset when the service restarts or redeploys. The scheduled keep-alive (every 10 min) avoids the sleep restarts; for permanent data use a paid disk or a hosted database.
* GitHub may delay scheduled workflows by several minutes and pauses them after 60 days without commits.
* The AI model gives *suggestions* (≈60 % agreement with the template categories in tests; better on clear wording, weaker on vague or Marathi text). Rules and people decide.
* Meeting-note names written in another script (e.g. "अनाया" for "Anaya") are not matched to the team member.

## Run it

- **Windows laptop:** double-click `run_local.bat`, then open http://localhost:8000
- **macOS / Linux:** `./run_local.sh`
- **Online:** see **DEPLOY.md** (Render, step by step, English + मराठी)

## Tests

```bash
python -m unittest -v                                         # 23 tests (core + v1.1)
python scripts/smoke_test.py http://localhost:8000 mr out     # end-to-end against a running server
python -m app.check_locales                                   # every language has every key
```

## Project layout

```
app/
  main.py          routes, pages, API
  planner.py       prompt + answers -> phases, tasks, schedule, team, budget, risks
  library.py       template library (task, risk, budget, feature catalogues)
  analytics.py     progress, plan vs actual, critical path, workload, earned value
  charts.py        SVG charts (Gantt, progress bars, burndown, S-curve, budget, risk matrix, heatmap)
  documents.py     the 14 documents and 7 reports as a language-neutral content model
  render.py        content model -> HTML (preview + PDF page)
  exporters.py     PDF (Chromium), Word, MS Project XML, Excel, ZIP
  ai.py            optional Claude API (server-side only)
  i18n.py          languages, fallback, plurals, number/date/money formats
  locales/<code>/  ui.json + content.json + meta.json per language
  templates/       Jinja pages      static/   app.css, app.js (tour, voice, popup, burndown picker)
tests/             unit tests
scripts/           smoke test
Dockerfile, render.yaml, run_local.bat, run_local.sh, DEPLOY.md
```

Data model: numbers, dates (ISO), money (integer minor units) and status codes are stored language-independently; template text is stored as translation keys, user/AI text in the project's language with optional translations (`content_translations`, with outdated-detection). Translating never changes the original record.
