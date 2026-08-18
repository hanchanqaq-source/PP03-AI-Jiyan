from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
import threading
from typing import Callable
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import newsradar
from news_intelligence.clustering import cluster_items
from news_intelligence.normalizer import normalize_radar

from .models import EvidenceItem, EvidenceSnapshot, FieldVerificationStatus, SourceRole, VerificationStatus
from .source_identity import OFFICIAL_PUBLISHERS, canonicalize_public_url, identify_evidence, official_content_source
from .storage import EvidenceStorage, field_document
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
        origin_publisher = official_content_source(req.full_url)
        if origin_publisher is None or official_content_source(safe_url) != origin_publisher:
            raise PermissionError("redirect changed the official publisher identity")
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

    def get_snapshot(self) -> EvidenceSnapshot | None:
        return self.storage.load_current()

    def get_summary(self) -> dict:
        snapshot = self.storage.load_current()
        last_refresh = self.storage.load_last_refresh()
        if snapshot is None:
            return {
                "loaded": False,
                "snapshot_id": None,
                "generated_at": None,
                "counts": None,
                "field_counts": None,
                "admitted_count": None,
                "isolated_count": None,
                "last_refresh": last_refresh,
            }
        counts = Counter(event.verification_status.value for event in snapshot.events)
        field_counts = Counter(field.verification_status.value for event in snapshot.events for field in event.key_fields)
        trusted = {VerificationStatus.VERIFIED.value, VerificationStatus.CORROBORATED.value}
        admitted_count = sum(counts[value] for value in trusted)
        return {
            "loaded": True,
            "snapshot_id": snapshot.snapshot_id,
            "generated_at": snapshot.generated_at.isoformat(),
            "counts": {value.value: counts[value.value] for value in VerificationStatus},
            "field_counts": {value.value: field_counts[value.value] for value in FieldVerificationStatus},
            "admitted_count": admitted_count,
            "isolated_count": len(snapshot.events) - admitted_count,
            "last_refresh": last_refresh,
        }

    def list_events(
        self,
        *,
        verification_status: str | None = None,
        tag_id: str | None = None,
        category: str | None = None,
        days: int = 7,
        holding_relevance: str | None = None,
    ) -> list:
        if verification_status is not None and verification_status not in {value.value for value in VerificationStatus}:
            raise ValueError("invalid verification_status")
        if days not in {1, 3, 7, 30}:
            raise ValueError("days must be one of 1, 3, 7, 30")
        snapshot = self.storage.load_current()
        if snapshot is None:
            return []
        current = self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = current - timedelta(days=days)
        rows = [event for event in snapshot.events if event.published_at is not None and event.published_at >= cutoff]
        if verification_status is not None:
            rows = [event for event in rows if event.verification_status.value == verification_status]
        if tag_id is not None:
            rows = [event for event in rows if tag_id in {key for key, _ in event.related_tags}]
        if category is not None:
            rows = [event for event in rows if event.category == category]
        if holding_relevance is not None:
            rows = [event for event in rows if event.holding_relevance == holding_relevance]
        return sorted(rows, key=lambda event: (event.verified_at, event.event_id), reverse=True)

    def admit(self, events: list) -> tuple[str, list]:
        snapshot = self.storage.load_current()
        if snapshot is None:
            return "unavailable", []
        trusted_statuses = {VerificationStatus.VERIFIED, VerificationStatus.CORROBORATED}
        evidence_by_id = {
            event.event_id: event for event in snapshot.events if event.verification_status in trusted_statuses
        }
        admitted = []
        trusted_field_statuses = {FieldVerificationStatus.VERIFIED, FieldVerificationStatus.CORROBORATED}
        for raw in events:
            evidence = evidence_by_id.get(raw.event_id)
            if evidence is None:
                continue
            projected = copy.deepcopy(raw)
            untrusted_values = sorted({
                field.raw_value for field in evidence.key_fields
                if field.verification_status not in trusted_field_statuses and field.raw_value
            }, key=len, reverse=True)
            for value in untrusted_values:
                projected.title = projected.title.replace(value, "")
                projected.summary = projected.summary.replace(value, "")
            projected.title = re.sub(r"\s{2,}", " ", projected.title).strip()
            projected.summary = re.sub(r"\s{2,}", " ", projected.summary).strip()
            projected.impact_basis = [
                basis for basis in getattr(projected, "impact_basis", [])
                if not any(value in basis for value in untrusted_values)
            ]
            projected.verification_status = evidence.verification_status.value
            projected.verification_reason = evidence.verification_reason
            projected.verified_at = evidence.verified_at.isoformat()
            projected.verified_key_fields = [
                field_document(field) for field in evidence.key_fields if field.verification_status in trusted_field_statuses
            ]
            admitted.append(projected)
        return snapshot.snapshot_id, admitted


_service: EvidenceVerificationService | None = None
_service_lock = threading.Lock()


def get_service() -> EvidenceVerificationService:
    global _service
    with _service_lock:
        if _service is None:
            _service = EvidenceVerificationService()
        return _service


def reset_service() -> None:
    global _service
    with _service_lock:
        _service = None
