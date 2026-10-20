#!/usr/bin/env python3
"""Pull Meta Ads insights to CSV. Stdlib only — no pip installs.

Usage:
  python3 pull_insights.py --level adset --last 7
  python3 pull_insights.py --level ad --since 2026-08-01 --until 2026-08-24 --daily
  python3 pull_insights.py --level campaign --last 28 --breakdowns publisher_platform,platform_position

Env vars (required):
  META_ACCESS_TOKEN     Graph API token with ads_read scope
  META_AD_ACCOUNT_ID    e.g. act_1234567890 (bare number also accepted)
Optional:
  META_API_VERSION      default v23.0

Output: CSV to --out dir (default ./data/raw) named
  YYYY-MM-DD_<level>_<since>-to-<until>[_daily][_<breakdowns>].csv
Action-array fields (actions, action_values, cost_per_action_type) are
flattened to columns: act__<type>, val__<type>, cpa__<type>.
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com"

BASE_FIELDS = [
    "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
    "impressions", "reach", "frequency", "spend", "cpm",
    "clicks", "ctr", "inline_link_clicks", "inline_link_click_ctr",
    "actions", "action_values", "cost_per_action_type",
    "purchase_roas", "video_thruplay_watched_actions",
    "date_start", "date_stop",
]

LEVEL_FIELD_PREFIXES = {
    "campaign": ("campaign_id", "campaign_name"),
    "adset": ("campaign_id", "campaign_name", "adset_id", "adset_name"),
    "ad": ("campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name"),
}


def env(name, required=True, default=None):
    v = os.environ.get(name, default)
    if required and not v:
        sys.exit(f"ERROR: {name} is not set. See meta-ads-genius/README.md for setup.")
    return v


def get(url, retries=4):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            transient = e.code in (500, 502, 503) or '"code":17' in body or '"code":32' in body or '"code":613' in body
            if transient and attempt < retries - 1:
                wait = 15 * (2 ** attempt)
                print(f"  rate-limited/transient ({e.code}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            sys.exit(f"ERROR: Graph API {e.code}\n{body}")
    sys.exit("ERROR: retries exhausted")


def flatten(row):
    out = {}
    for k, v in row.items():
        if k == "actions" and isinstance(v, list):
            for a in v:
                out[f"act__{a['action_type']}"] = a.get("value")
        elif k == "action_values" and isinstance(v, list):
            for a in v:
                out[f"val__{a['action_type']}"] = a.get("value")
        elif k == "cost_per_action_type" and isinstance(v, list):
            for a in v:
                out[f"cpa__{a['action_type']}"] = a.get("value")
        elif k == "purchase_roas" and isinstance(v, list):
            for a in v:
                out[f"roas__{a['action_type']}"] = a.get("value")
        elif k == "video_thruplay_watched_actions" and isinstance(v, list):
            for a in v:
                out["thruplays"] = a.get("value")
        elif isinstance(v, (dict, list)):
            out[k] = json.dumps(v)
        else:
            out[k] = v
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--level", choices=["campaign", "adset", "ad"], default="adset")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--last", type=int, help="last N full days (yesterday inclusive)")
    p.add_argument("--daily", action="store_true", help="time_increment=1 for time-series (marginal analysis)")
    p.add_argument("--breakdowns", help="e.g. publisher_platform,platform_position or age,gender")
    p.add_argument("--out", default="./data/raw")
    args = p.parse_args()

    token = env("META_ACCESS_TOKEN")
    account = env("META_AD_ACCOUNT_ID")
    if not account.startswith("act_"):
        account = f"act_{account}"
    version = env("META_API_VERSION", required=False, default="v23.0")

    if args.last:
        until = dt.date.today() - dt.timedelta(days=1)
        since = until - dt.timedelta(days=args.last - 1)
    elif args.since and args.until:
        since = dt.date.fromisoformat(args.since)
        until = dt.date.fromisoformat(args.until)
    else:
        sys.exit("ERROR: pass --last N, or both --since and --until (YYYY-MM-DD).")

    fields = [f for f in BASE_FIELDS if f in LEVEL_FIELD_PREFIXES[args.level] or f not in
              ("campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name")]

    params = {
        "level": args.level,
        "fields": ",".join(fields),
        "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
        "limit": "300",
        "access_token": token,
    }
    if args.daily:
        params["time_increment"] = "1"
    if args.breakdowns:
        params["breakdowns"] = args.breakdowns

    url = f"{GRAPH}/{version}/{account}/insights?{urllib.parse.urlencode(params)}"
    rows = []
    page = 1
    while url:
        print(f"page {page} ...", file=sys.stderr)
        data = get(url)
        rows.extend(flatten(r) for r in data.get("data", []))
        url = data.get("paging", {}).get("next")
        page += 1

    if not rows:
        print("No rows returned (no delivery in window, or wrong account id).", file=sys.stderr)

    headers = sorted({k for r in rows for k in r}, key=lambda k: (k.startswith(("act__", "val__", "cpa__", "roas__")), k))
    os.makedirs(args.out, exist_ok=True)
    suffix = "_daily" if args.daily else ""
    if args.breakdowns:
        suffix += "_" + args.breakdowns.replace(",", "-")
    name = f"{dt.date.today().isoformat()}_{args.level}_{since.isoformat()}-to-{until.isoformat()}{suffix}.csv"
    path = os.path.join(args.out, name)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        w.writerows(rows)
    print(path)


if __name__ == "__main__":
    main()
