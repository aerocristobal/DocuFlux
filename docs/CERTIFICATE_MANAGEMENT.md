# Certificate Management Guide

**Epic 24.4: Certificate Management for Redis TLS**

This guide covers TLS certificate management for DocuFlux, including generation, renewal, and distribution.

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
- **Redis**: Encrypts inter-service communication (web ↔ Redis ↔ worker)
- **HTTPS**: Public-facing web traffic (via Cloudflare Tunnel)

### Certificate Types

| Certificate | Purpose | Location | Validity | Renewal |
|-------------|---------|----------|----------|---------|
| Redis CA | Internal PKI root | `certs/redis/ca.crt` | 10 years (dev) | Manual |
| Redis Server | Redis TLS | `certs/redis/redis.crt` | 10 years (dev) | Manual |

---

## Development Certificates

### Initial Setup

Generate self-signed certificates for local development:

```bash
# Generate certificates (10-year validity)
./scripts/generate-redis-certs.sh

# Verify generation
ls -la certs/redis/
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
- **Distribution**: Mounted to all services at `/certs/redis/ca.crt`

**Redis Server Certificate (`redis.crt`)**
- **Subject**: `/C=US/ST=California/L=San Francisco/O=DocuFlux/OU=Development/CN=redis`
- **SANs**: DNS:redis, DNS:localhost, IP:127.0.0.1
- **Key Usage**: digitalSignature, keyEncipherment, serverAuth

### Verification

```bash
# Verify certificate chain
openssl verify -CAfile certs/redis/ca.crt certs/redis/redis.crt
# Expected: certs/redis/redis.crt: OK

# Check certificate details
openssl x509 -in certs/redis/redis.crt -noout -text

# Check expiration
openssl x509 -in certs/redis/redis.crt -noout -dates
# notBefore: ...
# notAfter: ...  (should be ~10 years from generation)
```

---

## Production Certificates

> **Note:** HTTPS in production is handled automatically by Cloudflare Tunnel. No Let's Encrypt or Certbot setup is needed.

### Commercial CA (for Redis TLS or non-Cloudflare deployments)

Purchase certificates from a commercial CA (DigiCert, GlobalSign, etc.):

1. **Generate CSR:**
   ```bash
   openssl req -new -key certs/redis/redis.key -out certs/redis/redis.csr
   ```

2. **Submit CSR to CA** and receive signed certificate

3. **Install certificate:**
   ```bash
   cp signed-cert.crt certs/redis/redis.crt
   cp ca-bundle.crt certs/redis/ca.crt
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
openssl x509 -in certs/redis/redis.crt -noout -dates
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
openssl x509 -in certs/redis/redis.crt -noout -ext subjectAltName

# Should include:
# DNS:redis, DNS:localhost, IP:127.0.0.1

# If missing, regenerate certificates:
rm -rf certs/redis/*.crt certs/redis/*.key
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
openssl x509 -in certs/redis/redis.crt -noout -dates
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
chmod 400 certs/redis/*.key
chmod 444 certs/redis/*.crt
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

---

## Related Documentation

- **Certificate Generation**: `scripts/generate-redis-certs.sh`
- **Certificate Renewal**: `scripts/renew-redis-certs.sh`
- **Redis TLS**: `certs/README.md`
- **Epic 24**: Encryption in Transit with Redis TLS
