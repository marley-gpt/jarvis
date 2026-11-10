"""Two-way brain sync (T119): the standby's own work flows back to the primary.

sync.py carries the primary's whole lean brain to the standby. This module carries the other half. The standby
writes an outbox of what it made or changed since its last pull, and the primary merges it on every routines
tick, so work done on the laptop (tasks, decisions, memory, deliverables, worker runs) reaches the PC.

    OneDrive/JARVIS-laptop-transfer/outbox-<machine>.zip   the standby's new and changed records and files
    OneDrive/JARVIS-laptop-transfer/status-<machine>.json  what each machine is running now (workers, open tasks)

What counts as "new or changed" is measured against a baseline the standby keeps outside brain/ (next to the
role file): a copy of the database it last pulled, the CRC of every pulled file and the time the pull finished.

Merging never clobbers. Records are matched by identity (a task by its created time and title, a journal line by
its session and digest, and so on), never by id, so a laptop T121 can't overwrite a PC T121. A new record keeps
its id when that id is free and gets the next one when it isn't (logged). A changed record or file is applied
when the primary's copy is still the one the standby started from; when both sides changed it, the newer
timestamp wins, the loser is kept under brain/work/sync/conflicts/ and the conflict is logged in
brain/work/sync/conflicts.jsonl. Journal days are merged line by line. Nothing is ever deleted by a merge.

Worker runs never travel as runs: each machine resumes only its own. A pulled brain's runs, and a merged
outbox's, are kept as a read-only mirror in brain/work/remote/<machine>/runs/.

    bin/jarvis sync outbox      write this standby's outbox now
    bin/jarvis sync merge       merge every other machine's outbox now (the primary's brain-merge routine)
    bin/jarvis sync machines    what the other machine is running
"""

from __future__ import annotations

import binascii
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import paths

OUTBOX = "outbox-{}.zip"
STATUS = "status-{}.json"
ID_GAP = 20  # the standby's new ids start this far past the primary's, so both rarely pick the same T<n>
STALE_HOURS = 6

# table: identity columns (ref columns resolve to the referenced row's identity), id column, references,
# the timestamp that settles a conflict, and whether rows change after they're written.
TABLES = {
    "products": {"ident": ("slug",), "key": "slug", "refs": {}, "stamp": "updated", "mutable": True},
    "tasks": {"ident": ("created", "title"), "key": "id", "refs": {"parent_id": "tasks"}, "stamp": "updated",
              "mutable": True},
    "task_events": {"ident": ("task_id", "ts", "kind", "note"), "key": "id", "refs": {"task_id": "tasks"}},
    "decisions": {"ident": ("ts", "decision"), "key": "id", "refs": {"task_id": "tasks"}},
    "approvals": {"ident": ("ts", "summary"), "key": "id", "refs": {"task_id": "tasks"}, "stamp": "decided_ts",
                  "mutable": True},
    "tests": {"ident": ("created", "product", "hypothesis"), "key": "id", "refs": {}, "mutable": True},
    "test_metrics": {"ident": ("test_id", "day", "recorded"), "key": "id", "refs": {"test_id": "tests"}},
    "costs": {"ident": ("ts", "service", "amount_aud"), "key": "id", "refs": {"task_id": "tasks"}},
    "money": {"ident": ("day", "kind", "category", "amount", "recorded"), "key": "id", "refs": {"test_id": "tests"}},
    "journal": {"ident": ("session_id", "digest"), "key": "id", "refs": {}},
}
ID_TABLES = ("tasks", "decisions", "approvals", "tests")  # the ids people say out loud: T, D, A, X

# Never sent back: machine-local, regenerated, or handled on their own (worker runs).
SKIP_FILES = {"config.json", "ESSENTIALS.md"}
SKIP_PREFIXES = ("work/routines/", "work/sync/", "work/runs/", "work/remote/")

MERGED_SCHEMA = """CREATE TABLE IF NOT EXISTS sync_merged (
    uid TEXT PRIMARY KEY, machine TEXT, kind TEXT, ref TEXT, outcome TEXT, ts TEXT)"""


