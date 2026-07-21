#!/usr/bin/env bash
#
# test_api.sh - End-to-end curl test suite for the LeadBoost API.
#
# Exercises every endpoint (health, auth, organizations, billing, leads,
# analytics) against a running instance, using real company URLs so the
# scraper / LangGraph pipeline are tested against real-world sites, not
# mocks.
#
# USAGE
#   chmod +x test_api.sh
#   ./test_api.sh                              # against http://localhost:8000
#   BASE_URL=https://api.example.com ./test_api.sh
#   ./test_api.sh --base-url https://api.example.com
#
# REQUIREMENTS
#   - curl
#   - jq        (brew install jq | apt-get install jq | choco install jq)
#   - The API must already be running (e.g. `uvicorn main:app --reload`
#     from backend/, or however you deploy it) and reachable at BASE_URL.
#
# WHAT IT DOES
#   1. Health checks              (/health, /live, /ready)
#   2. Auth                       (register, login, /me)
#   3. Organizations              (get, update)
#   4. Billing                    (plans, usage, upgrade to unlock AI features)
#   5. Leads                      (bulk-create from real company URLs,
#                                   single-create, list, get, update,
#                                   manual /process trigger, poll until the
#                                   LangGraph pipeline finishes, delete)
#   6. Analytics                  (pipeline-metrics, evaluation-metrics)
#
# Exit code is 0 if every check passed, 1 otherwise -- safe to wire into CI.
#
set -uo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL="${BASE_URL:-http://localhost:8000}"

while [ $# -gt 0 ]; do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2 ;;
    --base-url=*) BASE_URL="${1#*=}"; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^#//'
      exit 0
      ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

API="${BASE_URL%/}/api/v2"
TIMESTAMP=$(date +%s)
TEST_EMAIL="apitest_${TIMESTAMP}@example.com"
TEST_PASSWORD="TestPass123!"

# Real company websites. Chosen because they're stable, public, and have
# reasonably clean structured data (JSON-LD/meta) so the scraper's fast
# tiers can usually succeed -- good for validating the pipeline end-to-end.
# Override with your own targets:
#   LEAD_URLS="https://a.com,https://b.com" SINGLE_LEAD_URL="https://c.com" ./test_api.sh
if [ -n "${LEAD_URLS:-}" ]; then
  IFS=',' read -ra COMPANY_URLS <<< "$LEAD_URLS"
else
  COMPANY_URLS=(
    "https://stripe.com"
    "https://github.com"
    "https://www.notion.so"
    "https://openai.com"
    "https://vercel.com"
  )
fi
SINGLE_LEAD_URL="${SINGLE_LEAD_URL:-https://www.anthropic.com}"

# How long to wait for the background LangGraph pipeline to finish
# processing a lead before giving up on the "poll until processed" check.
POLL_MAX_ATTEMPTS=30
POLL_INTERVAL_SECONDS=2

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: this script requires 'jq' (a JSON parser)." >&2
  echo "  macOS:          brew install jq" >&2
  echo "  Debian/Ubuntu:  sudo apt-get install jq" >&2
  echo "  Windows:        choco install jq" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: this script requires 'curl'." >&2
  exit 1
fi

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
PASS_COUNT=0
FAIL_COUNT=0
AUTH_HEADER=""
TMP_RESP="$(mktemp)"
trap 'rm -f "$TMP_RESP"' EXIT

section() { echo -e "\n${BLUE}${BOLD}== $1 ==${NC}"; }
info()    { echo -e "  ${YELLOW}i${NC}  $1"; }

# do_request METHOD PATH [JSON_BODY]
# Response body is written to $TMP_RESP; the HTTP status code is echoed to
# stdout so callers do: STATUS=$(do_request ...); BODY=$(cat "$TMP_RESP")
do_request() {
  local method="$1" path="$2" data="${3:-}"
  local url="${API}${path}"
  local -a args=(-s -o "$TMP_RESP" -w "%{http_code}" -X "$method" "$url")
  if [ -n "$AUTH_HEADER" ]; then
    args+=(-H "Authorization: Bearer ${AUTH_HEADER}")
  fi
  if [ -n "$data" ]; then
    args+=(-H "Content-Type: application/json" -d "$data")
  fi
  curl "${args[@]}"
}

# do_form_request METHOD PATH "field1=val1&field2=val2"
# Same as do_request but sends application/x-www-form-urlencoded (needed
# for the OAuth2-password-flow /login endpoint).
do_form_request() {
  local method="$1" path="$2" data="$3"
  local url="${API}${path}"
  curl -s -o "$TMP_RESP" -w "%{http_code}" -X "$method" "$url" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "$data"
}

# check DESCRIPTION EXPECTED_STATUS ACTUAL_STATUS
check() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    echo -e "  ${GREEN}✔ PASS${NC}  $desc ${GREEN}(HTTP $actual)${NC}"
    PASS_COUNT=$((PASS_COUNT + 1))
    return 0
  else
    echo -e "  ${RED}✘ FAIL${NC}  $desc ${RED}(expected $expected, got $actual)${NC}"
    echo "         Response: $(cat "$TMP_RESP" | head -c 300)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    return 1
  fi
}

