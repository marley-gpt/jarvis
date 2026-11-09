"""Speaking visuals: the cards the dashboard shows while JARVIS says a sentence (T86).

`cards(conn, text)` turns one spoken sentence into a short list of cards. Two sources, in order:
1. Markers the reply may carry, never spoken: [[count 412 applications]], [[show T12]], [[list a; b; c]],
   [[date Thursday 3pm call with the supplier]], [[money 380 weekly target]], [[file brain/work/x.md]].
2. What the sentence itself says: known record ids (T12, A3, D7, M-261108-ede3), product names, money,
   counts with a noun ("412 applications"), percentages, a list of three or more ("a, b and c"), a day or
   time with a meeting word, and brain file paths.
Ids are resolved through the records, so a card shows the real title and status, never an invented one.
"""
from __future__ import annotations

import re

from . import memory

MARKER = re.compile(r"\[\[\s*(\w+)\s*([^\]]*?)\s*\]\]")
TASK_ID = re.compile(r"\b(?:T|task\s+)(\d{1,5})\b", re.I)
APPROVAL_ID = re.compile(r"\bA(\d{1,5})\b")
DECISION_ID = re.compile(r"\bD(\d{1,5})\b")
MEMORY_ID = re.compile(r"\bM-\d{6}-[0-9a-f]{4}\b")
MONEY = re.compile(r"(?:(A|US|AU)?\$|AUD\s?|USD\s?)(\d[\d,]*(?:\.\d+)?)(k)?(?:\s+(?:dollars\s+)?([a-z][a-z ]{2,30}?))?(?=[,.;!?]|\s+(?:and|but|so|for|of|in|on|this|today|a\b|per)|$)", re.I)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|per\s?cent)(?:\s+([a-z]+(?:\s[a-z]+)?))?", re.I)
COUNT = re.compile(r"\b(\d[\d,]*)\s+((?:new\s+|open\s+|pending\s+|live\s+)?[a-z][a-z\-]+s)\b", re.I)
DAY = re.compile(r"\b(today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
                 r"(?:\s+(?:at\s+)?(\d{1,2}(?::\d\d)?\s?(?:am|pm)))?", re.I)
