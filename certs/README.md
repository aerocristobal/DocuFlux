# TLS Certificates Directory

This directory stores TLS certificates and private keys for securing inter-service communication.

**Epic 24.1: Redis TLS Configuration with CA Certificates**

## Structure

```
certs/
├── redis/           # Redis TLS certificates
│   ├── ca.crt       # Certificate Authority (public)
│   ├── ca.key       # CA private key (SECRET - never commit!)
│   ├── redis.crt    # Redis server certificate (public)
│   └── redis.key    # Redis server private key (SECRET - never commit!)
└── .gitignore       # Prevents committing private keys
```

## Generating Certificates

### Development (Self-Signed)

For local development and testing:

```bash
# Generate self-signed certificates (10-year validity)
./scripts/generate-redis-certs.sh

# Verify generation
ls -la certs/redis/
```

## Certificate Details

### CA Certificate (`ca.crt`)
- **Purpose**: Root certificate authority for internal PKI
- **Validity**: 10 years (development), 90 days (production)
- **Distribution**: Must be trusted by all services connecting to Redis

### Redis Server Certificate (`redis.crt`)
- **Purpose**: Authenticates Redis server to clients
- **Subject Alternative Names**:
  - DNS: redis, localhost
  - IP: 127.0.0.1
- **Key Usage**: digitalSignature, keyEncipherment, serverAuth

## Security Notes

⚠️ **CRITICAL: Private Keys**
- `.key` files contain private keys and MUST NEVER be committed to version control
- `.gitignore` is configured to prevent accidental commits
- Store production private keys in secrets management (Docker secrets, HashiCorp Vault, etc.)

⚠️ **Certificate Rotation**
- Development certificates: Rotate annually or when compromised
- Check expiration: `openssl x509 -in certs/redis/redis.crt -noout -dates`

## Verifying TLS Connection

### Test Redis TLS Connection

```bash
# Using redis-cli
redis-cli \
  --tls \
  --cert certs/redis/redis.crt \
  --key certs/redis/redis.key \
  --cacert certs/redis/ca.crt \
  -h localhost \
  ping

# Expected output: PONG
```

### Test Python Connection

```python
import redis

client = redis.Redis(
    host='localhost',
    port=6379,
    ssl=True,
    ssl_ca_certs='certs/redis/ca.crt',
    ssl_certfile='certs/redis/redis.crt',
    ssl_keyfile='certs/redis/redis.key',
    ssl_cert_reqs='required'
)

print(client.ping())  # Should print True
```

## Troubleshooting

### Certificate Verification Failed

```
Error: certificate verify failed
```

**Solution**: Ensure CA certificate is properly installed and trusted:
```bash
openssl verify -CAfile certs/redis/ca.crt certs/redis/redis.crt
```

### Connection Refused

```
Error: Connection refused
```

**Solution**: Check Redis is running with TLS enabled:
```bash
docker-compose logs redis | grep TLS
```

### Hostname Mismatch

```
Error: Hostname 'redis' doesn't match certificate
```

**Solution**: Regenerate certificates with correct Subject Alternative Names.

## Related Documentation

- **Certificate Generation**: `scripts/generate-redis-certs.sh`
- **Epic 24**: Encryption in Transit with Redis TLS
- **Redis TLS Docs**: https://redis.io/docs/management/security/encryption/
