import re
from urllib.parse import urlsplit

from .config import settings

MAX_URL_LENGTH = 2048

# Custom aliases: 3-30 chars, letters/digits/_/-
ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,30}$")

# Paths the app itself uses. An alias with one of these names would be
# unreachable (or worse, shadow a real route).
RESERVED = {
    "api", "health", "docs", "redoc", "openapi.json", "static", "admin",
    "login", "register", "favicon.ico", "robots.txt", "www", "app",
}


def validate_target_url(raw: str) -> str:
    """Return the cleaned URL or raise ValueError.

    Rules: http/https only (blocks javascript:, data:, file: ...), must have a
    host, bounded length, and must not point back at ourselves (redirect loop).
    """
    url = raw.strip()
    if len(url) > MAX_URL_LENGTH:
        raise ValueError(f"URL longer than {MAX_URL_LENGTH} characters")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    if not parts.hostname:
        raise ValueError("URL has no host")
    own_host = urlsplit(settings.base_url).hostname
    if own_host and parts.hostname.lower() == own_host.lower():
        raise ValueError("Cannot shorten a link that points to this service")
    return url


def validate_alias(alias: str) -> str:
    if not ALIAS_RE.match(alias):
        raise ValueError("Alias must be 3-30 characters: letters, digits, _ or -")
    if alias.lower() in RESERVED:
        raise ValueError("That alias is reserved")
    return alias
