from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
import re
import time
from typing import Any
from urllib.parse import quote

from data_sources.models import AdapterDescriptor, BillingModel, CatalogStatus, ProviderValue, SourceRole
from data_sources.provider_contract import ProviderRequest
from data_sources.provider_errors import ProviderRateLimited, ProviderSchemaChanged, ProviderUnavailable

from .base import BaseProvider
from .numeric import is_finite_public_number


_REFERENCE = "https://sdmxcentral.imf.org/ws/public/sdmxapi/rest/"
_KEY = re.compile(r"^[A-Za-z0-9._,@-]+$")
_FREQUENCY = {"A": "annual", "Q": "quarterly", "M": "monthly", "D": "daily"}


def _now() -> datetime: return datetime.now(timezone.utc)


def _date(value: object, reference: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2})?", value): raise ProviderSchemaChanged("schema_changed", reference=reference)
    try: return date(int(value[:4]), int(value[5:]) if len(value) == 7 else 1, 1)
    except ValueError as error: raise ProviderSchemaChanged("schema_changed", reference=reference) from error


class ImfAdapter(BaseProvider):
    """IMF public SDMX contract adapter, deliberately catalog-only until live validation."""

    descriptor = AdapterDescriptor("imf", "IMF public SDMX", "imf", "http_client", (SourceRole.MACRO_DATA,), ("macro_series",), BillingModel.FREE_NO_KEY, "none", (), False, "IMF public SDMX contract is registered; current live accessibility is not asserted.", "Catalog-only pending a bounded official live validation; no credentials or fallback scraping.", "以 IMF 发布与修订为准", "未知；不在 catalog-only 状态发起健康连通性声明", "免费公开路径；不自动购买或升级", _REFERENCE, 40, CatalogStatus.CATALOG_ONLY)

    def __init__(self, *, http: Any, sleeper: Callable[[float], None] = time.sleep, fetched_at: Callable[[], datetime] = _now) -> None: self._http, self._sleeper, self._fetched_at = http, sleeper, fetched_at

    @staticmethod
    def _request(request: ProviderRequest) -> tuple[str, str, str | None, str | None]:
        if request.capability_id != "macro_series": raise ProviderUnavailable("unsupported_capability", reference=_REFERENCE)
        dataset, series = request.parameters.get("dataset"), request.parameters.get("series_key")
        start, end = request.parameters.get("start_period"), request.parameters.get("end_period")
        if not isinstance(dataset, str) or not _KEY.fullmatch(dataset) or not isinstance(series, str) or not _KEY.fullmatch(series) or any(value is not None and (not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2})?", value)) for value in (start, end)) or (start and end and start > end): raise ProviderUnavailable("invalid_request_parameter", reference=_REFERENCE)
        return dataset, series, start, end

    def _get_json(self, url: str, params: Mapping[str, object]) -> object:
        try: return self._http.get_json(url, headers={"Accept": "application/vnd.sdmx.data+json"}, params=params)
        except ProviderRateLimited as error:
            self._sleeper(min(max(float(error.retry_after_seconds), 0.0), 60.0))
            return self._http.get_json(url, headers={"Accept": "application/vnd.sdmx.data+json"}, params=params)

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]:
        dataset, series, start, end = self._request(request)
        url = f"https://sdmxcentral.imf.org/ws/public/sdmxapi/rest/data/{quote(dataset, safe=',.')}/{quote(series, safe='.')}"
        params: dict[str, object] = {"format": "sdmx-json"}
        if start: params["startPeriod"] = start
        if end: params["endPeriod"] = end
        payload = self._get_json(url, params)
        if not isinstance(payload, Mapping): raise ProviderSchemaChanged("schema_changed", reference=url)
        header, data_sets, structure = payload.get("header"), payload.get("dataSets"), payload.get("structure")
        if not isinstance(header, Mapping) or not isinstance(header.get("prepared"), str) or not isinstance(data_sets, list) or len(data_sets) != 1 or not isinstance(structure, Mapping): raise ProviderSchemaChanged("schema_changed", reference=url)
        dimensions = structure.get("dimensions")
        series_dimensions = dimensions.get("series") if isinstance(dimensions, Mapping) else None
        observation_dimensions = dimensions.get("observation") if isinstance(dimensions, Mapping) else None
        data_series = data_sets[0].get("series") if isinstance(data_sets[0], Mapping) else None
        if not isinstance(series_dimensions, list) or len(series_dimensions) < 2 or not isinstance(observation_dimensions, list) or len(observation_dimensions) != 1 or not isinstance(data_series, Mapping): raise ProviderSchemaChanged("schema_changed", reference=url)
        if not data_series: raise ProviderUnavailable("empty_result", reference=url)
        if len(data_series) != 1: raise ProviderSchemaChanged("schema_changed", reference=url)
        try:
            area = series_dimensions[0]["values"][0]["id"]
            indicator = series_dimensions[1]["values"][0]["id"]
            periods = observation_dimensions[0]["values"]
            attributes = structure["attributes"]
            unit = attributes["series"][0]["values"][0]["id"]
            frequency_code = attributes["observation"][0]["values"][0]["id"]
            observations = next(iter(data_series.values()))["observations"]
        except (KeyError, IndexError, TypeError, StopIteration) as error: raise ProviderSchemaChanged("schema_changed", reference=url) from error
        if f"{area}.{indicator}" != series or frequency_code not in _FREQUENCY or not isinstance(unit, str) or not unit or not isinstance(observations, Mapping) or not observations: raise ProviderSchemaChanged("schema_changed", reference=url)
        output: list[ProviderValue] = []
        for index, point in observations.items():
            try: raw_value = point[0]; period = periods[int(index)]["id"]
            except (IndexError, KeyError, TypeError, ValueError) as error: raise ProviderSchemaChanged("schema_changed", reference=url) from error
            if raw_value is not None and not is_finite_public_number(raw_value): raise ProviderSchemaChanged("schema_changed", reference=url)
            output.append(ProviderValue(raw_value, "imf", "imf", request.capability_id, _date(period, url), self._fetched_at(), "missing" if raw_value is None else "upstream_reported", "IMF public SDMX data", 40, None, unit, _FREQUENCY[frequency_code], {"dataset": dataset, "series_key": series, "source_revision": header["prepared"]}))
        return tuple(output)

    def probe(self, capability_id: str) -> Mapping[str, object]: return {"status": "catalog_only" if capability_id == "macro_series" else "unsupported_capability", "connected": False}
