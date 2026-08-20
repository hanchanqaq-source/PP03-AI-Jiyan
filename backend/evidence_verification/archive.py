from __future__ import annotations

from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, Callable, Iterator
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from cache_io_lock import CACHE_IO_LOCK

from .models import EvidenceSnapshot, VerificationStatus
from .storage import (
    _EVENT_KEYS,
    _cleanup_owned_temp,
    _close_owned_descriptor,
    _ensure_directory,
    _event_from_document,
    _exact_builtin,
    _parse_datetime,
    _reject_json_number,
    _replace_durable,
    _safe_directory,
    _strict_json_int,
    _strict_json_object,
    event_document,
    validated_snapshot_document,
)
_ARCHIVE_SCHEMA_VERSION = 2
_LEGACY_ARCHIVE_SCHEMA_VERSION = 1
_INDEX_SCHEMA_VERSION = 1
_ALLOWED_DAYS = {1, 3, 7, 30, 90}
_KNOWN_STATUSES = {status.value for status in VerificationStatus}
_BUCKET_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")
_MAX_BUCKET_BYTES = 32 * 1_048_576
_MAX_INDEX_BYTES = 16 * 1_048_576
_MAX_ROW_BYTES = 1_048_576
_MAX_BUCKET_ROWS = 4_096
_MAX_ARCHIVE_FILES = 512
_MAX_ARCHIVE_DIRECTORY_ENTRIES = _MAX_ARCHIVE_FILES + 4
_MAX_SCAN_FILES = _MAX_ARCHIVE_FILES + 2
_MAX_INDEX_EVENTS = 65_536
_MAX_ARCHIVE_SCAN_BYTES = 64 * 1_048_576
_MAX_JOURNAL_BYTES = 32 * 1_048_576
_MAX_STATE_BYTES = 48 * 1_048_576
_MAX_SNAPSHOT_BYTES = 16 * 1_048_576
_MAX_SNAPSHOT_NODES = 200_000
_MAX_SNAPSHOT_RESOURCES = 50_000
_MAX_SCAN_BYTES = _MAX_ARCHIVE_SCAN_BYTES + _MAX_JOURNAL_BYTES + _MAX_STATE_BYTES
_MAX_SCAN_ROWS = _MAX_INDEX_EVENTS * 2
_MAX_ARCHIVE_SCAN_NODES = _MAX_INDEX_EVENTS * 128
_MAX_SCAN_NODES = _MAX_ARCHIVE_SCAN_NODES + _MAX_SNAPSHOT_NODES * 2
_MAX_MUTATION_BYTES = 128 * 1_048_576
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_MAX_DIAGNOSTIC_COUNT = 1_000_000
_MAX_TOLERATED_CORRUPT_ROWS = 1
_JOURNAL_SCHEMA_VERSION = 3
_PREVIOUS_JOURNAL_SCHEMA_VERSION = 2
_LEGACY_JOURNAL_SCHEMA_VERSION = 1
_STATE_SCHEMA_VERSION = 4
_LEGACY_STATE_SCHEMA_VERSIONS = {1, 2, 3}
_ARCHIVE_KEYS = _EVENT_KEYS | {
    "schema_version",
    "evidence_snapshot_id",
    "raw_snapshot_id",
    "snapshot_generated_at",
    "archived_at",
    "last_updated_at",
    "snapshot_history",
    "content_digest",
}
_LEGACY_ARCHIVE_KEYS = _ARCHIVE_KEYS - {"content_digest"}
_LINEAGE_KEYS = {"evidence_snapshot_id", "raw_snapshot_id", "generated_at", "content_digest"}
_LEGACY_LINEAGE_KEYS = _LINEAGE_KEYS - {"content_digest"}
_MIGRATED_LINEAGE_KEYS = _LINEAGE_KEYS | {"legacy_v1", "legacy_projection_digest"}
_UNVERIFIABLE_MIGRATED_LINEAGE_KEYS = _MIGRATED_LINEAGE_KEYS | {"legacy_unverifiable"}
_INDEX_KEYS = {"schema_version", "events"}
_LEGACY_JOURNAL_KEYS = {"schema_version", "rows"}
_PREVIOUS_JOURNAL_KEYS = {
    "schema_version",
    "transaction_id",
    "base_generation",
    "target_generation",
    "base_index_digest",
    "target_index_digest",
    "base_bucket_digests",
    "target_bucket_digests",
    "rows",
}
_JOURNAL_KEYS = _PREVIOUS_JOURNAL_KEYS | {"cutoff", "target_index"}
_LEGACY_STATE_KEYS = {
    "schema_version",
    "generation",
    "phase",
    "transaction_id",
    "index_digest",
    "bucket_digests",
}
_PREPARED_STATE_KEYS = {
    "schema_version",
    "generation",
    "phase",
    "transaction_id",
    "base_generation",
    "target_generation",
    "cutoff",
    "base_index_digest",
    "target_index_digest",
    "base_bucket_digests",
    "target_bucket_digests",
    "target_index",
    "rows",
}
_FINALIZED_STATE_KEYS = {
    "schema_version",
    "generation",
    "phase",
    "transaction_id",
    "cutoff",
    "target_index_digest",
    "target_bucket_digests",
    "target_index",
}
_MISSING_DIGEST = "0" * 64
_EVIDENCE_COLLECTION_KEYS = (
    "primary_evidence",
    "independent_evidence",
    "syndicated_copies",
    "contradicting_evidence",
)
_SENSITIVE_QUERY_NAMES = {
    "key",
    "apikey",
    "accesskey",
    "privatekey",
    "secretkey",
    "authkey",
    "sessionkey",
    "subscriptionkey",
    "token",
    "accesstoken",
    "refreshtoken",
    "clientsecret",
    "secret",
    "credential",
    "credentials",
    "bearer",
    "password",
    "passwd",
    "authorization",
    "auth",
    "cookie",
    "session",
    "jwt",
    "signature",
    "sig",
    "code",
}
_NESTED_SECRET = re.compile(
    r"(?:^|[?&;=/])\s*(?:key|api[_-]?key|access[_-]?key|private[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|token|client[_-]?secret|secret|password|passwd|authorization|auth|"
    r"credential(?:s)?|bearer|cookie|session|jwt|signature|sig|code)\s*=",
    re.IGNORECASE,
)
_QUERY_ASSIGNMENT = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9_-]{1,128})\s*[:=]")
_MAX_QUERY_DECODE_ROUNDS = 8
_MAX_QUERY_JSON_NODES = 512
_MAX_QUERY_JSON_DEPTH = 8
_MAX_QUERY_NAME_LENGTH = 128
_MAX_QUERY_VERSION_SUFFIX_ROUNDS = 16
_MAX_V2_CUTOFF_CANDIDATES = 4_096
_SPECIAL_HOST_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".home",
    ".lan",
    ".test",
    ".invalid",
    ".example",
    ".onion",
)
_TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_source_platform",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
}
_NAT64_NETWORKS = (
    ipaddress.IPv6Network("64:ff9b::/96"),
    ipaddress.IPv6Network("64:ff9b:1::/48"),
)
_IPV4_COMPATIBLE_NETWORK = ipaddress.IPv6Network("::/96")
_UNRESERVED_PATH_BYTES = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_JSON_HEX_BYTES = frozenset(b"0123456789abcdefABCDEF")


def _new_scan_budget() -> dict[str, int]:
    return {
        "bytes": _MAX_SCAN_BYTES,
        "rows": _MAX_SCAN_ROWS,
        "nodes": _MAX_SCAN_NODES,
        "files": _MAX_SCAN_FILES,
    }


_PATH_SAFE_CHARACTERS = "/:@-._~!$&'()*+,;="
_CONTENT_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_STATUS_PRECEDENCE = {
    VerificationStatus.UNVERIFIED.value: 0,
    VerificationStatus.VERIFIED.value: 1,
    VerificationStatus.CORROBORATED.value: 2,
    VerificationStatus.CONFLICTING.value: 3,
    VerificationStatus.CORRECTED.value: 4,
    VerificationStatus.DISPROVED.value: 5,
}


class _ArchiveFutureSchemaError(ValueError):
    pass


def _utc(value: datetime, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} timezone must be aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime, name: str) -> str:
    return _utc(value, name).isoformat()


def _bounded_text(value: object, name: str, *, maximum: int = 128) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError(f"invalid {name}")
    return value


def _metadata_time(value: object, name: str) -> datetime:
    parsed = _parse_datetime(value)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")
    return parsed


def _valid_bucket_name(value: object) -> bool:
    if type(value) is not str:
        return False
    match = _BUCKET_NAME.fullmatch(value)
    if match is None:
        return False
    try:
        return date.fromisoformat(match.group(1)).isoformat() == match.group(1)
    except ValueError:
        return False


def _bucket_date(value: str) -> date:
    if not _valid_bucket_name(value):
        raise ValueError("invalid archive bucket name")
    return date.fromisoformat(value.removesuffix(".jsonl"))


def _bucket_name_in_window(value: str, cutoff: datetime, upper: datetime) -> bool:
    bucket_day = _bucket_date(value)
    return cutoff.date() <= bucket_day <= upper.date()


def _file_signature(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_nlink,
        metadata.st_mode,
        getattr(metadata, "st_reparse_tag", 0),
    )


def _file_mtime_utc(metadata: tuple[int, int, int, int, int, int, int, int]) -> datetime | None:
    try:
        mtime_ns = metadata[3]
        if type(mtime_ns) is not int or mtime_ns < 0:
            return None
        return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
            microseconds=mtime_ns // 1_000,
        )
    except (IndexError, OverflowError, TypeError, ValueError):
        return None


def _lineage_document(snapshot: EvidenceSnapshot, content_digest: str) -> dict[str, str]:
    if _CONTENT_DIGEST.fullmatch(content_digest) is None:
        raise ValueError("invalid archive lineage digest")
    return {
        "evidence_snapshot_id": _bounded_text(snapshot.snapshot_id, "evidence_snapshot_id"),
        "raw_snapshot_id": _bounded_text(snapshot.raw_snapshot_id, "raw_snapshot_id"),
        "generated_at": _timestamp(snapshot.generated_at, "snapshot generated_at"),
        "content_digest": content_digest,
    }


def _lineage_from_document(
    value: object,
    *,
    legacy: bool = False,
    legacy_projection_digest: str | None = None,
    legacy_unverifiable: bool = False,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("invalid archive lineage schema")
    keys = set(value)
    migrated = not legacy and frozenset(keys) in {
        frozenset(_MIGRATED_LINEAGE_KEYS),
        frozenset(_UNVERIFIABLE_MIGRATED_LINEAGE_KEYS),
    }
    if (legacy and keys != _LEGACY_LINEAGE_KEYS) or (
        not legacy
        and keys != _LINEAGE_KEYS
        and keys != _MIGRATED_LINEAGE_KEYS
        and keys != _UNVERIFIABLE_MIGRATED_LINEAGE_KEYS
    ):
        raise ValueError("invalid archive lineage schema")
    evidence_snapshot_id = _bounded_text(value["evidence_snapshot_id"], "evidence_snapshot_id")
    raw_snapshot_id = _bounded_text(value["raw_snapshot_id"], "raw_snapshot_id")
    generated_at = _metadata_time(value["generated_at"], "lineage generated_at").isoformat()
    if legacy:
        if legacy_projection_digest is None or _CONTENT_DIGEST.fullmatch(legacy_projection_digest) is None:
            raise ValueError("invalid legacy archive lineage projection")
        lineage_digest = legacy_projection_digest
    else:
        lineage_digest = _bounded_text(value["content_digest"], "lineage content_digest", maximum=64)
    lineage = {
        "evidence_snapshot_id": evidence_snapshot_id,
        "raw_snapshot_id": raw_snapshot_id,
        "generated_at": generated_at,
        "content_digest": lineage_digest,
    }
    if legacy or migrated:
        projection_digest = legacy_projection_digest if legacy else value["legacy_projection_digest"]
        if (
            projection_digest != lineage["content_digest"]
            or value.get("legacy_v1", True) is not True
        ):
            raise ValueError("invalid migrated archive lineage")
        lineage["legacy_v1"] = True
        lineage["legacy_projection_digest"] = projection_digest
        marked_unverifiable = legacy_unverifiable if legacy else value.get("legacy_unverifiable", False)
        if marked_unverifiable:
            if not legacy and value.get("legacy_unverifiable") is not True:
                raise ValueError("invalid migrated archive lineage")
            lineage["legacy_unverifiable"] = True
    if _CONTENT_DIGEST.fullmatch(lineage["content_digest"]) is None:
        raise ValueError("invalid archive lineage digest")
    return lineage


def _event_content_digest(row: dict[str, Any]) -> str:
    event = {key: row[key] for key in sorted(_EVENT_KEYS)}
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _event_time(row: dict[str, Any]) -> datetime:
    published_at = row["published_at"]
    if published_at is not None:
        return _parse_datetime(published_at).astimezone(timezone.utc)
    return _parse_datetime(row["verified_at"]).astimezone(timezone.utc)


def _bucket_name(row: dict[str, Any]) -> str:
    return f"{_event_time(row).date().isoformat()}.jsonl"


def _shift_datetime(value: datetime, delta: timedelta) -> datetime:
    try:
        return value + delta
    except OverflowError:
        raise OSError("storage_corrupt") from None


def _shift_days(value: datetime, days: int) -> datetime:
    return _shift_datetime(value, timedelta(days=days))


def _strip_query_version_suffixes(value: str) -> str:
    if len(value) > _MAX_QUERY_NAME_LENGTH:
        raise ValueError("invalid archive evidence URL")
    normalized = value
    for _ in range(_MAX_QUERY_VERSION_SUFFIX_ROUNDS):
        suffix = re.search(r"(?:version|ver|v)?[0-9]+$", normalized)
        if suffix is None:
            return normalized
        normalized = normalized[:suffix.start()]
    raise ValueError("invalid archive evidence URL")


def _query_name_is_sensitive(value: str) -> bool:
    if len(value) > _MAX_QUERY_NAME_LENGTH:
        raise ValueError("invalid archive evidence URL")
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    tokens = re.findall(r"[a-z0-9]+", separated.casefold())
    compact = "".join(tokens)
    normalized = _strip_query_version_suffixes(compact)
    if normalized in _SENSITIVE_QUERY_NAMES:
        return True
    if any(_strip_query_version_suffixes(token) in _SENSITIVE_QUERY_NAMES for token in tokens):
        return True
    sensitive_suffixes = (
        "apikey",
        "accesskey",
        "privatekey",
        "secretkey",
        "authkey",
        "sessionkey",
        "subscriptionkey",
        "token",
        "authorization",
        "auth",
        "password",
        "passwd",
        "signature",
        "secret",
        "cookie",
        "session",
        "jwt",
        "credential",
        "credentials",
        "bearer",
    )
    return any(normalized.endswith(suffix) for suffix in sensitive_suffixes)


def _text_contains_sensitive_assignment(value: str) -> bool:
    return any(_query_name_is_sensitive(match.group(1)) for match in _QUERY_ASSIGNMENT.finditer(value))


def _json_value_contains_sensitive_name(
    value: object,
    *,
    depth: int,
    remaining: list[int],
) -> bool:
    if depth > _MAX_QUERY_JSON_DEPTH:
        raise ValueError("invalid archive evidence URL")
    remaining[0] -= 1
    if remaining[0] < 0:
        raise ValueError("invalid archive evidence URL")
    if type(value) is dict:
        for key, nested in value.items():
            if type(key) is not str or _query_name_is_sensitive(key):
                return True
            if _json_value_contains_sensitive_name(nested, depth=depth + 1, remaining=remaining):
                return True
        return False
    if type(value) is list:
        return any(
            _json_value_contains_sensitive_name(nested, depth=depth + 1, remaining=remaining)
            for nested in value
        )
    if type(value) is str:
        return _decoded_query_value_contains_sensitive_name(
            value,
            depth=depth + 1,
            remaining=remaining,
        )
    if type(value) in {int, float, bool, type(None)}:
        return False
    raise ValueError("invalid archive evidence URL")


def _decoded_query_value_contains_sensitive_name(
    value: str,
    *,
    depth: int = 0,
    remaining: list[int] | None = None,
) -> bool:
    if depth > _MAX_QUERY_JSON_DEPTH or len(value) > 8_192:
        raise ValueError("invalid archive evidence URL")
    node_budget = [_MAX_QUERY_JSON_NODES] if remaining is None else remaining
    decoded = value
    stabilized = False
    for _ in range(_MAX_QUERY_DECODE_ROUNDS):
        if _text_contains_sensitive_assignment(decoded):
            return True
        stripped = decoded.strip()
        if (
            stripped.startswith(("{", "[", '"'))
            or stripped in {"true", "false", "null"}
            or re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", stripped)
        ):
            encoded = stripped.encode("utf-8")
            try:
                _preflight_json_payload(
                    encoded + b"\n",
                    maximum_depth=_MAX_QUERY_JSON_DEPTH,
                    maximum_tokens=min(_MAX_QUERY_JSON_NODES, max(1, node_budget[0])),
                )
                parsed = json.loads(
                    encoded.decode("utf-8"),
                    object_pairs_hook=_strict_json_object,
                    # Query-value inspection only needs the JSON shape and
                    # property names.  Avoid constructing attacker-sized
                    # numbers, but do not reject otherwise valid scalars.
                    parse_int=lambda _raw: 0,
                    parse_float=lambda _raw: 0.0,
                    parse_constant=_reject_json_number,
                )
            except (TypeError, ValueError, UnicodeDecodeError, RecursionError, json.JSONDecodeError):
                raise ValueError("invalid archive evidence URL") from None
            if _json_value_contains_sensitive_name(
                parsed,
                depth=depth + 1,
                remaining=node_budget,
            ):
                return True
        next_value = unquote(decoded)
        if next_value == decoded:
            stabilized = True
            break
        decoded = next_value
    if not stabilized:
        raise ValueError("invalid archive evidence URL")
    return False


def _legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    numeric_part = r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)"
    if not re.fullmatch(rf"{numeric_part}(?:\.{numeric_part})*", host):
        return None
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        raise ValueError("invalid archive evidence URL")

    values: list[int] = []
    for part in parts:
        try:
            if part.casefold().startswith("0x"):
                value = int(part[2:], 16)
            elif len(part) > 1 and part.startswith("0"):
                value = int(part, 8)
            else:
                value = int(part, 10)
        except ValueError:
            raise ValueError("invalid archive evidence URL") from None
        values.append(value)
    limits = {
        1: (0xFFFFFFFF,),
        2: (0xFF, 0xFFFFFF),
        3: (0xFF, 0xFF, 0xFFFF),
        4: (0xFF, 0xFF, 0xFF, 0xFF),
    }[len(values)]
    if any(value < 0 or value > maximum for value, maximum in zip(values, limits)):
        raise ValueError("invalid archive evidence URL")
    if len(values) == 1:
        encoded = values[0]
    elif len(values) == 2:
        encoded = (values[0] << 24) | values[1]
    elif len(values) == 3:
        encoded = (values[0] << 24) | (values[1] << 16) | values[2]
    else:
        encoded = sum(value << shift for value, shift in zip(values, (24, 16, 8, 0)))
    return ipaddress.IPv4Address(encoded)


def _public_host(host: str) -> tuple[str, bool]:
    candidate = host.rstrip(".")
    if "%" in candidate:
        raise ValueError("invalid archive evidence URL")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        legacy = _legacy_ipv4(candidate)
        if legacy is not None:
            address = legacy
        else:
            try:
                canonical = candidate.encode("idna").decode("ascii").casefold()
            except UnicodeError:
                raise ValueError("invalid archive evidence URL") from None
            if (
                not canonical
                or "." not in canonical
                or len(canonical) > 253
                or any(
                    len(label) > 63
                    or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                    for label in canonical.split(".")
                )
                or canonical in {"localhost", "localhost.localdomain"}
                or canonical.endswith(_SPECIAL_HOST_SUFFIXES)
            ):
                raise ValueError("invalid archive evidence URL")
            return canonical, False

    if isinstance(address, ipaddress.IPv6Address):
        if (
            address.ipv4_mapped is not None
            or address.sixtofour is not None
            or address.teredo is not None
            or any(address in network for network in _NAT64_NETWORKS)
            or address in _IPV4_COMPATIBLE_NETWORK
        ):
            raise ValueError("invalid archive evidence URL")
    if not address.is_global:
        raise ValueError("invalid archive evidence URL")
    return address.compressed.casefold(), isinstance(address, ipaddress.IPv6Address)


