# Certificate Management Guide

This guide covers TLS certificate management for DocuFlux, including generation, renewal, and distribution.

**Status:** Redis TLS is an opt-in overlay (`docker-compose.tls.yml`), not part of the default deployment. It currently covers `web`, `worker` and `beat` only; `worker-light` and `surya-controller` are not yet switched to `rediss://` (see [TLS Enforcement](#tls-enforcement-story-41a41b)).

## Table of Contents

- [Overview](#overview)
- [Development Certificates](#development-certificates)
- [Production Certificates](#production-certificates)
- [Certificate Renewal](#certificate-renewal)
- [Service Reload](#service-reload)
- [Monitoring & Alerting](#monitoring--alerting)
- [Troubleshooting](#troubleshooting)

---

## Overview

DocuFlux uses TLS certificates for:
- **Redis** (optional overlay): Encrypts inter-service communication (web ↔ Redis ↔ worker/beat)
- **HTTPS**: Public-facing web traffic is terminated outside Compose (reverse proxy or an operator-run Cloudflare Tunnel; the `docker-compose.cloudflare.yml` overlay was removed)

### Certificate Types

| Certificate | Purpose | Location | Validity | Renewal |
|-------------|---------|----------|----------|---------|
| Redis CA | Internal PKI root | `deploy/certs/redis/ca.crt` | 10 years (dev) | Manual |
| Redis Server | Redis TLS | `deploy/certs/redis/redis.crt` | 10 years (dev) | Manual |

### TLS Enforcement (Story 4.1a/4.1b)

Under the `docker-compose.tls.yml` overlay, Redis is TLS-only, not
TLS-optional: the server is started with `--port 0 --tls-port 6380`, which
disables the plaintext listener entirely rather than accepting both. A
client that does not connect with a `rediss://` URL and trust the CA
(`REDIS_TLS_CA_CERTS=/certs/redis/ca.crt`) cannot reach Redis at all under
this profile. The overlay sets `--tls-auth-clients no`, so client
certificates are not required (server authentication only); the client
cert/key settings (`REDIS_TLS_CERTFILE`/`REDIS_TLS_KEYFILE`) are optional.

**Overlay coverage.** `docker-compose.tls.yml` rewrites the Redis URLs and
sets the CA only for `web`, `worker` and `beat`. `worker-light` and
`surya-controller` keep `redis://…:6379`, which no longer exists once the
plaintext port is disabled, so running the overlay as shipped leaves those
two services unable to reach Redis. Until the overlay is extended, add the
same `rediss://…:6380` URLs and `REDIS_TLS_CA_CERTS` to them in a local
override. The controller also builds its Redis clients with
`redis.Redis.from_url` directly rather than through
`shared/redis_client.py`, so it needs the CA to be trusted by the system
store or the client code changed.

On the client side, every Redis-connecting code path — `web/app.py`,
`worker/tasks/__init__.py`, and `worker/warmup.py` — constructs its client
exclusively through `shared/redis_client.py`'s `create_redis_client()` /
`create_sentinel_client()`. Those factories set `ssl_cert_reqs=required`
plus `ssl_ca_certs`/`ssl_certfile`/`ssl_keyfile` whenever the URL scheme is
`rediss://`. No consumer in the codebase constructs a raw, unverified
Redis client — this is asserted directly by
`tests/unit/test_validation_metrics_warmup.py::TestWarmup::test_redis_client_created_via_tls_aware_factory`
and `tests/unit/test_redis_sentinel.py`.

---

## Development Certificates

### Initial Setup

Generate self-signed certificates for local development:

```bash
# Generate certificates (10-year validity)
./scripts/generate-redis-certs.sh

# Verify generation
ls -la deploy/certs/redis/
# Expected files:
#   ca.crt        - Certificate Authority (public)
#   ca.key        - CA private key (SECRET)
#   redis.crt     - Redis server certificate (public)
#   redis.key     - Redis server private key (SECRET)
```

### Certificate Details

**CA Certificate (`ca.crt`)**
- **Subject**: `/C=US/ST=California/L=San Francisco/O=DocuFlux/OU=Development/CN=DocuFlux CA`
- **Purpose**: Root certificate for internal PKI
- **Distribution**: Mounted (`./deploy/certs:/certs:ro`) into `web`, `worker`, `worker-light` and `beat` at `/certs/redis/ca.crt`; not mounted into `surya-controller`

**Redis Server Certificate (`redis.crt`)**
- **Subject**: `/C=US/ST=California/L=San Francisco/O=DocuFlux/OU=Development/CN=redis`
- **SANs**: DNS:redis, DNS:localhost, IP:127.0.0.1
- **Key Usage**: digitalSignature, keyEncipherment, serverAuth

### Verification

```bash
# Verify certificate chain
openssl verify -CAfile deploy/certs/redis/ca.crt deploy/certs/redis/redis.crt
# Expected: deploy/certs/redis/redis.crt: OK

# Check certificate details
openssl x509 -in deploy/certs/redis/redis.crt -noout -text

# Check expiration
openssl x509 -in deploy/certs/redis/redis.crt -noout -dates
# notBefore: ...
# notAfter: ...  (should be ~10 years from generation)
```

---

## Production Certificates

> **Note:** Public HTTPS is terminated outside Compose (reverse proxy, or a Cloudflare Tunnel you run yourself; see `deploy/cloudflare/` and the deprecated [archive/CLOUDFLARE_TUNNEL_SETUP.md](archive/CLOUDFLARE_TUNNEL_SETUP.md)). No Let's Encrypt or Certbot setup is bundled.

### Commercial CA (for Redis TLS or non-Cloudflare deployments)

Purchase certificates from a commercial CA (DigiCert, GlobalSign, etc.):

1. **Generate CSR:**
   ```bash
   openssl req -new -key deploy/certs/redis/redis.key -out deploy/certs/redis/redis.csr
   ```

2. **Submit CSR to CA** and receive signed certificate

3. **Install certificate:**
   ```bash
   cp signed-cert.crt deploy/certs/redis/redis.crt
   cp ca-bundle.crt deploy/certs/redis/ca.crt
   ```

4. **Reload services:**
   ```bash
   docker-compose restart redis web worker beat
   ```

**Pros:**
- Longer validity (1-3 years)
- Dedicated support
- Extended validation options

**Cons:**
- Expensive ($200-$2000/year)
- Manual renewal process

---

## Certificate Renewal

### Manual Renewal (Development)

Check and renew self-signed development certificates:

```bash
# Check expiration (automatic check)
./scripts/renew-redis-certs.sh

# Output:
# Certificate expires: Dec 31 23:59:59 2035 GMT
# Days until expiration: 3650
# ✓ Certificate is valid for 3650 more days.
# No renewal needed (threshold: 30 days).
```

If renewal is needed:

```bash
# Backup existing certificates automatically
./scripts/renew-redis-certs.sh

# Reload services to pick up new certificates
docker-compose restart redis web worker beat
```

---

## Service Reload

```bash
docker-compose restart
docker-compose restart redis  # individual service
```

---

## Monitoring & Alerting

### Prometheus Metrics

Monitor certificate expiration via Prometheus:

```prometheus
# Certificate expiration alert (30 days before expiry)
- alert: RedisCertificateExpiringSoon
  expr: (redis_tls_cert_expiry_seconds - time()) < 2592000  # 30 days
  for: 1h
  labels:
    severity: warning
  annotations:
    summary: "Redis TLS certificate expiring soon"
    description: "Certificate expires in {{ $value | humanizeDuration }}"
```

### Manual Expiration Check

Check certificate expiration manually:

```bash
# Quick check
./scripts/renew-redis-certs.sh

# Detailed check
openssl x509 -in deploy/certs/redis/redis.crt -noout -dates
```

### Cron Job (Automated Monitoring)

Set up automated certificate renewal checks:

```bash
# Add to crontab:
# Check certificates daily at 2 AM
0 2 * * * /path/to/docuflux/scripts/renew-redis-certs.sh >> /var/log/cert-renewal.log 2>&1
```

---

## Troubleshooting

### Certificate Verification Failed

**Symptom:**
```
Error: certificate verify failed: self signed certificate in certificate chain
```

**Cause:** Redis client doesn't trust the CA certificate.

**Solution:**
```bash
# Verify CA is mounted correctly
docker-compose exec web ls -la /certs/redis/ca.crt

# Verify Redis TLS configuration
docker-compose logs redis | grep TLS

# Test connection
docker-compose exec web redis-cli --tls \
  --cert /certs/redis/redis.crt \
  --key /certs/redis/redis.key \
  --cacert /certs/redis/ca.crt \
  -h redis ping
# Expected: PONG
```

### Connection Refused

**Symptom:**
```
Error: Connection refused
```

**Cause:** Redis not running with TLS, or port configuration incorrect.

**Solution:**
```bash
# Check Redis is running
docker-compose ps redis

# Verify Redis command line
docker-compose logs redis | head -20

# Expected:
# --tls-port 6379
# --port 0
# --tls-cert-file /certs/redis/redis.crt
```

### Hostname Mismatch

**Symptom:**
```
Error: Hostname 'redis' doesn't match certificate
```

**Cause:** Certificate doesn't include correct Subject Alternative Name.

**Solution:**
```bash
# Check certificate SANs
openssl x509 -in deploy/certs/redis/redis.crt -noout -ext subjectAltName

# Should include:
# DNS:redis, DNS:localhost, IP:127.0.0.1

# If missing, regenerate certificates:
rm -rf deploy/certs/redis/*.crt deploy/certs/redis/*.key
./scripts/generate-redis-certs.sh
docker-compose restart redis web worker beat
```

### Certificate Expired

**Symptom:**
```
Error: certificate has expired
```

**Solution:**
```bash
openssl x509 -in deploy/certs/redis/redis.crt -noout -dates
./scripts/renew-redis-certs.sh
docker-compose restart redis web worker beat
```

### Permission Denied

**Symptom:**
```
Error: Permission denied reading /certs/redis/redis.key
```

**Cause:** Incorrect file permissions on private key.

**Solution:**
```bash
chmod 400 deploy/certs/redis/*.key
chmod 444 deploy/certs/redis/*.crt
```

---

## Best Practices

### Security

- ✅ **DO**: Use strong key sizes (2048-bit minimum, 4096-bit recommended)
- ✅ **DO**: Set strict file permissions (400 for `.key`, 444 for `.crt`)
- ✅ **DO**: Store private keys in secrets management (production)
- ✅ **DO**: Rotate certificates before expiration
- ❌ **DON'T**: Commit private keys to version control
- ❌ **DON'T**: Use self-signed certificates in production
- ❌ **DON'T**: Reuse certificates across environments

### Operations

- ✅ **DO**: Backup certificates before renewal
- ✅ **DO**: Test certificate changes in staging first
- ✅ **DO**: Monitor certificate expiration
- ✅ **DO**: Document certificate rotation procedures
- ❌ **DON'T**: Skip health checks during reload
- ❌ **DON'T**: Restart all services simultaneously

### Compliance

- **SOC 2**: Certificate rotation every 12 months
- **PCI DSS**: Strong cryptography (TLS 1.2+, 2048-bit keys)
- **HIPAA**: Encryption in transit for PHI
- **GDPR**: Encryption for personal data
- **OSCAL SC-8** (Transmission Confidentiality and Integrity): mapped in
  `oscal/ssp.json` and `oscal/component-definition.json` (DocuFlux Redis
  Service component) — status `implemented`, citing this document plus
  Stories 4.1a/4.1b's enforcement and test coverage.

---

## Related Documentation

- **Certificate Generation**: `scripts/generate-redis-certs.sh`
- **Certificate Renewal**: `scripts/renew-redis-certs.sh`
- **Redis TLS**: `deploy/certs/README.md`
- **Redis TLS overlay**: `docker-compose.tls.yml`
