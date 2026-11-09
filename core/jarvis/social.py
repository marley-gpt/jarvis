"""Social media manager: a content calendar from the active products, drafted posts and comment replies,
and nothing public without the owner's yes (T101).

Everything queues in brain/work/social/queue.json. Drafting the words is creative work, so `social brief` writes
a brief for the creative-director (skills: social, content-creation, copywriting, brand-review; Blake's honesty
rules from blake-creatives) and can start it as a durable worker; the worker files its drafts with
`social draft` / `social reply`. Posting or replying publicly is outward-facing: `social submit` raises an
approval (kind other) that reaches the owner's phone, and `social posted` refuses anything not approved.

    bin/jarvis social plan [--days 7]              calendar slots from the active products
    bin/jarvis social brief [--worker]             the drafting brief (and a creative-director worker)
    bin/jarvis social draft S<n> --caption '...'   file a post's caption, hashtags and image brief
    bin/jarvis social comment --platform ig --text '...' [--post S<n>] [--author x]   log a comment to answer
    bin/jarvis social reply R<n> --text '...'      draft the reply
    bin/jarvis social submit S<n>|R<n>             ask the owner (approval request) before it goes out
    bin/jarvis social sync                         pick up the owner's approve/reject
    bin/jarvis social posted S<n>|R<n> [--url ..]  mark it published (only once approved)
    bin/jarvis social list [--status drafted]
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from . import paths, records

PLATFORMS = ("instagram", "facebook", "tiktok")
STATUSES = ("idea", "drafted", "needs-review", "submitted", "approved", "rejected", "posted")
# Organic formats that suit a static-ads store; the angle comes from the product's research when there is one.
FORMATS = [
    ("problem-solution", "Name the everyday problem the product fixes, then show it fixing it."),
    ("how-to", "A three-step how to use it, one step per slide or beat."),
    ("myth-vs-fact", "One common belief about the problem, and the honest fact."),
    ("behind-the-scenes", "How we pick and check products: honest, no hype."),
    ("customer-question", "Answer a real question buyers ask (from market research or comments)."),
    ("offer", "The current offer, plainly: price, bundle, shipping, guarantee."),
    ("before-after-routine", "A routine the product fits into. No invented results or medical claims."),
]
# D11: never fake reviews, experts or unprovable health claims. A draft that trips this goes to needs-review.
HONESTY = re.compile(r"\b(doctors?|dermatologists?|clinically|scientifically proven|proven to|cures?d?|heals?|FDA|"
                     r"guaranteed results|miracle|\d[\d,.]*\s*k?\+?\s*(?:5[- ]star\s+)?reviews|thousands of happy|"
                     r"as seen on|#1 rated)\b", re.I)


class SocialError(records.RecordError):
    pass


def folder() -> Path:
    d = paths.work_dir() / "social"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load() -> dict:
    try:
        q = json.loads((folder() / "queue.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        q = {}
    q.setdefault("posts", [])
    q.setdefault("replies", [])
    return q


def save(q: dict) -> None:
    tmp = folder() / "queue.json.tmp"
    tmp.write_text(json.dumps(q, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder() / "queue.json")


def _next(items: list, prefix: str) -> str:
    return f"{prefix}{max([int(i['id'][1:]) for i in items] or [0]) + 1}"


def find(q: dict, ref: str) -> dict:
    ref = str(ref).upper()
    for item in q["posts"] + q["replies"]:
        if item["id"] == ref:
            return item
    raise SocialError(f"No social item {ref}. See `bin/jarvis social list`.")


def flags(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in HONESTY.finditer(text or "")})


def active_products(conn) -> list[str]:
    """Products a shopper can buy now; ideas and research aren't on the store, so they get no posts."""
    rows = conn.execute("SELECT slug FROM products WHERE stage IN ('ready','testing','passed','scaling') "
                        "ORDER BY CASE stage WHEN 'scaling' THEN 0 WHEN 'testing' THEN 1 WHEN 'passed' THEN 2 "
                        "ELSE 3 END, updated DESC").fetchall()
    return [r["slug"] for r in rows]


