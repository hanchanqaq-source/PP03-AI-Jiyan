from __future__ import annotations

from datetime import date, timedelta

import pytest

from source_health.models import ProbeObservation
from source_health.repair_advisor import advise_repair
from source_health.scoring import (
    availability_score,
    confidence_display,
    confidence_label,
    failure_streak_score,
    health_label,
    latency_score,
    normalized_weights,
    score_health,
    score_observation,
)


def observation(**overrides) -> ProbeObservation:
    values = {
        "source_id": "news:example:feed",
        "source_name": "Example",
        "group": "news",
        "capability": "feed",
        "started_at": "2026-08-18T00:00:00+00:00",
        "finished_at": "2026-08-18T00:00:01+00:00",
        "latency_ms": 1000,
        "probe_status": "success",
        "error_type": "none",
        "error_message_redacted": "",
        "http_status": None,
        "returned_items": 1,
        "data_as_of_date": "2026-08-18",
        "freshness_seconds": 60,
        "field_completeness_pct": 100.0,
        "used_cache": False,
        "cache_status": "not_used",
        "fallback_available": True,
        "redirected": False,
        "final_reference": None,
    }
    values.update(overrides)
    return ProbeObservation(**values)


def test_inapplicable_dimensions_are_removed_and_remaining_weights_normalized():
    weights = normalized_weights({"freshness", "fallback"})

    assert set(weights) == {"availability", "completeness", "latency", "failure_streak"}
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["availability"] == pytest.approx(0.35 / 0.75)
    assert score_health({
        "availability": 100,
        "freshness": None,
        "completeness": 100,
        "latency": 100,
        "failure_streak": 100,
        "fallback": None,
    }) == pytest.approx(100.0)


def test_missing_dimension_is_not_silently_treated_as_inapplicable():
    with pytest.raises(ValueError, match="missing scoring dimensions"):
        score_health({"availability": 100})


@pytest.mark.parametrize(("status", "expected"), [("success", 100), ("partial", 60), ("failure", 0)])
def test_availability_scoring_boundaries(status, expected):
    assert availability_score(status) == expected


@pytest.mark.parametrize(("failures", "expected"), [(0, 100), (1, 70), (2, 40), (3, 0), (99, 0)])
def test_failure_streak_scoring_boundaries(failures, expected):
    assert failure_streak_score(failures) == expected


@pytest.mark.parametrize(
    ("latency_ms", "timed_out", "expected"),
    [
        (2000, False, 100),
        (2001, False, 80),
        (5000, False, 80),
        (5001, False, 60),
        (10000, False, 60),
        (10001, False, 40),
        (15000, False, 40),
        (15001, False, 10),
        (1, True, 0),
    ],
)
def test_latency_scoring_boundaries(latency_ms, timed_out, expected):
    assert latency_score(latency_ms, timed_out=timed_out) == expected


@pytest.mark.parametrize(
    ("score", "expected"),
    [(85, "healthy"), (84.99, "usable"), (70, "usable"), (69.99, "degraded"), (45, "degraded"), (44.99, "failed"), (0, "failed")],
)
def test_health_rating_boundaries(score, expected):
    assert health_label(score) == expected


def test_confidence_uses_distinct_dates_and_sample_count():
    start = date(2026, 8, 1)

    assert confidence_label([start, start, start + timedelta(days=1)], 5) == "initial"
    assert confidence_label([start + timedelta(days=i) for i in range(3)], 5) == "growing"
    assert confidence_label([start + timedelta(days=i) for i in range(7)], 19) == "growing"
    assert confidence_label([start + timedelta(days=i) for i in range(7)], 20) == "stable"
    assert confidence_display("initial") == "初始评级 · 样本不足"


def test_permanent_same_source_redirect_is_an_immediate_fix():
    advice = advise_repair(
        observation(http_status=301, redirected=True, final_reference="https://example.com/new.xml"),
        permanent_redirect_same_public_source=True,
    )

    assert advice.value == "immediate_fix"


@pytest.mark.parametrize("status", [401, 403])
def test_permission_failures_are_replace_candidates_without_bypass(status):
    advice = advise_repair(observation(probe_status="failure", error_type="authentication", http_status=status))

    assert advice.value == "replace_candidate"
    assert "绕过" not in advice.reason


def test_single_timeout_is_observe_only():
    advice = advise_repair(
        observation(
            probe_status="failure",
            error_type="timeout",
            consecutive_failures=1,
            fallback_available=False,
        ),
        failure_days=1,
    )

    assert advice.value == "observe"


def test_retry_after_and_reliable_cache_remain_observe_without_a_fallback():
    limited = advise_repair(
        observation(
            probe_status="failure",
            error_type="rate_limit",
            http_status=429,
            fallback_available=False,
        ),
        retry_after_present=True,
    )
    cached = advise_repair(
        observation(probe_status="failure", error_type="http", fallback_available=False),
        reliable_cache_available=True,
    )

    assert limited.value == "observe"
    assert cached.value == "observe"


def test_healthy_source_has_no_repair_advice():
    advice = advise_repair(observation())

    assert advice.value == "none"


def test_score_observation_removes_an_inapplicable_fallback_even_when_one_exists():
    subject = observation(
        probe_status="failure",
        field_completeness_pct=0,
        fallback_available=True,
        consecutive_failures=1,
    )

    score_observation(
        subject,
        freshness_max_age_seconds=None,
        freshness_applicable=False,
        fallback_applicable=False,
    )

    assert subject.rating_score == pytest.approx(22.67)


def test_score_observation_rejects_applicable_freshness_without_a_threshold():
    with pytest.raises(ValueError, match="freshness_max_age_seconds is required"):
        score_observation(
            observation(),
            freshness_max_age_seconds=None,
            freshness_applicable=True,
        )


def test_tls_bypass_is_never_recommended_as_an_immediate_fix():
    advice = advise_repair(
        observation(probe_status="failure", error_type="tls"),
        confirmed_compatibility_issue=True,
        requires_tls_bypass=True,
    )

    assert advice.value == "replace_candidate"