def _canonical_public_path(value: str) -> str:
    source = value or "/"
    parts: list[str] = []
    cursor = 0
    while cursor < len(source):
        character = source[cursor]
        if character == "%":
            if cursor + 2 >= len(source) or re.fullmatch(r"[0-9A-Fa-f]{2}", source[cursor + 1:cursor + 3]) is None:
                raise ValueError("invalid archive evidence URL")
            encoded = int(source[cursor + 1:cursor + 3], 16)
            if encoded in _UNRESERVED_PATH_BYTES:
                parts.append(chr(encoded))
            else:
                parts.append(f"%{encoded:02X}")
            cursor += 3
            continue
        parts.append(quote(character, safe=_PATH_SAFE_CHARACTERS))
        cursor += 1
    return "".join(parts)


def _archive_public_url(value: object) -> str:
    if value == "":
        return ""
    if type(value) is not str or len(value) > 8_192:
        raise ValueError("invalid archive evidence URL")
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        raise ValueError("invalid archive evidence URL") from None
    if parsed.fragment:
        raise ValueError("invalid archive evidence URL")
    decoded_query = parsed.query
    stabilized = False
    for _ in range(_MAX_QUERY_DECODE_ROUNDS):
        if _NESTED_SECRET.search(decoded_query):
            raise ValueError("invalid archive evidence URL")
        try:
            decoded_pairs = parse_qsl(decoded_query, keep_blank_values=True, strict_parsing=False)
        except ValueError:
            raise ValueError("invalid archive evidence URL") from None
        if any(
            _query_name_is_sensitive(key)
            or _decoded_query_value_contains_sensitive_name(query_value)
            for key, query_value in decoded_pairs
        ):
            raise ValueError("invalid archive evidence URL")
        next_value = unquote(decoded_query)
        if next_value == decoded_query:
            stabilized = True
            break
        decoded_query = next_value
    if not stabilized:
        raise ValueError("invalid archive evidence URL")
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        raise ValueError("invalid archive evidence URL") from None
    if any(
        _query_name_is_sensitive(key)
        or _decoded_query_value_contains_sensitive_name(query_value)
        for key, query_value in pairs
    ):
        raise ValueError("invalid archive evidence URL")
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid archive evidence URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("invalid archive evidence URL")
    bracketed_authority = parsed.netloc.rsplit("@", 1)[-1]
    if bracketed_authority.startswith("["):
        closing = bracketed_authority.find("]")
        if closing < 0:
            raise ValueError("invalid archive evidence URL")
        literal = bracketed_authority[1:closing]
        if re.fullmatch(r"v[0-9a-f]+\..+", literal, re.IGNORECASE):
            raise ValueError("invalid archive evidence URL")
        try:
            ipaddress.IPv6Address(literal)
        except ValueError:
            raise ValueError("invalid archive evidence URL") from None
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("invalid archive evidence URL") from None
    host, is_ipv6 = _public_host(parsed.hostname)
    scheme = parsed.scheme.casefold()
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    authority_host = f"[{host}]" if is_ipv6 else host
    authority = authority_host if port is None or default_port else f"{authority_host}:{port}"
    normalized_path = _canonical_public_path(parsed.path)
    if not normalized_path.startswith("/"):
        raise ValueError("invalid archive evidence URL")
    public_query = urlencode(sorted(
        (key, item) for key, item in pairs if key.casefold() not in _TRACKING_QUERY_KEYS
    ), doseq=True)
    return urlunsplit((scheme, authority, normalized_path, public_query, ""))


