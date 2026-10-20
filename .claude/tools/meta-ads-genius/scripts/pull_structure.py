#!/usr/bin/env python3
"""Pull the live account structure (campaigns > ad sets > ads) to JSON. Stdlib only.

Usage:
  python3 pull_structure.py [--out ./data/raw] [--include-paused]

Env vars: META_ACCESS_TOKEN, META_AD_ACCOUNT_ID (act_...), optional META_API_VERSION (default v23.0).
Output: YYYY-MM-DD_structure.json — includes statuses, budgets, objectives,
optimization goals, learning-stage info (when the API exposes it), and targeting summaries.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com"

CAMPAIGN_FIELDS = "id,name,status,effective_status,objective,buying_type,daily_budget,lifetime_budget,bid_strategy,start_time,stop_time,special_ad_categories"
ADSET_FIELDS = "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,optimization_goal,billing_event,bid_strategy,bid_amount,learning_stage_info,targeting,promoted_object,attribution_spec,start_time,end_time"
ADSET_FIELDS_FALLBACK = "id,name,campaign_id,status,effective_status,daily_budget,lifetime_budget,optimization_goal,billing_event,targeting,start_time,end_time"
AD_FIELDS = "id,name,adset_id,status,effective_status,creative{id,object_story_spec}"


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
            transient = e.code in (500, 502, 503) or '"code":17' in body or '"code":32' in body
            if transient and attempt < retries - 1:
                time.sleep(15 * (2 ** attempt))
                continue
            raise RuntimeError(f"Graph API {e.code}: {body}") from None
    raise RuntimeError("retries exhausted")


def paged(base, params):
    url = f"{base}?{urllib.parse.urlencode(params)}"
    out = []
    while url:
        data = get(url)
        out.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="./data/raw")
    p.add_argument("--include-paused", action="store_true", help="include paused/archived entities")
    args = p.parse_args()

    token = env("META_ACCESS_TOKEN")
    account = env("META_AD_ACCOUNT_ID")
    if not account.startswith("act_"):
        account = f"act_{account}"
    version = env("META_API_VERSION", required=False, default="v23.0")
    base = f"{GRAPH}/{version}/{account}"

    common = {"access_token": token, "limit": "200"}
    if not args.include_paused:
        common["filtering"] = json.dumps([{"field": "effective_status", "operator": "IN",
                                           "value": ["ACTIVE", "LEARNING", "LEARNING_LIMITED"]}])

    print("campaigns ...", file=sys.stderr)
    campaigns = paged(f"{base}/campaigns", {**common, "fields": CAMPAIGN_FIELDS})

    print("ad sets ...", file=sys.stderr)
    try:
        adsets = paged(f"{base}/adsets", {**common, "fields": ADSET_FIELDS})
    except RuntimeError as e:
        print(f"  full field set rejected ({e}); retrying with fallback fields", file=sys.stderr)
        adsets = paged(f"{base}/adsets", {**common, "fields": ADSET_FIELDS_FALLBACK})

    print("ads ...", file=sys.stderr)
    ads = paged(f"{base}/ads", {**common, "fields": AD_FIELDS})

    by_campaign = {c["id"]: {**c, "adsets": []} for c in campaigns}
    by_adset = {a["id"]: {**a, "ads": []} for a in adsets}
    for a in by_adset.values():
        if a.get("campaign_id") in by_campaign:
            by_campaign[a["campaign_id"]]["adsets"].append(a)
    for ad in ads:
        if ad.get("adset_id") in by_adset:
            by_adset[ad["adset_id"]]["ads"].append(ad)

    result = {
        "pulled_at": dt.datetime.now().isoformat(timespec="seconds"),
        "account": account,
        "api_version": version,
        "filter": "active-only" if not args.include_paused else "all",
        "counts": {"campaigns": len(campaigns), "adsets": len(adsets), "ads": len(ads)},
        "campaigns": list(by_campaign.values()),
    }

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"{dt.date.today().isoformat()}_structure.json")
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    print(path)


if __name__ == "__main__":
    main()
