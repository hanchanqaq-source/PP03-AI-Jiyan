from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_REFERENCE = "https://data.nasdaq.com/"
_ENV_NAME = "NASDAQ_DATA_LINK_API_KEY"
_MAX_ROWS, _MAX_TEXT = 1_000, 4_096
_RESOURCE = re.compile(r"^[A-Z0-9_]{1,64}$")
_KNOWN_FREE = frozenset({("FRED", "DFF"), ("FRED", "GDP")})


def _now() -> datetime: return datetime.now(timezone.utc)


def _text(value: object, *, allow_blank: bool = False) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT or (not allow_blank and not value): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _number(value: object) -> Decimal:
    if type(value) not in {str, int, float, Decimal} or (type(value) is int and value.bit_length() > 333): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    raw = str(value)
    if len(raw) > 128: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    try: parsed = Decimal(raw)
    except (InvalidOperation, ValueError): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 100 or abs(parsed.as_tuple().exponent) > 100: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return parsed


class NasdaqDataLinkAdapter(BaseProvider):
    descriptor = AdapterDescriptor(
        "nasdaq-data-link", "Nasdaq Data Link", "nasdaq_data_link", "http_client", (SourceRole.MACRO_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK),
        ("macro_series",), BillingModel.FREEMIUM, "api_key", (_ENV_NAME,), False,
        "Nasdaq Data Link account and dataset license terms apply.",
        "Known public datasets are distinguished from Premium datasets; Premium requires explicit account entitlement.",
        "以 dataset 实际刷新时间为准", "以账户实际套餐、dataset 权限和配额为准", "套餐和 Premium 成本未知；无可信能力级权益时禁止请求",
        _REFERENCE, 140, CatalogStatus.UNCONFIGURED,
    )

    def __init__(self, *, http: Any, credentials: Any, budget_guard: Any | None = None,
                 cache_getter: Callable[[ProviderRequest], object | None] | None = None, fetched_at: Callable[[], datetime] = _now) -> None:
        self._http, self._credentials, self._budget_guard = http, credentials, budget_guard
        self._cache_getter, self._fetched_at = cache_getter, fetched_at

    def _credential(self) -> str | None:
        try: value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception: return None
        return value if type(value) is str and value.strip() else None

    @staticmethod
    def _request(request: ProviderRequest) -> tuple[str, str, int]:
        if type(request) is not ProviderRequest or request.capability_id != "macro_series" or type(request.parameters) is not dict or set(request.parameters) - {"database_code", "dataset_code", "limit", "max_age_days"}: raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        database, dataset, limit = request.parameters.get("database_code"), request.parameters.get("dataset_code"), request.parameters.get("limit", 100)
        max_age = request.parameters.get("max_age_days")
        if type(database) is not str or not _RESOURCE.fullmatch(database) or type(dataset) is not str or not _RESOURCE.fullmatch(dataset) or type(limit) is not int or not 1 <= limit <= _MAX_ROWS or (max_age is not None and (type(max_age) is not int or not 0 <= max_age <= 36_500)): raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return database, dataset, limit

    @staticmethod
    def _error(payload: dict[str, object]) -> None:
        if "quandl_error" not in payload: return
        error = payload["quandl_error"]
        if type(error) is not dict or set(error) - {"code", "message", "retry_after"}: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        code, message = _text(error.get("code")), _text(error.get("message"))
        if code.startswith("QEL"):
            retry = error.get("retry_after", 0)
            if type(retry) not in {int, float} or type(retry) is bool or not 0 <= retry <= 60: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            raise ProviderRateLimited(retry_after_seconds=float(retry), reference=_REFERENCE)
        if code.startswith("QEA"): raise ProviderUnavailable("authentication", reference=_REFERENCE)
        if code.startswith("QEP") or "subscription" in message.lower(): raise ProviderUnavailable("plan_unavailable", reference=_REFERENCE)
        raise ProviderUnavailable("provider_error", reference=_REFERENCE)

    def _parse(self, payload: object, request: ProviderRequest, *, now: datetime, cached: bool) -> tuple[ProviderValue, ...]:
        if type(payload) is not dict: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        self._error(payload)
        dataset = payload.get("dataset")
        if type(dataset) is not dict: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        required = {"id", "dataset_code", "database_code", "name", "description", "refreshed_at", "newest_available_date", "oldest_available_date", "column_names", "frequency", "type", "premium", "data", "database_name"}
        if set(dataset) != required or type(dataset["premium"]) is not bool: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        requested_resource = (request.parameters.get("database_code"), request.parameters.get("dataset_code"))
        access_observation = "premium_dataset" if dataset["premium"] else ("known_free_dataset" if requested_resource in _KNOWN_FREE else "nonpremium_dataset")
        database, code, name, database_name = (_text(dataset[field]) for field in ("database_code", "dataset_code", "name", "database_name"))
        newest, frequency, columns, data = _text(dataset["newest_available_date"]), _text(dataset["frequency"]), dataset["column_names"], dataset["data"]
        if database != request.parameters.get("database_code") or code != request.parameters.get("dataset_code") or type(columns) is not list or type(data) is not list or columns != ["Date", "Value"]: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not data: raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(data) > _MAX_ROWS or any(type(column) is not str or not column or len(column) > _MAX_TEXT for column in columns): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        try: newest_date = date.fromisoformat(newest)
        except ValueError: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
        if newest_date > now.date(): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        rows = []
        max_age = request.parameters.get("max_age_days")
        for item in data:
            if type(item) is not list or len(item) != 2: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            raw_date = _text(item[0])
            try: as_of = date.fromisoformat(raw_date)
            except ValueError: raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from None
            if as_of > now.date(): raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            metadata = {"database_code": database, "dataset_code": code, "database_name": database_name, "dataset_name": name, "newest_available_date": newest, "dataset_access_observation": access_observation, "source_reference": _REFERENCE}
            if cached: metadata["cache_status"] = "fallback"
            stale = type(max_age) is int and (now.date() - as_of).days > max_age
            rows.append(ProviderValue(_number(item[1]), "nasdaq_data_link", "nasdaq-data-link", request.capability_id, as_of, now, "cached" if cached else ("stale" if stale else "upstream_reported"), "Nasdaq Data Link dataset terms apply", 140, None, "unknown", frequency, metadata))
        return tuple(rows)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        credential = self._credential()
        if credential is None: raise ProviderUnavailable("unconfigured", reference=_REFERENCE)
        del credential, request
        raise ProviderUnavailable("unsupported_credential_transport", reference=_REFERENCE)

    def probe(self, capability_id: str, *, parameters: Mapping[str, object] | None = None) -> Mapping[str, object]:
        credential = self._credential()
        if credential is None: return {"status": "unconfigured", "connected": False, "health_failure": False}
        del credential, parameters
        return {"status": "unsupported_credential_transport", "connected": False, "health_failure": False, "capability_id": capability_id}


__all__ = ["NasdaqDataLinkAdapter"]
