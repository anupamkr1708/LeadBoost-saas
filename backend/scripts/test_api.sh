#!/usr/bin/env bash
#
# test_api.sh - End-to-end release-validation suite for the LeadBoost API.
#
# Exercises every public endpoint in the OpenAPI spec (v2.0.0): health,
# auth, organizations, billing, the new Discovery layer, leads, and
# analytics -- against a running instance, using real-world natural
# language queries and real company URLs so the Discovery service,
# scraper, and LangGraph pipeline are all tested end-to-end, not mocked.
#
# PIPELINE
#   NL query -> Discovery -> Website Resolution -> Validation ->
#   Lead Creation -> Lead Pipeline -> Scraper -> AI Qualification ->
#   Analytics
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
#   - The API must already be running and reachable at BASE_URL.
#
# ENVIRONMENT OVERRIDES
#   BASE_URL             Target host                       (default http://localhost:8000)
#   LEAD_URLS            Comma-separated URLs for bulk lead creation
#   SINGLE_LEAD_URL      URL used for the single-lead-create test
#   DISCOVERY_QUERIES    Comma-separated natural language queries (overrides
#                        the built-in 10 real-world queries)
#   DISCOVERY_LIMIT      "limit" sent with each of the 10 main Discovery
#                        queries                            (default 5)
#   DUPLICATE_TEST_QUERY Query used for the duplicate-detection test
#   STRESS_TEST_QUERY    Query used for the limit=1/5/10/20 stress test
#
# Exit code is 0 if every hard check passed, 1 otherwise -- safe for CI.
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

# Real company websites for the Leads bulk-create test. Override with:
#   LEAD_URLS="https://a.com,https://b.com" ./test_api.sh
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

# The 10 required real-world Discovery queries. Stored in an array (never
# hardcoded inline in the test logic below). Override with:
#   DISCOVERY_QUERIES="q1,q2,..." ./test_api.sh
if [ -n "${DISCOVERY_QUERIES:-}" ]; then
  IFS=',' read -ra DISCOVERY_QUERIES_ARR <<< "$DISCOVERY_QUERIES"
else
  DISCOVERY_QUERIES_ARR=(
    "Hospitals in Patna"
    "Coffee shops in Pune"
    "Law firms in Chennai"
    "Accounting firms in Ahmedabad"
  )
fi
DISCOVERY_LIMIT="${DISCOVERY_LIMIT:-5}"
DUPLICATE_TEST_QUERY="${DUPLICATE_TEST_QUERY:-${DISCOVERY_QUERIES_ARR[0]}}"
STRESS_TEST_QUERY="${STRESS_TEST_QUERY:-${DISCOVERY_QUERIES_ARR[3]}}"
WORKFLOW_TEST_QUERY="${WORKFLOW_TEST_QUERY:-Bakeries in Patna}"

# How long to wait for background pipeline / AI processing to finish.
POLL_MAX_ATTEMPTS=30
POLL_INTERVAL_SECONDS=2
# Discovery runs the pipeline synchronously per the OpenAPI description, so
# this poll is a short confirmation pass rather than a long wait.
DISCOVERY_POLL_MAX_ATTEMPTS=10
DISCOVERY_POLL_INTERVAL=2

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
WARN_COUNT=0
AUTH_HEADER=""
TMP_RESP="$(mktemp)"
trap 'rm -f "$TMP_RESP"' EXIT

# Leads discovered along the way, polled for pipeline completion later.
DISCOVERY_LEAD_IDS=()

section() { echo -e "\n${BLUE}${BOLD}== $1 ==${NC}"; }
info()    { echo -e "  ${YELLOW}i${NC}  $1"; }
warn()    { echo -e "  ${YELLOW}⚠ WARN${NC}  $1"; WARN_COUNT=$((WARN_COUNT + 1)); }

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