def machine() -> str:
    from . import phone
    return phone.this_machine()


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "machine"


def _sync_dir() -> Path:
    from . import sync
    return sync.sync_dir()


def outbox_path(name: str | None = None) -> Path:
    return _sync_dir() / OUTBOX.format(_safe(name or machine()))


def sync_work() -> Path:
    d = paths.work_dir() / "sync"
    d.mkdir(parents=True, exist_ok=True)
    return d


def remote_runs_dir(name: str) -> Path:
    return paths.work_dir() / "remote" / _safe(name) / "runs"


# ---------------------------------------------------------------- baseline (kept outside brain/)

def _baseline_db() -> Path:
    return paths.role_file().with_name("sync-baseline.db")


def _baseline_json() -> Path:
    return paths.role_file().with_name("sync-baseline.json")


def save_baseline(db_file: Path, zf: zipfile.ZipFile, stamp: str | None, at: float) -> None:
    """Called by sync.pull once the pulled brain is in place, before this machine's own work is put back."""
    dest = _baseline_db()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(db_file, dest)
    crcs = {i.filename: i.CRC for i in zf.infolist() if i.filename != "jarvis.db" and not i.is_dir()}
    _baseline_json().write_text(json.dumps({"stamp": stamp, "at": at, "exact": True, "files": crcs}),
                                encoding="utf-8")


def _load_baseline(tmp: Path) -> tuple[sqlite3.Connection, dict] | None:
    """The baseline database and file map. A standby that pulled before T119 has none yet: then the brain the
    primary pushed stands in for it, trusting only changes made after the last pull (exact=False)."""
    try:
        meta = json.loads(_baseline_json().read_text(encoding="utf-8"))
        if _baseline_db().exists():
            return sqlite3.connect(f"file:{_baseline_db()}?mode=ro", uri=True), meta
    except (OSError, ValueError):
        pass
    from . import sync
    pulled_at = sync.local_state().get("pulled_at")
    src = _sync_dir() / sync.BRAIN_ZIP
    if not pulled_at or not src.exists():
        return None
    with zipfile.ZipFile(src) as zf:
        if "jarvis.db" not in zf.namelist():
            return None
        zf.extract("jarvis.db", tmp)
        crcs = {i.filename: i.CRC for i in zf.infolist() if i.filename != "jarvis.db" and not i.is_dir()}
    meta = {"stamp": None, "at": datetime.fromisoformat(pulled_at).timestamp(), "exact": False, "files": crcs}
    return sqlite3.connect(tmp / "jarvis.db"), meta


# ---------------------------------------------------------------- rows

