"""Compact state summaries, sized to be injected into context cheaply."""

from __future__ import annotations

from . import memory, records


def session_brief(conn, max_tasks: int = 8) -> str:
    out = ["# JARVIS brief (auto-loaded from brain/)"]

    core = memory.listing(conn, category="business", status="active", limit=6) + \
        memory.listing(conn, category="preferences", status="active", limit=6)
    if core:
        out.append("\n## Standing knowledge (read with `bin/jarvis mem get <id>` when relevant)")
        out += [f"- {r['id']} {r['title']}" for r in core]

    products = conn.execute(
        "SELECT slug, stage FROM products WHERE stage NOT IN ('archived','failed') ORDER BY updated DESC LIMIT 8"
    ).fetchall()
    if products:
        out.append("\n## Active products")
        out.append(", ".join(f"{p['slug']} ({p['stage']})" for p in products))

    tasks = records.task_list(conn, open_only=True, limit=max_tasks)
    if tasks:
        out.append("\n## Open tasks")
        out += [task_line(t) for t in tasks]

    approvals = conn.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY id").fetchall()
    if approvals:
        out.append("\n## Waiting for the user's approval")
        out += [f"- A{a['id']} [{a['kind']}] {a['summary']}" for a in approvals]

    running = conn.execute("SELECT * FROM tests WHERE status IN ('running','extended') ORDER BY id").fetchall()
    if running:
        out.append("\n## Running product tests")
        out += [f"- X{t['id']} {t['product']}: {t['hypothesis'][:90]}" for t in running]

    recent = conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 3").fetchall()
    if recent:
        out.append("\n## Recent decisions")
        out += [f"- D{d['id']} {d['ts'][:10]} {d['decision'][:110]}" for d in recent]

    ok, spent, budget = records.cost_check(conn, 0)
    st = memory.stats(conn)
    out.append(
        f"\n## Status\nMemory: {st['total']} entries"
        + (f", {st['needs_review']} need review" if st["needs_review"] else "")
        + f". Paid AI/API spend this month: A${spent:.2f} of A${budget:.2f}."
    )
    if st["total"] == 0:
        out.append("Brain is empty: run `bin/jarvis init` to create folders and seed business knowledge.")
    return "\n".join(out)


def task_line(t) -> str:
    bits = [f"- T{t['id']} [{t['status']}]"]
    if t["agent"]:
        bits.append(f"@{t['agent']}")
    if t["product"]:
        bits.append(f"<{t['product']}>")
    bits.append(t["title"])
    if t["parent_id"]:
        bits.append(f"(sub of T{t['parent_id']})")
    return " ".join(bits)


def product_dossier(conn, slug: str) -> str:
    """Everything JARVIS knows about one product, as an index of pointers."""
    p = conn.execute("SELECT * FROM products WHERE slug = ?", (slug,)).fetchone()
    if not p:
        raise records.RecordError(f"Unknown product '{slug}'")
    out = [f"# {p['name']} ({p['slug']}) — stage: {p['stage']}"]
    if p["notes"]:
        out.append(p["notes"])
    mems = memory.listing(conn, product=slug, limit=100)
    if mems:
        out.append("\n## Memory")
        out += [f"- {m['id']} [{m['category']}/{m['provenance']}/{m['status']}] {m['title']}" for m in mems]
    tasks = records.task_list(conn, product=slug, limit=100)
    if tasks:
        out.append("\n## Tasks")
        out += [task_line(t) + (f" → {t['deliverable']}" if t["deliverable"] else "") for t in tasks]
    tests = conn.execute("SELECT * FROM tests WHERE product = ? ORDER BY id", (slug,)).fetchall()
    if tests:
        out.append("\n## Tests")
        for t in tests:
            ev = records.evaluate(conn, t["id"])
            out.append(f"- X{t['id']} [{t['status']}] {t['hypothesis'][:80]} — spend ${ev['spend']:.2f}, "
                       f"purchases {ev['purchases']}, ROAS {ev['roas'] or '-'}")
    decisions = conn.execute("SELECT * FROM decisions WHERE product = ? ORDER BY id", (slug,)).fetchall()
    if decisions:
        out.append("\n## Decisions")
        out += [f"- D{d['id']} {d['ts'][:10]} {d['decision']}" for d in decisions]
    return "\n".join(out)
