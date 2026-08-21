from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from evidence_verification.archive import EvidenceArchive

from .service import NewsPipelineActiveError, get_service


router = APIRouter(prefix="/api", tags=["news-pipeline"])

ArchiveStatus = Literal[
    "verified",
    "corroborated",
    "unverified",
    "conflicting",
    "corrected",
    "disproved",
]

_ARCHIVE_CURSOR_KEYS = {
    "v", "days", "verification_status", "limit", "event_time", "verified_at", "event_id",
}
_ARCHIVE_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_ARCHIVE_CURSOR_MAX_LENGTH = 2_048
_ARCHIVE_PAGE_MAX_BYTES = 3 * 1_048_576


def _archive_provenance(event: dict[str, object]) -> dict[str, object]:
    provenance: dict[str, object] = {
        "event_id": event["event_id"],
        "evidence_snapshot_id": event["evidence_snapshot_id"],
        "raw_snapshot_id": event["raw_snapshot_id"],
        "snapshot_history": event["snapshot_history"],
    }
    history = event["snapshot_history"]
    if isinstance(history, list):
        recovery = [
            lineage["recovery"]
            for lineage in history
            if isinstance(lineage, dict) and isinstance(lineage.get("recovery"), dict)
        ]
        if recovery:
            provenance["recovery"] = recovery
    return provenance


def _archive_utc(value: object) -> datetime:
    if type(value) is not str or not value or len(value) > 64:
        raise ValueError("invalid archive cursor")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid archive cursor") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("invalid archive cursor")
    canonical = parsed.astimezone(timezone.utc)
    if value != canonical.isoformat():
        raise ValueError("invalid archive cursor")
    return canonical


def _archive_order_key(event: dict[str, object]) -> tuple[datetime, datetime, str]:
    if type(event) is not dict:
        raise RuntimeError("archive contract mismatch")
    event_id = event.get("event_id")
    if type(event_id) is not str or not event_id or len(event_id) > 128:
        raise RuntimeError("archive contract mismatch")
    verified_at = event.get("verified_at")
    event_time = event.get("published_at")
    try:
        return (
            _archive_utc(verified_at if event_time is None else event_time),
            _archive_utc(verified_at),
            event_id,
        )
    except ValueError:
        raise RuntimeError("archive contract mismatch") from None


def _archive_cursor(
    key: tuple[datetime, datetime, str],
    *,
    days: int,
    verification_status: ArchiveStatus | None,
    limit: int,
) -> str:
    document = {
        "v": 1,
        "days": days,
        "verification_status": verification_status,
        "limit": limit,
        "event_time": key[0].isoformat(),
        "verified_at": key[1].isoformat(),
        "event_id": key[2],
    }
    payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _archive_cursor_document(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("invalid archive cursor")
        document[key] = value
    return document


def _archive_cursor_key(
    cursor: str,
    *,
    days: int,
    verification_status: ArchiveStatus | None,
    limit: int,
) -> tuple[datetime, datetime, str]:
    if (
        type(cursor) is not str
        or not cursor
        or len(cursor) > _ARCHIVE_CURSOR_MAX_LENGTH
        or _ARCHIVE_CURSOR_PATTERN.fullmatch(cursor) is None
    ):
        raise ValueError("invalid archive cursor")
    try:
        payload = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
        if base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=") != cursor:
            raise ValueError("invalid archive cursor")
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_archive_cursor_document,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise ValueError("invalid archive cursor") from None
    if (
        type(document) is not dict
        or set(document) != _ARCHIVE_CURSOR_KEYS
        or type(document["v"]) is not int
        or document["v"] != 1
        or type(document["days"]) is not int
        or document["days"] != days
        or document["verification_status"] != verification_status
        or type(document["limit"]) is not int
        or document["limit"] != limit
        or type(document["event_id"]) is not str
        or not document["event_id"]
        or len(document["event_id"]) > 128
    ):
        raise ValueError("invalid archive cursor")
    return (
        _archive_utc(document["event_time"]),
        _archive_utc(document["verified_at"]),
        document["event_id"],
    )


def _archive_json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _archive_page_size(
    *,
    events: list[dict[str, object]],
    total: int,
    days: int,
    verification_status: ArchiveStatus | None,
    diagnostics: dict[str, int],
    provenance: list[dict[str, object]],
    limit: int,
    has_more: bool,
    next_cursor: str | None,
) -> int:
    return _archive_json_size({"data": {
        "events": events,
        "total": total,
        "filters": {"days": days, "verification_status": verification_status},
        "diagnostics": diagnostics,
        "provenance": provenance,
        "page": {
            "limit": limit,
            "returned": len(events),
            "has_more": has_more,
            "next_cursor": next_cursor,
        },
    }})


def _archive_page(
    events: list[dict[str, object]],
    *,
    total: int,
    days: int,
    verification_status: ArchiveStatus | None,
    diagnostics: dict[str, int],
    limit: int,
    cursor_key: tuple[datetime, datetime, str] | None,
) -> dict[str, object]:
    keys = [_archive_order_key(event) for event in events]
    if (
        len({event["event_id"] for event in events}) != len(events)
        or len(set(keys)) != len(keys)
        or keys != sorted(keys, reverse=True)
    ):
        raise RuntimeError("archive contract mismatch")
    start = 0
    if cursor_key is not None:
        try:
            start = keys.index(cursor_key) + 1
        except ValueError:
            raise ValueError("invalid archive cursor") from None
    candidates = events[start:]
    selected: list[dict[str, object]] = []
    provenance: list[dict[str, object]] = []
    for index, event in enumerate(candidates):
        candidate_events = [*selected, event]
        candidate_provenance = [*provenance, _archive_provenance(event)]
        has_more = index + 1 < len(candidates)
        next_cursor = _archive_cursor(
            keys[start + index],
            days=days,
            verification_status=verification_status,
            limit=limit,
        ) if has_more else None
        size = _archive_page_size(
            events=candidate_events,
            total=total,
            days=days,
            verification_status=verification_status,
            diagnostics=diagnostics,
            provenance=candidate_provenance,
            limit=limit,
            has_more=has_more,
            next_cursor=next_cursor,
        )
        if size > _ARCHIVE_PAGE_MAX_BYTES:
            if not selected:
                raise RuntimeError("archive page too large")
            break
        selected = candidate_events
        provenance = candidate_provenance
        if len(selected) == limit:
            break
    has_more = start + len(selected) < len(events)
    next_cursor = _archive_cursor(
        keys[start + len(selected) - 1],
        days=days,
        verification_status=verification_status,
        limit=limit,
    ) if has_more and selected else None
    result = {
        "events": selected,
        "total": total,
        "filters": {"days": days, "verification_status": verification_status},
        "diagnostics": diagnostics,
        "provenance": provenance,
        "page": {
            "limit": limit,
            "returned": len(selected),
            "has_more": has_more,
            "next_cursor": next_cursor,
        },
    }
    if _archive_json_size({"data": result}) > _ARCHIVE_PAGE_MAX_BYTES:
        raise RuntimeError("archive page too large")
    return result


def _start_pipeline() -> dict[str, object]:
    try:
        run = get_service().start()
    except NewsPipelineActiveError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "资讯刷新正在运行") from error
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "资讯刷新暂时不可用") from error
    return {"data": {
        "run_id": run.run_id,
        "raw_snapshot_id": run.raw_snapshot_id,
        "phase": run.phase.value,
    }}


