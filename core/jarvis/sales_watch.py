"""Live sales watch: Shopify orders and Meta spend into the ledger, and the owner told as it happens (T101).

Cheap and local: plain HTTP to the Shopify Admin API and the Meta Marketing API, read-only, no Claude run.
`bin/jarvis routines tick` runs it every 15 minutes; `bin/jarvis sales watch` runs it by hand.

    bin/jarvis sales watch            pull, record into `bin/jarvis money`, alert
    bin/jarvis sales status           today's sales, spend and profit, the target, what is configured
    bin/jarvis sales ingest --file x  record orders/spend a Claude session pulled through its connectors
    bin/jarvis sales setup            what the two read-only tokens are and where they go
    bin/jarvis sales roas             today's and 7-day ROAS against the breakeven ROAS (the dashboard tile)
    bin/jarvis sales breakeven 1.41 --note '...'   set the breakeven ROAS line (--clear removes it)

Sources (`sales_feed` in brain/config.json: auto | tokens | connectors | off):
- tokens: brain/secrets/shopify.json {"shop": "<store>.myshopify.com", "token": "shpat_..."} (read_orders only) and
  brain/secrets/meta.json {"token": "...", "ad_account": "act_..."} (ads_read only). Plain HTTP, no Claude run.
- connectors (T103, what `auto` uses while either token is missing): a headless `claude -p` on Haiku, started
  outside the repo (no hooks, no CLAUDE.md), whose only tools are ToolSearch, the Meta ads connector's
  ads_get_ad_entities and the Shopify connector's graphql_query, both read-only. Its words are ignored: the figures
  are parsed straight out of the raw tool results in its stream, so nothing the model writes becomes a number.
- neither: `ingest` and `money add` still feed the alerts, and the dashboard says "no live data".

Alerts (each once per day per key, to Telegram, the activity log the dashboard shows, and the spoken feed in
brain/work/sales/announce.jsonl that the dashboard and the call can read out):
- every sale, and the first sale of the day;
- ad spend with no sale: today's budget gone with no sale (Blake's day rules, blake-testing section 4), no
  add-to-carts yet (Blake: day 1 zero ATC = cut), and the owner's kill rule (about A$160, two days, without a
  sale: M-261106-7b04), which is urgent (message, voice note, call);
- the day's profit against the A$380/week target, once, after `sales_summary_hour`.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from . import activity, paths, records

DEFAULTS = {
    "sales_kill_aud": 160.0,          # owner's kill rule: about 2 days of spend without a sale (M-261106-7b04)
    "sales_day_budget_usd": 50.0,     # Blake's test budget, US$50/day (blake-testing section 2)
    "sales_summary_hour": 21,
    "shopify_api_version": "2025-10",
    "meta_api_version": "v21.0",
    "meta_ad_account": "act_2023827758161105",
    "sales_feed": "auto",             # auto: the tokens when both are set, otherwise the connectors
    "sales_feed_model": "haiku",
    "sales_feed_days": 7,             # days of Meta spend re-read each pull (late attribution moves them)
    "sales_feed_timeout": 240,
    "sales_live_minutes": 45,         # a pull older than this is no longer "live" on the dashboard
    "breakeven_roas": None,           # the line the ROAS tile is judged against (`sales breakeven`)
    "breakeven_note": "",
}

FEED_TOOLS = ("mcp__claude_ai_Meta_ads__ads_get_ad_entities", "mcp__claude_ai_Shopify__graphql_query")

ORDERS_QUERY = """query($q: String!, $after: String) {
  orders(first: 100, after: $after, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes { id name createdAt test cancelledAt displayFinancialStatus
      currentTotalPriceSet { shopMoney { amount currencyCode } }
      lineItems(first: 5) { nodes { title quantity product { handle } } } }
  }
}"""


class SalesError(records.RecordError):
    pass


def cfg() -> dict:
    return {**DEFAULTS, **{k: v for k, v in paths.config().items() if k in DEFAULTS}}


def folder() -> Path:
    d = paths.work_dir() / "sales"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _secret(name: str) -> dict:
    p = paths.home() / "secrets" / f"{name}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def shopify_settings() -> dict:
    s = _secret("shopify")
    shop, token = s.get("shop") or os.environ.get("SHOPIFY_SHOP"), s.get("token") or os.environ.get("SHOPIFY_TOKEN")
    return {"shop": shop, "token": token} if shop and token else {}


def meta_settings() -> dict:
    s = _secret("meta")
    token, account = s.get("token") or os.environ.get("META_TOKEN"), s.get("ad_account") or os.environ.get("META_AD_ACCOUNT")
    if not (token and account):
        return {}
    return {"token": token, "ad_account": account if str(account).startswith("act_") else f"act_{account}"}


def _http(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # network, HTTP or JSON: the watch reports it and carries on
        raise SalesError(f"{urllib.parse.urlsplit(url).netloc}: {exc}") from exc


# ---------------------------------------------------------------- pulling (read-only)

def parse_orders(nodes: list[dict]) -> list[dict]:
    """Admin GraphQL order nodes (the token route and the Shopify connector return the same shape) as plain dicts.
    Test and cancelled orders are left out."""
    out = []
    for n in nodes or []:
        if n.get("test") or n.get("cancelledAt"):
            continue
        money = n["currentTotalPriceSet"]["shopMoney"]
        items = (n.get("lineItems") or {}).get("nodes") or []
        out.append({"id": n["id"].rsplit("/", 1)[-1], "name": n.get("name"), "created": n["createdAt"],
                    "total": float(money["amount"]), "currency": money["currencyCode"],
                    "status": n.get("displayFinancialStatus"),
                    "handles": [i["product"]["handle"] for i in items if i.get("product")],
                    "items": ", ".join(f"{i['quantity']}x {i['title']}" for i in items)})
    return out


def fetch_orders(since: datetime, http=_http) -> list[dict]:
    """Orders created since `since`, as plain dicts. Test and cancelled orders are left out."""
    s = shopify_settings()
    if not s:
        return []
    shop = s["shop"] if "." in s["shop"] else f"{s['shop']}.myshopify.com"
    url = f"https://{shop}/admin/api/{cfg()['shopify_api_version']}/graphql.json"
    q, after, out = f"created_at:>='{since.isoformat(timespec='seconds')}'", None, []
    for _ in range(10):
        body = json.dumps({"query": ORDERS_QUERY, "variables": {"q": q, "after": after}}).encode()
        res = http(url, body, {"Content-Type": "application/json", "X-Shopify-Access-Token": s["token"]})
        if res.get("errors"):
            raise SalesError(f"Shopify: {str(res['errors'])[:200]}")
        page = res["data"]["orders"]
        out += parse_orders(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    return out


def fetch_meta(day: str, http=_http) -> dict | None:
    """The ad account's spend and buying intent for one day (account time zone), or None if not configured."""
    s = meta_settings()
    if not s:
        return None
    params = urllib.parse.urlencode({"fields": "spend,account_currency,actions", "level": "account",
                                     "time_range": json.dumps({"since": day, "until": day}), "access_token": s["token"]})
    res = http(f"https://graph.facebook.com/{cfg()['meta_api_version']}/{s['ad_account']}/insights?{params}")
    if res.get("error"):
        raise SalesError(f"Meta: {res['error'].get('message', res['error'])}")
    row = (res.get("data") or [{}])[0]
    acts = {a.get("action_type"): float(a.get("value") or 0) for a in row.get("actions") or []}
    return {"day": day, "account": s["ad_account"], "spend": float(row.get("spend") or 0),
            "currency": row.get("account_currency") or "USD",
            "add_to_cart": int(acts.get("offsite_conversion.fb_pixel_add_to_cart", acts.get("add_to_cart", 0))),
            "checkouts": int(acts.get("offsite_conversion.fb_pixel_initiate_checkout", acts.get("initiate_checkout", 0))),
            "purchases": int(acts.get("offsite_conversion.fb_pixel_purchase", acts.get("purchase", 0)))}


