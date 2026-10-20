"""The feedback loop: every verdict on JARVIS's work becomes a lesson the team reuses.

Whenever something the team made gets feedback (the owner's review of an ad or a product
page, test results, a QC check against Blake's rules), `bin/jarvis feedback add` records:
what was made, the verdict, the evidence, why it worked or didn't, what to keep and what
to change. Each record is a memory entry (tag `feedback`, plus `for:<agent>`), filed in the
category of the work, and a line in brain/feedback/ledger.md. Agents run
`bin/jarvis feedback list` (or `mem search ... --tags feedback`) before similar work, and
`feedback digest` shows the patterns: what keeps working and what keeps failing.
"""

from __future__ import annotations

from collections import Counter

from . import memory, paths

KINDS = {  # kind of work -> memory category
    "ad": "creative-inspiration", "creative": "creative-inspiration", "copy": "creative-inspiration",
    "pdp": "shopify", "store": "shopify",
    "test": "testing", "campaign": "meta-ads",
    "research": "customer-research", "product": "product-research",
    "process": "procedures", "other": "procedures",
}
VERDICTS = ("good", "bad", "mixed")
SOURCES = ("owner", "data", "qc", "blake")


class FeedbackError(Exception):
    pass


def ledger_path():
    return paths.home() / "feedback" / "ledger.md"


def add(conn, *, about: str, kind: str, verdict: str, source: str, what: str, why: str,
        keep: str = "", change: str = "", evidence: str = "", product=None, agent=None, task=None):
    if kind not in KINDS:
        raise FeedbackError(f"Unknown kind '{kind}'. Use one of: {', '.join(KINDS)}")
    if verdict not in VERDICTS:
        raise FeedbackError(f"Verdict must be one of: {', '.join(VERDICTS)}")
    if source not in SOURCES:
        raise FeedbackError(f"Source must be one of: {', '.join(SOURCES)}")
    mark = {"good": "WORKED", "bad": "DIDN'T WORK", "mixed": "MIXED"}[verdict]
    title = f"Feedback ({mark}): {about}"
    lines = [f"**Verdict:** {mark}, from {source}" + (f". Evidence: {evidence}" if evidence else ""),
             f"**What was made:** {what}", f"**Why (best understanding):** {why}"]
    if keep:
        lines.append(f"**Keep doing:** {keep}")
    if change:
        lines.append(f"**Change next time:** {change}")
    if task:
        lines.append(f"**Task:** {task}")
    tags = ["feedback", f"verdict:{verdict}", f"kind:{kind}"] + ([f"for:{agent}"] if agent else [])
    provenance = {"owner": "user", "data": "evidence", "qc": "agent", "blake": "mentor"}[source]
    entry, _ = memory.remember(conn, title=title, body="\n\n".join(lines) + "\n", category=KINDS[kind],
                               provenance=provenance, product=product, tags=tags,
                               confidence="confirmed" if source in ("owner", "data") else "likely",
                               source=f"feedback from {source}" if source != "blake" else "Blake (feedback check)",
                               source_ref=task, force=True)
    led = ledger_path()
    led.parent.mkdir(parents=True, exist_ok=True)
    if not led.exists():
        led.write_text("# Feedback ledger\n\nOne line per verdict, newest last. Full records are in memory (tag `feedback`).\n\n",
                       encoding="utf-8")
    with led.open("a", encoding="utf-8") as f:
        f.write(f"- {paths.now()[:16]} [{mark}] {kind}{' / ' + product if product else ''}"
                f"{' / ' + agent if agent else ''}: {about}. Keep: {keep or '-'}. Change: {change or '-'} ({entry.id})\n")
    return entry


def rows(conn, *, agent=None, verdict=None, kind=None, product=None, limit=50):
    sql = "SELECT * FROM memory WHERE status = 'active' AND tags LIKE '%feedback%'"
    params: list = []
    for col, val in (("verdict:", verdict), ("kind:", kind), ("for:", agent)):
        if val:
            sql += " AND tags LIKE ?"
            params.append(f"%{col}{val}%")
    if product:
        sql += " AND product = ?"
        params.append(product)
    sql += " ORDER BY created DESC LIMIT ?"
    params.append(limit)
    memory.sync(conn)
    return conn.execute(sql, params).fetchall()


def digest(conn, agent=None) -> str:
    """Patterns across all feedback: counts by verdict and kind, then the latest keep and change notes."""
    found = rows(conn, agent=agent, limit=500)
    if not found:
        return "No feedback recorded yet."
    by = Counter()
    keeps, changes = [], []
    for r in found:
        tags = r["tags"] or ""
        v = next((t.split(":", 1)[1] for t in tags.replace("[", "").replace("]", "").split(",") if t.strip().startswith("verdict:")), "?")
        k = next((t.split(":", 1)[1] for t in tags.replace("[", "").replace("]", "").split(",") if t.strip().startswith("kind:")), "?")
        by[(k.strip(), v.strip())] += 1
        body = memory.get(conn, r["id"]).body
        for line in body.splitlines():
            if line.startswith("**Keep doing:**"):
                keeps.append(line.split("**", 2)[2].strip(" :"))
            elif line.startswith("**Change next time:**"):
                changes.append(line.split("**", 2)[2].strip(" :"))
    out = [f"Feedback records: {len(found)}" + (f" (for {agent})" if agent else "")]
    out += [f"  {k:10s} {v:6s} {n}" for (k, v), n in sorted(by.items())]
    if keeps:
        out += ["Keep doing (latest first):"] + [f"  - {k}" for k in keeps[:10]]
    if changes:
        out += ["Change next time (latest first):"] + [f"  - {c}" for c in changes[:10]]
    return "\n".join(out)
