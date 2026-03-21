"""
Input Validation and Sanitization for DocuFlux

Provides validators and sanitizers to prevent injection attacks and ensure
data integrity throughout the application.

Epic 21.9: Input Validation and Sanitization
"""

import ipaddress
import os
import re
import socket
from functools import wraps
from urllib.parse import urlparse

from flask import request, jsonify
import logging


from uuid_validation import validate_uuid  # noqa: F811 — canonical impl in shared/


def validate_webhook_url(url, settings=None):
    """
    Validate a webhook URL against SSRF attacks.

    Checks: scheme, private/reserved IPs, cloud metadata endpoints,
    HTTPS requirement, and allowlist/blocklist.

    Returns:
        Tuple of (is_valid, error_message)
    """
    if settings is None:
        from config import settings as _settings
        settings = _settings

    if not url or not isinstance(url, str):
        return False, "webhook_url is required"

    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return False, "webhook_url must be a valid http/https URL"

    # HTTPS enforcement
    if settings.webhook_require_https and parsed.scheme != 'https':
        return False, "webhook_url must use HTTPS"

    hostname = parsed.hostname
    if not hostname:
        return False, "webhook_url must contain a valid hostname"

    # Allowlist check (if configured, only listed hosts are permitted)
    if settings.webhook_url_allowlist:
        allowed = {h.strip().lower() for h in settings.webhook_url_allowlist.split(',') if h.strip()}
        if hostname.lower() not in allowed:
            return False, f"hostname '{hostname}' is not in the webhook allowlist"

    # Blocklist check
    if settings.webhook_url_blocklist:
        blocked = {h.strip().lower() for h in settings.webhook_url_blocklist.split(',') if h.strip()}
        if hostname.lower() in blocked:
            return False, f"hostname '{hostname}' is blocked"

    # Resolve hostname and check for private/reserved IPs
    try:
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return False, f"cannot resolve hostname '{hostname}'"

    for family, _type, _proto, _canonname, sockaddr in addrinfos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
            return False, f"webhook_url resolves to a private/reserved IP ({ip})"

    return True, None


def sanitize_filename(filename, max_length=255):
    """
    Sanitize a filename to prevent path traversal and other attacks.

    Epic 21.9: Filename sanitization
    - Removes path traversal sequences (../, ..\\)
    - Replaces special characters with safe alternatives
    - Limits filename length
    - Preserves file extension

    Args:
        filename: Original filename
        max_length: Maximum allowed filename length (default: 255)

    Returns:
        Sanitized filename
    """
    if not filename:
        return "unnamed_file"

    # Remove any path components (/, \)
    filename = os.path.basename(filename)

    # Remove path traversal attempts
    filename = filename.replace("../", "").replace("..\\", "")

    # Replace dangerous characters with underscores
    # Allow: alphanumeric, dot, dash, underscore
    filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)

    # Prevent multiple consecutive dots (could hide extensions)
    filename = re.sub(r'\.{2,}', '.', filename)

    # Prevent hidden files (starting with dot)
    if filename.startswith('.'):
        filename = '_' + filename

    # Limit length while preserving extension
    if len(filename) > max_length:
        name, ext = os.path.splitext(filename)
        max_name_length = max_length - len(ext)
        filename = name[:max_name_length] + ext

    # Ensure non-empty filename
    if not filename or filename == '.':
        filename = "unnamed_file"

    return filename


def validate_job_id(job_id):
    """
    Validate job ID is a valid UUID.

    Args:
        job_id: Job ID to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not job_id:
        return False, "Job ID is required"

    if not validate_uuid(job_id):
        return False, f"Invalid job ID format: {job_id}"

    return True, None


from formats import validate_format  # re-export from shared module


def require_valid_uuid(param_name='job_id'):
    """
    Decorator to validate UUID parameter in route.

    Epic 21.9: UUID validation decorator

    Usage:
        @app.route('/api/job/<job_id>')
        @require_valid_uuid('job_id')
        def get_job(job_id):
            # job_id is guaranteed to be valid UUID
            pass

    Args:
        param_name: Name of the parameter to validate

    Returns:
        Decorator function
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            job_id = kwargs.get(param_name) or request.view_args.get(param_name)

            if not job_id:
                logging.warning(f"Missing {param_name} in request")
                return jsonify({"error": f"{param_name} is required"}), 400

            if not validate_uuid(job_id):
                logging.warning(f"Invalid UUID format for {param_name}: {job_id}")
                return jsonify({"error": f"Invalid {param_name} format"}), 400

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def validate_file_upload(file, allowed_extensions=None, max_size_mb=100):
    """
    Validate uploaded file.

    Args:
        file: Werkzeug FileStorage object
        allowed_extensions: Set of allowed extensions (e.g., {'.pdf', '.docx'})
        max_size_mb: Maximum file size in MB

    Returns:
        Tuple of (is_valid, error_message, sanitized_filename)
    """
    if not file or not file.filename:
        return False, "No file provided", None

    # Sanitize filename
    safe_filename = sanitize_filename(file.filename)

    # Check file extension if restrictions provided
    if allowed_extensions:
        _, ext = os.path.splitext(safe_filename)
        ext_lower = ext.lower()

        if not ext_lower:
            return False, "File must have an extension", None

        if ext_lower not in {e.lower() for e in allowed_extensions}:
            return False, f"File type {ext_lower} not allowed. Allowed: {', '.join(allowed_extensions)}", None

    # Check file size (if we can)
    try:
        file.seek(0, os.SEEK_END)
        size_bytes = file.tell()
        file.seek(0)  # Reset to beginning

        max_size_bytes = max_size_mb * 1024 * 1024
        if size_bytes > max_size_bytes:
            size_mb = size_bytes / (1024 * 1024)
            return False, f"File too large ({size_mb:.1f}MB). Maximum: {max_size_mb}MB", None

        if size_bytes == 0:
            return False, "File is empty", None

    except Exception as e:
        logging.warning(f"Could not check file size: {e}")

    return True, None, safe_filename


