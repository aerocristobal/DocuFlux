# Cloudflare Tunnel Setup Guide

This guide explains how to configure HTTPS support for DocuFlux using Cloudflare Tunnel (Epic 22).

## Overview

**Cloudflare Tunnel** provides zero-touch HTTPS with automatic SSL certificate management. It creates a secure outbound connection from your DocuFlux instance to Cloudflare's edge network, eliminating the need for:
- Public IP addresses or port forwarding
- Manual SSL certificate generation and renewal
- Complex nginx/caddy reverse proxy setup

## Benefits

✅ **Zero-touch HTTPS** - Automatic SSL certificates, managed by Cloudflare
✅ **No exposed ports** - Tunnel connects outbound, no inbound firewall rules
✅ **DDoS protection** - Traffic routed through Cloudflare's edge network
✅ **WebSocket support** - Automatic upgrade to wss:// for real-time updates
✅ **Free tier available** - No cost for basic usage

## Prerequisites

- **Cloudflare account** (free tier works)
- **Domain managed by Cloudflare DNS** (can transfer existing domain)
- **cloudflared CLI** installed on your machine (one-time setup)

## Quick Start (Automated Setup)

The fastest way to set up Cloudflare Tunnel:

```bash
# 1. Install cloudflared CLI
# macOS
brew install cloudflared

# Linux
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# 2. Run automated setup script
cd cloudflare
./setup.sh

# Follow prompts to:
# - Authenticate with Cloudflare
# - Create a tunnel
# - Configure DNS
# - Save configuration to .env

# 3. Start DocuFlux with Cloudflare Tunnel
docker-compose -f docker-compose.yml -f docker-compose.cloudflare.yml up
```

The setup script automatically:
- Creates a Cloudflare Tunnel
- Configures DNS records
- Generates tunnel token
- Updates `.env` with configuration
- Enables secure cookies and proxy headers

## Manual Setup

If you prefer manual configuration:

### Step 1: Install cloudflared CLI

Follow the installation instructions above.

### Step 2: Authenticate with Cloudflare

```bash
cloudflared tunnel login
```

This opens a browser window for authentication and downloads credentials to `~/.cloudflared/cert.pem`.

### Step 3: Create a Tunnel

```bash
cloudflared tunnel create docuflux-tunnel
```

Output:
```
Tunnel credentials written to /Users/you/.cloudflared/TUNNEL_ID.json
Created tunnel docuflux-tunnel with id TUNNEL_ID
```

Save the **TUNNEL_ID** for later steps.

### Step 4: Configure DNS

Route your domain to the tunnel:

```bash
cloudflared tunnel route dns docuflux-tunnel docuflux.example.com
```

Replace `docuflux.example.com` with your actual domain.

### Step 5: Get Tunnel Token

```bash
cloudflared tunnel token docuflux-tunnel
```

This outputs a long token starting with `eyJ...`. Copy this token.

### Step 6: Configure Environment Variables

Create or update `.env` file:

```bash
# Copy example if needed
cp .env.example .env

# Add tunnel configuration
echo "CLOUDFLARE_TUNNEL_TOKEN=your-token-here" >> .env
echo "SESSION_COOKIE_SECURE=true" >> .env
echo "BEHIND_PROXY=true" >> .env
```

### Step 7: Start DocuFlux with Cloudflare Tunnel

```bash
docker-compose -f docker-compose.yml -f docker-compose.cloudflare.yml up
```

### Step 8: Verify HTTPS Access

Open your browser and navigate to:
```
https://docuflux.example.com
```

You should see:
- ✅ Valid SSL certificate (from Cloudflare)
- ✅ DocuFlux UI loads over HTTPS
- ✅ WebSocket connections use wss://
- ✅ Secure cookies with HttpOnly flag

## Architecture

### Traffic Flow

```
User Browser (HTTPS)
    ↓
Cloudflare Edge Network (SSL Termination)
    ↓
Cloudflare Tunnel (Encrypted Tunnel)
    ↓
cloudflare-tunnel container (Docker)
    ↓
web container (HTTP on port 5000)
```

### Components

| Component | Purpose |
|-----------|---------|
| **Cloudflare Edge** | SSL termination, DDoS protection, CDN |
| **Tunnel** | Secure outbound connection from your network |
| **cloudflare-tunnel service** | Docker container running cloudflared |
| **web service** | Flask app (receives HTTP from tunnel) |

### Security Features (Epic 22.4)

When `BEHIND_PROXY=true`:

1. **ProxyFix Middleware**: Trusts `X-Forwarded-*` headers from Cloudflare
2. **Secure Cookies**: `SESSION_COOKIE_SECURE=true` prevents cookie theft
3. **HSTS Header**: Enforces HTTPS on all requests
4. **WSS Support**: WebSocket connections automatically upgrade to wss://

## Configuration Files

### cloudflare/config.yml

