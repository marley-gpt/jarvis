"""The T101 commands (sales, social, projects, routines) and their dashboard JSON, kept out of cli.py and
dashboard.py so those only carry one line each: `ops.register(sub)` and `ops.handle_get(route)`.

Dashboard endpoints (GET, JSON): /api/ops/projects, /api/ops/sales, /api/ops/social, /api/ops/routines and
/api/ops/overview (one status line each plus the projects counts, for a small tile).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import db, paths, projects, routines, sales_watch, social


def out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _json(data) -> None:
    out(json.dumps(data, indent=2, default=str, ensure_ascii=False))


def _read(arg: str | None) -> str:
    return sys.stdin.read() if not arg or arg == "-" else Path(arg).read_text(encoding="utf-8")


# ---------------------------------------------------------------- sales

def cmd_sales(conn, args):
    if args.sub == "setup":
        out(sales_watch.SETUP)
    elif args.sub == "watch":
        rep = sales_watch.watch(conn, notify=(lambda row: "quiet") if args.quiet else sales_watch._notify)
        if args.json:
            return _json(rep)
        if rep["source"] == "off":
            out("The sales feed is off (sales_feed in brain/config.json). Judged the ledger as it stands.")
        else:
            out(f"Pulled through the {rep['source']}.")
        out(f"{rep['new_orders']} new order(s) recorded.")
        for e in rep["errors"]:
            out(f"  error: {e}")
        for a in rep["alerts"]:
            out(f"  alert ({a['kind']}, {a.get('delivery')}): {a['say']}")
        out(sales_watch.status_line(conn))
    elif args.sub == "ingest":
        try:
            payload = json.loads(_read(args.file))
        except ValueError as exc:
            raise sales_watch.SalesError(f"ingest: not JSON ({exc})") from exc
        rep = sales_watch.ingest(conn, payload)
        out(f"{rep['new_orders']} new order(s); {len(rep['alerts'])} alert(s).")
        for a in rep["alerts"]:
            out(f"  {a['say']}")
    elif args.sub == "roas":
        if args.json:
            return _json(sales_watch.roas(conn))
        out(sales_watch.roas_line(conn))
        r = sales_watch.roas(conn)
        if not r["live"]:
            out(f"  {r['why_not_live']}")
    elif args.sub == "breakeven":
        if args.clear or args.value is not None:
            sales_watch.set_breakeven(None if args.clear else args.value, args.note)
        be = sales_watch.breakeven()
        out(f"Breakeven ROAS {be['value']:.2f}x" + (f" ({be['note']})" if be["note"] else "") if be["value"]
            else "No breakeven ROAS set: bin/jarvis sales breakeven <roas> --note '<product, price, costs>'")
    elif args.sub == "feed":
        for a in sales_watch.announcements(args.since, args.n):
            out(f"#{a['id']} {a['ts'][:16]} [{a['kind']}] {a['say']}")
    else:
        s = sales_watch.status(conn)
        if args.json:
            return _json(s)
        out(sales_watch.status_line(conn))
        f = s["figures"]
        out(f"  Spend since the last sale A${f['spend_since_sale_aud']:.2f} over {f['days_without_sale']} day(s) "
            f"(kill rule A${s['settings']['sales_kill_aud']:.0f}); last sale {f['last_sale_day'] or 'none yet'}.")
        last = s["last_watch"]
        out(f"  Last watch: {last['pulled'][:16]}" + (f", errors: {'; '.join(last['errors'])}" if last.get("errors") else "")
            if last else "  Not watched yet: bin/jarvis sales watch")


# ---------------------------------------------------------------- social

def _item_line(i: dict) -> str:
    if i["id"].startswith("S"):
        words = i["caption"] or i["brief"]
        return f"{i['id']} {i['day']} {i['platform']:9s} [{i['status']}] {i['product']} {i['format']}: {words[:80]}"
    return f"{i['id']} {i['platform']:9s} [{i['status']}] {i['author'] or '?'}: \"{i['comment'][:50]}\" -> {i['reply'][:60]}"


def cmd_social(conn, args):
    s = args.sub
    if s == "plan":
        added = social.plan(conn, args.days, [p.strip() for p in args.platforms.split(",") if p.strip()])
        out(f"{len(added)} slot(s) added to the calendar." if added else "The calendar is already full for those days.")
        for i in added:
            out("  " + _item_line(i))
    elif s == "brief":
        path = social.brief(conn)
        out(f"Brief written: {paths.rel(path)}")
        if args.worker:
            from . import work
            run = work.start(path.read_text(encoding="utf-8"), task=args.task or "", agent="creative-director")
            out(f"W{run['id']} (creative-director) is drafting them.")
    elif s == "draft":
        caption = _read(args.caption_file) if args.caption_file else args.caption
        i = social.draft(args.id, caption or "", args.hashtags or "", args.image_brief or "")
        out(_item_line(i) + (f"\n  needs review, honesty rules: {', '.join(i['flags'])}" if i["flags"] else ""))
    elif s == "comment":
        out(_item_line(social.comment(args.platform, args.text, args.post, args.author)))
    elif s == "reply":
        i = social.reply(args.id, args.text)
        out(_item_line(i) + (f"\n  needs review, honesty rules: {', '.join(i['flags'])}" if i["flags"] else ""))
    elif s == "submit":
        from .cli import push_approval
        i, aid = social.submit(conn, args.id, task=args.task)
        out(f"{i['id']} submitted: A{aid} is waiting for the owner. Nothing goes out until it's approved.")
        push_approval(conn, aid)
    elif s == "sync":
        changed = social.sync(conn)
        out("\n".join(_item_line(i) for i in changed) or "No new decisions.")
    elif s == "posted":
        out(_item_line(social.posted(conn, args.id, args.url)))
    elif s == "board":
        _json(social.board())
    else:
        items = social.listing(args.status)
        out("\n".join(_item_line(i) for i in items) or "The social queue is empty: bin/jarvis social plan")


# ---------------------------------------------------------------- projects and routines

def cmd_projects(conn, args):
    if getattr(args, "sub", None) == "deadline":
        projects.set_deadline(conn, args.id, None if args.clear else args.date)
        out(f"{args.id.upper()}: deadline " + ("cleared." if args.clear else f"{args.date}."))
        return
    ov = projects.overview(conn)
    _json(ov) if args.json else out("\n".join(projects.lines(ov)))


def cmd_routines(conn, args):
    s = args.sub or "status"
    if s == "tick":
        done = routines.tick(conn)
        if not args.quiet:
            out("\n".join(f"{e['routine']}: {'ok' if e['ok'] else 'FAILED'} - {e['note']}" for e in done) or "Nothing due.")
    elif s == "run":
        e = routines.run_now(conn, args.name)
        out(f"{e['routine']}: {'ok' if e['ok'] else 'FAILED'} - {e['note']}")
    elif s == "mark":
        routines.mark(args.name)
        out(f"{args.name} marked done for today.")
    elif s == "install":
        out(routines.install())
    elif s == "uninstall":
        out(routines.uninstall())
    else:
        st = routines.status()
        if args.json:
            return _json(st)
        out(routines.status_line(st))
        for r in st["routines"]:
            last = f"last {r['last_run'][5:16].replace('T', ' ')} {'ok' if r['ok'] else 'FAILED'}: {r['note']}" if r["last_run"] else "not run yet"
            out(f"  {r['name']:16s} {r['schedule']:14s} next {r['next']:10s} {r.get('done_today') or last}")


# ---------------------------------------------------------------- parser

def register(sub) -> None:
    sp = sub.add_parser("sales", help="live sales watch: Shopify orders and Meta spend into the ledger, alerts")
    sp.set_defaults(fn=cmd_sales, sub="status", json=False)
    ss = sp.add_subparsers(dest="sub")
    s = ss.add_parser("watch", help="pull (read-only), record into money, alert the owner")
    s.add_argument("--quiet", action="store_true", help="record and judge, but send nothing")
    s.add_argument("--json", action="store_true")
    s = ss.add_parser("status")
    s.add_argument("--json", action="store_true")
    s = ss.add_parser("ingest", help="record orders/spend pulled through the connectors (JSON file or - for stdin)")
    s.add_argument("--file", default="-")
    s = ss.add_parser("feed", help="the spoken lines the dashboard and call read out")
    s.add_argument("--since", type=int, default=0)
    s.add_argument("-n", type=int, default=10)
    s = ss.add_parser("roas", help="today's and 7-day ROAS against the breakeven line (the dashboard tile)")
    s.add_argument("--json", action="store_true")
    s = ss.add_parser("breakeven", help="show or set the breakeven ROAS the tile is judged against")
    s.add_argument("value", nargs="?", type=float)
    s.add_argument("--note", default="", help="where the figure comes from (product, price, costs, memory id)")
    s.add_argument("--clear", action="store_true")
    ss.add_parser("setup", help="the read-only tokens live tracking needs")

    so = sub.add_parser("social", help="social media manager: calendar, drafts, comment replies, posting behind approval")
    so.set_defaults(fn=cmd_social, sub="list", status=None)
    ss = so.add_subparsers(dest="sub")
    s = ss.add_parser("plan", help="calendar slots from the active products")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--platforms", default="instagram,facebook")
    s = ss.add_parser("brief", help="write the creative-director's drafting brief")
    s.add_argument("--worker", action="store_true", help="and start a creative-director worker on it")
    s.add_argument("--task")
    s = ss.add_parser("draft", help="file a post's words")
    s.add_argument("id")
    s.add_argument("--caption")
    s.add_argument("--caption-file")
    s.add_argument("--hashtags")
    s.add_argument("--image-brief")
    s = ss.add_parser("comment", help="log a comment that needs an answer")
    s.add_argument("--platform", required=True)
    s.add_argument("--text", required=True)
    s.add_argument("--post")
    s.add_argument("--author")
    s = ss.add_parser("reply", help="draft the reply to a logged comment")
    s.add_argument("id")
    s.add_argument("--text", required=True)
    s = ss.add_parser("submit", help="ask the owner's approval before it goes out")
    s.add_argument("id")
    s.add_argument("--task")
    ss.add_parser("sync", help="pick up the owner's decisions")
    s = ss.add_parser("posted", help="mark an approved item as published")
    s.add_argument("id")
    s.add_argument("--url")
    s = ss.add_parser("list")
    s.add_argument("--status", choices=social.STATUSES)
    ss.add_parser("board", help="JSON for the dashboard")

    pj = sub.add_parser("projects", help="every open project: tasks, workers, deadlines, blockers")
    pj.set_defaults(fn=cmd_projects)
    pj.add_argument("--json", action="store_true")
    ps = pj.add_subparsers(dest="sub")
    s = ps.add_parser("deadline", help="set or clear a project's deadline")
    s.add_argument("id")
    s.add_argument("date", nargs="?")
    s.add_argument("--clear", action="store_true")

    rt = sub.add_parser("routines", help="unattended routines: sales watch, briefs, learning, worker resume")
    rt.set_defaults(fn=cmd_routines, sub=None, json=False)
    rt.add_argument("--json", action="store_true")
    rs = rt.add_subparsers(dest="sub")
    s = rs.add_parser("tick", help="run what's due (the scheduled task calls this)")
    s.add_argument("--quiet", action="store_true")
    rs.add_parser("status")
    for name in ("run", "mark"):
        s = rs.add_parser(name)
        s.add_argument("name", choices=[r["name"] for r in routines.ROUTINES])
    rs.add_parser("install", help="add the Windows scheduled task (every 15 min, hidden)")
    rs.add_parser("uninstall")


# ---------------------------------------------------------------- dashboard

def handle_get(route: str):
    """(status, data) for /api/ops/<name>, or None if the route isn't one of ours."""
    name = route.rsplit("/", 1)[-1]
    if name not in ("projects", "sales", "social", "routines", "overview", "roas"):
        return None
    if name == "social":
        return 200, social.board()
    if name == "routines":
        return 200, routines.status(check_scheduler=False)
    conn = db.connect()
    try:
        if name == "projects":
            return 200, projects.overview(conn)
        if name == "sales":
            return 200, sales_watch.status(conn)
        if name == "roas":
            return 200, sales_watch.roas(conn)
        rs = routines.status(check_scheduler=False)
        return 200, {"sales": sales_watch.status_line(conn), "routines": routines.status_line(rs),
                     "projects": projects.overview(conn)["counts"], "social": social.board()["posts"],
                     "announcements": sales_watch.announcements(limit=5)}
    finally:
        conn.close()
