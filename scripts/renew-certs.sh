#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

echo "Starting Certbot certificate renewal..."

# Run Certbot renewal
# The --force-renewal flag is for testing purposes only. Remove in production.
# certbot renew --force-renewal

certbot renew --quiet

if [ $? -ne 0 ]; then
  echo "Certbot renewal failed."
  exit 1
fi

echo "Certbot renewal completed. Checking for updated certificates..."

# Check if certificates were actually renewed
# If certificates were renewed, Certbot exits with 0 and prints specific output
# We can check the timestamp of the fullchain.pem file
CERT_PATH="/etc/letsencrypt/live/${DOMAIN_NAME}/fullchain.pem"
LAST_RENEWAL_FILE="/tmp/cert_last_renewal"

if [ -f "$CERT_PATH" ]; then
  CURRENT_TIMESTAMP=$(date +%s)
  CERT_MOD_TIMESTAMP=$(stat -c %Y "$CERT_PATH")

  if [ -f "$LAST_RENEWAL_FILE" ]; then
    LAST_RENEWAL_TIMESTAMP=$(cat "$LAST_RENEWAL_FILE")
  else
    LAST_RENEWAL_TIMESTAMP=0 # Treat as never renewed if file doesn't exist
  fi

  # If certificate was modified more recently than our last check
  if [ "$CERT_MOD_TIMESTAMP" -gt "$LAST_RENEWAL_TIMESTAMP" ]; then
    echo "Certificates were renewed or updated. Deploying new certificates..."
    # Update the last renewal timestamp
    echo "$CURRENT_TIMESTAMP" > "$LAST_RENEWAL_FILE"
    # Call the script to deploy certificates
    /app/deploy-certs.sh

  else
    echo "No certificates were renewed. Services not reloaded."
  fi
else
  echo "Certificate path $CERT_PATH does not exist. Cannot check for renewal."
  exit 1
fi

echo "Certbot renewal script finished."
