"""Bounded V0.2 fund-source contract checks; never writes the user portfolio."""

import pytest

from fund_data.cache import FundCache
from fund_data.service import FundDataService


@pytest.mark.live
def test_public_mixed_fund_has_profile_official_nav_and_disclosed_holdings(tmp_path):
    service = FundDataService(cache=FundCache(tmp_path / "fund-cache"))

    result = service.get_fund_analysis("000001", force_refresh=True)

    assert result["profile"]["data"]["code"] == "000001"
    assert result["latest_nav"]["data"]["unit_nav"] > 0
    assert result["latest_nav"]["data"]["nav_date"]
    assert len(result["nav_history"]["data"]["points"]) > 100
    assert len(result["holdings"]["data"]["holdings"]) > 0
    assert result["holdings"]["data"]["disclosure_date"]
    assert result["holdings"]["meta"]["status"] == "disclosed"

