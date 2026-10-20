#!/usr/bin/env bash
# Smoke-test Meta API credentials. Reads META_ACCESS_TOKEN (+ optional META_AD_ACCOUNT_ID).
# Never writes tokens to disk.
set -euo pipefail

VERSION="${META_API_VERSION:-v23.0}"

if [[ -z "${META_ACCESS_TOKEN:-}" ]]; then
  echo "ERROR: META_ACCESS_TOKEN is not set." >&2
  echo "Create a token in a Meta Business App (Marketing API product) with scopes:" >&2
  echo "  ads_read  (analysis only)  |  + ads_management (only if Claude will execute changes)" >&2
  exit 1
fi

echo "== Accessible ad accounts =="
curl -sS "https://graph.facebook.com/${VERSION}/me/adaccounts?fields=name,account_id,account_status&access_token=${META_ACCESS_TOKEN}" | python3 -m json.tool

echo
echo "== Token debug (expiry & scopes) =="
curl -sS "https://graph.facebook.com/${VERSION}/debug_token?input_token=${META_ACCESS_TOKEN}&access_token=${META_ACCESS_TOKEN}" | python3 -m json.tool

if [[ -n "${META_AD_ACCOUNT_ID:-}" ]]; then
  ACCT="${META_AD_ACCOUNT_ID}"
  [[ "${ACCT}" == act_* ]] || ACCT="act_${ACCT}"
  echo
  echo "== Target account check: ${ACCT} =="
  curl -sS "https://graph.facebook.com/${VERSION}/${ACCT}?fields=name,account_status,currency,timezone_name,amount_spent&access_token=${META_ACCESS_TOKEN}" | python3 -m json.tool
fi
