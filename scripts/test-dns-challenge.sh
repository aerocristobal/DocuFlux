#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

DOMAIN=$1

if [ -z "$DOMAIN" ]; then
  echo "Usage: $0 <your-domain>"
  exit 1
fi

ACME_CHALLENGE_DOMAIN="_acme-challenge.$DOMAIN"

echo "Querying DNS for TXT records for: $ACME_CHALLENGE_DOMAIN"
echo "This usually takes a few seconds to propagate after Certbot creates it."
echo "You might need to run this script multiple times."

# Query for TXT records
TXT_RECORDS=$(dig +short TXT "$ACME_CHALLENGE_DOMAIN")

if [ -z "$TXT_RECORDS" ]; then
  echo "No TXT records found for $ACME_CHALLENGE_DOMAIN."
  echo "This might mean:"
  echo "1. The record has not propagated yet."
  2. Certbot failed to create the record."
  "3. There's a misconfiguration in your Cloudflare DNS settings or API token permissions."
  exit 1
else
  echo "Found TXT record(s) for $ACME_CHALLENGE_DOMAIN:"
  echo "$TXT_RECORDS"
  echo "DNS record found. This indicates that the DNS-01 challenge *should* succeed."
  exit 0
fi
