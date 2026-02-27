#!/usr/bin/env bash
# DocuFlux load test runner
# Usage: ./tests/load/run_load_test.sh [users] [spawn-rate] [duration]
# Defaults: 20 users, 5/s spawn rate, 60s duration

set -euo pipefail

USERS=${1:-20}
SPAWN_RATE=${2:-5}
DURATION=${3:-60s}
HOST=${LOCUST_HOST:-http://localhost:5000}
CSV_DIR="tests/load/results"

mkdir -p "$CSV_DIR"

echo "=== DocuFlux Load Test ==="
echo "Host:       $HOST"
echo "Users:      $USERS"
echo "Spawn rate: $SPAWN_RATE/s"
echo "Duration:   $DURATION"
echo ""

# Check locust is installed
if ! command -v locust &>/dev/null; then
    echo "ERROR: locust not installed. Run: pip install locust"
    exit 1
fi

locust \
    -f tests/load/locustfile.py \
    --headless \
    -u "$USERS" \
    -r "$SPAWN_RATE" \
    --run-time "$DURATION" \
    --host "$HOST" \
    --csv="$CSV_DIR/load_test" \
    --html="$CSV_DIR/load_test.html" \
    --exit-code-on-error 1

echo ""
echo "Results written to $CSV_DIR/"
echo "Open $CSV_DIR/load_test.html for the full report."
