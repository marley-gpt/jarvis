"""`jarvis` command line: the single interface agents and the user share for memory and records."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import activity, backup, dicloak, feedback, jobs, brief, db, journal, media, memory, pace, paths, records, skills, team, telegram

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
    push_approval(conn, aid)


def push_approval(conn, aid: int) -> None:
    if telegram.settings().get("chat_id"):
        try:
            telegram.push_approval(conn.execute("SELECT * FROM approvals WHERE id = ?", (aid,)).fetchone())
            out("Sent to the owner's phone with Approve / Reject buttons.")
        except telegram.TelegramError as exc:
            out(f"(Couldn't reach the owner's phone: {exc})")


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


def cmd_money_add(conn, args):
    mid = records.money_add(conn, args.kind, args.category, args.amount, currency=args.currency, aud=args.aud,
                            day=args.date, product=args.product, test=args.test, note=args.note, source=args.source,
                            ref=args.ref)
    row = conn.execute("SELECT * FROM money WHERE id = ?", (mid,)).fetchone()
    out(f"M{mid}: {row['day']} {row['kind']} {row['category']} A${row['amount_aud']:.2f}")


def cmd_money_list(conn, args):
    since = (datetime.now() - timedelta(days=args.days - 1)).strftime("%Y-%m-%d")
    rows = conn.execute("SELECT * FROM money WHERE day >= ? ORDER BY day DESC, id DESC", (since,)).fetchall()
    if not rows:
        out(f"Nothing recorded in the last {args.days} days.")
    for r in rows:
        sign = "+" if r["kind"] == "income" else "-"
        extra = " ".join(filter(None, [f"[{r['product']}]" if r["product"] else "",
                                        f"X{r['test_id']}" if r["test_id"] else "", r["note"] or ""]))
        out(f"{r['day']}  {sign}A${r['amount_aud']:>9.2f}  {r['category']:13s} {extra}".rstrip())


def money_lines(rep: dict) -> list[str]:
    lines = [f"Profit and loss {rep['start']} to {rep['end']} ({rep['days']} days):",
             f"  Sales in:   A${rep['total_income']:.2f}"]
    lines += [f"    {k}: A${v:.2f}" for k, v in rep["income"].items()]
    lines.append(f"  Costs out:  A${rep['total_expenses']:.2f}")
    lines += [f"    {k}: A${v:.2f}" for k, v in rep["expenses"].items()]
    verdict = "ahead of" if rep["gap"] >= 0 else "behind"
    lines.append(f"  Profit:     A${rep['profit']:.2f}  (target A${rep['target']:.2f}, {verdict} by A${abs(rep['gap']):.2f})")
    return lines


def cmd_money_report(conn, args):
    end = args.end or datetime.now().strftime("%Y-%m-%d")
    start = args.start or (datetime.fromisoformat(end) - timedelta(days=args.days - 1)).strftime("%Y-%m-%d")
    rep = records.money_report(conn, start, end)
    if args.json:
        out(json.dumps(rep, indent=2))
    else:
        out("\n".join(money_lines(rep)))


def cmd_money_test(conn, args):
    p = records.test_pnl(conn, args.id)
    if args.json:
        out(json.dumps(p, indent=2))
        return
    out(f"Test {p['test']} {p['product']} ({p['status']}, {p['days']} days, {p['purchases']} sales):")
    out(f"  Sales A${p['revenue']:.2f} - ad spend A${p['ad_spend']:.2f}"
        + "".join(f" - {k} A${v:.2f}" for k, v in p["other_costs"].items()) + f" = profit A${p['profit']:.2f}")
    if not p["sufficient"]:
        out("  Not enough data for a verdict yet.")


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


def cmd_job_add(conn, args):
    jid = jobs.add(conn, args.text, args.source)
    out(f"J{jid} queued for the desktop runner." + ("" if jobs.runner_alive() else
        " The runner isn't running right now, so it starts when the owner next opens it at the PC."))


def cmd_job_list(conn, args):
    rows = jobs.listing(conn, args.n)
    out(f"Runner: {'running' if jobs.runner_alive() else 'not running'}")
    if not rows:
        out("No jobs yet.")
    for r in rows:
        out(f"J{r['id']} [{r['status']}] {r['text'][:90]}" + (f" -> {r['summary'][:90]}" if r["summary"] else ""))


def cmd_job_wait(conn, args):
    job = jobs.wait(conn, args.timeout)
    out(f"J{job['id']} from {job['source']}: {job['text']}" if job else "No job yet: run this again to keep waiting.")


def cmd_job_finish(ok):
    def run(conn, args):
        row = jobs.finish(conn, args.id, args.summary, ok)
        out(f"J{row['id']} {row['status']}")
        if not args.quiet and telegram.settings().get("chat_id"):
            telegram.send(("" if ok else "Sir, that job failed. ") + args.summary)
            out("Sent to the owner's phone.")
    return run


def cmd_metrics(conn, args):
    from . import hq
    if args.sub == "set":
        raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
        try:
            snap = hq.metrics_set(json.loads(raw))
        except (ValueError, OSError) as exc:
            raise records.RecordError(f"metrics: {exc}") from exc
        out(f"Saved the {snap['period']} snapshot ({len(snap['shopify'])} Shopify and {len(snap['meta'])} Meta figures) for the HQ.")
        return
    snap = hq.metrics()
    if not snap:
        out("No metrics snapshot yet. JARVIS pulls Shopify and Meta figures read-only and saves them with: bin/jarvis metrics set --file <json>")
        return
    out(f"Pulled {snap['pulled']} ({snap['period']}, {snap['source']})")
    for section in ("shopify", "meta"):
        out(f"  {section}: " + ", ".join(f"{k}={v}" for k, v in snap[section].items()))
def cmd_event_add(conn, args):
    eid = activity.add(conn, args.agent, args.action, status=args.status, task=args.task, product=args.product,
                       tool=args.tool, file=args.file, summary=args.summary,
                       with_agent=args.with_agent)
    out(f"E{eid} logged")


def cmd_event_list(conn, args):
    rows = activity.recent(conn, args.n, agent=args.agent)
    if not rows:
        out("No activity yet.")
    for r in reversed(rows):
        where = " ".join(x for x in (f"T{r['task_id']}" if r["task_id"] else "", r["product"] or "",
                                     r["tool"] or "", r["file"] or "",
                                     f"with {r['with_agent']}" if r["with_agent"] else "") if x)
        out(f"E{r['id']} {r['ts']} {r['agent']} [{r['status']}] {r['action']}" + (f" ({where})" if where else "")
            + (f" - {r['summary']}" if r["summary"] else ""))


def cmd_dicloak(conn, args):
    if args.sub == "status":
        titles = dicloak.windows()
        out("Master Key profile: " + ("open (" + "; ".join(titles) + ")" if titles else "not open"))
        h = dicloak.holder()
        out("Screen: " + (f"in use by {h['who']}" if h else "free"))
    elif args.sub == "show":
        out("Brought to the front: " + "; ".join(dicloak.show()))
    elif args.sub == "lock":
        dicloak.lock(args.who)
        out(f"DICloak screen locked for {args.who}. Run `bin/jarvis dicloak unlock {args.who}` when done.")
    elif args.sub == "unlock":
        out("Unlocked." if dicloak.unlock(args.who) else "Not yours to unlock.")


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


def cmd_alert(conn, args):
    from . import tts
    kokoro = tts.Kokoro() if args.urgent else None
    try:
        sent = telegram.alert(args.text, args.urgent, kokoro)
    finally:
        if kokoro:
            kokoro.stop()
    out("Alert sent: " + ", ".join(sent) + ".")


def cmd_telegram_ring(conn, args):
    out("Calling the owner: " + telegram.ring())


def cmd_call_serve(conn, args):
    from . import call
    call.serve(port=args.port)


def cmd_call_link(conn, args):
    from . import call
    out(call.link())


def cmd_call_ring(conn, args):
    from . import call
    try:
        call.ring(args.say or "", args.phone)
    except (RuntimeError, telegram.TelegramError) as exc:
        out(f"Couldn't ring: {exc}")
        return 1
    out("Ringing the owner: the Answer button is on their phone.")


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


def _agents_arg(value) -> list[str]:
    return [a.strip() for a in (value or "").split(",") if a.strip()]


def cmd_skills_list(conn, args):
    for name in [args.agent] if args.agent else skills.agent_names():
        levels = skills.skill_levels(conn, name)
        out(f"{name}: {len(levels)} skills")
        for s in levels:
            bar = "#" * s["level"] + "." * (5 - s["level"])
            out(f"  [{bar}] {s['name']:28s} {s['label']:10s} {s['how']:7s} uses {s['uses']}, notes {s['notes']}")


def cmd_skills_gaps(conn, args):
    g = skills.gaps(conn, args.agent)
    out(f"Gaps for {args.agent}:" if g["items"] else f"No recorded gaps for {args.agent}.")
    for item in g["items"]:
        out(f"  - {item}")
    if g["keywords"]:
        out("Keywords: " + ", ".join(g["keywords"]))
    found = skills.suggest_installed(conn, args.agent, g)
    if found:
        out("Installed skills that fit and aren't assigned yet:")
        for c in found:
            out(f"  {c['name']:28s} {c['fit']:6s} score {c['score']:5}  {c['description'][:100]}")


def cmd_skills_brief(conn, args):
    out(json.dumps(skills.brief(conn, args.agent), default=str, indent=1) if args.json
        else skills.brief_text(conn, args.agent))


def cmd_skills_due(conn, args):
    agents = skills.due(conn)
    if args.json:
        out(json.dumps(agents))
        return
    out("Due for a learning run now: " + (", ".join(agents) if agents else "nobody (" + pace.status(conn)["status"] + ")"))


def cmd_skills_run_start(conn, args):
    rid, path = skills.run_start(conn, args.agent)
    out(f"L{rid} started for {args.agent}. Brief: {paths.rel(path)}")


def cmd_skills_run_done(conn, args):
    skills.run_done(conn, args.id, args.summary, "skipped" if args.skipped else "done")
    out(f"{args.id} closed.")


def cmd_skills_runs(conn, args):
    skills.ensure(conn)
    for r in conn.execute("SELECT * FROM skill_runs ORDER BY id DESC LIMIT ?", (args.limit,)):
        out(f"L{r['id']} {r['ts'][:16]} {r['kind']:5s} {r['agent']:18s} {r['status']:8s} {r['summary'] or ''}")


def cmd_skills_note_add(conn, args):
    nid = skills.note_add(conn, args.agent, args.title, read_body(args), skill=args.skill,
                          for_agents=_agents_arg(args.for_agents), source=args.source)
    row = skills.note_get(conn, nid)
    out(f"N{nid} saved ({row['memory_id']}), for {row['for_agents'].replace(',', ', ')}.")


def cmd_skills_notes(conn, args):
    rows = skills.note_list(conn, agent=args.agent, for_agent=args.for_agent,
                            status=None if args.all else "active", limit=args.limit)
    if not rows:
        out("No skill notes yet.")
    for n in rows:
        out(f"N{n['id']} {n['ts'][:10]} [{n['status']}] {n['agent']}: {n['title']}"
            f"{' (' + n['skill'] + ')' if n['skill'] else ''}  helped {n['helped']}/{n['uses']}  "
            f"for {n['for_agents']}  {n['memory_id']}")


def cmd_skills_used(conn, args):
    helped = True if args.helped else False if args.unhelpful else None
    skills.log_use(conn, args.agent, skill=args.skill, note=args.note, helped=helped, task=args.task)
    out("Logged.")


def cmd_skills_teach(conn, args):
    added = skills.teach(conn, args.ref, _agents_arg(args.for_agents), by=args.by, why=args.why or "")
    out(f"{args.ref} now taught to: {', '.join(added)}" if added else f"{args.ref} was already tagged for them.")


def cmd_skills_candidate_add(conn, args):
    cid = skills.candidate_add(conn, args.agent, args.name, origin=args.origin, what=args.what, source=args.source,
                               cost=args.cost, why=args.why)
    out(f"C{cid} queued for review (nothing installed).")


def cmd_skills_review(conn, args):
    q = skills.review_queue(conn)
    if not q["candidates"] and not q["changes"]:
        out("Nothing waiting for review.")
    for c in q["candidates"]:
        cost = "free" if skills.is_free(c["cost"]) else f"COSTS {c['cost']} (needs approval request spend)"
        out(f"C{c['id']} {c['agent']} <- {c['name']} [{c['origin']}] {cost}")
        out(f"    source: {c['source'] or '-'}")
        out(f"    what (untrusted, as described): {c['what']}")
        out(f"    why: {c['why'] or '-'}")
    for c in q["changes"]:
        out(f"P{c['id']} {c['kind']:8s} {c['agent']}: {c['what']}  (why: {c['why']})")


def cmd_skills_decide(approve: bool):
    def run(conn, args):
        out(skills.decide(conn, args.ref, approve, by=args.by, note=args.note or "", approval=args.approval))
    return run


def cmd_skills_installed(conn, args):
    out(f"P{skills.mark_installed(conn, args.ref, args.as_name)} queued: assign it (review with `skills review`).")


def cmd_skills_merge(conn, args):
    res = skills.merge(conn, days=args.days)
    out(f"Taught {len(res['taught'])}: " + (", ".join(res["taught"][:20]) or "-"))
    out(f"Proposed {len(res['proposed'])} change(s) for review: " + (", ".join(res["proposed"]) or "-"))


def cmd_skills_changes(conn, args):
    skills.ensure(conn)
    sql = "SELECT * FROM skill_changes" + (" WHERE agent = ?" if args.agent else "") + " ORDER BY id DESC LIMIT ?"
    for c in conn.execute(sql, ([args.agent] if args.agent else []) + [args.limit]):
        who = f" by {c['decided_by']}" if c["decided_by"] else ""
        out(f"P{c['id']} {c['ts'][:10]} {c['status']:8s}{who} {c['kind']:8s} {c['agent']}: {c['what']} "
            f"(why: {c['why']}{'; ' + c['decision_note'] if c['decision_note'] else ''})")


def cmd_skills_board(conn, args):
    out(skills.board_json(conn, args.agent))


def cmd_skills_mine(conn, args):
    """What an agent reads before working: its learned playbook and the notes taught to it."""
    path = skills.playbook_path(args.agent)
    out(path.read_text(encoding="utf-8") if path.exists() else f"No learned playbook for {args.agent} yet.")
    rows = skills.note_list(conn, for_agent=args.agent, limit=args.limit)
    if rows:
        out("\nActive skill notes for you (`mem get <id>` to read; log what helped with `skills used`):")
        for n in rows:
            out(f"  N{n['id']} {n['memory_id']} {n['title']} (by {n['agent']})")


def cmd_pace(conn, args):
    if args.json:
        out(json.dumps(pace.status(conn), indent=1))
    else:
        out(pace.summary(conn))


def cmd_pace_record(conn, args):
    pace.record(conn, args.weekly, args.resets, five_hour=args.five_hour, five_hour_resets=args.five_hour_resets,
                plan=args.plan)
    out("Recorded. " + pace.summary(conn))


def cmd_team_delegate(conn, args):
    sid = team.delegate(conn, args.parent, args.job, child=args.child, task=args.task)
    out(f"S{sid} queued: {args.child or args.parent} will take it in a fresh run (JARVIS: `team next`).")


def cmd_team_next(conn, args):
    rows = team.queued(conn, args.limit)
    if args.json:
        out(json.dumps([{**dict(r), "id": f"S{r['id']}"} for r in rows], indent=1))
        return
    if not rows:
        out("No sub-agent jobs waiting.")
    for r in rows:
        out(f"S{r['id']} {r['parent']} -> {r['child']}{' (' + r['task'] + ')' if r['task'] else ''}: {r['job']}")


def cmd_team_sub(status):
    def run(conn, args):
        team.sub_update(conn, args.id, status, getattr(args, "result", None))
        out(f"{args.id} {status}.")
    return run


def cmd_team_subs(conn, args):
    team.ensure(conn)
    sql = "SELECT * FROM sub_agents" + (" WHERE parent = ?" if args.parent else "") + " ORDER BY id DESC LIMIT ?"
    for r in conn.execute(sql, ([args.parent] if args.parent else []) + [args.limit]):
        out(f"S{r['id']} {r['ts'][:16]} {r['status']:7s} {r['parent']} -> {r['child']}: {r['job'][:80]}"
            + (f"\n    result: {r['result']}" if r["result"] else ""))


def cmd_team_propose(conn, args):
    hid, tier, reasons = team.propose(conn, args.proposer, args.name, role=args.role, body=read_body(args),
                                      tools=_agents_arg(args.tools), model=args.model,
                                      skill_list=_agents_arg(args.skills), team=args.team, cost=args.cost,
                                      why=args.why)
    if tier == "owner":
        aid = team.hire_get(conn, hid)["approval_id"]
        out(f"H{hid} needs the owner first ({'; '.join(reasons)}): approval A{aid} requested.")
        push_approval(conn, aid)
    else:
        out(f"H{hid} drafted: within the team's role and tools, free. JARVIS reviews it with `team approve H{hid}`.")


def cmd_team_review(conn, args):
    team.ensure(conn)
    rows = conn.execute("SELECT * FROM hires WHERE status = 'proposed' ORDER BY id").fetchall()
    if not rows:
        out("No agent proposals waiting.")
    for r in rows:
        who = "JARVIS can approve" if r["tier"] == "jarvis" else f"owner first (A{r['approval_id']}): {r['tier_reasons']}"
        out(f"H{r['id']} {r['name']} [{r['team']}, {r['model']}] by {r['proposer']}: {r['role']}")
        out(f"    tools: {r['tools']}; skills: {r['skills']}; cost: {r['cost']}; {who}")
        out(f"    draft: {r['draft']}; why: {r['why'] or '-'}")


def cmd_team_approve(conn, args):
    out(team.approve(conn, args.id, by=args.by, note=args.note or ""))


def cmd_team_reject(conn, args):
    out(team.reject(conn, args.id, by=args.by, note=args.note or ""))


def cmd_team_remove(conn, args):
    out(team.remove(conn, args.name, by=args.by, why=args.why))


def cmd_team_board(conn, args):
    out(json.dumps(team.board(conn), default=str, indent=1))


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

    money = sub.add_parser("money", help="the accountant's ledger: every cost and sale, profit and loss").add_subparsers(
        dest="sub", required=True)
    s = money.add_parser("add", help="record a cost (expense) or a sale (income)")
    s.add_argument("kind", choices=("expense", "income"))
    s.add_argument("category", help="expense: " + ", ".join(records.EXPENSE_CATEGORIES)
                   + "; income: " + ", ".join(records.INCOME_CATEGORIES))
    s.add_argument("amount", type=float)
    s.add_argument("--currency", default="AUD")
    s.add_argument("--aud", type=float, help="the AUD value, when the amount is in another currency")
    s.add_argument("--date", help="YYYY-MM-DD (default today)")
    s.add_argument("-p", "--product")
    s.add_argument("--test", help="X<n>: the product test this belongs to")
    s.add_argument("--note")
    s.add_argument("--source", help="where the figure came from (Meta, Shopify order, receipt...)")
    s.add_argument("--ref", help="unique id; recording the same ref again updates it (e.g. meta:2026-10-28)")
    s.set_defaults(fn=cmd_money_add)
    s = money.add_parser("list")
    s.add_argument("--days", type=int, default=30)
    s.set_defaults(fn=cmd_money_list)
    s = money.add_parser("report", help="profit and loss for a period against the weekly target")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--start")
    s.add_argument("--end")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_money_report)
    s = money.add_parser("test", help="profit and loss for one product test")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_money_test)

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

    jb = sub.add_parser("job", help="desktop jobs: screen work queued from the phone or a schedule").add_subparsers(
        dest="sub", required=True)
    s = jb.add_parser("add", help="queue a job for the desktop runner")
    s.add_argument("text")
    s.add_argument("--source", default="owner")
    s.set_defaults(fn=cmd_job_add)
    s = jb.add_parser("list")
    s.add_argument("-n", type=int, default=15)
    s.set_defaults(fn=cmd_job_list)
    s = jb.add_parser("wait", help="the runner blocks here until a job arrives, and claims it")
    s.add_argument("--timeout", type=float, default=3000, help="seconds before giving up (run it again to keep waiting)")
    s.set_defaults(fn=cmd_job_wait)
    for name, ok in (("done", True), ("fail", False)):
        s = jb.add_parser(name, help="report the job's result; the owner's phone gets the summary")
        s.add_argument("id")
        s.add_argument("--summary", required=True)
        s.add_argument("--quiet", action="store_true", help="don't message the phone")
        s.set_defaults(fn=cmd_job_finish(ok))

    mt = sub.add_parser("metrics", help="the live Shopify and Meta figures the HQ shows (a read-only snapshot JARVIS pulls)").add_subparsers(
        dest="sub", required=True)
    s = mt.add_parser("set", help="save a snapshot: JSON with period, shopify {...}, meta {...}, products [...]")
    s.add_argument("--file", help="read the JSON from this file instead of stdin")
    s.set_defaults(fn=cmd_metrics)
    mt.add_parser("show").set_defaults(fn=cmd_metrics)
    ev = sub.add_parser("event", help="the agent activity log the HQ dashboard reads").add_subparsers(
        dest="sub", required=True)
    s = ev.add_parser("add", help="log what an agent is doing")
    s.add_argument("agent")
    s.add_argument("action")
    s.add_argument("--status", default="working", choices=activity.STATUSES)
    s.add_argument("--task")
    s.add_argument("-p", "--product")
    s.add_argument("--tool")
    s.add_argument("--file")
    s.add_argument("--with", dest="with_agent", help="the other agent, for collaborating")
    s.add_argument("--summary")
    s.set_defaults(fn=cmd_event_add)
    s = ev.add_parser("list")
    s.add_argument("--agent")
    s.add_argument("-n", type=int, default=20)
    s.set_defaults(fn=cmd_event_list)

    dc = sub.add_parser("dicloak", help="the Master Key tools in the DICloak browser: status, show, lock, unlock")
    dc.add_argument("sub", choices=("status", "show", "lock", "unlock"))
    dc.add_argument("who", nargs="?", default="jarvis", help="who is using the screen (lock/unlock)")
    dc.set_defaults(fn=cmd_dicloak)

    tg = sub.add_parser("telegram", help="the owner's phone: pair the bot, check it, send a message").add_subparsers(
        dest="sub", required=True)
    tg.add_parser("pair", help="print the code the owner sends the bot to pair").set_defaults(fn=cmd_telegram_pair)
    tg.add_parser("status", help="token, pairing and bot name").set_defaults(fn=cmd_telegram_status)
    s = tg.add_parser("send", help="message the owner's phone (briefs, approvals, alerts)")
    s.add_argument("text")
    s.add_argument("--voice", action="store_true", help="also send it as a voice note in JARVIS's voice")
    s.set_defaults(fn=cmd_telegram_send)
    tg.add_parser("ring", help="test call: ring the owner's Telegram (CallMeBot, free)").set_defaults(fn=cmd_telegram_ring)
    cl = sub.add_parser("call", help="live voice calls with JARVIS from the phone (our own code, no call service)"
                        ).add_subparsers(dest="sub", required=True)
    s = cl.add_parser("serve", help="run the call server (the app starts it in the background)")
    s.add_argument("--port", type=int, default=8770)
    s.set_defaults(fn=cmd_call_serve)
    cl.add_parser("link", help="print the call link, starting the server and tunnel if needed").set_defaults(
        fn=cmd_call_link)
    s = cl.add_parser("ring", help="JARVIS calls the owner: Telegram message with an Answer button")
    s.add_argument("--say", help="what JARVIS opens the call with")
    s.add_argument("--phone", action="store_true", help="also ring their phone through Telegram (CallMeBot)")
    s.set_defaults(fn=cmd_call_ring)
    s = sub.add_parser("alert", help="push a problem to the owner's phone; --urgent adds a voice note and a call")
    s.add_argument("text")
    s.add_argument("--urgent", action="store_true", help="kill rule hit, store down, money at risk: ring the owner")
    s.set_defaults(fn=cmd_alert)

    skills_p = sub.add_parser("skills", help="skills: private playbooks, and the skill-learning loop").add_subparsers(
        dest="sub", required=True)
    s = skills_p.add_parser("backup", help="copy ~/.claude/skills/blake-* into brain/skills (or back with --restore)")
    s.add_argument("--restore", action="store_true", help="copy brain/skills back to ~/.claude/skills")
    s.add_argument("--zip", action="store_true", help="also package each one for upload to claude.ai Skills")
    s.set_defaults(fn=cmd_skills_backup)
    s = skills_p.add_parser("list", help="each agent's skills with their level")
    s.add_argument("--agent")
    s.set_defaults(fn=cmd_skills_list)
    s = skills_p.add_parser("gaps", help="where an agent's feedback and mistakes say it is weak, and skills that fit")
    s.add_argument("agent")
    s.set_defaults(fn=cmd_skills_gaps)
    s = skills_p.add_parser("brief", help="the learning brief for an agent")
    s.add_argument("agent")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_skills_brief)
    s = skills_p.add_parser("due", help="agents due a learning run today (within the daily cap)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_skills_due)
    run = skills_p.add_parser("run", help="start or finish a learning run").add_subparsers(dest="sub2", required=True)
    s = run.add_parser("start")
    s.add_argument("agent")
    s.set_defaults(fn=cmd_skills_run_start)
    s = run.add_parser("done")
    s.add_argument("id", help="L<n>")
    s.add_argument("--summary", required=True)
    s.add_argument("--skipped", action="store_true")
    s.set_defaults(fn=cmd_skills_run_done)
    s = skills_p.add_parser("runs", help="recent learning and merge runs")
    s.add_argument("-n", "--limit", type=int, default=15)
    s.set_defaults(fn=cmd_skills_runs)
    note = skills_p.add_parser("note", help="skill notes").add_subparsers(dest="sub2", required=True)
    s = note.add_parser("add", help="save a short skill note, tagged for the agents that need it")
    s.add_argument("agent", help="who learned it")
    s.add_argument("title")
    s.add_argument("--body", help="text, or - for stdin")
    s.add_argument("--body-file")
    s.add_argument("--skill", help="the skill it is about")
    s.add_argument("--for", dest="for_agents", help="other agents to teach, comma separated")
    s.add_argument("--source", help="skill name, memory id or URL it came from")
    s.set_defaults(fn=cmd_skills_note_add)
    s = skills_p.add_parser("notes", help="list skill notes")
    s.add_argument("--agent", help="written by")
    s.add_argument("--for", dest="for_agent", help="taught to")
    s.add_argument("--all", action="store_true", help="include promoted and trimmed")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.set_defaults(fn=cmd_skills_notes)
    s = skills_p.add_parser("used", help="log that a skill or note was used, and whether it helped")
    s.add_argument("agent")
    s.add_argument("--skill")
    s.add_argument("--note", help="N<n>")
    s.add_argument("--helped", action="store_true")
    s.add_argument("--unhelpful", action="store_true")
    s.add_argument("--task")
    s.set_defaults(fn=cmd_skills_used)
    s = skills_p.add_parser("teach", help="tag a note (N<n>) or memory entry (lesson, feedback, mistake) for agents")
    s.add_argument("ref")
    s.add_argument("--for", dest="for_agents", required=True)
    s.add_argument("--by", default="jarvis")
    s.add_argument("--why")
    s.set_defaults(fn=cmd_skills_teach)
    cand = skills_p.add_parser("candidate", help="queue a skill, plugin or MCP for review").add_subparsers(
        dest="sub2", required=True)
    s = cand.add_parser("add")
    s.add_argument("agent", help="the agent it is for")
    s.add_argument("name")
    s.add_argument("--origin", required=True, choices=tuple(skills.ORIGINS))
    s.add_argument("--what", required=True, help="what it does, in your words")
    s.add_argument("--source", help="catalogue id, plugin id or URL")
    s.add_argument("--cost", default="free", help="price, credits or 'free'")
    s.add_argument("--why", help="the gap it fixes")
    s.set_defaults(fn=cmd_skills_candidate_add)
    s = skills_p.add_parser("review", help="candidates and changes waiting for review")
    s.set_defaults(fn=cmd_skills_review)
    for name, ok in (("approve", True), ("reject", False)):
        s = skills_p.add_parser(name, help=f"{name} a candidate (C<n>) or proposed change (P<n>)")
        s.add_argument("ref")
        s.add_argument("--by", required=True, choices=skills.REVIEWERS)
        s.add_argument("--note")
        s.add_argument("--approval", help="A<n>: the approved spend request, for anything that costs money")
        s.set_defaults(fn=cmd_skills_decide(ok))
    s = skills_p.add_parser("installed", help="an approved outside skill was installed by hand: queue assigning it")
    s.add_argument("ref")
    s.add_argument("--as", dest="as_name", help="session name of an account or plugin skill, e.g. anthropic-skills:x")
    s.set_defaults(fn=cmd_skills_installed)
    s = skills_p.add_parser("merge", help="weekly: teach new lessons to the agents they concern, propose promotions and trims")
    s.add_argument("--days", type=int, default=30, help="how far back to look for lessons to teach")
    s.set_defaults(fn=cmd_skills_merge)
    s = skills_p.add_parser("changes", help="the promotion log: what changed, why, who reviewed it")
    s.add_argument("--agent")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.set_defaults(fn=cmd_skills_changes)
    s = skills_p.add_parser("board", help="JSON for the dashboard: skills and level, recently learned, backlog")
    s.add_argument("--agent")
    s.set_defaults(fn=cmd_skills_board)
    s = skills_p.add_parser("mine", help="an agent's learned playbook and the notes taught to it")
    s.add_argument("agent")
    s.add_argument("-n", "--limit", type=int, default=10)
    s.set_defaults(fn=cmd_skills_mine)

    tm = sub.add_parser("team", help="sub-agents for hard jobs, and hiring new permanent agents (D36)").add_subparsers(
        dest="sub", required=True)
    s = tm.add_parser("delegate", help="hand part of a hard job to a fresh agent run (no permission needed)")
    s.add_argument("parent", help="the lead agent asking for help")
    s.add_argument("job")
    s.add_argument("--child", help="agent type for the sub-agent (default: same as the parent)")
    s.add_argument("--task", help="T<n> it belongs to")
    s.set_defaults(fn=cmd_team_delegate)
    s = tm.add_parser("next", help="queued sub-agent jobs for JARVIS or the scheduler to run")
    s.add_argument("-n", "--limit", type=int)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_team_next)
    s = tm.add_parser("start")
    s.add_argument("id", help="S<n>")
    s.set_defaults(fn=cmd_team_sub("running"))
    for name in ("done", "fail"):
        s = tm.add_parser(name)
        s.add_argument("id", help="S<n>")
        s.add_argument("--result", required=True)
        s.set_defaults(fn=cmd_team_sub("done" if name == "done" else "failed"))
    s = tm.add_parser("subs", help="sub-agent log: parent, child, job, result")
    s.add_argument("--parent")
    s.add_argument("-n", "--limit", type=int, default=20)
    s.set_defaults(fn=cmd_team_subs)
    s = tm.add_parser("propose", help="draft a new permanent agent for a recurring job in your role")
    s.add_argument("proposer")
    s.add_argument("name", help="lower-case-with-dashes")
    s.add_argument("--role", required=True, help="one-line description: what it does and when to use it")
    s.add_argument("--tools", required=True, help="comma separated, e.g. Bash,Read,Write,Grep")
    s.add_argument("--body", help="its instructions (or - for stdin)")
    s.add_argument("--body-file")
    s.add_argument("--model", default="sonnet", choices=team.MODELS)
    s.add_argument("--skills", help="comma separated skill names")
    s.add_argument("--team", choices=tuple(team.TEAMS), help="default: the proposer's team")
    s.add_argument("--cost", default="free")
    s.add_argument("--why", help="the recurring job it takes on")
    s.set_defaults(fn=cmd_team_propose)
    s = tm.add_parser("review", help="agent proposals waiting")
    s.set_defaults(fn=cmd_team_review)
    for name, fn in (("approve", cmd_team_approve), ("reject", cmd_team_reject)):
        s = tm.add_parser(name)
        s.add_argument("id", help="H<n>")
        s.add_argument("--by", required=True, choices=skills.REVIEWERS)
        s.add_argument("--note")
        s.set_defaults(fn=fn)
    s = tm.add_parser("remove", help="undo a hire: archive the agent file into the brain")
    s.add_argument("name")
    s.add_argument("--by", required=True, choices=skills.REVIEWERS)
    s.add_argument("--why", required=True)
    s.set_defaults(fn=cmd_team_remove)
    s = tm.add_parser("board", help="JSON for the dashboard: teams, sub-agents, hires, pace")
    s.set_defaults(fn=cmd_team_board)

    s = sub.add_parser("pace", help="pace background agent work against the weekly Claude plan limit")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_pace)
    pc = s.add_subparsers(dest="sub")
    r = pc.add_parser("record", help="save a usage reading (from get_usage in a Claude session)")
    r.add_argument("--weekly", type=float, required=True, help="weekly all-models percent used")
    r.add_argument("--resets", required=True, help="when the weekly window resets (ISO time)")
    r.add_argument("--five-hour", type=float, help="5-hour window percent used")
    r.add_argument("--five-hour-resets", help="when the 5-hour window resets (ISO time)")
    r.add_argument("--plan", help="plan name as shown (Pro, Max)")
    r.set_defaults(fn=cmd_pace_record)

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
            telegram.TelegramError, dicloak.DICloakError, jobs.JobError, activity.ActivityError, skills.SkillError,
            team.TeamError) as exc:
        sys.stderr.write(f"jarvis: {exc}\n")
        return 2
    finally:
        conn.close()
