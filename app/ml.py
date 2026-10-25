"""Small pre-trained AI model that runs on the server for free (no paid API).

Model: sentence-transformers/static-similarity-mrl-multilingual-v1 (Apache-2.0), reduced to its
first 128 dimensions in float16 (~27 MB, see scripts/build_model.py). It turns any sentence in
English, French, Marathi and ~50 other languages into a vector; similar meanings get similar
vectors. It is a *static* embedding model (no neural network runs at request time), so it is
fast on Render's free CPU and uses ~30 MB of memory.

Used for suggestions only — a person always decides:
  * category of a risk or task      (similarity to multilingual category descriptions + keyword rules)
  * duplicate / very similar risks   (cosine similarity ≥ DUP_THRESHOLD)
  * phase of a new task              (meeting notes, import)
  * "is this sentence an action item?" (meeting notes)
If the model files are missing, everything falls back to the keyword rules and the UI says so.
"""
from __future__ import annotations

import re
import threading
from functools import lru_cache
from pathlib import Path

MODEL_DIR = Path(__file__).parent / "models"
EMB_FILE = MODEL_DIR / "static_mrl_multilingual_128.npy"
TOK_FILE = MODEL_DIR / "tokenizer.json"
MODEL_NAME = "static-similarity-mrl-multilingual-v1 (128d)"
DUP_THRESHOLD = 0.62

_lock = threading.Lock()
_state: dict = {}


def _load():
    if "ok" in _state:
        return _state["ok"]
    with _lock:
        if "ok" in _state:
            return _state["ok"]
        try:
            import numpy as np
            from tokenizers import Tokenizer
            _state["E"] = np.load(EMB_FILE, mmap_mode="r")
            _state["tok"] = Tokenizer.from_file(str(TOK_FILE))
            _state["np"] = np
            _state["ok"] = True
        except Exception as e:  # files or packages missing
            _state["ok"] = False
            _state["error"] = str(e)[:200]
    return _state["ok"]


def available() -> bool:
    return _load()


def status() -> dict:
    return dict(on=available(), model=MODEL_NAME, error=_state.get("error"))


@lru_cache(maxsize=4096)
def _embed_one(text: str):
    np = _state["np"]
    ids = _state["tok"].encode(text, add_special_tokens=False).ids
    if not ids:
        return None
    v = np.asarray(_state["E"][ids], dtype=np.float32).mean(axis=0)
    n = float(np.linalg.norm(v))
    return v / n if n else None


def embed(text: str):
    if not text or not available():
        return None
    return _embed_one(text.strip()[:500])


def sim(a, b) -> float:
    if a is None or b is None:
        return 0.0
    return float(a @ b)


# ---------------------------------------------------------------- categories

# risk/task categories asked for: scope, schedule, cost, security, GDPR, people, supplier (+ technical)
CATEGORIES = ["scope", "schedule", "cost", "security", "gdpr", "people", "supplier", "technical"]
# how each suggestion maps to the risk register's own categories
TO_RISKCAT = {"scope": "scope", "schedule": "schedule", "cost": "cost", "security": "security", "gdpr": "compliance",
              "people": "resource", "supplier": "external", "technical": "technical"}
FROM_RISKCAT = {v: k for k, v in TO_RISKCAT.items()}

