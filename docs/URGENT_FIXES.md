# Urgent Fixes Required

**Date**: 2026-01-17
**Priority**: P0 - Critical

## Summary

Two critical issues identified that require immediate attention:

1. **24 Security Vulnerabilities** (GitHub Code Scanning)
2. **Docker Build Failure** (PyTorch version conflict)

---

## Issue 1: Docker Build Failure

### Problem

Docker build fails with dependency conflict:

```
ERROR: Cannot install marker-pdf and torch==2.2.0 because these package versions have conflicting dependencies.

The conflict is caused by:
    torch 2.2.0 (specified in requirements-true.txt)
    marker-pdf 1.10.0 depends on torch<3.0.0 and >=2.7.0
```

### Root Cause

File: `worker/requirements-true.txt`

```python
# Lines 15-17 pin old PyTorch version
torch==2.2.0          # marker-pdf needs >=2.7.0
torchvision==0.17.0
torchaudio==2.2.0

# Line 21 needs newer PyTorch
marker-pdf[full]==1.10.0  # Requires torch>=2.7.0
```

### Solution

Let `marker-pdf[full]` manage PyTorch dependencies (it includes the correct versions):

**Update worker/requirements-true.txt**:

```python
# GPU-enabled worker requirements
# Includes full Marker dependencies with CUDA/PyTorch GPU support

celery==5.3.4
redis==5.0.1
requests==2.32.4
flask==3.0.0
flask-socketio==5.3.6
gevent==23.9.1
gevent-websocket==0.10.1
prometheus-client==0.19.0
cryptography==42.0.0

# Marker with full GPU support
# This includes PyTorch, torchvision, torchaudio with CUDA support
marker-pdf[full]==1.10.0

# transformers (specify separately for compatibility)
transformers==4.38.0
```

**Explanation**: The `marker-pdf[full]` package automatically installs compatible versions of torch, torchvision, and torchaudio. By removing the explicit pins, we let marker-pdf choose the right versions (torch>=2.7.0).

### Quick Fix

```bash
# 1. Update requirements-true.txt
cat > worker/requirements-true.txt <<'EOF'
# GPU-enabled worker requirements
celery==5.3.4
redis==5.0.1
requests==2.32.4
flask==3.0.0
flask-socketio==5.3.6
gevent==23.9.1
gevent-websocket==0.10.1
prometheus-client==0.19.0
cryptography==42.0.0

# Marker with full GPU support (includes PyTorch with CUDA)
marker-pdf[full]==1.10.0
transformers==4.38.0
EOF

# 2. Rebuild
docker-compose --profile gpu up --build -d
```

---

## Issue 2: 24 Security Vulnerabilities

### Summary by Severity

| Severity | Count | Type |
|----------|-------|------|
| **HIGH** | 23 | Path Injection (19) + Clear-text Logging (4) |
| **MEDIUM** | 1 | Stack Trace Exposure |

### Detailed Breakdown

#### 1. Path Injection - 19 instances (HIGH)

**CWE**: CWE-22/23/36
**Risk**: Attackers can read/write arbitrary files

**Affected Files**:
- `web/app.py`: Lines 240, 256-257, 541-542, 574, 580-581, 590, 596, 607, 645-646
- `web/encryption.py`: Lines 181, 185, 192, 193
- `worker/encryption.py`: Lines 181, 185, 192, 193

**Example Vulnerability**:
```python
# web/app.py:240
job_dir = os.path.join(OUTPUT_FOLDER, job_id)  # job_id not validated!

# Attack:
job_id = "../../etc/passwd"
# Results in path: data/outputs/../../etc/passwd
```

**Fix Required**: Validate job_id is a proper UUID, sanitize filenames, use secure path joining.

#### 2. Clear-text Logging of Secrets - 4 instances (HIGH)

**Affected Files**:
- `web/secrets.py`: Lines 196, 198
- `worker/secrets.py`: Lines 196, 198

**Example Vulnerability**:
```python
# secrets.py:196
logging.info(f"Loaded secrets: {', '.join(loaded_secrets)}")
# Logs: "Loaded secrets: SECRET_KEY, MASTER_ENCRYPTION_KEY"
```

**Fix Required**: Don't log secret names or values.

#### 3. Stack Trace Exposure - 1 instance (MEDIUM)

**CWE**: CWE-209
**Affected File**: `web/app.py`: Line 685

**Example Vulnerability**:
```python
# app.py:685
return jsonify({"error": "Internal server error", "details": str(error)}), 500
# Exposes file paths, stack traces to users
```

**Fix Required**: Generic errors in production, detailed only in development.

### Implementation Plan

See **docs/SECURITY_FIXES.md** for complete user stories and implementation details.

**Quick Implementation Order**:

1. **Day 1**: Fix Path Injection (Epic 26.1)
   - Create path_security.py modules
   - Update all file operations
   - Add comprehensive tests

2. **Day 2**: Fix Secrets Logging (Epic 26.2)
   - Remove secret logging
   - Add log scrubbing
   - Update test output

3. **Day 2**: Fix Stack Trace Exposure (Epic 26.3)
   - Update error handlers
   - Add error ID generation
   - Environment-based error details

4. **Day 3**: Verification
   - Run full test suite
   - Verify GitHub alerts = 0
   - Update documentation

---

## Immediate Actions

### For Docker Build (5 minutes)

```bash
# Update requirements file
nano worker/requirements-true.txt
# Remove torch==2.2.0, torchvision==0.17.0, torchaudio==2.2.0 lines
# Keep only marker-pdf[full]==1.10.0

# Rebuild
docker-compose down
docker-compose --profile gpu up --build -d
```

### For Security Vulnerabilities (2-3 days)

```bash
# Create Epic 26 branch
git checkout -b epic-26-security-fixes

# Implement fixes per SECURITY_FIXES.md
# Day 1: Path injection fixes
# Day 2: Secrets logging + error handling
# Day 3: Testing and verification

# Verify fixes
gh api repos/:owner/:repo/code-scanning/alerts --jq '[.[] | select(.state == "open")] | length'
# Target: 0
```

---

## Risk Assessment

### Docker Build Issue
- **Impact**: HIGH - Application cannot deploy
- **Urgency**: CRITICAL - Blocks all operations
- **Effort**: 5 minutes
- **Priority**: Fix immediately

### Security Vulnerabilities
- **Impact**: CRITICAL - Data breach risk, unauthorized file access
- **Urgency**: HIGH - Active code scanning alerts
- **Effort**: 2-3 days
- **Priority**: Start today, complete within 1 week

---

## Next Steps

1. ✅ **Fix Docker build** (now)
   ```bash
   # Edit worker/requirements-true.txt
   # Remove explicit torch pins
   # Rebuild containers
   ```

2. ⏳ **Review SECURITY_FIXES.md** (1 hour)
   - Understand vulnerability details
   - Review proposed solutions
   - Approve implementation approach

3. ⏳ **Implement Epic 26** (2-3 days)
   - Follow SECURITY_FIXES.md user stories
   - Test each fix thoroughly
   - Verify GitHub alerts resolve

4. ⏳ **Deploy fixes** (when ready)
   - Merge security fixes
   - Deploy to production
   - Monitor for issues

---

## References

- **Security Fixes Detail**: `docs/SECURITY_FIXES.md`
- **GitHub Code Scanning**: https://github.com/aerocristobal/DocuFlux/security/code-scanning
- **Docker Build Logs**: `/tmp/claude/.../b01500e.output`