# check_one_of DESCRIPTION ACTUAL_STATUS EXPECTED1 EXPECTED2 ...
check_one_of() {
  local desc="$1" actual="$2"; shift 2
  local exp
  for exp in "$@"; do
    if [ "$actual" = "$exp" ]; then
      echo -e "  ${GREEN}✔ PASS${NC}  $desc ${GREEN}(HTTP $actual)${NC}"
      PASS_COUNT=$((PASS_COUNT + 1))
      return 0
    fi
  done
  echo -e "  ${RED}✘ FAIL${NC}  $desc ${RED}(expected one of: $*, got $actual)${NC}"
  echo "         Response: $(cat "$TMP_RESP" | head -c 300)"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  return 1
}

echo -e "${BOLD}LeadBoost API test suite${NC}"
echo "Target: $BASE_URL"
echo "Test user: $TEST_EMAIL"

# ---------------------------------------------------------------------------
# 1. Health checks
# ---------------------------------------------------------------------------
section "1. Health checks"

STATUS=$(curl -s -o "$TMP_RESP" -w "%{http_code}" "${BASE_URL%/}/live")
check "GET /live" 200 "$STATUS"

STATUS=$(curl -s -o "$TMP_RESP" -w "%{http_code}" "${BASE_URL%/}/ready")
check_one_of "GET /ready" "$STATUS" 200 503
[ "$STATUS" = "503" ] && info "Not fully ready yet (often Redis being unavailable in dev) - continuing anyway"

STATUS=$(curl -s -o "$TMP_RESP" -w "%{http_code}" "${BASE_URL%/}/health")
check_one_of "GET /health" "$STATUS" 200 503
cat "$TMP_RESP" | jq '.' 2>/dev/null | sed 's/^/         /' || true

# ---------------------------------------------------------------------------
# 2. Auth
# ---------------------------------------------------------------------------
section "2. Auth"

STATUS=$(do_request POST "/register" "$(jq -n \
  --arg email "$TEST_EMAIL" --arg password "$TEST_PASSWORD" \
  '{email: $email, password: $password, first_name: "API", last_name: "Tester"}')")
check "POST /register" 200 "$STATUS"
USER_ID=$(jq -r '.id // empty' "$TMP_RESP")
ORG_ID=$(jq -r '.organization_id // empty' "$TMP_RESP")
info "Created user_id=$USER_ID in organization_id=$ORG_ID"

STATUS=$(do_form_request POST "/login" "username=${TEST_EMAIL}&password=${TEST_PASSWORD}")
check "POST /login" 200 "$STATUS"
AUTH_HEADER=$(jq -r '.access_token // empty' "$TMP_RESP")
if [ -z "$AUTH_HEADER" ]; then
  echo -e "${RED}Could not obtain an access token - aborting remaining tests.${NC}"
  exit 1
fi
info "Access token acquired"

STATUS=$(do_request GET "/me")
check "GET /me" 200 "$STATUS"
jq '{id, email, organization_id}' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

# ---------------------------------------------------------------------------
# 3. Organizations
# ---------------------------------------------------------------------------
section "3. Organizations"

STATUS=$(do_request GET "/organizations/")
check "GET /organizations/ (current org)" 200 "$STATUS"
jq '{id, name, plan_tier}' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

STATUS=$(do_request GET "/organizations/${ORG_ID}")
check "GET /organizations/{id}" 200 "$STATUS"

STATUS=$(do_request PUT "/organizations/${ORG_ID}" '{"description": "Updated via API test script"}')
check "PUT /organizations/{id}" 200 "$STATUS"

# ---------------------------------------------------------------------------
# 4. Billing
# ---------------------------------------------------------------------------
section "4. Billing"

STATUS=$(do_request GET "/plans")
check "GET /plans" 200 "$STATUS"
jq -c '.[] | {name, max_leads_per_day, can_use_ai}' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

STATUS=$(do_request GET "/usage")
check "GET /usage" 200 "$STATUS"
jq '.' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

# Upgrade to "pro" so AI-enhanced pipeline stages (enrichment, company
# intelligence, decision reasoning, messaging) actually run for the lead
# tests below, and so POST /leads/{id}/process (which requires AI features)
# succeeds. This is a self-service dev/test upgrade with no billing
# provider involved (see core/infrastructure/billing/subscription_service.py).
STATUS=$(do_request POST "/upgrade?plan_name=pro")
check "POST /upgrade?plan_name=pro" 200 "$STATUS"

STATUS=$(do_request GET "/usage")
check "GET /usage (after upgrade)" 200 "$STATUS"
jq '{plan_name, remaining_daily_leads}' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

# ---------------------------------------------------------------------------
# 5. Leads (real company URLs)
# ---------------------------------------------------------------------------
section "5. Leads - bulk create from real company URLs"

