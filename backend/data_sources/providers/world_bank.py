from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
import re
import time
from typing import Any

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider
from .numeric import is_finite_public_number


_REFERENCE = "https://api.worldbank.org/"
_PATH_VALUE = re.compile(r"^[A-Za-z0-9._;-]+$")
_DATE_VALUE = re.compile(r"^[0-9MQY:-]+$")
_MAX_RETRY_AFTER_SECONDS = 60.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _period(value: object, reference: str) -> tuple[date, str]:
    if not isinstance(value, str) or not value:
        raise ProviderSchemaChanged("schema_changed", reference=reference)
    try:
        if re.fullmatch(r"\d{4}", value):
            return date(int(value), 1, 1), "annual"
        if re.fullmatch(r"\d{4}M\d{2}", value):
            return date(int(value[:4]), int(value[5:]), 1), "monthly"
        if re.fullmatch(r"\d{4}Q[1-4]", value):
            return date(int(value[:4]), (int(value[-1]) - 1) * 3 + 1, 1), "quarterly"
    except ValueError as error:
        raise ProviderSchemaChanged("schema_changed", reference=reference) from error
    raise ProviderSchemaChanged("schema_changed", reference=reference)


class WorldBankAdapter(BaseProvider):
    """Official World Bank Indicators V2 adapter with explicit public request bounds."""

    descriptor = AdapterDescriptor(
        "world-bank", "World Bank Indicators", "world_bank", "http_client", (SourceRole.MACRO_DATA,),
        ("macro_indicator",), BillingModel.FREE_NO_KEY, "none", (), True,
        "World Bank Indicators API 公开数据；使用须遵守上游条款。",
        "仅请求明确国家和指标代码；不使用凭据。", "以 World Bank 发布与修订为准",
        "未声明；按公开入口合理限速", "免费无需密钥；不自动购买或升级", _REFERENCE, 30,
        CatalogStatus.CONFIGURED,
    )

    def __init__(self, *, http: Any, sleeper: Callable[[float], None] = time.sleep, fetched_at: Callable[[], datetime] = _now) -> None:
        self._http, self._sleeper, self._fetched_at = http, sleeper, fetched_at

    @staticmethod
    def _request(request: ProviderRequest) -> tuple[str, str, str | None, str | None]:
        if request.capability_id != "macro_indicator":
            raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)
        country, indicator = request.parameters.get("country"), request.parameters.get("indicator")
        raw_date, frequency = request.parameters.get("date"), request.parameters.get("frequency")
        if not isinstance(country, str) or not _PATH_VALUE.fullmatch(country) or not isinstance(indicator, str) or not _PATH_VALUE.fullmatch(indicator):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if raw_date is not None and (not isinstance(raw_date, str) or not _DATE_VALUE.fullmatch(raw_date)):
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if frequency is not None and frequency not in {"annual", "quarterly", "monthly"}:
            raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return country, indicator, raw_date, str(frequency) if frequency is not None else None

    def _get_json(self, url: str, params: Mapping[str, object]) -> object:
        try:
            return self._http.get_json(url, headers={"Accept": "application/json"}, params=params)
        except ProviderRateLimited as error:
            self._sleeper(min(max(float(error.retry_after_seconds), 0.0), _MAX_RETRY_AFTER_SECONDS))
            return self._http.get_json(url, headers={"Accept": "application/json"}, params=params)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        country, indicator, requested_date, frequency = self._request(request)
        url = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
        params: dict[str, object] = {"format": "json", "per_page": 1000}
        if requested_date is not None:
            params["date"] = requested_date
        payload = self._get_json(url, params)
        if not isinstance(payload, list) or len(payload) != 2 or not isinstance(payload[0], Mapping) or not isinstance(payload[1], list):
            raise ProviderSchemaChanged("schema_changed", reference=url)
        if not payload[1]:
            if payload[0].get("total") in {0, "0"}:
                raise ProviderUnavailable("empty_result", reference=url)
            raise ProviderSchemaChanged("schema_changed", reference=url)
        revision = payload[0].get("lastupdated")
        if not isinstance(revision, str) or not revision:
            raise ProviderSchemaChanged("schema_changed", reference=url)
        rows: list[ProviderValue] = []
        for item in payload[1]:
            indicator_data = item.get("indicator") if isinstance(item, Mapping) else None
            if not isinstance(item, Mapping) or not isinstance(indicator_data, Mapping) or item.get("countryiso3code") != country or indicator_data.get("id") != indicator:
                raise ProviderSchemaChanged("schema_changed", reference=url)
            unit, value = item.get("unit"), item.get("value")
            if not isinstance(unit, str) or (value is not None and not is_finite_public_number(value)):
                raise ProviderSchemaChanged("schema_changed", reference=url)
            as_of_date, observed_frequency = _period(item.get("date"), url)
            if frequency is not None and frequency != observed_frequency:
                raise ProviderSchemaChanged("schema_changed", reference=url)
            rows.append(ProviderValue(value, "world_bank", "world-bank", request.capability_id, as_of_date, self._fetched_at(), "missing" if value is None else "upstream_reported", "World Bank Indicators API public data", 30, None, unit or "unknown", observed_frequency, {"country": country, "indicator": indicator, "source_revision": revision}))
        return tuple(rows)

    def probe(self, capability_id: str) -> Mapping[str, object]:
        return {"status": "not_probed" if capability_id == "macro_indicator" else "unsupported_capability", "connected": False}
