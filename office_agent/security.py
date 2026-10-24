"""Small shared boundaries for untrusted text, secrets and outgoing document URLs."""

import ipaddress
import os
import re
import socket
from urllib.parse import urlsplit

SOURCE_HOSTS = frozenset({
    "news.google.com", "www.bisnow.com", "www.propertyweek.com", "www.cityam.com",
    "www.bankofengland.co.uk", "www.ons.gov.uk", "www.jll.com", "www.savills.co.uk",
    "www.avisonyoung.co.uk", "www.knightfrank.co.uk",
})


class SecurityError(ValueError):
    pass


def safe_text(value: str) -> str:
    """Strip terminal escapes/control characters and redact configured credentials."""
    value = str(value)
    for key, secret in os.environ.items():
        if any(part in key.upper() for part in ("API_KEY", "SECRET", "PASSWORD", "ACCESS_TOKEN")) and len(secret) >= 6:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?i)\b(?:sk-|tp-|apikey_)[a-z0-9_-]{8,}", "[REDACTED]", value)
    value = re.sub(r"(?i)(bearer\s+)[a-z0-9._-]+", r"\1[REDACTED]", value)
    value = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", value)
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", value)


def validate_source_url(url: str, *, resolve: bool = True) -> str:
    """Only configured public HTTPS sources, including every redirect destination."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        valid = (parts.scheme == "https" and host in SOURCE_HOSTS and not parts.username
                 and not parts.password and parts.port in (None, 443)
                 and not re.search(r"[\x00-\x20\\]", url))
    except ValueError:
        valid = False
    if not valid:
        raise SecurityError("Document URL is outside the approved HTTPS sources.")
    if resolve:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise SecurityError("Document host resolves to a non-public address.")
    return url
