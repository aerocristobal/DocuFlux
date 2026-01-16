#!/bin/bash
# Cloudflare Tunnel Setup Script
# This script helps create and configure a Cloudflare Tunnel for DocuFlux

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}DocuFlux - Cloudflare Tunnel Setup${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if cloudflared is installed
if ! command -v cloudflared &> /dev/null; then
    echo -e "${RED}Error: cloudflared CLI not found${NC}"
    echo ""
    echo "Please install cloudflared:"
    echo "  macOS: brew install cloudflared"
    echo "  Linux: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/"
    exit 1
fi

echo -e "${GREEN}✓ cloudflared CLI found${NC}"
echo ""

# Check if user is logged in
echo -e "${YELLOW}Step 1: Authenticate with Cloudflare${NC}"
echo "This will open a browser window for authentication..."
cloudflared tunnel login

echo ""
echo -e "${YELLOW}Step 2: Create a new tunnel${NC}"
read -p "Enter a name for your tunnel (e.g., docuflux-tunnel): " TUNNEL_NAME

if [ -z "$TUNNEL_NAME" ]; then
    echo -e "${RED}Error: Tunnel name cannot be empty${NC}"
    exit 1
fi

# Create tunnel
echo -e "${BLUE}Creating tunnel: ${TUNNEL_NAME}${NC}"
cloudflared tunnel create "$TUNNEL_NAME"

# Get tunnel ID
TUNNEL_ID=$(cloudflared tunnel list | grep "$TUNNEL_NAME" | awk '{print $1}')

if [ -z "$TUNNEL_ID" ]; then
    echo -e "${RED}Error: Failed to create tunnel${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Tunnel created successfully${NC}"
echo -e "${GREEN}  Tunnel ID: ${TUNNEL_ID}${NC}"
echo ""

# Get tunnel token
echo -e "${YELLOW}Step 3: Generate tunnel token${NC}"
TUNNEL_TOKEN=$(cloudflared tunnel token "$TUNNEL_NAME")

echo -e "${GREEN}✓ Tunnel token generated${NC}"
echo ""

# Setup DNS
echo -e "${YELLOW}Step 4: Configure DNS${NC}"
read -p "Enter your domain (e.g., docuflux.example.com): " DOMAIN

if [ -z "$DOMAIN" ]; then
    echo -e "${RED}Error: Domain cannot be empty${NC}"
    exit 1
fi

# Create DNS record
echo -e "${BLUE}Creating DNS record for ${DOMAIN}${NC}"
cloudflared tunnel route dns "$TUNNEL_NAME" "$DOMAIN"

echo -e "${GREEN}✓ DNS record created${NC}"
echo ""

# Save configuration
echo -e "${YELLOW}Step 5: Save configuration${NC}"

# Create .env file if it doesn't exist
if [ ! -f ../.env ]; then
    cp ../.env.example ../.env
    echo -e "${GREEN}✓ Created .env file from .env.example${NC}"
fi

# Update .env file
if grep -q "CLOUDFLARE_TUNNEL_TOKEN=" ../.env; then
    # Update existing token
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s|CLOUDFLARE_TUNNEL_TOKEN=.*|CLOUDFLARE_TUNNEL_TOKEN=${TUNNEL_TOKEN}|" ../.env
    else
        sed -i "s|CLOUDFLARE_TUNNEL_TOKEN=.*|CLOUDFLARE_TUNNEL_TOKEN=${TUNNEL_TOKEN}|" ../.env
    fi
    echo -e "${GREEN}✓ Updated CLOUDFLARE_TUNNEL_TOKEN in .env${NC}"
else
    # Append new token
    echo "" >> ../.env
    echo "# Cloudflare Tunnel Configuration" >> ../.env
    echo "CLOUDFLARE_TUNNEL_TOKEN=${TUNNEL_TOKEN}" >> ../.env
    echo -e "${GREEN}✓ Added CLOUDFLARE_TUNNEL_TOKEN to .env${NC}"
fi

# Update SESSION_COOKIE_SECURE for HTTPS
if grep -q "SESSION_COOKIE_SECURE=" ../.env; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s|SESSION_COOKIE_SECURE=.*|SESSION_COOKIE_SECURE=true|" ../.env
    else
        sed -i "s|SESSION_COOKIE_SECURE=.*|SESSION_COOKIE_SECURE=true|" ../.env
    fi
else
    echo "SESSION_COOKIE_SECURE=true" >> ../.env
fi

# Update BEHIND_PROXY for proxy detection
if grep -q "BEHIND_PROXY=" ../.env; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s|BEHIND_PROXY=.*|BEHIND_PROXY=true|" ../.env
    else
        sed -i "s|BEHIND_PROXY=.*|BEHIND_PROXY=true|" ../.env
    fi
else
    echo "BEHIND_PROXY=true" >> ../.env
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✓ Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "1. Start DocuFlux with HTTPS profile:"
echo -e "   ${BLUE}docker-compose --profile https up${NC}"
echo ""
echo "2. Access your application at:"
echo -e "   ${BLUE}https://${DOMAIN}${NC}"
echo ""
echo -e "${YELLOW}Configuration saved to:${NC}"
echo "  - Tunnel Token: ../.env (CLOUDFLARE_TUNNEL_TOKEN)"
echo "  - Tunnel ID: ${TUNNEL_ID}"
echo "  - Domain: ${DOMAIN}"
echo ""
echo -e "${YELLOW}Tunnel Management:${NC}"
echo "  List tunnels:  cloudflared tunnel list"
echo "  Delete tunnel: cloudflared tunnel delete ${TUNNEL_NAME}"
echo "  View routes:   cloudflared tunnel route dns"
echo ""
