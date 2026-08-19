"""Exact, atomic cost reservations and reconciled provider usage."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, Iterable, Mapping
import uuid

from cache_io_lock import CACHE_IO_LOCK


class UsageStoreError(RuntimeError):
    """Raised when the usage ledger cannot be trusted or persisted."""


class UsageValidationError(UsageStoreError, ValueError):
    """Raised for invalid usage input or ledger content."""


class UsageConflictError(UsageStoreError):
    """Raised when an idempotency key is reused with different facts."""


class UsageBudgetExceeded(UsageStoreError):
    """Raised when an atomic reservation would exceed a cost budget."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_LEDGER_VERSION = 1
_DEFAULT_MAX_LEDGER_BYTES = 4 * 1024 * 1024
_MAX_DECIMAL_PLACES = 8
_MAX_DECIMAL_DIGITS = 28
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_STATUS = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CREDENTIAL_TERMS = (
    "credential", "secret", "token", "password", "api_key", "apikey",
    "access_key", "private_key", "authorization", "bearer", "cookie",
)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_RECORD_FIELDS = {
    "reservation_id", "adapter_id", "authorized_at", "recorded_at",
    "estimated_cost", "actual_cost", "request_count", "status", "units",
}


def _validate_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise UsageValidationError(f"{field} must be a Decimal")
    if not value.is_finite() or value < 0:
        raise UsageValidationError(f"{field} must be a finite non-negative Decimal")
    sign, digits, exponent = value.as_tuple()
    del sign
    if exponent < -_MAX_DECIMAL_PLACES or len(digits) > _MAX_DECIMAL_DIGITS:
        raise UsageValidationError(f"{field} precision is invalid")
    return value


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _validate_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise UsageValidationError(f"{field} is invalid")
    if field == "reservation_id" and any(marker in value.lower() for marker in _CREDENTIAL_TERMS):
        raise UsageValidationError(f"{field} is invalid")
    return value


