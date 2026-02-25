#!/bin/bash
#
# Renew Redis TLS Certificates
#
# Checks certificate expiration and renews if needed.
# For self-signed development certificates only.
# Production should use Certbot with Let's Encrypt (Epic 25).
#
# Epic 24.4: Certificate Management for Redis TLS
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERTS_DIR="${SCRIPT_DIR}/../certs"
REDIS_CERTS_DIR="${CERTS_DIR}/redis"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Certificate renewal threshold (days before expiration)
RENEWAL_THRESHOLD=30

echo -e "${GREEN}=== Redis TLS Certificate Renewal ===${NC}"
echo ""

# Check if certificates exist
if [ ! -f "${REDIS_CERTS_DIR}/redis.crt" ]; then
    echo -e "${RED}Error: Redis certificate not found at ${REDIS_CERTS_DIR}/redis.crt${NC}"
    echo "Run ./scripts/generate-redis-certs.sh to create certificates."
    exit 1
fi

# Check certificate expiration
echo "Checking certificate expiration..."
EXPIRY_DATE=$(openssl x509 -in "${REDIS_CERTS_DIR}/redis.crt" -noout -enddate | cut -d= -f2)
EXPIRY_EPOCH=$(date -d "${EXPIRY_DATE}" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "${EXPIRY_DATE}" +%s)
CURRENT_EPOCH=$(date +%s)
DAYS_UNTIL_EXPIRY=$(( (EXPIRY_EPOCH - CURRENT_EPOCH) / 86400 ))

echo "Certificate expires: ${EXPIRY_DATE}"
echo "Days until expiration: ${DAYS_UNTIL_EXPIRY}"
echo ""

if [ ${DAYS_UNTIL_EXPIRY} -gt ${RENEWAL_THRESHOLD} ]; then
    echo -e "${GREEN}✓ Certificate is valid for ${DAYS_UNTIL_EXPIRY} more days.${NC}"
    echo "No renewal needed (threshold: ${RENEWAL_THRESHOLD} days)."
    exit 0
fi

echo -e "${YELLOW}⚠️  Certificate expires in ${DAYS_UNTIL_EXPIRY} days.${NC}"
echo "Renewal threshold: ${RENEWAL_THRESHOLD} days"
echo ""

# Prompt for confirmation
read -p "Renew certificate now? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Renewal cancelled."
    exit 0
fi

# Backup existing certificates
BACKUP_DIR="${CERTS_DIR}/backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "${BACKUP_DIR}"
echo "Backing up existing certificates to ${BACKUP_DIR}..."
cp "${REDIS_CERTS_DIR}"/*.{crt,key} "${BACKUP_DIR}/" 2>/dev/null || true

# Regenerate certificates
echo "Regenerating certificates..."
"${SCRIPT_DIR}/generate-redis-certs.sh" --force

echo ""
echo -e "${GREEN}✓ Certificate renewal complete!${NC}"
echo ""
echo "Next steps:"
echo "1. Reload services: docker-compose restart redis web worker beat"
echo "2. Verify connection: redis-cli --tls --cert certs/redis/redis.crt --key certs/redis/redis.key --cacert certs/redis/ca.crt -h localhost ping"
echo ""
echo "Backup location: ${BACKUP_DIR}"