def _cols(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _rows(conn, table: str) -> list[dict]:
    cur = conn.execute(f"SELECT * FROM {table}")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


class _Index:
    """Rows of one database by id, for turning a reference into the referenced row's identity."""

    def __init__(self, conn):
        self.conn = conn
        self.by_id: dict[str, dict] = {}

    def row(self, table: str, rid):
        if table not in self.by_id:
            key = TABLES[table]["key"]
            self.by_id[table] = {r[key]: r for r in _rows(self.conn, table)} if _cols(self.conn, table) else {}
        return self.by_id[table].get(rid)

    def ident(self, table: str, row: dict) -> list | None:
        spec = TABLES[table]
        out = []
        for c in spec["ident"]:
            if c in spec["refs"]:
                ref = self.row(spec["refs"][c], row.get(c)) if row.get(c) is not None else None
                out.append(self.ident(spec["refs"][c], ref) if ref else None)
            else:
                out.append(row.get(c))
        return out

    def portable(self, table: str, row: dict) -> dict:
        """The row without its id, references replaced by identities: comparable across machines."""
        spec = TABLES[table]
        out = {}
        for c, v in row.items():
            if c == "id":
                continue
            if c in spec["refs"]:
                ref = self.row(spec["refs"][c], v) if v is not None else None
                out[c] = self.ident(spec["refs"][c], ref) if ref else None
            else:
                out[c] = v
        return json.loads(json.dumps(out))


def _key(ident) -> str:
    return json.dumps(ident, sort_keys=True)


def _newer(a, b) -> bool:
    def t(x):
        try:
            return datetime.fromisoformat(x).timestamp()
        except (TypeError, ValueError):
            return float("-inf")
    return t(a) > t(b)


def _changed_rows(conn, base, meta: dict) -> list[dict]:
    here, there = _Index(conn), _Index(base)
    out = []
    for table, spec in TABLES.items():
        if not _cols(conn, table) or not _cols(base, table):
            continue
        base_rows = {_key(there.ident(table, r)): r for r in _rows(base, table)}
        for r in _rows(conn, table):
            ident = here.ident(table, r)
            old = base_rows.get(_key(ident))
            if old is None:
                kind = "new"
            elif spec.get("mutable") and here.portable(table, r) != there.portable(table, old):
                # Without an exact baseline only a change stamped after the last pull is this machine's own.
                if not meta.get("exact") and not (spec.get("stamp") and _newer(r.get(spec["stamp"]),
                                                                                datetime.fromtimestamp(meta["at"]).astimezone().isoformat())):
                    continue
                kind = "changed"
            else:
                continue
            out.append({"table": table, "kind": kind, "id": r.get("id"), "ident": ident,
                        "row": here.portable(table, r),
                        "base": there.portable(table, old) if old is not None else None})
    return out


# ---------------------------------------------------------------- files

def _crc(path: Path) -> int:
    crc = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            crc = binascii.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def _changed_files(meta: dict) -> list[dict]:
    from . import sync
    out = []
    for f, rel in sync.files():
        if rel in SKIP_FILES or rel.startswith(SKIP_PREFIXES):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        base = meta["files"].get(rel)
        if base is not None and st.st_mtime < meta["at"]:
            continue  # written by the pull itself, or older: not this machine's work
        # A file the primary never sent is this machine's own, however old (work from before T119 included).
        crc = _crc(f)
        if crc != base:
            out.append({"rel": rel, "crc": crc, "base": base, "mtime": st.st_mtime})
    return out


# ---------------------------------------------------------------- workers and status

def _task_titles(conn) -> dict:
    return {f"T{r[0]}": (r[1], r[2]) for r in conn.execute("SELECT id, title, status FROM tasks")}


def _workers(conn) -> list[dict]:
    from . import work
    try:
        runs = work.listing(200)
    except Exception:
        return []
    titles = _task_titles(conn)
    cutoff = time.time() - 2 * 86400
    out = []
    for r in runs:
        try:
            recent = datetime.fromisoformat(r.get("updated") or r.get("started")).timestamp() > cutoff
        except (TypeError, ValueError):
            recent = False
        if r.get("status") not in ("running", "stopped", "failed", "new") and not recent:
            continue
        task = str(r.get("task") or "").upper()
        out.append({"id": r["id"], "task": task, "task_title": (titles.get(task) or ("",))[0],
                    "agent": r.get("agent") or "", "status": r.get("status"), "started": r.get("started"),
                    "updated": r.get("updated"), "brief": (r.get("brief") or "")[:300], "session": r.get("session")})
    return out


def status(conn, pending: list[dict] | None = None) -> dict:
    """What this machine is running: its workers and the open tasks it's working on."""
    workers = _workers(conn)
    ids = {w["task"] for w in workers if w["status"] in ("running", "stopped", "new")}
    ids |= {f"T{p['id']}" for p in pending or [] if p["table"] == "tasks"}
    tasks = []
    for tid in sorted(ids, key=lambda t: int(t[1:]) if t[1:].isdigit() else 0):
        if not tid[1:].isdigit():
            continue
        row = conn.execute("SELECT id, title, status, agent, product FROM tasks WHERE id = ?", (int(tid[1:]),)).fetchone()
        if row and row[2] not in ("done", "cancelled"):
            tasks.append({"id": f"T{row[0]}", "title": row[1], "status": row[2], "agent": row[3], "product": row[4]})
    return {"machine": machine(), "role": paths.machine_role(), "stamp": paths.now(), "workers": workers,
            "tasks": tasks}


def write_status(conn, pending: list[dict] | None = None) -> dict:
    st = status(conn, pending)
    d = _sync_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / STATUS.format(_safe(st["machine"]))
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    try:
        os.replace(tmp, p)
    except OSError:
        tmp.unlink(missing_ok=True)  # OneDrive busy: the next tick writes it again
    return st


def other_machines() -> list[dict]:
    """The last status every other machine wrote, with how old it is."""
    me = _safe(machine()).lower()
    out = []
    for p in sorted(_sync_dir().glob(STATUS.format("*"))):
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(st, dict) or _safe(str(st.get("machine") or "")).lower() == me:
            continue
        try:
            st["age_hours"] = round((time.time() - datetime.fromisoformat(st["stamp"]).timestamp()) / 3600, 1)
        except (KeyError, TypeError, ValueError):
            st["age_hours"] = None
        out.append(st)
    return out


def remote_workers() -> list[dict]:
    """Every other machine's workers, as projects.overview wants them (id tagged with the machine)."""
    return [{**w, "id": f"{w['id']}@{st['machine']}", "machine": st["machine"]}
            for st in other_machines() for w in st.get("workers") or []]


def lines() -> list[str]:
    out = []
    for st in other_machines():
        age = st.get("age_hours")
        when = (st.get("stamp") or "?")[:16].replace("T", " ")
        head = f"{st['machine']} ({st.get('role', '?')}), as of {when}"
        if age is not None and age >= STALE_HOURS:
            head += f", not heard from for {age:.0f}h (probably off)"
        live = [w for w in st.get("workers") or [] if w.get("status") in ("running", "stopped", "new", "failed")]
        out.append(f"{head}: {len(live)} worker(s), {len(st.get('tasks') or [])} open task(s) there")
        out += [f"- W{w['id']}@{st['machine']} [{w['status']}] {w['task']} {w['agent']}: "
                f"{(w.get('task_title') or w.get('brief') or '')[:90]}" for w in live]
        out += [f"- {t['id']} [{t['status']}] {t['title'][:100]}" for t in st.get("tasks") or []]
    return out


# ---------------------------------------------------------------- the standby: outbox

def export(conn) -> dict:
    """Write this machine's outbox: rows, files and worker runs made or changed since its last pull."""
    with tempfile.TemporaryDirectory() as tmp:
        loaded = _load_baseline(Path(tmp))
        if not loaded:
            write_status(conn)
            return {"written": False, "note": "no pulled brain to compare with yet", "rows": 0, "files": 0}
        base, meta = loaded
        try:
            rows = _changed_rows(conn, base, meta)
        finally:
            base.close()
    found = _changed_files(meta)
    workers = _workers(conn)
    from . import work
    for w in workers:
        try:
            w["tail"] = work.tail(w["id"], 20)["lines"]
        except Exception:
            w["tail"] = []
    body = {"machine": machine(), "baseline": meta.get("stamp"), "rows": rows, "files": found, "workers": workers}
    digest = hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    write_status(conn, rows)
    dest = outbox_path()
    if not (rows or found or workers) and not dest.exists():
        return {"written": False, "note": "nothing of this machine's own to send", "rows": 0, "files": 0}
    try:
        with zipfile.ZipFile(dest) as zf:
            if json.loads(zf.read("outbox.json")).get("digest") == digest:
                return {"written": False, "note": "outbox unchanged", "rows": len(rows), "files": len(found)}
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        pass
    body.update(digest=digest, stamp=paths.now())
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("outbox.json", json.dumps(body, default=str))
        for f in found:
            try:
                zf.write(paths.home() / f["rel"], "files/" + f["rel"])
            except OSError:
                pass
    try:
        os.replace(tmp, dest)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return {"written": False, "note": f"couldn't replace the outbox ({exc}); the next tick tries again",
                "rows": len(rows), "files": len(found)}
    return {"written": True, "note": f"outbox: {len(rows)} record(s), {len(found)} file(s), {len(workers)} worker(s)",
            "rows": len(rows), "files": len(found)}


# ---------------------------------------------------------------- merging (the primary, and the standby after a pull)

class _Merge:
    def __init__(self, conn, src: str, local: bool):
        self.conn, self.src, self.local = conn, src, local
        self.idx = _Index(conn)
        self.stats = {"added": 0, "updated": 0, "same": 0, "remapped": 0, "conflicts": 0, "files": 0}
        conn.execute(MERGED_SCHEMA)

    def log(self, kind: str, entry: dict) -> None:
        if self.local:
            return
        with open(sync_work() / f"{kind}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": paths.now(), "from": self.src, **entry}, default=str) + "\n")

    def done(self, uid: str, kind: str, ref: str, outcome: str) -> None:
        if not self.local:
            self.conn.execute("INSERT OR REPLACE INTO sync_merged VALUES (?,?,?,?,?,?)",
                              (uid, self.src, kind, ref, outcome, paths.now()))

    def merged(self, uid: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM sync_merged WHERE uid = ?", (uid,)).fetchone())

    # -- rows
    def find(self, table: str, ident) -> int | str | None:
        spec = TABLES[table]
        where, params = [], []
        for c, v in zip(spec["ident"], ident):
            if c in spec["refs"]:
                v = self.find(spec["refs"][c], v) if v is not None else None
                if v is None and ident[spec["ident"].index(c)] is not None:
                    return None
            where.append(f"{c} IS ?")
            params.append(v)
        row = self.conn.execute(f"SELECT {spec['key']} FROM {table} WHERE {' AND '.join(where)} LIMIT 1",
                                params).fetchone()
        return row[0] if row else None

    def local_values(self, table: str, portable: dict) -> dict:
        spec, cols = TABLES[table], set(_cols(self.conn, table))
        out = {}
        for c, v in portable.items():
            if c not in cols:
                continue
            out[c] = self.find(spec["refs"][c], v) if c in spec["refs"] and v is not None else v
        return out

    def add(self, item: dict) -> None:
        table, spec = item["table"], TABLES[item["table"]]
        vals = self.local_values(table, item["row"])
        if table in ID_TABLES and item.get("id") is not None:  # other tables' ids mean nothing to anyone
            if self.conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (item["id"],)).fetchone():
                self.stats["remapped"] += 1
                self.log("merge", {"what": f"{table} {item['id']} took a new id (that id is used here)",
                                   "ident": item["ident"]})
            else:
                vals["id"] = item["id"]
        names = list(vals)
        try:
            cur = self.conn.execute(f"INSERT INTO {table} ({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                                    [vals[n] for n in names])
        except sqlite3.IntegrityError as exc:
            self.log("conflicts", {"what": f"{table} record not added: {exc}", "ident": item["ident"],
                                   "theirs": item["row"]})
            self.stats["conflicts"] += 1
            return
        if table == "journal":
            self.conn.execute("INSERT INTO journal_fts (rowid, text) VALUES (?, ?)", (cur.lastrowid, vals.get("text")))
        self.idx.by_id.pop(table, None)
        self.stats["added"] += 1

    def change(self, item: dict) -> None:
        table, spec = item["table"], TABLES[item["table"]]
        key = self.find(table, item["ident"])
        if key is None:
            return self.add(item)
        uid = "row:" + hashlib.sha1(_key([table, item["ident"], item["row"]]).encode()).hexdigest()
        if self.merged(uid):
            return
        row = self.conn.execute(f"SELECT * FROM {table} WHERE {spec['key']} = ?", (key,)).fetchone()
        mine = self.idx.portable(table, dict(zip(_cols(self.conn, table), row)))
        ref = f"{table}:{key}"
        if mine == item["row"]:
            self.stats["same"] += 1
            return self.done(uid, "row", ref, "same")
        outcome = "applied"
        if mine != item["base"]:
            stamp = spec.get("stamp")
            theirs_newer = bool(stamp) and _newer(item["row"].get(stamp), mine.get(stamp))
            outcome = "conflict: theirs newer, applied" if theirs_newer else "conflict: kept this machine's"
            self.stats["conflicts"] += 1
            self.log("conflicts", {"what": f"{ref} changed on both machines; {outcome}", "ident": item["ident"],
                                   "here": mine, "theirs": item["row"], "base": item["base"]})
            if not theirs_newer:
                return self.done(uid, "row", ref, outcome)
        vals = self.local_values(table, item["row"])
        vals.pop(spec["key"], None)
        self.conn.execute(f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in vals)} WHERE {spec['key']} = ?",
                          [*vals.values(), key])
        self.idx.by_id.pop(table, None)
        self.stats["updated"] += 1
        self.done(uid, "row", ref, outcome)

    # -- files
    def file(self, f: dict, data: bytes) -> None:
        rel = f["rel"]
        if ".." in Path(rel).parts or Path(rel).is_absolute():
            return
        uid = f"file:{rel}:{f['crc']}"
        if self.merged(uid):
            return
        dest = paths.home() / rel
        outcome = "added"
        if dest.exists():
            cur = _crc(dest)
            if cur == f["crc"]:
                return self.done(uid, "file", rel, "same")
            if cur != f["base"]:
                if rel.startswith("journal/") and rel.endswith(".md"):
                    data = _union_journal(dest.read_text(encoding="utf-8", errors="replace"),
                                          data.decode("utf-8", errors="replace")).encode("utf-8")
                    outcome = "journal lines merged"
                else:
                    theirs_newer = f["mtime"] > dest.stat().st_mtime
                    loser = dest.read_bytes() if theirs_newer else data
                    outcome = "conflict: theirs newer, applied" if theirs_newer else "conflict: kept this machine's"
                    self.stats["conflicts"] += 1
                    if not self.local:
                        keep = sync_work() / "conflicts" / _safe(self.src if not theirs_newer else machine()) / (
                            rel + "." + datetime.now().strftime("%Y%m%d-%H%M%S"))
                        keep.parent.mkdir(parents=True, exist_ok=True)
                        keep.write_bytes(loser)
                        self.log("conflicts", {"what": f"{rel} changed on both machines; {outcome}",
                                               "loser_kept": paths.rel(keep)})
                    if not theirs_newer:
                        return self.done(uid, "file", rel, outcome)
            else:
                outcome = "updated"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        self.stats["files"] += 1
        self.done(uid, "file", rel, outcome)


