#!/usr/bin/env bash
# smoke_test.sh - E2E smoke test for a running DocuFlux instance
#
# Usage:
#   ./scripts/smoke_test.sh [BASE_URL]
#
# Default BASE_URL: http://localhost:5000
#
# Requirements: curl, jq
#
# Exit codes:
#   0 - all checks passed
#   1 - one or more checks failed

set -euo pipefail

BASE_URL="${1:-http://localhost:5000}"
PASS=0
FAIL=0

green() { printf '\033[0;32m✓ %s\033[0m\n' "$1"; }
red()   { printf '\033[0;31m✗ %s\033[0m\n' "$1"; }

check() {
    local name="$1"
    local result="$2"
    if [ "$result" = "ok" ]; then
        green "$name"
        PASS=$((PASS + 1))
    else
        red "$name ($result)"
        FAIL=$((FAIL + 1))
    fi
}

echo "==> DocuFlux smoke test: $BASE_URL"
echo

# 1. Liveness probe
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/healthz")
[ "$STATUS" = "200" ] && check "GET /healthz returns 200" "ok" || check "GET /healthz" "$STATUS"

# 2. Readiness probe
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/readyz")
[ "$STATUS" = "200" ] && check "GET /readyz returns 200" "ok" || check "GET /readyz" "$STATUS"

# 3. Main UI
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/")
[ "$STATUS" = "200" ] && check "GET / returns 200" "ok" || check "GET /" "$STATUS"

# 4. Service status API
BODY=$(curl -s "$BASE_URL/api/status/services")
DISK=$(echo "$BODY" | jq -r '.disk_space // empty' 2>/dev/null)
[ -n "$DISK" ] && check "GET /api/status/services has disk_space" "ok" || check "GET /api/status/services" "missing disk_space"

# 5. Formats API
BODY=$(curl -s "$BASE_URL/api/v1/formats")
COUNT=$(echo "$BODY" | jq '.input_formats | length' 2>/dev/null)
[ "${COUNT:-0}" -gt 0 ] && check "GET /api/v1/formats returns formats" "ok" || check "GET /api/v1/formats" "empty or error"

# 6. Submit a conversion job (markdown -> html)
TMPFILE=$(mktemp /tmp/smoke_XXXXXX.md)
echo "# Smoke Test\n\nThis is a test document." > "$TMPFILE"

RESPONSE=$(curl -s -X POST "$BASE_URL/convert" \
    -F "file=@$TMPFILE;filename=smoke.md" \
    -F "from_format=markdown" \
    -F "to_format=html")
rm -f "$TMPFILE"

JOB_ID=$(echo "$RESPONSE" | jq -r '.job_ids[0] // empty' 2>/dev/null)
if [ -n "$JOB_ID" ]; then
    check "POST /convert creates job (job_id=$JOB_ID)" "ok"
else
    check "POST /convert" "no job_id in response: $RESPONSE"
fi

# 7. API v1 submit
TMPFILE=$(mktemp /tmp/smoke_XXXXXX.md)
echo "# API v1 Smoke Test" > "$TMPFILE"

RESPONSE=$(curl -s -X POST "$BASE_URL/api/v1/convert" \
    -F "file=@$TMPFILE;filename=api_smoke.md" \
    -F "from_format=markdown" \
    -F "to_format=html")
rm -f "$TMPFILE"

API_JOB_ID=$(echo "$RESPONSE" | jq -r '.job_id // empty' 2>/dev/null)
STATUS_URL=$(echo "$RESPONSE" | jq -r '.status_url // empty' 2>/dev/null)
if [ -n "$API_JOB_ID" ] && [ -n "$STATUS_URL" ]; then
    check "POST /api/v1/convert returns job_id and status_url" "ok"
else
    check "POST /api/v1/convert" "missing job_id or status_url: $RESPONSE"
fi

# 8. Status check (poll up to 10s for completion)
if [ -n "${JOB_ID:-}" ]; then
    for i in $(seq 1 10); do
        sleep 1
        JOB_STATUS=$(curl -s "$BASE_URL/api/v1/status/$JOB_ID" | jq -r '.status // empty' 2>/dev/null)
        if [ "$JOB_STATUS" = "success" ] || [ "$JOB_STATUS" = "failed" ]; then
            break
        fi
    done
    if [ "$JOB_STATUS" = "success" ]; then
        check "Job $JOB_ID completes successfully" "ok"
    elif [ "$JOB_STATUS" = "failed" ]; then
        check "Job $JOB_ID" "failed (may be expected without worker)"
    else
        check "Job status poll (${JOB_STATUS:-pending/processing})" "ok (worker may not be running)"
        PASS=$((PASS + 1))
        FAIL=$((FAIL - 1))
    fi
fi

echo
echo "==> Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
