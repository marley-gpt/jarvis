"""`jarvis` command line: the single interface agents and the user share for memory and records."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import feedback, backup, brief, db, journal, media, memory, paths, records, telegram

SEED_DIR = paths.CORE_DIR / "seed"


def out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def read_body(args) -> str:
    if getattr(args, "body_file", None):
        return Path(args.body_file).read_text(encoding="utf-8")
    if args.body == "-":
        return sys.stdin.read()
    return args.body or ""


def tags(value) -> list[str]:
    return [t.strip() for t in (value or "").split(",") if t.strip()]


# ---------------------------------------------------------------- commands

def cmd_init(conn, args):
    for folder in paths.CATEGORIES:
        (paths.memory_dir() / folder).mkdir(parents=True, exist_ok=True)
    for folder in (paths.work_dir(), paths.files_dir(), paths.backups_dir(), paths.scratch_dir(),
                   paths.journal_dir()):
        folder.mkdir(parents=True, exist_ok=True)
    memory.sync(conn)
    added = 0
    for seed in sorted(SEED_DIR.rglob("*.md")):
        entry = memory.read_entry(seed)
        if conn.execute("SELECT 1 FROM memory WHERE id = ?", (entry.id,)).fetchone():
            continue
        dest = paths.memory_dir() / seed.relative_to(SEED_DIR)
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed, dest)
        added += 1
    memory.sync(conn)
    out(f"Brain ready at {paths.home()} ({added} seed entries added).")


def cmd_brief(conn, args):
    out(brief.session_brief(conn))


def fmt_hit(row) -> str:
    conf = f" · {row['confidence']}" if row["confidence"] else ""
    status = "" if row["status"] == "active" else f" · {row['status'].upper()}"
    src = ""
    if row["source"]:
        src = f"\n    source: {row['source']}" + (f" @ {row['source_ref']}" if row["source_ref"] else "")
    prod = f" <{row['product']}>" if row["product"] else ""
    return f"{row['id']}  [{row['category']} · {row['provenance']}{conf}{status}]{prod} {row['title']}{src}"


def cmd_mem_search(conn, args):
    rows = memory.search(conn, args.query, category=args.category, product=args.product,
                         provenance=args.provenance, include_inactive=args.all, limit=args.limit)
    if not rows:
        out("No matching memory. (Nothing documented: say so, and label any advice as your own.)")
        return
    for row in rows:
        out(fmt_hit(row))
        out(f"    {' '.join(row['snippet'].split())}")


def cmd_mem_get(conn, args):
    for entry_id in args.ids:
        entry = memory.get(conn, entry_id)
        out(f"# {entry.title}\n({paths.rel(entry.path)})")
        out(memory.dump(entry.meta, entry.body))


def _remember(conn, args, supersedes=None, defaults=None):
    defaults = defaults or {}
    entry, related = memory.remember(
        conn, title=args.title, body=read_body(args),
        category=args.category or defaults.get("category"),
        provenance=args.provenance or defaults.get("provenance") or "agent",
        product=args.product or defaults.get("product"), tags=tags(args.tags) or defaults.get("tags"),
        confidence=args.confidence, source=args.source or defaults.get("source"),
        source_ref=args.ref or defaults.get("source_ref"), supersedes=supersedes,
        status=args.status, force=args.force,
    )
    out(f"Saved {entry.id} → {paths.rel(entry.path)}" + (f" (supersedes {supersedes})" if supersedes else ""))
    if related:
        out("Related entries (check they don't contradict this):")
        for ratio, row in related[:3]:
            out(f"  {row['id']}  {ratio:.2f}  {row['title']}")


def cmd_mem_add(conn, args):
    if not args.category:
        raise memory.MemoryError("--category is required")
    _remember(conn, args)


def cmd_mem_supersede(conn, args):
    old = memory.get(conn, args.old_id)
    _remember(conn, args, supersedes=old.id, defaults=old.meta)


def cmd_mem_status(conn, args):
    entry = memory.set_status(conn, args.id, args.status, args.note)
    out(f"{entry.id} → {args.status} ({paths.rel(entry.path)})")


def cmd_mem_list(conn, args):
    for row in memory.listing(conn, category=args.category, product=args.product, status=args.status,
                              limit=args.limit):
        out(fmt_hit(row))


def cmd_mem_stats(conn, args):
    st = memory.stats(conn)
    for key, label in paths.CATEGORIES.items():
        out(f"{st['by_category'].get(key, 0):4d}  {key:22s} {label}")
    out(f"{st['total']:4d}  total active ({st['needs_review']} need review)")


def cmd_mem_reindex(conn, args):
    if args.full:
        conn.execute("DELETE FROM memory")
        conn.execute("DELETE FROM memory_fts")
        conn.commit()
    out(json.dumps(memory.sync(conn)))


def cmd_product_add(conn, args):
    slug = records.product_add(conn, args.name, args.stage, args.notes)
    out(f"Product {slug}")


def cmd_product_list(conn, args):
    for p in conn.execute("SELECT * FROM products ORDER BY updated DESC"):
        out(f"{p['slug']:28s} {p['stage']:12s} {p['name']}")


def cmd_product_show(conn, args):
    out(brief.product_dossier(conn, args.slug))


def cmd_product_stage(conn, args):
    records.product_update(conn, args.slug, stage=args.stage, notes=args.notes)
    out(f"{args.slug} → {args.stage}")


def cmd_task_add(conn, args):
    tid = records.task_add(conn, args.title, agent=args.agent, product=args.product, parent=args.parent,
                           description=args.desc, priority=args.priority)
    out(f"T{tid}")


def cmd_task_list(conn, args):
    rows = records.task_list(conn, status=args.status, agent=args.agent, product=args.product,
                             open_only=not args.all and not args.status, limit=args.limit)
    if not rows:
        out("No tasks.")
    for t in rows:
        out(brief.task_line(t)[2:])


def cmd_task_show(conn, args):
    task, events, children = records.task_get(conn, args.id)
    out(brief.task_line(task)[2:])
    for key in ("description", "summary", "deliverable"):
        if task[key]:
            out(f"{key}: {task[key]}")
    for c in children:
        out("  sub: " + brief.task_line(c)[2:])
    for e in events:
        out(f"  {e['ts'][:16]} {e['kind']}" + (f" — {e['note']}" if e["note"] else ""))


def cmd_task_status(status):
    def run(conn, args):
        records.task_update(conn, args.id, status=status, note=getattr(args, "note", None))
        out(f"{args.id.upper()} → {status}")
    return run


def cmd_task_note(conn, args):
    records.task_update(conn, args.id, note=args.note)
    out("Noted.")


def cmd_task_done(conn, args):
    deliverable = None
    if args.deliverable:
        path = Path(args.deliverable)
        if not path.is_absolute():
            path = paths.REPO_ROOT / path
        if not path.exists():
            raise records.RecordError(f"Deliverable not found: {args.deliverable}. Save it first.")
        deliverable = paths.rel(path)
    elif not args.no_deliverable:
        raise records.RecordError("Save the work and pass --deliverable PATH (see `jarvis task path`), "
                                  "or --no-deliverable if the task genuinely produced no file.")
    records.task_update(conn, args.id, status="done", summary=args.summary, deliverable=deliverable,
                        note=args.summary[:200])
    out(f"{args.id.upper()} done" + (f" → {deliverable}" if deliverable else ""))


def cmd_task_path(conn, args):
    out(paths.rel(records.task_workpath(conn, args.id)))


def cmd_decide(conn, args):
    did = records.decision_add(conn, args.decision, rationale=args.why, product=args.product, task=args.task,
                               decided_by=args.by)
    out(f"D{did}")


def cmd_approval_request(conn, args):
    aid = records.approval_request(conn, args.kind, args.summary, detail=args.detail, task=args.task,
                                   amount=args.amount, currency=args.currency)
    out(f"A{aid} pending — ask the user to approve or reject it.")


def cmd_approval_list(conn, args):
    sql = "SELECT * FROM approvals" + ("" if args.all else " WHERE status = 'pending'") + " ORDER BY id DESC"
    rows = conn.execute(sql).fetchall()
    if not rows:
        out("No approvals." if args.all else "Nothing waiting for approval.")
    for a in rows:
        amount = f" {a['amount']} {a['currency'] or ''}".rstrip() if a["amount"] is not None else ""
        task = f" (T{a['task_id']})" if a["task_id"] else ""
        out(f"A{a['id']} [{a['status']}] {a['kind']}{amount}{task}: {a['summary']}")


def cmd_approval_decide(approved):
    def run(conn, args):
        row = records.approval_decide(conn, args.id, approved, args.note)
        out(f"A{row['id']} {row['status']}")
    return run


def cmd_test_add(conn, args):
    tid = records.test_add(conn, args.product, args.hypothesis, angle=args.angle, budget_usd=args.budget,
                           days=args.days, criteria=args.criteria)
    out(f"X{tid}")


def cmd_test_metric(conn, args):
    records.test_metric(conn, args.id, args.day, spend=args.spend, impressions=args.impressions,
                        clicks=args.clicks, add_to_cart=args.atc, checkouts=args.checkouts,
                        purchases=args.purchases, revenue=args.revenue, source=args.source)
    out("Recorded.")


def cmd_test_show(conn, args):
    tid = int(args.id.upper().lstrip("X"))
    t = records._one(conn, "SELECT * FROM tests WHERE id = ?", (tid,))
    ev = records.evaluate(conn, tid)
    out(f"X{t['id']} [{t['status']}] {t['product']} — {t['hypothesis']}")
    out(f"budget ${t['budget_usd']} over {t['planned_days']} days; criteria: {t['criteria'] or '(none recorded)'}")
    pct = lambda v: f"{v * 100:.2f}%" if v is not None else "-"  # noqa: E731
    money = lambda v: f"${v:.2f}" if v is not None else "-"  # noqa: E731
    out(f"spend {money(ev['spend'])} | impr {ev['impressions']} | CPM {money(ev['cpm'])} | CTR {pct(ev['ctr'])} | "
        f"CPC {money(ev['cpc'])} | ATC {ev['atc']} | checkouts {ev['checkouts']} | purchases {ev['purchases']} | "
        f"CPA {money(ev['cpa'])} | ROAS {ev['roas'] if ev['roas'] is not None else '-'} | CVR {pct(ev['cvr'])}")
    if not ev["sufficient"]:
        cfg = paths.config()
        out(f"INSUFFICIENT DATA: needs ≥ ${cfg['min_spend_for_verdict_usd']} spend and "
            f"≥ {cfg['min_clicks_for_verdict']} clicks before any verdict.")
    if t["verdict"]:
        out(f"verdict: {t['verdict']}")
    if t["lesson"]:
        out(f"lesson: {t['lesson']}")


def cmd_test_status(conn, args):
    records.test_update(conn, args.id, status=args.status, verdict=args.verdict, lesson=args.lesson)
    out(f"{args.id.upper()} → {args.status}")


def cmd_test_list(conn, args):
    for t in conn.execute("SELECT * FROM tests ORDER BY id DESC LIMIT 50"):
        out(f"X{t['id']} [{t['status']}] {t['product']}: {t['hypothesis'][:90]}")


def cmd_cost_log(conn, args):
    records.cost_log(conn, args.service, args.amount, task=args.task, note=args.note)
    ok, spent, budget = records.cost_check(conn, 0)
    out(f"Logged. Month to date A${spent:.2f} of A${budget:.2f}.")


def cmd_cost_check(conn, args):
    ok, spent, budget = records.cost_check(conn, args.amount)
    out(f"{'OK' if ok else 'OVER BUDGET'}: A${spent:.2f} spent + A${args.amount:.2f} vs A${budget:.2f} budget.")
    return 0 if ok else 1


def cmd_cost_summary(conn, args):
    rows = conn.execute(
        "SELECT substr(ts,1,7) month, service, SUM(amount_aud) total, COUNT(*) n FROM costs "
        "GROUP BY month, service ORDER BY month DESC, total DESC").fetchall()
    if not rows:
        out("No paid usage logged.")
    for r in rows:
        out(f"{r['month']}  {r['service']:24s} A${r['total']:.2f} ({r['n']} entries)")
    runs = conn.execute("SELECT agent, COUNT(*) n FROM agent_runs WHERE substr(ts,1,7) = strftime('%Y-%m','now') "
                        "GROUP BY agent ORDER BY n DESC").fetchall()
    if runs:
        out("Agent runs this month: " + ", ".join(f"{r['agent'] or '?'}×{r['n']}" for r in runs))


def fmt_journal(row, text_key="text", n=300) -> str:
    who = "Owner " if row["role"] == "owner" else "JARVIS"
    return f"J{row['id']}  {journal.when(row['ts'])} {who}  {journal.short(row[text_key], n)}"


def cmd_recall(conn, args):
    rows = journal.search(conn, args.query, limit=args.limit, role=args.role)
    if not rows:
        out("No earlier conversation matches.")
    for r in rows:
        out(fmt_journal(r, "snippet"))


def cmd_journal(conn, args):
    session = journal.previous_session(conn) if args.previous else None
    for r in journal.recent(conn, limit=args.limit, role=args.role, session_id=session):
        out(fmt_journal(r, n=args.width))


def cmd_dashboard(conn, args):
    from . import dashboard
    conn.close()  # the server opens its own connections per request
    dashboard.serve(port=args.port, open_browser=not args.no_open)


def cmd_ready(conn, args):
    from . import readiness
    if args.n:
        readiness.set_status(args.n, args.status, args.note)
    s = readiness.summary(conn)
    a = s["academy"]
    out(f"Blake Academy: {a['captured']}/{a['lessons']} lessons captured, {a['studied']} studied, "
        f"{a['mentor_entries']} mentor entries, {len(a['playbooks'])} playbooks")
    for c in a["courses"]:
        out(f"  {c['order']:2d}. {c['title']:28s} captured {c['captured']}/{c['lessons']}  studied {c['studied']}/{c['lessons']}")
    out("Readiness for the first product test:" + ("  READY" if s["ready"] else ""))
    for i in s["readiness"]:
        mark = {"done": "[x]", "doing": "[~]", "todo": "[ ]"}[i["status"]]
        out(f"  {mark} {i['n']}. {i['item']}" + (f" ({i['note']})" if i.get("note") else ""))


def cmd_feedback_add(conn, args):
    from . import feedback
    e = feedback.add(conn, about=args.about, kind=args.kind, verdict=args.verdict, source=args.source, what=args.what,
                     why=args.why, keep=args.keep or "", change=args.change or "", evidence=args.evidence or "",
                     product=args.product, agent=args.agent, task=args.task, features=args.features or "",
                     metrics=args.metrics or "")
    out(f"Feedback saved {e.id} -> {paths.rel(e.path)} (and the ledger at brain/feedback/ledger.md)")


def cmd_feedback_list(conn, args):
    from . import feedback
    for r in feedback.rows(conn, agent=args.agent, verdict=args.verdict, kind=args.kind, product=args.product, limit=args.limit):
        out(fmt_hit(r))


def cmd_feedback_digest(conn, args):
    from . import feedback
    out(feedback.digest(conn, agent=args.agent))


def cmd_mentor_serve(conn, args):
    from . import mentor_capture
    conn.close()
    mentor_capture.serve(port=args.port)


def cmd_mentor_import(conn, args):
    from . import mentor_capture
    folder = Path(args.folder).expanduser() if args.folder else Path.home() / "Downloads"
    done = mentor_capture.import_bundles(folder, remove=not args.keep, conn=conn)
    out(f"Imported {len(done)} lesson(s) from {folder}")
    for d in done:
        out(f"  {d}")


def cmd_mentor_script(conn, args):
    from . import mentor_capture
    target = paths.home() / "scratch" / "skool-install.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(mentor_capture.install_snippet(), encoding="utf-8")
    out(f"Install snippet written to {paths.rel(target)}. Run its contents once per Skool tab, then on each lesson "
        "page: eval(sessionStorage.jarvisSkool); await JarvisSkool.captureLesson({...})")


def cmd_mentor_status(conn, args):
    from . import mentor_capture
    done = mentor_capture.captured()
    info = mentor_capture.info_path()
    out(f"Receiver: {'running (' + paths.rel(info) + ')' if info.exists() else 'not running'}")
    out(f"Captured lessons: {len(done)} under {paths.rel(mentor_capture.root())}")
    for d in done:
        out(f"  {d}")


def cmd_watch(conn, args):
    if args.setup:
        out(f"Video tools ready in {media.setup()}")
        return
    if not args.urls:
        raise media.MediaError("Give one or more links, e.g. bin/jarvis watch https://www.instagram.com/reel/...")
    for url in args.urls:
        out(f"Watching {url} ...")
        entry, folder = media.watch(conn, url, note=args.note or "", category=args.category, product=args.product)
        out(f"Saved {entry.id}: {entry.title}\n  files: {paths.rel(folder)}")


def cmd_telegram_pair(conn, args):
    if telegram.settings().get("chat_id"):
        out("Already paired with the owner's chat.")
        return
    out(f"Send this code to the bot from the owner's phone: {telegram.pairing_code()}")


def cmd_telegram_status(conn, args):
    s = telegram.settings()
    out(f"Token: {'set' if s.get('bot_token') else 'missing'}; paired: {'yes' if s.get('chat_id') else 'no'}")
    if s.get("bot_token"):
        bot = telegram.call("getMe")
        out(f"Bot: @{bot.get('username')} ({bot.get('first_name')})")


def cmd_telegram_send(conn, args):
    from . import tts
    voice = None
    if args.voice:
        kokoro = tts.Kokoro()
        try:
            voice = telegram.speak(kokoro, args.text)
        finally:
            kokoro.stop()
    telegram.send(args.text, voice)
    out("Sent to the owner's phone" + (" with a voice note." if voice else "."))


PRIVATE_SKILL_PREFIXES = ("blake-",)


def cmd_skills_backup(conn, args):
    """Mirror private skills (Blake's playbooks) between ~/.claude/skills and brain/skills.

    Private skills hold the mentor's paid material, so they live outside this public repo. brain/skills is
    the backed-up copy (OneDrive and daily brain backups). --restore copies them back onto a new machine."""
    live, safe = Path.home() / ".claude" / "skills", paths.home() / "skills"
    src, dst = (safe, live) if args.restore else (live, safe)
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for d in sorted(src.glob("*")) if src.exists() else []:
        if d.is_dir() and d.name.startswith(PRIVATE_SKILL_PREFIXES) and (d / "SKILL.md").exists():
            shutil.copytree(d, dst / d.name, dirs_exist_ok=True)
            n += 1
    out(f"{'Restored' if args.restore else 'Backed up'} {n} private skill(s): {src} -> {dst}")
    if args.zip:
        import zipfile
        zdir = Path.home() / "Downloads" / "jarvis-skill-uploads" / "blake"
        zdir.mkdir(parents=True, exist_ok=True)
        for d in sorted(live.glob("*")):
            if d.is_dir() and d.name.startswith(PRIVATE_SKILL_PREFIXES) and (d / "SKILL.md").exists():
                with zipfile.ZipFile(zdir / f"{d.name}.zip", "w", zipfile.ZIP_DEFLATED) as z:
                    for f in d.rglob("*"):
                        if f.is_file():
                            z.write(f, f"{d.name}/{f.relative_to(d).as_posix()}")
        out(f"Upload zips for claude.ai (Settings > Skills > Add > Upload): {zdir}")


def cmd_backup(conn, args):
    out(f"Backup → {paths.rel(backup.create(conn, args.label))}")


def cmd_agents(conn, args):
    for r in conn.execute("SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?", (args.limit,)):
        out(f"{r['ts'][:16]} {r['event']:14s} {r['agent'] or '?'}" + (f" — {r['note']}" if r["note"] else ""))


def cmd_log(conn, args):
    for r in conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (args.limit,)):
        out(f"{r['ts'][:16]} {r['tool']} — {r['summary']}")


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jarvis", description="JARVIS memory and records")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create brain folders and seed knowledge").set_defaults(fn=cmd_init)
    sub.add_parser("brief", help="compact state summary").set_defaults(fn=cmd_brief)

    mem = sub.add_parser("mem", help="long-term knowledge").add_subparsers(dest="sub", required=True)
    s = mem.add_parser("search")
    s.add_argument("query")
    s.add_argument("-c", "--category", choices=paths.CATEGORIES)
    s.add_argument("-p", "--product")
    s.add_argument("--provenance", choices=paths.PROVENANCES)
    s.add_argument("-n", "--limit", type=int, default=5)
    s.add_argument("--all", action="store_true", help="include superseded entries")
    s.set_defaults(fn=cmd_mem_search)
    s = mem.add_parser("get")
    s.add_argument("ids", nargs="+")
    s.set_defaults(fn=cmd_mem_get)

    def entry_args(s, required):
        s.add_argument("--title", required=True)
        s.add_argument("--body", default="", help="text, or - for stdin")
        s.add_argument("--body-file")
        s.add_argument("-c", "--category", choices=paths.CATEGORIES)
        s.add_argument("--provenance", choices=paths.PROVENANCES, required=required)
        s.add_argument("-p", "--product")
        s.add_argument("--tags")
        s.add_argument("--confidence", choices=memory.CONFIDENCES)
        s.add_argument("--source")
        s.add_argument("--ref", help="timestamp / page / URL anchor in the source")
        s.add_argument("--status", default="active", choices=("active", "needs-review"))
        s.add_argument("--force", action="store_true", help="save even if it looks like a duplicate")

    s = mem.add_parser("add")
    entry_args(s, required=True)
    s.set_defaults(fn=cmd_mem_add)
    s = mem.add_parser("supersede", help="replace an entry with a corrected version")
    s.add_argument("old_id")
    entry_args(s, required=False)
    s.set_defaults(fn=cmd_mem_supersede)
    s = mem.add_parser("status", help="mark active / needs-review / archived")
    s.add_argument("id")
    s.add_argument("status", choices=("active", "needs-review", "archived"))
    s.add_argument("--note")
    s.set_defaults(fn=cmd_mem_status)
    s = mem.add_parser("list")
    s.add_argument("-c", "--category", choices=paths.CATEGORIES)
    s.add_argument("-p", "--product")
    s.add_argument("--status", choices=memory.STATUSES)
    s.add_argument("-n", "--limit", type=int, default=50)
    s.set_defaults(fn=cmd_mem_list)
    mem.add_parser("stats").set_defaults(fn=cmd_mem_stats)
    s = mem.add_parser("reindex")
    s.add_argument("--full", action="store_true")
    s.set_defaults(fn=cmd_mem_reindex)

    prod = sub.add_parser("product").add_subparsers(dest="sub", required=True)
    s = prod.add_parser("add")
    s.add_argument("name")
    s.add_argument("--stage", default="idea", choices=records.PRODUCT_STAGES)
    s.add_argument("--notes")
    s.set_defaults(fn=cmd_product_add)
    prod.add_parser("list").set_defaults(fn=cmd_product_list)
    s = prod.add_parser("show")
    s.add_argument("slug")
    s.set_defaults(fn=cmd_product_show)
    s = prod.add_parser("stage")
    s.add_argument("slug")
    s.add_argument("stage", choices=records.PRODUCT_STAGES)
    s.add_argument("--notes")
    s.set_defaults(fn=cmd_product_stage)

    task = sub.add_parser("task").add_subparsers(dest="sub", required=True)
    s = task.add_parser("add")
    s.add_argument("title")
    s.add_argument("--agent")
    s.add_argument("-p", "--product")
    s.add_argument("--parent")
    s.add_argument("--desc")
    s.add_argument("--priority", type=int, default=2, choices=(1, 2, 3))
    s.set_defaults(fn=cmd_task_add)
    s = task.add_parser("list")
    s.add_argument("--all", action="store_true")
    s.add_argument("--status", choices=records.TASK_STATUSES)
    s.add_argument("--agent")
    s.add_argument("-p", "--product")
    s.add_argument("-n", "--limit", type=int, default=30)
    s.set_defaults(fn=cmd_task_list)
    for name, status in (("start", "in_progress"), ("block", "blocked"), ("cancel", "cancelled"),
                         ("reopen", "todo")):
        s = task.add_parser(name)
        s.add_argument("id")
        s.add_argument("--note")
        s.set_defaults(fn=cmd_task_status(status))
    for name, fn in (("show", cmd_task_show), ("path", cmd_task_path)):
        s = task.add_parser(name)
        s.add_argument("id")
        s.set_defaults(fn=fn)
    s = task.add_parser("note")
    s.add_argument("id")
    s.add_argument("note")
    s.set_defaults(fn=cmd_task_note)
    s = task.add_parser("done")
    s.add_argument("id")
    s.add_argument("--summary", required=True)
    s.add_argument("--deliverable")
    s.add_argument("--no-deliverable", action="store_true")
    s.set_defaults(fn=cmd_task_done)

    s = sub.add_parser("decide", help="record a decision")
    s.add_argument("decision")
    s.add_argument("--why")
    s.add_argument("-p", "--product")
    s.add_argument("--task")
    s.add_argument("--by", default="user", choices=("user", "jarvis"))
    s.set_defaults(fn=cmd_decide)

    ap = sub.add_parser("approval").add_subparsers(dest="sub", required=True)
    s = ap.add_parser("request")
    s.add_argument("kind", choices=records.APPROVAL_KINDS)
    s.add_argument("summary")
    s.add_argument("--detail")
    s.add_argument("--task")
    s.add_argument("--amount", type=float)
    s.add_argument("--currency")
    s.set_defaults(fn=cmd_approval_request)
    s = ap.add_parser("list")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_approval_list)
    for name, approved in (("approve", True), ("reject", False)):
        s = ap.add_parser(name)
        s.add_argument("id")
        s.add_argument("--note")
        s.set_defaults(fn=cmd_approval_decide(approved))

    tst = sub.add_parser("test", help="product tests").add_subparsers(dest="sub", required=True)
    s = tst.add_parser("add")
    s.add_argument("product")
    s.add_argument("hypothesis")
    s.add_argument("--angle")
    s.add_argument("--budget", type=float, help="USD")
    s.add_argument("--days", type=int)
    s.add_argument("--criteria")
    s.set_defaults(fn=cmd_test_add)
    s = tst.add_parser("metric", help="record one day of results")
    s.add_argument("id")
    s.add_argument("--day", required=True)
    for name in ("spend", "revenue"):
        s.add_argument(f"--{name}", type=float, default=0)
    for name in ("impressions", "clicks", "atc", "checkouts", "purchases"):
        s.add_argument(f"--{name}", type=int, default=0)
    s.add_argument("--source", default="manual")
    s.set_defaults(fn=cmd_test_metric)
    s = tst.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn=cmd_test_show)
    s = tst.add_parser("status")
    s.add_argument("id")
    s.add_argument("status", choices=records.TEST_STATUSES)
    s.add_argument("--verdict")
    s.add_argument("--lesson")
    s.set_defaults(fn=cmd_test_status)
    tst.add_parser("list").set_defaults(fn=cmd_test_list)

    cost = sub.add_parser("cost", help="paid AI/API usage").add_subparsers(dest="sub", required=True)
    s = cost.add_parser("log")
    s.add_argument("service")
    s.add_argument("amount", type=float, help="AUD")
    s.add_argument("--task")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_cost_log)
    s = cost.add_parser("check", help="exit 1 if this spend would break the monthly budget")
    s.add_argument("amount", type=float, help="AUD")
    s.set_defaults(fn=cmd_cost_check)
    cost.add_parser("summary").set_defaults(fn=cmd_cost_summary)

    s = sub.add_parser("recall", help="search earlier conversations (the journal)")
    s.add_argument("query")
    s.add_argument("-n", "--limit", type=int, default=8)
    s.add_argument("--role", choices=journal.ROLES)
    s.set_defaults(fn=cmd_recall)
    s = sub.add_parser("journal", help="show recent conversation")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.add_argument("--role", choices=journal.ROLES)
    s.add_argument("--previous", action="store_true", help="only the most recent session")
    s.add_argument("-w", "--width", type=int, default=300)
    s.set_defaults(fn=cmd_journal)

    s = sub.add_parser("dashboard", help="live dashboard: watch the agents and talk to JARVIS")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-open", action="store_true", help="don't open the browser")
    s.set_defaults(fn=cmd_dashboard)

    s = sub.add_parser("ready", help="Blake course coverage and the first-product-test readiness checklist")
    s.add_argument("n", nargs="?", type=int, help="checklist item to update")
    s.add_argument("status", nargs="?", choices=("todo", "doing", "done"))
    s.add_argument("--note")
    s.set_defaults(fn=cmd_ready)

    fb = sub.add_parser("feedback", help="the feedback loop: record what worked and what didn't").add_subparsers(
        dest="sub", required=True)
    s = fb.add_parser("add", help="record feedback on something the team made")
    s.add_argument("about", help="what the feedback is about, e.g. 'BOF static #4 for posture corrector'")
    s.add_argument("--kind", required=True, help="ad, creative, copy, pdp, store, test, campaign, research, product, process, other")
    s.add_argument("--verdict", required=True, choices=("good", "bad", "mixed"))
    s.add_argument("--source", required=True, choices=("owner", "data", "qc", "blake"))
    s.add_argument("--what", required=True, help="what was made (angle, format, headline, offer...)")
    s.add_argument("--why", required=True, help="why it worked or didn't (best understanding)")
    s.add_argument("--keep", help="what to keep doing")
    s.add_argument("--change", help="what to change next time")
    s.add_argument("--evidence", help="numbers or quotes behind the verdict")
    s.add_argument("-p", "--product")
    s.add_argument("--agent", help="the agent whose work this was")
    s.add_argument("--task", help="T<n> it came from")
    s.add_argument("--features", help="fixed feature list: format; focus; content type; messaging; hook; headline; offer (+ where)")
    s.add_argument("--metrics", help="fixed metric set: spend; CPC; ATC; checkouts; purchases; cost per sale; source of each")
    s.set_defaults(fn=cmd_feedback_add)
    s = fb.add_parser("list", help="past feedback, newest first")
    s.add_argument("--agent")
    s.add_argument("--verdict", choices=("good", "bad", "mixed"))
    s.add_argument("--kind")
    s.add_argument("-p", "--product")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.set_defaults(fn=cmd_feedback_list)
    s = fb.add_parser("digest", help="patterns: what keeps working and what keeps failing")
    s.add_argument("--agent")
    s.set_defaults(fn=cmd_feedback_digest)

    mentor = sub.add_parser("mentor", help="capture mentor lessons from the owner's browser").add_subparsers(
        dest="sub", required=True)
    s = mentor.add_parser("serve", help="receive lessons captured by core/jarvis/skool_capture.js")
    s.add_argument("--port", type=int, default=8766)
    s.set_defaults(fn=cmd_mentor_serve)
    s = mentor.add_parser("import", help="file lesson bundles saved by the browser into the brain")
    s.add_argument("--folder", help="where Chrome saved them (default: ~/Downloads)")
    s.add_argument("--keep", action="store_true", help="keep the bundle files after importing")
    s.set_defaults(fn=cmd_mentor_import)
    s = mentor.add_parser("script", help="write the one-time browser install snippet")
    s.set_defaults(fn=cmd_mentor_script)
    s = mentor.add_parser("status", help="list captured lessons")
    s.set_defaults(fn=cmd_mentor_status)

    s = sub.add_parser("watch", help="watch reels or videos the owner sends: transcript, frames and a memory entry")
    s.add_argument("urls", nargs="*", help="Instagram, TikTok or YouTube links")
    s.add_argument("--note", help="what the owner wants taken from it")
    s.add_argument("-c", "--category", default="creative-inspiration", choices=paths.CATEGORIES)
    s.add_argument("-p", "--product")
    s.add_argument("--setup", action="store_true", help="install the free video tools (yt-dlp, faster-whisper)")
    s.set_defaults(fn=cmd_watch)

    tg = sub.add_parser("telegram", help="the owner's phone: pair the bot, check it, send a message").add_subparsers(
        dest="sub", required=True)
    tg.add_parser("pair", help="print the code the owner sends the bot to pair").set_defaults(fn=cmd_telegram_pair)
    tg.add_parser("status", help="token, pairing and bot name").set_defaults(fn=cmd_telegram_status)
    s = tg.add_parser("send", help="message the owner's phone (briefs, approvals, alerts)")
    s.add_argument("text")
    s.add_argument("--voice", action="store_true", help="also send it as a voice note in JARVIS's voice")
    s.set_defaults(fn=cmd_telegram_send)

    skills = sub.add_parser("skills", help="private skills (Blake's playbooks)").add_subparsers(dest="sub", required=True)
    s = skills.add_parser("backup", help="copy ~/.claude/skills/blake-* into brain/skills (or back with --restore)")
    s.add_argument("--restore", action="store_true", help="copy brain/skills back to ~/.claude/skills")
    s.add_argument("--zip", action="store_true", help="also package each one for upload to claude.ai Skills")
    s.set_defaults(fn=cmd_skills_backup)

    s = sub.add_parser("backup")
    s.add_argument("--label", default="manual")
    s.set_defaults(fn=cmd_backup)
    for name, fn in (("agents", cmd_agents), ("log", cmd_log)):
        s = sub.add_parser(name)
        s.add_argument("-n", "--limit", type=int, default=20)
        s.set_defaults(fn=fn)
    return p


def main(argv=None) -> int:
    # Windows consoles default to a legacy code page; keep output (…, →, $) printable.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    conn = db.connect()
    try:
        return args.fn(conn, args) or 0
    except (memory.MemoryError, records.RecordError, feedback.FeedbackError, media.MediaError,
            telegram.TelegramError) as exc:
        sys.stderr.write(f"jarvis: {exc}\n")
        return 2
    finally:
        conn.close()
