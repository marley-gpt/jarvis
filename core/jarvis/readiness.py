"""Progress the owner can see: Blake's course coverage and the first-product-test readiness checklist.

academy() scans the captured Skool lessons (brain/files/mentor/skool), which of them
mentor-learning has studied (a "Lesson notes" memory entry exists), and the private
blake-* playbooks. The readiness checklist lives in brain/readiness.json and is updated
with `bin/jarvis ready set <n> <todo|doing|done> [--note ...]`. JARVIS only tells the
owner "ready" when every item is done.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import paths

# Blake's ROA$ classroom: order -> (title, number of lessons)
COURSES = {
    1: ("Intro", 1), 2: ("Product Research", 4), 3: ("Website/CRO", 11), 4: ("Beginner Creatives", 8),
    5: ("Advanced Creatives", 11), 6: ("Campaign Setup", 9), 7: ("Scaling Strategy's", 4), 8: ("Brand Building", 4),
    9: ("Q4 Scaling Guide", 4), 10: ("Tools/Resources/Suppliers", 10), 11: ("External Resources", 1),
}

ITEMS = [
    "Every Blake course captured (videos, transcripts, frames, files)",
    "Every lesson studied by mentor-learning; all blake-* playbooks complete and cited",
    "Blake's rules built into the agents (15 ads per test, BOF/MOF statics, test budget and kill/scale numbers)",
    "One-command pipeline: product in, then research, 15 creatives, PDP, campaign draft and test plan",
    "PDP excellence: Website/CRO studied; shopify agent has a PDP build and review checklist",
    "Store check: Urban Supplied theme, policies and pixel reviewed against Blake (read-only)",
    "Meta check: ad account, pixel and setup reviewed read-only via the Meta connector",
    "Approvals, cost tracking, test records and the feedback loop working end to end",
    "Full rehearsal on an example product (no spend, no live changes) with gaps fixed",
]
STATUSES = ("todo", "doing", "done")


def _path() -> Path:
    return paths.home() / "readiness.json"


def checklist() -> list[dict]:
    saved = json.loads(_path().read_text(encoding="utf-8")) if _path().exists() else {}
    return [{"n": i + 1, "item": text, **saved.get(str(i + 1), {"status": "todo", "note": ""})}
            for i, text in enumerate(ITEMS)]


def set_status(n: int, status: str, note: str | None = None) -> dict:
    if status not in STATUSES or not 1 <= n <= len(ITEMS):
        raise ValueError(f"Use an item 1-{len(ITEMS)} and a status in {STATUSES}")
    saved = json.loads(_path().read_text(encoding="utf-8")) if _path().exists() else {}
    cur = saved.get(str(n), {})
    saved[str(n)] = {"status": status, "note": note if note is not None else cur.get("note", ""), "updated": paths.now()}
    _path().write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return checklist()[n - 1]


def academy(conn) -> dict:
    root = paths.files_dir() / "mentor" / "skool"
    notes = [r[0].lower() for r in conn.execute(
        "SELECT title FROM memory WHERE status = 'active' AND title LIKE 'Lesson notes:%'")]
    courses = []
    for order, (title, total) in COURSES.items():
        dirs = sorted(root.glob(f"{order:02d}-*/*/lesson.json")) if root.exists() else []
        captured = studied = 0
        for lj in dirs:
            captured += 1
            lesson = json.loads(lj.read_text(encoding="utf-8")).get("lesson_title", "").lower()
            if lesson and any(lesson[:40] in n for n in notes):
                studied += 1
        courses.append({"order": order, "title": title, "lessons": total, "captured": min(captured, total),
                        "studied": min(studied, total)})
    playbooks = []
    for d in sorted((Path.home() / ".claude" / "skills").glob("blake-*")):
        skill = d / "SKILL.md"
        if skill.exists():
            playbooks.append({"name": d.name, "lines": skill.read_text(encoding="utf-8").count("\n"),
                              "references": len(list(d.rglob("*.md"))) - 1})
    total = sum(c["lessons"] for c in courses)
    return {"courses": courses, "playbooks": playbooks, "lessons": total,
            "captured": sum(c["captured"] for c in courses), "studied": sum(c["studied"] for c in courses),
            "mentor_entries": conn.execute("SELECT COUNT(*) FROM memory WHERE status='active' AND provenance='mentor'").fetchone()[0]}


def summary(conn) -> dict:
    items = checklist()
    return {"academy": academy(conn), "readiness": items, "ready": all(i["status"] == "done" for i in items)}
