from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import math
import re
import threading

from data_sources.budgets import BudgetDecision
from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_REFERENCE = "https://api.tushare.pro/"
_ENV_NAME = "TUSHARE_TOKEN"
_MAX_ROWS = 1_000
_MAX_FIELDS = 64
_MAX_TEXT = 4_096
_MAX_NUMBER_TEXT = 128
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_PARAMETER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_CACHEABLE = {"timeout", "tls", "dns", "connection", "server_error", "rate_limited"}
# This is an authorization sentinel, not a provider price. It classifies an
# unqualified plan/points request as cost-bearing for Free-only enforcement and
# is reconciled to zero without transport when no trusted entitlement exists.
_PLAN_DEPENDENT_PREFLIGHT_COST = Decimal("0.01")
_CAPABILITIES: Mapping[str, tuple[str, str, tuple[str, ...]]] = {
    "fund_holdings": ("fund_portfolio", "quarterly", ("ts_code", "period")),
    "stock_history": ("daily", "daily", ("ts_code", "trade_date", "start_date", "end_date")),
    "stock_financials": ("fina_indicator", "quarterly", ("ts_code", "period", "start_date", "end_date")),
    "index_calendar": ("trade_cal", "daily", ("exchange", "start_date", "end_date")),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: object, *, allow_blank: bool = False) -> str:
    if type(value) is not str or len(value) > _MAX_TEXT or (not allow_blank and not value):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    return value


def _date(value: object) -> date:
    raw = _text(value)
    if not re.fullmatch(r"\d{8}", raw):
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    except ValueError as error:
        raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE) from error


def _public_scalar(value: object) -> object:
    if value is None:
        return None
    if type(value) is str:
        return _text(value, allow_blank=True)
    if type(value) is int:
        if value.bit_length() > 333:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if len(str(abs(value))) > 100:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        return Decimal(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        raw = str(value)
        if len(raw) > _MAX_NUMBER_TEXT:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        return Decimal(raw)
    if type(value) is Decimal:
        if not value.is_finite() or len(value.as_tuple().digits) > 100 or abs(value.as_tuple().exponent) > 100:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        return value
    raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)


