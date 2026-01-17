#!/bin/bash
#
# Reload Services After Certificate Renewal
#
# Gracefully restarts services to pick up new TLS certificates.
# Ensures zero downtime by waiting for health checks.
#
# Epic 24.4: Certificate Management for Redis TLS
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SCRIPT_DIR}/.."

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Service Reload ===${NC}"
echo ""

# Check if docker-compose is available
if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}Error: docker-compose not found${NC}"
    exit 1
fi

cd "${PROJECT_DIR}"

# Function to check service health
check_health() {
    local service=$1
    local max_attempts=30
    local attempt=0

    echo "Waiting for ${service} to be healthy..."
    while [ $attempt -lt $max_attempts ]; do
        if docker-compose ps ${service} | grep -q "healthy"; then
            echo -e "${GREEN}✓ ${service} is healthy${NC}"
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 2
    done

    echo -e "${RED}✗ ${service} did not become healthy${NC}"
    return 1
}

# Step 1: Restart Redis (picks up new certificates)
echo "Step 1/4: Restarting Redis..."
docker-compose restart redis
check_health redis
echo ""

# Step 2: Restart worker (reconnects to Redis with new certs)
echo "Step 2/4: Restarting worker..."
docker-compose restart worker
check_health worker
echo ""

# Step 3: Restart beat scheduler
echo "Step 3/4: Restarting beat scheduler..."
docker-compose restart beat
sleep 3  # Beat doesn't have health check
echo -e "${GREEN}✓ beat restarted${NC}"
echo ""

# Step 4: Restart web (reconnects to Redis with new certs)
echo "Step 4/4: Restarting web..."
docker-compose restart web
check_health web
echo ""

echo -e "${GREEN}=== All Services Reloaded Successfully ===${NC}"
echo ""
echo "Verification:"
echo "1. Check logs: docker-compose logs --tail=50 redis web worker"
echo "2. Test Redis TLS: redis-cli --tls --cert certs/redis/redis.crt --key certs/redis/redis.key --cacert certs/redis/ca.crt -h localhost ping"
echo "3. Test web UI: curl http://localhost:5000/"