PROTOTYPES = {
    "scope": ["scope creep, new requirements keep being added", "changes to the scope and unclear requirements",
              "le périmètre change, nouvelles demandes et exigences floues", "व्याप्ती वाढते, नवीन मागण्या आणि अस्पष्ट आवश्यकता"],
    "schedule": ["schedule delay, tasks take longer than planned, deadline missed", "late delivery and milestone slips",
                 "retard du planning, la date limite est dépassée", "वेळापत्रकात उशीर, काम ठरलेल्या वेळेत पूर्ण होत नाही"],
    "cost": ["costs exceed the budget, overspending", "budget overrun and unexpected expenses",
             "les coûts dépassent le budget", "खर्च बजेटपेक्षा जास्त होतो"],
    "security": ["cyber attack, hacking, malware, vulnerability, unauthorized access", "security incident, phishing, ransomware, log monitoring",
                 "security logs are missing, threat detection and monitoring gaps",
                 "attaque informatique, vulnérabilité, sécurité des accès", "सायबर हल्ला, सुरक्षा धोका, हॅकिंग"],
    "gdpr": ["personal data exposed, GDPR non-compliance, privacy breach", "data protection law and consent",
             "données personnelles, conformité RGPD, protection de la vie privée", "वैयक्तिक माहिती उघड होणे, गोपनीयता आणि GDPR"],
    "people": ["key team member unavailable, staff overloaded, skills missing", "people workload, absence, resignation, training",
               "stakeholders or decision makers are not available",
               "un membre clé de l'équipe est absent ou surchargé", "टीममधील महत्त्वाची व्यक्ती उपलब्ध नाही किंवा कामाचा ताण जास्त"],
    "supplier": ["supplier or vendor delivers late, licence or subcontractor problem", "third-party provider failure",
                 "le fournisseur ou le sous-traitant livre en retard", "पुरवठादार किंवा विक्रेता उशिरा देतो"],
    "technical": ["technical integration problems, bugs, performance, architecture", "components do not work together, technical debt",
                  "the system is slow or down, poor data quality, too many false alerts, the app crashes on some devices",
                  "problèmes techniques d'intégration, bugs et performance", "तांत्रिक अडचणी, बग्स आणि प्रणाली एकत्र काम करत नाही"],
}
KEYWORDS = {
    "scope": ["scope", "périmètre", "requirement", "exigence", "change request", "व्याप्ती", "आवश्यकता"],
    "schedule": ["delay", "late", "deadline", "schedule", "retard", "délai", "planning", "उशीर", "वेळापत्रक", "मुदत"],
    "cost": ["cost", "budget", "expense", "coût", "dépense", "खर्च", "बजेट"],
    "security": ["security", "cyber", "attack", "malware", "ransomware", "phishing", "vulnerab", "breach", "log", "sécurité",
                 "attaque", "सुरक्षा", "सायबर", "हल्ला"],
    "gdpr": ["gdpr", "rgpd", "personal data", "privacy", "données personnelles", "consent", "वैयक्तिक", "गोपनीयता"],
    "people": ["team member", "staff", "people", "overload", "absence", "skill", "resign", "équipe", "absent", "surcharg",
               "टीम", "कर्मचारी", "अनुपस्थित"],
    "supplier": ["supplier", "vendor", "licence", "license", "subcontract", "third-party", "fournisseur", "sous-traitant",
                 "पुरवठादार", "विक्रेता"],
    "technical": ["integration", "bug", "performance", "architecture", "technical", "intégration", "technique", "तांत्रिक"],
}


@lru_cache(maxsize=None)
def _proto(label_set: tuple, which: str):
    import numpy as np
    table = PROTOTYPES if which == "cat" else (PHASE_PROTOTYPES if which == "phase" else ACTION_PROTOTYPES)
    out = {}
    for k in label_set:
        vs = [embed(s) for s in table[k]]
        vs = [v for v in vs if v is not None]
        if vs:
            v = np.mean(vs, axis=0)
            out[k] = v / (np.linalg.norm(v) or 1)
    return out


def _keyword_scores(text: str) -> dict:
    low = text.lower()
    return {k: sum(1 for w in ws if w in low) for k, ws in KEYWORDS.items()}


def suggest_category(text: str) -> dict:
    """Returns {label, riskcat, confidence (0-1), engine: 'model+rules' | 'rules'}."""
    kw = _keyword_scores(text)
    scores = {k: 0.25 * min(kw[k], 2) for k in CATEGORIES}
    engine = "rules"
    v = embed(text)
    if v is not None:
        protos = _proto(tuple(CATEGORIES), "cat")
        for k in CATEGORIES:
            scores[k] += sim(v, protos.get(k))
        engine = "model+rules"
    best = max(scores, key=lambda k: scores[k])
    ranked = sorted(scores.values(), reverse=True)
    if scores[best] <= 0:
        return dict(label=None, riskcat=None, confidence=0.0, engine=engine)
    margin = ranked[0] - (ranked[1] if len(ranked) > 1 else 0)
    conf = max(0.0, min(1.0, 0.45 + margin * 2.5)) if engine != "rules" else min(1.0, 0.4 + 0.2 * kw[best])
    return dict(label=best, riskcat=TO_RISKCAT[best], confidence=round(conf, 2), engine=engine)