# ---------------------------------------------------------------- the connectors route (no tokens needed)

def feed_prompt(account: str, since: str, until: str, orders_since: str) -> str:
    meta_args = {"ad_account_id": account.removeprefix("act_"), "level": "ad_account",
                 "fields": ["amount_spent", "omni_purchase", "omni_purchase_values", "omni_add_to_cart",
                            "omni_initiated_checkout"],
                 "time_range": json.dumps({"since": since, "until": until}), "time_increment": "1",
                 "include_additional_context": False, "reporting_purpose": ["metric_lookup"],
                 "advertiser_job": "report_measure_or_look_up", "scheduled_task_run": True}
    shop_args = {"query": " ".join(ORDERS_QUERY.split()), "first": 100,
                 "variables": {"q": f"created_at:>='{orders_since}'", "after": None}}
    return ("Read-only data pull for the JARVIS sales watch. Make exactly these two tool calls with exactly these "
            "arguments (load the tools with ToolSearch first if they are deferred), then reply with the one word: done. "
            "Call no other tool and change nothing.\n\n"
            f"1. mcp__claude_ai_Meta_ads__ads_get_ad_entities with {json.dumps(meta_args)}\n"
            f"2. mcp__claude_ai_Shopify__graphql_query with {json.dumps(shop_args)}")


