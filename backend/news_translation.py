"""Bounded optional Chinese translation for market-news presentation.

Original event facts are never modified here. Successful translations are cached by
source content only; user-supplied model credentials exist for the duration of one
request and are never serialized.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import chat as chat_layer
from cache_io_lock import CACHE_IO_LOCK


MAX_BATCH_SIZE = 20
MAX_TITLE_LENGTH = 1000
MAX_SUMMARY_LENGTH = 8000
_TRANSLATION_LOCK = threading.Lock()
_CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
_ALLOWED_MODEL_KEYS = {"translated_title_zh", "translated_summary_zh"}


def default_cache_file() -> Path:
    data_dir = Path(os.environ.get("VR_DATA_DIR") or Path.home() / ".vibe-research")
    return data_dir / "cache" / "translations" / "v1.json"


def _normalize(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def content_hash(title: str, summary: str, source_language: str) -> str:
    canonical = "\n".join((_normalize(title), _normalize(summary), _normalize(source_language).lower()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_chinese(item: dict[str, Any]) -> bool:
    language = str(item.get("source_language") or "").strip().lower()
    if language.startswith(("zh", "cmn", "zho")):
        return True
    return bool(_CHINESE_RE.search(f"{item.get('title', '')} {item.get('summary', '')}")) and language not in {"en", "en-us", "en-gb"}


def _unavailable(event_id: str, status: str = "unavailable") -> dict[str, Any]:
    return {
        "event_id": event_id,
        "translated_title_zh": None,
        "translated_summary_zh": None,
        "translation_status": status,
        "translation_provider": None,
        "translated_at": None,
    }


def _parse_model_json(content: str) -> list[dict[str, Any]]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    raw = json.loads(text)
    rows = raw.get("translations") if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("翻译响应格式无效")
    return rows


def _default_model_runner(items: list[dict[str, Any]], llm: dict[str, Any]) -> list[dict[str, Any]]:
    payload = [{
        "title": item["title"],
        "summary": item["summary"],
        "source_language": item["source_language"],
    } for item in items]
    prompt = (
        "将下面资讯的原始标题和已有摘要翻译成简体中文。只能翻译输入内容，不得补充、推断或改写新闻中不存在的事实。"
        "公司名、产品名、股票代码和专有名词尽量保留原文，必要时放在中文后的括号中。"
        "按输入顺序只返回严格 JSON：{\"translations\":[{\"translated_title_zh\":\"...\","
        "\"translated_summary_zh\":\"...\"}]}。不要返回 Markdown 或额外字段。\n输入："
        + json.dumps(payload, ensure_ascii=False)
    )
    provider = str(llm.get("provider") or "")
    if provider.startswith("cli-"):
        result = chat_layer.run_chat_cli(llm, [{"role": "user", "content": prompt}], "市场资讯忠实翻译")
        return _parse_model_json(result.get("content") or "")
    data = chat_layer._call_llm(  # existing checked OpenAI-compatible boundary, without tools
        llm,
        [
            {"role": "system", "content": "你是忠实的金融资讯翻译器，只翻译给定文字，不添加事实。"},
            {"role": "user", "content": prompt},
        ],
        use_tools=False,
    )
    return _parse_model_json(data["choices"][0]["message"].get("content") or "")


class TranslationService:
    def __init__(
        self,
        cache_file: Path | None = None,
        now: Callable[[], datetime] | None = None,
        model_runner: Callable[[list[dict[str, Any]], dict[str, Any]], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.cache_file = cache_file or default_cache_file()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._model_runner = model_runner or _default_model_runner

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
            if raw.get("version") != 1 or not isinstance(raw.get("entries"), dict):
                return {"version": 1, "entries": {}}
            return raw
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return {"version": 1, "entries": {}}

    def _write(self, document: dict[str, Any]) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_file.with_suffix(self.cache_file.suffix + ".tmp")
        tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.cache_file)

    @staticmethod
    def _validate_item(item: dict[str, Any]) -> dict[str, str]:
        event_id = str(item.get("event_id") or "").strip()
        title = _normalize(str(item.get("title") or ""))
        summary = _normalize(str(item.get("summary") or ""))
        language = _normalize(str(item.get("source_language") or "unknown"))
        if not title:
            raise ValueError("资讯标题不能为空")
        if len(title) > MAX_TITLE_LENGTH or len(summary) > MAX_SUMMARY_LENGTH:
            raise ValueError("资讯标题或摘要过长")
        return {"event_id": event_id, "title": title, "summary": summary, "source_language": language}

    @staticmethod
    def _valid_translation(row: Any, source: dict[str, str]) -> tuple[str, str] | None:
        if not isinstance(row, dict) or not set(row).issubset(_ALLOWED_MODEL_KEYS):
            return None
        title = _normalize(str(row.get("translated_title_zh") or ""))
        summary = _normalize(str(row.get("translated_summary_zh") or ""))
        if not title or (source["summary"] and not summary):
            return None
        if len(title) > max(600, len(source["title"]) * 6 + 100):
            return None
        if len(summary) > max(3000, len(source["summary"]) * 6 + 200):
            return None
        return title, summary

    def translate_batch(self, items: list[dict[str, Any]], llm: dict[str, Any] | None) -> dict[str, Any]:
        if len(items) > MAX_BATCH_SIZE:
            raise ValueError("每次最多 20 条资讯")
        clean = [self._validate_item(item) for item in items]
        now_text = self._now().isoformat()
        results: list[dict[str, Any] | None] = [None] * len(clean)
        misses: list[tuple[int, str, dict[str, str]]] = []
        with _TRANSLATION_LOCK:
            with CACHE_IO_LOCK:
                document = self._load()
                entries = document["entries"]
                cache_changed = False
                for index, item in enumerate(clean):
                    if _is_chinese(item):
                        results[index] = _unavailable(item["event_id"], "not_required")
                        continue
                    digest = content_hash(item["title"], item["summary"], item["source_language"])
                    cached = entries.get(digest)
                    if isinstance(cached, dict) and cached.get("translation_status") == "translated":
                        cached["last_accessed_at"] = now_text
                        cache_changed = True
                        results[index] = {
                            "event_id": item["event_id"],
                            "translated_title_zh": cached.get("translated_title_zh"),
                            "translated_summary_zh": cached.get("translated_summary_zh"),
                            "translation_status": "translated",
                            "translation_provider": cached.get("translation_provider"),
                            "translated_at": cached.get("translated_at"),
                        }
                    else:
                        misses.append((index, digest, item))
                if cache_changed:
                    self._write(document)

            generated_entries: dict[str, dict[str, Any]] = {}
            if misses and llm and str(llm.get("model") or "").strip():
                try:
                    model_rows = self._model_runner([item for _, _, item in misses], dict(llm))
                    if len(model_rows) != len(misses):
                        raise ValueError("翻译响应条数不一致")
                    provider = str(llm.get("provider") or llm.get("model") or "configured-model")
                    for (index, digest, item), row in zip(misses, model_rows, strict=True):
                        validated = self._valid_translation(row, item)
                        if validated is None:
                            results[index] = _unavailable(item["event_id"])
                            continue
                        title, summary = validated
                        entry = {
                            "translated_title_zh": title,
                            "translated_summary_zh": summary,
                            "translation_status": "translated",
                            "translation_provider": provider,
                            "translated_at": now_text,
                            "last_accessed_at": now_text,
                        }
                        generated_entries[digest] = entry
                        results[index] = {"event_id": item["event_id"], **{
                            key: entry[key] for key in (
                                "translated_title_zh", "translated_summary_zh", "translation_status",
                                "translation_provider", "translated_at",
                            )
                        }}
                except Exception:  # model failures must never block market-news evidence
                    pass

            if generated_entries:
                with CACHE_IO_LOCK:
                    latest = self._load()
                    latest["entries"].update(generated_entries)
                    self._write(latest)
            for index, _, item in misses:
                if results[index] is None:
                    results[index] = _unavailable(item["event_id"])

        return {"translations": results, "limit": MAX_BATCH_SIZE}


_service: TranslationService | None = None


def get_service() -> TranslationService:
    global _service
    if _service is None:
        _service = TranslationService()
    return _service
