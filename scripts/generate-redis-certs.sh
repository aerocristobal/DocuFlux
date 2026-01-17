#!/bin/bash
#
# Generate TLS Certificates for Redis
#
# Creates a self-signed CA and server certificates for Redis TLS.
# For development/testing only. Production should use proper CA (Certbot/Let's Encrypt).
#
# Epic 24.1: Redis TLS Configuration with CA Certificates
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERTS_DIR="${SCRIPT_DIR}/../certs"
REDIS_CERTS_DIR="${CERTS_DIR}/redis"

# Certificate parameters
CA_SUBJECT="/C=US/ST=California/L=San Francisco/O=DocuFlux/OU=Development/CN=DocuFlux CA"
SERVER_SUBJECT="/C=US/ST=California/L=San Francisco/O=DocuFlux/OU=Development/CN=redis"
VALIDITY_DAYS=3650  # 10 years for development

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Redis TLS Certificate Generation ===${NC}"
echo ""

# Create certificates directory
mkdir -p "${REDIS_CERTS_DIR}"

# Check if certificates already exist
if [ -f "${REDIS_CERTS_DIR}/ca.crt" ] && [ -f "${REDIS_CERTS_DIR}/redis.key" ]; then
    echo -e "${YELLOW}Warning: Certificates already exist in ${REDIS_CERTS_DIR}${NC}"
    echo -e "${YELLOW}Delete them manually if you want to regenerate.${NC}"
    echo ""
    echo "Existing certificates:"
    ls -lh "${REDIS_CERTS_DIR}"
    exit 0
fi

cd "${REDIS_CERTS_DIR}"

echo "Step 1/5: Generating CA private key..."
openssl genrsa -out ca.key 4096
chmod 400 ca.key

echo "Step 2/5: Generating CA certificate..."
openssl req \
    -new \
    -x509 \
    -days ${VALIDITY_DAYS} \
    -key ca.key \
    -out ca.crt \
    -subj "${CA_SUBJECT}"
chmod 444 ca.crt

echo "Step 3/5: Generating Redis server private key..."
openssl genrsa -out redis.key 2048
chmod 400 redis.key

echo "Step 4/5: Generating Redis server certificate signing request..."
openssl req \
    -new \
    -key redis.key \
    -out redis.csr \
    -subj "${SERVER_SUBJECT}"

# Create server certificate extensions
cat > redis.ext <<EOF
basicConstraints = CA:FALSE
nsCertType = server
nsComment = "Redis TLS Server Certificate"
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer:always
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = redis
DNS.2 = localhost
IP.1 = 127.0.0.1
EOF

echo "Step 5/5: Signing Redis server certificate with CA..."
openssl x509 \
    -req \
    -days ${VALIDITY_DAYS} \
    -in redis.csr \
    -CA ca.crt \
    -CAkey ca.key \
    -CAcreateserial \
    -out redis.crt \
    -extfile redis.ext
chmod 444 redis.crt

# Clean up intermediate files
rm -f redis.csr redis.ext ca.srl

echo ""
echo -e "${GREEN}✓ Certificate generation complete!${NC}"
echo ""
echo "Generated files:"
ls -lh "${REDIS_CERTS_DIR}"
echo ""

# Verify certificates
echo "Verifying certificates..."
openssl verify -CAfile ca.crt redis.crt

echo ""
echo -e "${GREEN}Certificate details:${NC}"
echo "CA Certificate:"
openssl x509 -in ca.crt -noout -subject -issuer -dates
echo ""
echo "Redis Server Certificate:"
openssl x509 -in redis.crt -noout -subject -issuer -dates -ext subjectAltName
echo ""

echo -e "${GREEN}=== Setup Complete ===${NC}"
echo ""
echo "Next steps:"
echo "1. Restart services: docker-compose restart redis web worker"
echo "2. Verify TLS connection: redis-cli --tls --cert certs/redis/redis.crt --key certs/redis/redis.key --cacert certs/redis/ca.crt -h localhost ping"
echo ""
echo -e "${YELLOW}Note: These are self-signed certificates for development only.${NC}"
echo -e "${YELLOW}For production, use Certbot with Let's Encrypt (see Epic 25).${NC}"
