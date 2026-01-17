# Security Vulnerability Fixes

**Date**: 2026-01-17
**Source**: GitHub Code Scanning Alerts
**Total Open Vulnerabilities**: 24

## Executive Summary

GitHub's CodeQL analysis identified 24 security vulnerabilities in the DocuFlux codebase:
- **19 HIGH severity**: Path Injection (CWE-22/23/36)
- **4 HIGH severity**: Clear-text Logging of Sensitive Information
- **1 MEDIUM severity**: Stack Trace Exposure (CWE-209)

This document provides detailed user stories and implementation plans to remediate all vulnerabilities.

---

## Epic 26: Security Vulnerability Remediation

**Status**: 🔵 Planned | **Priority**: P0 - Critical | **Effort**: 2-3 days

**Goal**: Fix all security vulnerabilities identified by GitHub code scanning to achieve zero critical/high severity issues.

### Story 26.1: Fix Path Injection Vulnerabilities (19 instances)

**Severity**: HIGH
**CWE**: CWE-22 (Path Traversal), CWE-23 (Relative Path Traversal), CWE-36 (Absolute Path Traversal)
**Risk**: Attackers could read/write arbitrary files on the system, potentially accessing sensitive data or overwriting critical files.

#### Vulnerability Details

**Description**: Uncontrolled data used in path expressions. User-controlled data (job_id, file paths) is used to construct file system paths without proper validation, allowing path traversal attacks.

**Attack Scenarios**:
```python
# Attacker supplies malicious job_id
job_id = "../../etc/passwd"
file_path = os.path.join(OUTPUT_FOLDER, job_id, "output.pdf")
# Results in: data/outputs/../../etc/passwd/output.pdf
# Which resolves to: /etc/passwd/output.pdf
```

**Affected Locations**:

**web/app.py** (11 instances):
- Line 240: `decrypt_file_to_temp()` - encrypted_path construction
- Line 256: File read in `/download/<job_id>`
- Line 257: `os.path.join(job_dir, target_file)`
- Line 541-542: ZIP download - file path construction
- Lines 574, 580-581, 590, 596, 607: File operations with job_id
- Lines 645-646: File deletion operations

**web/encryption.py** (4 instances):
- Line 181: `encrypt_file()` - input_path
- Line 185: `encrypt_file()` - output_path
- Line 192: `decrypt_file()` - input_path
- Line 193: `decrypt_file()` - output_path

**worker/encryption.py** (4 instances):
- Lines 181, 185, 192, 193: Same as web/encryption.py

#### User Story

**As a** security engineer
**I want** all file path operations to validate and sanitize user input
**So that** attackers cannot access files outside designated directories

**Acceptance Criteria**:

```gherkin
Feature: Path Injection Prevention
  As a security engineer
  I need to prevent path traversal attacks
  So that users cannot access files outside allowed directories

  Scenario: Job ID validation prevents path traversal
    Given a user submits a conversion job
    When the job_id contains path traversal sequences like "../"
    Then the system rejects the job with a validation error
    And no file operations are performed

  Scenario: Filename sanitization prevents absolute paths
    Given a user uploads a file with a malicious filename
    When the filename is "/etc/passwd" or contains ".."
    Then the filename is sanitized to remove dangerous characters
    And the file is saved with a safe name

  Scenario: Path construction is secure
    Given a validated job_id and filename
    When constructing file paths with os.path.join
    Then the resulting path is within the allowed directory
    And any attempt to escape is detected and blocked
```

#### Technical Implementation

**Step 1: Create Path Validation Module**

Create `web/path_security.py` and `worker/path_security.py`:

```python
"""
Secure Path Validation and Sanitization

Prevents path traversal attacks (CWE-22, CWE-23, CWE-36)
"""

import os
from pathlib import Path
from typing import Optional

class PathTraversalError(ValueError):
    """Raised when path traversal is detected."""
    pass

def validate_job_id(job_id: str) -> bool:
    """
    Validate that job_id is a valid UUID with no path traversal.

    Args:
        job_id: Job identifier to validate

    Returns:
        True if valid

    Raises:
        ValueError: If job_id is invalid or contains traversal sequences
    """
    import uuid

    if not job_id:
        raise ValueError("Job ID cannot be empty")

    # Check for path traversal sequences
    if '..' in job_id or '/' in job_id or '\\' in job_id:
        raise PathTraversalError(f"Job ID contains invalid characters: {job_id}")

    # Validate UUID format
    try:
        uuid.UUID(str(job_id))
    except ValueError:
        raise ValueError(f"Invalid job ID format: {job_id}")

    return True

def sanitize_filename(filename: str, max_length: int = 255) -> str:
    """
    Sanitize filename to prevent path traversal and other attacks.

    Args:
        filename: Original filename
        max_length: Maximum allowed filename length

    Returns:
        Sanitized filename

    Raises:
        ValueError: If filename is empty after sanitization
    """
    if not filename:
        raise ValueError("Filename cannot be empty")

    # Remove path components
    filename = os.path.basename(filename)

    # Remove null bytes
    filename = filename.replace('\x00', '')

    # Replace path separators
    filename = filename.replace('/', '_').replace('\\', '_')

    # Remove control characters
    filename = ''.join(c for c in filename if ord(c) >= 32)

    # Truncate to max length
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        filename = name[:max_length - len(ext)] + ext

    if not filename:
        raise ValueError("Filename is empty after sanitization")

    return filename

def secure_join(*paths: str, base_dir: str) -> str:
    """
    Securely join path components and validate result is within base directory.

    This prevents path traversal attacks by ensuring the resolved path
    is within the allowed base directory.

    Args:
        *paths: Path components to join
        base_dir: Base directory that result must be within

    Returns:
        Absolute path within base_dir

    Raises:
        PathTraversalError: If resolved path is outside base_dir
    """
    # Resolve base directory to absolute path
    base = Path(base_dir).resolve()

    # Join all path components
    target = Path(base_dir).joinpath(*paths).resolve()

    # Check if target is within base directory
    try:
        target.relative_to(base)
    except ValueError:
        raise PathTraversalError(
            f"Path traversal detected: {target} is outside {base}"
        )

    return str(target)

def validate_file_path(file_path: str, allowed_dirs: list[str]) -> bool:
    """
    Validate that a file path is within allowed directories.

    Args:
        file_path: Path to validate
        allowed_dirs: List of allowed base directories

    Returns:
        True if path is within allowed directories

    Raises:
        PathTraversalError: If path is outside allowed directories
    """
    file_path = Path(file_path).resolve()

    for allowed_dir in allowed_dirs:
        base = Path(allowed_dir).resolve()
        try:
            file_path.relative_to(base)
            return True
        except ValueError:
            continue

    raise PathTraversalError(
        f"Path {file_path} is not within allowed directories: {allowed_dirs}"
    )
```

**Step 2: Update web/app.py**

Replace unsafe path operations:

```python
# BEFORE (vulnerable):
job_dir = os.path.join(OUTPUT_FOLDER, job_id)

# AFTER (secure):
from path_security import validate_job_id, secure_join, PathTraversalError

validate_job_id(job_id)  # Raises ValueError if invalid
job_dir = secure_join(job_id, base_dir=OUTPUT_FOLDER)
```

**Step 3: Update encryption.py**

Add path validation to encryption functions:

```python
# In encrypt_file() and decrypt_file():
from path_security import validate_file_path

# Validate paths before use
validate_file_path(input_path, allowed_dirs=[UPLOAD_FOLDER, OUTPUT_FOLDER])
validate_file_path(output_path, allowed_dirs=[UPLOAD_FOLDER, OUTPUT_FOLDER])
```

**Step 4: Update existing validation.py**

Integrate path security into existing validators:

```python
# web/validation.py
from path_security import validate_job_id, sanitize_filename, PathTraversalError

# Update sanitize_filename to use new implementation
# Add path validation to require_valid_uuid decorator
```

**Files to Modify**:
- `web/path_security.py` (new) - Path validation module (~200 lines)
- `worker/path_security.py` (new) - Same for worker (~200 lines)
- `web/app.py` - Add path validation to all file operations (~50 lines modified)
- `web/encryption.py` - Add path validation (~20 lines modified)
- `worker/encryption.py` - Add path validation (~20 lines modified)
- `web/validation.py` - Integrate path security (~30 lines modified)

**Testing**:
```bash
# Test path traversal prevention
pytest tests/security/test_path_injection.py -v

# Test cases:
# - Valid job_id (UUID) passes validation
# - job_id with "../" is rejected
# - job_id with absolute path is rejected
# - filename with "/" is sanitized
# - secure_join prevents escaping base directory
```