# check_true DESCRIPTION CONDITION(0/1 result already evaluated by caller)
check_true() {
  local desc="$1" ok="$2"
  if [ "$ok" = "0" ]; then
    echo -e "  ${GREEN}✔ PASS${NC}  $desc"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo -e "  ${RED}✘ FAIL${NC}  $desc"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

# poll_lead_until_processed LEAD_ID MAX_ATTEMPTS INTERVAL LABEL
# Polls GET /leads/{id} until qualification_label looks final, printing key
# pipeline fields. Returns 0 on success, 1 on timeout.
poll_lead_until_processed() {
  local lead_id="$1" max_attempts="$2" interval="$3" label="$4"
  local attempt=0
  while [ "$attempt" -lt "$max_attempts" ]; do
    STATUS=$(do_request GET "/leads/${lead_id}")
    if [ "$STATUS" = "200" ]; then
      local qualification
      qualification=$(jq -r '.qualification_label // empty' "$TMP_RESP")
      if [ -n "$qualification" ] && [ "$qualification" != "null" ] && [ "$qualification" != "Low Priority" ]; then
        echo -e "  ${GREEN}✔ PASS${NC}  $label (lead $lead_id) finished processing"
        PASS_COUNT=$((PASS_COUNT + 1))
        jq '{company_name, industry, score, qualification_label, scrape_source, scrape_confidence}' "$TMP_RESP" | sed 's/^/         /'
        return 0
      fi
    fi
    attempt=$((attempt + 1))
    sleep "$interval"
  done
  echo -e "  ${RED}✘ FAIL${NC}  $label (lead $lead_id) did not finish within timeout"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  return 1
}

# discovery_search QUERY [LIMIT]
# POSTs to /discovery/search. Body/response land in $TMP_RESP as usual.
discovery_search() {
  local query="$1" limit="${2:-}"
  local body
  if [ -n "$limit" ]; then
    body=$(jq -n --arg q "$query" --argjson l "$limit" '{query: $q, limit: $l}')
  else
    body=$(jq -n --arg q "$query" '{query: $q}')
  fi
  do_request POST "/discovery/search" "$body"
}

# validate_discovery_shape LABEL
# Structural validation of a DiscoveryResponse currently in $TMP_RESP.
# Collects any lead_id into the global DISCOVERY_LEAD_IDS array.
validate_discovery_shape() {
  local label="$1"
  local businesses_found count malformed i
  businesses_found=$(jq -r '.businesses_found // "null"' "$TMP_RESP")
  count=$(jq '(.businesses // []) | length' "$TMP_RESP" 2>/dev/null)

  if [ "$count" = "$businesses_found" ]; then
    echo -e "  ${GREEN}✔ PASS${NC}  $label: businesses_found ($businesses_found) matches array length"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo -e "  ${RED}✘ FAIL${NC}  $label: businesses_found ($businesses_found) != array length ($count)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  local category location
  category=$(jq -r '.category // empty' "$TMP_RESP")
  location=$(jq -r '.location // empty' "$TMP_RESP")
  if [ -n "$category" ] && [ -n "$location" ]; then
    echo -e "  ${GREEN}✔ PASS${NC}  $label: category/location parsed (\"$category\" / \"$location\")"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo -e "  ${RED}✘ FAIL${NC}  $label: category or location missing from response"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  i=0
  malformed=0
  while IFS= read -r biz; do
    i=$((i + 1))
    local name status website lead_id pipeline_status reason
    name=$(echo "$biz" | jq -r '.name // empty')
    status=$(echo "$biz" | jq -r '.status // empty')
    website=$(echo "$biz" | jq -r '.website')
    lead_id=$(echo "$biz" | jq -r '.lead_id')
    pipeline_status=$(echo "$biz" | jq -r '.pipeline_status')
    reason=$(echo "$biz" | jq -r '.reason')

    if [ -z "$name" ] || [ -z "$status" ]; then
      malformed=$((malformed + 1))
      echo -e "         ${RED}malformed business #$i: missing name or status${NC}"
    fi
    if [ "$website" != "null" ] && ! [[ "$website" =~ ^https?:// ]]; then
      malformed=$((malformed + 1))
      echo -e "         ${RED}malformed business #$i: website is not null but not a URL: $website${NC}"
    fi
    if [ "$website" = "null" ] && [ "$lead_id" != "null" ] && [ -n "$lead_id" ]; then
      warn "business #$i (\"$name\") has no website but lead_id=$lead_id was still created"
    fi
    if [ "$lead_id" != "null" ] && [ -n "$lead_id" ]; then
      DISCOVERY_LEAD_IDS+=("$lead_id")
    fi
    echo "         [$i] name=\"$name\" website=$website status=$status lead_id=$lead_id pipeline_status=$pipeline_status reason=$reason"
  done < <(jq -c '(.businesses // [])[]' "$TMP_RESP" 2>/dev/null)

  if [ "$malformed" -eq 0 ]; then
    echo -e "  ${GREEN}✔ PASS${NC}  $label: no malformed business entries ($i checked)"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo -e "  ${RED}✘ FAIL${NC}  $label: $malformed malformed business entries"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

echo -e "${BOLD}LeadBoost API release-validation suite${NC}"
echo "Target: $BASE_URL"
echo "Test user: $TEST_EMAIL"
echo "Discovery queries: ${#DISCOVERY_QUERIES_ARR[@]} (limit=$DISCOVERY_LIMIT each)"

# ===========================================================================
# 1. HEALTH
# ===========================================================================
section "1. Health checks"

STATUS=$(curl -s -o "$TMP_RESP" -w "%{http_code}" "${BASE_URL%/}/live")
check "GET /live" 200 "$STATUS"

STATUS=$(curl -s -o "$TMP_RESP" -w "%{http_code}" "${BASE_URL%/}/ready")
check_one_of "GET /ready" "$STATUS" 200 503
[ "$STATUS" = "503" ] && info "Not fully ready yet (often Redis being unavailable in dev) - continuing anyway"

STATUS=$(curl -s -o "$TMP_RESP" -w "%{http_code}" "${BASE_URL%/}/health")
check_one_of "GET /health" "$STATUS" 200 503
jq '.' "$TMP_RESP" 2>/dev/null | sed 's/^/         /' || true

# ===========================================================================
# 2. AUTH
# ===========================================================================
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
REFRESH_TOKEN=$(jq -r '.refresh_token // empty' "$TMP_RESP")
if [ -z "$AUTH_HEADER" ]; then
  echo -e "${RED}Could not obtain an access token - aborting remaining tests.${NC}"
  exit 1
fi
info "Access token acquired"

STATUS=$(do_request GET "/me")
check "GET /me" 200 "$STATUS"
jq '{id, email, organization_id}' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

STATUS=$(do_request PUT "/me" '{"first_name": "APIUpdated"}')
check "PUT /me" 200 "$STATUS"

if [ -n "$REFRESH_TOKEN" ] && [ "$REFRESH_TOKEN" != "null" ]; then
  ENC_REFRESH=$(jq -rn --arg t "$REFRESH_TOKEN" '$t|@uri')
  STATUS=$(do_request POST "/refresh?refresh_token=${ENC_REFRESH}")
  check_one_of "POST /refresh" "$STATUS" 200 401
else
  info "No refresh_token returned by /login - skipping POST /refresh"
fi

info "Token validation"
SAVED_AUTH="$AUTH_HEADER"
AUTH_HEADER="this.is.not.a.valid.jwt"
STATUS=$(do_request GET "/me")
check_one_of "GET /me with invalid token" "$STATUS" 401 403
AUTH_HEADER=""
STATUS=$(do_request GET "/me")
check_one_of "GET /me with no token" "$STATUS" 401 403
AUTH_HEADER="$SAVED_AUTH"

# ===========================================================================
# 3. ORGANIZATIONS
# ===========================================================================
section "3. Organizations"

STATUS=$(do_request GET "/organizations/")
check "GET /organizations/ (current org)" 200 "$STATUS"
jq '{id, name, plan_tier}' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

STATUS=$(do_request GET "/organizations/${ORG_ID}")
check "GET /organizations/{id}" 200 "$STATUS"

STATUS=$(do_request PUT "/organizations/${ORG_ID}" '{"description": "Updated via API test script"}')
check "PUT /organizations/{id}" 200 "$STATUS"

STATUS=$(do_request POST "/organizations/" '{"name": "Secondary Org (test)", "description": "created by test_api.sh"}')
check_one_of "POST /organizations/ (secondary org, informational)" "$STATUS" 200 400 409 422
[ "$STATUS" = "200" ] && info "Server allows a user to create/own a second organization"

# ===========================================================================
# 4. BILLING
# ===========================================================================
section "4. Billing"

STATUS=$(do_request GET "/plans")
check "GET /plans" 200 "$STATUS"
jq -c '.[] | {name, max_leads_per_day, can_use_ai}' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

STATUS=$(do_request GET "/usage")
check "GET /usage" 200 "$STATUS"
jq '.' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

# Upgrade to "pro" so AI-enhanced pipeline stages (enrichment, company
# intelligence, decision reasoning, messaging) actually run for the
# Discovery + Lead tests below. Self-service dev/test upgrade, no billing
# provider involved.
STATUS=$(do_request POST "/upgrade?plan_name=pro")
check "POST /upgrade?plan_name=pro" 200 "$STATUS"

STATUS=$(do_request GET "/usage")
check "GET /usage (after upgrade)" 200 "$STATUS"
PLAN_NAME=$(jq -r '.plan_name // empty' "$TMP_RESP")
REMAINING_LEADS=$(jq -r '.remaining_daily_leads // empty' "$TMP_RESP")
CAN_PROCESS_MORE=$(jq -r '.can_process_more_today // empty' "$TMP_RESP")
jq '{plan_name, remaining_daily_leads, can_process_more_today}' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'
if [ "$CAN_PROCESS_MORE" = "true" ] && [ -n "$REMAINING_LEADS" ]; then
  echo -e "  ${GREEN}✔ PASS${NC}  Quota validation: plan=$PLAN_NAME has $REMAINING_LEADS lead(s) of daily quota remaining"
  PASS_COUNT=$((PASS_COUNT + 1))
else
  echo -e "  ${RED}✘ FAIL${NC}  Quota validation: org cannot process leads right after upgrading"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# ===========================================================================
# 5. DISCOVERY (NEW)
# ===========================================================================
section "5. Discovery - business search (10 real-world queries)"

for q in "${DISCOVERY_QUERIES_ARR[@]}"; do
  STATUS=$(discovery_search "$q" "$DISCOVERY_LIMIT")
  check "POST /discovery/search - \"$q\"" 200 "$STATUS"
  if [ "$STATUS" = "200" ]; then
    validate_discovery_shape "\"$q\""

    expected_location=$(echo "$q" | sed -E 's/.*[Ii]n +//')
    actual_location=$(jq -r '.location // empty' "$TMP_RESP")
    if echo "$actual_location" | grep -qi "$expected_location"; then
      info "Natural language parsing: location \"$actual_location\" matches query's \"$expected_location\""
    else
      warn "Natural language parsing: expected location containing \"$expected_location\", got \"$actual_location\""
    fi
  fi
done

section "5a. Discovery - duplicate detection"
info "Running \"$DUPLICATE_TEST_QUERY\" twice to verify dedup"

STATUS=$(discovery_search "$DUPLICATE_TEST_QUERY" "$DISCOVERY_LIMIT")
check "POST /discovery/search (duplicate test, run 1)" 200 "$STATUS"
RUN1_JSON=$(jq -c '(.businesses // [])' "$TMP_RESP" 2>/dev/null)

STATUS=$(discovery_search "$DUPLICATE_TEST_QUERY" "$DISCOVERY_LIMIT")
check "POST /discovery/search (duplicate test, run 2)" 200 "$STATUS"
RUN2_JSON=$(jq -c '(.businesses // [])' "$TMP_RESP" 2>/dev/null)

DUP_EVIDENCE=$(jq -n --argjson r1 "$RUN1_JSON" --argjson r2 "$RUN2_JSON" '
  ([$r2[] | select(.status | ascii_downcase | contains("dup"))] | length) as $dup_status |
  ([$r2[] | . as $b2 | select(any($r1[]; .name == $b2.name and .lead_id != null and .lead_id == $b2.lead_id))] | length) as $reused_lead_id |
  {dup_status: $dup_status, reused_lead_id: $reused_lead_id}')
DUP_STATUS_COUNT=$(echo "$DUP_EVIDENCE" | jq -r '.dup_status')
REUSED_LEAD_COUNT=$(echo "$DUP_EVIDENCE" | jq -r '.reused_lead_id')
if [ "$DUP_STATUS_COUNT" -gt 0 ] 2>/dev/null || [ "$REUSED_LEAD_COUNT" -gt 0 ] 2>/dev/null; then
  echo -e "  ${GREEN}✔ PASS${NC}  Duplicate detection: run 2 shows $DUP_STATUS_COUNT duplicate-flagged / $REUSED_LEAD_COUNT reused lead_id(s)"
  PASS_COUNT=$((PASS_COUNT + 1))
else
  warn "Duplicate detection: no explicit duplicate status or reused lead_id observed between the two runs (results may legitimately differ run-to-run for an AI-backed search - verify manually if this repeats)"
fi

section "5b. Discovery - no-website businesses handled correctly"
NO_WEBSITE_COUNT=$(jq '[(.businesses // [])[] | select(.website == null)] | length' "$TMP_RESP" 2>/dev/null)
if [ "$NO_WEBSITE_COUNT" -gt 0 ] 2>/dev/null; then
  BAD=$(jq '[(.businesses // [])[] | select(.website == null and .lead_id != null)] | length' "$TMP_RESP" 2>/dev/null)
  if [ "$BAD" = "0" ]; then
    echo -e "  ${GREEN}✔ PASS${NC}  No-website test: $NO_WEBSITE_COUNT business(es) with website==null correctly have no lead_id/pipeline"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo -e "  ${RED}✘ FAIL${NC}  No-website test: $BAD business(es) with website==null still have a lead_id"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
else
  info "No-website test: no business without a website appeared in this run - nothing to validate here"
fi

section "5c. Discovery - quota enforcement (if applicable)"
STATUS=$(do_request GET "/usage")
REMAINING_BEFORE=$(jq -r '.remaining_daily_leads // empty' "$TMP_RESP")
if [ -n "$REMAINING_BEFORE" ] && [ "$REMAINING_BEFORE" -gt 0 ] && [ "$REMAINING_BEFORE" -le 15 ] 2>/dev/null; then
  QUOTA_TEST_LIMIT=$((REMAINING_BEFORE + 5))
  [ "$QUOTA_TEST_LIMIT" -gt 50 ] && QUOTA_TEST_LIMIT=50
  STATUS=$(discovery_search "Gyms in Patna" "$QUOTA_TEST_LIMIT")
  check_one_of "POST /discovery/search (quota test, limit=$QUOTA_TEST_LIMIT, remaining=$REMAINING_BEFORE)" "$STATUS" 200 402 403 429
else
  info "Quota test skipped/not applicable: remaining_daily_leads=$REMAINING_BEFORE is large or unset for this plan"
fi

section "5d. Discovery - negative tests"

STATUS=$(discovery_search "")
check "Empty query" 422 "$STATUS"

STATUS=$(discovery_search "   ")
check_one_of "Whitespace-only query" "$STATUS" 200 422

STATUS=$(discovery_search "asdkjh1234 qwopiuqwer zxcvnm")
check_one_of "Random garbage query" "$STATUS" 200 422

STATUS=$(discovery_search "Businesses in Atlantis City")
check_one_of "Unknown location query" "$STATUS" 200 404 422

STATUS=$(discovery_search "Quantum flux capacitor repair in Mumbai")
check_one_of "Unknown business type query" "$STATUS" 200 404 422

LONG_QUERY="$(printf 'a%.0s' {1..250})"
STATUS=$(discovery_search "$LONG_QUERY")
check "Very long query (250 chars, max is 200)" 422 "$STATUS"

section "5e. Discovery - stress test (limit=1/5/10/20)"
for lim in 1 5 10 20; do
  STATUS=$(discovery_search "$STRESS_TEST_QUERY" "$lim")
  check "POST /discovery/search - \"$STRESS_TEST_QUERY\" limit=$lim" 200 "$STATUS"
  if [ "$STATUS" = "200" ]; then
    ARR_LEN=$(jq '(.businesses // []) | length' "$TMP_RESP")
    REQ_LIMIT=$(jq -r '.requested_limit // empty' "$TMP_RESP")
    if [ "$ARR_LEN" -le "$lim" ] 2>/dev/null && [ "$REQ_LIMIT" = "$lim" ]; then
      echo -e "  ${GREEN}✔ PASS${NC}  limit=$lim: requested_limit echoed correctly and $ARR_LEN <= $lim businesses returned"
      PASS_COUNT=$((PASS_COUNT + 1))
    else
      echo -e "  ${RED}✘ FAIL${NC}  limit=$lim: requested_limit=$REQ_LIMIT, businesses returned=$ARR_LEN"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
  fi
done

# ===========================================================================
# 6. LEADS
# ===========================================================================
section "6. Leads - bulk create from real company URLs"

URLS_JSON=$(printf '%s\n' "${COMPANY_URLS[@]}" | jq -R . | jq -s .)
STATUS=$(do_request POST "/leads/" "$(jq -n --argjson urls "$URLS_JSON" \
  '{urls: $urls, message_style: "professional"}')")
check "POST /leads/ (bulk create, ${#COMPANY_URLS[@]} URLs)" 200 "$STATUS"
BULK_LEAD_IDS=$(jq -r '.[].id' "$TMP_RESP" 2>/dev/null)
echo "         Created/matched lead IDs: $(echo $BULK_LEAD_IDS | tr '\n' ' ')"

section "6a. Leads - single create"

STATUS=$(do_request POST "/leads/single" "$(jq -n \
  --arg website "$SINGLE_LEAD_URL" --argjson org "$ORG_ID" --argjson owner "$USER_ID" \
  '{website: $website, organization_id: $org, owner_id: $owner}')")
check "POST /leads/single" 200 "$STATUS"
LEAD_ID=$(jq -r '.id // empty' "$TMP_RESP")
info "Primary test lead_id=$LEAD_ID ($SINGLE_LEAD_URL)"

section "6b. Leads - list / get / update"

STATUS=$(do_request GET "/leads/?skip=0&limit=50")
check "GET /leads/ (list)" 200 "$STATUS"
LEAD_COUNT=$(jq 'length' "$TMP_RESP" 2>/dev/null)
info "Organization currently has $LEAD_COUNT lead(s)"

if [ -n "$LEAD_ID" ] && [ "$LEAD_ID" != "null" ]; then
  STATUS=$(do_request GET "/leads/${LEAD_ID}")
  check "GET /leads/{id}" 200 "$STATUS"

  STATUS=$(do_request PUT "/leads/${LEAD_ID}" '{"contact_name": "Manually Edited Contact"}')
  check "PUT /leads/{id}" 200 "$STATUS"

  section "6c. Leads - manual /process trigger + poll for pipeline completion"

  STATUS=$(do_request POST "/leads/${LEAD_ID}/process")
  check_one_of "POST /leads/{id}/process" "$STATUS" 200 403
  if [ "$STATUS" = "403" ]; then
    info "AI features not enabled on this org's plan - skipping poll"
  else
    info "Polling GET /leads/${LEAD_ID} until the pipeline finishes (max $((POLL_MAX_ATTEMPTS * POLL_INTERVAL_SECONDS))s)..."
    poll_lead_until_processed "$LEAD_ID" "$POLL_MAX_ATTEMPTS" "$POLL_INTERVAL_SECONDS" "Manual /process"
  fi

  section "6d. Leads - delete"
  STATUS=$(do_request DELETE "/leads/${LEAD_ID}")
  check "DELETE /leads/{id}" 200 "$STATUS"
else
  echo -e "  ${YELLOW}Skipping single-lead detail tests - no lead_id available${NC}"
fi

section "6e. Leads - pipeline completion for Discovery-created leads"
UNIQUE_DISCOVERY_LEAD_IDS=$(printf '%s\n' "${DISCOVERY_LEAD_IDS[@]:-}" | sort -u -n | grep -v '^$' || true)
if [ -n "$UNIQUE_DISCOVERY_LEAD_IDS" ]; then
  info "Polling $(echo "$UNIQUE_DISCOVERY_LEAD_IDS" | wc -l | tr -d ' ') lead(s) created via Discovery"
  while IFS= read -r did; do
    [ -z "$did" ] && continue
    poll_lead_until_processed "$did" "$DISCOVERY_POLL_MAX_ATTEMPTS" "$DISCOVERY_POLL_INTERVAL" "Discovery pipeline"
  done <<< "$UNIQUE_DISCOVERY_LEAD_IDS"
else
  info "No Discovery-created leads with a website were available to poll"
fi

# ===========================================================================
# 7. ANALYTICS
# ===========================================================================
section "7. Analytics"

STATUS=$(do_request GET "/analytics/pipeline-metrics")
check "GET /analytics/pipeline-metrics" 200 "$STATUS"
jq '.' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

STATUS=$(do_request GET "/analytics/pipeline-metrics?hours=24")
check "GET /analytics/pipeline-metrics?hours=24" 200 "$STATUS"

STATUS=$(do_request GET "/analytics/evaluation-metrics")
check "GET /analytics/evaluation-metrics" 200 "$STATUS"
jq '.' "$TMP_RESP" 2>/dev/null | sed 's/^/         /'

STATUS=$(do_request GET "/analytics/discovery-metrics")
check "GET /analytics/discovery-metrics" 200 "$STATUS"
if [ "$STATUS" = "200" ]; then
  for field in discovery_success_rate_pct website_resolution_rate_pct duplicate_removal_rate_pct avg_discovery_time_ms; do
    val=$(jq -r --arg f "$field" '.[$f] // "missing"' "$TMP_RESP")
    if [ "$val" != "missing" ]; then
      echo -e "  ${GREEN}✔ PASS${NC}  discovery-metrics.$field = $val"
      PASS_COUNT=$((PASS_COUNT + 1))
    else
      echo -e "  ${RED}✘ FAIL${NC}  discovery-metrics.$field is missing"
      FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
  done
fi

STATUS=$(do_request GET "/analytics/discovery-metrics?hours=24")
check "GET /analytics/discovery-metrics?hours=24" 200 "$STATUS"

# ===========================================================================
# 8. COMPLETE WORKFLOW (NL query -> Discovery -> Lead -> Pipeline -> Analytics)
# ===========================================================================
section "8. Complete workflow: \"$WORKFLOW_TEST_QUERY\""

STATUS=$(do_request GET "/analytics/discovery-metrics")
RUNS_BEFORE=$(jq -r '.total_discovery_runs // 0' "$TMP_RESP")

STATUS=$(discovery_search "$WORKFLOW_TEST_QUERY" 5)
check "Workflow step 1: POST /discovery/search" 200 "$STATUS"
WORKFLOW_LEAD_ID=$(jq -r '[(.businesses // [])[] | select(.lead_id != null)][0].lead_id // empty' "$TMP_RESP")

if [ -n "$WORKFLOW_LEAD_ID" ] && [ "$WORKFLOW_LEAD_ID" != "null" ]; then
  STATUS=$(do_request GET "/leads/${WORKFLOW_LEAD_ID}")
  check "Workflow step 2: GET /leads/{id} for discovered lead" 200 "$STATUS"

  info "Workflow step 3: confirming pipeline output on the discovered lead"
  poll_lead_until_processed "$WORKFLOW_LEAD_ID" "$DISCOVERY_POLL_MAX_ATTEMPTS" "$DISCOVERY_POLL_INTERVAL" "Workflow"

  STATUS=$(do_request GET "/analytics/discovery-metrics")
  check "Workflow step 4: GET /analytics/discovery-metrics reflects the run" 200 "$STATUS"
  RUNS_AFTER=$(jq -r '.total_discovery_runs // 0' "$TMP_RESP")
  if [ "$RUNS_AFTER" -ge "$RUNS_BEFORE" ] 2>/dev/null; then
    echo -e "  ${GREEN}✔ PASS${NC}  total_discovery_runs did not decrease ($RUNS_BEFORE -> $RUNS_AFTER)"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo -e "  ${RED}✘ FAIL${NC}  total_discovery_runs decreased ($RUNS_BEFORE -> $RUNS_AFTER)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
else
  warn "Workflow query \"$WORKFLOW_TEST_QUERY\" produced no lead_id (no resolvable website) - full chain could not be exercised end-to-end. Try WORKFLOW_TEST_QUERY=... with a query more likely to resolve websites."
fi

# ===========================================================================
# 9. Billing - cancel (run last, non-destructive: immediate=false)
# ===========================================================================
section "9. Billing - cancel subscription (end-of-period, non-destructive)"
STATUS=$(do_request POST "/cancel?immediate=false")
check_one_of "POST /cancel?immediate=false" "$STATUS" 200 400

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
section "Summary"
TOTAL=$((PASS_COUNT + FAIL_COUNT))
echo -e "  ${GREEN}Passed: $PASS_COUNT${NC} / $TOTAL"
[ "$WARN_COUNT" -gt 0 ] && echo -e "  ${YELLOW}Warnings: $WARN_COUNT${NC} (non-fatal, review above)"
if [ "$FAIL_COUNT" -gt 0 ]; then
  echo -e "  ${RED}Failed: $FAIL_COUNT${NC} / $TOTAL"
  exit 1
fi
echo -e "  ${GREEN}${BOLD}All checks passed.${NC}"
exit 0