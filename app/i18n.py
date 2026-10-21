"""Localization layer: locale config, translation lookup with fallback, plurals,
missing-key tracking and locale-aware formatting.

Adding a language = add one entry to LOCALES + one folder in app/locales/<code>/
with ui.json and content.json. Nothing else changes.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from functools import lru_cache
from pathlib import Path

from babel import Locale
from babel.dates import format_date as _bd, format_datetime as _bdt
from babel.numbers import format_currency as _bc, format_decimal as _bn

LOCALE_DIR = Path(__file__).parent / "locales"
DEFAULT = "en"

# script -> font stacks (web, PDF) and Word fonts
SCRIPTS = {
    "latin": {"css": "'Noto Sans', 'Segoe UI', system-ui, -apple-system, Arial, sans-serif",
              "gfonts": [], "docx_latin": "Calibri", "docx_cs": "Calibri", "docx_ea": None},
    "deva": {"css": "'Noto Sans', 'Noto Sans Devanagari', 'Nirmala UI', 'Mangal', 'FreeSans', sans-serif",
             "gfonts": ["Noto+Sans+Devanagari:wght@400;500;600;700"],
             "docx_latin": "Calibri", "docx_cs": "Nirmala UI", "docx_ea": None},
    "arab": {"css": "'Noto Sans Arabic', 'Noto Naskh Arabic', 'Segoe UI', Tahoma, 'DejaVu Sans', sans-serif",
             "gfonts": ["Noto+Sans+Arabic:wght@400;500;600;700"],
             "docx_latin": "Arial", "docx_cs": "Arial", "docx_ea": None},
    "jpan": {"css": "'Noto Sans', 'Noto Sans JP', 'Noto Sans CJK JP', 'Yu Gothic', Meiryo, sans-serif",
             "gfonts": ["Noto+Sans+JP:wght@400;500;700"],
             "docx_latin": "Calibri", "docx_cs": "Calibri", "docx_ea": "Yu Gothic"},
    "hans": {"css": "'Noto Sans', 'Noto Sans SC', 'Noto Sans CJK SC', 'Microsoft YaHei', 'PingFang SC', sans-serif",
             "gfonts": ["Noto+Sans+SC:wght@400;500;700"],
             "docx_latin": "Calibri", "docx_cs": "Calibri", "docx_ea": "Microsoft YaHei"},
}

LOCALES = {
    "en":    {"name": "English",   "english": "English",            "dir": "ltr", "script": "latin", "babel": "en",         "tts": ["en-GB", "en-US", "en"], "currency": "EUR", "docx_lang": "en-GB"},
    "fr":    {"name": "Français",  "english": "French",             "dir": "ltr", "script": "latin", "babel": "fr",         "tts": ["fr-FR", "fr"],          "currency": "EUR", "docx_lang": "fr-FR"},
    "mr":    {"name": "मराठी",      "english": "Marathi",            "dir": "ltr", "script": "deva",  "babel": "mr",         "tts": ["mr-IN", "mr"],          "currency": "INR", "docx_lang": "mr-IN"},
    "hi":    {"name": "हिन्दी",      "english": "Hindi",              "dir": "ltr", "script": "deva",  "babel": "hi",         "tts": ["hi-IN", "hi"],          "currency": "INR", "docx_lang": "hi-IN"},
    "es":    {"name": "Español",   "english": "Spanish",            "dir": "ltr", "script": "latin", "babel": "es",         "tts": ["es-ES", "es-MX", "es"], "currency": "EUR", "docx_lang": "es-ES"},
    "de":    {"name": "Deutsch",   "english": "German",             "dir": "ltr", "script": "latin", "babel": "de",         "tts": ["de-DE", "de"],          "currency": "EUR", "docx_lang": "de-DE"},
    "pt":    {"name": "Português", "english": "Portuguese",         "dir": "ltr", "script": "latin", "babel": "pt_PT",      "tts": ["pt-PT", "pt-BR", "pt"], "currency": "EUR", "docx_lang": "pt-PT"},
    "it":    {"name": "Italiano",  "english": "Italian",            "dir": "ltr", "script": "latin", "babel": "it",         "tts": ["it-IT", "it"],          "currency": "EUR", "docx_lang": "it-IT"},
    "ar":    {"name": "العربية",    "english": "Arabic",             "dir": "rtl", "script": "arab",  "babel": "ar",         "tts": ["ar-SA", "ar-EG", "ar"], "currency": "EUR", "docx_lang": "ar-SA"},
    "ja":    {"name": "日本語",      "english": "Japanese",           "dir": "ltr", "script": "jpan",  "babel": "ja",         "tts": ["ja-JP", "ja"],          "currency": "EUR", "docx_lang": "ja-JP"},
    "zh-CN": {"name": "简体中文",    "english": "Chinese (Simplified)", "dir": "ltr", "script": "hans",  "babel": "zh_Hans_CN", "tts": ["zh-CN", "zh"],          "currency": "EUR", "docx_lang": "zh-CN"},
}
# Languages switched on. The others are configured but on standby: to switch one on, add its
# folder app/locales/<code>/ (ui.json, content.json, meta.json) and list the code here.
ENABLED = ["en", "fr", "mr"]
STANDBY = [c for c in LOCALES if c not in ENABLED]
CODES = [c for c in LOCALES if c in ENABLED]
CURRENCIES = ["EUR", "INR", "USD", "GBP", "CHF", "JPY", "CNY", "AED", "BRL"]


def norm(code: str | None) -> str:
    """Normalise a language tag to an enabled locale code (falls back to English)."""
    if not code:
        return DEFAULT
    code = code.strip()
    if code in CODES:
        return code
    low = code.lower().replace("_", "-")
    for c in CODES:
        if c.lower() == low:
            return c
    base = low.split("-")[0]
    if base == "zh" and "zh-CN" in CODES:
        return "zh-CN"
    return base if base in CODES else DEFAULT


def from_accept_language(header: str | None) -> str:
    for part in (header or "").split(","):
        tag = part.split(";")[0].strip()
        if tag:
            c = norm(tag)
            if c != DEFAULT or tag.lower().startswith("en"):
                return c
    return DEFAULT


def info(code: str) -> dict:
    c = LOCALES[norm(code)]
    s = SCRIPTS[c["script"]]
    return {**c, "code": norm(code), "font_css": s["css"], "gfonts": s["gfonts"], "script_fonts": s}


# ---------------------------------------------------------------- catalogs
def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and not _is_plural(v):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


_PLURAL_KEYS = {"zero", "one", "two", "few", "many", "other"}


def _is_plural(v: dict) -> bool:
    return bool(v) and set(v) <= _PLURAL_KEYS and "other" in v


@lru_cache(maxsize=None)
def catalog(code: str) -> dict:
    data = {}
    folder = LOCALE_DIR / code
    for name in ("ui.json", "content.json"):
        f = folder / name
        if f.exists():
            data.update(_flatten(json.loads(f.read_text(encoding="utf-8"))))
    return data


@lru_cache(maxsize=None)
def meta(code: str) -> dict:
    f = LOCALE_DIR / code / "meta.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"status": "missing"}


def reload():
    catalog.cache_clear()
    meta.cache_clear()


# missing-translation tracking (flushed to DB by the app)
_missing: dict[tuple[str, str], int] = {}
_lock = threading.Lock()


def _note_missing(key: str, code: str):
    with _lock:
        _missing[(key, code)] = _missing.get((key, code), 0) + 1


def drain_missing() -> dict:
    with _lock:
        out = dict(_missing)
        _missing.clear()
    return out


def _humanize(key: str) -> str:
    last = key.rsplit(".", 1)[-1].replace("_", " ")
    return last[:1].upper() + last[1:]


_PARAM = re.compile(r"\{(\w+)\}")


def has(key: str, code: str) -> bool:
    return key in catalog(norm(code))


def t(key: str, code: str = DEFAULT, **params) -> str:
    code = norm(code)
    val = catalog(code).get(key)
    if val is None:
        if code != DEFAULT:
            _note_missing(key, code)
        val = catalog(DEFAULT).get(key)
        if val is None:
            _note_missing(key, DEFAULT)
            val = _humanize(key)
    if isinstance(val, dict):  # plural
        n = params.get("n", params.get("count", 0))
        form = plural_form(n, code)
        val = val.get(form) or val.get("other", "")
    if isinstance(val, list):
        return val  # type: ignore[return-value]
    if params:
        val = _PARAM.sub(lambda m: _fmt_param(params.get(m.group(1), m.group(0)), code), val)
    return val


def tl(key: str, code: str = DEFAULT) -> list:
    """List-valued key (e.g. bullet lists)."""
    v = catalog(norm(code)).get(key)
    if not isinstance(v, list):
        if norm(code) != DEFAULT:
            _note_missing(key, norm(code))
        v = catalog(DEFAULT).get(key, [])
    return v if isinstance(v, list) else []


def _fmt_param(v, code):
    if isinstance(v, bool) or v is None:
        return str(v)
    if isinstance(v, int):
        return num(v, code)
    if isinstance(v, float):
        return num(v, code, 1)
    return str(v)


def plural_form(n, code: str) -> str:
    try:
        return Locale.parse(LOCALES[norm(code)]["babel"]).plural_form(abs(n))
    except Exception:
        return "one" if n == 1 else "other"


# ---------------------------------------------------------------- formatting
def _bl(code):
    return LOCALES[norm(code)]["babel"]


def num(v, code: str = DEFAULT, decimals: int = 0) -> str:
    fmt = "#,##0" + ("." + "0" * decimals if decimals else "")
    try:
        return _bn(v, format=fmt, locale=_bl(code), numbering_system="latn")
    except Exception:
        return f"{v:,.{decimals}f}"


def pct(v, code: str = DEFAULT) -> str:
    """v in 0..100"""
    s = num(round(v), code)
    c = norm(code)
    if c in ("fr", "de", "es", "pt"):
        return s + " %"
    return s + "%"


def money_minor(minor: int, currency: str, code: str = DEFAULT, decimals: bool = False) -> str:
    amount = (minor or 0) / (1 if currency == "JPY" else 100)
    try:
        s = _bc(amount, currency, locale=_bl(code), numbering_system="latn",
                format_type="standard", currency_digits=decimals)
        if not decimals:
            s = re.sub(r"[.,]00(?=\D*$)", "", s)
        return s.replace("‏", "")
    except Exception:
        return f"{amount:,.0f} {currency}"


def date(d, code: str = DEFAULT, style: str = "medium") -> str:
    if not d:
        return "—"
    if isinstance(d, str):
        d = dt.date.fromisoformat(d[:10])
    try:
        return _bd(d, style, locale=_bl(code)).replace("‏", "")
    except Exception:
        return d.isoformat()


def datetime(v, code: str = DEFAULT, style: str = "medium") -> str:
    if isinstance(v, str):
        v = dt.datetime.fromisoformat(v)
    try:
        return _bdt(v, style, locale=_bl(code)).replace("‏", "")
    except Exception:
        return v.isoformat(sep=" ", timespec="minutes")


def short_date(d, code: str = DEFAULT) -> str:
    if isinstance(d, str):
        d = dt.date.fromisoformat(d[:10])
    try:
        return _bd(d, "d MMM", locale=_bl(code)).replace("‏", "")
    except Exception:
        return d.strftime("%d %b")


def month_label(d, code: str = DEFAULT) -> str:
    try:
        return _bd(d, "MMM y", locale=_bl(code)).replace("‏", "")
    except Exception:
        return d.strftime("%b %Y")


def weekday_short(d, code: str = DEFAULT) -> str:
    try:
        return _bd(d, "EEEEE", locale=_bl(code))
    except Exception:
        return d.strftime("%a")[0]


def currency_symbol(currency: str, code: str = DEFAULT) -> str:
    from babel.numbers import get_currency_symbol
    try:
        return get_currency_symbol(currency, locale=_bl(code))
    except Exception:
        return currency


def js_bundle(code: str, prefixes=("js.", "tour.", "q.", "common.", "voice.", "share.", "progress.", "gantt.today", "report.none",
                                   "doc.c.role", "status.", "priority.")) -> dict:
    """Strings the browser needs (tour, popup questions, voice, share)."""
    en = catalog(DEFAULT)
    cur = catalog(norm(code))
    out = {}
    for k, v in en.items():
        if k.startswith(prefixes):
            out[k] = cur.get(k, v)
    return out
