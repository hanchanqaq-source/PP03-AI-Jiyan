from __future__ import annotations

import csv
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from io import StringIO
import re
import time
from typing import Any
from urllib.parse import quote

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider


_REFERENCE = "https://sdmx.oecd.org/public/"
_KEY = re.compile(r"^[A-Za-z0-9._,@-]+$")
_PERIOD = re.compile(r"^\d{4}(?:-\d{2})?(?:-\d{2})?$")
_FREQUENCY = {"A": "annual", "Q": "quarterly", "M": "monthly", "D": "daily"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value: str, reference: str) -> date:
    try:
        if re.fullmatch(r"\d{4}", value): return date(int(value), 1, 1)
        if re.fullmatch(r"\d{4}-\d{2}", value): return date(int(value[:4]), int(value[5:]), 1)
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ProviderSchemaChanged("schema_changed", reference=reference) from error


class OecdAdapter(BaseProvider):
    """OECD SDMX CSV adapter; rows retain OECD dataset and series provenance."""

    descriptor = AdapterDescriptor("oecd", "OECD SDMX", "oecd", "http_client", (SourceRole.MACRO_DATA,), ("macro_series",), BillingModel.FREE_NO_KEY, "none", (), True, "OECD SDMX 公开数据；使用须遵守上游条款。", "仅请求明确 dataset 与 series key；不使用凭据。", "以 OECD 发布与修订为准", "未声明；按公开入口合理限速", "免费无需密钥；不自动购买或升级", _REFERENCE, 30, CatalogStatus.CONFIGURED)

    def __init__(self, *, http: Any, sleeper: Callable[[float], None] = time.sleep, fetched_at: Callable[[], datetime] = _now) -> None:
        self._http, self._sleeper, self._fetched_at = http, sleeper, fetched_at

    @staticmethod
    def _request(request: ProviderRequest) -> tuple[str, str, str | None, str | None]:
        if request.capability_id != "macro_series": raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)
        dataset, series = request.parameters.get("dataset"), request.parameters.get("series_key")
        start, end = request.parameters.get("start_period"), request.parameters.get("end_period")
        if not isinstance(dataset, str) or not _KEY.fullmatch(dataset) or not isinstance(series, str) or not _KEY.fullmatch(series): raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        if any(value is not None and (not isinstance(value, str) or not _PERIOD.fullmatch(value)) for value in (start, end)) or (start and end and start > end): raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return dataset, series, start, end

    def _get_bytes(self, url: str, params: Mapping[str, object]) -> bytes:
        try: return self._http.get_bytes(url, headers={"Accept": "text/csv"}, params=params)
        except ProviderRateLimited as error:
            self._sleeper(min(max(float(error.retry_after_seconds), 0.0), 60.0))
            return self._http.get_bytes(url, headers={"Accept": "text/csv"}, params=params)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        dataset, series, start, end = self._request(request)
        url = f"https://sdmx.oecd.org/public/rest/v1/data/{quote(dataset, safe=',.')}/{quote(series, safe='.')}"
        params: dict[str, object] = {"format": "csvfile"}
        if start: params["startPeriod"] = start
        if end: params["endPeriod"] = end
        try: decoded = self._get_bytes(url, params).decode("utf-8")
        except UnicodeDecodeError as error: raise ProviderSchemaChanged("schema_changed", reference=url) from error
        rows = list(csv.DictReader(StringIO(decoded)))
        required = {"DATAFLOW", "REF_AREA", "SUBJECT", "FREQ", "TIME_PERIOD", "OBS_VALUE", "UNIT_MEASURE", "LAST_UPDATE", "OBS_STATUS"}
        if not rows or not required.issubset(set(rows[0] or ())): raise ProviderSchemaChanged("schema_changed", reference=url)
        values: list[ProviderValue] = []
        for row in rows:
            if any(not isinstance(row.get(field), str) for field in required) or row["DATAFLOW"] != dataset or f"{row['FREQ']}.{row['REF_AREA']}.{row['SUBJECT']}" != series or row["FREQ"] not in _FREQUENCY or not row["UNIT_MEASURE"] or not row["LAST_UPDATE"]:
                raise ProviderSchemaChanged("schema_changed", reference=url)
            raw = row["OBS_VALUE"].strip()
            try: value = None if not raw else float(raw)
            except ValueError as error: raise ProviderSchemaChanged("schema_changed", reference=url) from error
            values.append(ProviderValue(value, "oecd", "oecd", request.capability_id, _as_date(row["TIME_PERIOD"], url), self._fetched_at(), "missing" if value is None else "upstream_reported", "OECD SDMX public data", 30, None, row["UNIT_MEASURE"], _FREQUENCY[row["FREQ"]], {"dataset": dataset, "series_key": series, "source_revision": row["LAST_UPDATE"]}))
        return tuple(values)

    def probe(self, capability_id: str) -> Mapping[str, object]:
        return {"status": "not_probed" if capability_id == "macro_series" else "unsupported_capability", "connected": False}
