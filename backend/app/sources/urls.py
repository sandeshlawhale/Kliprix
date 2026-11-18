"""URL allowlist: reject anything that is not an http(s) link to a supported host.

Runs before any adapter or yt-dlp call, because yt-dlp can fetch arbitrary sites
(docs/11-constraints.md, "Security and privacy").
"""

from __future__ import annotations

import re
from urllib.parse import SplitResult, urlsplit

from app.config import MAX_URL_LENGTH, SUPPORTED_HOSTS
from app.core.errors import AppError, ErrorCode

ALLOWED_SCHEMES = ("http", "https")
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def host_matches(host: str, allowed: str) -> bool:
    """True for the host itself and its subdomains (`www.youtube.com`), never look-alikes."""
    return host == allowed or host.endswith("." + allowed)


def parse_allowed_url(raw: str) -> SplitResult:
    """Parse `raw` and check it against `SUPPORTED_HOSTS`.

    Returns the parsed URL with a lower-case host.

    Raises:
        AppError INVALID_URL: empty, too long, not http(s), credentials, port, or no
            valid host name.
        AppError UNSUPPORTED_HOST: a valid link to a host that is not supported.
    """
    text = raw.strip()
    if not text or len(text) > MAX_URL_LENGTH:
        raise AppError(ErrorCode.INVALID_URL, "This is not a valid link.")
    if "://" not in text:
        # People paste "youtu.be/abc" without a scheme.
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_URL, "This is not a valid link.") from exc
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in ALLOWED_SCHEMES or not _HOSTNAME_RE.match(host):
        raise AppError(ErrorCode.INVALID_URL, "This is not a valid link.")
    if parts.username is not None or parts.password is not None or port is not None:
        raise AppError(ErrorCode.INVALID_URL, "This is not a valid link.")
    if not any(host_matches(host, allowed) for allowed in SUPPORTED_HOSTS):
        raise AppError(
            ErrorCode.UNSUPPORTED_HOST,
            "Only YouTube links are supported for now.",
            {"supported_hosts": list(SUPPORTED_HOSTS)},
        )
    return parts._replace(scheme=parts.scheme.lower(), netloc=host)
