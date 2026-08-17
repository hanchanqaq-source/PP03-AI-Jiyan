from __future__ import annotations

from datetime import date, datetime, timezone
import re
import time
from typing import Any

from fund_data.models import ProviderResult
from source_health.probe_errors import (
    ProbeEmptyPayloadError,
    ProbeSchemaError,
    classify_probe_error,
    retry_delay_seconds,
)


_CODE = re.compile(r"\d{6}")


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _valid_code(value: Any) -> bool:
    return bool(_CODE.fullmatch(str(value or "")))


def _valid_date(value: Any) -> bool:
    if not _present(value):
        return False
    try:
        date.fromisoformat(str(value)[:10])
        return True
    except ValueError:
        try:
            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return True
        except ValueError:
            return False


def _pct(present: int, total: int) -> float:
    return round(100.0 * present / total, 2) if total else 100.0


def _validate(capability: str, data: Any, probe_args: dict[str, Any]) -> tuple[float, int]:
    if capability == "search":
        if not isinstance(data, list):
            raise ProbeSchemaError()
        if not data:
            raise ProbeEmptyPayloadError()
        total = len(data) * 2
        present = sum(int(_valid_code(row.get("code"))) + int(_present(row.get("name"))) for row in data if isinstance(row, dict))
        if not any(isinstance(row, dict) and _valid_code(row.get("code")) and _present(row.get("name")) for row in data):
            raise ProbeSchemaError()
        return _pct(present, total), len(data)

    if capability == "profile":
        if not isinstance(data, dict):
            raise ProbeSchemaError()
        checks = (_valid_code(data.get("code")), _present(data.get("name")), _present(data.get("fund_type")))
        completeness = _pct(sum(map(int, checks)), len(checks))
        if not all(checks):
            raise ProbeSchemaError()
        return completeness, 1

    if capability == "nav_history":
        if not isinstance(data, dict):
            raise ProbeSchemaError()
        points = data.get("points")
        latest = data.get("latest")
        numeric_nav = isinstance(latest, dict) and isinstance(latest.get("unit_nav"), (int, float)) and latest["unit_nav"] > 0
        checks = (isinstance(points, list) and bool(points), numeric_nav, isinstance(latest, dict) and _valid_date(latest.get("nav_date")))
        completeness = _pct(sum(map(int, checks)), len(checks))
        if not all(checks):
            if isinstance(points, list) and not points:
                raise ProbeEmptyPayloadError()
            raise ProbeSchemaError()
        return completeness, len(points)

    if capability == "holdings":
        if not isinstance(data, dict):
            raise ProbeSchemaError()
        rows = data.get("holdings")
        base_checks = [_present(data.get("report_period")), isinstance(rows, list)]
        row_checks = []
        if isinstance(rows, list):
            for row in rows:
                row_checks.extend([
                    isinstance(row, dict) and _present(row.get("stock_code")),
                    isinstance(row, dict) and _present(row.get("stock_name")),
                    isinstance(row, dict) and _present(row.get("weight_pct")),
                ])
        checks = base_checks + row_checks
        completeness = _pct(sum(map(int, checks)), len(checks))
        if not all(checks):
            raise ProbeSchemaError()
        return completeness, len(rows)

    if capability == "industry_allocation":
        if not isinstance(data, dict):
            raise ProbeSchemaError()
        rows = data.get("industries")
        base_checks = [_valid_date(data.get("as_of_date")), isinstance(rows, list)]
        row_checks = []
        if isinstance(rows, list):
            for row in rows:
                row_checks.extend([
                    isinstance(row, dict) and _present(row.get("name")),
                    isinstance(row, dict) and _present(row.get("weight_pct")),
                ])
        checks = base_checks + row_checks
        completeness = _pct(sum(map(int, checks)), len(checks))
        if not all(checks):
            raise ProbeSchemaError()
        return completeness, len(rows)

    if capability == "stock_industry_classification":
        if not isinstance(data, dict):
            raise ProbeSchemaError()
        requested = data.get("requested_codes")
        rows = data.get("classifications")
        if not isinstance(requested, list) or not requested:
            raise ProbeSchemaError()
        if not isinstance(rows, dict):
            raise ProbeSchemaError()
        if not rows:
            raise ProbeEmptyPayloadError()
        checks = []
        for row in rows.values():
            checks.extend([
                isinstance(row, dict) and _present(row.get("source_name")),
                isinstance(row, dict) and _present(row.get("source_reference")),
            ])
        completeness = _pct(sum(map(int, checks)), len(checks))
        if not all(checks):
            raise ProbeSchemaError()
        return completeness, len(rows)

    if capability == "stock_snapshot":
        if not isinstance(data, dict):
            raise ProbeSchemaError()
        requested = {str(code) for code in probe_args.get("codes") or []}
        matched = [
            row for key, row in data.items()
            if isinstance(row, dict) and (str(key) in requested or str(row.get("stock_code") or "") in requested)
        ]
        if not requested or not matched:
            raise ProbeSchemaError()
        checks = []
        for row in matched:
            checks.extend([
                _valid_code(row.get("stock_code")),
                _present(row.get("stock_name")),
                _present(row.get("price")),
                _present(row.get("change_pct")),
            ])
        return _pct(sum(map(int, checks)), len(checks)), len(matched)

    raise ProbeSchemaError()