**Definition of Done**:
- [ ] Path validation module created and tested
- [ ] All 19 path injection vulnerabilities fixed
- [ ] Unit tests cover all attack vectors
- [ ] GitHub code scanning shows 0 path injection alerts
- [ ] No regression in functionality

---

### Story 26.2: Fix Clear-Text Logging of Secrets (4 instances)

**Severity**: HIGH
**Risk**: Sensitive information (secrets, keys) logged to stdout/files, accessible to anyone with log access.

#### Vulnerability Details

**Description**: Secrets module logs loaded secrets to stdout, potentially exposing encryption keys and API tokens.

**Affected Locations**:

**web/secrets.py**:
- Line 196: Logs secret names in `validate_secrets_at_startup()`
- Line 198: Logs secret validation result

**worker/secrets.py**:
- Lines 196, 198: Same as web/secrets.py

**Current Vulnerable Code**:
```python
logging.info(f"Loaded secrets: {', '.join(loaded_secrets)}")
```

**Attack Scenario**:
```bash
# Attacker gains access to logs
docker logs pandoc-web-web 2>&1 | grep "Loaded secrets"
# Output: Loaded secrets: SECRET_KEY, MASTER_ENCRYPTION_KEY, CELERY_SIGNING_KEY
# Attacker now knows which secrets are in use

# Worse case - if secrets are logged directly:
# Output: SECRET_KEY=abc123...
```

#### User Story

**As a** security engineer
**I want** secrets to never be logged in plain text
**So that** sensitive data cannot be exposed through log files

**Acceptance Criteria**:

```gherkin
Feature: Secure Secrets Logging
  As a security engineer
  I need to prevent secrets from appearing in logs
  So that sensitive data is not exposed

  Scenario: Secret names are not logged
    Given the application loads secrets at startup
    When secrets validation completes
    Then only a success/failure indicator is logged
    And no secret names or values appear in logs

  Scenario: Secret validation failures are logged safely
    Given a required secret is missing
    When secrets validation fails
    Then an error is logged with the secret name (allowed)
    But the default or provided value is never logged

  Scenario: Test output does not expose secrets
    Given the secrets module has a __main__ test block
    When python web/secrets.py is executed
    Then no actual secret values are printed
    And only placeholder text is shown
```

#### Technical Implementation

**Step 1: Update secrets.py logging**

```python
# BEFORE (vulnerable):
loaded_secrets = [name for name, value in secrets.items() if value]
logging.info(f"Loaded secrets: {', '.join(loaded_secrets)}")

# AFTER (secure):
secret_count = len([v for v in secrets.values() if v])
logging.info(f"✓ Loaded {secret_count} secrets successfully")
# Do not log secret names or values

# For debugging (development only):
if os.environ.get('DEBUG_SECRETS') == 'true' and not is_production:
    logging.debug("Secrets loaded (names only, debug mode):")
    for name, value in secrets.items():
        status = "SET" if value else "NOT SET"
        logging.debug(f"  - {name}: {status}")
```

**Step 2: Update test block**

```python
# BEFORE (vulnerable):
if __name__ == '__main__':
    secrets = validate_secrets_at_startup()
    print(f"Loaded {len(secrets)} secrets")

# AFTER (secure):
if __name__ == '__main__':
    try:
        secrets = validate_secrets_at_startup()
        print(f"\n✓ Secrets validation passed")
        print(f"✓ Loaded {len(secrets)} secrets (not showing values)")

        # Safe to show which secrets are configured
        for name in secrets.keys():
            status = "✓ SET" if secrets[name] else "✗ NOT SET"
            print(f"  {name}: {status}")
    except Exception as e:
        print(f"\n✗ Secrets validation failed: {e}")
        sys.exit(1)
```

**Step 3: Add log scrubbing middleware**

Create `web/log_security.py`:

```python
"""
Log Security - Scrub sensitive data from logs

Prevents accidental logging of secrets, tokens, keys
"""

import re
import logging

# Patterns to detect and scrub
SENSITIVE_PATTERNS = [
    (re.compile(r'(SECRET_KEY|API_KEY|TOKEN|PASSWORD)=[^\s]+'), r'\1=***REDACTED***'),
    (re.compile(r'(ghp_[a-zA-Z0-9]{36})'), r'***GITHUB_TOKEN***'),
    (re.compile(r'([a-f0-9]{64})'), r'***HEX_KEY***'),  # 256-bit hex keys
    (re.compile(r'Bearer [^\s]+'), r'Bearer ***REDACTED***'),
]

class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that scrubs sensitive data from log messages.
    """

    def filter(self, record):
        # Scrub message
        if isinstance(record.msg, str):
            for pattern, replacement in SENSITIVE_PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)

        # Scrub args
        if record.args:
            scrubbed_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    for pattern, replacement in SENSITIVE_PATTERNS:
                        arg = pattern.sub(replacement, arg)
                scrubbed_args.append(arg)
            record.args = tuple(scrubbed_args)

        return True

# Add to all handlers
for handler in logging.root.handlers:
    handler.addFilter(SensitiveDataFilter())
```