def _validate_status(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_STATUS.fullmatch(value):
        raise UsageValidationError("status is invalid")
    if any(marker in value.lower() for marker in _CREDENTIAL_TERMS) or value == "reserved":
        raise UsageValidationError("status is invalid")
    return value


def _utc_datetime(value: object, field: str = "now") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise UsageValidationError(f"{field} must be a timezone-aware datetime")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise UsageValidationError(f"{field} is invalid") from None


def _timestamp_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise UsageValidationError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise UsageValidationError(f"{field} is invalid") from None
    if _timestamp_text(parsed) != value:
        raise UsageValidationError(f"{field} is not canonical")
    return parsed


def _parse_decimal_text(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise UsageValidationError(f"{field} must be a decimal string")
    try:
        parsed = Decimal(value)
    except Exception:
        raise UsageValidationError(f"{field} must be a decimal string") from None
    _validate_decimal(parsed, field)
    if _decimal_text(parsed) != value:
        raise UsageValidationError(f"{field} is not canonical")
    return parsed


@dataclass(frozen=True, slots=True)
class UsageRecord:
    reservation_id: str
    adapter_id: str
    authorized_at: datetime
    recorded_at: datetime | None
    estimated_cost: Decimal
    actual_cost: Decimal | None
    request_count: int
    status: str
    units: Decimal

    def __post_init__(self) -> None:
        _validate_identifier(self.reservation_id, "reservation_id")
        _validate_identifier(self.adapter_id, "adapter_id")
        authorized_at = _utc_datetime(self.authorized_at, "authorized_at")
        recorded_at = (
            None if self.recorded_at is None else _utc_datetime(self.recorded_at, "recorded_at")
        )
        _validate_decimal(self.estimated_cost, "estimated_cost")
        if self.actual_cost is not None:
            _validate_decimal(self.actual_cost, "actual_cost")
        if isinstance(self.request_count, bool) or not isinstance(self.request_count, int) or self.request_count < 0:
            raise UsageValidationError("request_count is invalid")
        _validate_decimal(self.units, "units")
        if self.actual_cost is None:
            if recorded_at is not None or self.request_count != 0 or self.status != "reserved" or self.units != 0:
                raise UsageValidationError("open reservation state is invalid")
        else:
            _validate_status(self.status)
            if recorded_at is None or recorded_at < authorized_at:
                raise UsageValidationError("reconciled timestamp is invalid")
        object.__setattr__(self, "authorized_at", authorized_at)
        object.__setattr__(self, "recorded_at", recorded_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "reservation_id": self.reservation_id,
            "adapter_id": self.adapter_id,
            "authorized_at": _timestamp_text(self.authorized_at),
            "recorded_at": _timestamp_text(self.recorded_at) if self.recorded_at else None,
            "estimated_cost": _decimal_text(self.estimated_cost),
            "actual_cost": _decimal_text(self.actual_cost) if self.actual_cost is not None else None,
            "request_count": self.request_count,
            "status": self.status,
            "units": _decimal_text(self.units),
        }

    @classmethod
    def from_dict(cls, value: object) -> "UsageRecord":
        if not isinstance(value, Mapping) or set(value) != _RECORD_FIELDS:
            raise UsageValidationError("usage record schema is invalid")
        reservation_id = _validate_identifier(value["reservation_id"], "reservation_id")
        adapter_id = _validate_identifier(value["adapter_id"], "adapter_id")
        authorized_at = _parse_timestamp(value["authorized_at"], "authorized_at")
        recorded_raw = value["recorded_at"]
        recorded_at = None if recorded_raw is None else _parse_timestamp(recorded_raw, "recorded_at")
        estimated_cost = _parse_decimal_text(value["estimated_cost"], "estimated_cost")
        actual_raw = value["actual_cost"]
        actual_cost = None if actual_raw is None else _parse_decimal_text(actual_raw, "actual_cost")
        request_count = value["request_count"]
        if isinstance(request_count, bool) or not isinstance(request_count, int) or request_count < 0:
            raise UsageValidationError("request_count is invalid")
        status = value["status"]
        units = _parse_decimal_text(value["units"], "units")
        if actual_cost is None:
            if recorded_at is not None or request_count != 0 or status != "reserved" or units != 0:
                raise UsageValidationError("open reservation state is invalid")
        else:
            _validate_status(status)
            if recorded_at is None or recorded_at < authorized_at:
                raise UsageValidationError("reconciled timestamp is invalid")
        return cls(
            reservation_id, adapter_id, authorized_at, recorded_at,
            estimated_cost, actual_cost, request_count, status, units,
        )


@dataclass(frozen=True, slots=True)
class UsageSummary:
    adapter_id: str
    day: str
    month: str
    daily_cost: Decimal
    monthly_cost: Decimal
    daily_request_count: int
    monthly_request_count: int
    daily_units: Decimal
    monthly_units: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            "adapter_id": self.adapter_id,
            "day": self.day,
            "month": self.month,
            "daily_cost": _decimal_text(self.daily_cost),
            "monthly_cost": _decimal_text(self.monthly_cost),
            "daily_request_count": self.daily_request_count,
            "monthly_request_count": self.monthly_request_count,
            "daily_units": _decimal_text(self.daily_units),
            "monthly_units": _decimal_text(self.monthly_units),
        }


class UsageStore:
    """Persist a bounded usage ledger under ``VR_DATA_DIR`` with process locking."""

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        lock_timeout_seconds: float = 2.0,
        max_ledger_bytes: int = _DEFAULT_MAX_LEDGER_BYTES,
    ) -> None:
        if root is None:
            configured = os.environ.get("VR_DATA_DIR")
            root = (
                Path(configured)
                if configured and configured.strip()
                else Path(os.environ.get("USERPROFILE") or Path.home()) / ".vibe-research"
            )
        if isinstance(lock_timeout_seconds, bool) or not isinstance(lock_timeout_seconds, (int, float)):
            raise UsageValidationError("lock timeout is invalid")
        try:
            timeout = float(lock_timeout_seconds)
        except (OverflowError, TypeError, ValueError):
            raise UsageValidationError("lock timeout is invalid") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise UsageValidationError("lock timeout is invalid")
        if isinstance(max_ledger_bytes, bool) or not isinstance(max_ledger_bytes, int) or max_ledger_bytes <= 0:
            raise UsageValidationError("max ledger bytes is invalid")
        self._data_root = Path(root)
        self.root = self._data_root / "data-sources" / "v1"
        self.path = self.root / "usage.json"
        self._lock_path = self.root / ".usage.lock"
        self._lock_timeout_seconds = timeout
        self._max_ledger_bytes = max_ledger_bytes

    @staticmethod
    def _is_reparse_or_link(metadata: os.stat_result) -> bool:
        return stat.S_ISLNK(metadata.st_mode) or bool(
            getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT
        )

    def _assert_no_reparse_components(self, path: Path) -> None:
        parts = path.parts
        if not parts:
            raise UsageStoreError("usage path is unsafe")
        if path.anchor:
            current = Path(path.anchor)
            path_parts = parts[len(Path(path.anchor).parts):]
        else:
            current = Path()
            path_parts = parts
        for part in path_parts:
            current = current / part
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                break
            except OSError:
                raise UsageStoreError("usage path is unsafe") from None
            if self._is_reparse_or_link(metadata):
                raise UsageStoreError("usage path is unsafe")

    def _prepare_root(self) -> None:
        self._assert_no_reparse_components(self._data_root)
        self._assert_no_reparse_components(self.root)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise UsageStoreError("usage path is unavailable") from None
        self._assert_no_reparse_components(self._data_root)
        self._assert_no_reparse_components(self.root)
        self._assert_no_reparse_components(self.path)
        self._assert_no_reparse_components(self._lock_path)
        try:
            resolved_data_root = self._data_root.resolve(strict=True)
            resolved_usage_root = self.root.resolve(strict=True)
            resolved_usage_root.relative_to(resolved_data_root)
        except (OSError, ValueError):
            raise UsageStoreError("usage path is unsafe") from None

    def _try_lock_file(self, handle: Any) -> bool:
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
    def _unlock_file(handle: Any) -> None:
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
    def _process_lock(self):
        self._prepare_root()
        try:
            handle = self._lock_path.open("a+b")
        except OSError:
            raise UsageStoreError("usage lock is unavailable") from None
        acquired = False
        deadline = time.monotonic() + self._lock_timeout_seconds
        try:
            while not (acquired := self._try_lock_file(handle)):
                if time.monotonic() >= deadline:
                    raise UsageStoreError("usage lock is unavailable")
                time.sleep(0.01)
            yield
        finally:
            if acquired:
                self._unlock_file(handle)
            handle.close()

    def _atomic_write(self, records: Iterable[UsageRecord]) -> None:
        document = {"version": _LEDGER_VERSION, "records": [record.to_dict() for record in records]}
        descriptor, raw_path = tempfile.mkstemp(prefix=".usage.", suffix=".tmp", dir=self.root)
        temporary = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if temporary.stat().st_size > self._max_ledger_bytes:
                raise UsageStoreError("usage ledger is too large")
            os.replace(temporary, self.path)
        except BaseException as exc:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temporary.unlink()
            except OSError:
                pass
            if isinstance(exc, UsageStoreError):
                raise
            raise UsageStoreError("usage ledger could not be written") from None

    def _load_unlocked(self, reference_now: datetime) -> list[UsageRecord]:
        if not self.path.exists():
            return []
        try:
            if self.path.stat().st_size > self._max_ledger_bytes:
                raise UsageStoreError("usage ledger is too large")
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except UsageStoreError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise UsageStoreError("usage ledger is corrupt") from None
        if (
            not isinstance(document, Mapping)
            or set(document) != {"version", "records"}
            or document.get("version") != _LEDGER_VERSION
            or not isinstance(document.get("records"), list)
        ):
            raise UsageStoreError("usage ledger schema is invalid")
        try:
            records = [UsageRecord.from_dict(row) for row in document["records"]]
        except UsageValidationError as exc:
            raise UsageStoreError("usage ledger record is invalid") from exc
        seen: set[str] = set()
        for record in records:
            if record.reservation_id in seen:
                raise UsageStoreError("usage ledger contains duplicate reservations")
            seen.add(record.reservation_id)
            if record.authorized_at > reference_now:
                raise UsageValidationError("usage ledger contains a future authorization")
            if record.recorded_at is not None and record.recorded_at > reference_now:
                raise UsageValidationError("usage ledger contains a future reconciliation")
        return records

    @staticmethod
    def _summary_from(records: Iterable[UsageRecord], adapter_id: str, now: datetime) -> UsageSummary:
        day = now.date().isoformat()
        month = now.strftime("%Y-%m")
        daily_cost = Decimal("0")
        monthly_cost = Decimal("0")
        daily_requests = 0
        monthly_requests = 0
        daily_units = Decimal("0")
        monthly_units = Decimal("0")
        for record in records:
            if record.adapter_id != adapter_id:
                continue
            cost = record.actual_cost if record.actual_cost is not None else record.estimated_cost
            record_day = record.authorized_at.date().isoformat()
            record_month = record.authorized_at.strftime("%Y-%m")
            if record_month == month:
                monthly_cost += cost
                monthly_requests += record.request_count
                monthly_units += record.units
            if record_day == day:
                daily_cost += cost
                daily_requests += record.request_count
                daily_units += record.units
        return UsageSummary(
            adapter_id, day, month, daily_cost, monthly_cost,
            daily_requests, monthly_requests, daily_units, monthly_units,
        )

    def records(self, *, now: datetime) -> tuple[UsageRecord, ...]:
        normalized_now = _utc_datetime(now)
        with CACHE_IO_LOCK:
            with self._process_lock():
                return tuple(self._load_unlocked(normalized_now))

    def summary(self, adapter_id: str, *, now: datetime) -> UsageSummary:
        normalized_id = _validate_identifier(adapter_id, "adapter_id")
        normalized_now = _utc_datetime(now)
        with CACHE_IO_LOCK:
            with self._process_lock():
                records = self._load_unlocked(normalized_now)
                return self._summary_from(records, normalized_id, normalized_now)

    def reserve(
        self,
        adapter_id: str,
        *,
        estimated_cost: Decimal,
        daily_budget: Decimal,
        monthly_budget: Decimal,
        now: datetime,
        reservation_id: str | None = None,
    ) -> UsageRecord:
        normalized_id = _validate_identifier(adapter_id, "adapter_id")
        estimate = _validate_decimal(estimated_cost, "estimated_cost")
        daily_limit = _validate_decimal(daily_budget, "daily_budget")
        monthly_limit = _validate_decimal(monthly_budget, "monthly_budget")
        normalized_now = _utc_datetime(now)
        normalized_reservation = _validate_identifier(
            reservation_id or uuid.uuid4().hex, "reservation_id"
        )
        with CACHE_IO_LOCK:
            with self._process_lock():
                records = self._load_unlocked(normalized_now)
                for record in records:
                    if record.reservation_id != normalized_reservation:
                        continue
                    if (
                        record.adapter_id == normalized_id
                        and record.estimated_cost == estimate
                        and record.authorized_at == normalized_now
                    ):
                        return record
                    raise UsageConflictError("reservation identifier is already in use")
                summary = self._summary_from(records, normalized_id, normalized_now)
                if estimate > 0 and summary.daily_cost + estimate > daily_limit:
                    raise UsageBudgetExceeded("daily_budget_exhausted")
                if estimate > 0 and summary.monthly_cost + estimate > monthly_limit:
                    raise UsageBudgetExceeded("monthly_budget_exhausted")
                record = UsageRecord(
                    normalized_reservation, normalized_id, normalized_now, None,
                    estimate, None, 0, "reserved", Decimal("0"),
                )
                records.append(record)
                self._atomic_write(records)
                return record

    def reconcile(
        self,
        adapter_id: str,
        *,
        reservation_id: str,
        actual_cost: Decimal,
        request_count: int,
        status: str,
        units: Decimal,
        now: datetime,
    ) -> UsageRecord:
        normalized_id = _validate_identifier(adapter_id, "adapter_id")
        normalized_reservation = _validate_identifier(reservation_id, "reservation_id")
        actual = _validate_decimal(actual_cost, "actual_cost")
        if isinstance(request_count, bool) or not isinstance(request_count, int) or request_count < 0:
            raise UsageValidationError("request_count must be a non-negative integer")
        normalized_status = _validate_status(status)
        normalized_units = _validate_decimal(units, "units")
        normalized_now = _utc_datetime(now)
        with CACHE_IO_LOCK:
            with self._process_lock():
                records = self._load_unlocked(normalized_now)
                for index, existing in enumerate(records):
                    if existing.reservation_id != normalized_reservation:
                        continue
                    if existing.adapter_id != normalized_id:
                        raise UsageConflictError("reservation belongs to another adapter")
                    if existing.actual_cost is not None:
                        if (
                            existing.actual_cost == actual
                            and existing.request_count == request_count
                            and existing.status == normalized_status
                            and existing.units == normalized_units
                        ):
                            return existing
                        raise UsageConflictError("reservation was reconciled with different usage")
                    if normalized_now < existing.authorized_at:
                        raise UsageValidationError("reconciliation predates authorization")
                    reconciled = UsageRecord(
                        existing.reservation_id,
                        existing.adapter_id,
                        existing.authorized_at,
                        normalized_now,
                        existing.estimated_cost,
                        actual,
                        request_count,
                        normalized_status,
                        normalized_units,
                    )
                    records[index] = reconciled
                    self._atomic_write(records)
                    return reconciled
                raise UsageConflictError("reservation does not exist")


__all__ = [
    "UsageBudgetExceeded",
    "UsageConflictError",
    "UsageRecord",
    "UsageStore",
    "UsageStoreError",
    "UsageSummary",
    "UsageValidationError",
]