def _union_journal(here: str, theirs: str) -> str:
    """Both machines append to the same day's journal: keep every entry from both, in time order."""
    def split(text):
        head, blocks, cur = [], [], None
        for line in text.splitlines(keepends=True):
            if line.startswith("### "):
                cur = [line]
                blocks.append(cur)
            elif cur is None:
                head.append(line)
            else:
                cur.append(line)
        return "".join(head), ["".join(b) for b in blocks]
    head, mine = split(here.replace("\r\n", "\n"))
    _, other = split(theirs.replace("\r\n", "\n"))
    seen = set(b.strip() for b in mine)
    blocks = mine + [b for b in other if b.strip() not in seen]
    blocks.sort(key=lambda b: b[4:9])  # "### HH:MM": stable, so same-minute entries keep their order
    return (head.rstrip("\n") + "\n\n" + "\n\n".join(b.strip("\n") for b in blocks) + "\n").lstrip("\n")


def _apply(conn, box: dict, zf: zipfile.ZipFile, local: bool) -> dict:
    m = _Merge(conn, box.get("machine") or "?", local)
    order = list(TABLES)
    for item in sorted(box.get("rows") or [], key=lambda i: (order.index(i["table"]), i.get("id") or 0)):
        if item["table"] not in TABLES or not _cols(conn, item["table"]):
            continue
        if item["kind"] == "new":
            if m.find(item["table"], item["ident"]) is None:
                m.add(item)
        else:
            m.change(item)
    conn.commit()
    for f in box.get("files") or []:
        try:
            data = zf.read("files/" + f["rel"])
        except KeyError:
            continue
        m.file(f, data)
    conn.commit()
    if not local:
        mirror = remote_runs_dir(m.src)
        mirror.mkdir(parents=True, exist_ok=True)
        for w in box.get("workers") or []:
            (mirror / f"W{w['id']}.json").write_text(json.dumps({**w, "machine": m.src}, indent=1), encoding="utf-8")
    if m.stats["files"]:
        from . import memory
        memory.sync(conn)
    return m.stats