**Files to Modify**:
- `web/secrets.py` - Remove secret logging (~10 lines modified)
- `worker/secrets.py` - Remove secret logging (~10 lines modified)
- `web/log_security.py` (new) - Log scrubbing middleware (~80 lines)
- `worker/log_security.py` (new) - Same for worker (~80 lines)
- `web/app.py` - Import and enable log security (~5 lines)
- `worker/tasks.py` - Import and enable log security (~5 lines)

**Testing**:
```bash
# Test that secrets don't appear in logs
python web/secrets.py 2>&1 | grep -i "secret_key"
# Should NOT show secret values

# Test log scrubbing
pytest tests/security/test_log_security.py -v
```

**Definition of Done**:
- [ ] All 4 clear-text logging vulnerabilities fixed
- [ ] Log scrubbing filter implemented and active
- [ ] No secrets visible in application logs
- [ ] GitHub code scanning shows 0 clear-text logging alerts
- [ ] Test suite validates no secrets in logs

---

### Story 26.3: Fix Stack Trace Exposure (1 instance)

**Severity**: MEDIUM
**CWE**: CWE-209 (Information Exposure Through an Error Message)
**Risk**: Stack traces reveal internal application structure, file paths, and logic that aids attackers.

#### Vulnerability Details

**Description**: Exception stack traces exposed to users through HTTP responses.

**Affected Location**:
- `web/app.py` line 685: Error handler exposes exception details

**Current Vulnerable Code**:
```python
@app.errorhandler(500)
def internal_error(error):
    logging.error(f"Internal error: {error}")
    return jsonify({"error": "Internal server error", "details": str(error)}), 500
    # ^^^ "details": str(error) exposes stack trace
```

**Attack Scenario**:
```bash
# Attacker triggers an error
curl http://localhost:5000/download/invalid-uuid

# Response exposes internal details:
{
  "error": "Internal server error",
  "details": "File not found: /app/data/outputs/invalid-uuid/output.pdf"
}
# Attacker now knows:
# - Application directory structure (/app/data/outputs)
# - File naming conventions
# - Framework details (Flask stack trace)
```

#### User Story

**As a** security engineer
**I want** error messages to be generic for users
**So that** internal application details are not exposed

**Acceptance Criteria**:

```gherkin
Feature: Secure Error Handling
  As a security engineer
  I need to prevent information disclosure through error messages
  So that attackers cannot learn about internal application structure

  Scenario: Production errors are generic
    Given the application is running in production
    When an internal error occurs
    Then the user receives a generic error message
    And no stack trace or file paths are exposed
    And the full error is logged securely for debugging

  Scenario: Development errors are detailed
    Given the application is running in development
    When an error occurs
    Then detailed error information is shown (for debugging)
    But sensitive data is still scrubbed

  Scenario: Error codes enable support tracking
    Given an error occurs
    When the error response is sent
    Then a unique error ID is included
    And support staff can correlate user error ID with logs
```

#### Technical Implementation

**Step 1: Update error handlers**

```python
# web/app.py

import uuid
from datetime import datetime

def generate_error_id():
    """Generate unique error ID for tracking."""
    return f"ERR-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

@app.errorhandler(500)
def internal_error(error):
    error_id = generate_error_id()

    # Log full error with ID (for debugging)
    logging.error(f"[{error_id}] Internal error: {error}", exc_info=True)

    # Return generic error to user
    is_production = os.environ.get('FLASK_ENV', 'production') == 'production'

    if is_production:
        return jsonify({
            "error": "Internal server error",
            "error_id": error_id,
            "message": "An unexpected error occurred. Please contact support with this error ID."
        }), 500
    else:
        # Development: Show error details (but scrub secrets)
        from log_security import scrub_sensitive_data
        error_details = scrub_sensitive_data(str(error))

        return jsonify({
            "error": "Internal server error",
            "error_id": error_id,
            "details": error_details,
            "note": "Detailed errors shown in development only"
        }), 500

@app.errorhandler(Exception)
def handle_unexpected_error(error):
    """Catch-all for unexpected exceptions."""
    error_id = generate_error_id()
    logging.exception(f"[{error_id}] Unexpected error: {error}")

    return jsonify({
        "error": "An unexpected error occurred",
        "error_id": error_id
    }), 500
```