Optional configuration file for advanced ingress rules:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /etc/cloudflared/credentials.json

ingress:
  # Route all traffic to web service
  - hostname: "*.your-domain.com"
    service: http://web:5000
    originRequest:
      noTLSVerify: false
      connectTimeout: 30s
      http2Origin: true

  # Default rule (required)
  - service: http_status:404

loglevel: info
```

### docker-compose.cloudflare.yml

The `cloudflare-tunnel` service is defined in `docker-compose.cloudflare.yml`:

```yaml
cloudflare-tunnel:
  image: cloudflare/cloudflared:latest
  command: tunnel --no-autoupdate run --token ${CLOUDFLARE_TUNNEL_TOKEN}
  environment:
    - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}
  depends_on:
    - web
```

It's an optional overlay, so it only starts when you include it with `-f docker-compose.cloudflare.yml`.

## Deployment Options

### HTTP only (Default)
```bash
docker-compose up
```
- No HTTPS
- Direct access on port 5000

### With Cloudflare Tunnel
```bash
docker-compose -f docker-compose.yml -f docker-compose.cloudflare.yml up
```
- HTTPS via Cloudflare Tunnel
- Secure cookies enabled
- ProxyFix middleware enabled

### GPU + Cloudflare Tunnel
```bash
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.cloudflare.yml up
```

### CPU + Cloudflare Tunnel
```bash
docker-compose -f docker-compose.yml -f docker-compose.cpu.yml -f docker-compose.cloudflare.yml up
```

## Troubleshooting

### Tunnel Won't Connect

**Symptom**: `cloudflare-tunnel` container fails to start or logs connection errors

**Solutions**:
1. Verify token is correct in `.env`:
   ```bash
   echo $CLOUDFLARE_TUNNEL_TOKEN
   ```
2. Check tunnel status in Cloudflare dashboard
3. Ensure `web` service is running (tunnel depends on it)
4. Check logs:
   ```bash
   docker-compose logs cloudflare-tunnel
   ```

### SSL Certificate Invalid

**Symptom**: Browser shows SSL warning

**Solutions**:
1. Verify DNS record points to tunnel:
   ```bash
   dig docuflux.example.com
   # Should show Cloudflare IP addresses
   ```
2. Wait for DNS propagation (can take up to 24 hours, usually <5 minutes)
3. Clear browser cache and retry

### WebSocket Connection Fails

**Symptom**: Real-time job updates don't work over HTTPS

**Solutions**:
1. Verify `BEHIND_PROXY=true` in `.env`
2. Check CSP headers allow wss://
3. Ensure Socket.IO client is using correct protocol (auto-detected)
4. Check browser console for WebSocket errors

### Session Cookies Not Secure

**Symptom**: Cookies transmitted over HTTP instead of HTTPS-only

**Solutions**:
1. Verify `SESSION_COOKIE_SECURE=true` in `.env`
2. Restart web service after changing env vars
3. Clear browser cookies and retry

## Advanced Configuration

### Custom Domain Paths

Route specific paths to different services:

```yaml
# cloudflare/config.yml
ingress:
  - hostname: docuflux.example.com
    path: /api/*
    service: http://web:5000

  - hostname: docuflux.example.com
    path: /*
    service: http://web:5000

  - service: http_status:404
```

### Multiple Tunnels

For high availability, create multiple tunnels:

```bash
cloudflared tunnel create docuflux-tunnel-1
cloudflared tunnel create docuflux-tunnel-2

cloudflared tunnel route dns docuflux-tunnel-1 docuflux.example.com
cloudflared tunnel route dns docuflux-tunnel-2 docuflux.example.com
```

### Access Control

Restrict access to specific users/IPs via Cloudflare Access:

1. Go to Cloudflare Dashboard → Zero Trust → Access
2. Create an application for your domain
3. Add policies (e.g., require email, IP ranges)

## Cost

**Cloudflare Tunnel is FREE** for:
- Unlimited tunnels
- Unlimited bandwidth
- Basic DDoS protection
- SSL certificates

Paid features (optional):
- Advanced WAF rules
- Custom certificates
- Additional Access policies

## Next Steps

After setting up HTTPS:

1. ✅ **Epic 22 Complete**: HTTPS with Cloudflare Tunnel
2. 🔜 **Epic 23**: Encryption at rest (file encryption)
3. 🔜 **Epic 24**: Redis TLS (encryption in transit)
4. 🔜 **Epic 25**: Certificate management (Certbot alternative)

## Resources

- [Cloudflare Tunnel Documentation](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
- [cloudflared CLI Reference](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/tunnel-guide/)
- [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/)

## Support

For issues with:
- **Cloudflare Tunnel**: Check [Cloudflare Community](https://community.cloudflare.com/)
- **DocuFlux HTTPS**: Open issue at [GitHub](https://github.com/your-repo/pandoc-web/issues)