def plan(conn, days: int = 7, platforms=("instagram", "facebook"), start: date | None = None) -> list[dict]:
    """Add one idea slot per day and platform for the coming days, rotating products and formats. Days that already
    have a slot for a platform are left alone, so running it daily just tops the calendar up."""
    q = load()
    products = active_products(conn) or ["urban-supplied"]
    taken = {(p["day"], p["platform"]) for p in q["posts"]}
    start = start or date.today() + timedelta(days=1)
    added = []
    n = len(q["posts"])
    for d in range(days):
        day = (start + timedelta(days=d)).isoformat()
        for platform in platforms:
            if platform not in PLATFORMS:
                raise SocialError(f"Platform must be one of: {', '.join(PLATFORMS)}")
            if (day, platform) in taken:
                continue
            fmt, how = FORMATS[n % len(FORMATS)]
            item = {"id": _next(q["posts"], "S"), "day": day, "platform": platform, "product": products[n % len(products)],
                    "format": fmt, "brief": how, "caption": "", "hashtags": "", "image_brief": "", "status": "idea",
                    "flags": [], "approval": None, "url": None, "created": paths.now(), "updated": paths.now()}
            q["posts"].append(item)
            added.append(item)
            n += 1
    save(q)
    return added


def draft(ref: str, caption: str, hashtags: str = "", image_brief: str = "") -> dict:
    q = load()
    item = find(q, ref)
    if not item["id"].startswith("S"):
        raise SocialError("Use `social reply` for a comment reply.")
    if item["status"] in ("submitted", "approved", "posted"):
        raise SocialError(f"{item['id']} is already {item['status']}; plan a new slot instead of changing it.")
    if not (caption or "").strip():
        raise SocialError("A draft needs a caption.")
    found = flags(" ".join([caption, hashtags, image_brief]))
    item.update(caption=caption.strip(), hashtags=hashtags.strip(), image_brief=image_brief.strip(), flags=found,
                status="needs-review" if found else "drafted", updated=paths.now())
    save(q)
    return item


def comment(platform: str, text: str, post: str | None = None, author: str | None = None) -> dict:
    if not (text or "").strip():
        raise SocialError("Give the comment's text.")
    q = load()
    item = {"id": _next(q["replies"], "R"), "platform": platform, "post": post, "author": author, "comment": text.strip(),
            "reply": "", "status": "idea", "flags": [], "approval": None, "url": None, "created": paths.now(),
            "updated": paths.now()}
    q["replies"].append(item)
    save(q)
    return item


def reply(ref: str, text: str) -> dict:
    q = load()
    item = find(q, ref)
    if not item["id"].startswith("R"):
        raise SocialError("Use `social draft` for a post.")
    if item["status"] in ("submitted", "approved", "posted"):
        raise SocialError(f"{item['id']} is already {item['status']}.")
    found = flags(text)
    item.update(reply=(text or "").strip(), flags=found, status="needs-review" if found else "drafted", updated=paths.now())
    save(q)
    return item


def submit(conn, ref: str, task=None) -> tuple[dict, int]:
    """Raise the owner's approval for one post or reply. Nothing goes out before it is approved."""
    q = load()
    item = find(q, ref)
    if item["status"] == "needs-review":
        raise SocialError(f"{item['id']} trips the honesty rules ({', '.join(item['flags'])}): rewrite it first (D11).")
    if item["status"] != "drafted":
        raise SocialError(f"{item['id']} is {item['status']}; only a drafted item can be submitted.")
    if item["id"].startswith("S"):
        summary = f"Post on {item['platform']} {item['day']} ({item['product']}): {item['caption'][:120]}"
        detail = f"{item['caption']}\n\n{item['hashtags']}\n\nImage: {item['image_brief']}".strip()
    else:
        summary = f"Reply on {item['platform']} to {item['author'] or 'a comment'}: {item['reply'][:120]}"
        detail = f"Comment: {item['comment']}\n\nReply: {item['reply']}"
    aid = records.approval_request(conn, "other", summary, detail=f"Social {item['id']}. {detail}", task=task)
    item.update(status="submitted", approval=aid, updated=paths.now())
    save(q)
    return item, aid