def _sanitize_archive_urls(row: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(row)
    for collection_name in _EVIDENCE_COLLECTION_KEYS:
        items: list[dict[str, Any]] = []
        for value in row[collection_name]:
            item = dict(value)
            item["canonical_url"] = _archive_public_url(item["canonical_url"])
            items.append(item)
        sanitized[collection_name] = items
    return sanitized


def _json_string_bytes(value: str, *, maximum_bytes: int, error: str) -> int:
    if len(value) + 2 > maximum_bytes:
        raise ValueError(error)
    encoded_bytes = 2
    for character in value:
        codepoint = ord(character)
        if character in {'"', "\\"} or character in {"\b", "\f", "\n", "\r", "\t"}:
            encoded_bytes += 2
        elif codepoint < 0x20:
            encoded_bytes += 6
        elif codepoint <= 0x7F:
            encoded_bytes += 1
        elif codepoint <= 0x7FF:
            encoded_bytes += 2
        elif 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("archive text is invalid")
        elif codepoint <= 0xFFFF:
            encoded_bytes += 3
        else:
            encoded_bytes += 4
        if encoded_bytes > maximum_bytes:
            raise ValueError(error)
    return encoded_bytes


def _json_metrics(
    value: object,
    *,
    maximum_bytes: int,
    maximum_nodes: int,
    depth: int = 0,
) -> tuple[int, int]:
    if depth > 16:
        raise ValueError("archive document is too deep")
    if value is None:
        encoded_bytes = 4
    elif type(value) is bool:
        encoded_bytes = 4 if value else 5
    elif type(value) is int:
        if value < -1_000_000_000 or value > 1_000_000_000:
            raise ValueError("archive integer is too large")
        encoded_bytes = len(str(value).encode("ascii"))
    elif type(value) is str:
        if len(value) > 8_192:
            raise ValueError("archive text is too large")
        encoded_bytes = _json_string_bytes(
            value,
            maximum_bytes=maximum_bytes,
            error="archive document budget exceeded",
        )
    elif type(value) is list:
        if len(value) > 5_000:
            raise ValueError("archive list is too large")
        encoded_bytes = 2 + max(0, len(value) - 1)
        nodes = 1
        if encoded_bytes > maximum_bytes or nodes > maximum_nodes:
            raise ValueError("archive document budget exceeded")
        for item in value:
            item_bytes, item_nodes = _json_metrics(
                item,
                maximum_bytes=maximum_bytes - encoded_bytes,
                maximum_nodes=maximum_nodes - nodes,
                depth=depth + 1,
            )
            encoded_bytes += item_bytes
            nodes += item_nodes
            if encoded_bytes > maximum_bytes or nodes > maximum_nodes:
                raise ValueError("archive document budget exceeded")
        return encoded_bytes, nodes
    elif type(value) is dict:
        if len(value) > 5_000:
            raise ValueError("archive object is too large")
        encoded_bytes = 2 + max(0, len(value) - 1)
        nodes = 1
        if encoded_bytes > maximum_bytes or nodes > maximum_nodes:
            raise ValueError("archive document budget exceeded")
        for key, item in value.items():
            if type(key) is not str or len(key) > 128:
                raise ValueError("archive object key is invalid")
            key_bytes, key_nodes = _json_metrics(
                key,
                maximum_bytes=maximum_bytes - encoded_bytes,
                maximum_nodes=maximum_nodes - nodes,
                depth=depth + 1,
            )
            encoded_bytes += key_bytes + 1
            nodes += key_nodes
            item_bytes, item_nodes = _json_metrics(
                item,
                maximum_bytes=maximum_bytes - encoded_bytes,
                maximum_nodes=maximum_nodes - nodes,
                depth=depth + 1,
            )
            encoded_bytes += item_bytes
            nodes += item_nodes
            if encoded_bytes > maximum_bytes or nodes > maximum_nodes:
                raise ValueError("archive document budget exceeded")
        return encoded_bytes, nodes
    else:
        raise ValueError("archive value type is invalid")
    if encoded_bytes > maximum_bytes or maximum_nodes < 1:
        raise ValueError("archive document budget exceeded")
    return encoded_bytes, 1


def _row_resources(row: dict[str, Any]) -> int:
    return (
        1
        + len(row["related_tags"])
        + len(row["key_fields"])
        + len(row["status_history"])
        + len(row["snapshot_history"])
        + sum(len(row[key]) for key in _EVIDENCE_COLLECTION_KEYS)
    )


def _ensure_snapshot_budget(rows: list[dict[str, Any]]) -> None:
    encoded_bytes = 0
    nodes = 1
    resources = 0
    for row in rows:
        try:
            row_bytes, row_nodes = _json_metrics(
                row,
                maximum_bytes=min(_MAX_ROW_BYTES, _MAX_SNAPSHOT_BYTES - encoded_bytes),
                maximum_nodes=_MAX_SNAPSHOT_NODES - nodes,
            )
        except ValueError as error:
            if "budget" in str(error):
                raise ValueError("archive snapshot budget exceeded") from None
            raise
        if row_bytes > _MAX_ROW_BYTES:
            raise ValueError("archive snapshot row budget exceeded")
        encoded_bytes += row_bytes + 1
        nodes += row_nodes
        resources += _row_resources(row)
        if (
            encoded_bytes > _MAX_SNAPSHOT_BYTES
            or nodes > _MAX_SNAPSHOT_NODES
            or resources > _MAX_SNAPSHOT_RESOURCES
        ):
            raise ValueError("archive snapshot budget exceeded")


def _input_metrics(
    value: object,
    *,
    maximum_bytes: int,
    maximum_nodes: int,
    depth: int = 0,
) -> tuple[int, int]:
    """Bound the frozen input graph before snapshot_document allocates nested copies."""
    if depth > 16:
        raise ValueError("archive snapshot is too deep")
    if isinstance(value, Enum):
        value = value.value
    elif type(value) is datetime:
        value = _timestamp(value, "snapshot input time")

    if value is None:
        encoded_bytes = 4
    elif type(value) is bool:
        encoded_bytes = 4 if value else 5
    elif type(value) is int:
        if value < -1_000_000_000 or value > 1_000_000_000:
            raise ValueError("archive snapshot integer is too large")
        encoded_bytes = len(str(value))
    elif type(value) is str:
        if len(value) > _MAX_ROW_BYTES:
            raise ValueError("archive snapshot text is too large")
        encoded_bytes = _json_string_bytes(
            value,
            maximum_bytes=min(_MAX_ROW_BYTES, maximum_bytes),
            error="archive snapshot budget exceeded",
        )
    elif type(value) in {tuple, list}:
        if len(value) > 5_000:
            raise ValueError("archive snapshot list is too large")
        encoded_bytes = 2 + max(0, len(value) - 1)
        nodes = 1
        for item in value:
            item_bytes, item_nodes = _input_metrics(
                item,
                maximum_bytes=maximum_bytes - encoded_bytes,
                maximum_nodes=maximum_nodes - nodes,
                depth=depth + 1,
            )
            encoded_bytes += item_bytes
            nodes += item_nodes
            if encoded_bytes > maximum_bytes or nodes > maximum_nodes:
                raise ValueError("archive snapshot budget exceeded")
        return encoded_bytes, nodes
    elif type(value) is dict:
        if len(value) > 5_000:
            raise ValueError("archive snapshot object is too large")
        encoded_bytes = 2 + max(0, len(value) - 1)
        nodes = 1
        for key, item in value.items():
            if type(key) is not str or len(key) > 128:
                raise ValueError("archive snapshot object key is invalid")
            key_bytes, key_nodes = _input_metrics(
                key,
                maximum_bytes=maximum_bytes - encoded_bytes,
                maximum_nodes=maximum_nodes - nodes,
                depth=depth + 1,
            )
            encoded_bytes += key_bytes + 1
            nodes += key_nodes
            item_bytes, item_nodes = _input_metrics(
                item,
                maximum_bytes=maximum_bytes - encoded_bytes,
                maximum_nodes=maximum_nodes - nodes,
                depth=depth + 1,
            )
            encoded_bytes += item_bytes
            nodes += item_nodes
            if encoded_bytes > maximum_bytes or nodes > maximum_nodes:
                raise ValueError("archive snapshot budget exceeded")
        return encoded_bytes, nodes
    elif is_dataclass(value) and not isinstance(value, type):
        encoded_bytes = 2
        nodes = 1
        dataclass_fields = fields(value)
        if len(dataclass_fields) > 128:
            raise ValueError("archive snapshot object is too large")
        for index, field in enumerate(dataclass_fields):
            if index:
                encoded_bytes += 1
            key_bytes, key_nodes = _input_metrics(
                field.name,
                maximum_bytes=maximum_bytes - encoded_bytes,
                maximum_nodes=maximum_nodes - nodes,
                depth=depth + 1,
            )
            encoded_bytes += key_bytes + 1
            nodes += key_nodes
            item_bytes, item_nodes = _input_metrics(
                getattr(value, field.name),
                maximum_bytes=maximum_bytes - encoded_bytes,
                maximum_nodes=maximum_nodes - nodes,
                depth=depth + 1,
            )
            encoded_bytes += item_bytes
            nodes += item_nodes
            if encoded_bytes > maximum_bytes or nodes > maximum_nodes:
                raise ValueError("archive snapshot budget exceeded")
        return encoded_bytes, nodes
    else:
        raise ValueError("archive snapshot input type is invalid")
    if encoded_bytes > maximum_bytes or maximum_nodes < 1:
        raise ValueError("archive snapshot budget exceeded")
    return encoded_bytes, 1


def _preflight_snapshot_input(snapshot: EvidenceSnapshot) -> None:
    if type(snapshot) is not EvidenceSnapshot or type(snapshot.events) is not tuple:
        raise ValueError("invalid archive snapshot input")
    resources = 0
    for selected in snapshot.events:
        try:
            resources += (
                1
                + len(selected.related_tags)
                + len(selected.key_fields)
                + len(selected.status_history)
                + len(selected.primary_evidence)
                + len(selected.independent_evidence)
                + len(selected.syndicated_copies)
                + len(selected.contradicting_evidence)
            )
        except (AttributeError, TypeError):
            raise ValueError("invalid archive snapshot input") from None
        if resources > _MAX_SNAPSHOT_RESOURCES:
            raise ValueError("archive snapshot resource budget exceeded")
    _input_metrics(
        snapshot,
        maximum_bytes=_MAX_SNAPSHOT_BYTES,
        maximum_nodes=_MAX_SNAPSHOT_NODES,
    )


def _preflight_json_payload(raw: bytes, *, maximum_depth: int, maximum_tokens: int) -> None:
    if not raw or raw[-1:] != b"\n" or raw.count(b"\n") != 1:
        raise ValueError("invalid archive JSON framing")
    stack: list[int] = []
    in_string = False
    escaped = False
    tokens = 0
    for value in raw[:-1]:
        if in_string:
            if escaped:
                escaped = False
            elif value == 0x5C:
                escaped = True
            elif value == 0x22:
                in_string = False
            elif value < 0x20:
                raise ValueError("invalid archive JSON string")
            continue
        if value == 0x22:
            in_string = True
            tokens += 1
        elif value in {0x7B, 0x5B}:
            stack.append(value)
            tokens += 1
            if len(stack) > maximum_depth:
                raise ValueError("archive JSON is too deep")
        elif value in {0x7D, 0x5D}:
            expected = 0x7B if value == 0x7D else 0x5B
            if not stack or stack.pop() != expected:
                raise ValueError("invalid archive JSON structure")
            tokens += 1
        elif value in {0x2C, 0x3A}:
            tokens += 1
        if tokens > maximum_tokens:
            raise ValueError("archive JSON token budget exceeded")
    if in_string or escaped or stack:
        raise ValueError("invalid archive JSON structure")


def _decode_index_json_string(
    data: bytes,
    cursor: int,
    *,
    maximum_characters: int = 128,
) -> tuple[str, int]:
    if cursor >= len(data) or data[cursor] != 0x22:
        raise ValueError("invalid archive index string")
    cursor += 1
    segment_start = cursor
    parts: list[str] = []
    character_count = 0

    def append_raw(end: int) -> None:
        nonlocal character_count
        raw_segment = data[segment_start:end]
        if len(raw_segment) > (maximum_characters - character_count) * 4:
            raise ValueError("invalid archive index string")
        decoded = raw_segment.decode("utf-8")
        character_count += len(decoded)
        if character_count > maximum_characters:
            raise ValueError("invalid archive index string")
        if decoded:
            parts.append(decoded)

    while cursor < len(data):
        value = data[cursor]
        if value == 0x22:
            append_raw(cursor)
            return "".join(parts), cursor + 1
        if value < 0x20:
            raise ValueError("invalid archive index string")
        if value != 0x5C:
            if cursor - segment_start > maximum_characters * 4:
                raise ValueError("invalid archive index string")
            cursor += 1
            continue

        append_raw(cursor)
        cursor += 1
        if cursor >= len(data):
            raise ValueError("invalid archive index string")
        escape = data[cursor]
        simple_escape = {
            0x22: '"',
            0x5C: "\\",
            0x2F: "/",
            0x62: "\b",
            0x66: "\f",
            0x6E: "\n",
            0x72: "\r",
            0x74: "\t",
        }.get(escape)
        if simple_escape is not None:
            parts.append(simple_escape)
            character_count += 1
            cursor += 1
            segment_start = cursor
            if character_count > maximum_characters:
                raise ValueError("invalid archive index string")
            continue
        if escape != 0x75 or cursor + 5 > len(data):
            raise ValueError("invalid archive index string")
        codepoint_bytes = data[cursor + 1:cursor + 5]
        if len(codepoint_bytes) != 4 or any(value not in _JSON_HEX_BYTES for value in codepoint_bytes):
            raise ValueError("invalid archive index string")
        try:
            codepoint = int(codepoint_bytes.decode("ascii"), 16)
        except (UnicodeDecodeError, ValueError):
            raise ValueError("invalid archive index string") from None
        cursor += 5
        if 0xD800 <= codepoint <= 0xDBFF:
            if cursor + 6 > len(data) or data[cursor:cursor + 2] != b"\\u":
                raise ValueError("invalid archive index string")
            low_bytes = data[cursor + 2:cursor + 6]
            if len(low_bytes) != 4 or any(value not in _JSON_HEX_BYTES for value in low_bytes):
                raise ValueError("invalid archive index string")
            try:
                low = int(low_bytes.decode("ascii"), 16)
            except (UnicodeDecodeError, ValueError):
                raise ValueError("invalid archive index string") from None
            if not 0xDC00 <= low <= 0xDFFF:
                raise ValueError("invalid archive index string")
            codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + (low - 0xDC00)
            cursor += 6
        elif 0xDC00 <= codepoint <= 0xDFFF:
            raise ValueError("invalid archive index string")
        parts.append(chr(codepoint))
        character_count += 1
        if character_count > maximum_characters:
            raise ValueError("invalid archive index string")
        segment_start = cursor
    raise ValueError("invalid archive index string")


def _preparse_index_payload(raw: bytes, *, budget: dict[str, int]) -> int:
    if not raw or raw[-1:] != b"\n" or raw.count(b"\n") != 1:
        raise ValueError("invalid archive JSON framing")
    if any(type(budget.get(name)) is not int or budget[name] < 0 for name in ("nodes", "rows")):
        raise ValueError("invalid archive index budget")
    data = raw[:-1]
    cursor = 0

    def skip_space(position: int) -> int:
        while position < len(data) and data[position] in b" \t\r\n":
            position += 1
        return position

    def expect(position: int, token: int) -> int:
        position = skip_space(position)
        if position >= len(data) or data[position] != token:
            raise ValueError("invalid archive index")
        return position + 1

    def parse_events(position: int) -> tuple[int, int]:
        position = expect(position, 0x7B)
        position = skip_space(position)
        event_ids: set[str] = set()
        entry_count = 0
        if position < len(data) and data[position] == 0x7D:
            return position + 1, 0
        while True:
            event_id, position = _decode_index_json_string(data, position)
            if event_id in event_ids:
                raise ValueError("duplicate archive index event")
            event_ids.add(event_id)
            entry_count += 1
            if entry_count > _MAX_INDEX_EVENTS or entry_count > budget["rows"]:
                raise ValueError("archive index row budget exceeded")
            position = expect(position, 0x3A)
            position = skip_space(position)
            if position >= len(data) or data[position] != 0x22:
                raise ValueError("invalid archive index events")
            _bucket, position = _decode_index_json_string(data, position)
            position = skip_space(position)
            if position >= len(data):
                raise ValueError("invalid archive index events")
            if data[position] == 0x7D:
                return position + 1, entry_count
            if data[position] != 0x2C:
                raise ValueError("invalid archive index events")
            position = skip_space(position + 1)
            if position >= len(data) or data[position] == 0x7D:
                raise ValueError("invalid archive index events")

    cursor = expect(cursor, 0x7B)
    cursor = skip_space(cursor)
    root_keys: set[str] = set()
    entry_count: int | None = None
    schema_version_seen = False
    if cursor < len(data) and data[cursor] == 0x7D:
        raise ValueError("invalid archive index")
    while True:
        key, cursor = _decode_index_json_string(data, cursor)
        if key in root_keys:
            raise ValueError("duplicate archive index key")
        if key not in _INDEX_KEYS:
            raise ValueError("invalid archive index key")
        root_keys.add(key)
        cursor = expect(cursor, 0x3A)
        cursor = skip_space(cursor)
        if key == "schema_version":
            if cursor >= len(data) or data[cursor] != 0x31:
                raise ValueError("invalid archive index schema")
            cursor += 1
            schema_version_seen = True
        else:
            cursor, entry_count = parse_events(cursor)
        cursor = skip_space(cursor)
        if cursor >= len(data):
            raise ValueError("invalid archive index")
        if data[cursor] == 0x7D:
            cursor += 1
            break
        if data[cursor] != 0x2C:
            raise ValueError("invalid archive index")
        cursor = skip_space(cursor + 1)
        if cursor >= len(data) or data[cursor] == 0x7D:
            raise ValueError("invalid archive index")
    cursor = skip_space(cursor)
    if (
        cursor != len(data)
        or root_keys != _INDEX_KEYS
        or not schema_version_seen
        or entry_count is None
    ):
        raise ValueError("invalid archive index")
    document_nodes = 5 + entry_count * 2
    if document_nodes > budget["nodes"]:
        raise ValueError("archive index node budget exceeded")
    budget["nodes"] -= document_nodes
    budget["rows"] -= entry_count
    return entry_count


def _preflight_top_level_container_entries(
    raw: bytes,
    *,
    key: str,
    opening: int,
    maximum_entries: int,
    require_key: bool = False,
    observed_keys: set[str] | None = None,
) -> int:
    if (
        type(key) is not str
        or not key
        or len(key) > 128
        or opening not in {0x5B, 0x7B}
        or type(maximum_entries) is not int
        or maximum_entries < 0
    ):
        raise ValueError("invalid archive container budget")
    data = raw[:-1]
    stripped = data.strip()
    if not stripped or stripped[:1] != b"{" or stripped[-1:] != b"}":
        raise ValueError("invalid archive JSON root")
    depth = 0
    in_string = False
    escaped = False
    string_start = 0
    entry_count = 0
    matched_key = False
    index = 0
    while index < len(data):
        value = data[index]
        if in_string:
            if escaped:
                escaped = False
            elif value == 0x5C:
                escaped = True
            elif value == 0x22:
                in_string = False
                if depth == 1:
                    cursor = index + 1
                    while cursor < len(data) and data[cursor] in b" \t\r\n":
                        cursor += 1
                    if cursor < len(data) and data[cursor] == 0x3A:
                        raw_key = data[string_start:index + 1]
                        if len(raw_key) > 130:
                            raise ValueError("invalid archive JSON key")
                        try:
                            decoded_key = json.loads(raw_key.decode("utf-8"))
                        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                            raise ValueError("invalid archive JSON key") from None
                        if observed_keys is not None:
                            observed_keys.add(decoded_key)
                        if decoded_key == key:
                            if matched_key:
                                raise ValueError(f"duplicate archive {key}")
                            matched_key = True
                            cursor += 1
                            while cursor < len(data) and data[cursor] in b" \t\r\n":
                                cursor += 1
                            if cursor >= len(data) or data[cursor] != opening:
                                raise ValueError(f"invalid archive {key}")
                            if cursor < len(data) and data[cursor] == opening:
                                array_depth = 1
                                array_string = False
                                array_escaped = False
                                expecting_value = True
                                expected_close = 0x5D if opening == 0x5B else 0x7D
                                cursor += 1
                                while cursor < len(data):
                                    token = data[cursor]
                                    if array_string:
                                        if array_escaped:
                                            array_escaped = False
                                        elif token == 0x5C:
                                            array_escaped = True
                                        elif token == 0x22:
                                            array_string = False
                                        cursor += 1
                                        continue
                                    if token == 0x22:
                                        if array_depth == 1 and expecting_value:
                                            entry_count += 1
                                            expecting_value = False
                                        array_string = True
                                    elif token in {0x7B, 0x5B}:
                                        if array_depth == 1 and expecting_value:
                                            entry_count += 1
                                            expecting_value = False
                                        array_depth += 1
                                    elif token in {0x7D, 0x5D}:
                                        if array_depth == 1 and token != expected_close:
                                            raise ValueError(f"invalid archive {key}")
                                        array_depth -= 1
                                        if array_depth == 0:
                                            break
                                    elif array_depth == 1 and token == 0x2C:
                                        expecting_value = True
                                    elif array_depth == 1 and token not in b" \t\r\n" and expecting_value:
                                        entry_count += 1
                                        expecting_value = False
                                    if entry_count > maximum_entries:
                                        raise ValueError("archive container budget exceeded")
                                    cursor += 1
                                if array_depth != 0:
                                    raise ValueError(f"invalid archive {key}")
            index += 1
            continue
        if value == 0x22:
            in_string = True
            string_start = index
        elif value in {0x7B, 0x5B}:
            depth += 1
        elif value in {0x7D, 0x5D}:
            depth -= 1
        index += 1
    if require_key and not matched_key:
        raise ValueError(f"missing archive {key}")
    return entry_count


def _preflight_top_level_array_rows(raw: bytes, *, key: str, maximum_rows: int) -> int:
    return _preflight_top_level_container_entries(
        raw,
        key=key,
        opening=0x5B,
        maximum_entries=maximum_rows,
    )


def _preflight_top_level_object_entries(
    raw: bytes,
    *,
    key: str,
    maximum_entries: int,
    require_key: bool = False,
) -> int:
    return _preflight_top_level_container_entries(
        raw,
        key=key,
        opening=0x7B,
        maximum_entries=maximum_entries,
        require_key=require_key,
    )


def _count_bucket_lines(raw: bytes, *, maximum_rows: int) -> int:
    if type(maximum_rows) is not int or maximum_rows < 0:
        raise ValueError("invalid archive row budget")
    count = 0
    cursor = 0
    line_start = 0
    while cursor < len(raw):
        value = raw[cursor]
        if value not in {0x0A, 0x0D}:
            cursor += 1
            continue
        count += 1
        if count > maximum_rows:
            raise ValueError("archive row budget exceeded")
        if value == 0x0D and cursor + 1 < len(raw) and raw[cursor + 1] == 0x0A:
            cursor += 2
        else:
            cursor += 1
        line_start = cursor
    if line_start < len(raw):
        count += 1
        if count > maximum_rows:
            raise ValueError("archive row budget exceeded")
    return count


def _iter_bucket_lines(raw: bytes) -> Iterator[bytes]:
    cursor = 0
    line_start = 0
    while cursor < len(raw):
        value = raw[cursor]
        if value not in {0x0A, 0x0D}:
            cursor += 1
            continue
        if value == 0x0D and cursor + 1 < len(raw) and raw[cursor + 1] == 0x0A:
            cursor += 2
        else:
            cursor += 1
        yield raw[line_start:cursor]
        line_start = cursor
    if line_start < len(raw):
        yield raw[line_start:]


def _validate_evidence_references(row: dict[str, Any]) -> None:
    _validate_evidence_identity_domain(row)
    available = {
        item["evidence_id"]
        for collection_name in _EVIDENCE_COLLECTION_KEYS
        for item in row[collection_name]
    }
    for field in row["key_fields"]:
        references = field["evidence_ids"]
        if len(references) != len(set(references)) or any(reference not in available for reference in references):
            raise ValueError("invalid archive key-field evidence reference")


def _validate_evidence_identity_domain(row: dict[str, Any]) -> None:
    evidence_ids: dict[str, tuple[str, str]] = {}
    canonical_urls: dict[str, tuple[str, str]] = {}
    for collection_name in _EVIDENCE_COLLECTION_KEYS:
        for item in row[collection_name]:
            evidence_id = item["evidence_id"]
            canonical_url = item["canonical_url"]
            if evidence_id in evidence_ids:
                raise ValueError("ambiguous archive evidence identity")
            evidence_ids[evidence_id] = (collection_name, canonical_url)
            if canonical_url:
                if canonical_url in canonical_urls:
                    raise ValueError("ambiguous archive evidence identity")
                canonical_urls[canonical_url] = (collection_name, evidence_id)


def _row_times(row: dict[str, Any]) -> Iterator[datetime]:
    for key in ("published_at", "verified_at", "evidence_as_of"):
        value = row[key]
        if value is not None:
            yield _metadata_time(value, key)
    for collection_name in _EVIDENCE_COLLECTION_KEYS:
        for item in row[collection_name]:
            if item["published_at"] is not None:
                yield _metadata_time(item["published_at"], "evidence published_at")
    for transition in row["status_history"]:
        yield _metadata_time(transition["changed_at"], "status changed_at")


def _validate_temporal_row(row: dict[str, Any], now: datetime) -> None:
    current = _utc(now, "archive clock")
    maximum = _shift_datetime(current, _MAX_CLOCK_SKEW)
    generated_at = _metadata_time(row["snapshot_generated_at"], "snapshot_generated_at")
    archived_at = _metadata_time(row["archived_at"], "archived_at")
    if generated_at > maximum or archived_at > maximum:
        raise ValueError("invalid archive time")
    if len(row["snapshot_history"]) == 1 and generated_at > _shift_datetime(archived_at, _MAX_CLOCK_SKEW):
        raise ValueError("invalid archive time")
    snapshot_maximum = _shift_datetime(generated_at, _MAX_CLOCK_SKEW)
    status_times = [
        _metadata_time(transition["changed_at"], "status changed_at")
        for transition in row["status_history"]
    ]
    if status_times != sorted(status_times):
        raise ValueError("invalid archive time order")
    history = row["status_history"]
    if not history:
        raise ValueError("invalid archive status history")
    previous_status: str | None = None
    for index, transition in enumerate(history):
        reason = transition["reason"]
        if not reason.strip():
            raise ValueError("invalid archive status reason")
        if transition["from_status"] is None:
            previous_status = transition["to_status"]
            continue
        if previous_status is None or transition["from_status"] != previous_status:
            raise ValueError("invalid archive status history continuity")
        if index > 0 and transition["from_status"] == transition["to_status"]:
            raise ValueError("invalid archive status history transition")
        previous_status = transition["to_status"]
    if previous_status != row["verification_status"]:
        raise ValueError("invalid archive status history final status")
    if not row["verification_reason"].strip() or row["verification_reason"] != history[-1]["reason"]:
        raise ValueError("invalid archive final status reason")
    verified_at = _metadata_time(row["verified_at"], "verified_at")
    evidence_as_of = _metadata_time(row["evidence_as_of"], "evidence_as_of")
    if any(value > verified_at for value in status_times) or evidence_as_of > verified_at:
        raise ValueError("invalid archive verification time")
    if row["published_at"] is not None and _metadata_time(row["published_at"], "published_at") > verified_at:
        raise ValueError("invalid archive publication time")
    for collection_name in _EVIDENCE_COLLECTION_KEYS:
        for item in row[collection_name]:
            if item["published_at"] is not None and _metadata_time(
                item["published_at"], "evidence published_at"
            ) > evidence_as_of:
                raise ValueError("invalid archive evidence time")
    for value in _row_times(row):
        if value > maximum or value > snapshot_maximum:
            raise ValueError("invalid archive time")
    lineage_times = [
        _metadata_time(lineage["generated_at"], "lineage generated_at")
        for lineage in row["snapshot_history"]
    ]
    if (
        lineage_times != sorted(lineage_times)
        or any(value > maximum or value > generated_at for value in lineage_times)
    ):
        raise ValueError("invalid archive time order")


def _archive_document(snapshot: EvidenceSnapshot, row: dict[str, Any], archived_at: datetime) -> dict[str, Any]:
    archived_event = _sanitize_archive_urls(row)
    archived_event["title"] = archived_event["title"][:500]
    archived_event["summary"] = archived_event["summary"][:1_200]
    archived_event["core_claim"] = archived_event["core_claim"][:1_200]
    _validate_evidence_references(archived_event)
    content_digest = _event_content_digest(archived_event)
    lineage = _lineage_document(snapshot, content_digest)
    document = {
        "schema_version": _ARCHIVE_SCHEMA_VERSION,
        **archived_event,
        "evidence_snapshot_id": lineage["evidence_snapshot_id"],
        "raw_snapshot_id": lineage["raw_snapshot_id"],
        "snapshot_generated_at": lineage["generated_at"],
        "archived_at": _timestamp(archived_at, "archived_at"),
        "last_updated_at": lineage["generated_at"],
        "snapshot_history": [lineage],
        "content_digest": content_digest,
    }
    _exact_builtin(document)
    return document


def _archive_from_document(value: object) -> dict[str, Any]:
    _exact_builtin(value)
    if type(value) is not dict:
        raise ValueError("invalid archive row schema")
    schema_version = value.get("schema_version")
    if type(schema_version) is not int:
        raise ValueError("invalid archive row schema")
    if schema_version == _ARCHIVE_SCHEMA_VERSION:
        if set(value) != _ARCHIVE_KEYS:
            raise ValueError("invalid archive row schema")
        legacy = False
    elif schema_version == _LEGACY_ARCHIVE_SCHEMA_VERSION:
        if set(value) != _LEGACY_ARCHIVE_KEYS:
            raise ValueError("invalid archive row schema")
        legacy = True
    else:
        if schema_version > _ARCHIVE_SCHEMA_VERSION:
            raise _ArchiveFutureSchemaError("unsupported archive row schema")
        raise ValueError("invalid archive row schema")
    event = _event_from_document({key: value[key] for key in _EVENT_KEYS})
    canonical_event = event_document(event)
    if canonical_event != {key: value[key] for key in _EVENT_KEYS}:
        raise ValueError("archive event is not canonical")
    calculated_digest = _event_content_digest(canonical_event)
    content_digest = calculated_digest if legacy else _bounded_text(
        value["content_digest"],
        "archive content_digest",
        maximum=64,
    )
    if _CONTENT_DIGEST.fullmatch(content_digest) is None or calculated_digest != content_digest:
        raise ValueError("archive content digest mismatch")
    _validate_evidence_references(canonical_event)
    event_id = _bounded_text(value["event_id"], "event_id")
    evidence_snapshot_id = _bounded_text(value["evidence_snapshot_id"], "evidence_snapshot_id")
    raw_snapshot_id = _bounded_text(value["raw_snapshot_id"], "raw_snapshot_id")
    generated_at = _metadata_time(value["snapshot_generated_at"], "snapshot_generated_at")
    archived_at = _metadata_time(value["archived_at"], "archived_at")
    last_updated_at = _metadata_time(value["last_updated_at"], "last_updated_at")
    history = value["snapshot_history"]
    if type(history) is not list or not history:
        raise ValueError("invalid archive snapshot history")
    parsed_history = [
        _lineage_from_document(
            item,
            legacy=legacy,
            legacy_projection_digest=content_digest if legacy else None,
            legacy_unverifiable=legacy and len(history) > 1,
        )
        for item in history
    ]
    identities: dict[tuple[str, str, str], str] = {}
    for item in parsed_history:
        identity = (item["evidence_snapshot_id"], item["raw_snapshot_id"], item["generated_at"])
        previous_digest = identities.setdefault(identity, item["content_digest"])
        if previous_digest != item["content_digest"]:
            raise ValueError("archive lineage content mismatch")
    if len(identities) != len(parsed_history):
        raise ValueError("duplicate archive lineage")
    if parsed_history != sorted(parsed_history, key=_lineage_order):
        raise ValueError("archive lineage is not ordered")
    current_identity = (evidence_snapshot_id, raw_snapshot_id, generated_at.isoformat())
    if (
        current_identity not in identities
        or generated_at != max(_metadata_time(row["generated_at"], "lineage generated_at") for row in parsed_history)
        or last_updated_at != generated_at
    ):
        raise ValueError("archive lineage is inconsistent")
    result = {
        **canonical_event,
        "schema_version": _ARCHIVE_SCHEMA_VERSION,
        "evidence_snapshot_id": evidence_snapshot_id,
        "raw_snapshot_id": raw_snapshot_id,
        "snapshot_generated_at": generated_at.isoformat(),
        "archived_at": archived_at.isoformat(),
        "last_updated_at": last_updated_at.isoformat(),
        "snapshot_history": parsed_history,
        "content_digest": content_digest,
    }
    for collection_name in _EVIDENCE_COLLECTION_KEYS:
        for item in result[collection_name]:
            if _archive_public_url(item["canonical_url"]) != item["canonical_url"]:
                raise ValueError("archive evidence URL is not canonical")
    return result


def _validate_archive_projection(
    row: dict[str, Any],
    *,
    validation_now: datetime,
    bucket_name: str | None = None,
) -> dict[str, Any]:
    validated = _archive_from_document(row)
    _validate_temporal_row(validated, validation_now)
    if bucket_name is not None and _bucket_name(validated) != bucket_name:
        raise ValueError("archive row is stored in the wrong UTC bucket")
    return validated


def _legacy_archive_projection(row: dict[str, Any]) -> dict[str, Any]:
    projection = {
        key: row[key]
        for key in _LEGACY_ARCHIVE_KEYS
        if key not in {"schema_version", "snapshot_history"}
    }
    projection["schema_version"] = _LEGACY_ARCHIVE_SCHEMA_VERSION
    projection["snapshot_history"] = [
        {
            key: lineage[key]
            for key in _LEGACY_LINEAGE_KEYS
        }
        for lineage in row["snapshot_history"]
    ]
    return projection


def _mark_legacy_projection_unverifiable(
    row: dict[str, Any],
    *,
    validation_now: datetime,
) -> dict[str, Any]:
    marked = dict(row)
    history: list[dict[str, Any]] = []
    for lineage in row["snapshot_history"]:
        if lineage.get("legacy_v1") is not True:
            raise ValueError("invalid historical v1 archive projection")
        updated = dict(lineage)
        updated["legacy_unverifiable"] = True
        history.append(updated)
    marked["snapshot_history"] = history
    return _validate_archive_projection(marked, validation_now=validation_now)


def _lineage_order(row: dict[str, str]) -> tuple[datetime, str, str, str]:
    return (
        _parse_datetime(row["generated_at"]),
        row["evidence_snapshot_id"],
        row["raw_snapshot_id"],
        row["content_digest"],
    )


def _row_precedence(row: dict[str, Any]) -> tuple[datetime, int, str, str, str]:
    current_content = {key: row[key] for key in sorted(_EVENT_KEYS)}
    fingerprint = json.dumps(
        current_content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        _metadata_time(row["snapshot_generated_at"], "snapshot_generated_at"),
        _STATUS_PRECEDENCE[row["verification_status"]],
        row["evidence_snapshot_id"],
        row["raw_snapshot_id"],
        fingerprint,
    )


def _merge_by_key(
    loser: list[dict[str, Any]],
    winner: list[dict[str, Any]],
    *,
    key: Callable[[dict[str, Any]], tuple[object, ...]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[object, ...], dict[str, Any]] = {}
    for item in (*loser, *winner):
        identity = key(item)
        if (
            type(identity) is not tuple
            or not identity
            or any(type(value) not in {str, int, bool, type(None)} for value in identity)
        ):
            raise ValueError("invalid archive merge identity")
        merged[identity] = item
    return list(merged.values())


def _merge_key_fields(
    loser: list[dict[str, Any]],
    winner: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    references: dict[tuple[str, str], set[str]] = {}
    for item in (*loser, *winner):
        identity = (item["field_name"], item["verification_status"])
        merged[identity] = dict(item)
        references.setdefault(identity, set()).update(item["evidence_ids"])
    result: list[dict[str, Any]] = []
    for identity, item in merged.items():
        item["evidence_ids"] = sorted(references[identity])
        result.append(item)
    return result


def _merge_evidence(
    loser: list[dict[str, Any]],
    winner: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    loser_matches: dict[int, int] = {}
    winner_matches: dict[int, int] = {}
    winner_by_id = {item["evidence_id"]: index for index, item in enumerate(winner)}
    winner_by_url = {
        item["canonical_url"]: index
        for index, item in enumerate(winner)
        if item["canonical_url"]
    }
    for loser_index, loser_item in enumerate(loser):
        candidates: set[int] = set()
        if loser_item["evidence_id"] in winner_by_id:
            matched_index = winner_by_id[loser_item["evidence_id"]]
            matched_url = winner[matched_index]["canonical_url"]
            if loser_item["canonical_url"] and matched_url and loser_item["canonical_url"] != matched_url:
                raise ValueError("ambiguous archive evidence identity")
            candidates.add(matched_index)
        if loser_item["canonical_url"] in winner_by_url:
            candidates.add(winner_by_url[loser_item["canonical_url"]])
        if len(candidates) > 1:
            raise ValueError("ambiguous archive evidence identity")
        if candidates:
            winner_index = next(iter(candidates))
            if winner_index in winner_matches:
                raise ValueError("ambiguous archive evidence identity")
            loser_matches[loser_index] = winner_index
            winner_matches[winner_index] = loser_index

    aliases: dict[str, str] = {}
    result: list[dict[str, Any]] = []
    for loser_index, winner_index in loser_matches.items():
        pair = (loser[loser_index], winner[winner_index])
        canonical_id = min(item["evidence_id"] for item in pair)
        selected = dict(winner[winner_index])
        provenance_fields = (
            "canonical_url",
            "published_at",
            "content_source",
            "collector_source",
            "origin_cluster",
        )
        winner_provenance = tuple(selected[field] for field in provenance_fields)
        loser_provenance = tuple(loser[loser_index][field] for field in provenance_fields)
        winner_present = tuple(value not in {None, ""} for value in winner_provenance)
        loser_present = tuple(value not in {None, ""} for value in loser_provenance)
        if all(winner_present):
            pass
        elif not any(winner_present):
            if any(loser_present) and not all(loser_present):
                raise ValueError("invalid archive evidence provenance")
            for field, value in zip(provenance_fields, loser_provenance, strict=True):
                selected[field] = value
        else:
            raise ValueError("invalid archive evidence provenance")
        for content_field in ("title", "excerpt"):
            if selected[content_field] in {None, ""} and loser[loser_index][content_field] not in {None, ""}:
                selected[content_field] = loser[loser_index][content_field]
        selected["evidence_id"] = canonical_id
        result.append(selected)
        for item in pair:
            aliases[item["evidence_id"]] = canonical_id
    for index, item in enumerate(loser):
        if index not in loser_matches:
            result.append(dict(item))
            aliases[item["evidence_id"]] = item["evidence_id"]
    for index, item in enumerate(winner):
        if index not in winner_matches:
            result.append(dict(item))
            aliases[item["evidence_id"]] = item["evidence_id"]
    return sorted(result, key=lambda item: (item["evidence_id"], item["canonical_url"])), aliases


def _merge_evidence_collections(
    loser: dict[str, Any],
    winner: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    loser_items = [
        (collection_name, item)
        for collection_name in _EVIDENCE_COLLECTION_KEYS
        for item in loser[collection_name]
    ]
    winner_items = [
        (collection_name, item)
        for collection_name in _EVIDENCE_COLLECTION_KEYS
        for item in winner[collection_name]
    ]
    merged, aliases = _merge_evidence(
        [item for _collection_name, item in loser_items],
        [item for _collection_name, item in winner_items],
    )
    loser_roles = {
        aliases[item["evidence_id"]]: collection_name
        for collection_name, item in loser_items
    }
    winner_roles = {
        aliases[item["evidence_id"]]: collection_name
        for collection_name, item in winner_items
    }
    collections: dict[str, list[dict[str, Any]]] = {
        collection_name: [] for collection_name in _EVIDENCE_COLLECTION_KEYS
    }
    for item in merged:
        evidence_id = item["evidence_id"]
        collection_name = winner_roles.get(evidence_id, loser_roles.get(evidence_id))
        if collection_name is None:
            raise ValueError("ambiguous archive evidence identity")
        collections[collection_name].append(item)
    return collections, aliases


def _transition_identity(row: dict[str, Any]) -> tuple[object, ...]:
    return (
        row["changed_at"],
        row["from_status"],
        row["to_status"],
        row["reason"],
    )


def _status_history_is_continuous(history: list[dict[str, Any]], final_status: str) -> bool:
    if not history:
        return False
    previous: str | None = None
    for index, transition in enumerate(history):
        if transition["from_status"] is None:
            previous = transition["to_status"]
            continue
        if transition["from_status"] != previous:
            return False
        if index > 0 and transition["from_status"] == transition["to_status"]:
            return False
        previous = transition["to_status"]
    return previous == final_status


def _merge_status_history(
    loser: list[dict[str, Any]],
    winner: list[dict[str, Any]],
    final_status: str,
) -> list[dict[str, Any]]:
    winner_identities = {_transition_identity(row) for row in winner}
    loser_identities = {_transition_identity(row) for row in loser}
    if loser_identities <= winner_identities and _status_history_is_continuous(winner, final_status):
        return list(winner)
    combined = {
        _transition_identity(row): row
        for row in (*loser, *winner)
    }
    candidate = [
        combined[identity]
        for identity in sorted(
            combined,
            key=lambda item: (
                _parse_datetime(str(item[0])),
                _STATUS_PRECEDENCE[str(item[2])],
                str(item[1]),
                str(item[3]),
            ),
        )
    ]
    if _status_history_is_continuous(candidate, final_status):
        return candidate
    raise ValueError("invalid archive status history merge")


def _merge_archive_rows(
    previous: dict[str, Any],
    incoming: dict[str, Any],
    *,
    lineage_cutoff: datetime | None = None,
) -> dict[str, Any]:
    if previous["event_id"] != incoming["event_id"]:
        raise ValueError("archive event identity mismatch")
    use_incoming = _row_precedence(incoming) > _row_precedence(previous)
    current, other = (incoming, previous) if use_incoming else (previous, incoming)

    histories_by_identity = {
        (row["evidence_snapshot_id"], row["raw_snapshot_id"], row["generated_at"]): row
        for row in previous["snapshot_history"]
    }
    for row in incoming["snapshot_history"]:
        identity = (row["evidence_snapshot_id"], row["raw_snapshot_id"], row["generated_at"])
        previous_lineage = histories_by_identity.get(identity)
        if previous_lineage is None:
            histories_by_identity[identity] = row
            continue
        if previous_lineage["content_digest"] != row["content_digest"]:
            raise ValueError("archive lineage content mismatch")
        previous_legacy = previous_lineage.get("legacy_v1") is True
        incoming_legacy = row.get("legacy_v1") is True
        if previous_legacy and not incoming_legacy:
            histories_by_identity[identity] = row
    histories = list(histories_by_identity.values())
    histories.sort(key=_lineage_order)
    if lineage_cutoff is not None:
        histories = [
            row for row in histories
            if _metadata_time(row["generated_at"], "lineage generated_at") >= lineage_cutoff
        ]
    if not histories:
        histories = [max(
            (*previous["snapshot_history"], *incoming["snapshot_history"]),
            key=_lineage_order,
        )]
    merged = dict(current)
    if not current["title"] and other["title"]:
        merged["title"] = other["title"]
    if not current["summary"] and other["summary"]:
        merged["summary"] = other["summary"]
    merged["related_tags"] = _merge_by_key(
        list(other["related_tags"]),
        list(current["related_tags"]),
        key=lambda row: (row["id"],),
    )
    merged["key_fields"] = _merge_key_fields(
        list(other["key_fields"]),
        list(current["key_fields"]),
    )
    evidence_collections, aliases = _merge_evidence_collections(other, current)
    for key, items in evidence_collections.items():
        merged[key] = items
    available_evidence = {
        item["evidence_id"]
        for key in _EVIDENCE_COLLECTION_KEYS
        for item in merged[key]
    }
    remapped_fields: list[dict[str, Any]] = []
    for field in merged["key_fields"]:
        updated = dict(field)
        references = [aliases.get(evidence_id, evidence_id) for evidence_id in field["evidence_ids"]]
        updated["evidence_ids"] = sorted(set(references))
        if any(evidence_id not in available_evidence for evidence_id in updated["evidence_ids"]):
            raise ValueError("invalid archive key-field evidence reference")
        remapped_fields.append(updated)
    merged["key_fields"] = remapped_fields
    merged["status_history"] = _merge_status_history(
        list(other["status_history"]),
        list(current["status_history"]),
        current["verification_status"],
    )
    evidence_times = [
        _metadata_time(item["published_at"], "evidence published_at")
        for key in _EVIDENCE_COLLECTION_KEYS
        for item in merged[key]
        if item["published_at"] is not None
    ]
    evidence_as_of = max(
        _metadata_time(previous["evidence_as_of"], "evidence_as_of"),
        _metadata_time(incoming["evidence_as_of"], "evidence_as_of"),
        *evidence_times,
    )
    transition_times = [
        _metadata_time(transition["changed_at"], "status changed_at")
        for transition in merged["status_history"]
    ]
    publication_times = [
        _metadata_time(merged["published_at"], "published_at")
    ] if merged["published_at"] is not None else []
    verified_at = max(
        _metadata_time(previous["verified_at"], "verified_at"),
        _metadata_time(incoming["verified_at"], "verified_at"),
        evidence_as_of,
        *transition_times,
        *publication_times,
    )
    merged["evidence_as_of"] = evidence_as_of.isoformat()
    merged["verified_at"] = verified_at.isoformat()
    merged["snapshot_history"] = histories
    merged["evidence_snapshot_id"] = current["evidence_snapshot_id"]
    merged["raw_snapshot_id"] = current["raw_snapshot_id"]
    merged["snapshot_generated_at"] = current["snapshot_generated_at"]
    merged["last_updated_at"] = current["snapshot_generated_at"]
    merged["archived_at"] = min(previous["archived_at"], incoming["archived_at"])
    merged["content_digest"] = _event_content_digest(merged)
    validation_now = max(
        _metadata_time(previous["archived_at"], "archived_at"),
        _metadata_time(incoming["archived_at"], "archived_at"),
    )
    return _validate_archive_projection(merged, validation_now=validation_now)


def _apply_bucket_mutations(
    bucket_rows: dict[str, list[dict[str, Any]]],
    removals: dict[str, set[str]],
    additions: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    planned: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(set(bucket_rows) | set(removals) | set(additions)):
        removed = removals.get(name, set())
        retained: dict[str, dict[str, Any]] = {}
        for row in bucket_rows.get(name, []):
            event_id = row["event_id"]
            if event_id in removed:
                continue
            previous = retained.get(event_id)
            retained[event_id] = row if previous is None else _merge_archive_rows(previous, row)
        for row in additions.get(name, []):
            retained[row["event_id"]] = row
        planned[name] = [retained[event_id] for event_id in sorted(retained)]
    return planned


class EvidenceArchive:
    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        lock_timeout: float = 10.0,
    ) -> None:
        if root is None:
            configured = os.environ.get("VR_DATA_DIR")
            if configured:
                root = Path(configured) / "evidence-verification" / "v1"
            else:
                profile = Path(os.environ.get("USERPROFILE") or Path.home())
                root = profile / ".vibe-research" / "evidence-verification" / "v1"
        self.root = Path(os.path.abspath(root))
        self.archive_root = self.root / "archive"
        self.index_path = self.archive_root / "index.json"
        self.journal_path = self.archive_root / "transaction.json"
        self.state_path = self.archive_root / "state.json"
        self.lock_path = self.archive_root / ".archive.lock"
        self._now = now or (lambda: datetime.now(timezone.utc))
        if type(lock_timeout) not in {int, float} or isinstance(lock_timeout, bool) or lock_timeout <= 0 or lock_timeout > 30:
            raise ValueError("invalid archive lock timeout")
        self._lock_timeout = float(lock_timeout)
        self._last_diagnostics = self._diagnostics()

    @staticmethod
    def _diagnostics() -> dict[str, int]:
        return {
            "scanned_files": 0,
            "skipped_files": 0,
            "scanned_rows": 0,
            "skipped_corrupt_rows": 0,
            "duplicate_rows": 0,
        }

    @staticmethod
    def _increment(diagnostics: dict[str, int], key: str, amount: int = 1) -> None:
        diagnostics[key] = min(_MAX_DIAGNOSTIC_COUNT, diagnostics[key] + amount)

    @property
    def last_diagnostics(self) -> dict[str, int]:
        return dict(self._last_diagnostics)

    def _inside_root(self, path: Path) -> bool:
        try:
            return os.path.commonpath((str(self.root), os.path.abspath(path))) == str(self.root)
        except (OSError, ValueError):
            return False

    def _verify_directory_chain(self, directory: Path) -> None:
        if not self._inside_root(directory):
            raise OSError("storage_error")
        relative = directory.relative_to(self.root)
        cursor = self.root
        if not _safe_directory(cursor):
            raise OSError("storage_error")
        for component in relative.parts:
            cursor = cursor / component
            if not _safe_directory(cursor):
                raise OSError("storage_error")

    def _verify_parent(self, path: Path) -> None:
        if not self._inside_root(path):
            raise OSError("storage_error")
        self._verify_directory_chain(path.parent)

    @staticmethod
    def _read_directory_present(path: Path) -> bool:
        try:
            metadata = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            raise OSError("storage_error") from None
        if not stat.S_ISDIR(metadata.st_mode) or getattr(metadata, "st_reparse_tag", 0):
            raise OSError("storage_corrupt")
        return True

    def _prepare(self) -> None:
        for directory in (self.root, self.archive_root):
            try:
                _ensure_directory(directory)
            except FileExistsError:
                if not _safe_directory(directory):
                    raise OSError("storage_error") from None
        self._verify_directory_chain(self.archive_root)

    def _open_lock(self) -> io.FileIO:
        self._prepare()
        self._verify_parent(self.lock_path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            try:
                before = self.lock_path.stat(follow_symlinks=False)
            except FileNotFoundError:
                before = None
            if before is not None and (
                not stat.S_ISREG(before.st_mode)
                or getattr(before, "st_reparse_tag", 0)
                or before.st_nlink != 1
            ):
                raise OSError("storage_error")
            try:
                descriptor = os.open(
                    self.lock_path,
                    flags | (os.O_EXCL if before is None else 0),
                    0o600,
                )
            except FileExistsError:
                before = self.lock_path.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or getattr(before, "st_reparse_tag", 0)
                    or before.st_nlink != 1
                ):
                    raise OSError("storage_error")
                descriptor = os.open(self.lock_path, flags, 0o600)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or before is not None
                and identity != (before.st_dev, before.st_ino)
            ):
                raise OSError("storage_error")
            handle = io.FileIO(descriptor, mode="r+", closefd=True)
            descriptor = None
            return handle
        except OSError:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)
            raise OSError("storage_error") from None

    @staticmethod
    def _try_lock(handle: io.FileIO) -> bool:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError):
            return False

    @staticmethod
    def _unlock(handle: io.FileIO) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        handle = self._open_lock()
        acquired = False
        try:
            deadline = time.monotonic() + self._lock_timeout
            while not (acquired := self._try_lock(handle)):
                if time.monotonic() >= deadline:
                    raise OSError("storage_lock_unavailable")
                time.sleep(0.01)
            yield
        finally:
            if acquired:
                self._unlock(handle)
            try:
                handle.close()
            except OSError:
                pass

    def _open_existing_lock(self) -> io.FileIO | None:
        before = self._safe_file(self.lock_path)
        if before is None:
            if self._entry_present(self.lock_path):
                raise OSError("storage_error")
            return None
        self._verify_parent(self.lock_path)
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.lock_path, flags)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            if (
                identity != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
            ):
                raise OSError("storage_error")
            handle = io.FileIO(descriptor, mode="r+", closefd=True)
            descriptor = None
            return handle
        except OSError:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)
            raise OSError("storage_error") from None

    def _authority_present_without_lock(self) -> bool:
        for path in (self.state_path, self.index_path):
            if self._entry_present(path):
                return True
        entry_count = 0
        try:
            with os.scandir(self.archive_root) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > _MAX_ARCHIVE_DIRECTORY_ENTRIES:
                        raise OSError("storage_corrupt")
                    if (
                        _valid_bucket_name(entry.name)
                        and self._safe_file(self.archive_root / entry.name) is not None
                    ):
                        return True
        except OSError:
            raise OSError("storage_corrupt") from None
        return False

    @contextmanager
    def _reader_process_lock(self) -> Iterator[bool]:
        if not self._read_directory_present(self.root):
            yield False
            return
        if not self._read_directory_present(self.archive_root):
            yield False
            return
        handle: io.FileIO | None = None
        for attempt in range(2):
            handle = self._open_existing_lock()
            if handle is not None:
                break
            if not self._authority_present_without_lock():
                yield False
                return
            if attempt == 1:
                raise OSError("storage_corrupt")
        if handle is None:
            raise OSError("storage_corrupt")
        acquired = False
        try:
            deadline = time.monotonic() + self._lock_timeout
            while not (acquired := self._try_lock(handle)):
                if time.monotonic() >= deadline:
                    raise OSError("storage_lock_unavailable")
                time.sleep(0.01)
            yield True
        finally:
            if acquired:
                self._unlock(handle)
            try:
                handle.close()
            except OSError:
                pass

    def _safe_file(self, path: Path) -> os.stat_result | None:
        try:
            metadata = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError:
            raise OSError("storage_error") from None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or getattr(metadata, "st_reparse_tag", 0)
            or metadata.st_nlink != 1
        ):
            return None
        return metadata

    @staticmethod
    def _entry_present(path: Path) -> bool:
        try:
            path.stat(follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return True

    def _read_bytes(
        self,
        path: Path,
        maximum: int,
        *,
        observed_identity: list[tuple[int, int]] | None = None,
        observed_metadata: list[tuple[int, int, int, int, int, int, int, int]] | None = None,
        observed_descriptor_metadata: list[tuple[int, int, int, int, int, int, int, int]] | None = None,
    ) -> bytes | None:
        before = self._safe_file(path)
        if before is None:
            return None
        before_signature = _file_signature(before)
        self._verify_parent(path)
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            opened_signature = _file_signature(opened)
            if (
                identity != (before.st_dev, before.st_ino)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size > maximum
            ):
                return None
            handle = os.fdopen(descriptor, "rb")
            descriptor = None
            with handle:
                payload = handle.read(maximum + 1)
                after = os.fstat(handle.fileno())
                final_path = path.stat(follow_symlinks=False)
                final_descriptor = os.fstat(handle.fileno())
            after_signature = _file_signature(after)
            final_path_signature = _file_signature(final_path)
            final_descriptor_signature = _file_signature(final_descriptor)
            if (
                len(payload) > maximum
                or after_signature != opened_signature
                or final_descriptor_signature != opened_signature
                or final_path_signature != before_signature
                or final_path_signature[:2] != identity
                or final_path.st_size != len(payload)
                or final_path.st_nlink != 1
                or getattr(final_path, "st_reparse_tag", 0)
                or not stat.S_ISREG(final_path.st_mode)
                or final_descriptor.st_size != len(payload)
                or final_descriptor.st_nlink != 1
                or getattr(final_descriptor, "st_reparse_tag", 0)
                or not stat.S_ISREG(final_descriptor.st_mode)
                or not stat.S_ISREG(after.st_mode)
            ):
                return None
            if observed_identity is not None:
                observed_identity.append((after.st_dev, after.st_ino))
            if observed_metadata is not None:
                observed_metadata.append(final_path_signature)
            if observed_descriptor_metadata is not None:
                observed_descriptor_metadata.append(final_descriptor_signature)
            return payload
        except OSError:
            return None
        finally:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)

    def _read_budgeted_bytes(
        self,
        path: Path,
        maximum: int,
        *,
        budget: dict[str, int],
        observed_metadata: list[tuple[int, int, int, int, int, int, int, int]] | None = None,
        observed_descriptor_metadata: list[tuple[int, int, int, int, int, int, int, int]] | None = None,
    ) -> bytes | None:
        metadata = self._safe_file(path)
        if metadata is None:
            if self._entry_present(path):
                raise OSError("storage_corrupt")
            return None
        size = metadata.st_size
        if (
            budget["files"] <= 0
            or type(size) is not int
            or size < 0
            or size > maximum
            or size > budget["bytes"]
        ):
            raise OSError("storage_corrupt")
        budget["files"] -= 1
        budget["bytes"] -= size
        payload = self._read_bytes(
            path,
            size,
            observed_metadata=observed_metadata,
            observed_descriptor_metadata=observed_descriptor_metadata,
        )
        if payload is None or len(payload) != size:
            raise OSError("storage_corrupt")
        return payload

    def _atomic_write(self, path: Path, payload: bytes, maximum: int) -> tuple[int, int]:
        if len(payload) > maximum:
            raise ValueError("archive document is too large")
        self._prepare()
        self._verify_parent(path)
        present = self._entry_present(path)
        before = self._safe_file(path)
        if present and before is None:
            raise OSError("storage_error")
        descriptor: int | None = None
        temp_path: Path | None = None
        identity: tuple[int, int] | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            temp_path = Path(raw_path)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            try:
                handle = os.fdopen(descriptor, "wb")
            except BaseException:
                _close_owned_descriptor(descriptor, identity)
                descriptor = None
                raise
            descriptor = None
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_durable(temp_path, path)
            return identity
        except OSError:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)
            if temp_path is not None and identity is not None:
                _cleanup_owned_temp(temp_path, identity)
            raise OSError("storage_error") from None
        except BaseException:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)
            if temp_path is not None and identity is not None:
                _cleanup_owned_temp(temp_path, identity)
            raise

    def _bucket_payload(self, name: str, rows: list[dict[str, Any]]) -> bytes:
        if not _valid_bucket_name(name) or len(rows) > _MAX_BUCKET_ROWS:
            raise ValueError("invalid archive bucket")
        canonical = sorted(rows, key=lambda row: row["event_id"])
        event_ids = [row["event_id"] for row in canonical]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("archive bucket contains duplicate event identities")
        payload_parts: list[bytes] = []
        validation_now = _utc(self._now(), "archive clock")
        for row in canonical:
            validated = _validate_archive_projection(
                row,
                validation_now=validation_now,
                bucket_name=name,
            )
            encoded = json.dumps(validated, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(encoded) > _MAX_ROW_BYTES:
                raise ValueError("archive row is too large")
            payload_parts.append(encoded + b"\n")
        payload = b"".join(payload_parts)
        if len(payload) > _MAX_BUCKET_BYTES:
            raise ValueError("archive document is too large")
        return payload

    def _parse_json(self, raw: bytes) -> object:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_int=_strict_json_int,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )

    def _read_bucket(
        self,
        name: str,
        diagnostics: dict[str, int],
        *,
        budget: dict[str, int] | None = None,
        fail_on_budget: bool = False,
        strict_rows: bool = True,
        validation_now: datetime | None = None,
        observed_digests: dict[str, str] | None = None,
        expected_digests: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not _valid_bucket_name(name):
            raise ValueError("invalid archive bucket name")
        path = self.archive_root / name
        maximum = _MAX_BUCKET_BYTES
        if budget is not None:
            if budget["files"] <= 0:
                raise OSError("storage_corrupt")
            maximum = min(maximum, max(0, budget["bytes"]))
        payload = self._read_bytes(path, maximum)
        if payload is None:
            if self._entry_present(path):
                if fail_on_budget:
                    raise OSError("storage_corrupt")
                self._increment(diagnostics, "skipped_files")
            elif observed_digests is not None:
                observed_digests[name] = _MISSING_DIGEST
            return []
        raw_digest = self._payload_digest(payload)
        tolerant = not strict_rows or (
            expected_digests is not None and raw_digest not in expected_digests
        )
        if budget is not None:
            budget["files"] -= 1
            budget["bytes"] -= len(payload)
        self._increment(diagnostics, "scanned_files")
        row_limit = _MAX_BUCKET_ROWS
        if budget is not None:
            row_limit = min(row_limit, budget["rows"])
        try:
            framed_line_count = _count_bucket_lines(payload, maximum_rows=row_limit)
        except ValueError:
            self._increment(diagnostics, "skipped_files")
            self._last_diagnostics = diagnostics
            raise OSError("storage_corrupt")
        if budget is not None:
            budget["rows"] -= framed_line_count
        rows: list[dict[str, Any]] = []
        source_payload_parts: list[bytes] = []
        corrupt_rows = 0

        def reject_corrupt_row() -> None:
            nonlocal corrupt_rows
            corrupt_rows += 1
            self._increment(diagnostics, "skipped_corrupt_rows")
            if not tolerant or corrupt_rows > _MAX_TOLERATED_CORRUPT_ROWS:
                self._last_diagnostics = diagnostics
                raise OSError("storage_corrupt")

        for framed_line in _iter_bucket_lines(payload):
            if framed_line.endswith(b"\r\n"):
                line = framed_line[:-2]
            elif framed_line.endswith((b"\n", b"\r")):
                line = framed_line[:-1]
            else:
                line = framed_line
            self._increment(diagnostics, "scanned_rows")
            if budget is not None:
                if budget["nodes"] <= 0:
                    self._last_diagnostics = diagnostics
                    raise OSError("storage_corrupt")
                # Every physical row consumes at least its root node even when the
                # JSON or schema is corrupt.  A corrupt row can never be a free
                # shared-budget continuation.
                budget["nodes"] -= 1
            if len(line) > _MAX_ROW_BYTES:
                self._increment(diagnostics, "skipped_corrupt_rows")
                self._last_diagnostics = diagnostics
                raise OSError("storage_corrupt")
            if not line:
                reject_corrupt_row()
                continue
            try:
                if budget is not None:
                    _preflight_json_payload(
                        line + b"\n",
                        maximum_depth=16,
                        maximum_tokens=min(
                            _MAX_SCAN_NODES,
                            budget["nodes"] * 4 + 16,
                        ),
                    )
                document = self._parse_json(line)
                document_nodes = 1
                if budget is not None:
                    try:
                        _document_bytes, document_nodes = _json_metrics(
                            document,
                            maximum_bytes=_MAX_ROW_BYTES,
                            maximum_nodes=budget["nodes"] + 1,
                        )
                    except ValueError as error:
                        if "budget" not in str(error):
                            raise
                        self._last_diagnostics = diagnostics
                        raise OSError("storage_corrupt") from None
                    additional_nodes = document_nodes - 1
                    if additional_nodes > budget["nodes"]:
                        self._last_diagnostics = diagnostics
                        raise OSError("storage_corrupt")
                    budget["nodes"] -= additional_nodes
                parsed = _archive_from_document(document)
            except _ArchiveFutureSchemaError:
                reject_corrupt_row()
                continue
            except OSError:
                self._last_diagnostics = diagnostics
                raise
            except ValueError as error:
                if "budget" in str(error):
                    self._last_diagnostics = diagnostics
                    raise OSError("storage_corrupt") from None
                reject_corrupt_row()
                continue
            except (KeyError, TypeError, UnicodeDecodeError, RecursionError, json.JSONDecodeError):
                reject_corrupt_row()
                continue
            try:
                _validate_temporal_row(parsed, validation_now or _utc(self._now(), "archive clock"))
                if _bucket_name(parsed) != name:
                    raise ValueError("archive row is in the wrong bucket")
                if budget is not None:
                    _row_bytes, canonical_nodes = _json_metrics(
                        parsed,
                        maximum_bytes=_MAX_ROW_BYTES,
                        maximum_nodes=budget["nodes"] + document_nodes,
                    )
                    additional_nodes = max(0, canonical_nodes - document_nodes)
                    if additional_nodes > budget["nodes"]:
                        raise OSError("storage_corrupt")
                    budget["nodes"] -= additional_nodes
            except OSError:
                self._last_diagnostics = diagnostics
                raise
            except (KeyError, TypeError, ValueError, RecursionError):
                # A parsed native row with an impossible time or physical bucket
                # is an authority violation, not a skippable corrupt fragment.
                self._last_diagnostics = diagnostics
                raise OSError("storage_corrupt") from None
            rows.append(parsed)
            source_payload_parts.append(framed_line)
        event_ids = [row["event_id"] for row in rows]
        if event_ids != sorted(event_ids) or len(event_ids) != len(set(event_ids)):
            self._increment(diagnostics, "skipped_files")
            self._last_diagnostics = diagnostics
            raise OSError("storage_corrupt")
        resolved_digest = raw_digest
        if expected_digests is not None and raw_digest not in expected_digests:
            canonical_digests = {
                self._payload_digest(self._bucket_payload(name, rows)),
                self._payload_digest(b"".join(source_payload_parts)),
            }
            matched_digests = canonical_digests & expected_digests
            if not matched_digests:
                self._last_diagnostics = diagnostics
                raise OSError("storage_corrupt")
            resolved_digest = sorted(matched_digests)[0]
        if observed_digests is not None:
            observed_digests[name] = resolved_digest
        return rows

    def _bucket_names(
        self,
        *,
        diagnostics: dict[str, int] | None = None,
        prepare: bool = True,
    ) -> list[str]:
        if prepare:
            self._prepare()
        names: list[str] = []
        entry_count = 0
        try:
            with os.scandir(self.archive_root) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > _MAX_ARCHIVE_DIRECTORY_ENTRIES:
                        raise OSError("storage_corrupt")
                    if _valid_bucket_name(entry.name):
                        path = self.archive_root / entry.name
                        if self._safe_file(path) is None:
                            if diagnostics is not None and self._entry_present(path):
                                self._increment(diagnostics, "skipped_files")
                            continue
                        names.append(entry.name)
                        if len(names) > _MAX_ARCHIVE_FILES:
                            raise OSError("storage_corrupt")
        except OSError:
            raise OSError("storage_corrupt") from None
        return sorted(names)

    def _preflight_bucket_mutation_paths(
        self,
        names: Iterator[str] | list[str] | set[str] | tuple[str, ...],
    ) -> dict[str, tuple[int, int, int, int, int, int, int, int] | None]:
        records: dict[str, tuple[int, int, int, int, int, int, int, int] | None] = {}
        for name in sorted(self._bounded_bucket_candidates(names)):
            path = self.archive_root / name
            self._verify_parent(path)
            try:
                metadata = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                records[name] = None
                continue
            except OSError:
                raise OSError("storage_error") from None
            if (
                not stat.S_ISREG(metadata.st_mode)
                or getattr(metadata, "st_reparse_tag", 0)
                or metadata.st_nlink != 1
            ):
                raise OSError("storage_error")
            records[name] = _file_signature(metadata)
        return records

    def _bucket_mutation_records_are_current(
        self,
        records: dict[str, tuple[int, int, int, int, int, int, int, int] | None],
    ) -> bool:
        try:
            return self._preflight_bucket_mutation_paths(records) == records
        except OSError:
            return False

    @staticmethod
    def _payload_digest(payload: bytes | None) -> str:
        return _MISSING_DIGEST if payload is None else hashlib.sha256(payload).hexdigest()

    def _index_digest(self) -> str:
        record = self._read_index_record(budget=_new_scan_budget())
        return _MISSING_DIGEST if record is None else record["digest"]

    def _index_record_is_current(self, record: dict[str, Any] | None) -> bool:
        if record is None:
            return not self._entry_present(self.index_path)
        expected = record.get("metadata")
        expected_descriptor = record.get("descriptor_metadata")
        if (
            type(expected) is not tuple
            or len(expected) != 8
            or any(type(value) is not int for value in expected)
            or type(expected_descriptor) is not tuple
            or len(expected_descriptor) != 8
            or any(type(value) is not int for value in expected_descriptor)
            or expected_descriptor[:2] != expected[:2]
        ):
            return False
        before = self._safe_file(self.index_path)
        if before is None:
            return False
        if _file_signature(before) != expected:
            return False
        self._verify_parent(self.index_path)
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.index_path, flags)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            opened_signature = _file_signature(opened)
            final_path = self.index_path.stat(follow_symlinks=False)
            final_path_signature = _file_signature(final_path)
            final_descriptor = os.fstat(descriptor)
            final_descriptor_signature = _file_signature(final_descriptor)
            return (
                (opened.st_dev, opened.st_ino) == expected[:2]
                and opened_signature == expected_descriptor
                and final_path_signature == expected
                and final_descriptor_signature == expected_descriptor
                and stat.S_ISREG(opened.st_mode)
                and opened.st_nlink == 1
                and not getattr(opened, "st_reparse_tag", 0)
                and stat.S_ISREG(final_path.st_mode)
                and final_path.st_nlink == 1
                and not getattr(final_path, "st_reparse_tag", 0)
                and stat.S_ISREG(final_descriptor.st_mode)
                and final_descriptor.st_nlink == 1
                and not getattr(final_descriptor, "st_reparse_tag", 0)
            )
        except OSError:
            return False
        finally:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)

    def _parse_authority_state(
        self,
        document: object,
        *,
        now: datetime,
    ) -> dict[str, Any]:
        if type(document) is not dict:
            raise OSError("storage_corrupt")
        schema_version = document.get("schema_version")
        if type(schema_version) is not int:
            raise OSError("storage_corrupt")
        if schema_version in _LEGACY_STATE_SCHEMA_VERSIONS:
            if set(document) != _LEGACY_STATE_KEYS:
                raise OSError("storage_corrupt")
            return {"legacy": True, "document": dict(document)}
        if schema_version != _STATE_SCHEMA_VERSION:
            raise OSError("storage_corrupt")
        phase = document.get("phase")
        expected_keys = _PREPARED_STATE_KEYS if phase == "prepared" else _FINALIZED_STATE_KEYS
        if phase not in {"prepared", "finalized"} or set(document) != expected_keys:
            raise OSError("storage_corrupt")
        try:
            generation = document["generation"]
            transaction_id = document["transaction_id"]
            cutoff = _metadata_time(document["cutoff"], "archive state cutoff")
            upper = _shift_days(cutoff, 90)
            target_index_digest = document["target_index_digest"]
            target_bucket_digests = self._bucket_digest_map(document["target_bucket_digests"])
            target_index = self._index_events(document["target_index"])
            if (
                type(generation) is not int
                or generation < 1
                or type(transaction_id) is not str
                or _CONTENT_DIGEST.fullmatch(transaction_id) is None
                or type(target_index_digest) is not str
                or _CONTENT_DIGEST.fullmatch(target_index_digest) is None
                or upper > _shift_datetime(now, _MAX_CLOCK_SKEW)
                or self._payload_digest(self._index_payload(target_index)) != target_index_digest
            ):
                raise ValueError("invalid archive authority state")
            result = dict(document)
            result.update({
                "legacy": False,
                "cutoff": cutoff,
                "target_bucket_digests": target_bucket_digests,
                "target_index": target_index,
            })
            if phase == "finalized":
                indexed_buckets = set(target_index.values())
                manifest_upper = _shift_datetime(upper, _MAX_CLOCK_SKEW)
                empty_digest = self._payload_digest(b"")
                if (
                    not indexed_buckets <= set(target_bucket_digests)
                    or any(
                        not _bucket_name_in_window(name, cutoff, manifest_upper)
                        or digest in {_MISSING_DIGEST, empty_digest}
                        for name, digest in target_bucket_digests.items()
                    )
                ):
                    raise ValueError("invalid archive finalized manifest")
                return result
            base_generation = document["base_generation"]
            target_generation = document["target_generation"]
            base_index_digest = document["base_index_digest"]
            base_bucket_digests = self._bucket_digest_map(document["base_bucket_digests"])
            raw_rows = document["rows"]
            if (
                type(base_generation) is not int
                or base_generation < 0
                or type(target_generation) is not int
                or target_generation != generation
                or target_generation != base_generation + 1
                or type(base_index_digest) is not str
                or _CONTENT_DIGEST.fullmatch(base_index_digest) is None
                or set(base_bucket_digests) != set(target_bucket_digests)
                or type(raw_rows) is not list
                or len(raw_rows) > _MAX_INDEX_EVENTS
            ):
                raise ValueError("invalid archive prepared state")
            rows = [_archive_from_document(row) for row in raw_rows]
            event_ids = [row["event_id"] for row in rows]
            if event_ids != sorted(event_ids) or len(event_ids) != len(set(event_ids)):
                raise ValueError("invalid archive prepared state")
            for row in rows:
                _validate_temporal_row(row, now)
                expected = _bucket_name(row) if cutoff <= _event_time(row) <= upper else None
                if target_index.get(row["event_id"]) != expected:
                    raise ValueError("invalid archive prepared target index")
            if transaction_id != self._journal_transaction_id(
                base_generation=base_generation,
                target_generation=target_generation,
                base_index_digest=base_index_digest,
                target_index_digest=target_index_digest,
                base_bucket_digests=base_bucket_digests,
                target_bucket_digests=target_bucket_digests,
                rows=rows,
                cutoff=cutoff.isoformat(),
                target_index=target_index,
            ):
                raise ValueError("invalid archive prepared transaction")
            result.update({
                "base_bucket_digests": base_bucket_digests,
                "rows": rows,
            })
            return result
        except OSError:
            raise
        except (KeyError, TypeError, ValueError, RecursionError):
            raise OSError("storage_corrupt") from None

    def _read_authority_state(
        self,
        now: datetime,
        *,
        diagnostics: dict[str, int] | None = None,
        budget: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        raw = (
            self._read_bytes(self.state_path, _MAX_STATE_BYTES)
            if budget is None
            else self._read_budgeted_bytes(
                self.state_path,
                _MAX_STATE_BYTES,
                budget=budget,
            )
        )
        if raw is None:
            if self._entry_present(self.state_path):
                raise OSError("storage_corrupt")
            return None
        try:
            _preflight_json_payload(
                raw,
                maximum_depth=16,
                maximum_tokens=_MAX_SCAN_NODES if budget is None else budget["nodes"],
            )
            _preflight_top_level_array_rows(
                raw,
                key="rows",
                maximum_rows=_MAX_INDEX_EVENTS if budget is None else budget["rows"],
            )
            document = self._parse_json(raw)
            _document_bytes, nodes = _json_metrics(
                document,
                maximum_bytes=_MAX_STATE_BYTES,
                maximum_nodes=_MAX_SCAN_NODES if budget is None else budget["nodes"],
            )
            rows = len(document.get("rows", ())) if type(document) is dict and type(document.get("rows")) is list else 0
            if budget is not None:
                if (
                    budget["nodes"] < nodes
                    or budget["rows"] < rows
                ):
                    raise ValueError("archive state exceeds shared scan budget")
                budget["nodes"] -= nodes
                budget["rows"] -= rows
            if diagnostics is not None:
                self._increment(diagnostics, "scanned_files")
                self._increment(diagnostics, "scanned_rows", rows)
            return self._parse_authority_state(document, now=now)
        except OSError:
            raise
        except (TypeError, ValueError, UnicodeDecodeError, RecursionError, json.JSONDecodeError):
            if diagnostics is not None:
                self._increment(diagnostics, "skipped_files")
                self._last_diagnostics = diagnostics
            raise OSError("storage_corrupt") from None

    def _prepared_authority_payload(
        self,
        *,
        base_generation: int,
        target_generation: int,
        cutoff: datetime,
        base_index_digest: str,
        target_index_digest: str,
        base_bucket_digests: dict[str, str],
        target_bucket_digests: dict[str, str],
        target_index: dict[str, str],
        rows: list[dict[str, Any]],
    ) -> bytes:
        cutoff_time = _utc(cutoff, "archive transaction cutoff")
        canonical_rows = [
            _validate_archive_projection(row, validation_now=_utc(self._now(), "archive clock"))
            for row in sorted(rows, key=lambda row: row["event_id"])
        ]
        canonical_base = self._bucket_digest_map(base_bucket_digests)
        canonical_target = self._bucket_digest_map(target_bucket_digests)
        canonical_index = self._index_events(target_index)
        transaction_id = self._journal_transaction_id(
            base_generation=base_generation,
            target_generation=target_generation,
            base_index_digest=base_index_digest,
            target_index_digest=target_index_digest,
            base_bucket_digests=canonical_base,
            target_bucket_digests=canonical_target,
            rows=canonical_rows,
            cutoff=cutoff_time.isoformat(),
            target_index=canonical_index,
        )
        document = {
            "schema_version": _STATE_SCHEMA_VERSION,
            "generation": target_generation,
            "phase": "prepared",
            "transaction_id": transaction_id,
            "base_generation": base_generation,
            "target_generation": target_generation,
            "cutoff": cutoff_time.isoformat(),
            "base_index_digest": base_index_digest,
            "target_index_digest": target_index_digest,
            "base_bucket_digests": canonical_base,
            "target_bucket_digests": canonical_target,
            "target_index": canonical_index,
            "rows": canonical_rows,
        }
        payload = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > _MAX_STATE_BYTES:
            raise ValueError("archive state is too large")
        self._parse_authority_state(document, now=_utc(self._now(), "archive clock"))
        return payload

    def _finalized_authority_payload(self, prepared: dict[str, Any]) -> bytes:
        cutoff = prepared["cutoff"]
        if type(cutoff) is not datetime:
            cutoff = _metadata_time(cutoff, "archive state cutoff")
        manifest_upper = _shift_datetime(_shift_days(cutoff, 90), _MAX_CLOCK_SKEW)
        empty_digest = self._payload_digest(b"")
        document = {
            "schema_version": _STATE_SCHEMA_VERSION,
            "generation": prepared["target_generation"],
            "phase": "finalized",
            "transaction_id": prepared["transaction_id"],
            "cutoff": prepared["cutoff"].isoformat() if type(prepared["cutoff"]) is datetime else prepared["cutoff"],
            "target_index_digest": prepared["target_index_digest"],
            "target_bucket_digests": {
                name: digest
                for name, digest in prepared["target_bucket_digests"].items()
                if _bucket_name_in_window(name, cutoff, manifest_upper)
                and digest not in {_MISSING_DIGEST, empty_digest}
            },
            "target_index": prepared["target_index"],
        }
        payload = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > _MAX_STATE_BYTES:
            raise ValueError("archive state is too large")
        self._parse_authority_state(document, now=_utc(self._now(), "archive clock"))
        return payload

    @staticmethod
    def _bucket_digest_map(value: object) -> dict[str, str]:
        if type(value) is not dict or len(value) > _MAX_ARCHIVE_FILES:
            raise ValueError("invalid archive transaction buckets")
        result: dict[str, str] = {}
        for name, digest in value.items():
            if (
                not _valid_bucket_name(name)
                or type(digest) is not str
                or _CONTENT_DIGEST.fullmatch(digest) is None
            ):
                raise ValueError("invalid archive transaction buckets")
            result[name] = digest
        return dict(sorted(result.items()))

    @staticmethod
    def _journal_transaction_id(
        *,
        base_generation: int,
        target_generation: int,
        base_index_digest: str,
        target_index_digest: str,
        base_bucket_digests: dict[str, str],
        target_bucket_digests: dict[str, str],
        rows: list[dict[str, Any]],
        cutoff: str | None = None,
        target_index: dict[str, str] | None = None,
    ) -> str:
        document: dict[str, object] = {
            "base_generation": base_generation,
            "target_generation": target_generation,
            "base_index_digest": base_index_digest,
            "target_index_digest": target_index_digest,
            "base_bucket_digests": base_bucket_digests,
            "target_bucket_digests": target_bucket_digests,
            "rows": rows,
        }
        if cutoff is not None or target_index is not None:
            if cutoff is None or target_index is None:
                raise ValueError("invalid archive transaction")
            document["cutoff"] = cutoff
            document["target_index"] = target_index
        material = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _read_index_record(
        self,
        *,
        budget: dict[str, int],
        diagnostics: dict[str, int] | None = None,
    ) -> dict[str, Any] | None:
        metadata = self._safe_file(self.index_path)
        if metadata is None:
            if self._entry_present(self.index_path):
                raise OSError("storage_corrupt")
            return None
        if any(budget[name] <= 0 for name in ("files", "bytes", "nodes", "rows")):
            raise OSError("storage_corrupt")
        observed_metadata: list[tuple[int, int, int, int, int, int, int, int]] = []
        observed_descriptor_metadata: list[tuple[int, int, int, int, int, int, int, int]] = []
        raw = self._read_budgeted_bytes(
            self.index_path,
            _MAX_INDEX_BYTES,
            budget=budget,
            observed_metadata=observed_metadata,
            observed_descriptor_metadata=observed_descriptor_metadata,
        )
        if (
            raw is None
            or len(observed_metadata) != 1
            or len(observed_descriptor_metadata) != 1
        ):
            raise OSError("storage_corrupt")
        try:
            entry_count = _preparse_index_payload(raw, budget=budget)
            document = self._parse_json(raw)
            if (
                type(document) is not dict
                or set(document) != _INDEX_KEYS
                or type(document["schema_version"]) is not int
                or document["schema_version"] != _INDEX_SCHEMA_VERSION
            ):
                raise ValueError("invalid archive index")
            events = self._index_events(document["events"])
            if len(events) != entry_count:
                raise ValueError("invalid archive index events")
            if diagnostics is not None:
                self._increment(diagnostics, "scanned_files")
                self._increment(diagnostics, "scanned_rows", entry_count)
            return {
                "events": events,
                "payload": raw,
                "digest": self._payload_digest(raw),
                "metadata": observed_metadata[0],
                "descriptor_metadata": observed_descriptor_metadata[0],
            }
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, RecursionError, json.JSONDecodeError):
            if diagnostics is not None:
                self._increment(diagnostics, "skipped_files")
                self._last_diagnostics = diagnostics
            raise OSError("storage_corrupt") from None

    def _read_index(
        self,
        *,
        budget: dict[str, int] | None = None,
        diagnostics: dict[str, int] | None = None,
    ) -> dict[str, str] | None:
        record = self._read_index_record(
            budget=_new_scan_budget() if budget is None else budget,
            diagnostics=diagnostics,
        )
        return None if record is None else record["events"]

    @staticmethod
    def _index_events(value: object) -> dict[str, str]:
        if type(value) is not dict or len(value) > _MAX_INDEX_EVENTS:
            raise ValueError("invalid archive index events")
        result: dict[str, str] = {}
        for event_id, bucket in value.items():
            canonical_id = _bounded_text(event_id, "index event_id")
            canonical_bucket = _bounded_text(bucket, "index bucket")
            if not _valid_bucket_name(canonical_bucket):
                raise ValueError("invalid archive index bucket")
            result[canonical_id] = canonical_bucket
        return dict(sorted(result.items()))

    @staticmethod
    def _bounded_bucket_candidates(*collections: Iterator[str] | list[str] | set[str] | tuple[str, ...]) -> set[str]:
        names: set[str] = set()
        for collection in collections:
            for name in collection:
                if not _valid_bucket_name(name):
                    raise OSError("storage_corrupt")
                names.add(name)
                if len(names) > _MAX_ARCHIVE_FILES:
                    raise OSError("storage_corrupt")
        return names

    @staticmethod
    def _validate_index_projection(
        index: dict[str, str],
        bucket_rows: dict[str, list[dict[str, Any]]],
        *,
        cutoff: datetime,
        upper: datetime,
        authoritative_rows: list[dict[str, Any]] | None = None,
        allow_stale_index_entries: bool = False,
    ) -> None:
        all_projected: dict[str, dict[str, Any]] = {}
        window_projected: dict[str, dict[str, Any]] = {}
        try:
            for rows in bucket_rows.values():
                for row in rows:
                    event_id = row["event_id"]
                    previous = all_projected.get(event_id)
                    all_projected[event_id] = row if previous is None else _merge_archive_rows(previous, row)
                    effective = _event_time(row)
                    if effective < cutoff or effective > upper:
                        continue
                    previous = window_projected.get(event_id)
                    window_projected[event_id] = row if previous is None else _merge_archive_rows(previous, row)
            for row in authoritative_rows or []:
                event_id = row["event_id"]
                all_projected[event_id] = row
                effective = _event_time(row)
                if effective < cutoff or effective > upper:
                    window_projected.pop(event_id, None)
                else:
                    window_projected[event_id] = row
            for event_id, name in index.items():
                row = all_projected.get(event_id)
                if row is None or name != _bucket_name(row):
                    raise OSError("storage_corrupt")
            if not set(window_projected) <= set(index):
                raise OSError("storage_corrupt")
            if any(index[event_id] != _bucket_name(row) for event_id, row in window_projected.items()):
                raise OSError("storage_corrupt")
            if not allow_stale_index_entries and set(index) != set(window_projected):
                raise OSError("storage_corrupt")
        except OSError:
            raise
        except (KeyError, TypeError, ValueError):
            raise OSError("storage_corrupt") from None

    def _v2_projection_at_upper(
        self,
        journal: dict[str, Any],
        physical_index: dict[str, str],
        *,
        upper: datetime,
        physical_rows: dict[str, dict[str, Any]],
        locations: dict[str, set[str]],
        physical_index_digest: str,
    ) -> dict[str, str]:
        if journal["legacy"] or journal["native_schema_version"] != _PREVIOUS_JOURNAL_SCHEMA_VERSION:
            raise OSError("storage_corrupt")
        if physical_index_digest not in {journal["base_index_digest"], journal["target_index_digest"]}:
            raise OSError("storage_corrupt")
        transaction_upper = _utc(upper, "archive transaction time")
        cutoff = _shift_days(transaction_upper, -90)
        journal_by_id = {row["event_id"]: row for row in journal["rows"]}
        projected_rows: dict[str, dict[str, Any]] = {}
        for event_id, row in physical_rows.items():
            bucket_name = _bucket_name(row)
            if locations.get(event_id) != {bucket_name}:
                raise OSError("storage_corrupt")
            if event_id not in journal_by_id:
                projected_rows[event_id] = row
        projected_rows.update(journal_by_id)
        for event_id, bucket_name in physical_index.items():
            row = projected_rows.get(event_id)
            if row is None or _bucket_name(row) != bucket_name:
                raise OSError("storage_corrupt")
        target: dict[str, str] = {}
        for event_id, row in projected_rows.items():
            if cutoff <= _event_time(row) <= upper:
                target[event_id] = _bucket_name(row)
        if self._payload_digest(self._index_payload(target)) != journal["target_index_digest"]:
            raise OSError("storage_corrupt")
        return target

    @staticmethod
    def _v2_causal_lower_bound(rows: Iterator[dict[str, Any]] | list[dict[str, Any]]) -> datetime | None:
        causal: list[datetime] = []
        for row in rows:
            for key in ("snapshot_generated_at", "archived_at", "last_updated_at"):
                causal.append(_metadata_time(row[key], key))
            causal.extend(_row_times(row))
            causal.extend(
                _metadata_time(lineage["generated_at"], "lineage generated_at")
                for lineage in row["snapshot_history"]
            )
        return max(causal) if causal else None

    @staticmethod
    def _v2_projection_rows(
        journal: dict[str, Any],
        physical_rows: dict[str, dict[str, Any]],
        locations: dict[str, set[str]],
    ) -> dict[str, dict[str, Any]]:
        journal_by_id = {row["event_id"]: row for row in journal["rows"]}
        rows: dict[str, dict[str, Any]] = {}
        for event_id, row in physical_rows.items():
            canonical_bucket = _bucket_name(row)
            if locations.get(event_id) != {canonical_bucket}:
                raise OSError("storage_corrupt")
            if event_id not in journal_by_id:
                rows[event_id] = row
        rows.update(journal_by_id)
        return rows

    @staticmethod
    def _v2_allowed_upper_intervals(
        target: dict[str, str],
        rows: dict[str, dict[str, Any]],
        *,
        lower: datetime,
        upper: datetime,
    ) -> list[tuple[datetime, datetime]]:
        if lower > upper or not set(target) <= set(rows):
            raise OSError("storage_corrupt")
        feasible_start = lower
        feasible_finish = upper
        for event_id, bucket_name in target.items():
            row = rows[event_id]
            if _bucket_name(row) != bucket_name:
                raise OSError("storage_corrupt")
            event_time = _event_time(row)
            included_upper = _shift_days(event_time, 90)
            feasible_start = max(feasible_start, event_time)
            feasible_finish = min(feasible_finish, included_upper)
            if feasible_start > feasible_finish:
                raise OSError("storage_corrupt")

        excluded: list[tuple[datetime, datetime]] = []
        for event_id, row in rows.items():
            if event_id in target:
                continue
            event_time = _event_time(row)
            excluded_start = max(feasible_start, event_time)
            excluded_finish = min(
                feasible_finish,
                _shift_days(event_time, 90),
            )
            if excluded_start <= excluded_finish:
                excluded.append((excluded_start, excluded_finish))
        excluded.sort()

        one_tick = timedelta(microseconds=1)
        merged: list[tuple[datetime, datetime]] = []
        for start, finish in excluded:
            if not merged:
                merged.append((start, finish))
                continue
            previous_start, previous_finish = merged[-1]
            if start <= _shift_datetime(previous_finish, one_tick):
                merged[-1] = (previous_start, max(previous_finish, finish))
            else:
                merged.append((start, finish))

        allowed: list[tuple[datetime, datetime]] = []
        cursor = feasible_start
        for start, finish in merged:
            if cursor < start:
                allowed.append((cursor, _shift_datetime(start, -one_tick)))
            if finish >= feasible_finish:
                cursor = _shift_datetime(feasible_finish, one_tick)
                break
            cursor = _shift_datetime(finish, one_tick)
        if cursor <= feasible_finish:
            allowed.append((cursor, feasible_finish))
        if not allowed:
            raise OSError("storage_corrupt")
        return allowed

    def _resolve_v2_transaction_projection(
        self,
        journal: dict[str, Any],
        physical_index: dict[str, str],
        *,
        physical_rows: dict[str, dict[str, Any]],
        locations: dict[str, set[str]],
        physical_index_digest: str,
        now: datetime,
    ) -> tuple[datetime, dict[str, str]]:
        if journal["legacy"] or journal["native_schema_version"] != _PREVIOUS_JOURNAL_SCHEMA_VERSION:
            raise OSError("storage_corrupt")
        current = _utc(now, "archive clock")
        latest_allowed = _shift_datetime(current, _MAX_CLOCK_SKEW)
        projected_rows = self._v2_projection_rows(journal, physical_rows, locations)
        authenticated_maximum = self._v2_causal_lower_bound(list(projected_rows.values()))
        causal_lower = (
            None
            if authenticated_maximum is None
            else _shift_datetime(authenticated_maximum, -_MAX_CLOCK_SKEW)
        )
        persisted_upper = journal.get("transaction_mtime")
        if not projected_rows:
            selected_upper = (
                persisted_upper
                if type(persisted_upper) is datetime and persisted_upper <= latest_allowed
                else current
            )
            target = self._v2_projection_at_upper(
                journal,
                physical_index,
                upper=selected_upper,
                physical_rows=physical_rows,
                locations=locations,
                physical_index_digest=physical_index_digest,
            )
            return _shift_days(selected_upper, -90), target

        if causal_lower is None or causal_lower > latest_allowed:
            raise OSError("storage_corrupt")
        if (
            type(persisted_upper) is datetime
            and causal_lower <= persisted_upper <= latest_allowed
        ):
            try:
                target = self._v2_projection_at_upper(
                    journal,
                    physical_index,
                    upper=persisted_upper,
                    physical_rows=physical_rows,
                    locations=locations,
                    physical_index_digest=physical_index_digest,
                )
                return _shift_days(persisted_upper, -90), target
            except OSError:
                pass

        matched_target: dict[str, str] | None = (
            dict(physical_index)
            if physical_index_digest == journal["target_index_digest"]
            else None
        )
        if matched_target is None:
            try:
                matched_target = self._v2_projection_at_upper(
                    journal,
                    physical_index,
                    upper=causal_lower,
                    physical_rows=physical_rows,
                    locations=locations,
                    physical_index_digest=physical_index_digest,
                )
            except OSError:
                pass
        if matched_target is None:
            candidate_limit = min(16, max(1, _MAX_V2_CUTOFF_CANDIDATES))
            candidates: set[datetime] = set()
            one_tick = timedelta(microseconds=1)
            for observed in sorted({_event_time(row) for row in projected_rows.values()}):
                for boundary in (observed, _shift_days(observed, 90)):
                    for candidate in (
                        boundary,
                        _shift_datetime(boundary, -one_tick),
                        _shift_datetime(boundary, one_tick),
                    ):
                        if causal_lower <= candidate <= latest_allowed:
                            candidates.add(candidate)
                            if len(candidates) > candidate_limit:
                                raise OSError("storage_corrupt")
            matched_upper: datetime | None = None
            for candidate in sorted(candidates):
                try:
                    target = self._v2_projection_at_upper(
                        journal,
                        physical_index,
                        upper=candidate,
                        physical_rows=physical_rows,
                        locations=locations,
                        physical_index_digest=physical_index_digest,
                    )
                except OSError:
                    continue
                if matched_target is not None and target != matched_target:
                    raise OSError("storage_corrupt")
                matched_target = target
                matched_upper = candidate
            if matched_target is None or matched_upper is None:
                raise OSError("storage_corrupt")

        intervals = self._v2_allowed_upper_intervals(
            matched_target,
            projected_rows,
            lower=causal_lower,
            upper=latest_allowed,
        )
        if len(intervals) != 1:
            raise OSError("storage_corrupt")
        selected_upper = intervals[0][0]
        try:
            verified_target = self._v2_projection_at_upper(
                journal,
                physical_index,
                upper=selected_upper,
                physical_rows=physical_rows,
                locations=locations,
                physical_index_digest=physical_index_digest,
            )
        except OSError:
            raise OSError("storage_corrupt") from None
        if verified_target != matched_target:
            raise OSError("storage_corrupt")
        return _shift_days(selected_upper, -90), matched_target

    def _index_payload(self, events: dict[str, str]) -> bytes:
        if len(events) > _MAX_INDEX_EVENTS:
            raise ValueError("archive index is too large")
        canonical: dict[str, str] = {}
        for event_id, bucket in events.items():
            canonical[_bounded_text(event_id, "index event_id")] = _bounded_text(bucket, "index bucket")
            if not _valid_bucket_name(bucket):
                raise ValueError("invalid archive index bucket")
        document = {"schema_version": _INDEX_SCHEMA_VERSION, "events": dict(sorted(canonical.items()))}
        payload = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > _MAX_INDEX_BYTES:
            raise ValueError("archive index is too large")
        return payload

    def _write_index(self, events: dict[str, str]) -> None:
        self._atomic_write(self.index_path, self._index_payload(events), _MAX_INDEX_BYTES)


    def _read_journal(
        self,
        now: datetime,
        diagnostics: dict[str, int],
        *,
        budget: dict[str, int],
        legacy_state: dict[str, Any] | None = None,
        allow_unrecognized: bool = False,
        physical_index_record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        observed_metadata: list[tuple[int, int, int, int, int, int, int, int]] = []
        raw = self._read_budgeted_bytes(
            self.journal_path,
            _MAX_JOURNAL_BYTES,
            budget=budget,
            observed_metadata=observed_metadata,
        )
        if raw is None:
            if self._entry_present(self.journal_path):
                self._increment(diagnostics, "skipped_files")
                self._last_diagnostics = diagnostics
                raise OSError("storage_corrupt")
            return None
        owned_shape = False
        try:
            self._increment(diagnostics, "scanned_files")
            _preflight_json_payload(
                raw,
                maximum_depth=16,
                maximum_tokens=min(_MAX_SNAPSHOT_NODES * 4 + 16, budget["nodes"]),
            )
            top_level_keys: set[str] = set()
            _preflight_top_level_container_entries(
                raw,
                key="__archive_missing_key__",
                opening=0x5B,
                maximum_entries=0,
                observed_keys=top_level_keys,
            )
            owned_shape = frozenset(top_level_keys) in {
                frozenset(_LEGACY_JOURNAL_KEYS),
                frozenset(_PREVIOUS_JOURNAL_KEYS),
                frozenset(_JOURNAL_KEYS),
            }
            _preflight_top_level_array_rows(
                raw,
                key="rows",
                maximum_rows=budget["rows"],
            )
            document = self._parse_json(raw)
            if type(document) is not dict:
                raise ValueError("invalid archive journal")
            schema_version = document.get("schema_version")
            if type(schema_version) is not int:
                raise ValueError("invalid archive journal")
            legacy = schema_version == _LEGACY_JOURNAL_SCHEMA_VERSION
            previous_native = schema_version == _PREVIOUS_JOURNAL_SCHEMA_VERSION
            expected_keys = {
                _LEGACY_JOURNAL_SCHEMA_VERSION: _LEGACY_JOURNAL_KEYS,
                _PREVIOUS_JOURNAL_SCHEMA_VERSION: _PREVIOUS_JOURNAL_KEYS,
                _JOURNAL_SCHEMA_VERSION: _JOURNAL_KEYS,
            }.get(schema_version)
            if expected_keys is None or set(document) != expected_keys:
                if (
                    set(document) == {"schema_version", "owner"}
                    and type(document.get("owner")) is str
                    and 0 < len(document["owner"]) <= 128
                ) or allow_unrecognized:
                    return {"foreign": True}
                raise ValueError("invalid archive journal")
            owned_shape = True
            _document_bytes, document_nodes = _json_metrics(
                document,
                maximum_bytes=_MAX_JOURNAL_BYTES,
                maximum_nodes=budget["nodes"],
            )
            budget["nodes"] -= document_nodes
            if type(document["rows"]) is not list:
                raise ValueError("invalid archive journal")
            if len(document["rows"]) > _MAX_INDEX_EVENTS:
                raise ValueError("invalid archive journal")
            if len(document["rows"]) > budget["rows"]:
                raise ValueError("invalid archive journal")
            budget["rows"] -= len(document["rows"])
            self._increment(diagnostics, "scanned_rows", len(document["rows"]))
            rows = [_archive_from_document(row) for row in document["rows"]]
            event_ids = [row["event_id"] for row in rows]
            if event_ids != sorted(event_ids) or len(event_ids) != len(set(event_ids)):
                raise ValueError("invalid archive journal order")
            for row in rows:
                _validate_temporal_row(row, now)
            if len(observed_metadata) != 1:
                raise ValueError("invalid archive journal identity")
            file_metadata = observed_metadata[0]
            transaction_mtime = _file_mtime_utc(file_metadata)
            if legacy:
                return {
                    "legacy": True,
                    "rows": rows,
                    "file_identity": file_metadata[:2],
                    "file_digest": self._payload_digest(raw),
                    "transaction_mtime": transaction_mtime,
                }
            transaction_id = document["transaction_id"]
            base_generation = document["base_generation"]
            target_generation = document["target_generation"]
            base_index_digest = document["base_index_digest"]
            target_index_digest = document["target_index_digest"]
            base_bucket_digests = self._bucket_digest_map(document["base_bucket_digests"])
            target_bucket_digests = self._bucket_digest_map(document["target_bucket_digests"])
            cutoff_time: datetime | None = None
            target_index: dict[str, str] | None = None
            if not previous_native:
                cutoff_time = _metadata_time(document["cutoff"], "archive transaction cutoff")
                transaction_upper = _shift_days(cutoff_time, 90)
                if transaction_upper > _shift_datetime(now, _MAX_CLOCK_SKEW):
                    raise ValueError("invalid archive transaction cutoff")
                target_index = self._index_events(document["target_index"])
                if self._payload_digest(self._index_payload(target_index)) != target_index_digest:
                    raise ValueError("invalid archive transaction target index")
                for row in rows:
                    expected = _bucket_name(row) if cutoff_time <= _event_time(row) <= transaction_upper else None
                    if target_index.get(row["event_id"]) != expected:
                        raise ValueError("invalid archive transaction target index")
            if (
                type(transaction_id) is not str
                or _CONTENT_DIGEST.fullmatch(transaction_id) is None
                or type(base_generation) is not int
                or type(target_generation) is not int
                or base_generation < 0
                or target_generation <= base_generation
                or type(base_index_digest) is not str
                or _CONTENT_DIGEST.fullmatch(base_index_digest) is None
                or type(target_index_digest) is not str
                or _CONTENT_DIGEST.fullmatch(target_index_digest) is None
                or set(base_bucket_digests) != set(target_bucket_digests)
                or transaction_id != self._journal_transaction_id(
                    base_generation=base_generation,
                    target_generation=target_generation,
                    base_index_digest=base_index_digest,
                    target_index_digest=target_index_digest,
                    base_bucket_digests=base_bucket_digests,
                    target_bucket_digests=target_bucket_digests,
                    rows=rows,
                    cutoff=None if previous_native else cutoff_time.isoformat(),
                    target_index=target_index,
                )
            ):
                raise ValueError("invalid archive journal")
            state = legacy_state or {
                "schema_version": 1,
                "generation": 0,
                "phase": "finalized",
                "transaction_id": None,
                "index_digest": None,
                "bucket_digests": {},
            }
            actual_index_digest = (
                _MISSING_DIGEST
                if physical_index_record is None
                else physical_index_record["digest"]
            )
            completed = False
            if state["phase"] == "prepared":
                if (
                    state["generation"] != target_generation
                    or state["transaction_id"] != transaction_id
                    or state["index_digest"] != target_index_digest
                    or actual_index_digest != target_index_digest
                ):
                    raise ValueError("stale archive journal")
            else:
                if state["generation"] == target_generation:
                    if (
                        state["transaction_id"] != transaction_id
                        or state["index_digest"] != target_index_digest
                        or actual_index_digest != target_index_digest
                    ):
                        raise ValueError("stale archive journal")
                    completed = True
                elif (
                    state["generation"] != base_generation
                    or state["generation"] > 0
                    and state["index_digest"] != base_index_digest
                    or actual_index_digest not in {base_index_digest, target_index_digest}
                ):
                    raise ValueError("stale archive journal")
            return {
                "legacy": False,
                "rows": rows,
                "transaction_id": transaction_id,
                "base_generation": base_generation,
                "target_generation": target_generation,
                "base_index_digest": base_index_digest,
                "target_index_digest": target_index_digest,
                "base_bucket_digests": base_bucket_digests,
                "target_bucket_digests": target_bucket_digests,
                "state_phase": state["phase"],
                "completed": completed,
                "native_schema_version": schema_version,
                "cutoff": cutoff_time,
                "target_index": target_index,
                "physical_index_digest": actual_index_digest,
                "file_identity": file_metadata[:2],
                "file_digest": self._payload_digest(raw),
                "transaction_mtime": transaction_mtime,
            }
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, RecursionError, json.JSONDecodeError):
            if allow_unrecognized and not owned_shape:
                return {"foreign": True}
            self._increment(diagnostics, "skipped_files")
            self._last_diagnostics = diagnostics
            raise OSError("storage_corrupt") from None


    def _validate_journal_bucket_state(
        self,
        journal: dict[str, Any] | None,
        observed: dict[str, str],
    ) -> None:
        if journal is None or journal["legacy"]:
            return
        base = journal["base_bucket_digests"]
        target = journal["target_bucket_digests"]
        if set(observed) & set(base) != set(base):
            raise OSError("storage_corrupt")
        for name in base:
            actual = observed[name]
            allowed = (
                {target[name]}
                if journal["state_phase"] == "prepared" or journal["completed"]
                else {base[name], target[name]}
            )
            if actual not in allowed:
                raise OSError("storage_corrupt")



    def _read_all_buckets(
        self,
        *,
        now: datetime,
        diagnostics: dict[str, int],
        budget: dict[str, int],
        required_names: Iterator[str] | list[str] | set[str] | tuple[str, ...] = (),
        include_discovered: bool = True,
        strict_rows: bool = True,
        expected_digests: dict[str, set[str]] | None = None,
    ) -> tuple[
        dict[str, list[dict[str, Any]]],
        dict[str, str],
        dict[str, dict[str, Any]],
        dict[str, set[str]],
    ]:
        names = self._bounded_bucket_candidates(
            self._bucket_names(diagnostics=diagnostics) if include_discovered else (),
            required_names,
        )
        bucket_rows: dict[str, list[dict[str, Any]]] = {}
        observed: dict[str, str] = {}
        existing: dict[str, dict[str, Any]] = {}
        locations: dict[str, set[str]] = {}
        for name in sorted(names):
            rows = self._read_bucket(
                name,
                diagnostics,
                budget=budget,
                fail_on_budget=True,
                strict_rows=strict_rows,
                validation_now=now,
                observed_digests=observed,
                expected_digests=(
                    None
                    if expected_digests is None
                    else expected_digests.get(name)
                ),
            )
            bucket_rows[name] = rows
            for row in rows:
                event_id = row["event_id"]
                locations.setdefault(event_id, set()).add(name)
                previous = existing.get(event_id)
                existing[event_id] = row if previous is None else _merge_archive_rows(previous, row)
        return bucket_rows, observed, existing, locations

    def _validate_finalized_authority(
        self,
        state: dict[str, Any],
        *,
        now: datetime,
        diagnostics: dict[str, int],
        budget: dict[str, int],
        observed_index_record: list[dict[str, Any]] | None = None,
    ) -> tuple[
        dict[str, list[dict[str, Any]]],
        dict[str, str],
        dict[str, dict[str, Any]],
        dict[str, set[str]],
    ]:
        if state["legacy"] or state["phase"] != "finalized":
            raise OSError("storage_corrupt")
        physical_index_record = self._read_index_record(
            budget=budget,
            diagnostics=diagnostics,
        )
        physical_index = (
            None
            if physical_index_record is None
            else physical_index_record["events"]
        )
        if physical_index is None or physical_index != state["target_index"]:
            raise OSError("storage_corrupt")
        if physical_index_record["digest"] != state["target_index_digest"]:
            raise OSError("storage_corrupt")
        if observed_index_record is not None:
            observed_index_record.append(physical_index_record)
        active_cutoff = _shift_days(now, -90)
        active_upper = _shift_datetime(now, _MAX_CLOCK_SKEW)
        discovered = self._bucket_names(diagnostics=diagnostics)
        active_manifest = {
            name: digest
            for name, digest in state["target_bucket_digests"].items()
            if _bucket_name_in_window(name, active_cutoff, active_upper)
        }
        active_discovered = {
            name
            for name in discovered
            if _bucket_name_in_window(name, active_cutoff, active_upper)
        }
        active_index = {
            event_id: name
            for event_id, name in physical_index.items()
            if _bucket_name_in_window(name, active_cutoff, active_upper)
        }
        if set(active_index.values()) - set(active_manifest):
            raise OSError("storage_corrupt")
        for name in sorted(active_discovered - set(active_manifest)):
            unmanifested_rows = self._read_bucket(
                name,
                diagnostics,
                budget=budget,
                fail_on_budget=True,
                strict_rows=False,
                validation_now=now,
            )
            if any(
                active_cutoff <= _event_time(row) <= active_upper
                for row in unmanifested_rows
            ):
                raise OSError("storage_corrupt")
        bucket_rows, observed, existing, locations = self._read_all_buckets(
            now=now,
            diagnostics=diagnostics,
            budget=budget,
            required_names=active_manifest,
            include_discovered=False,
            expected_digests={
                name: {digest}
                for name, digest in active_manifest.items()
            },
        )
        if observed != active_manifest:
            self._last_diagnostics = diagnostics
            raise OSError("storage_corrupt")
        self._validate_index_projection(
            active_index,
            bucket_rows,
            cutoff=state["cutoff"],
            upper=_shift_days(state["cutoff"], 90),
        )
        return bucket_rows, observed, existing, locations

    def _plan_prepared_authority(
        self,
        state: dict[str, Any],
        *,
        now: datetime,
        diagnostics: dict[str, int],
        budget: dict[str, int],
        preloaded_bucket_rows: dict[str, list[dict[str, Any]]] | None = None,
        preloaded_observed: dict[str, str] | None = None,
        preloaded_locations: dict[str, set[str]] | None = None,
        physical_index_record: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if state["legacy"] or state["phase"] != "prepared":
            raise OSError("storage_corrupt")
        required = self._bounded_bucket_candidates(
            state["base_bucket_digests"],
            state["target_bucket_digests"],
            state["target_index"].values(),
        )
        supplied = (
            preloaded_bucket_rows is not None,
            preloaded_observed is not None,
            preloaded_locations is not None,
        )
        if any(supplied) and not all(supplied):
            raise OSError("storage_corrupt")
        if all(supplied):
            all_bucket_rows = dict(preloaded_bucket_rows or {})
            all_observed = dict(preloaded_observed or {})
            calculated_locations: dict[str, set[str]] = {}
            for name, rows in all_bucket_rows.items():
                for row in rows:
                    calculated_locations.setdefault(row["event_id"], set()).add(name)
            if calculated_locations != preloaded_locations:
                raise OSError("storage_corrupt")
        else:
            all_bucket_rows, all_observed, _all_existing, _all_locations = self._read_all_buckets(
                now=now,
                diagnostics=diagnostics,
                budget=budget,
                required_names=required,
                include_discovered=True,
                strict_rows=True,
                expected_digests={
                    name: {
                        state["base_bucket_digests"][name],
                        state["target_bucket_digests"][name],
                    }
                    for name in required
                },
            )
        discovered = {
            name
            for name, digest in all_observed.items()
            if digest != _MISSING_DIGEST
        }
        authority_upper = _shift_datetime(_shift_days(state["cutoff"], 90), _MAX_CLOCK_SKEW)
        unexpected_active = {
            name
            for name in discovered
            if _bucket_name_in_window(name, state["cutoff"], authority_upper)
            and name not in required
        }
        for name in sorted(unexpected_active):
            unexpected_rows = all_bucket_rows.get(name, [])
            if any(
                state["cutoff"] <= _event_time(row) <= authority_upper
                for row in unexpected_rows
            ):
                raise OSError("storage_corrupt")
        bucket_rows = {name: all_bucket_rows.get(name, []) for name in required}
        observed = {name: all_observed.get(name, _MISSING_DIGEST) for name in required}
        locations: dict[str, set[str]] = {}
        for name, rows in bucket_rows.items():
            for row in rows:
                locations.setdefault(row["event_id"], set()).add(name)
        if set(observed) != set(state["target_bucket_digests"]):
            raise OSError("storage_corrupt")
        for name, actual in observed.items():
            if actual not in {
                state["base_bucket_digests"][name],
                state["target_bucket_digests"][name],
            }:
                raise OSError("storage_corrupt")
        planned_index_digest = (
            _MISSING_DIGEST
            if physical_index_record is None
            else physical_index_record["digest"]
        )
        if planned_index_digest not in {state["base_index_digest"], state["target_index_digest"]}:
            raise OSError("storage_corrupt")
        if planned_index_digest == state["target_index_digest"]:
            # Prepared rows cannot manufacture physical membership for a target
            # index.  When the target differs from the base, writer ordering also
            # proves that every target manifest digest must already be physical.
            self._validate_index_projection(
                state["target_index"],
                bucket_rows,
                cutoff=state["cutoff"],
                upper=_shift_days(state["cutoff"], 90),
            )
            if (
                state["target_index_digest"] != state["base_index_digest"]
                and observed != state["target_bucket_digests"]
            ):
                raise OSError("storage_corrupt")
        affected = {
            name
            for name in state["target_bucket_digests"]
            if state["base_bucket_digests"][name] != state["target_bucket_digests"][name]
        }
        row_ids = {row["event_id"] for row in state["rows"]}
        if any(not locations.get(event_id, set()) <= affected for event_id in row_ids):
            raise OSError("storage_corrupt")
        removals = {name: set(row_ids) for name in affected}
        additions: dict[str, list[dict[str, Any]]] = {name: [] for name in affected}
        for row in state["rows"]:
            target_name = _bucket_name(row)
            if target_name not in affected:
                if state["base_bucket_digests"].get(target_name) != state["target_bucket_digests"].get(target_name):
                    raise OSError("storage_corrupt")
                continue
            additions[target_name].append(row)
        planned_rows = _apply_bucket_mutations(
            {name: bucket_rows.get(name, []) for name in affected},
            removals,
            additions,
        )
        bucket_payloads = {name: self._bucket_payload(name, rows) for name, rows in planned_rows.items()}
        if {
            name: self._payload_digest(bucket_payloads[name])
            for name in affected
        } != {
            name: state["target_bucket_digests"][name]
            for name in affected
        }:
            raise OSError("storage_corrupt")
        target_bucket_rows = dict(bucket_rows)
        target_bucket_rows.update(planned_rows)
        self._validate_index_projection(
            state["target_index"],
            target_bucket_rows,
            cutoff=state["cutoff"],
            upper=_shift_days(state["cutoff"], 90),
            authoritative_rows=state["rows"],
        )
        index_payload = self._index_payload(state["target_index"])
        if self._payload_digest(index_payload) != state["target_index_digest"]:
            raise OSError("storage_corrupt")
        finalized_payload = self._finalized_authority_payload(state)
        mutation_bytes = len(index_payload) + len(finalized_payload) + sum(map(len, bucket_payloads.values()))
        if mutation_bytes > _MAX_MUTATION_BYTES:
            raise OSError("storage_corrupt")
        return {
            "affected": affected,
            "observed": observed,
            "bucket_payloads": bucket_payloads,
            "index_payload": index_payload,
            "finalized_payload": finalized_payload,
            "actual_index_digest": planned_index_digest,
            "physical_index_record": physical_index_record,
            "mutation_bytes": mutation_bytes,
        }

    def _recover_prepared_authority(
        self,
        state: dict[str, Any],
        *,
        now: datetime,
        diagnostics: dict[str, int],
        budget: dict[str, int],
        plan: dict[str, Any] | None = None,
    ) -> None:
        if plan is None:
            physical_index_record = self._read_index_record(
                budget=budget,
                diagnostics=diagnostics,
            )
            recovery_plan = self._plan_prepared_authority(
                state,
                now=now,
                diagnostics=diagnostics,
                budget=budget,
                physical_index_record=physical_index_record,
            )
        else:
            recovery_plan = plan
        affected = recovery_plan["affected"]
        observed = recovery_plan["observed"]
        bucket_payloads = recovery_plan["bucket_payloads"]
        index_payload = recovery_plan["index_payload"]
        finalized_payload = recovery_plan["finalized_payload"]
        mutation_names = self._bounded_bucket_candidates(
            state["target_bucket_digests"],
            affected,
        )
        bucket_records = self._preflight_bucket_mutation_paths(mutation_names)
        if not self._index_record_is_current(recovery_plan["physical_index_record"]):
            raise OSError("storage_corrupt")
        if not self._bucket_mutation_records_are_current(bucket_records):
            raise OSError("storage_corrupt")
        for name in sorted(affected):
            if observed[name] != state["target_bucket_digests"][name]:
                self._atomic_write(self.archive_root / name, bucket_payloads[name], _MAX_BUCKET_BYTES)
        actual_index_digest = recovery_plan["actual_index_digest"]
        if actual_index_digest not in {state["base_index_digest"], state["target_index_digest"]}:
            raise OSError("storage_corrupt")
        if actual_index_digest != state["target_index_digest"]:
            self._atomic_write(self.index_path, index_payload, _MAX_INDEX_BYTES)
        self._atomic_write(self.state_path, finalized_payload, _MAX_STATE_BYTES)

    def _migration_prepared_payload(
        self,
        *,
        legacy_generation: int,
        cutoff: datetime,
        base_index_digest: str,
        index: dict[str, str],
        base_bucket_digests: dict[str, str],
        target_bucket_digests: dict[str, str],
        rows: list[dict[str, Any]],
    ) -> bytes:
        target_generation = max(0, legacy_generation) + 1
        index_payload = self._index_payload(index)
        index_digest = self._payload_digest(index_payload)
        return self._prepared_authority_payload(
            base_generation=target_generation - 1,
            target_generation=target_generation,
            cutoff=cutoff,
            base_index_digest=base_index_digest,
            target_index_digest=index_digest,
            base_bucket_digests=base_bucket_digests,
            target_bucket_digests=target_bucket_digests,
            target_index=index,
            rows=rows,
        )

    def _recover_first_legacy_journal(
        self,
        journal: dict[str, Any],
        *,
        now: datetime,
        diagnostics: dict[str, int],
        budget: dict[str, int],
        physical_index_record: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not journal["legacy"]:
            raise OSError("storage_corrupt")
        rows = [
            _mark_legacy_projection_unverifiable(row, validation_now=now)
            for row in journal["rows"]
        ]
        cutoff = _shift_days(now, -90)
        bucket_rows: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            bucket_rows.setdefault(_bucket_name(row), []).append(row)
        names = self._bounded_bucket_candidates(bucket_rows)
        bucket_payloads = {
            name: self._bucket_payload(name, selected)
            for name, selected in bucket_rows.items()
        }
        base_bucket_digests = {name: _MISSING_DIGEST for name in names}
        target_bucket_digests = {
            name: self._payload_digest(bucket_payloads[name])
            for name in names
        }
        target_index = {
            row["event_id"]: _bucket_name(row)
            for row in rows
            if cutoff <= _event_time(row) <= now
        }
        target_index_digest = self._payload_digest(self._index_payload(target_index))
        prepared_payload = self._prepared_authority_payload(
            base_generation=0,
            target_generation=1,
            cutoff=cutoff,
            base_index_digest=_MISSING_DIGEST,
            target_index_digest=target_index_digest,
            base_bucket_digests=base_bucket_digests,
            target_bucket_digests=target_bucket_digests,
            target_index=target_index,
            rows=rows,
        )
        prepared = self._parse_authority_state(self._parse_json(prepared_payload), now=now)
        recovery_plan = self._plan_prepared_authority(
            prepared,
            now=now,
            diagnostics=diagnostics,
            budget=budget,
            physical_index_record=physical_index_record,
        )
        if len(prepared_payload) + recovery_plan["mutation_bytes"] > _MAX_MUTATION_BYTES:
            raise OSError("storage_corrupt")
        bucket_records = self._preflight_bucket_mutation_paths(
            prepared["target_bucket_digests"],
        )
        if not self._index_record_is_current(recovery_plan["physical_index_record"]):
            raise OSError("storage_corrupt")
        if not self._bucket_mutation_records_are_current(bucket_records):
            raise OSError("storage_corrupt")
        self._atomic_write(self.state_path, prepared_payload, _MAX_STATE_BYTES)
        self._recover_prepared_authority(
            prepared,
            now=now,
            diagnostics=diagnostics,
            budget=budget,
            plan=recovery_plan,
        )
        recovered = self._read_authority_state(
            now,
            diagnostics=diagnostics,
            budget=budget,
        )
        if recovered is None or recovered["legacy"] or recovered["phase"] != "finalized":
            raise OSError("storage_corrupt")
        return recovered

    def _migrate_legacy_authority(
        self,
        legacy_state: dict[str, Any] | None,
        *,
        now: datetime,
        diagnostics: dict[str, int],
        budget: dict[str, int],
    ) -> dict[str, Any] | None:
        physical_index_record = self._read_index_record(
            budget=budget,
            diagnostics=diagnostics,
        )
        physical_index_document = (
            None
            if physical_index_record is None
            else physical_index_record["events"]
        )
        physical_index = physical_index_document or {}
        physical_index_digest = (
            _MISSING_DIGEST
            if physical_index_record is None
            else physical_index_record["digest"]
        )
        legacy_files = physical_index_record is not None or bool(
            self._bucket_names(diagnostics=diagnostics)
        )
        journal_only = legacy_state is None and not legacy_files
        state_document = None if legacy_state is None else legacy_state["document"]
        if state_document is not None:
            try:
                if (
                    type(state_document["generation"]) is not int
                    or state_document["generation"] < 1
                    or state_document["phase"] not in {"prepared", "finalized"}
                    or type(state_document["transaction_id"]) is not str
                    or _CONTENT_DIGEST.fullmatch(state_document["transaction_id"]) is None
                    or type(state_document["index_digest"]) is not str
                    or _CONTENT_DIGEST.fullmatch(state_document["index_digest"]) is None
                ):
                    raise ValueError("invalid legacy archive state")
                state_document["bucket_digests"] = self._bucket_digest_map(state_document["bucket_digests"])
            except (KeyError, TypeError, ValueError):
                raise OSError("storage_corrupt") from None
        journal = self._read_journal(
            now,
            diagnostics,
            budget=budget,
            legacy_state=state_document,
            allow_unrecognized=journal_only,
            physical_index_record=physical_index_record,
        )
        if journal is not None and journal.get("foreign") is True:
            journal = None
        if journal_only and journal is None:
            return None
        if journal_only and journal is not None and journal["legacy"]:
            return self._recover_first_legacy_journal(
                journal,
                now=now,
                diagnostics=diagnostics,
                budget=budget,
                physical_index_record=physical_index_record,
            )
        if state_document is not None and state_document["phase"] == "prepared" and journal is None:
            raise OSError("storage_corrupt")
        if journal is not None and not journal["legacy"]:
            (
                journal_bucket_rows,
                journal_observed,
                journal_existing,
                journal_locations,
            ) = self._read_all_buckets(
                now=now,
                diagnostics=diagnostics,
                budget=budget,
                required_names=journal["target_bucket_digests"],
                expected_digests={
                    name: {
                        journal["base_bucket_digests"][name],
                        journal["target_bucket_digests"][name],
                    }
                    for name in journal["target_bucket_digests"]
                },
            )
            self._validate_journal_bucket_state(journal, journal_observed)
            if journal["native_schema_version"] == _PREVIOUS_JOURNAL_SCHEMA_VERSION:
                cutoff, target_index = self._resolve_v2_transaction_projection(
                    journal,
                    physical_index,
                    physical_rows=journal_existing,
                    locations=journal_locations,
                    physical_index_digest=journal["physical_index_digest"],
                    now=now,
                )
            else:
                cutoff = journal["cutoff"]
                target_index = journal["target_index"]
                if type(cutoff) is not datetime or type(target_index) is not dict:
                    raise OSError("storage_corrupt")
            prepared_payload = self._prepared_authority_payload(
                base_generation=journal["base_generation"],
                target_generation=journal["target_generation"],
                cutoff=cutoff,
                base_index_digest=journal["base_index_digest"],
                target_index_digest=journal["target_index_digest"],
                base_bucket_digests=journal["base_bucket_digests"],
                target_bucket_digests=journal["target_bucket_digests"],
                target_index=target_index,
                rows=journal["rows"],
            )
            prepared = self._parse_authority_state(self._parse_json(prepared_payload), now=now)
            recovery_plan = self._plan_prepared_authority(
                prepared,
                now=now,
                diagnostics=diagnostics,
                budget=budget,
                preloaded_bucket_rows=journal_bucket_rows,
                preloaded_observed=journal_observed,
                preloaded_locations=journal_locations,
                physical_index_record=physical_index_record,
            )
            if len(prepared_payload) + recovery_plan["mutation_bytes"] > _MAX_MUTATION_BYTES:
                raise OSError("storage_corrupt")
            bucket_records = self._preflight_bucket_mutation_paths(
                prepared["target_bucket_digests"],
            )
            if not self._index_record_is_current(recovery_plan["physical_index_record"]):
                raise OSError("storage_corrupt")
            if not self._bucket_mutation_records_are_current(bucket_records):
                raise OSError("storage_corrupt")
            self._atomic_write(self.state_path, prepared_payload, _MAX_STATE_BYTES)
            self._recover_prepared_authority(
                prepared,
                now=now,
                diagnostics=diagnostics,
                budget=budget,
                plan=recovery_plan,
            )
            return self._read_authority_state(
                now,
                diagnostics=diagnostics,
                budget=budget,
            )

        if (
            state_document is not None
            and state_document["phase"] == "finalized"
            and physical_index_digest != state_document["index_digest"]
        ):
            raise OSError("storage_corrupt")
        if journal is None:
            required: set[str] | dict[str, str] = set()
        elif journal["legacy"]:
            required = self._bounded_bucket_candidates(
                tuple(_bucket_name(row) for row in journal["rows"]),
            )
        else:
            required = journal["target_bucket_digests"]
        bucket_rows, observed, existing, locations = self._read_all_buckets(
            now=now,
            diagnostics=diagnostics,
            budget=budget,
            required_names=required,
            strict_rows=bool(journal is not None and not journal["legacy"]),
            expected_digests=(
                {
                    name: {digest}
                    for name, digest in state_document["bucket_digests"].items()
                }
                if state_document is not None and state_document["bucket_digests"]
                else None
            ),
        )
        cutoff = _shift_days(now, -90)
        authority_upper = _shift_datetime(now, _MAX_CLOCK_SKEW)
        active_bucket_rows = {
            name: rows
            for name, rows in bucket_rows.items()
            if _bucket_name_in_window(name, cutoff, authority_upper)
        }
        active_nonempty_names = {
            name for name, rows in active_bucket_rows.items() if rows
        }
        historical_v1_unmanifested = bool(
            state_document is not None
            and state_document["schema_version"] == _LEGACY_ARCHIVE_SCHEMA_VERSION
            and state_document["phase"] == "finalized"
            and not state_document["bucket_digests"]
        )
        if state_document is not None and state_document["phase"] == "finalized":
            if historical_v1_unmanifested:
                # Historical state v1 authenticated its index but not bucket bytes.  Accept
                # only one exact physical projection, then label every migrated lineage as
                # unverifiable before schema-v4 computed manifests become authoritative.
                if (
                    diagnostics["skipped_files"] != 0
                    or diagnostics["skipped_corrupt_rows"] != 0
                    or set(physical_index) != set(existing)
                    or any(
                        locations.get(event_id) != {bucket_name}
                        or _bucket_name(existing[event_id]) != bucket_name
                        for event_id, bucket_name in physical_index.items()
                    )
                    or any(
                        lineage.get("legacy_v1") is not True
                        for row in existing.values()
                        for lineage in row["snapshot_history"]
                    )
                ):
                    raise OSError("storage_corrupt")
            else:
                active_declared = {
                    name: digest
                    for name, digest in state_document["bucket_digests"].items()
                    if _bucket_name_in_window(name, cutoff, authority_upper)
                }
                if (
                    set(active_declared) != active_nonempty_names
                    or any(observed.get(name) != digest for name, digest in active_declared.items())
                ):
                    raise OSError("storage_corrupt")
        elif state_document is not None and state_document["bucket_digests"]:
            if any(
                observed.get(name) != digest
                for name, digest in state_document["bucket_digests"].items()
            ):
                raise OSError("storage_corrupt")
        if journal is not None and not journal["legacy"]:
            self._validate_journal_bucket_state(journal, observed)
        if journal is not None and journal["legacy"]:
            matched: dict[str, int] = {}
            for row in journal["rows"]:
                previous = existing.get(row["event_id"])
                if (
                    previous is None
                    or locations.get(row["event_id"]) != {_bucket_name(row)}
                    or _legacy_archive_projection(previous) != _legacy_archive_projection(row)
                ):
                    raise OSError("storage_corrupt")
                matched[row["event_id"]] = matched.get(row["event_id"], 0) + 1
            if matched != {row["event_id"]: 1 for row in journal["rows"]}:
                raise OSError("storage_corrupt")
        active_physical_index = {
            event_id: name
            for event_id, name in physical_index.items()
            if _bucket_name_in_window(name, cutoff, authority_upper)
        }
        if state_document is None and journal is None and physical_index_document is None:
            if not existing:
                # A discovered bucket containing only one bounded corrupt row is
                # diagnostic input, not sufficient authority for a new v4 state.
                self._last_diagnostics = diagnostics
                return None
            active_physical_index = {
                event_id: _bucket_name(row)
                for event_id, row in existing.items()
                if cutoff <= _event_time(row) <= now
            }
        self._validate_index_projection(
            active_physical_index,
            active_bucket_rows,
            cutoff=cutoff,
            upper=now,
            allow_stale_index_entries=True,
        )
        migration_bucket_rows: dict[str, list[dict[str, Any]]] = {}
        for name, rows in active_bucket_rows.items():
            if not rows:
                continue
            migration_bucket_rows[name] = [
                _mark_legacy_projection_unverifiable(row, validation_now=now)
                if historical_v1_unmanifested
                else row
                for row in rows
            ]
        target_index = {
            event_id: _bucket_name(row)
            for event_id, row in existing.items()
            if cutoff <= _event_time(row) <= now
        }
        target_bucket_payloads = {
            name: self._bucket_payload(name, rows)
            for name, rows in migration_bucket_rows.items()
        }
        base_bucket_digests = {
            name: observed[name]
            for name in sorted(migration_bucket_rows)
        }
        target_bucket_digests = {
            name: self._payload_digest(payload)
            for name, payload in sorted(target_bucket_payloads.items())
        }
        changed_names = {
            name
            for name in target_bucket_digests
            if base_bucket_digests[name] != target_bucket_digests[name]
        }
        changed_rows = [
            row
            for name in sorted(changed_names)
            for row in migration_bucket_rows[name]
        ]
        legacy_generation = 0 if state_document is None else state_document["generation"]
        prepared_payload = self._migration_prepared_payload(
            legacy_generation=legacy_generation,
            cutoff=cutoff,
            base_index_digest=physical_index_digest,
            index=target_index,
            base_bucket_digests=base_bucket_digests,
            target_bucket_digests=target_bucket_digests,
            rows=changed_rows,
        )
        prepared = self._parse_authority_state(self._parse_json(prepared_payload), now=now)
        recovery_plan = self._plan_prepared_authority(
            prepared,
            now=now,
            diagnostics=diagnostics,
            budget=budget,
            preloaded_bucket_rows=bucket_rows,
            preloaded_observed=observed,
            preloaded_locations=locations,
            physical_index_record=physical_index_record,
        )
        if len(prepared_payload) + recovery_plan["mutation_bytes"] > _MAX_MUTATION_BYTES:
            raise OSError("storage_corrupt")
        bucket_records = self._preflight_bucket_mutation_paths(
            prepared["target_bucket_digests"],
        )
        if not self._index_record_is_current(recovery_plan["physical_index_record"]):
            raise OSError("storage_corrupt")
        if not self._bucket_mutation_records_are_current(bucket_records):
            raise OSError("storage_corrupt")
        self._atomic_write(self.state_path, prepared_payload, _MAX_STATE_BYTES)
        self._recover_prepared_authority(
            prepared,
            now=now,
            diagnostics=diagnostics,
            budget=budget,
            plan=recovery_plan,
        )
        return self._read_authority_state(
            now,
            diagnostics=diagnostics,
            budget=budget,
        )


    def upsert(self, snapshot: EvidenceSnapshot) -> None:
        _preflight_snapshot_input(snapshot)
        document = validated_snapshot_document(snapshot)
        archived_at = _utc(self._now(), "archive clock")
        incoming = [
            _archive_document(snapshot, event, archived_at)
            for event in document["events"]
        ]
        for row in incoming:
            _validate_temporal_row(row, archived_at)
        incoming_ids = [row["event_id"] for row in incoming]
        if len(incoming_ids) != len(set(incoming_ids)):
            raise ValueError("archive snapshot contains duplicate event identities")
        if len({_bucket_name(row) for row in incoming}) > _MAX_ARCHIVE_FILES:
            raise ValueError("archive snapshot has too many bucket candidates")
        _ensure_snapshot_budget(incoming)

        with CACHE_IO_LOCK, self._process_lock():
            diagnostics = self._diagnostics()
            budget = _new_scan_budget()
            state = self._read_authority_state(
                archived_at,
                diagnostics=diagnostics,
                budget=budget,
            )
            if state is None or state["legacy"]:
                state = self._migrate_legacy_authority(
                    state,
                    now=archived_at,
                    diagnostics=diagnostics,
                    budget=budget,
                )
            if state is not None and state["phase"] == "prepared":
                self._recover_prepared_authority(
                    state,
                    now=archived_at,
                    diagnostics=diagnostics,
                    budget=budget,
                )
                diagnostics = self._diagnostics()
                budget = _new_scan_budget()
                state = self._read_authority_state(
                    archived_at,
                    diagnostics=diagnostics,
                    budget=budget,
                )

            if state is None:
                if self._entry_present(self.index_path) or self._bucket_names(diagnostics=diagnostics):
                    raise OSError("storage_corrupt")
                bucket_rows: dict[str, list[dict[str, Any]]] = {}
                observed_bucket_digests: dict[str, str] = {}
                existing: dict[str, dict[str, Any]] = {}
                locations: dict[str, set[str]] = {}
                base_generation = 0
                base_index_digest = _MISSING_DIGEST
            else:
                observed_index_records: list[dict[str, Any]] = []
                (
                    bucket_rows,
                    observed_bucket_digests,
                    existing,
                    locations,
                ) = self._validate_finalized_authority(
                    state,
                    now=archived_at,
                    diagnostics=diagnostics,
                    budget=budget,
                    observed_index_record=observed_index_records,
                )
                if len(observed_index_records) != 1:
                    raise OSError("storage_corrupt")
                base_index_record = observed_index_records[0]
                base_generation = state["generation"]
                base_index_digest = state["target_index_digest"]

            cutoff = _shift_days(archived_at, -90)
            committed: dict[str, dict[str, Any]] = {}
            for row in incoming:
                event_id = row["event_id"]
                previous = existing.get(event_id)
                lineage_cutoff = cutoff if _event_time(row) >= cutoff else None
                committed[event_id] = row if previous is None else _merge_archive_rows(
                    previous,
                    row,
                    lineage_cutoff=lineage_cutoff,
                )
            _ensure_snapshot_budget(list(committed.values()))

            affected: set[str] = set()
            target_names: set[str] = set()
            for event_id, row in committed.items():
                affected.update(locations.get(event_id, set()))
                target_name = _bucket_name(row)
                affected.add(target_name)
                target_names.add(target_name)
                bucket_rows.setdefault(target_name, [])
                observed_bucket_digests.setdefault(target_name, _MISSING_DIGEST)
            all_names = self._bounded_bucket_candidates(bucket_rows, affected)
            if len(all_names) > _MAX_ARCHIVE_FILES:
                raise ValueError("archive snapshot has too many bucket candidates")

            removals: dict[str, set[str]] = {name: set() for name in affected}
            additions: dict[str, list[dict[str, Any]]] = {name: [] for name in affected}
            for event_id, row in committed.items():
                for name in locations.get(event_id, set()):
                    removals[name].add(event_id)
                target_name = _bucket_name(row)
                removals[target_name].add(event_id)
                additions[target_name].append(row)
            planned_rows = _apply_bucket_mutations(
                {name: bucket_rows.get(name, []) for name in affected},
                removals,
                additions,
            )
            if any(len(rows) > _MAX_BUCKET_ROWS for rows in planned_rows.values()):
                raise ValueError("archive bucket is full")

            current_rows = dict(existing)
            current_rows.update(committed)
            planned_index = {
                event_id: _bucket_name(row)
                for event_id, row in current_rows.items()
                if cutoff <= _event_time(row) <= archived_at
            }
            index_payload = self._index_payload(planned_index)
            target_index_digest = self._payload_digest(index_payload)
            bucket_payloads = {
                name: self._bucket_payload(name, rows)
                for name, rows in planned_rows.items()
            }
            base_bucket_digests = {
                name: observed_bucket_digests.get(name, _MISSING_DIGEST)
                for name in sorted(all_names)
            }
            target_bucket_digests = dict(base_bucket_digests)
            for name, payload in bucket_payloads.items():
                target_bucket_digests[name] = self._payload_digest(payload)

            target_bucket_rows = dict(bucket_rows)
            target_bucket_rows.update(planned_rows)
            self._validate_index_projection(
                planned_index,
                target_bucket_rows,
                cutoff=cutoff,
                upper=archived_at,
                authoritative_rows=list(committed.values()),
            )
            target_generation = base_generation + 1
            prepared_payload = self._prepared_authority_payload(
                base_generation=base_generation,
                target_generation=target_generation,
                cutoff=cutoff,
                base_index_digest=base_index_digest,
                target_index_digest=target_index_digest,
                base_bucket_digests=base_bucket_digests,
                target_bucket_digests=target_bucket_digests,
                target_index=planned_index,
                rows=list(committed.values()),
            )
            prepared = self._parse_authority_state(self._parse_json(prepared_payload), now=archived_at)
            finalized_payload = self._finalized_authority_payload(prepared)
            mutation_bytes = (
                len(prepared_payload)
                + len(finalized_payload)
                + len(index_payload)
                + sum(map(len, bucket_payloads.values()))
            )
            if mutation_bytes > _MAX_MUTATION_BYTES:
                raise ValueError("archive snapshot mutation budget exceeded")

            expected_index_record = None if state is None else base_index_record
            bucket_records = self._preflight_bucket_mutation_paths(
                target_bucket_digests,
            )
            if not self._index_record_is_current(expected_index_record):
                raise OSError("storage_corrupt")
            if not self._bucket_mutation_records_are_current(bucket_records):
                raise OSError("storage_corrupt")
            self._atomic_write(self.state_path, prepared_payload, _MAX_STATE_BYTES)
            write_order = sorted(target_names) + sorted(affected - target_names)
            for name in write_order:
                self._atomic_write(self.archive_root / name, bucket_payloads[name], _MAX_BUCKET_BYTES)
            self._atomic_write(self.index_path, index_payload, _MAX_INDEX_BYTES)
            self._atomic_write(self.state_path, finalized_payload, _MAX_STATE_BYTES)
            verification_diagnostics = self._diagnostics()
            verification_budget = _new_scan_budget()
            finalized = self._read_authority_state(
                archived_at,
                diagnostics=verification_diagnostics,
                budget=verification_budget,
            )
            if finalized is None:
                raise OSError("storage_corrupt")
            self._validate_finalized_authority(
                finalized,
                now=archived_at,
                diagnostics=verification_diagnostics,
                budget=verification_budget,
            )


    def _query_unlocked(self, days: int, status: str | None) -> list[dict[str, Any]]:
        now = _utc(self._now(), "archive clock")
        diagnostics = self._diagnostics()
        budget = _new_scan_budget()
        state = self._read_authority_state(now, diagnostics=diagnostics, budget=budget)
        if state is None or state["legacy"]:
            state = self._migrate_legacy_authority(
                state,
                now=now,
                diagnostics=diagnostics,
                budget=budget,
            )
        if state is None:
            self._last_diagnostics = diagnostics
            return []
        if state["phase"] == "prepared":
            self._recover_prepared_authority(
                state,
                now=now,
                diagnostics=diagnostics,
                budget=budget,
            )
            diagnostics = self._diagnostics()
            budget = _new_scan_budget()
            state = self._read_authority_state(now, diagnostics=diagnostics, budget=budget)
            if state is None:
                raise OSError("storage_corrupt")
        _bucket_rows, _observed, existing, _locations = self._validate_finalized_authority(
            state,
            now=now,
            diagnostics=diagnostics,
            budget=budget,
        )
        cutoff = _shift_days(now, -days)
        self._last_diagnostics = diagnostics
        return sorted(
            (
                row
                for row in existing.values()
                if cutoff <= _event_time(row) <= now
                and (status is None or row["verification_status"] == status)
            ),
            key=lambda row: (_event_time(row), _parse_datetime(row["verified_at"]), row["event_id"]),
            reverse=True,
        )

    def _query_without_lock(self, days: int, status: str | None) -> list[dict[str, Any]]:
        del days, status
        diagnostics = self._diagnostics()
        if (
            self._read_directory_present(self.root)
            and self._read_directory_present(self.archive_root)
        ):
            self._bucket_names(diagnostics=diagnostics, prepare=False)
        self._last_diagnostics = diagnostics
        return []

    def query(self, days: int, status: str | None = None) -> list[dict[str, Any]]:
        if type(days) is not int or days not in _ALLOWED_DAYS:
            raise ValueError("days must be one of 1, 3, 7, 30, 90")
        if status is not None and (type(status) is not str or status not in _KNOWN_STATUSES):
            raise ValueError("invalid verification status")
        with CACHE_IO_LOCK:
            with self._reader_process_lock() as locked:
                return self._query_unlocked(days, status) if locked else self._query_without_lock(days, status)

    def get(self, event_id: str) -> dict[str, Any] | None:
        if type(event_id) is not str or not event_id or len(event_id) > 128:
            return None
        with CACHE_IO_LOCK:
            with self._reader_process_lock() as locked:
                rows = self._query_unlocked(90, None) if locked else self._query_without_lock(90, None)
                return next((row for row in rows if row["event_id"] == event_id), None)

    def count(self) -> int:
        with CACHE_IO_LOCK:
            with self._reader_process_lock() as locked:
                rows = self._query_unlocked(90, None) if locked else self._query_without_lock(90, None)
                return len(rows)
