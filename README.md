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

## Run it

- **Windows laptop:** double-click `run_local.bat`, then open http://localhost:8000
- **macOS / Linux:** `./run_local.sh`
- **Online:** see **DEPLOY.md** (Render, step by step, English + मराठी)

## Tests

```bash
python -m unittest -v                                         # 15 core tests
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