EVENT = re.compile(r"\b(call|meeting|booked|scheduled|launch|deadline|due|review|brief)\b", re.I)
FILE = re.compile(r"\b(brain/[\w./\-]+\.(?:md|png|jpe?g|webp|gif|txt|csv|json))\b")
LIST = re.compile(r"(?:^|:\s*|\b(?:are|were|is|include|including)\s+)([\w'&\- ]{2,40}(?:,\s+[\w'&\- ]{2,40})+,?\s+(?:and|or)\s+[\w'&\- ]{2,40})", re.I)
NOT_COUNT_NOUNS = {"years", "hours", "minutes", "seconds", "days", "weeks", "months", "times", "ones", "others", "steps"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MAX_CARDS = 4


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def _task(conn, tid: int) -> dict | None:
    row = conn.execute("SELECT id, title, status, agent, product, deliverable FROM tasks WHERE id = ?", (tid,)).fetchone()
    if not row:
        return None
    return {"kind": "task", "id": f"T{row['id']}", "title": row["title"], "status": row["status"],
            "agent": row["agent"], "product": row["product"], "file": row["deliverable"]}


def _approval(conn, aid: int) -> dict | None:
    row = conn.execute("SELECT id, kind, summary, status, amount, currency, task_id FROM approvals WHERE id = ?", (aid,)).fetchone()
    if not row:
        return None
    return {"kind": "approval", "id": f"A{row['id']}", "title": row["summary"], "status": row["status"],
            "type": row["kind"], "amount": row["amount"], "currency": row["currency"],
            "task": f"T{row['task_id']}" if row["task_id"] else None}


def _decision(conn, did: int) -> dict | None:
    row = conn.execute("SELECT id, decision, rationale, product, ts FROM decisions WHERE id = ?", (did,)).fetchone()
    if not row:
        return None
    return {"kind": "decision", "id": f"D{row['id']}", "title": row["decision"], "detail": row["rationale"],
            "product": row["product"], "when": row["ts"]}


def _memory(conn, mid: str) -> dict | None:
    try:
        e = memory.get(conn, mid)
    except memory.MemoryError:
        return None
    body = re.sub(r"\s+", " ", e.body).strip()
    return {"kind": "memory", "id": mid, "title": e.meta.get("title") or mid, "category": e.meta.get("category"),
            "detail": body[:160] + ("…" if len(body) > 160 else "")}


def _resolve(conn, ref: str) -> dict | None:
    ref = ref.strip()
    if m := re.fullmatch(r"T(\d+)", ref, re.I):
        return _task(conn, int(m[1]))
    if m := re.fullmatch(r"A(\d+)", ref):
        return _approval(conn, int(m[1]))
    if m := re.fullmatch(r"D(\d+)", ref):
        return _decision(conn, int(m[1]))
    if MEMORY_ID.fullmatch(ref):
        return _memory(conn, ref)
    return _product(conn, ref)


def _product(conn, name: str) -> dict | None:
    row = conn.execute("SELECT slug, name, stage, notes FROM products WHERE slug = ? OR lower(name) = lower(?)",
                       (name, name)).fetchone()
    if not row:
        return None
    return {"kind": "product", "id": row["slug"], "title": row["name"], "status": row["stage"]}


def _file(path: str) -> dict:
    return {"kind": "image" if path.lower().endswith(IMAGE_EXT) else "file", "path": path,
            "title": path.rsplit("/", 1)[-1]}


def _marker(conn, kind: str, arg: str) -> list[dict]:
    kind = kind.lower()
    if kind == "count":
        m = re.match(r"([\d,.]+)\s*(.*)", arg)
        return [{"kind": "count", "value": _num(m[1]), "label": m[2] or ""}] if m else []
    if kind == "money":
        m = re.match(r"(?:A\$|\$)?([\d,.]+)\s*(.*)", arg)
        return [{"kind": "money", "value": _num(m[1]), "currency": "AUD", "label": m[2] or ""}] if m else []
    if kind == "percent":
        m = re.match(r"([\d.]+)%?\s*(.*)", arg)
        return [{"kind": "percent", "value": float(m[1]), "label": m[2] or ""}] if m else []
    if kind == "list":
        items = [i.strip() for i in arg.split(";") if i.strip()]
        return [{"kind": "list", "items": items[:6]}] if items else []
    if kind in ("case", "propose"):  # JARVIS arguing for taking a job on: the proposal, then its reasons
        parts = [i.strip() for i in arg.split(";") if i.strip()]
        return [{"kind": "case", "title": parts[0], "items": parts[1:5]}] if parts else []
    if kind == "date":
        m = DAY.search(arg)
        return [{"kind": "date", "day": m[1].title() if m else "", "time": (m[2] or "") if m else "", "label": arg}]
    if kind == "file":
        return [_file(arg)] if arg.startswith("brain/") else []
    if kind == "show":
        out = [c for c in (_resolve(conn, r) for r in re.split(r"[\s;,]+", arg) if r) if c]
        return out
    return []


def strip_markers(text: str) -> str:
    return re.sub(r"\s{2,}", " ", MARKER.sub("", text)).strip()


def cards(conn, text: str) -> list[dict]:
    """The cards for one sentence (or a short reply), in the order they are mentioned. Never more than four."""
    out: list[dict] = []
    seen: set = set()

    def add(card, pos=0):
        if not card:
            return
        key = (card["kind"], card.get("id") or card.get("path") or card.get("value") or tuple(card.get("items") or ()))
        if key in seen:
            return
        seen.add(key)
        out.append((pos, len(out), card))

    for m in MARKER.finditer(text or ""):
        for c in _marker(conn, m[1], m[2]):
            add(c, -1)
    plain = strip_markers(text or "")
    hinted = any(p == -1 for p, _, _ in out)  # the reply said what to show: record ids still count, guesses don't

    for m in TASK_ID.finditer(plain):
        add(_task(conn, int(m[1])), m.start())
    for m in APPROVAL_ID.finditer(plain):
        add(_approval(conn, int(m[1])), m.start())
    for m in DECISION_ID.finditer(plain):
        add(_decision(conn, int(m[1])), m.start())
    for m in MEMORY_ID.finditer(plain):
        add(_memory(conn, m[0]), m.start())
    for m in FILE.finditer(plain):
        add(_file(m[1]), m.start())
    if hinted:
        return [c for _, _, c in sorted(out, key=lambda x: x[:2])][:MAX_CARDS]
    low = plain.lower()
    for row in conn.execute("SELECT slug, name, stage FROM products"):
        name = row["name"].lower()
        at = low.find(name) if len(name) > 2 else -1
        if at >= 0:
            add({"kind": "product", "id": row["slug"], "title": row["name"], "status": row["stage"]}, at)
    money_spans = []
    for m in MONEY.finditer(plain):
        value = _num(m[2]) * (1000 if m[3] else 1)
        cur = "USD" if (m[1] or "").upper() == "US" or plain[m.start():m.start() + 3].upper() == "USD" else "AUD"
        add({"kind": "money", "value": value, "currency": cur, "label": (m[4] or "").strip()}, m.start())
        money_spans.append(m.span())
    for m in PERCENT.finditer(plain):
        add({"kind": "percent", "value": float(m[1]), "label": (m[2] or "").strip()}, m.start())
    for m in COUNT.finditer(plain):
        noun = m[2].lower().split()[-1]
        if noun in NOT_COUNT_NOUNS or any(a <= m.start() < b for a, b in money_spans):
            continue
        add({"kind": "count", "value": _num(m[1]), "label": m[2]}, m.start())
    if (d := DAY.search(plain)) and EVENT.search(plain):
        ev = EVENT.search(plain)
        add({"kind": "date", "day": d[1].title(), "time": d[2] or "", "label": _clause(plain, ev.start())}, d.start())
    if m := LIST.search(plain):
        items = [i.strip() for i in re.split(r",\s*|\s+(?:and|or)\s+", m[1]) if i.strip()]
        items = [re.sub(r"^(?:and|or)\s+", "", i) for i in items]
        if items:  # "The shortlist is Ava": the first item starts after the verb
            items[0] = re.sub(r"^.*\b(?:are|were|is|include|including)\s+", "", items[0], flags=re.I)
        if 3 <= len(items) <= 8:
            add({"kind": "list", "items": items}, m.start(1))
    return [c for _, _, c in sorted(out, key=lambda x: x[:2])][:MAX_CARDS]


def _clause(text: str, at: int) -> str:
    start = max(text.rfind(",", 0, at), text.rfind(".", 0, at)) + 1
    end = min([i for i in (text.find(",", at), text.find(".", at)) if i >= 0] or [len(text)])
    return text[start:end].strip()[:80]