class TushareAdapter(BaseProvider):
    """Tushare Pro parser and account-capability boundary.

    The official endpoint is POST-only. The current shared SafeHttpClient is
    deliberately GET-only, so a configured production instance reports
    ``unsupported_transport`` until a separately reviewed safe POST transport
    exists. Deterministic tests inject a bounded ``post_json`` fake; this class
    never falls back to GET or direct requests.
    """

    descriptor = AdapterDescriptor(
        "tushare", "Tushare Pro", "tushare", "safe_post_client_required",
        (SourceRole.MARKET_DATA, SourceRole.FALLBACK_DATA, SourceRole.CROSS_CHECK),
        ("fund_holdings", "stock_history", "stock_financials", "index_calendar"),
        BillingModel.FREEMIUM, "api_token", (_ENV_NAME,), False,
        "Tushare Pro token and capability-specific account permission required.",
        "Configured capability access depends on actual points/plan; denials are not provider-health failures.",
        "以 Tushare Pro 发布和修订为准", "以账户实际积分和套餐权限为准", "套餐/积分成本未知；无可信能力级权益时禁止请求",
        _REFERENCE, 50, CatalogStatus.UNCONFIGURED,
    )

    def __init__(
        self,
        *,
        http: Any,
        credentials: Any,
        budget_guard: Any | None = None,
        cache_getter: Callable[[ProviderRequest], object | None] | None = None,
        fetched_at: Callable[[], datetime] = _now,
    ) -> None:
        self._http, self._credentials = http, credentials
        self._budget_guard, self._cache_getter, self._fetched_at = budget_guard, cache_getter, fetched_at
        self._capability_states: dict[str, str] = {}
        self._state_lock = threading.RLock()

    def capability_status(self, capability_id: str) -> str:
        with self._state_lock:
            return self._capability_states.get(capability_id, "unprobed")

    def _set_capability_status(self, capability_id: str, status: str) -> None:
        with self._state_lock:
            self._capability_states[capability_id] = status

    def _credential(self) -> str | None:
        try:
            value = self._credentials.get(self.descriptor.adapter_id, _ENV_NAME)
        except Exception:
            return None
        return value if type(value) is str and value.strip() else None

    def _authorize(self, now: datetime) -> str | None:
        if self._budget_guard is None:
            return None
        decision = self._budget_guard.authorize(
            self.descriptor,
            estimated_cost=_PLAN_DEPENDENT_PREFLIGHT_COST,
            now=now,
        )
        if (
            type(decision) is not BudgetDecision
            or type(decision.allowed) is not bool
            or type(decision.reason) is not str
            or type(decision.estimated_cost) is not Decimal
            or (decision.reservation_id is not None and type(decision.reservation_id) is not str)
            or decision.estimated_cost != _PLAN_DEPENDENT_PREFLIGHT_COST
        ):
            raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
        if not decision.allowed:
            return str(decision.reason)
        if decision.reservation_id is None:
            raise ProviderUnavailable("budget_status_invalid", reference=_REFERENCE)
        self._budget_guard.record(
            self.descriptor.adapter_id,
            reservation_id=decision.reservation_id,
            actual_cost=Decimal("0"),
            request_count=0,
            status="cost_unknown",
            units=Decimal("0"),
            now=now,
        )
        return "cost_unknown"

    @staticmethod
    def _request(request: ProviderRequest) -> tuple[str, dict[str, object], str, int | None]:
        if type(request) is not ProviderRequest or type(request.capability_id) is not str or type(request.parameters) is not dict:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        contract = _CAPABILITIES.get(request.capability_id)
        if contract is None:
            raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)
        api_name, frequency, allowed_parameters = contract
        cleaned: dict[str, object] = {}
        for key, value in request.parameters.items():
            if type(key) is not str or not _PARAMETER.fullmatch(key) or key not in (*allowed_parameters, "max_age_days"):
                raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
            if key == "max_age_days":
                continue
            if type(value) not in {str, int} or (type(value) is int and value.bit_length() > 850) or len(str(value)) > 256:
                raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
            cleaned[key] = value
        max_age = request.parameters.get("max_age_days")
        if max_age is not None and (type(max_age) is not int or not 0 <= max_age <= 36_500):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return api_name, cleaned, frequency, max_age

    @staticmethod
    def _payload_error(payload: Mapping[str, object]) -> None:
        code = payload.get("code")
        message = payload.get("msg", "")
        if type(code) is not int or type(message) is not str or len(message) > _MAX_TEXT:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if code == 0:
            return
        lower = message.lower()
        if code == -2001 or "权限" in message or "permission" in lower or "积分" in message:
            raise ProviderUnavailable("plan_unavailable", reference=_REFERENCE)
        if code in {-2002, -1} or "token" in lower or "auth" in lower:
            raise ProviderUnavailable("authentication", reference=_REFERENCE)
        if code == 429 or "频率" in message or "rate" in lower:
            raise ProviderRateLimited(retry_after_seconds=0.0, reference=_REFERENCE)
        raise ProviderUnavailable("provider_error", reference=_REFERENCE)

    def _parse(self, payload: object, request: ProviderRequest, *, now: datetime, frequency: str, max_age_days: int | None, cached: bool) -> tuple[ProviderValue, ...]:
        if type(payload) is not dict:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        self._payload_error(payload)
        data = payload.get("data")
        if type(data) is not dict:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        fields, items = data.get("fields"), data.get("items")
        if type(fields) is not list or not 0 < len(fields) <= _MAX_FIELDS or type(items) is not list:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if any(type(field) is not str or not _FIELD.fullmatch(field) for field in fields) or len(set(fields)) != len(fields):
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        if not items:
            raise ProviderUnavailable("empty_result", reference=_REFERENCE)
        if len(items) > _MAX_ROWS:
            raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
        api_name = _CAPABILITIES[request.capability_id][0]
        rows: list[ProviderValue] = []
        for item in items:
            if type(item) is not list or len(item) != len(fields):
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            public_row = {field: _public_scalar(value) for field, value in zip(fields, item)}
            period_value = next((public_row.get(field) for field in ("end_date", "trade_date", "ann_date", "period") if public_row.get(field) is not None), request.parameters.get("period"))
            as_of = _date(period_value)
            if as_of > now.date():
                raise ProviderSchemaChanged("schema_changed", reference=_REFERENCE)
            stale = max_age_days is not None and (now.date() - as_of).days > max_age_days
            status = "cached" if cached else ("stale" if stale else "upstream_reported")
            metadata = {
                "api_name": api_name,
                "period": str(period_value),
                "source_reference": _REFERENCE,
                "plan_status": "available",
            }
            if cached:
                metadata["cache_status"] = "fallback"
            rows.append(ProviderValue(public_row, "tushare", "tushare", request.capability_id, as_of, now, status, "Tushare Pro account terms apply", 50, None, "provider_native", frequency, metadata))
        return tuple(rows)

    def _execute(self, request: ProviderRequest, credential: str, now: datetime) -> tuple[ProviderValue, ...]:
        api_name, parameters, frequency, max_age_days = self._request(request)
        post_json = getattr(self._http, "post_json", None)
        if not callable(post_json):
            raise ProviderUnavailable("unsupported_transport", reference=_REFERENCE)
        body = {"api_name": api_name, "token": credential, "params": parameters, "fields": ""}
        try:
            payload = post_json(_REFERENCE, headers={"Accept": "application/json", "Content-Type": "application/json"}, json_body=body)
        except ProviderUnavailable as error:
            if self._cache_getter is None or error.code not in _CACHEABLE:
                raise
            cached = self._cache_getter(request)
            if cached is None:
                raise
            return self._parse(cached, request, now=now, frequency=frequency, max_age_days=max_age_days, cached=True)
        return self._parse(payload, request, now=now, frequency=frequency, max_age_days=max_age_days, cached=False)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        credential = self._credential()
        if credential is None:
            raise ProviderUnavailable("unconfigured", reference=_REFERENCE)
        now = self._fetched_at()
        blocked = self._authorize(now)
        if blocked is not None:
            raise ProviderUnavailable(blocked, reference=_REFERENCE)
        try:
            rows = self._execute(request, credential, now)
        except ProviderUnavailable as error:
            if error.code == "plan_unavailable":
                self._set_capability_status(request.capability_id, "plan_unavailable")
            raise
        self._set_capability_status(request.capability_id, "available")
        return rows

    def probe(self, capability_id: str, *, parameters: Mapping[str, object] | None = None) -> Mapping[str, object]:
        credential = self._credential()
        if credential is None:
            return {"status": "unconfigured", "connected": False, "health_failure": False}
        now = self._fetched_at()
        blocked = self._authorize(now)
        if blocked is not None:
            self._set_capability_status(capability_id, blocked)
            return {"status": blocked, "connected": False, "health_failure": False, "capability_id": capability_id}
        request = ProviderRequest(capability_id, {} if parameters is None else parameters)
        try:
            rows = self._execute(request, credential, now)
        except ProviderRateLimited as error:
            status, failure = "rate_limited", False
            result: dict[str, object] = {"status": status, "connected": False, "health_failure": failure, "capability_id": capability_id, "retry_after_seconds": error.retry_after_seconds}
        except ProviderSchemaChanged:
            status, result = "schema_changed", {"status": "schema_changed", "connected": False, "health_failure": True, "capability_id": capability_id}
        except ProviderUnavailable as error:
            status = "authentication_failed" if error.code == "authentication" else error.code
            failure = error.code not in {"authentication", "plan_unavailable", "empty_result", "unsupported_transport", "unconfigured", "disabled"}
            result = {"status": status, "connected": False, "health_failure": failure, "capability_id": capability_id}
        else:
            status, result = "available", {"status": "available", "connected": True, "health_failure": False, "capability_id": capability_id, "returned_count": len(rows)}
        self._set_capability_status(capability_id, status)
        return result


__all__ = ["TushareAdapter"]