def _claude_feed(prompt: str, timeout: int) -> str:
    """Run the headless read-only pull and return its stream-json output."""
    import subprocess
    import sys
    import tempfile
    from .dashboard import find_claude
    fake = os.environ.get("JARVIS_SALES_FEED_COMMAND")  # the tests' stand-in (core/tests/fixtures/fake_sales_feed.py)
    claude = [sys.executable, fake] if fake else find_claude()
    if not claude:
        raise SalesError("connectors: can't find the claude command")
    cmd = [*claude, "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", cfg()["sales_feed_model"],
           "--tools", "ToolSearch", "--allowedTools", ",".join(("ToolSearch",) + FEED_TOOLS), "--no-session-persistence"]
    try:  # outside the repo, so no project hooks journal it and no CLAUDE.md loads
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=tempfile.gettempdir(), stdin=subprocess.DEVNULL, creationflags=paths.NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise SalesError(f"connectors: no answer in {timeout}s") from exc
    if not r.stdout.strip():
        raise SalesError(f"connectors: claude gave nothing back ({(r.stderr or '').strip()[:160]})")
    return r.stdout


def tool_results(stream: str) -> tuple[dict[str, list[str]], list[str]]:
    """The raw result text of each tool call in a stream-json run, by tool name, and any tool used that isn't
    one of the read-only ones."""
    names, results, stray = {}, {}, []
    for line in stream.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("parent_tool_use_id"):
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                names[block.get("id")] = block.get("name")
                if block.get("name") not in FEED_TOOLS + ("ToolSearch",):
                    stray.append(block.get("name"))
            elif block.get("type") == "tool_result" and not block.get("is_error"):
                body = block.get("content")
                if isinstance(body, list):
                    body = "".join(b.get("text", "") for b in body if isinstance(b, dict))
                results.setdefault(names.get(block.get("tool_use_id"), "?"), []).append(body or "")
    return results, stray


def parse_meta_entities(text: str, account: str) -> list[dict]:
    """ads_get_ad_entities at account level, one row per day, as the watch's per-day spend dicts."""
    res = json.loads(text)
    rows = res.get("ad_entities") if isinstance(res, dict) else None
    rows = json.loads(rows) if isinstance(rows, str) else rows
    if not isinstance(rows, list):
        raise SalesError(f"Meta connector: unexpected answer {text[:160]}")

    def num(v):
        return float((v.get("value") if isinstance(v, dict) else v) or 0)

    out = []
    for r in rows:
        spent = r.get("amount_spent") or {}
        out.append({"day": r["date_start"], "account": account, "spend": num(spent),
                    "currency": (spent.get("unit") if isinstance(spent, dict) else None) or "AUD",
                    "add_to_cart": int(num(r.get("omni_add_to_cart"))), "checkouts": int(num(r.get("omni_initiated_checkout"))),
                    "purchases": int(num(r.get("omni_purchase"))), "purchase_value": num(r.get("omni_purchase_values"))})
    return out


