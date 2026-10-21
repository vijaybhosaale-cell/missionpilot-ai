"""Checks every enabled locale against English: same keys, same {placeholders}, same list lengths.
Run: python -m app.check_locales   (exit code 1 on problems)"""
import json
import re
import sys
from pathlib import Path

from . import i18n

PH = re.compile(r"\{(\w+)\}")


def flat(d, p=""):
    out = {}
    for k, v in d.items():
        key = p + k
        if isinstance(v, dict):
            out.update(flat(v, key + "."))
        else:
            out[key] = v
    return out


def placeholders(v):
    if isinstance(v, list):
        return set().union(*[placeholders(x) for x in v]) if v else set()
    return set(PH.findall(v)) if isinstance(v, str) else set()


def check(code: str) -> list[str]:
    problems = []
    for name in ("ui.json", "content.json"):
        en = flat(json.loads((i18n.LOCALE_DIR / "en" / name).read_text(encoding="utf-8")))
        f = i18n.LOCALE_DIR / code / name
        if not f.exists():
            return [f"{code}: {name} missing"]
        tr = flat(json.loads(f.read_text(encoding="utf-8")))
        for k in en:
            if k not in tr:
                problems.append(f"{code}/{name}: missing {k}")
            elif placeholders(en[k]) != placeholders(tr[k]):
                problems.append(f"{code}/{name}: placeholders differ in {k}: {placeholders(en[k])} vs {placeholders(tr[k])}")
            elif isinstance(en[k], list) and (not isinstance(tr[k], list) or (len(tr[k]) != len(en[k]) and not k.startswith("assistant.kw."))):
                problems.append(f"{code}/{name}: list length differs in {k}")
        for k in tr:
            if k not in en:
                problems.append(f"{code}/{name}: extra key {k}")
    return problems


if __name__ == "__main__":
    all_p = []
    for c in sys.argv[1:] or i18n.CODES:
        p = check(c)
        print(f"{c}: {'OK' if not p else str(len(p)) + ' problems'}")
        all_p += p
    for p in all_p[:60]:
        print("  ", p)
    sys.exit(1 if all_p else 0)
