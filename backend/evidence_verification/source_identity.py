from __future__ import annotations

import hashlib
import ipaddress
import posixpath
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from news_intelligence.models import NewsSourceItem

from .models import EvidenceItem, SourceRole


OFFICIAL_PUBLISHERS: dict[str, str] = {
    "openai.com": "openai.com",
    "research.google": "research.google",
    "deepmind.google": "deepmind.google",
    "huggingface.co": "huggingface.co",
    "github.blog": "github.blog",
    "nasa.gov": "nasa.gov",
    "sec.gov": "sec.gov",
    "federalreserve.gov": "federalreserve.gov",
    "cninfo.com.cn": "cninfo.com.cn",
    "sse.com.cn": "sse.com.cn",
    "szse.cn": "szse.cn",
    "hkexnews.hk": "hkexnews.hk",
}
TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform", "gclid", "fbclid", "mc_cid", "mc_eid",
}
SECRET_QUERY_KEYS = {
    "api_key", "apikey", "access_token", "token", "client_secret", "password",
    "refresh_token", "session", "jwt", "authorization", "code",
}


def _publisher_for_host(host: str) -> tuple[str, bool]:
    lowered = host.rstrip(".").lower()
    for official_host, publisher in OFFICIAL_PUBLISHERS.items():
        if lowered == official_host or lowered.endswith(f".{official_host}"):
            return publisher, True
    if lowered.startswith("www."):
        lowered = lowered[4:]
    return lowered, False


def canonicalize_public_url(url: str) -> str | None:
    try:
        parsed = urlsplit(str(url or "").strip())
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    try:
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return None
    try:
        if not ipaddress.ip_address(host).is_global:
            return None
    except ValueError:
        pass
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    lowered_keys = {key.lower() for key, _ in query_pairs}
    if lowered_keys & SECRET_QUERY_KEYS:
        return None
    public_query = urlencode(sorted(
        (key, value) for key, value in query_pairs if key.lower() not in TRACKING_QUERY_KEYS
    ), doseq=True)
    raw_path = unquote(parsed.path or "/")
    normalized_path = posixpath.normpath(raw_path)
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    normalized_path = quote(normalized_path, safe="/%:@-._~!$&'()*+,;=")
    scheme = parsed.scheme.lower()
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    authority = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit((scheme, authority, normalized_path, public_query, ""))


def official_content_source(url: str) -> str | None:
    canonical = canonicalize_public_url(url)
    if canonical is None:
        return None
    host = urlsplit(canonical).hostname or ""
    publisher, is_official = _publisher_for_host(host)
    return publisher if is_official else None


def identify_evidence(source: NewsSourceItem) -> EvidenceItem:
    canonical = canonicalize_public_url(source.original_url)
    if canonical is None:
        canonical = canonicalize_public_url(source.source_url) or ""
    host = urlsplit(canonical).hostname or "unknown"
    content_source, is_official = _publisher_for_host(host)
    feed = canonicalize_public_url(source.source_url)
    feed_host = urlsplit(feed).hostname if feed else None
    collector_publisher, collector_is_official = _publisher_for_host(feed_host or "unknown")
    is_attested_official = is_official and collector_is_official and collector_publisher == content_source
    collector_source = f"{source.source_name}|{feed_host or 'unknown'}"
    digest_input = "\0".join((canonical, source.normalized_title, source.published_at.isoformat() if source.published_at else ""))
    evidence_id = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:24]
    return EvidenceItem(
        evidence_id=evidence_id,
        content_source=content_source,
        collector_source=collector_source,
        canonical_url=canonical,
        published_at=source.published_at,
        source_role=SourceRole.PRIMARY if is_attested_official else SourceRole.INDEPENDENT,
        origin_cluster=f"publisher:{content_source}",
        is_official=is_attested_official,
        title=source.title,
        excerpt=source.summary,
    )
