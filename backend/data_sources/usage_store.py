"""Exact, atomic cost reservations and reconciled provider usage."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
_RECONCILIATION_VERSION = 1
_RECONCILIATION_SERVICE = "pp03-data-sources"
_RECONCILIATION_OPERATION = "provider_validation"
_RECONCILIATION_GUARD_SCOPE = "server_authorization_v1"
_VALIDATION_SETTLEMENT_STATUSES = frozenset({
    "validation_success",
    "validation_partial",
    "validation_failure",
})
_VALIDATION_IN_FLIGHT_WINDOW = timedelta(minutes=5)
_MAX_CLOCK_SKEW = timedelta(seconds=5)
_DEFAULT_MAX_LEDGER_BYTES = 4 * 1024 * 1024
_MAX_LEDGER_BYTES = 64 * 1024 * 1024
_DEFAULT_MAX_RECONCILIATION_BYTES = 1024 * 1024
_MAX_RECONCILIATION_BYTES = 4 * 1024 * 1024
_MAX_RECONCILIATION_INTENTS = 4_096
_MAX_RECOVERY_ATTEMPTS = 128
_MAX_DECIMAL_PLACES = 8
_MAX_DECIMAL_DIGITS = 28
_MAX_DECIMAL_ABSOLUTE = Decimal("1000000000000")
_MAX_DECIMAL_POSITIVE_EXPONENT = 12
_MAX_DECIMAL_TEXT_LENGTH = 64
_MAX_REQUEST_COUNT = 1_000_000_000
_MAX_RECORDS = 100_000
_MAX_TIMESTAMP_LENGTH = 32
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_STATUS = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAFE_MONTH = re.compile(r"^\d{4}-\d{2}$")
_CREDENTIAL_TERMS = (
    "credential", "secret", "token", "password", "api_key", "apikey",
    "access_key", "private_key", "authorization", "bearer", "cookie",
)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_RECORD_FIELDS = {
    "reservation_id", "adapter_id", "authorized_at", "recorded_at",
    "estimated_cost", "actual_cost", "request_count", "status", "units",
}
_RECONCILIATION_FIELDS = {
    "operation", "guard_scope", "adapter_id", "reservation_id",
    "authorized_at", "recorded_at", "estimated_cost", "actual_cost",
    "request_count", "status", "units",
}


def _validate_decimal(value: object, field: str) -> Decimal:
    if type(value) is not Decimal:
        raise UsageValidationError(f"{field} must be a Decimal")
    if not value.is_finite():
        raise UsageValidationError(f"{field} must be a finite non-negative Decimal")
    sign, digits, exponent = value.as_tuple()
    if (
        type(exponent) is not int
        or exponent < -_MAX_DECIMAL_PLACES
        or exponent > _MAX_DECIMAL_POSITIVE_EXPONENT
        or len(digits) > _MAX_DECIMAL_DIGITS
    ):
        raise UsageValidationError(f"{field} precision is invalid")
    if value < 0 or value > _MAX_DECIMAL_ABSOLUTE:
        raise UsageValidationError(f"{field} must be a finite non-negative Decimal")
    return Decimal((sign, digits, exponent))


def _decimal_text(value: Decimal) -> str:
    return format(_validate_decimal(value, "decimal value"), "f")


def _validate_identifier(value: object, field: str) -> str:
    if type(value) is not str or not _SAFE_IDENTIFIER.fullmatch(value):
        raise UsageValidationError(f"{field} is invalid")
    if field == "reservation_id" and any(marker in value.lower() for marker in _CREDENTIAL_TERMS):
        raise UsageValidationError(f"{field} is invalid")
    return value.encode("utf-8").decode("utf-8")


def _validate_status(value: object, *, allow_reserved: bool = False) -> str:
    if type(value) is not str or not _SAFE_STATUS.fullmatch(value):
        raise UsageValidationError("status is invalid")
    if any(marker in value.lower() for marker in _CREDENTIAL_TERMS) or (
        value == "reserved" and not allow_reserved
    ):
        raise UsageValidationError("status is invalid")
    return value.encode("utf-8").decode("utf-8")


def _validate_request_limit(value: object, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0 or value > _MAX_REQUEST_COUNT:
        raise UsageValidationError(f"{field} must be a bounded non-negative integer or null")
    return value


def _utc_datetime(value: object, field: str = "now") -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise UsageValidationError(f"{field} must be a timezone-aware datetime")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise UsageValidationError(f"{field} is invalid") from None


def _timestamp_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, field: str) -> datetime:
    if (
        type(value) is not str
        or len(value) > _MAX_TIMESTAMP_LENGTH
        or not value.endswith("Z")
    ):
        raise UsageValidationError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise UsageValidationError(f"{field} is invalid") from None
    if _timestamp_text(parsed) != value:
        raise UsageValidationError(f"{field} is not canonical")
    return parsed


def _parse_decimal_text(value: object, field: str) -> Decimal:
    if type(value) is not str or not value or len(value) > _MAX_DECIMAL_TEXT_LENGTH:
        raise UsageValidationError(f"{field} must be a decimal string")
    try:
        parsed = Decimal(value)
    except Exception:
        raise UsageValidationError(f"{field} must be a decimal string") from None
    _validate_decimal(parsed, field)
    if _decimal_text(parsed) != value:
        raise UsageValidationError(f"{field} is not canonical")
    return parsed


def _json_object(pairs: list[tuple[object, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in document:
            raise ValueError("invalid JSON object")
        document[key] = value
    return document


def _json_integer(raw: str) -> int:
    if len(raw) > 11:
        raise ValueError("JSON integer is too large")
    value = int(raw)
    if value < -_MAX_REQUEST_COUNT or value > _MAX_REQUEST_COUNT:
        raise ValueError("JSON integer is too large")
    return value


def _invalid_json_number(_raw: str) -> float:
    raise ValueError("non-integer JSON numbers are not allowed")


def _validate_validation_settlement(status: object, units: object) -> tuple[str, Decimal]:
    normalized_status = _validate_status(status)
    normalized_units = _validate_decimal(units, "units")
    if (
        normalized_status not in _VALIDATION_SETTLEMENT_STATUSES
        or normalized_units != normalized_units.to_integral_value()
        or normalized_units > _MAX_REQUEST_COUNT
        or (
            normalized_status == "validation_failure"
            and normalized_units != 0
        )
    ):
        raise UsageValidationError("validation reconciliation facts are invalid")
    return normalized_status, normalized_units


@dataclass(frozen=True, slots=True)
class _ReconciliationIntent:
    operation: str
    guard_scope: str
    adapter_id: str
    reservation_id: str
    authorized_at: datetime
    recorded_at: datetime
    estimated_cost: Decimal
    actual_cost: Decimal
    request_count: int
    status: str
    units: Decimal

    def __post_init__(self) -> None:
        if type(self.operation) is not str or self.operation != _RECONCILIATION_OPERATION:
            raise UsageValidationError("reconciliation operation is invalid")
        if type(self.guard_scope) is not str or self.guard_scope != _RECONCILIATION_GUARD_SCOPE:
            raise UsageValidationError("reconciliation guard scope is invalid")
        object.__setattr__(self, "adapter_id", _validate_identifier(self.adapter_id, "adapter_id"))
        object.__setattr__(
            self,
            "reservation_id",
            _validate_identifier(self.reservation_id, "reservation_id"),
        )
        authorized_at = _utc_datetime(self.authorized_at, "authorized_at")
        recorded_at = _utc_datetime(self.recorded_at, "recorded_at")
        if recorded_at < authorized_at:
            raise UsageValidationError("reconciliation timestamp is invalid")
        object.__setattr__(self, "authorized_at", authorized_at)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(
            self, "estimated_cost", _validate_decimal(self.estimated_cost, "estimated_cost")
        )
        object.__setattr__(self, "actual_cost", _validate_decimal(self.actual_cost, "actual_cost"))
        if (
            type(self.request_count) is not int
            or self.request_count < 0
            or self.request_count > _MAX_REQUEST_COUNT
        ):
            raise UsageValidationError("request_count must be a non-negative integer")
        normalized_status, normalized_units = _validate_validation_settlement(
            self.status, self.units,
        )
        object.__setattr__(self, "status", normalized_status)
        object.__setattr__(self, "units", normalized_units)
        if (
            self.estimated_cost != 0
            or self.actual_cost != 0
            or self.request_count != 1
        ):
            raise UsageValidationError("validation reconciliation facts are invalid")

    @classmethod
    def from_dict(cls, value: object) -> "_ReconciliationIntent":
        if type(value) is not dict:
            raise UsageValidationError("reconciliation intent schema is invalid")
        keys = tuple(value.keys())
        if any(type(key) is not str for key in keys) or set(keys) != _RECONCILIATION_FIELDS:
            raise UsageValidationError("reconciliation intent schema is invalid")
        return cls(
            value["operation"],
            value["guard_scope"],
            _validate_identifier(value["adapter_id"], "adapter_id"),
            _validate_identifier(value["reservation_id"], "reservation_id"),
            _parse_timestamp(value["authorized_at"], "authorized_at"),
            _parse_timestamp(value["recorded_at"], "recorded_at"),
            _parse_decimal_text(value["estimated_cost"], "estimated_cost"),
            _parse_decimal_text(value["actual_cost"], "actual_cost"),
            value["request_count"],
            _validate_status(value["status"]),
            _parse_decimal_text(value["units"], "units"),
        )


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
        object.__setattr__(self, "reservation_id", _validate_identifier(self.reservation_id, "reservation_id"))
        object.__setattr__(self, "adapter_id", _validate_identifier(self.adapter_id, "adapter_id"))
        authorized_at = _utc_datetime(self.authorized_at, "authorized_at")
        recorded_at = (
            None if self.recorded_at is None else _utc_datetime(self.recorded_at, "recorded_at")
        )
        _validate_decimal(self.estimated_cost, "estimated_cost")
        if self.actual_cost is not None:
            _validate_decimal(self.actual_cost, "actual_cost")
        if (
            type(self.request_count) is not int
            or self.request_count < 0
            or self.request_count > _MAX_REQUEST_COUNT
        ):
            raise UsageValidationError("request_count is invalid")
        _validate_decimal(self.units, "units")
        if self.actual_cost is None:
            if recorded_at is None:
                if (
                    self.request_count != 0
                    or type(self.status) is not str
                    or self.status != "reserved"
                    or self.units != 0
                ):
                    raise UsageValidationError("open reservation state is invalid")
                object.__setattr__(self, "status", "reserved")
            else:
                if self.estimated_cost != 0 or self.request_count != 1:
                    raise UsageValidationError("staged validation state is invalid")
                normalized_status, normalized_units = _validate_validation_settlement(
                    self.status, self.units,
                )
                object.__setattr__(self, "status", normalized_status)
                object.__setattr__(self, "units", normalized_units)
                if recorded_at < authorized_at:
                    raise UsageValidationError("staged validation timestamp is invalid")
        else:
            object.__setattr__(self, "status", _validate_status(self.status))
            if recorded_at is None or recorded_at < authorized_at:
                raise UsageValidationError("reconciled timestamp is invalid")
        object.__setattr__(self, "authorized_at", authorized_at)
        object.__setattr__(self, "recorded_at", recorded_at)

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
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
        if type(value) is not dict:
            raise UsageValidationError("usage record schema is invalid")
        keys = tuple(value.keys())
        if any(type(key) is not str for key in keys) or set(keys) != _RECORD_FIELDS:
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
        if (
            type(request_count) is not int
            or request_count < 0
            or request_count > _MAX_REQUEST_COUNT
        ):
            raise UsageValidationError("request_count is invalid")
        status = _validate_status(value["status"], allow_reserved=True)
        units = _parse_decimal_text(value["units"], "units")
        if actual_cost is None:
            if recorded_at is None:
                if request_count != 0 or status != "reserved" or units != 0:
                    raise UsageValidationError("open reservation state is invalid")
            else:
                if estimated_cost != 0 or request_count != 1:
                    raise UsageValidationError("staged validation state is invalid")
                _validate_validation_settlement(status, units)
                if recorded_at < authorized_at:
                    raise UsageValidationError("staged validation timestamp is invalid")
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _validate_identifier(self.adapter_id, "adapter_id"))
        if type(self.day) is not str or not _SAFE_DAY.fullmatch(self.day):
            raise UsageValidationError("summary day is invalid")
        if type(self.month) is not str or not _SAFE_MONTH.fullmatch(self.month):
            raise UsageValidationError("summary month is invalid")
        object.__setattr__(self, "day", self.day.encode("utf-8").decode("utf-8"))
        object.__setattr__(self, "month", self.month.encode("utf-8").decode("utf-8"))
        for field in ("daily_cost", "monthly_cost", "daily_units", "monthly_units"):
            object.__setattr__(self, field, _validate_decimal(getattr(self, field), field))
        for field in ("daily_request_count", "monthly_request_count"):
            value = getattr(self, field)
            if (
                type(value) is not int
                or value < 0
                or value > _MAX_REQUEST_COUNT * _MAX_RECORDS
            ):
                raise UsageValidationError(f"{field} is invalid")

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
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
        max_reconciliation_bytes: int = _DEFAULT_MAX_RECONCILIATION_BYTES,
    ) -> None:
        if root is None:
            configured = os.environ.get("VR_DATA_DIR")
            if configured is not None and type(configured) is not str:
                raise UsageValidationError("VR_DATA_DIR is invalid")
            if configured is not None and configured.strip():
                root = Path(configured)
            else:
                user_profile = os.environ.get("USERPROFILE")
                if user_profile is not None and type(user_profile) is not str:
                    raise UsageValidationError("USERPROFILE is invalid")
                profile_root = (
                    Path(user_profile)
                    if user_profile is not None and user_profile.strip()
                    else Path.home()
                )
                root = profile_root / ".vibe-research"
        if type(lock_timeout_seconds) not in (int, float):
            raise UsageValidationError("lock timeout is invalid")
        try:
            timeout = float(lock_timeout_seconds)
        except (OverflowError, TypeError, ValueError):
            raise UsageValidationError("lock timeout is invalid") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise UsageValidationError("lock timeout is invalid")
        if (
            type(max_ledger_bytes) is not int
            or max_ledger_bytes <= 0
            or max_ledger_bytes > _MAX_LEDGER_BYTES
        ):
            raise UsageValidationError("max ledger bytes is invalid")
        if (
            type(max_reconciliation_bytes) is not int
            or max_reconciliation_bytes <= 0
            or max_reconciliation_bytes > _MAX_RECONCILIATION_BYTES
        ):
            raise UsageValidationError("max reconciliation bytes is invalid")
        self._data_root = Path(root)
        self.root = self._data_root / "data-sources" / "v1"
        self.path = self.root / "usage.json"
        self.reconciliation_path = self.root / "usage-reconciliation.json"
        self._lock_path = self.root / ".usage.lock"
        self._lock_timeout_seconds = timeout
        self._max_ledger_bytes = max_ledger_bytes
        self._max_reconciliation_bytes = max_reconciliation_bytes

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
        self._assert_no_reparse_components(self.reconciliation_path)
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

    def _sync_parent_directory(self) -> None:
        """Persist the parent directory where supported by the host OS."""
        if os.name == "nt":
            return
        descriptor: int | None = None
        try:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(self.root, flags)
            os.fsync(descriptor)
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _atomic_write(self, records: Iterable[UsageRecord]) -> None:
        record_list = list(records)
        if len(record_list) > _MAX_RECORDS:
            raise UsageStoreError("usage ledger contains too many records")
        document = {"version": _LEDGER_VERSION, "records": [record.to_dict() for record in record_list]}
        descriptor: int | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=".usage.", suffix=".tmp", dir=self.root,
            )
            temporary = Path(raw_path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                descriptor = None
                json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if temporary.stat().st_size > self._max_ledger_bytes:
                raise UsageStoreError("usage ledger is too large")
            os.replace(temporary, self.path)
            self._sync_parent_directory()
        except UsageStoreError:
            raise
        except OSError:
            raise UsageStoreError("usage ledger could not be written") from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _load_reconciliations_unlocked(
        self,
        reference_now: datetime,
        *,
        trusted_adapter_ids: frozenset[str] | None = None,
    ) -> list[_ReconciliationIntent]:
        try:
            with self.reconciliation_path.open("rb") as handle:
                raw = handle.read(self._max_reconciliation_bytes + 1)
        except FileNotFoundError:
            return []
        except OSError:
            raise UsageStoreError("usage reconciliation intents are unavailable") from None
        if len(raw) > self._max_reconciliation_bytes:
            raise UsageStoreError("usage reconciliation intents are too large")
        try:
            document = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_json_object,
                parse_int=_json_integer,
                parse_float=_invalid_json_number,
                parse_constant=_invalid_json_number,
            )
            if (
                type(document) is not dict
                or set(document) != {"version", "service", "intents"}
                or type(document.get("version")) is not int
                or document.get("version") != _RECONCILIATION_VERSION
                or type(document.get("service")) is not str
                or document.get("service") != _RECONCILIATION_SERVICE
                or type(document.get("intents")) is not list
                or len(document["intents"]) > _MAX_RECONCILIATION_INTENTS
            ):
                raise UsageValidationError("usage reconciliation schema is invalid")
            intents = [
                _ReconciliationIntent.from_dict(row) for row in document["intents"]
            ]
            seen: set[str] = set()
            for intent in intents:
                if intent.reservation_id in seen:
                    raise UsageValidationError(
                        "usage reconciliation contains duplicate reservations"
                    )
                seen.add(intent.reservation_id)
                if (
                    intent.authorized_at > reference_now
                    or intent.recorded_at > reference_now
                ):
                    raise UsageValidationError("usage reconciliation timestamp is in the future")
                if (
                    trusted_adapter_ids is not None
                    and intent.adapter_id not in trusted_adapter_ids
                ):
                    raise UsageValidationError("usage reconciliation adapter is not trusted")
            return intents
        except UsageValidationError:
            raise UsageStoreError("usage reconciliation intents are corrupt") from None
        except (
            OverflowError,
            RecursionError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            raise UsageStoreError("usage reconciliation intents are corrupt") from None

    def _load_unlocked(self, reference_now: datetime) -> list[UsageRecord]:
        if not self.path.exists():
            return []
        try:
            if self.path.stat().st_size > self._max_ledger_bytes:
                raise UsageStoreError("usage ledger is too large")
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except UsageStoreError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise UsageStoreError("usage ledger is corrupt") from None
        if (
            type(document) is not dict
            or set(document) != {"version", "records"}
            or type(document.get("version")) is not int
            or document.get("version") != _LEDGER_VERSION
            or type(document.get("records")) is not list
        ):
            raise UsageStoreError("usage ledger schema is invalid")
        if len(document["records"]) > _MAX_RECORDS:
            raise UsageStoreError("usage ledger contains too many records")
        try:
            records = [UsageRecord.from_dict(row) for row in document["records"]]
        except UsageValidationError as exc:
            raise UsageStoreError("usage ledger record is invalid") from exc
        seen: set[str] = set()
        for record in records:
            if record.reservation_id in seen:
                raise UsageStoreError("usage ledger contains duplicate reservations")
            seen.add(record.reservation_id)
            if (
                record.authorized_at > reference_now
                and record.authorized_at - reference_now > _MAX_CLOCK_SKEW
            ):
                raise UsageValidationError("usage ledger contains a future authorization")
            if (
                record.recorded_at is not None
                and record.recorded_at > reference_now
                and record.recorded_at - reference_now > _MAX_CLOCK_SKEW
            ):
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

    @staticmethod
    def _request_counts_from(
        records: Iterable[UsageRecord], adapter_id: str, now: datetime,
    ) -> tuple[int, int]:
        day = now.date().isoformat()
        month = now.strftime("%Y-%m")
        daily_requests = 0
        monthly_requests = 0
        for record in records:
            if record.adapter_id != adapter_id:
                continue
            # An open reservation represents one in-flight request. Reconciled
            # zero-request barriers (for example cost_unknown) remain true zero.
            reserved_requests = 1 if record.actual_cost is None else record.request_count
            if record.authorized_at.strftime("%Y-%m") == month:
                monthly_requests += reserved_requests
            if record.authorized_at.date().isoformat() == day:
                daily_requests += reserved_requests
        return daily_requests, monthly_requests

    @classmethod
    def _check_authorization_limits(
        cls,
        records: Iterable[UsageRecord],
        adapter_id: str,
        *,
        estimated_cost: Decimal,
        daily_budget: Decimal,
        monthly_budget: Decimal,
        daily_request_limit: int | None,
        monthly_request_limit: int | None,
        now: datetime,
    ) -> None:
        summary = cls._summary_from(records, adapter_id, now)
        if estimated_cost > 0 and summary.daily_cost + estimated_cost > daily_budget:
            raise UsageBudgetExceeded("daily_budget_exhausted")
        if estimated_cost > 0 and summary.monthly_cost + estimated_cost > monthly_budget:
            raise UsageBudgetExceeded("monthly_budget_exhausted")
        daily_requests, monthly_requests = cls._request_counts_from(records, adapter_id, now)
        if daily_request_limit is not None and daily_requests + 1 > daily_request_limit:
            raise UsageBudgetExceeded("daily_request_limit_exhausted")
        if monthly_request_limit is not None and monthly_requests + 1 > monthly_request_limit:
            raise UsageBudgetExceeded("monthly_request_limit_exhausted")

    def preflight(
        self,
        adapter_id: str,
        *,
        estimated_cost: Decimal,
        daily_budget: Decimal,
        monthly_budget: Decimal,
        daily_request_limit: int | None,
        monthly_request_limit: int | None,
        now: datetime,
    ) -> None:
        normalized_id = _validate_identifier(adapter_id, "adapter_id")
        estimate = _validate_decimal(estimated_cost, "estimated_cost")
        daily_budget_value = _validate_decimal(daily_budget, "daily_budget")
        monthly_budget_value = _validate_decimal(monthly_budget, "monthly_budget")
        daily_requests = _validate_request_limit(daily_request_limit, "daily_request_limit")
        monthly_requests = _validate_request_limit(monthly_request_limit, "monthly_request_limit")
        normalized_now = _utc_datetime(now)
        with CACHE_IO_LOCK:
            with self._process_lock():
                records = self._load_unlocked(normalized_now)
                self._check_authorization_limits(
                    records,
                    normalized_id,
                    estimated_cost=estimate,
                    daily_budget=daily_budget_value,
                    monthly_budget=monthly_budget_value,
                    daily_request_limit=daily_requests,
                    monthly_request_limit=monthly_requests,
                    now=normalized_now,
                )

    def reserve(
        self,
        adapter_id: str,
        *,
        estimated_cost: Decimal,
        daily_budget: Decimal,
        monthly_budget: Decimal,
        now: datetime,
        reservation_id: str | None = None,
        daily_request_limit: int | None = None,
        monthly_request_limit: int | None = None,
    ) -> UsageRecord:
        normalized_id = _validate_identifier(adapter_id, "adapter_id")
        estimate = _validate_decimal(estimated_cost, "estimated_cost")
        daily_limit = _validate_decimal(daily_budget, "daily_budget")
        monthly_limit = _validate_decimal(monthly_budget, "monthly_budget")
        daily_requests = _validate_request_limit(daily_request_limit, "daily_request_limit")
        monthly_requests = _validate_request_limit(monthly_request_limit, "monthly_request_limit")
        normalized_now = _utc_datetime(now)
        normalized_reservation = _validate_identifier(
            uuid.uuid4().hex if reservation_id is None else reservation_id,
            "reservation_id",
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
                    ):
                        return record
                    raise UsageConflictError("reservation identifier is already in use")
                self._check_authorization_limits(
                    records,
                    normalized_id,
                    estimated_cost=estimate,
                    daily_budget=daily_limit,
                    monthly_budget=monthly_limit,
                    daily_request_limit=daily_requests,
                    monthly_request_limit=monthly_requests,
                    now=normalized_now,
                )
                record = UsageRecord(
                    normalized_reservation, normalized_id, normalized_now, None,
                    estimate, None, 0, "reserved", Decimal("0"),
                )
                records.append(record)
                self._atomic_write(records)
                return record

    @staticmethod
    def _reconcile_records(
        records: list[UsageRecord],
        *,
        adapter_id: str,
        reservation_id: str,
        actual_cost: Decimal,
        request_count: int,
        status: str,
        units: Decimal,
        recorded_at: datetime,
    ) -> tuple[UsageRecord, bool]:
        for index, existing in enumerate(records):
            if existing.reservation_id != reservation_id:
                continue
            if existing.adapter_id != adapter_id:
                raise UsageConflictError("reservation belongs to another adapter")
            if existing.actual_cost is not None:
                if (
                    existing.actual_cost == actual_cost
                    and existing.request_count == request_count
                    and existing.status == status
                    and existing.units == units
                ):
                    return existing, False
                raise UsageConflictError("reservation was reconciled with different usage")
            if existing.recorded_at is not None and (
                existing.recorded_at != recorded_at
                or existing.request_count != request_count
                or existing.status != status
                or existing.units != units
            ):
                raise UsageConflictError(
                    "staged reservation has different reconciliation facts"
                )
            if recorded_at < existing.authorized_at:
                raise UsageValidationError("reconciliation predates authorization")
            reconciled = UsageRecord(
                existing.reservation_id,
                existing.adapter_id,
                existing.authorized_at,
                recorded_at,
                existing.estimated_cost,
                actual_cost,
                request_count,
                status,
                units,
            )
            records[index] = reconciled
            return reconciled, True
        raise UsageConflictError("reservation does not exist")

    def _stage_validation_reconciliation(
        self,
        adapter_id: str,
        *,
        reservation_id: str,
        status: str,
        units: Decimal,
        recorded_at: datetime,
    ) -> UsageRecord:
        """Stage one Service-owned post-probe settlement in the trusted ledger."""
        normalized_id = _validate_identifier(adapter_id, "adapter_id")
        normalized_reservation = _validate_identifier(reservation_id, "reservation_id")
        normalized_status, normalized_units = _validate_validation_settlement(
            status, units,
        )
        normalized_recorded_at = _utc_datetime(recorded_at, "recorded_at")
        with CACHE_IO_LOCK:
            with self._process_lock():
                records = self._load_unlocked(normalized_recorded_at)
                for index, reservation in enumerate(records):
                    if reservation.reservation_id != normalized_reservation:
                        continue
                    if reservation.adapter_id != normalized_id:
                        raise UsageConflictError(
                            "reservation belongs to another adapter"
                        )
                    if reservation.actual_cost is not None:
                        if (
                            reservation.actual_cost == 0
                            and reservation.recorded_at == normalized_recorded_at
                            and reservation.request_count == 1
                            and reservation.status == normalized_status
                            and reservation.units == normalized_units
                        ):
                            return reservation
                        raise UsageConflictError(
                            "reservation was reconciled with different usage"
                        )
                    if reservation.recorded_at is not None:
                        if (
                            reservation.recorded_at == normalized_recorded_at
                            and reservation.request_count == 1
                            and reservation.status == normalized_status
                            and reservation.units == normalized_units
                        ):
                            return reservation
                        raise UsageConflictError(
                            "reservation has a different staged settlement"
                        )
                    staged = UsageRecord(
                        reservation.reservation_id,
                        reservation.adapter_id,
                        reservation.authorized_at,
                        normalized_recorded_at,
                        reservation.estimated_cost,
                        None,
                        1,
                        normalized_status,
                        normalized_units,
                    )
                    records[index] = staged
                    self._atomic_write(records)
                    return staged
                raise UsageConflictError("reservation does not exist")

    def recover_reconciliations(
        self,
        *,
        trusted_adapter_ids: frozenset[str],
        now: datetime,
    ) -> dict[str, object]:
        """Boundedly settle trusted staged intents without provider/config access."""
        if (
            type(trusted_adapter_ids) is not frozenset
            or len(trusted_adapter_ids) > _MAX_RECORDS
        ):
            raise UsageValidationError("trusted reconciliation adapters are invalid")
        normalized_trusted = frozenset(
            _validate_identifier(adapter_id, "adapter_id")
            for adapter_id in trusted_adapter_ids
        )
        normalized_now = _utc_datetime(now)
        with CACHE_IO_LOCK:
            with self._process_lock():
                # Round-4 external intent files are untrusted inputs. Even an
                # exact-schema document cannot prove that a probe ran, because
                # its constants and reservation facts are public. Preserve it
                # byte-for-byte and fail closed instead of granting authority.
                intents = self._load_reconciliations_unlocked(
                    normalized_now,
                    trusted_adapter_ids=normalized_trusted,
                )
                records = self._load_unlocked(normalized_now)
                open_records = [
                    record
                    for record in records
                    if record.actual_cost is None and record.recorded_at is None
                ]
                staged_records = [
                    record
                    for record in records
                    if record.actual_cost is None and record.recorded_at is not None
                ]
                if intents:
                    by_reservation = {
                        record.reservation_id: record for record in records
                    }
                    for intent in intents:
                        record = by_reservation.get(intent.reservation_id)
                        if (
                            record is None
                            or record.actual_cost is None
                            or record.adapter_id != intent.adapter_id
                            or record.authorized_at != intent.authorized_at
                            or record.recorded_at != intent.recorded_at
                            or record.estimated_cost != intent.estimated_cost
                            or record.actual_cost != intent.actual_cost
                            or record.request_count != 1
                            or record.status != intent.status
                            or record.units != intent.units
                        ):
                            return {
                                "blocked": True,
                                "attempted": 0,
                                "recovered": 0,
                                "retained": (
                                    len(intents)
                                    + len(open_records)
                                    + len(staged_records)
                                ),
                            }
                    # A Round-4 post-write-ambiguous artifact that exactly
                    # repeats an already settled trusted ledger record is inert.
                    # Preserve it; never use it to change ledger state.

                stale_open = [
                    record
                    for record in open_records
                    if normalized_now - record.authorized_at > _VALIDATION_IN_FLIGHT_WINDOW
                ]
                batch = staged_records[:_MAX_RECOVERY_ATTEMPTS]
                changed = False
                try:
                    for staged in batch:
                        _record, record_changed = self._reconcile_records(
                            records,
                            adapter_id=staged.adapter_id,
                            reservation_id=staged.reservation_id,
                            actual_cost=Decimal("0"),
                            request_count=1,
                            status=staged.status,
                            units=staged.units,
                            recorded_at=staged.recorded_at,
                        )
                        changed = changed or record_changed
                    if changed:
                        self._atomic_write(records)
                except UsageStoreError:
                    return {
                        "blocked": True,
                        "attempted": len(batch),
                        "recovered": 0,
                        "retained": len(open_records) + len(staged_records),
                    }
                retained = (
                    len(open_records)
                    + len(staged_records)
                    - len(batch)
                )
                return {
                    "blocked": bool(stale_open),
                    "attempted": len(batch),
                    "recovered": len(batch),
                    "retained": retained,
                }

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
        recorded_at: datetime | None = None,
    ) -> UsageRecord:
        normalized_id = _validate_identifier(adapter_id, "adapter_id")
        normalized_reservation = _validate_identifier(reservation_id, "reservation_id")
        actual = _validate_decimal(actual_cost, "actual_cost")
        if (
            type(request_count) is not int
            or request_count < 0
            or request_count > _MAX_REQUEST_COUNT
        ):
            raise UsageValidationError("request_count must be a non-negative integer")
        normalized_status = _validate_status(status)
        normalized_units = _validate_decimal(units, "units")
        normalized_now = _utc_datetime(now)
        normalized_recorded_at = (
            normalized_now
            if recorded_at is None
            else _utc_datetime(recorded_at, "recorded_at")
        )
        if normalized_recorded_at > normalized_now:
            raise UsageValidationError("reconciliation timestamp is in the future")
        with CACHE_IO_LOCK:
            with self._process_lock():
                records = self._load_unlocked(normalized_now)
                reconciled, changed = self._reconcile_records(
                    records,
                    adapter_id=normalized_id,
                    reservation_id=normalized_reservation,
                    actual_cost=actual,
                    request_count=request_count,
                    status=normalized_status,
                    units=normalized_units,
                    recorded_at=normalized_recorded_at,
                )
                if changed:
                    self._atomic_write(records)
                return reconciled


__all__ = [
    "UsageBudgetExceeded",
    "UsageConflictError",
    "UsageRecord",
    "UsageStore",
    "UsageStoreError",
    "UsageSummary",
    "UsageValidationError",
]