# Extensions considered text formats for content type validation
TEXT_EXTENSIONS = {
    '.md', '.markdown', '.txt', '.rst', '.html', '.htm',
    '.tex', '.latex', '.xml', '.json', '.org', '.textile',
    '.mediawiki', '.twiki', '.opml', '.asciidoc',
}


def validate_file_content_type(file, declared_extension):
    """
    Validate that file content matches its declared format using magic bytes.

    Lightweight check: reads only the first 8 bytes.

    Args:
        file: Werkzeug FileStorage object (stream position is preserved)
        declared_extension: Extension string including dot (e.g., '.pdf')

    Returns:
        Tuple of (is_valid, error_message)
    """
    ext = declared_extension.lower()

    # Read first 8 bytes, then reset
    pos = file.tell()
    header = file.read(8)
    file.seek(pos)

    if not header:
        return False, "File is empty"

    # Check PDF magic bytes
    if ext == '.pdf':
        if not header.startswith(b'%PDF'):
            return False, "File does not appear to be a valid PDF (missing %PDF header)"
        return True, None

    # Check ZIP-based formats (DOCX, ODT, EPUB)
    if ext in ('.docx', '.odt', '.epub'):
        if not header.startswith(b'PK'):
            return False, f"File does not appear to be a valid {ext} file (missing ZIP/PK header)"
        return True, None

    # Text format: basic encoding validation
    if ext in TEXT_EXTENSIONS:
        try:
            header.decode('utf-8')
        except UnicodeDecodeError:
            try:
                header.decode('latin-1')
            except UnicodeDecodeError:
                return False, "File does not appear to be a valid text file (unrecognized encoding)"
        return True, None

    # Unknown extension: skip validation (permissive)
    return True, None


def sanitize_string(value, max_length=1000, allow_newlines=False):
    """
    Sanitize a string input.

    Args:
        value: String to sanitize
        max_length: Maximum allowed length
        allow_newlines: Whether to allow newline characters

    Returns:
        Sanitized string
    """
    if not value:
        return ""

    # Convert to string
    value = str(value)

    # Remove null bytes (security risk)
    value = value.replace('\x00', '')

    # Remove newlines if not allowed
    if not allow_newlines:
        value = value.replace('\n', ' ').replace('\r', ' ')

    # Limit length
    if len(value) > max_length:
        value = value[:max_length]

    # Strip leading/trailing whitespace
    value = value.strip()

    return value


def validate_pagination_params(page=None, per_page=None, max_per_page=100):
    """
    Validate pagination parameters.

    Args:
        page: Page number (1-indexed)
        per_page: Items per page
        max_per_page: Maximum allowed items per page

    Returns:
        Tuple of (is_valid, error_message, (page, per_page))
    """
    try:
        page = int(page) if page else 1
        per_page = int(per_page) if per_page else 20
    except (ValueError, TypeError):
        return False, "Invalid pagination parameters", (1, 20)

    if page < 1:
        return False, "Page must be >= 1", (1, per_page)

    if per_page < 1:
        return False, "per_page must be >= 1", (page, 1)

    if per_page > max_per_page:
        return False, f"per_page must be <= {max_per_page}", (page, max_per_page)

    return True, None, (page, per_page)


# Validation constants
ALLOWED_INPUT_FORMATS = {
    'markdown', 'rst', 'textile', 'html', 'docx', 'odt', 'epub', 'latex',
    'mediawiki', 'twiki', 'opml', 'org', 'docbook', 'pdf_marker', 'pdf'
}

ALLOWED_OUTPUT_FORMATS = {
    'markdown', 'rst', 'html', 'pdf', 'docx', 'odt', 'epub', 'latex',
    'mediawiki', 'textile', 'rtf', 'asciidoc', 'plain', 'json', 'xml'
}


if __name__ == '__main__':
    # Test validation functions
    print("Testing validation functions...")

    # Test UUID validation
    assert validate_uuid("123e4567-e89b-12d3-a456-426614174000") == True
    assert validate_uuid("invalid-uuid") == False

    # Test filename sanitization
    assert sanitize_filename("../../etc/passwd") == "_.._.._etc_passwd"
    assert sanitize_filename("safe-file_name.pdf") == "safe-file_name.pdf"
    assert sanitize_filename("file with spaces.docx") == "file_with_spaces.docx"

    print("✓ All validation tests passed")