@router.post("/market-news/refresh", status_code=status.HTTP_202_ACCEPTED)
def market_news_refresh(
    mode: Literal["my_focus", "my_holdings", "global_tech", "domestic_policy"] = "my_focus",
    tag_id: list[str] = Query(default=[]),
    category: Literal["all", "policy", "industry", "company", "fund_notice", "deep_content"] = "all",
    days: int = 7,
    sort: Literal["importance", "latest", "holding_relevance"] = "importance",
):
    if days not in {1, 3, 7, 30}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid market-news filters")
    # Query values remain accepted for compatibility; filtering happens on the
    # existing read endpoint after the shared snapshot is published.
    _ = mode, tag_id, category, sort
    return _start_pipeline()


@router.post("/evidence/refresh", status_code=status.HTTP_202_ACCEPTED)
def evidence_refresh():
    return _start_pipeline()


@router.post("/radar/refresh", status_code=status.HTTP_202_ACCEPTED)
def radar_refresh():
    return _start_pipeline()


@router.get("/news/pipeline-status")
def pipeline_status(run_id: str | None = Query(default=None, min_length=1, max_length=128)):
    try:
        return {"data": get_service().get_status(run_id)}
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资讯刷新运行不存在") from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "无效的资讯刷新运行 ID") from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "资讯刷新状态暂时不可用") from error


@router.get("/news/archive")
def news_archive(
    days: int = 90,
    verification_status: ArchiveStatus | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = None,
):
    if days not in {1, 3, 7, 30, 90}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "无效的资讯历史筛选")
    if cursor is not None and limit is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "无效的资讯历史游标")
    try:
        cursor_key = None if cursor is None else _archive_cursor_key(
            cursor,
            days=days,
            verification_status=verification_status,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "无效的资讯历史游标") from error
    try:
        archive = EvidenceArchive()
        events = archive.query(days, verification_status)
        if type(events) is not list or (
            verification_status is not None
            and any(
                type(event) is not dict
                or event.get("verification_status") != verification_status
                for event in events
            )
        ):
            raise RuntimeError("archive contract mismatch")
        if limit is not None:
            return {"data": _archive_page(
                events,
                total=len(events),
                days=days,
                verification_status=verification_status,
                diagnostics=archive.last_diagnostics,
                limit=limit,
                cursor_key=cursor_key,
            )}
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "无效的资讯历史筛选") from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "资讯历史暂时不可用") from error
    return {"data": {
        "events": events,
        "total": len(events),
        "filters": {"days": days, "verification_status": verification_status},
        "diagnostics": archive.last_diagnostics,
        "provenance": [_archive_provenance(event) for event in events],
    }}
