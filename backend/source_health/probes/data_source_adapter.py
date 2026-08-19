from __future__ import annotations

import time
from typing import Any, Mapping

from data_sources.provider_errors import ProviderError
from data_sources.references import public_source_reference
from source_health.probe_errors import classify_probe_error, redact_probe_message


_BARRIER_STATUSES = {
    "catalog_only",
    "disabled",
    "disabled_personal_research_only",
    "license_required",
    "not_probed",
    "unconfigured",
    "unconfigured_contact",
    "unexamined",
}


def _reference(value: object) -> str | None:
    try:
        reference = public_source_reference(str(value or ""))
    except ValueError:
        return None
    return reference or None


def _error_type(error: BaseException) -> str:
    if isinstance(error, ProviderError):
        return {
            "timeout": "timeout",
            "dns": "dns",
            "tls": "tls",
            "authentication": "authentication",
            "rate_limited": "rate_limit",
            "schema_changed": "schema_changed",
        }.get(error.code, "unknown")
    return classify_probe_error(error).error_type


def probe_data_source_adapter(adapter: Any, capability_id: str) -> dict[str, Any]:
    """Translate a contract-adapter probe into health metadata only.

    This adapter never inspects content-admission fields and does not claim a
    connection until the adapter itself reports one.
    """
    started = time.perf_counter()
    descriptor = getattr(adapter, "descriptor", None)
    configured_reference = _reference(getattr(descriptor, "configured_reference", ""))
    source_name = str(getattr(descriptor, "adapter_name", getattr(descriptor, "adapter_id", type(adapter).__name__)))
    try:
        raw = adapter.probe(capability_id)
        if not isinstance(raw, Mapping):
            raise TypeError("provider probe result must be a mapping")
    except Exception as error:
        return {
            "status": "failure", "source_name": source_name, "source_reference": configured_reference,
            "final_reference": None, "error_type": _error_type(error),
            "error_message_redacted": redact_probe_message(error), "http_status": None,
            "latency_ms": max(0, round((time.perf_counter() - started) * 1000)), "returned_items": 0,
            "data_as_of_date": None, "field_completeness_pct": 0.0,
        }

    connection_status = str(raw.get("status") or "unknown")
    connected = raw.get("connected") is True
    barrier = connection_status in _BARRIER_STATUSES
    if connected and connection_status == "success":
        status, error_type, completeness = "success", "none", 100.0
    elif barrier:
        status, error_type, completeness = "partial", "none", None
    else:
        status, error_type, completeness = "failure", "unknown", 0.0
    observed_reference = _reference(raw.get("final_reference") or raw.get("observed_final_reference")) if connected else None
    return {
        "status": status,
        "source_name": source_name,
        "source_reference": configured_reference,
        "final_reference": observed_reference,
        "error_type": error_type,
        "error_message_redacted": "" if status != "failure" else redact_probe_message(connection_status),
        "http_status": None,
        "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
        "returned_items": max(0, int(raw.get("returned_items") or 0)),
        "data_as_of_date": raw.get("data_as_of_date"),
        "field_completeness_pct": completeness,
        "connection_status": connection_status,
    }
