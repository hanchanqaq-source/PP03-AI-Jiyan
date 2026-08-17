from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest


UTC = timezone.utc
NOW = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value


def english_event(event_id: str = "a" * 20) -> dict:
    return {
        "event_id": event_id,
        "title": "Micron launches HBM3E",
        "summary": "Shipments begin this quarter.",
        "source_language": "en",
    }


def translated_payload() -> list[dict]:
    return [{
        "translated_title_zh": "美光（Micron）发布 HBM3E",
        "translated_summary_zh": "本季度开始出货。",
    }]


def test_translation_preserves_original_fields_and_reuses_content_hash_cache(tmp_path):
    from news_translation import TranslationService, content_hash

    calls: list[list[dict]] = []

    def runner(items: list[dict], llm: dict) -> list[dict]:
        calls.append(items)
        assert llm["provider"] == "openai"
        return translated_payload()

    clock = Clock()
    cache_file = tmp_path / "translations.json"
    service = TranslationService(cache_file=cache_file, now=clock.now, model_runner=runner)
    original = english_event()

    first = service.translate_batch([original], {"provider": "openai", "model": "test"})
    clock.value += timedelta(days=1)
    second = service.translate_batch(
        [{**original, "event_id": "b" * 20}],
        {"provider": "openai", "model": "test"},
    )

    assert original == english_event()
    assert first["translations"] == [{
        "event_id": "a" * 20,
        "translated_title_zh": "美光（Micron）发布 HBM3E",
        "translated_summary_zh": "本季度开始出货。",
        "translation_status": "translated",
        "translation_provider": "openai",
        "translated_at": NOW.isoformat(),
    }]
    assert second["translations"][0]["event_id"] == "b" * 20
    assert len(calls) == 1
    digest = content_hash(original["title"], original["summary"], "en")
    document = json.loads(cache_file.read_text(encoding="utf-8"))
    assert document["entries"][digest]["last_accessed_at"] == clock.value.isoformat()
    assert "apiKey" not in cache_file.read_text(encoding="utf-8")


def test_missing_model_and_model_failure_fall_back_without_blocking(tmp_path):
    from news_translation import TranslationService

    service = TranslationService(
        cache_file=tmp_path / "translations.json",
        now=lambda: NOW,
        model_runner=lambda items, llm: (_ for _ in ()).throw(RuntimeError("Bearer secret-token")),
    )

    missing = service.translate_batch([english_event()], None)
    failed = service.translate_batch([english_event()], {"provider": "openai", "model": "test"})

    for result in (missing, failed):
        assert result["translations"] == [{
            "event_id": "a" * 20,
            "translated_title_zh": None,
            "translated_summary_zh": None,
            "translation_status": "unavailable",
            "translation_provider": None,
            "translated_at": None,
        }]
    assert not (tmp_path / "translations.json").exists()


def test_chinese_event_is_not_sent_to_model(tmp_path):
    from news_translation import TranslationService

    calls = 0

    def runner(items: list[dict], llm: dict) -> list[dict]:
        nonlocal calls
        calls += 1
        return []

    service = TranslationService(cache_file=tmp_path / "translations.json", now=lambda: NOW, model_runner=runner)
    result = service.translate_batch([{
        "event_id": "c" * 20,
        "title": "国内半导体政策发布",
        "summary": "正文摘要",
        "source_language": "zh-CN",
    }], {"provider": "openai", "model": "test"})

    assert result["translations"][0]["translation_status"] == "not_required"
    assert calls == 0


def test_malformed_or_fact_expanding_model_rows_are_rejected(tmp_path):
    from news_translation import TranslationService

    service = TranslationService(
        cache_file=tmp_path / "translations.json",
        now=lambda: NOW,
        model_runner=lambda items, llm: [{
            "translated_title_zh": "新增了并不存在的超长事实" * 100,
            "translated_summary_zh": "摘要",
            "extra_fact": "target price 100",
        }],
    )

    result = service.translate_batch([english_event()], {"provider": "openai", "model": "test"})

    assert result["translations"][0]["translation_status"] == "unavailable"
    assert not (tmp_path / "translations.json").exists()


def test_batch_limit_and_input_validation_are_enforced(tmp_path):
    from news_translation import TranslationService

    service = TranslationService(cache_file=tmp_path / "translations.json", now=lambda: NOW)

    with pytest.raises(ValueError, match="最多 20"):
        service.translate_batch([{**english_event(str(index).zfill(20))} for index in range(21)], None)
    with pytest.raises(ValueError, match="标题"):
        service.translate_batch([{**english_event(), "title": ""}], None)


def test_slow_translation_does_not_block_the_independent_fund_cache(tmp_path):
    from fund_data.cache import FundCache
    from news_translation import TranslationService

    model_started = threading.Event()
    release_model = threading.Event()
    translation_done = threading.Event()
    fund_write_done = threading.Event()

    def runner(items: list[dict], llm: dict) -> list[dict]:
        model_started.set()
        assert release_model.wait(3)
        return translated_payload()

    service = TranslationService(
        cache_file=tmp_path / "translations.json",
        now=lambda: NOW,
        model_runner=runner,
    )
    fund_cache = FundCache(tmp_path / "fund-cache", now=lambda: NOW)
    translation_thread = threading.Thread(
        target=lambda: (service.translate_batch([english_event()], {"provider": "openai", "model": "test"}), translation_done.set()),
    )
    fund_thread = threading.Thread(
        target=lambda: (fund_cache.set("profile:017811", {"name": "测试基金"}, 60), fund_write_done.set()),
    )

    translation_thread.start()
    assert model_started.wait(1)
    fund_thread.start()
    try:
        assert fund_write_done.wait(1), "slow model call held the shared cache I/O lock"
    finally:
        release_model.set()
        translation_thread.join(timeout=3)
        fund_thread.join(timeout=3)

    assert translation_done.is_set()
    assert fund_cache.get("profile:017811") is not None