**Step 2: Update all error responses**

```python
# Example: File not found
@app.route('/download/<job_id>')
def download_file(job_id):
    try:
        validate_job_id(job_id)
        # ... file operations
    except PathTraversalError as e:
        # Specific validation error (safe to show)
        logging.warning(f"Path traversal attempt: {job_id}")
        return jsonify({"error": "Invalid job ID"}), 400
    except FileNotFoundError:
        # Generic error (don't reveal file paths)
        logging.error(f"File not found for job {job_id}")
        return jsonify({"error": "File not found"}), 404
    except Exception as e:
        # Unexpected error (use error handler)
        raise
```

**Files to Modify**:
- `web/app.py` - Update error handlers (~40 lines modified)
- `web/log_security.py` - Add scrub_sensitive_data function (~20 lines)

**Testing**:
```bash
# Test error handling
pytest tests/security/test_error_handling.py -v

# Test production mode hides details
FLASK_ENV=production pytest tests/security/test_error_handling.py::test_production_errors

# Test development mode shows details
FLASK_ENV=development pytest tests/security/test_error_handling.py::test_development_errors
```

**Definition of Done**:
- [ ] Stack trace exposure vulnerability fixed
- [ ] Generic errors in production, detailed in development
- [ ] Error IDs enable support tracking
- [ ] GitHub code scanning shows 0 stack trace exposure alerts
- [ ] All error paths tested

---

## Implementation Plan

### Phase 1: Path Injection Fixes (Day 1-2)
**Priority**: P0 - Critical

1. Create path_security.py modules (web + worker)
2. Update all file path operations in web/app.py
3. Update encryption.py modules (web + worker)
4. Write comprehensive tests
5. Verify all 19 alerts are resolved

**Estimated Effort**: 1.5 days

### Phase 2: Secrets Logging Fixes (Day 2)
**Priority**: P0 - Critical

1. Update secrets.py logging (web + worker)
2. Create log_security.py modules
3. Add log scrubbing filters
4. Update test blocks
5. Verify all 4 alerts are resolved

**Estimated Effort**: 0.5 days

### Phase 3: Error Handling Fixes (Day 3)
**Priority**: P1 - High

1. Update error handlers in web/app.py
2. Add error ID generation
3. Implement environment-based error detail levels
4. Test error scenarios
5. Verify alert is resolved

**Estimated Effort**: 0.5 days

### Phase 4: Verification & Documentation (Day 3)
**Priority**: P1 - High

1. Run full test suite
2. Verify GitHub code scanning shows 0 high/critical alerts
3. Update security documentation
4. Create security testing guidelines

**Estimated Effort**: 0.5 days

**Total Effort**: 2-3 days

---

## Verification

### Pre-Fix Baseline
```bash
# Current state
gh api repos/:owner/:repo/code-scanning/alerts --jq '[.[] | select(.state == "open")] | length'
# Expected: 24
```

### Post-Fix Validation
```bash
# After fixes
gh api repos/:owner/:repo/code-scanning/alerts --jq '[.[] | select(.state == "open")] | length'
# Expected: 0

# Verify by severity
gh api repos/:owner/:repo/code-scanning/alerts --jq '[.[] | select(.state == "open" and .rule.security_severity_level == "high")] | length'
# Expected: 0
```

### Test Coverage
```bash
# Run security-specific tests
pytest tests/security/ -v --cov=web --cov=worker --cov-report=term-missing

# Expected coverage:
# - path_security.py: 100%
# - log_security.py: 100%
# - Modified routes: 100%
```

---

## Related Documentation

- **GitHub Code Scanning**: https://github.com/aerocristobal/DocuFlux/security/code-scanning
- **OWASP Path Traversal**: https://owasp.org/www-community/attacks/Path_Traversal
- **CWE-22**: https://cwe.mitre.org/data/definitions/22.html
- **CWE-209**: https://cwe.mitre.org/data/definitions/209.html