def merge_all(conn) -> dict:
    """On the primary: merge every other machine's outbox that hasn't been merged yet."""
    from . import db
    total = {"outboxes": 0, "added": 0, "updated": 0, "same": 0, "remapped": 0, "conflicts": 0, "files": 0}
    me = _safe(machine()).lower()
    for p in sorted(_sync_dir().glob(OUTBOX.format("*"))):
        if p.name.endswith(".tmp"):
            continue
        try:
            with zipfile.ZipFile(p) as zf:
                box = json.loads(zf.read("outbox.json"))
                src = _safe(str(box.get("machine") or ""))
                if src.lower() == me or db.get_meta(conn, f"outbox:{src}") == box.get("stamp"):
                    continue
                stats = _apply(conn, box, zf, local=False)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue  # OneDrive still downloading it: the next tick tries again
        db.set_meta(conn, f"outbox:{src}", box.get("stamp") or "")
        total["outboxes"] += 1
        for k, v in stats.items():
            total[k] += v
        with open(sync_work() / "merge.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": paths.now(), "from": src, "outbox": box.get("stamp"), **stats}) + "\n")
    return total


def merge_note(t: dict) -> str:
    if not t["outboxes"]:
        return "no new outbox from the other machine"
    note = (f"merged {t['outboxes']} outbox(es): {t['added']} record(s) added, {t['updated']} updated, "
            f"{t['files']} file(s)")
    if t["remapped"]:
        note += f", {t['remapped']} given new ids"
    if t["conflicts"]:
        note += f", {t['conflicts']} conflict(s) logged in brain/work/sync/conflicts.jsonl"
    return note


