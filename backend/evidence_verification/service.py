from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import socket
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import newsradar
from news_intelligence.clustering import cluster_items
from news_intelligence.normalizer import normalize_radar

from .models import EvidenceItem, EvidenceSnapshot, SourceRole
from .source_identity import OFFICIAL_PUBLISHERS, canonicalize_public_url, identify_evidence, official_content_source
from .storage import EvidenceStorage
from .verifier import verify_event


@dataclass(frozen=True, slots=True)
class PublicDocument:
    canonical_url: str
    title: str
    excerpt: str
    published_at: datetime | None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        if lowered == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        if lowered == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or not data.strip():
            return
        value = " ".join(data.split())
        self.text_parts.append(value)
        if self._in_title:
            self.title_parts.append(value)


def _validate_public_network_url(url: str, resolver) -> str:
    canonical = canonicalize_public_url(url)
    if canonical is None:
        raise PermissionError("URL is not an allowed public network URL")
    host = urlsplit(canonical).hostname
    try:
        addresses = resolver(host, None, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ConnectionError("public host resolution failed") from error
    if not addresses:
        raise ConnectionError("public host resolution returned no addresses")
    for row in addresses:
        try:
            address = row[4][0]
            if not ipaddress.ip_address(address).is_global:
                raise PermissionError("redirect destination is not on the public network")
        except (IndexError, TypeError, ValueError) as error:
            raise PermissionError("resolver returned an invalid public network address") from error
    return canonical


class SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, *, resolver=socket.getaddrinfo, max_redirects: int = 3) -> None:
        super().__init__()
        self._resolver = resolver
        self._max_redirects = max_redirects

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirects = getattr(req, "redirect_dict", {})
        if len(redirects) >= self._max_redirects:
            raise PermissionError("public document redirect limit exceeded")
        safe_url = _validate_public_network_url(newurl, self._resolver)
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


def fetch_public_document(
    url: str,
    *,
    timeout: float = 5.0,
    max_bytes: int = 1_048_576,
    resolver=socket.getaddrinfo,
    opener=None,
) -> PublicDocument:
    if timeout <= 0 or timeout > 15:
        raise ValueError("timeout must be between 0 and 15 seconds")
    if max_bytes < 1 or max_bytes > 2_097_152:
        raise ValueError("max_bytes must be between 1 and 2097152")
    canonical = _validate_public_network_url(url, resolver)
    active_opener = opener or build_opener(SafeRedirectHandler(resolver=resolver, max_redirects=3))
    request = Request(canonical, headers={
        "User-Agent": "PP03-EvidenceVerifier/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9",
    })
    with active_opener.open(request, timeout=timeout) as response:
        final_url = _validate_public_network_url(response.geturl(), resolver)
        content_type = response.headers.get_content_type().lower()
        if content_type not in {"text/html", "application/xhtml+xml", "application/xml", "text/xml", "text/plain"}:
            raise ValueError("public document content type is not supported")
        payload = response.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError("public document size limit exceeded")
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset)
        except (LookupError, UnicodeDecodeError) as error:
            raise ValueError("public document encoding is not supported") from error
    parser = _TextExtractor()
    parser.feed(decoded)
    text = " ".join(parser.text_parts).strip()
    title = " ".join(parser.title_parts).strip()
    if not title:
        title = next((line.strip() for line in decoded.splitlines() if line.strip()), "")[:500]
    if not title or not text:
        raise ValueError("public document has no verifiable text")
    return PublicDocument(
        canonical_url=final_url,
        title=title[:500],
        excerpt=text[:1200],
        published_at=None,
    )


def _default_event_loader(now: datetime):
    radar = newsradar.get_radar()
    return cluster_items(normalize_radar(radar, now=now))


class EvidenceVerificationService:
    def __init__(
        self,
        *,
        storage: EvidenceStorage | None = None,
        event_loader: Callable[[], list] | None = None,
        document_fetcher: Callable[[str], PublicDocument] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.storage = storage or EvidenceStorage(now=self._now)
        self._event_loader = event_loader
        self._document_fetcher = document_fetcher or fetch_public_document

    def _load_events(self):
        if self._event_loader is not None:
            return self._event_loader()
        return _default_event_loader(self._now())

    def _attest_existing_official_link(self, item: EvidenceItem) -> EvidenceItem:
        official_publishers = set(OFFICIAL_PUBLISHERS.values())
        if item.is_official or item.content_source not in official_publishers or self._document_fetcher is None:
            return item
        try:
            document = self._document_fetcher(item.canonical_url)
        except Exception:
            return item
        final_url = canonicalize_public_url(document.canonical_url)
        if (
            final_url is None
            or official_content_source(final_url) != item.content_source
            or not document.title.strip()
        ):
            return item
        return replace(
            item,
            canonical_url=final_url,
            source_role=SourceRole.PRIMARY,
            is_official=True,
            title=document.title[:500],
            excerpt=document.excerpt[:1200],
            published_at=document.published_at or item.published_at,
        )

    def refresh(self) -> EvidenceSnapshot:
        attempted_at = self._now()
        previous = self.storage.load_current()
        previous_by_id = {event.event_id: event for event in previous.events} if previous else {}
        try:
            events = list(self._load_events())
            if not events:
                raise RuntimeError("no events available for evidence refresh")
            verified = []
            for event in events:
                evidence = []
                for source in event.sources:
                    item = identify_evidence(source)
                    if item.canonical_url:
                        evidence.append(self._attest_existing_official_link(item))
                verified.append(verify_event(
                    event,
                    evidence,
                    previous=previous_by_id.get(event.event_id),
                    now=attempted_at,
                ))
            fingerprint = json.dumps(
                [(event.event_id, event.verification_status.value, event.verification_reason) for event in verified],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            snapshot = EvidenceSnapshot(
                snapshot_id=hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:20],
                generated_at=attempted_at,
                events=tuple(verified),
            )
            self.storage.publish(snapshot)
            return snapshot
        except Exception as error:
            try:
                self.storage.record_failure(error)
            except Exception:
                pass
            raise

    def get_event(self, event_id: str):
        snapshot = self.storage.load_current()
        if snapshot is None:
            return None
        return next((event for event in snapshot.events if event.event_id == event_id), None)
