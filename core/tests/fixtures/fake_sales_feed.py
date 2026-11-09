"""Stands in for the sales watch's connector pull (`claude -p <prompt> --output-format stream-json ...`, T103).

Prints the stream a real run gives: the two read-only tool calls and their raw results. The Meta rows cover the
prompt's date range (A$20 on its last day, A$10 the day before); the Shopify result has one A$50 order made at noon on that last day.
FAKE_FEED_MODE=stray adds a call to a tool the pull may not use; FAKE_FEED_MODE=shopify_error fails Shopify.
"""

import json
import os
import re
import sys
from datetime import datetime

prompt = sys.argv[sys.argv.index("-p") + 1]
rng = re.search(r'\\"since\\": \\"(\d{4}-\d\d-\d\d)\\", \\"until\\": \\"(\d{4}-\d\d-\d\d)\\"', prompt)
since, until = rng.groups()
mode = os.environ.get("FAKE_FEED_MODE", "")


def say(ev):
    print(json.dumps(ev), flush=True)


def call(i, name, result, error=False):
    say({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": name, "input": {}}]}})
    say({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "is_error": error,
                                                  "content": [{"type": "text", "text": result}]}]}})


say({"type": "system", "subtype": "init"})
rows = [{"date_start": d, "date_stop": d, "amount_spent": {"value": v, "unit": "AUD"}, "omni_purchase": None,
         "omni_purchase_values": None, "omni_add_to_cart": "1" if d == until else None, "omni_initiated_checkout": None}
        for d, v in ((since, "0"), (until, "20"))]
if since != until:
    prev = datetime.fromisoformat(until).toordinal() - 1
    rows.insert(1, dict(rows[0], date_start=datetime.fromordinal(prev).date().isoformat(), amount_spent={"value": "10", "unit": "AUD"}))
call(1, "mcp__claude_ai_Meta_ads__ads_get_ad_entities", json.dumps({"ad_entities": json.dumps(rows)}))
if mode == "shopify_error":
    call(2, "mcp__claude_ai_Shopify__graphql_query", json.dumps({"errors": [{"message": "Access denied"}]}))
else:
    node = {"id": "gid://shopify/Order/777", "name": "#1777", "createdAt": f"{until}T12:00:00",  # local noon that day
            "test": False, "cancelledAt": None, "displayFinancialStatus": "PAID",
            "currentTotalPriceSet": {"shopMoney": {"amount": "50.00", "currencyCode": "AUD"}},
            "lineItems": {"nodes": [{"title": "Brush", "quantity": 1, "product": {"handle": "brush"}}]}}
    call(2, "mcp__claude_ai_Shopify__graphql_query",
         json.dumps({"data": {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [node]}}}))
if mode == "stray":
    call(3, "mcp__claude_ai_Meta_ads__ads_update_entity", "{}")
say({"type": "result", "subtype": "success", "is_error": False, "result": "done"})
