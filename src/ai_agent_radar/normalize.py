import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {"ref", "source"}


def canonicalize_url(url: str) -> str:
    """Return a stable URL without fragments or common tracking parameters."""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def normalize_text(text: str) -> str:
    """Collapse insignificant text differences for event comparison."""
    return re.sub(r"\s+", " ", text).strip().casefold()