URLS_JSON=$(printf '%s\n' "${COMPANY_URLS[@]}" | jq -R . | jq -s .)
STATUS=$(do_request POST "/leads/" "$(jq -n --argjson urls "$URLS_JSON" \
  '{urls: $urls, message_style: "professional"}')")
check "POST /leads/ (bulk create, ${#COMPANY_URLS[@]} URLs)" 200 "$STATUS"
BULK_LEAD_IDS=$(jq -r '.[].id' "$TMP_RESP" 2>/dev/null)
echo "         Created/matched lead IDs: $(echo $BULK_LEAD_IDS | tr '\n' ' ')"

section "5b. Leads - single create"

STATUS=$(do_request POST "/leads/single" "$(jq -n \
  --arg website "$SINGLE_LEAD_URL" --argjson org "$ORG_ID" --argjson owner "$USER_ID" \
  '{website: $website, organization_id: $org, owner_id: $owner}')")
check "POST /leads/single" 200 "$STATUS"
LEAD_ID=$(jq -r '.id // empty' "$TMP_RESP")
info "Primary test lead_id=$LEAD_ID ($SINGLE_LEAD_URL)"

section "5c. Leads - list / get / update"

STATUS=$(do_request GET "/leads/?skip=0&limit=50")
check "GET /leads/ (list)" 200 "$STATUS"
LEAD_COUNT=$(jq 'length' "$TMP_RESP" 2>/dev/null)
info "Organization currently has $LEAD_COUNT lead(s)"

if [ -n "$LEAD_ID" ] && [ "$LEAD_ID" != "null" ]; then
  STATUS=$(do_request GET "/leads/${LEAD_ID}")
  check "GET /leads/{id}" 200 "$STATUS"

  STATUS=$(do_request PUT "/leads/${LEAD_ID}" '{"contact_name": "Manually Edited Contact"}')
  check "PUT /leads/{id}" 200 "$STATUS"

  section "5d. Leads - manual /process trigger + poll for pipeline completion"

  STATUS=$(do_request POST "/leads/${LEAD_ID}/process")
  check_one_of "POST /leads/{id}/process" "$STATUS" 200 403
  if [ "$STATUS" = "403" ]; then
    info "AI features not enabled on this org's plan (check CAN_USE_AI_PRO on the server) - skipping poll"
  else
    info "Polling GET /leads/${LEAD_ID} until the LangGraph pipeline finishes (max $((POLL_MAX_ATTEMPTS * POLL_INTERVAL_SECONDS))s)..."
    attempt=0
    while [ $attempt -lt $POLL_MAX_ATTEMPTS ]; do
      STATUS=$(do_request GET "/leads/${LEAD_ID}")
      QUALIFICATION=$(jq -r '.qualification_label // empty' "$TMP_RESP")
      if [ -n "$QUALIFICATION" ] && [ "$QUALIFICATION" != "null" ] && [ "$QUALIFICATION" != "Low Priority" ]; then
        echo -e "  ${GREEN}✔ PASS${NC}  Pipeline finished processing lead $LEAD_ID"
        PASS_COUNT=$((PASS_COUNT + 1))
        jq '{company_name, industry, score, qualification_label, scrape_source, scrape_confidence, outreach_message}' "$TMP_RESP" | sed 's/^/         /'
        break
      fi
      attempt=$((attempt + 1))
      sleep "$POLL_INTERVAL_SECONDS"
    done
    if [ $attempt -ge $POLL_MAX_ATTEMPTS ]; then
      echo -e "  ${RED}✘ FAIL${NC}  Pipeline did not finish within the timeout"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
  fi

  section "5e. Leads - delete"
  STATUS=$(do_request DELETE "/leads/${LEAD_ID}")
  check "DELETE /leads/{id}" 200 "$STATUS"
else
  echo -e "  ${YELLOW}Skipping single-lead detail tests - no lead_id available${NC}"
fi

# ---------------------------------------------------------------------------
# 6. Analytics
# ---------------------------------------------------------------------------
section "6. Analytics"

STATUS=$(do_request GET "/analytics/pipeline-metrics")
check "GET /analytics/pipeline-metrics" 200 "$STATUS"
jq '.' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

STATUS=$(do_request GET "/analytics/pipeline-metrics?hours=24")
check "GET /analytics/pipeline-metrics?hours=24" 200 "$STATUS"

STATUS=$(do_request GET "/analytics/evaluation-metrics")
check "GET /analytics/evaluation-metrics" 200 "$STATUS"
jq '.' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
section "Summary"
TOTAL=$((PASS_COUNT + FAIL_COUNT))
echo -e "  ${GREEN}Passed: $PASS_COUNT${NC} / $TOTAL"
if [ "$FAIL_COUNT" -gt 0 ]; then
  echo -e "  ${RED}Failed: $FAIL_COUNT${NC} / $TOTAL"
  exit 1
fi
echo -e "  ${GREEN}${BOLD}All checks passed.${NC}"
exit 0
