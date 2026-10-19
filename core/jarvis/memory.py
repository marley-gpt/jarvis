"""Long-term knowledge: one Markdown file per entry, indexed into SQLite FTS5.

The Markdown files are the source of truth, so the user can read and edit them
directly. The index is rebuilt incrementally from file mtimes on every access.
"""

from __future__ import annotations

import difflib
import re
import secrets
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import paths

LIST_FIELDS = {"tags"}
FIELD_ORDER = (
    "id", "title", "category", "product", "tags", "provenance", "status", "confidence",
    "source", "source_ref", "supersedes", "superseded_by", "created", "updated",
)
STATUSES = ("active", "needs-review", "superseded", "archived")
CONFIDENCES = ("confirmed", "likely", "speculative")


class MemoryError(Exception):
    pass


@dataclass
class Entry:
    meta: dict = field(default_factory=dict)
    body: str = ""
    path: Path | None = None

    def __getattr__(self, name):
        if name in FIELD_ORDER:
            return self.meta.get(name)
        raise AttributeError(name)


# ---------------------------------------------------------------- file format

def parse(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    meta: dict = {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in LIST_FIELDS:
            value = [v.strip() for v in value.strip("[]").split(",") if v.strip()]
        meta[key] = value
    if end is None:
        return {}, text
    return meta, "\n".join(lines[end + 1:]).strip() + "\n"


def dump(meta: dict, body: str) -> str:
    out = ["---"]
    keys = [k for k in FIELD_ORDER if k in meta] + [k for k in meta if k not in FIELD_ORDER]
    for key in keys:
        value = meta[key]
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            value = "[" + ", ".join(value) + "]"
        out.append(f"{key}: {value}")
    out.append("---")
    return "\n".join(out) + "\n\n" + body.strip() + "\n"


def read_entry(path: Path) -> Entry:
    meta, body = parse(path.read_text(encoding="utf-8"))
    return Entry(meta, body, path)


def write_entry(entry: Entry) -> None:
    entry.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = entry.path.with_suffix(".md.tmp")
    tmp.write_text(dump(entry.meta, entry.body), encoding="utf-8")
    tmp.replace(entry.path)


def slugify(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "entry"


def new_id() -> str:
    return f"M-{datetime.now():%y%m%d}-{secrets.token_hex(2)}"


# ---------------------------------------------------------------- index

def iter_files():
    root = paths.memory_dir()
    if not root.exists():
        return
    for path in sorted(root.rglob("*.md")):
        parts = path.relative_to(root).parts
        if any(p.startswith(("_", ".")) for p in parts) or path.name == "README.md":
            continue
        yield path


def sync(conn: sqlite3.Connection) -> dict:
    """Bring the index in line with the Markdown files. Returns counts of changes."""
    seen: set[str] = set()
    known = {r["path"]: r for r in conn.execute("SELECT path, id, mtime, size FROM memory")}
    stats = {"added": 0, "updated": 0, "removed": 0, "fixed": 0}
    for path in iter_files():
        key = paths.rel(path)
        st = path.stat()
        row = known.get(key)
        seen.add(key)
        if row and row["mtime"] == st.st_mtime and row["size"] == st.st_size:
            continue
        entry = read_entry(path)
        if _normalise(entry, path):
            write_entry(entry)
            st = path.stat()
            stats["fixed"] += 1
        _index(conn, entry, key, st)
        stats["updated" if row else "added"] += 1
    for key, row in known.items():
        if key not in seen:
            conn.execute("DELETE FROM memory WHERE path = ?", (key,))
            conn.execute("DELETE FROM memory_fts WHERE id = ?", (row["id"],))
            stats["removed"] += 1
    conn.commit()
    return stats


def _normalise(entry: Entry, path: Path) -> bool:
    """Fill in missing front matter for hand-written files. Returns True if changed."""
    m = entry.meta
    before = dict(m)
    m.setdefault("id", new_id())
    if not m.get("title"):
        heading = re.search(r"^#\s+(.+)$", entry.body, re.M)
        m["title"] = heading.group(1).strip() if heading else path.stem.replace("-", " ")
    rel_parts = path.relative_to(paths.memory_dir()).parts
    if not m.get("category") and len(rel_parts) > 1 and rel_parts[0] in paths.CATEGORIES:
        m["category"] = rel_parts[0]
    m.setdefault("category", "business")
    m.setdefault("provenance", "user")
    m.setdefault("status", "active")
    stamp = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    m.setdefault("created", stamp)
    m.setdefault("updated", stamp)
    return m != before


def _index(conn, entry: Entry, key: str, st) -> None:
    m = entry.meta
    clash = conn.execute("SELECT path FROM memory WHERE id = ? AND path != ?", (m["id"], key)).fetchone()
    if clash:
        # A copied file kept its id. Give the copy a fresh one rather than silently overwriting.
        m["id"] = new_id()
        write_entry(entry)
        st = entry.path.stat()
    tags = m.get("tags") or []
    conn.execute("DELETE FROM memory WHERE path = ? OR id = ?", (key, m["id"]))
    conn.execute("DELETE FROM memory_fts WHERE id = ?", (m["id"],))
    conn.execute(
        """INSERT INTO memory (id, path, title, category, product, tags, provenance, status, confidence,
           source, source_ref, supersedes, superseded_by, created, updated, mtime, size)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (m["id"], key, m["title"], m["category"], m.get("product"), ",".join(tags), m["provenance"],
         m["status"], m.get("confidence"), m.get("source"), m.get("source_ref"), m.get("supersedes"),
         m.get("superseded_by"), m.get("created"), m.get("updated"), st.st_mtime, st.st_size),
    )
    conn.execute(
        "INSERT INTO memory_fts (id, title, tags, body) VALUES (?,?,?,?)",
        (m["id"], m["title"], " ".join(tags), entry.body),
    )


# ---------------------------------------------------------------- queries

def _fts_query(text: str, mode: str) -> str:
    words = re.findall(r"[\w$%-]+", text.lower())
    words = [w.strip("-") for w in words if len(w.strip("-")) > 1]
    if not words:
        return ""
    return f" {mode} ".join(f'"{w}"' for w in words)


def search(conn, text: str, *, category=None, product=None, provenance=None,
           include_inactive=False, limit=5) -> list[sqlite3.Row]:
    sync(conn)
    filters, params = [], []
    if category:
        filters.append("m.category = ?")
        params.append(category)
    if product:
        filters.append("m.product = ?")
        params.append(product)
    if provenance:
        filters.append("m.provenance = ?")
        params.append(provenance)
    if not include_inactive:
        filters.append("m.status IN ('active', 'needs-review')")
    where = (" AND " + " AND ".join(filters)) if filters else ""
    for mode in ("AND", "OR"):
        q = _fts_query(text, mode)
        if not q:
            break
        rows = conn.execute(
            f"""SELECT m.*, snippet(memory_fts, 3, '[', ']', ' … ', 18) AS snippet,
                       bm25(memory_fts, 0.0, 8.0, 4.0, 1.0) AS rank
                FROM memory_fts JOIN memory m ON m.id = memory_fts.id
                WHERE memory_fts MATCH ?{where}
                ORDER BY rank LIMIT ?""",
            [q, *params, limit],
        ).fetchall()
        if rows:
            return rows
    return []


def listing(conn, *, category=None, product=None, status=None, limit=50) -> list[sqlite3.Row]:
    sync(conn)
    filters, params = [], []
    for col, val in (("category", category), ("product", product), ("status", status)):
        if val:
            filters.append(f"{col} = ?")
            params.append(val)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    return conn.execute(
        f"SELECT * FROM memory {where} ORDER BY updated DESC LIMIT ?", [*params, limit]
    ).fetchall()


def get(conn, entry_id: str) -> Entry:
    sync(conn)
    row = conn.execute("SELECT path FROM memory WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        raise MemoryError(f"No memory entry {entry_id}")
    return read_entry(paths.REPO_ROOT / row["path"])


def similar(conn, title: str, body: str, *, category=None, product=None, limit=5) -> list[tuple[float, sqlite3.Row]]:
    """Existing active entries that look like the same knowledge, best first."""
    rows = search(conn, f"{title} {body[:300]}", category=category, product=product, limit=12)
    target = _norm(f"{title}\n{body}")
    scored = []
    for row in rows:
        other = get(conn, row["id"])
        ratio = difflib.SequenceMatcher(None, target, _norm(f"{other.title}\n{other.body}")).ratio()
        if _norm(row["title"]) == _norm(title):
            ratio = max(ratio, 0.9)
        scored.append((ratio, row))
    scored.sort(key=lambda s: s[0], reverse=True)
    return scored[:limit]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (text or "").lower())).strip()


# ---------------------------------------------------------------- writes

def remember(conn, *, title: str, body: str, category: str, provenance: str, product=None, tags=None,
             confidence=None, source=None, source_ref=None, supersedes=None, status="active",
             force=False) -> tuple[Entry, list]:
    """Create an entry. Refuses near-duplicates unless `force` or `supersedes` is given.

    Returns the new entry and the list of related (not duplicate) entries for the caller to review.
    """
    if category not in paths.CATEGORIES:
        raise MemoryError(f"Unknown category '{category}'. Use one of: {', '.join(paths.CATEGORIES)}")
    if provenance not in paths.PROVENANCES:
        raise MemoryError(f"Unknown provenance '{provenance}'. Use one of: {', '.join(paths.PROVENANCES)}")
    if provenance == "mentor" and not source:
        raise MemoryError("Mentor knowledge must cite its source lesson (--source, plus --ref timestamp).")
    if confidence and confidence not in CONFIDENCES:
        raise MemoryError(f"Unknown confidence '{confidence}'. Use one of: {', '.join(CONFIDENCES)}")
    if status not in STATUSES:
        raise MemoryError(f"Unknown status '{status}'.")

    old = get(conn, supersedes) if supersedes else None
    related = similar(conn, title, body, category=category, product=product)
    threshold = paths.config()["duplicate_threshold"]
    dupes = [(r, row) for r, row in related if r >= threshold and row["id"] != supersedes]
    if dupes and not force:
        lines = "\n".join(f"  {row['id']}  {r:.2f}  {row['title']}" for r, row in dupes)
        raise MemoryError(
            "Looks like existing knowledge:\n" + lines +
            "\nUpdate it with `supersede`, or pass --force if it is genuinely different."
        )

    stamp = paths.now()
    meta = {
        "id": new_id(), "title": title.strip(), "category": category, "product": product,
        "tags": tags or [], "provenance": provenance, "status": status, "confidence": confidence,
        "source": source, "source_ref": source_ref, "supersedes": supersedes,
        "created": stamp, "updated": stamp,
    }
    folder = paths.memory_dir() / category
    if product:
        folder = folder / slugify(product)
    path = folder / f"{slugify(title)}.md"
    n = 2
    while path.exists():
        path = folder / f"{slugify(title)}-{n}.md"
        n += 1
    entry = Entry(meta, body, path)
    write_entry(entry)
    if old:
        old.meta["status"] = "superseded"
        old.meta["superseded_by"] = meta["id"]
        old.meta["updated"] = stamp
        write_entry(old)
    sync(conn)
    return entry, [(r, row) for r, row in related if r < threshold]


def set_status(conn, entry_id: str, status: str, note: str | None = None) -> Entry:
    if status not in STATUSES:
        raise MemoryError(f"Unknown status '{status}'.")
    entry = get(conn, entry_id)
    entry.meta["status"] = status
    entry.meta["updated"] = paths.now()
    if note:
        entry.body = entry.body.rstrip() + f"\n\n> {paths.now()[:10]} status → {status}: {note}\n"
    write_entry(entry)
    if status == "archived":
        # Archiving moves the file out of the active tree; nothing is ever hard-deleted.
        dest = paths.memory_dir() / "_archive" / entry.path.relative_to(paths.memory_dir())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(entry.path), dest)
        entry.path = dest
    sync(conn)
    return entry


def stats(conn) -> dict:
    sync(conn)
    by_cat = {r[0]: r[1] for r in conn.execute(
        "SELECT category, COUNT(*) FROM memory WHERE status IN ('active','needs-review') GROUP BY category")}
    review = conn.execute("SELECT COUNT(*) FROM memory WHERE status = 'needs-review'").fetchone()[0]
    return {"by_category": by_cat, "needs_review": review, "total": sum(by_cat.values())}