def sync(conn) -> list[dict]:
    """Carry the owner's decisions over from the approvals table."""
    q = load()
    changed = []
    for item in q["posts"] + q["replies"]:
        if item["status"] != "submitted" or not item.get("approval"):
            continue
        row = conn.execute("SELECT status FROM approvals WHERE id = ?", (item["approval"],)).fetchone()
        if row and row["status"] in ("approved", "rejected"):
            item.update(status=row["status"], updated=paths.now())
            changed.append(item)
    if changed:
        save(q)
    return changed


def posted(conn, ref: str, url: str | None = None) -> dict:
    sync(conn)
    q = load()
    item = find(q, ref)
    if item["status"] != "approved":
        raise SocialError(f"{item['id']} is {item['status']}: it can only go out once the owner has approved it.")
    item.update(status="posted", url=url, updated=paths.now())
    save(q)
    return item


def listing(status: str | None = None) -> list[dict]:
    q = load()
    items = sorted(q["posts"], key=lambda p: (p["day"], p["platform"])) + q["replies"]
    return [i for i in items if not status or i["status"] == status]


def board() -> dict:
    q = load()
    count = lambda items: {s: sum(1 for i in items if i["status"] == s) for s in STATUSES}  # noqa: E731
    today = date.today().isoformat()
    return {"posts": count(q["posts"]), "replies": count(q["replies"]),
            "upcoming": [p for p in sorted(q["posts"], key=lambda p: p["day"]) if p["day"] >= today][:14],
            "replies_open": [r for r in q["replies"] if r["status"] not in ("posted", "rejected")][:20]}


def brief(conn) -> Path:
    """Write the creative-director's drafting brief for every slot and comment still needing words."""
    q = load()
    posts = [p for p in q["posts"] if p["status"] in ("idea", "needs-review")]
    replies = [r for r in q["replies"] if r["status"] in ("idea", "needs-review")]
    if not posts and not replies:
        raise SocialError("Nothing to draft: run `bin/jarvis social plan` or log a comment first.")
    lines = [f"# Social drafting brief {datetime.now():%Y-%m-%d}", "",
             "You are the creative-director. Draft organic posts and comment replies for Urban Supplied.",
             "Load the skills `social`, `content-creation`, `copywriting` and `brand-review`, and `blake-creatives` for "
             "Blake's offer and compliance rules. Read each product's research first: `bin/jarvis product show <slug>` "
             "and `bin/jarvis mem search '<product> angle' -p <slug>`. Check `bin/jarvis feedback digest` and mistakes "
             "(`bin/jarvis mem search mistake -c procedures`).",
             "", "Rules: no fake reviews or review counts, no invented doctors or experts, no unprovable health claims "
             "(decision D11); real customer language only where research recorded it. Short, native to the platform, "
             "one clear call to action. Organic posts, not ads: no paid boosts.", "",
             "File each draft (single quotes; no apostrophes inside, or use --caption-file):",
             "  bin/jarvis social draft S<n> --caption '...' --hashtags '#a #b' --image-brief '...'",
             "  bin/jarvis social reply R<n> --text '...'",
             "Do NOT submit, post or reply publicly: JARVIS submits them to the owner for approval.", "", "## Posts"]
    lines += [f"- {p['id']} {p['day']} {p['platform']} [{p['product']}] {p['format']}: {p['brief']}"
              + (f" (rewrite: flagged {', '.join(p['flags'])})" if p["flags"] else "") for p in posts] or ["- none"]
    lines += ["", "## Comments to answer"]
    lines += [f"- {r['id']} {r['platform']} from {r['author'] or 'someone'}: \"{r['comment']}\"" for r in replies] or ["- none"]
    path = folder() / f"brief-{datetime.now():%Y-%m-%d-%H%M}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
