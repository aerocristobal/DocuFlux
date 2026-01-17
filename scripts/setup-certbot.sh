#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Starting Certbot setup..."

# Check if environment variables are set
if [ -z "$CERTBOT_EMAIL" ]; then
  echo "Error: CERTBOT_EMAIL environment variable is not set."
  exit 1
fi

if [ -z "$DOMAIN_NAME" ]; then
  echo "Error: DOMAIN_NAME environment variable is not set."
  exit 1
fi

echo "Attempting to obtain certificate for ${DOMAIN_NAME} with email ${CERTBOT_EMAIL}"

# Obtain certificate using Certbot with Cloudflare DNS authenticator
# --dns-cloudflare-credentials points to the mounted credentials.ini
# --dns-cloudflare-propagation-seconds can be adjusted if DNS updates are slow
certbot certonly \
  --dns-cloudflare \
  --dns-cloudflare-credentials /credentials.ini \
  --dns-cloudflare-propagation-seconds 60 \
  --non-interactive \
  --agree-tos \
  --email "$CERTBOT_EMAIL" \
  -d "$DOMAIN_NAME" \
  --cert-name "$DOMAIN_NAME" \
  --staging # Use --staging for testing, remove for production
