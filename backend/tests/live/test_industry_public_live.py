from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path

import pytest

from data_sources.http import SafeHttpClient
from data_sources.providers.industry_price_public import TrendForcePublicPriceAdapter
from industry_research.source_qualification import (
    PUBLIC_PRICE_RESPONSE_CAP_BYTES,
    PUBLIC_PRICE_URL,
    UNVERIFIED_LICENSE,
)


@pytest.mark.live
def test_trendforce_public_current_snapshot_qualification_once_without_redirects_or_credentials(request):
    """One bounded qualification GET; an unavailable/unconfigured result is an accepted truthful outcome."""
    if "live" not in str(request.config.option.markexpr or ""):
        pytest.skip("requires explicit -m live")
    adapter = TrendForcePublicPriceAdapter(
        http=SafeHttpClient(
            timeout_seconds=10,
            max_bytes=PUBLIC_PRICE_RESPONSE_CAP_BYTES,
            max_redirects=0,
            user_agent="PP03-AI-Jiyan/source-qualification-v0.2-w3",
        )
    )

    result = adapter.qualify()
    payload = asdict(result)
    payload["qualification_run_utc"] = datetime.now(timezone.utc).isoformat()
    payload["request_count_cap"] = 1
    payload["redirect_follow_cap"] = 0
    payload["request_header_values_recorded"] = False
    if isinstance(payload.get("data_date"), date):
        payload["data_date"] = payload["data_date"].isoformat()

    evidence_path = Path(os.environ["VR_ACCEPTANCE_DIR"]) / "industry-public-qualification.json"
    evidence_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert result.request_url == PUBLIC_PRICE_URL
    assert result.response_cap_bytes == PUBLIC_PRICE_RESPONSE_CAP_BYTES
    assert result.license_conclusion == UNVERIFIED_LICENSE
    assert result.failure_reason is not None
    assert result.login_required is False or result.failure_reason == "login_required"
    assert result.cookie_required is False or result.failure_reason == "cookie_required"
    assert result.member_download is False or result.failure_reason == "member_download"
    assert not hasattr(result, "values")
