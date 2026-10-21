"""Optional Claude API integration. Set ANTHROPIC_API_KEY on the server to turn it on.
Without a key every feature still works from the template library.
The key stays on the server; the browser never sees it."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from . import i18n, library as L

API_URL = os.environ.get("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
TIMEOUT = int(os.environ.get("ANTHROPIC_TIMEOUT", "90"))

last_error: str | None = None


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _call(system: str, messages: list[dict], max_tokens: int = 4000) -> str | None:
    global last_error
    if not available():
        return None
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens, "system": system, "messages": messages}).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode())
        last_error = None
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    except urllib.error.HTTPError as e:
        last_error = f"HTTP {e.code}: {e.read()[:300].decode(errors='ignore')}"
    except Exception as e:  # network, timeout, bad JSON
        last_error = str(e)[:300]
    return None


def _json(text: str | None):
    if not text:
        return None
    m = re.search(r"\{.*\}|\[.*\]", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def lang_name(code: str) -> str:
    return f'{i18n.LOCALES[code]["english"]} ({code})'


KEEP = "Keep code, API names, product names, file paths, identifiers and database field names in English."


# ---------------------------------------------------------------- clarifying questions

def questions(prompt: str, locale: str) -> list[dict] | None:
    sys = ("You help set up a project plan. The user typed a project name and a short description. "
           "Ask 1-2 extra clarifying questions that matter most for THIS project (not budget, team size, duration, type "
           "or start date; those are asked separately). Each question has 2-4 short answer options. "
           f"Write everything in {lang_name(locale)}. {KEEP} "
           'Reply with JSON only: {"questions":[{"id":"x1","question":"...","header":"max 12 chars","options":["..",".."]}]}')
    out = _json(_call(sys, [{"role": "user", "content": prompt}], 800))
    if isinstance(out, dict) and isinstance(out.get("questions"), list):
        qs = []
        for i, q in enumerate(out["questions"][:2], 1):
            if isinstance(q, dict) and q.get("question") and isinstance(q.get("options"), list):
                qs.append({"id": f"x{i}", "question": str(q["question"])[:200], "header": str(q.get("header", ""))[:16],
                           "options": [str(o)[:60] for o in q["options"][:4]], "kind": "chips", "allow_other": True})
        return qs
    return None


# ---------------------------------------------------------------- full plan

def plan(answers: dict, locale: str) -> dict | None:
    canon = answers.get("locale", locale)
    roles = ", ".join(L.ROLES)
    sys = (
        "You are an expert project manager. Create a realistic project plan as JSON for the project described by the user. "
        f"Write all names and descriptions in {lang_name(canon)}. {KEEP}\n"
        "Rules: 7 phases with these codes in order: " + ", ".join(L.PHASES) + ". "
        "25-45 tasks. Each task: code (snake_case, unique), phase (one of the phase codes), title, description (one sentence), "
        f"days (relative working days, 1-15; 0 for milestones), role (one of: {roles}), deps (list of task codes it waits for; "
        "no cycles), priority (low|medium|high|critical), milestone (true/false), deliverable (one of: "
        + ", ".join(L.DOC_TYPES) + " or null). End every phase with a milestone. "
        "8-12 risks: title, mitigation, category (scope|schedule|cost|resource|technical|security|compliance|external), "
        "p (probability 1-5), i (impact 1-5), owner_role. Budget split: list of {category, pct} using categories "
        + ", ".join(L.BUDGET_CATEGORIES) + " (pct sums to 100). "
        'JSON shape: {"phases":[{"code":"initiation","name":"..."}],"tasks":[...],"risks":[...],"budget":[...]}. Reply with JSON only.')
    user = json.dumps({k: answers.get(k) for k in ("name", "description", "type", "budget", "currency", "team_size",
                                                   "duration_weeks", "start_date", "extra")}, ensure_ascii=False)
    out = _json(_call(sys, [{"role": "user", "content": user}], 8000))
    try:
        return _validate_plan(out, answers.get("type", "generic"))
    except Exception:
        return None


def _validate_plan(out, ptype):
    if not isinstance(out, dict):
        return None
    phases = []
    names = {p.get("code"): p.get("name", "") for p in out.get("phases", []) if isinstance(p, dict)}
    for code in L.PHASES:
        phases.append(dict(code=code, name_key=None if names.get(code) else f"phase.{code}", name_text=names.get(code, "")))
    tasks, seen = [], set()
    for t in out.get("tasks", []):
        if not isinstance(t, dict):
            continue
        code = re.sub(r"\W", "_", str(t.get("code", "")))[:40] or f"t{len(tasks) + 1}"
        if code in seen:
            code = f"{code}_{len(tasks) + 1}"
        seen.add(code)
        ms = bool(t.get("milestone")) or int(t.get("days", 1) or 0) == 0
        role = t.get("role") if t.get("role") in L.ROLES else ("pm" if ms else "dev")
        tasks.append(dict(code=code, phase=t.get("phase") if t.get("phase") in L.PHASES else "build",
                          days=0 if ms else max(1, min(30, int(t.get("days", 3) or 3))), role=role,
                          deps=[str(d) for d in t.get("deps", []) if isinstance(d, (str, int))],
                          deliverable=t.get("deliverable") if t.get("deliverable") in L.DOC_TYPES else None,
                          priority=t.get("priority") if t.get("priority") in ("low", "medium", "high", "critical") else "medium",
                          milestone=ms, title_key=None, desc_key=None, title_text=str(t.get("title", code))[:200],
                          desc_text=str(t.get("description", ""))[:500]))
    if len(tasks) < 8:
        return None
    codes = {t["code"] for t in tasks}
    for t in tasks:
        t["deps"] = [d for d in t["deps"] if d in codes and d != t["code"]]
    risks = []
    for r in out.get("risks", [])[:15]:
        if not isinstance(r, dict) or not r.get("title"):
            continue
        risks.append(dict(code="", category=r.get("category", "technical"), p=max(1, min(5, int(r.get("p", 3)))),
                          i=max(1, min(5, int(r.get("i", 3)))), owner_role=r.get("owner_role", "pm"),
                          title_key=None, mitigation_key=None, title_text=str(r["title"])[:200],
                          mitigation_text=str(r.get("mitigation", ""))[:400]))
    budget = [dict(category=b["category"], pct=float(b["pct"])) for b in out.get("budget", [])
              if isinstance(b, dict) and b.get("category") in L.BUDGET_CATEGORIES and float(b.get("pct", 0)) > 0]
    if not budget:
        budget = [dict(category=c, pct=p) for c, p in zip(L.BUDGET_CATEGORIES, L.BUDGET_SPLIT.get(ptype, L.BUDGET_SPLIT["generic"]))]
    return dict(type=ptype, engine="ai", phases=phases, tasks=tasks, risks=risks or None, budget=budget)


# ---------------------------------------------------------------- chat, translation, document text

def chat(context: str, history: list[dict], message: str, locale: str) -> str | None:
    sys = ("You are MissionPilot AI, a project management assistant. Use only the project data below for facts and numbers; "
           "never invent numbers. Be concise and practical. "
           f"Always answer in {lang_name(locale)}, even if the user writes in another language or mixes languages. {KEEP}\n\n"
           f"PROJECT DATA:\n{context}")
    msgs = [{"role": h["role"], "content": h["content"]} for h in history[-10:]] + [{"role": "user", "content": message}]
    return _call(sys, msgs, 1500)


def translate(texts: list[str], target: str) -> list[str] | None:
    if not texts:
        return []
    sys = (f"Translate each string into {lang_name(target)} for a project-management app. {KEEP} "
           'Reply with JSON only: {"t":["...", "..."]} with exactly the same number of items, same order.')
    out = _json(_call(sys, [{"role": "user", "content": json.dumps(texts, ensure_ascii=False)}], 4000))
    if isinstance(out, dict) and isinstance(out.get("t"), list) and len(out["t"]) == len(texts):
        return [str(x) for x in out["t"]]
    return None


def doc_sections(doc_type: str, context: str, section_ids: list[str], locale: str) -> dict | None:
    sys = (f"You write sections of a professional '{doc_type}' document for the project below. "
           f"Write in {lang_name(locale)}. {KEEP} Use only facts from the project data; do not invent numbers or dates. "
           "Each section: 1-2 short paragraphs (max 120 words). "
           'Reply with JSON only: {"<section_id>": "text", ...} for these section ids: ' + ", ".join(section_ids)
           + f"\n\nPROJECT DATA:\n{context}")
    out = _json(_call(sys, [{"role": "user", "content": "Write the sections."}], 3000))
    if isinstance(out, dict):
        return {k: str(v) for k, v in out.items() if k in section_ids and v}
    return None
