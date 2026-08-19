from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_PUBLIC_QUERY_NAMES = {"mid"}


def public_source_reference(url: str) -> str:
    """Keep only explicitly public routing parameters and discard all others."""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.strip().lower() in _PUBLIC_QUERY_NAMES
    ]
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), ""))
