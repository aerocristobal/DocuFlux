"""SSRF validation for webhook URLs.

Lives in shared/ rather than web/ because both tiers need it:

  * `web/routes/webhooks.py` validates at registration time
  * `shared/job_metadata.py` re-validates at delivery time (defence in depth, since a
    hostname can start resolving to a private address after it was registered)

It previously lived in `web/validation.py`, which the worker image never copies —
`worker/Dockerfile` COPYs only `config.py`, `shared/` and `worker/`. The delivery-time
import therefore raised ModuleNotFoundError inside the worker, and `fire_webhook`'s own
`except Exception` swallowed it and logged "Webhook delivery failed", so every webhook
silently failed in production. `web/validation.py` re-exports this for compatibility.
"""
import ipaddress
import socket
from urllib.parse import urlparse


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
