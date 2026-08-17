from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Mapping

from source_health.models import HealthLabel, ProbeObservation, ProbeStatus, RatingConfidence


DEFAULT_WEIGHTS = {
    "availability": 0.35,
    "freshness": 0.20,
    "completeness": 0.20,
    "latency": 0.10,
    "failure_streak": 0.10,
    "fallback": 0.05,
}

HEALTH_LABELS_ZH: dict[HealthLabel, str] = {
    "healthy": "健康",
    "usable": "基本可用",
    "degraded": "降级",
    "failed": "失败",
}

CONFIDENCE_LABELS_ZH: dict[RatingConfidence, str] = {
    "initial": "初始评级 · 样本不足",
    "growing": "成长中评级",
    "stable": "稳定评级",
}


def normalized_weights(
    inapplicable_dimensions: Iterable[str] = (),
    *,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> dict[str, float]:
    excluded = set(inapplicable_dimensions)
    unknown = excluded.difference(weights)
    if unknown:
        raise ValueError(f"unknown scoring dimensions: {sorted(unknown)}")
    applicable = {name: float(weight) for name, weight in weights.items() if name not in excluded}
    total = sum(applicable.values())
    if total <= 0:
        raise ValueError("at least one scoring dimension must be applicable")
    return {name: weight / total for name, weight in applicable.items()}


def availability_score(status: ProbeStatus) -> int:
    try:
        return {"success": 100, "partial": 60, "failure": 0}[status]
    except KeyError as error:
        raise ValueError(f"unsupported probe status: {status}") from error


def latency_score(latency_ms: int | float | None, *, timed_out: bool = False) -> int:
    if timed_out:
        return 0
    if latency_ms is None or latency_ms < 0:
        raise ValueError("latency_ms must be a non-negative number")
    if latency_ms <= 2_000:
        return 100
    if latency_ms <= 5_000:
        return 80
    if latency_ms <= 10_000:
        return 60
    if latency_ms <= 15_000:
        return 40
    return 10


def failure_streak_score(consecutive_failures: int) -> int:
    if consecutive_failures < 0:
        raise ValueError("consecutive_failures must be non-negative")
    return {0: 100, 1: 70, 2: 40}.get(consecutive_failures, 0)


def freshness_score(freshness_seconds: int | None, max_age_seconds: int) -> int:
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    if freshness_seconds is None or freshness_seconds < 0:
        return 0
    return 100 if freshness_seconds <= max_age_seconds else 0


def score_health(
    dimension_scores: Mapping[str, float | int | None],
    *,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> float:
    unknown = set(dimension_scores).difference(weights)
    if unknown:
        raise ValueError(f"unknown scoring dimensions: {sorted(unknown)}")
    missing = set(weights).difference(dimension_scores)
    if missing:
        raise ValueError(f"missing scoring dimensions: {sorted(missing)}")
    inapplicable = {name for name in weights if dimension_scores.get(name) is None}
    applicable_weights = normalized_weights(inapplicable, weights=weights)
    total = 0.0
    for name, weight in applicable_weights.items():
        value = dimension_scores.get(name)
        if value is None:
            raise ValueError(f"missing applicable score: {name}")
        numeric = float(value)
        if not 0 <= numeric <= 100:
            raise ValueError(f"score for {name} must be between 0 and 100")
        total += numeric * weight
    return round(total, 2)


def health_label(score: float) -> HealthLabel:
    if not 0 <= score <= 100:
        raise ValueError("health score must be between 0 and 100")
    if score >= 85:
        return "healthy"
    if score >= 70:
        return "usable"
    if score >= 45:
        return "degraded"
    return "failed"


def health_label_zh(label: HealthLabel) -> str:
    return HEALTH_LABELS_ZH[label]


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as error:
        raise ValueError(f"invalid observation date: {value}") from error


def confidence_label(
    observation_dates: Iterable[date | datetime | str],
    sample_count: int,
) -> RatingConfidence:
    if sample_count < 0:
        raise ValueError("sample_count must be non-negative")
    distinct_dates = len({_as_date(value) for value in observation_dates})
    if distinct_dates >= 7 and sample_count >= 20:
        return "stable"
    if distinct_dates >= 3 and sample_count >= 5:
        return "growing"
    return "initial"


def confidence_display(confidence: RatingConfidence) -> str:
    return CONFIDENCE_LABELS_ZH[confidence]


def score_observation(
    observation: ProbeObservation,
    *,
    freshness_max_age_seconds: int | None,
    freshness_applicable: bool = True,
    fallback_applicable: bool = True,
    observation_dates: Iterable[date | datetime | str] = (),
    sample_count: int = 1,
) -> ProbeObservation:
    dimensions: dict[str, float | int | None] = {
        "availability": availability_score(observation.probe_status),
        "freshness": (
            freshness_score(observation.freshness_seconds, freshness_max_age_seconds)
            if freshness_applicable and freshness_max_age_seconds is not None
            else None
        ),
        "completeness": observation.field_completeness_pct if observation.field_completeness_pct is not None else 0,
        "latency": latency_score(observation.latency_ms, timed_out=observation.error_type == "timeout"),
        "failure_streak": failure_streak_score(observation.consecutive_failures),
        "fallback": (100 if observation.fallback_available else 0) if fallback_applicable else None,
    }
    observation.rating_score = score_health(dimensions)
    observation.rating = health_label(observation.rating_score)
    observation.rating_confidence = confidence_label(observation_dates, sample_count)
    return observation