def duplicates(items: list[dict], threshold: float = DUP_THRESHOLD) -> list[tuple[dict, dict, float]]:
    """items: [{id, code, text}] -> pairs that look like the same risk."""
    if not available():
        return []
    vecs = [(it, embed(it["text"])) for it in items if it.get("text")]
    out = []
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            s = sim(vecs[i][1], vecs[j][1])
            if s >= threshold:
                out.append((vecs[i][0], vecs[j][0], round(s, 2)))
    return sorted(out, key=lambda x: -x[2])


# ---------------------------------------------------------------- phases and action items (meeting notes, import)

PHASE_PROTOTYPES = {
    "initiation": ["project kick-off, charter, stakeholders, proposal", "lancement du projet, charte", "प्रकल्प सुरुवात, सनद"],
    "planning": ["requirements, planning, schedule, budget plan, specification", "exigences, planification, budget", "आवश्यकता, नियोजन, बजेट"],
    "design": ["design, architecture, mock-ups, technical design", "conception, architecture, maquettes", "डिझाइन, आर्किटेक्चर"],
    "build": ["development, build, implement, code, configure, integrate", "développement, coder, configurer, intégrer", "विकास, कोड लिहिणे, तयार करणे"],
    "testing": ["testing, QA, test cases, fix bugs, user acceptance", "tests, recette, corriger les bugs", "चाचणी, बग दुरुस्ती, टेस्टिंग"],
    "deployment": ["deployment, go-live, release, rollout, training users", "déploiement, mise en production, formation", "डिप्लॉयमेंट, लाईव्ह करणे, प्रशिक्षण"],
    "closure": ["closure report, lessons learned, handover, project close", "clôture, retour d'expérience", "प्रकल्प समाप्ती, शिकलेले धडे"],
}
PHASE_KEYWORDS = {
    "initiation": ["kick-off", "kickoff", "charter", "stakeholder", "lancement", "charte"],
    "planning": ["requirement", "plan", "budget", "specification", "exigence", "planning", "नियोजन"],
    "design": ["design", "architecture", "mock", "wireframe", "conception", "maquette", "डिझाइन"],
    "build": ["develop", "build", "implement", "code", "configure", "integrat", "développ", "coder", "configur", "विकास"],
    "testing": ["test", "qa", "bug", "uat", "recette", "चाचणी"],
    "deployment": ["deploy", "go-live", "go live", "release", "rollout", "déploi", "mise en production", "डिप्लॉय"],
    "closure": ["closure", "lessons", "handover", "clôture", "समाप्ती"],
}
ACTION_PROTOTYPES = {
    "action": ["someone will do this task by Friday", "action: prepare and send the document", "we need to finish the test plan",
               "X doit préparer le rapport avant vendredi", "action : envoyer le devis au client",
               "अनिल शुक्रवारपर्यंत अहवाल तयार करेल", "हे काम पूर्ण करावे"],
    "info": ["the meeting started at ten", "we discussed the general situation", "everyone attended the meeting",
             "la réunion a commencé à dix heures", "nous avons discuté de la situation", "बैठक दहा वाजता सुरू झाली"],
}


def suggest_phase(text: str, default: str = "build") -> str:
    """Keywords win; otherwise the model decides only when it is clearly sure; otherwise the current phase."""
    low = text.lower()
    kw = {k: sum(1 for w in ws if w in low) for k, ws in PHASE_KEYWORDS.items()}
    if max(kw.values()) > 0:
        return max(kw, key=lambda k: kw[k])
    v = embed(text)
    if v is None:
        return default
    protos = _proto(tuple(PHASE_PROTOTYPES), "phase")
    scores = sorted(((sim(v, protos.get(k)), k) for k in PHASE_PROTOTYPES), reverse=True)
    return scores[0][1] if scores[0][0] - scores[1][0] > 0.08 and scores[0][0] > 0.3 else default


def action_score(text: str) -> float:
    """> 0 means the sentence looks more like an action item than plain information."""
    v = embed(text)
    if v is None:
        return 0.0
    p = _proto(("action", "info"), "action")
    return sim(v, p.get("action")) - sim(v, p.get("info"))


WORD = re.compile(r"\w+", re.U)
