"""Meeting notes → action items (English, French, Marathi).

Rules find the owner (team member names), the deadline (dates, weekdays, "tomorrow", "next week",
"in 3 days"... in the three languages) and the priority; the on-server model scores whether a
sentence is an action item and suggests the phase. With a Claude API key, Claude extracts the
items instead (rules stay as the fallback). Nothing is created here: the page shows a preview
the user edits and confirms.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from . import ai, ml

MR_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
MONTHS = {
    # en
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
    # fr
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    # mr
    "जानेवारी": 1, "फेब्रुवारी": 2, "मार्च": 3, "एप्रिल": 4, "मे": 5, "जून": 6, "जुलै": 7, "ऑगस्ट": 8,
    "सप्टेंबर": 9, "ऑक्टोबर": 10, "नोव्हेंबर": 11, "डिसेंबर": 12,
}
WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6,
    "सोमवार": 0, "मंगळवार": 1, "बुधवार": 2, "गुरुवार": 3, "शुक्रवार": 4, "शनिवार": 5, "रविवार": 6,
}
REL = [  # (regex, function(today, match) -> date)
    (r"\b(today|aujourd'hui|aujourd’hui)\b|आज", lambda d, m: d),
    (r"\b(tomorrow|demain)\b|उद्या", lambda d, m: d + dt.timedelta(days=1)),
    (r"\b(next week|semaine prochaine|la semaine prochaine)\b|पुढच्या आठवड्यात|पुढील आठवड्यात",
     lambda d, m: d + dt.timedelta(days=7 - d.weekday() + 4)),
    (r"\b(end of (the )?week|fin de (la )?semaine)\b|आठवड्याच्या शेवटी", lambda d, m: d + dt.timedelta(days=max(0, 4 - d.weekday()))),
    (r"\b(end of (the )?month|fin du mois)\b|महिन्याच्या शेवटी",
     lambda d, m: (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)),
    (r"\bin (\d+) days?\b|\bdans (\d+) jours?\b|(\d+) दिवसां?त", lambda d, m: d + dt.timedelta(days=int(next(g for g in m.groups() if g)))),
    (r"\bin (\d+) weeks?\b|\bdans (\d+) semaines?\b|(\d+) आठवड्यां?त", lambda d, m: d + dt.timedelta(weeks=int(next(g for g in m.groups() if g)))),
]
PRIORITY = {
    "critical": ["urgent", "asap", "critical", "critique", "immédiatement", "immediately", "तातडीने", "ताबडतोब", "अतिशय महत्त्वाचे"],
    "high": ["important", "high priority", "priorité haute", "prioritaire", "महत्त्वाचे", "लवकर"],
    "low": ["low priority", "when possible", "later", "nice to have", "plus tard", "si possible", "नंतर", "वेळ मिळाल्यावर"],
}
ACTION_MARKERS = re.compile(
    r"^\s*(action|todo|to do|to-do|a\.i\.|ai|tâche|à faire|a faire|कृती|काम)\s*[:\-–]|"
    r"\b(will|must|should|needs? to|has to|have to|to send|to prepare|to finish|to fix|to update|"
    r"doit|doivent|devra|va|vont|faut|à faire"
    r")\b|करेल|करतील|करावे|करावा|करायचे|करणे|पूर्ण\s*कर|पाठव|तयार\s*कर", re.I | re.U)
MODAL_START = re.compile(r"^(will|must|should|needs? to|has to|have to|to|doit|doivent|devra|va|vont|is going to)\s+", re.I)
BULLET = re.compile(r"^\s*(?:[-*•▪◦·]|\d+[.)]|\[\s?\])\s*")


def _next_weekday(today: dt.date, wd: int) -> dt.date:
    delta = (wd - today.weekday()) % 7 or 7
    return today + dt.timedelta(days=delta)


def find_date(text: str, today: dt.date) -> tuple[dt.date | None, tuple[int, int] | None]:
    s = text.translate(MR_DIGITS)
    low = s.lower()
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", low)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3])), m.span()
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", low)
    if m:
        y = int(m[3]) if m[3] else today.year
        y = y + 2000 if y < 100 else y
        try:
            d = dt.date(y, int(m[2]), int(m[1]))
            if not m[3] and d < today:
                d = d.replace(year=y + 1)
            return d, m.span()
        except ValueError:
            pass
    names = "|".join(sorted(map(re.escape, MONTHS), key=len, reverse=True))
    m = re.search(rf"(\d{{1,2}})\s*(?:er\s+)?({names})\.?(?:\s+(\d{{4}}))?|({names})\.?\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?", low)
    if m:
        day, mon, yr = (m[1], m[2], m[3]) if m[1] else (m[5], m[4], m[6])
        try:
            d = dt.date(int(yr) if yr else today.year, MONTHS[mon], int(day))
            if not yr and d < today - dt.timedelta(days=30):
                d = d.replace(year=d.year + 1)
            return d, m.span()
        except ValueError:
            pass
    for pat, fn in REL:
        m = re.search(pat, low)
        if m:
            return fn(today, m), m.span()
    for name, wd in sorted(WEEKDAYS.items(), key=lambda kv: -len(kv[0])):
        i = low.find(name)
        if i >= 0:
            j = i + len(name)
            if low[j:j + len("पर्यंत")] == "पर्यंत":
                j += len("पर्यंत")
            return _next_weekday(today, wd), (i, j)
    return None, None


def find_owner(text: str, members: list[dict]):
    low = text.lower()
    best = None
    for m in members:
        name = (m["name"] or "").strip()
        if not name:
            continue
        for cand in [name] + name.split():
            if len(cand) < 2:
                continue
            pat = r"(?<![\w])@?" + re.escape(cand.lower()) + r"(?![\w])"
            hit = re.search(pat, low)
            if hit and (best is None or len(cand) > best[2]):
                best = (m, hit.span(), len(cand))
            if hit is None and re.search(r"[ऀ-ॿ]", cand) and cand.lower() in low:  # Devanagari + suffixes
                i = low.find(cand.lower())
                if best is None or len(cand) > best[2]:
                    best = (m, (i, i + len(cand)), len(cand))
    return (best[0], best[1]) if best else (None, None)


def find_priority(text: str) -> str:
    low = text.lower()
    for level in ("critical", "high", "low"):
        if any(w in low for w in PRIORITY[level]):
            return level
    return "medium"


PRIO_WORDS = re.compile(r"[(\[]?\s*(" + "|".join(re.escape(w) for ws in PRIORITY.values() for w in sorted(ws, key=len, reverse=True))
                        + r")\s*[)\]]?", re.I)


def _clean_title(text: str, spans: list) -> str:
    s = text
    for a, b in sorted([x for x in spans if x], reverse=True):
        s = s[:a] + " " + s[b:]
    s = PRIO_WORDS.sub(" ", s)
    s = BULLET.sub("", s)
    s = re.sub(r"^\s*(action|todo|to do|to-do|a\.i\.|tâche|à faire|कृती)\s*[:\-–]\s*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,;:.-–—")
    s = re.sub(r"\b(by|before|until|on|avant|pour|le|d'ici|jusqu'à)\s*$", "", s, flags=re.I).strip(" ,;:.-–—")
    s = re.sub(r"\s+\b(by|before|until|avant|pour|d'ici)\b\s*([,.;]|$)", r"\2", s, flags=re.I).strip(" ,;:.-–—")
    s = MODAL_START.sub("", s).strip()
    s = re.sub(r"^\b(will|must|should)\b\s*", "", s, flags=re.I)
    return (s[:1].upper() + s[1:])[:200] if s else ""


def split_sentences(text: str) -> list[str]:
    out = []
    for line in (text or "").replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if BULLET.match(line):
            out.append(line)
            continue
        out += [p.strip() for p in re.split(r"(?<=[.!?।])\s+", line) if p.strip()]
    return out


def extract_rules(text: str, members: list[dict], today: dt.date, phase_default: str = "build") -> list[dict]:
    items = []
    for sent in split_sentences(text):
        if len(sent) < 6:
            continue
        member, ospan = find_owner(sent, members)
        date, dspan = find_date(sent, today)
        marker = bool(ACTION_MARKERS.search(sent))
        bullet = bool(BULLET.match(sent))
        model = ml.action_score(sent) if ml.available() else 0.0
        score = 1.0 * marker + 0.6 * bool(member) + 0.6 * bool(date) + 0.3 * bullet + 1.5 * model
        if score < 1.0:
            continue
        title = _clean_title(sent, [ospan, dspan])
        if len(title) < 3:
            continue
        items.append(dict(title=title, owner_id=member["id"] if member else None, owner=member["name"] if member else "",
                          deadline=date.isoformat() if date else "", priority=find_priority(sent),
                          phase=ml.suggest_phase(title, phase_default), source=sent, confidence=round(min(1.0, score / 3), 2)))
    return items


def extract(text: str, members: list[dict], today: dt.date, loc: str, phase_default: str = "build") -> tuple[list[dict], str]:
    """Returns (items, engine) where engine is 'claude' or 'rules+model' / 'rules'."""
    if ai.available():
        got = _extract_claude(text, members, today, loc, phase_default)
        if got is not None:
            return got, "claude"
    return extract_rules(text, members, today, phase_default), ("rules+model" if ml.available() else "rules")


def _extract_claude(text, members, today, loc, phase_default):
    names = [m["name"] for m in members if m["name"]]
    sys = ("Extract the action items from these meeting notes. Today is " + today.isoformat() + ". "
           "Team members: " + ", ".join(names) + ". Reply with JSON only: "
           '{"items":[{"title":"short imperative task title in the notes\' language","owner":"exact team member name or empty",'
           '"deadline":"YYYY-MM-DD or empty","priority":"low|medium|high|critical"}]}. Do not invent items or owners.')
    raw = ai._call(sys, [{"role": "user", "content": text[:12000]}], 2000)
    out = ai._json(raw)
    if not isinstance(out, dict) or not isinstance(out.get("items"), list):
        return None
    by_name = {m["name"].lower(): m for m in members if m["name"]}
    items = []
    for it in out["items"][:40]:
        if not isinstance(it, dict) or not str(it.get("title", "")).strip():
            continue
        m = by_name.get(str(it.get("owner", "")).strip().lower())
        dl = str(it.get("deadline", ""))[:10]
        try:
            dt.date.fromisoformat(dl)
        except ValueError:
            dl = ""
        title = str(it["title"]).strip()[:200]
        items.append(dict(title=title, owner_id=m["id"] if m else None, owner=m["name"] if m else "", deadline=dl,
                          priority=it.get("priority") if it.get("priority") in ("low", "medium", "high", "critical") else "medium",
                          phase=ml.suggest_phase(title, phase_default), source="", confidence=1.0))
    return items


def to_json(items) -> str:
    return json.dumps(items, ensure_ascii=False)
