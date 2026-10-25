"""E-mail sending through SMTP (Gmail by default).

Settings come only from environment variables (Render → Environment):
  SMTP_USER       the Gmail address that sends the mails
  SMTP_PASSWORD   a Gmail *App password* (16 characters), never the normal password
  EMAIL_TO        address that receives demo-workspace mails and the admin copy
optional: SMTP_HOST (smtp.gmail.com), SMTP_PORT (587), SMTP_SECURITY (starttls | ssl | none),
          EMAIL_FROM_NAME (MissionPilot AI), EMAIL_DAILY_LIMIT (60 per project per day)

Mails are sent in a background thread so pages never wait for Gmail. Every attempt
is written to the email_log table: queued → sent / failed, or not_configured / skipped.
"""
from __future__ import annotations

import os
import re
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr

from . import db

EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
DAILY_LIMIT_PER_PROJECT = int(os.environ.get("EMAIL_DAILY_LIMIT", "60"))
SYNC = False  # tests set this to True so sending happens inline
SMTP_CLASS = smtplib.SMTP  # tests replace it with a fake server


def cfg() -> dict:
    return dict(user=os.environ.get("SMTP_USER", "").strip(), password=os.environ.get("SMTP_PASSWORD", "").replace(" ", ""),
                host=os.environ.get("SMTP_HOST", "smtp.gmail.com"), port=int(os.environ.get("SMTP_PORT", "587") or 587),
                to=os.environ.get("EMAIL_TO", "").strip(), name=os.environ.get("EMAIL_FROM_NAME", "MissionPilot AI"))


def configured() -> bool:
    c = cfg()
    return bool(c["user"] and c["password"])


def admin_to() -> str:
    c = cfg()
    return c["to"] or c["user"]


def valid(addr: str | None) -> bool:
    return bool(addr) and bool(EMAIL_RE.match(addr.strip())) and not addr.strip().endswith("@demo.missionpilot")


def _today_count(project_id) -> int:
    if project_id is None:
        return 0
    r = db.q1("SELECT COUNT(*) n FROM email_log WHERE project_id=? AND created_at>=? AND status IN ('queued','sent')",
              (project_id, db.now()[:10]))
    return r["n"] if r else 0


def send(to: list[str], subject: str, html: str, text: str, *, event: str, project_id=None, user_id=None,
         attachments: list[tuple[str, bytes, str]] | None = None) -> int:
    """Queue one e-mail. Returns the email_log id. Never raises."""
    rcpts = sorted({a.strip().lower() for a in to if valid(a)})
    status = "queued"
    error = None
    if not configured():
        status, error = "not_configured", "SMTP_USER / SMTP_PASSWORD are not set on the server"
    elif not rcpts:
        status, error = "skipped", "no valid recipient"
    elif _today_count(project_id) >= DAILY_LIMIT_PER_PROJECT:
        status, error = "skipped", f"daily limit of {DAILY_LIMIT_PER_PROJECT} e-mails for this project reached"
    log_id = db.ex("INSERT INTO email_log(project_id,user_id,event,recipients,subject,status,error,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (project_id, user_id, event, ", ".join(rcpts), subject[:200], status, error, db.now()))
    if status != "queued":
        return log_id
    msg = EmailMessage()
    c = cfg()
    msg["Subject"] = subject
    msg["From"] = formataddr((c["name"], c["user"]))
    msg["To"] = ", ".join(rcpts)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for fname, data, mime in attachments or []:
        main, _, sub = mime.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=fname)
    if SYNC:
        _deliver(log_id, msg, c)
    else:
        threading.Thread(target=_deliver, args=(log_id, msg, c), daemon=True).start()
    return log_id


def _deliver(log_id: int, msg: EmailMessage, c: dict) -> None:
    security = os.environ.get("SMTP_SECURITY", "ssl" if c["port"] == 465 else "starttls").lower()
    try:
        if security == "ssl" and SMTP_CLASS is smtplib.SMTP:
            conn = smtplib.SMTP_SSL(c["host"], c["port"], timeout=30, context=ssl.create_default_context())
        else:
            conn = SMTP_CLASS(c["host"], c["port"], timeout=30)
        with conn as s:
            s.ehlo()
            if security == "starttls":
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
            s.login(c["user"], c["password"])
            s.send_message(msg)
        db.ex("UPDATE email_log SET status='sent', sent_at=?, error=NULL WHERE id=?", (db.now(), log_id))
    except Exception as e:  # wrong password, no network, Gmail limit...
        err = str(e)
        if "535" in err or "Username and Password not accepted" in err:
            err = "Gmail refused the login: use an App password (not your normal password). " + err[:160]
        db.ex("UPDATE email_log SET status='failed', error=? WHERE id=?", (err[:400], log_id))


def recent(limit: int = 30, project_ids: list[int] | None = None, user_id: int | None = None) -> list:
    if project_ids is not None:
        ids = project_ids or [-1]
        qs = ",".join("?" * len(ids))
        return db.q(f"SELECT * FROM email_log WHERE project_id IN ({qs}) OR user_id=? ORDER BY id DESC LIMIT ?",
                    (*ids, user_id or -1, limit))
    return db.q("SELECT * FROM email_log ORDER BY id DESC LIMIT ?", (limit,))
