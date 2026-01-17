#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Deploying renewed certificates..."

# Define certificate paths
LE_LIVE_DIR="/etc/letsencrypt/live/${DOMAIN_NAME}"
REDIS_CERT_DIR="/certs/redis" # Path where Redis expects certificates

# Ensure Redis certificate directory exists
mkdir -p "${REDIS_CERT_DIR}"

# Copy Let's Encrypt certificates to Redis certificate directory
# fullchain.pem contains the server certificate followed by intermediate CAs
# privkey.pem is the private key
# The CA certificate (chain.pem) is not strictly needed by Redis if it trusts system CAs
# or if clients already have it, but for explicit trust it's good to include.

# Copy server certificate and private key
cp "${LE_LIVE_DIR}/fullchain.pem" "${REDIS_CERT_DIR}/redis.crt"
cp "${LE_LIVE_DIR}/privkey.pem" "${REDIS_CERT_DIR}/redis.key"

# Copy the CA chain (for clients to verify server, and possibly for server to verify client if mTLS)
# Redis might not strictly need ca.crt on the server side unless client certificates are used.
# But for a complete setup, we include it.
cp "${LE_LIVE_DIR}/chain.pem" "${REDIS_CERT_DIR}/ca.crt"

echo "Certificates deployed to ${REDIS_CERT_DIR}"
ls -l "${REDIS_CERT_DIR}"

# Set appropriate permissions (private key should be readable only by root)
chmod 600 "${REDIS_CERT_DIR}/redis.key"
chmod 644 "${REDIS_CERT_DIR}/redis.crt"
chmod 644 "${REDIS_CERT_DIR}/ca.crt"

echo "Certificate deployment complete."