def fetch_connectors(now: datetime, run=None) -> tuple[list[dict], list[dict], list[str]]:
    """Orders since yesterday and the last `sales_feed_days` of Meta spend, through the claude.ai connectors.
    Returns (orders, spend per day, errors); a source that failed is simply missing."""
    c = cfg()
    account = meta_settings().get("ad_account") or c["meta_ad_account"]
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    prompt = feed_prompt(account, (start - timedelta(days=int(c["sales_feed_days"]) - 1)).date().isoformat(),
                         start.date().isoformat(), (start - timedelta(days=1)).isoformat(timespec="seconds"))
    results, stray = tool_results((run or _claude_feed)(prompt, int(c["sales_feed_timeout"])))
    if stray:
        return [], [], [f"connectors: the pull used a tool it may not ({', '.join(stray)}); its figures were not used"]
    orders, days, errors = [], [], []
    try:
        texts = results.get(FEED_TOOLS[0])
        if not texts:
            raise SalesError("Meta connector: no result")
        days = parse_meta_entities(texts[-1], account)
    except (SalesError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(str(exc) if isinstance(exc, SalesError) else f"Meta connector: {exc}")
    try:
        texts = results.get(FEED_TOOLS[1])
        if not texts:
            raise SalesError("Shopify connector: no result")
        res = json.loads(texts[-1])
        if res.get("errors") or not res.get("data"):
            raise SalesError(f"Shopify connector: {str(res.get('errors') or texts[-1])[:200]}")
        orders = parse_orders(res["data"]["orders"]["nodes"])
    except (SalesError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(str(exc) if isinstance(exc, SalesError) else f"Shopify connector: {exc}")
    return orders, days, errors


def feed_mode() -> str:
    """The route the watch pulls through: tokens, connectors or off."""
    want = str(cfg()["sales_feed"]).lower()
    if want in ("tokens", "connectors", "off"):
        return want
    return "tokens" if shopify_settings() and meta_settings() else "connectors"


# ---------------------------------------------------------------- state and the spoken feed

def _state_path() -> Path:
    return folder() / "state.json"


def load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st: dict) -> None:
    _state_path().write_text(json.dumps(st, indent=1), encoding="utf-8")


def announcements(since_id: int = 0, limit: int = 20) -> list[dict]:
    """Recent spoken lines, oldest first, for the dashboard and the call to read out."""
    p = folder() / "announce.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines()[-200:]:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("id", 0) > since_id:
            rows.append(r)
    return rows[-limit:]


def _announce(st: dict, kind: str, say: str, text: str, urgent: bool) -> dict:
    st["announce_id"] = st.get("announce_id", 0) + 1
    row = {"id": st["announce_id"], "ts": paths.now(), "kind": kind, "say": say, "text": text, "urgent": urgent}
    with open(folder() / "announce.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def _notify(row: dict) -> str:
    from . import telegram
    try:
        if not telegram.settings().get("chat_id"):
            return "phone not paired"
        if row["urgent"]:
            telegram.alert(row["text"], urgent=True)
        else:
            telegram.send(row["text"])
        return "sent"
    except Exception as exc:  # a dead network must not stop the ledger being written
        return f"not sent: {exc}"


# ---------------------------------------------------------------- recording and judging

def _product(conn, handles: list[str]) -> str | None:
    for h in handles or []:
        if conn.execute("SELECT 1 FROM products WHERE slug = ?", (h,)).fetchone():
            return h
    return None


def record_orders(conn, orders: list[dict], source: str = "shopify") -> list[dict]:
    """Write orders into the ledger (one line per order, keyed by id so a re-pull never doubles). Returns new ones."""
    new = []
    for o in orders:
        ref = f"{source}:order:{o['id']}"
        # A line written by hand before the feed (ref "shopify:1001" for order #1001) is the same order.
        legacy = f"shopify:{str(o['name']).lstrip('#')}" if o.get("name") else None
        if legacy and conn.execute("SELECT 1 FROM money WHERE ref = ?", (legacy,)).fetchone():
            continue
        known = conn.execute("SELECT 1 FROM money WHERE ref = ?", (ref,)).fetchone()
        day = o.get("day") or _local_day(o.get("created"))
        product = o.get("product") or _product(conn, o.get("handles"))
        records.money_add(conn, "income", "sales", float(o["total"]), currency=o.get("currency") or "AUD",
                          aud=o.get("aud"), day=day, product=product, source=source, ref=ref,
                          note=" ".join(filter(None, [o.get("name"), o.get("items")]))[:200] or None)
        if not known:
            new.append({**o, "day": day, "product": product, "aud": records.to_aud(float(o["total"]), o.get("currency") or "AUD", o.get("aud"))})
    return new


def record_meta(conn, m: dict) -> None:
    """A day's spend as one ledger line per account and day, updated as the day's spend grows.

    Ad lines for that day written another way (by hand, by a brief) stay; the feed's line carries only the rest,
    so the day's ads total in the ledger matches what Meta reports and nothing is counted twice."""
    if not m:
        return
    ref = f"meta:{m.get('account', 'account')}:{m['day']}"
    total = records.to_aud(float(m["spend"]), m.get("currency") or "USD", m.get("aud"))
    other = conn.execute("SELECT COALESCE(SUM(amount_aud),0) FROM money WHERE kind='expense' AND category='ads' AND day = ? "
                         "AND (ref IS NULL OR ref != ?)", (m["day"], ref)).fetchone()[0]
    rest = round(max(0.0, total - other), 2)
    if not rest and not conn.execute("SELECT 1 FROM money WHERE ref = ?", (ref,)).fetchone():
        return
    note = f"ATC {m.get('add_to_cart', '?')}, checkouts {m.get('checkouts', '?')}, purchases {m.get('purchases', '?')}"
    if other:
        note += f"; Meta's day total A${total:.2f}, of which A${other:.2f} was already in other lines"
    records.money_add(conn, "expense", "ads", rest, currency="AUD", day=m["day"], source="meta", ref=ref, note=note)


def _local_day(created: str | None) -> str:
    if not created:
        return paths.now()[:10]
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone().date().isoformat()
    except ValueError:
        return created[:10]


def figures(conn, day: str) -> dict:
    """What the alerts judge, straight from the ledger (so manual `money add` lines count too)."""
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    sales_n = one("SELECT COUNT(*) FROM money WHERE kind='income' AND category='sales' AND day = ?", day)
    sales = one("SELECT COALESCE(SUM(amount_aud),0) FROM money WHERE kind='income' AND category='sales' AND day = ?", day)
    ads = one("SELECT COALESCE(SUM(amount_aud),0) FROM money WHERE kind='expense' AND category='ads' AND day = ?", day)
    last_sale = one("SELECT MAX(day) FROM money WHERE kind='income' AND category='sales' AND day <= ?", day)
    floor = (datetime.fromisoformat(day) - timedelta(days=13)).date().isoformat()
    after = max(filter(None, [last_sale, (datetime.fromisoformat(floor) - timedelta(days=1)).date().isoformat()]))
    since = one("SELECT COALESCE(SUM(amount_aud),0) FROM money WHERE kind='expense' AND category='ads' AND day > ? AND day <= ?",
                after, day)
    dry_days = one("SELECT COUNT(DISTINCT day) FROM money WHERE kind='expense' AND category='ads' AND amount_aud > 0 "
                   "AND day > ? AND day <= ?", after, day)
    monday = (datetime.fromisoformat(day) - timedelta(days=datetime.fromisoformat(day).weekday())).date().isoformat()
    today, week = records.money_report(conn, day, day), records.money_report(conn, monday, day)
    weekly = paths.config()["weekly_profit_target_aud"]
    return {"day": day, "sales_count": sales_n, "sales_aud": round(sales, 2), "ad_spend_aud": round(ads, 2),
            "last_sale_day": last_sale, "spend_since_sale_aud": round(since, 2), "days_without_sale": dry_days,
            "profit_today_aud": today["profit"], "costs_today_aud": today["total_expenses"],
            "week_start": monday, "profit_week_aud": week["profit"], "weekly_target_aud": weekly,
            "daily_target_aud": round(weekly / 7, 2), "week_gap_aud": round(week["profit"] - weekly, 2)}


def judge(conn, day: str, new_orders: list[dict], meta: dict | None, st: dict, now: datetime) -> list[dict]:
    """The alerts due now. `st['sent']` remembers what was already said today."""
    c, f = cfg(), figures(conn, day)
    sent = st.setdefault("sent", {}).setdefault(day, [])
    for d in [k for k in st["sent"] if k < (now - timedelta(days=3)).date().isoformat()]:
        st["sent"].pop(d)
    due = []

    def once(key, kind, say, text, urgent=False):
        if key not in sent:
            sent.append(key)
            due.append(_announce(st, kind, say, text, urgent))

    sales_before = f["sales_count"] - len([o for o in new_orders if o["day"] == day])
    for i, o in enumerate(o for o in new_orders if o["day"] == day):
        what = o.get("product") or o.get("items") or "an order"
        count = sales_before + i + 1
        lead = "First sale of the day, sir. " if count == 1 else "Another sale, sir. "
        say = f"{lead}A${o['aud']:.2f} for {what}." + ("" if count == 1 else f" That makes {count} today.")
        once(f"order:{o['id']}", "first_sale" if count == 1 else "sale", say,
             f"💰 {say} Today: {f['sales_count']} sales, A${f['sales_aud']:.2f} in, A${f['ad_spend_aud']:.2f} on ads.")

    budget_aud = records.to_aud(c["sales_day_budget_usd"], "USD")
    if f["spend_since_sale_aud"] >= c["sales_kill_aud"]:
        once("kill", "kill", f"Sir, A${f['spend_since_sale_aud']:.0f} has gone on ads over {f['days_without_sale']} days "
             "without a sale. That hits your kill rule.",
             f"A${f['spend_since_sale_aud']:.2f} of ad spend over {f['days_without_sale']} day(s) without a sale "
             f"(last sale: {f['last_sale_day'] or 'none'}). That hits your kill rule (about A${c['sales_kill_aud']:.0f}, two days). "
             "Blake: by day 3 there must be sales, otherwise cut and go back to research. I recommend pausing the test; "
             "say the word and I'll raise the approval.", urgent=True)
    elif f["sales_count"] == 0 and f["ad_spend_aud"] >= budget_aud:
        intent = ""
        if meta and meta.get("day") == day:
            intent = f" Meta shows {meta.get('add_to_cart', 0)} add-to-carts and {meta.get('checkouts', 0)} checkouts."
        once("day_budget", "no_sales", f"Today's ad budget is spent with no sale yet, sir.{intent}",
             f"Today's budget (A${f['ad_spend_aud']:.2f} of about A${budget_aud:.0f}) is spent with no sale.{intent} "
             "Blake's day rules: no add-to-carts on day 1 = cut; checkouts without sales = remove checkout friction, "
             "then split-test the offer; day 3 with no sales = cut. Judge intent on Shopify analytics.")
    if (meta and meta.get("day") == day and f["sales_count"] == 0 and meta.get("add_to_cart", 0) == 0
            and f["ad_spend_aud"] >= budget_aud / 2):
        once("zero_atc", "no_intent", "No add-to-carts yet today, sir, with half the budget spent.",
             f"A${f['ad_spend_aud']:.2f} spent today and Meta shows no add-to-carts. Blake: zero ATCs on day 1 means "
             "nobody cares, kill it. Check Shopify analytics before deciding.")

    activity_today = f["sales_count"] or f["costs_today_aud"]
    if now.hour >= int(c["sales_summary_hour"]) and activity_today:
        ahead = "ahead of" if f["week_gap_aud"] >= 0 else "behind"
        once("summary", "summary", f"Today's profit is A${f['profit_today_aud']:.0f}. The week stands at "
             f"A${f['profit_week_aud']:.0f} against the A${f['weekly_target_aud']:.0f} target.",
             f"📊 Today: {f['sales_count']} sales, A${f['sales_aud']:.2f} in, A${f['costs_today_aud']:.2f} out, profit "
             f"A${f['profit_today_aud']:.2f} (daily share of target A${f['daily_target_aud']:.2f}). Week from {f['week_start']}: "
             f"A${f['profit_week_aud']:.2f}, {ahead} the A${f['weekly_target_aud']:.0f} target by A${abs(f['week_gap_aud']):.2f}.")
    return due


def process(conn, orders: list[dict], meta: dict | None, *, now: datetime | None = None, notify=_notify,
            source: str = "shopify") -> dict:
    now = now or datetime.now().astimezone()
    day = now.date().isoformat()
    st = load_state()
    new = record_orders(conn, orders, source)
    record_meta(conn, meta)
    due = judge(conn, day, new, meta, st, now)
    for row in due:
        row["delivery"] = notify(row)
        activity.add(conn, "accountant", row["say"], status="done", summary=row["text"], tool="sales watch")
    save_state(st)
    return {"day": day, "new_orders": len(new), "alerts": due, "figures": figures(conn, day)}


def watch(conn, *, now: datetime | None = None, http=_http, notify=_notify, run=None) -> dict:
    """One pull-record-alert round. Errors from either source are reported, not raised."""
    now = now or datetime.now().astimezone()
    day = now.date().isoformat()
    errors, orders, meta, mode = [], [], None, feed_mode()
    live_shop = False
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)  # catches late-recorded orders
    if mode == "tokens":
        try:
            orders = fetch_orders(start, http=http)
            live_shop = bool(shopify_settings())
        except SalesError as exc:
            errors.append(str(exc))
        try:
            meta = fetch_meta(day, http=http)
        except SalesError as exc:
            errors.append(str(exc))
    elif mode == "connectors":
        try:
            orders, days, errors = fetch_connectors(now, run=run)
        except SalesError as exc:
            days, errors = [], [str(exc)]
        live_shop = not any(e.startswith(("Shopify", "connectors")) for e in errors)
        for d in days:
            if d["day"] != day:
                record_meta(conn, d)  # earlier days settle as Meta finishes attributing them
        meta = next((d for d in days if d["day"] == day), None)
        if days and not meta:  # Meta leaves out a day with nothing delivered yet: today is A$0 so far
            meta = {"day": day, "account": days[0]["account"], "spend": 0.0, "currency": days[0]["currency"],
                    "add_to_cart": 0, "checkouts": 0, "purchases": 0}
    rep = process(conn, orders, meta, now=now, notify=notify)
    rep.update(errors=errors, shopify=bool(shopify_settings()), meta=bool(meta_settings()), pulled=paths.now(),
               source=mode, live_shopify=live_shop, live_meta=meta is not None)
    (folder() / "status.json").write_text(json.dumps({k: v for k, v in rep.items() if k != "alerts"}
                                                     | {"alerts": len(rep["alerts"])}, indent=1), encoding="utf-8")
    return rep


def ingest(conn, payload: dict, *, notify=_notify) -> dict:
    """Orders and spend pulled by a Claude session through the Shopify and Meta connectors (read-only):
    {"orders": [{"id", "total", "currency", "created", "product"?, "items"?}], "meta": {"day", "spend", "currency",
    "add_to_cart"?, "checkouts"?, "purchases"?, "account"?}}"""
    if not isinstance(payload, dict):
        raise SalesError("ingest wants a JSON object with 'orders' and/or 'meta'.")
    orders = payload.get("orders") or []
    for o in orders:
        if not (isinstance(o, dict) and o.get("id") is not None and isinstance(o.get("total"), (int, float))):
            raise SalesError("each order needs an 'id' and a numeric 'total'.")
    meta = payload.get("meta")
    if meta is not None and not (isinstance(meta, dict) and meta.get("day") and isinstance(meta.get("spend"), (int, float))):
        raise SalesError("'meta' needs 'day' (YYYY-MM-DD) and a numeric 'spend'.")
    return process(conn, orders, meta, notify=notify, source=payload.get("source") or "shopify")


def status(conn) -> dict:
    try:
        last = json.loads((folder() / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = None
    return {"figures": figures(conn, paths.now()[:10]), "shopify": bool(shopify_settings()), "meta": bool(meta_settings()),
            "last_watch": last, "announcements": announcements(limit=8), "settings": cfg()}


def status_line(conn) -> str:
    f = figures(conn, paths.now()[:10])
    r = roas(conn)
    src = r["source_label"] if r["live"] else "no live source"
    return (f"Sales today: {f['sales_count']} (A${f['sales_aud']:.2f}), ads A${f['ad_spend_aud']:.2f}, profit "
            f"A${f['profit_today_aud']:.2f}; week A${f['profit_week_aud']:.2f} of A${f['weekly_target_aud']:.0f} [{src}]")


def breakeven() -> dict:
    v = cfg().get("breakeven_roas")
    try:
        v = round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        v = None
    return {"value": v, "note": cfg().get("breakeven_note") or ""}


def set_breakeven(value: float | None, note: str = "") -> dict:
    """Save the breakeven ROAS line in brain/config.json (None clears it)."""
    if value is not None and not value > 0:
        raise SalesError("The breakeven ROAS must be above zero.")
    p = paths.home() / "config.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.pop("breakeven_roas", None)
    data.pop("breakeven_note", None)
    if value is not None:
        data.update(breakeven_roas=round(float(value), 2), breakeven_note=note)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return breakeven()


def _window(conn, since: str, until: str, be: float | None) -> dict:
    def one(sql):
        return conn.execute(sql + " AND day BETWEEN ? AND ?", (since, until)).fetchone()[0]
    revenue = round(one("SELECT COALESCE(SUM(amount_aud),0) FROM money WHERE kind='income' AND category='sales'"), 2)
    orders = one("SELECT COUNT(*) FROM money WHERE kind='income' AND category='sales'")
    spend = round(one("SELECT COALESCE(SUM(amount_aud),0) FROM money WHERE kind='expense' AND category='ads'"), 2)
    value = round(revenue / spend, 2) if spend > 0 else None
    verdict = None if value is None or be is None else "above" if value >= be else "below"
    return {"since": since, "until": until, "revenue_aud": revenue, "orders": orders, "spend_aud": spend,
            "roas": value, "verdict": verdict}


def roas(conn, day: str | None = None, now: datetime | None = None) -> dict:
    """Today's and the last 7 days' ROAS (Shopify sales over Meta spend, both from the ledger) against the breakeven
    line, and whether the figures are live: both sources pulled successfully within `sales_live_minutes`."""
    now = now or datetime.now().astimezone()
    day = day or now.date().isoformat()
    be = breakeven()
    try:
        last = json.loads((folder() / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    age = None
    if last.get("pulled"):
        try:
            age = round((now - datetime.fromisoformat(last["pulled"]).astimezone(now.tzinfo)).total_seconds() / 60)
        except (ValueError, TypeError):
            age = None
    fresh = age is not None and age <= int(cfg()["sales_live_minutes"])
    live_meta, live_shop = bool(last.get("live_meta")), bool(last.get("live_shopify"))
    live = fresh and live_meta and live_shop
    if not last.get("pulled"):
        why = "The sales watch hasn't pulled yet."
    elif not fresh:
        why = f"Last pull {age} min ago." if age is not None else "Last pull time unknown."
    elif not live:
        missing = " or ".join(n for n, ok in (("Meta", live_meta), ("Shopify", live_shop)) if not ok)
        why = f"No live data from {missing}: " + ("; ".join(last.get("errors") or []) or "no source set up")
    else:
        why = ""
    week_start = (datetime.fromisoformat(day) - timedelta(days=6)).date().isoformat()
    return {"day": day, "today": _window(conn, day, day, be["value"]), "week": _window(conn, week_start, day, be["value"]),
            "breakeven": be["value"], "breakeven_note": be["note"], "live": live, "why_not_live": why,
            "source": last.get("source"),
            "source_label": {"tokens": "Shopify + Meta tokens", "connectors": "Shopify + Meta connectors"}.get(last.get("source"), ""),
            "pulled": last.get("pulled"), "age_minutes": age, "errors": last.get("errors") or []}


def roas_line(conn) -> str:
    r = roas(conn)

    def fmt(w):
        x = f"{w['roas']:.2f}x" if w["roas"] is not None else "n/a (no spend)"
        return f"{x} (A${w['revenue_aud']:.2f} sales / A${w['spend_aud']:.2f} ads)"
    be = f"breakeven {r['breakeven']:.2f}x" if r["breakeven"] else "breakeven not set"
    return f"ROAS today {fmt(r['today'])}, 7 days {fmt(r['week'])}; {be} [{'live' if r['live'] else 'NO LIVE DATA'}]"


SETUP = """Live sales tracking works without any token: while either is missing, the 15-minute routine pulls through
the claude.ai Shopify and Meta connectors (a headless Haiku run with read-only tools, T103). The two read-only tokens
are the cheaper route (plain HTTP, no Claude usage); once both are in, the watch uses them instead.

1. Shopify (orders): Shopify admin > Settings > Apps and sales channels > Develop apps > Create an app
   ("JARVIS read-only") > Configure Admin API scopes: read_orders (and read_products) only > Install >
   copy the Admin API access token (shpat_...). Save brain/secrets/shopify.json:
   {"shop": "<store>.myshopify.com", "token": "shpat_..."}
2. Meta (spend): Business settings > Users > System users > add one (Employee) > Assign assets: the ad account
   act_2023827758161105, "View performance" > Generate new token with ads_read only. Save brain/secrets/meta.json:
   {"token": "...", "ad_account": "act_2023827758161105"}

brain/ is git-ignored, so the tokens never reach the public repo. Then `bin/jarvis sales watch` to test, and
`bin/jarvis routines install` runs it every 15 minutes."""