def _failure_result(
    provider: Any,
    capability: str,
    error: BaseException,
    *,
    started: float,
    completeness: float | None = None,
) -> dict[str, Any]:
    classified = classify_probe_error(error)
    return {
        "status": "failure",
        "data": None,
        "source_name": str(getattr(provider, "name", type(provider).__name__)),
        "source_reference": None,
        "capability": capability,
        "error_type": classified.error_type,
        "error_message_redacted": classified.message,
        "http_status": classified.http_status,
        "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
        "returned_items": 0,
        "data_as_of_date": None,
        "field_completeness_pct": completeness,
    }


def probe_provider_capability(
    provider: Any,
    capability: str,
    *,
    probe_args: dict[str, Any] | None = None,
    freshness_max_age_seconds: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    args = dict(probe_args or {})
    started = time.perf_counter()
    result: ProviderResult | None = None
    for attempt in range(2):
        try:
            result = provider.fetch(capability, **args)
            break
        except Exception as error:
            classified = classify_probe_error(error)
            if attempt == 0 and classified.retryable:
                time.sleep(retry_delay_seconds(error))
                continue
            return _failure_result(provider, capability, error, started=started)

    if not isinstance(result, ProviderResult):
        return _failure_result(provider, capability, ProbeSchemaError(), started=started)
    try:
        completeness, returned_items = _validate(capability, result.data, args)
    except (ProbeSchemaError, ProbeEmptyPayloadError) as error:
        try:
            completeness, _ = _validate_for_failure(capability, result.data, args)
        except Exception:
            completeness = 0.0
        failed = _failure_result(provider, capability, error, started=started, completeness=completeness)
        failed["data"] = result.data
        failed["source_name"] = result.source_name
        failed["source_reference"] = result.source_reference
        failed["data_as_of_date"] = result.as_of_date
        return failed

    status = "success" if completeness == 100.0 else "partial"
    error_type = "none"
    message = ""
    if freshness_max_age_seconds is not None and result.as_of_date and _valid_date(result.as_of_date):
        current = now or datetime.now(timezone.utc)
        as_of = datetime.fromisoformat(str(result.as_of_date)[:10]).replace(tzinfo=timezone.utc)
        if (current - as_of).total_seconds() > freshness_max_age_seconds:
            status, error_type, message = "partial", "stale_data", "数据日期超过能力新鲜度阈值"
    return {
        "status": status,
        "data": result.data,
        "source_name": result.source_name,
        "source_reference": result.source_reference,
        "capability": capability,
        "error_type": error_type,
        "error_message_redacted": message,
        "http_status": None,
        "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
        "returned_items": returned_items,
        "data_as_of_date": result.as_of_date,
        "field_completeness_pct": completeness,
    }


def _validate_for_failure(capability: str, data: Any, probe_args: dict[str, Any]) -> tuple[float, int]:
    """Return diagnostic completeness without weakening the contract validator."""
    if capability == "profile" and isinstance(data, dict):
        checks = (_valid_code(data.get("code")), _present(data.get("name")), _present(data.get("fund_type")))
        return _pct(sum(map(int, checks)), len(checks)), 1
    return 0.0, 0


probe_fund_provider = probe_provider_capability