def reapply(conn) -> dict | None:
    """On the standby, right after a pull: put back this machine's own work the primary hasn't merged yet,
    from the outbox written just before the pull, then keep its next ids clear of the primary's."""
    tables = [t for t in ID_TABLES if _cols(conn, t)]
    pulled = {t: conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t}").fetchone()[0] for t in tables}
    stats = None
    p = outbox_path()
    if p.exists():
        try:
            with zipfile.ZipFile(p) as zf:
                stats = _apply(conn, json.loads(zf.read("outbox.json")), zf, local=True)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            stats = None
    gap = int(paths.config().get("sync_id_gap") or ID_GAP)
    for table in tables:
        top = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
        seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone()
        want = max(top, seq[0] if seq else 0, pulled[table] + gap)
        if seq:
            conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (want, table))
        else:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, want))
    conn.commit()
    return stats


def conflicts(limit: int = 20) -> list[dict]:
    p = sync_work() / "conflicts.jsonl"
    try:
        rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    return rows[-limit:]


def status_bit() -> str:
    """One clause for the sync status line: the last merge here, or this standby's outbox."""
    if paths.standby():
        try:
            with zipfile.ZipFile(outbox_path()) as zf:
                box = json.loads(zf.read("outbox.json"))
            return (f"outbox {box.get('stamp', '?')[:16]}: {len(box.get('rows') or [])} record(s), "
                    f"{len(box.get('files') or [])} file(s) waiting for the primary")
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            return "no outbox written yet"
    try:
        last = json.loads((sync_work() / "merge.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return "nothing merged from the other machine yet"
    bit = f"last merge {last['ts'][:16]} from {last['from']}"
    n = len(conflicts(1000))
    return bit + (f", {n} conflict(s) logged (`bin/jarvis sync conflicts`)" if n else "")
