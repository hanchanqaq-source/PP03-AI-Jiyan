from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading
from typing import Any, Callable

import requests

from fund_data.models import ProviderResult
from fund_data.providers.base import BaseFundProvider, ProviderUnavailable

SOURCE_NAME = "巨潮资讯上市公司行业归属"
SOURCE_REFERENCE = "https://webapi.cninfo.com.cn/api/stock/p_stock2110"
STANDARD_PRIORITY = {
    "008003": 0,
    "008018": 1,
    "008002": 2,
    "008014": 3,
    "008019": 3,
    "008021": 4,
    "008001": 4,
}
_TOKEN_RUNTIME_INIT_LOCK = threading.RLock()


class _CninfoTokenFactory:
    def __init__(self):
        self._thread_state = threading.local()

    def __call__(self) -> str:
        runtime = getattr(self._thread_state, "runtime", None)
        if runtime is None:
            # MiniRacer's V8 pool initialization is process-global and crashes the
            # interpreter when two request threads initialize it concurrently.
            # The isolate itself is thread-affine, so cache one runtime per thread.
            with _TOKEN_RUNTIME_INIT_LOCK:
                runtime = getattr(self._thread_state, "runtime", None)
                if runtime is None:
                    from akshare.datasets import get_ths_js
                    from py_mini_racer import MiniRacer

                    runtime = MiniRacer()
                    js_path = Path(get_ths_js("cninfo.js"))
                    runtime.eval(js_path.read_text(encoding="utf-8"))
                    self._thread_state.runtime = runtime
        return str(runtime.call("getResCode1"))


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _changed_at(row: dict[str, Any]) -> str:
    return str(row.get("VARYDATE") or "")[:10]


def _select_classification(code: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    supported = [row for row in rows if str(row.get("F001V") or "") in STANDARD_PRIORITY]
    if not supported:
        return None
    selected_priority = min(STANDARD_PRIORITY[str(row.get("F001V"))] for row in supported)
    candidates = [
        row for row in supported
        if STANDARD_PRIORITY[str(row.get("F001V"))] == selected_priority
    ]
    selected = max(candidates, key=_changed_at)
    return {
        "stock_code": str(selected.get("SECCODE") or code).zfill(6),
        "stock_name": _clean(selected.get("SECNAME")) or code,
        "primary_industry": _clean(selected.get("F004V")),
        "secondary_industry": _clean(selected.get("F005V")),
        "detail_industry": _clean(selected.get("F006V")),
        "fine_industry": _clean(selected.get("F007V")),
        "classification_standard": _clean(selected.get("F002V")) or "未命名分类标准",
        "classification_code": str(selected.get("F001V") or ""),
        "classification_industry_code": _clean(selected.get("F003V")),
        "classification_changed_at": _changed_at(selected) or None,
        "source_name": SOURCE_NAME,
        "source_reference": SOURCE_REFERENCE,
    }


class CninfoIndustryProvider(BaseFundProvider):
    name = "cninfo-industry"
    priority = 5
    capabilities = {"stock_industry_classification"}

    def __init__(
        self,
        *,
        token_factory: Callable[[], str] | None = None,
        post: Callable[..., Any] | None = None,
        request_timeout: float = 15,
        max_workers: int = 5,
    ):
        self.token_factory = token_factory or _CninfoTokenFactory()
        self.post = post or requests.post
        self.request_timeout = request_timeout
        self.max_workers = max(1, max_workers)

    def fetch(self, capability: str, **kwargs: Any) -> ProviderResult:
        if capability != "stock_industry_classification":
            raise ProviderUnavailable(f"巨潮行业分类不支持能力：{capability}")
        codes = sorted({str(code).zfill(6) for code in kwargs.get("codes") or [] if code})
        if not codes:
            raise ProviderUnavailable("巨潮行业分类缺少股票代码")

        # py_mini_racer must stay on this calling thread. Only HTTP runs in workers.
        requests_with_tokens = [(code, self.token_factory()) for code in codes]
        rows_by_code: dict[str, list[dict[str, Any]]] = {}
        failed_codes: list[str] = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(codes))) as executor:
            future_to_code = {
                executor.submit(self._fetch_one, code, token): code
                for code, token in requests_with_tokens
            }
            for future in as_completed(future_to_code):
                code = future_to_code[future]
                try:
                    rows = future.result()
                except Exception:
                    failed_codes.append(code)
                    continue
                if rows:
                    rows_by_code[code] = rows
                else:
                    failed_codes.append(code)

        classifications: dict[str, dict[str, Any]] = {}
        for code, rows in rows_by_code.items():
            selected = _select_classification(code, rows)
            if selected is None:
                failed_codes.append(code)
            else:
                classifications[code] = selected
        if not classifications:
            raise ProviderUnavailable("巨潮行业分类未返回任何可核验股票记录")

        dates = [
            str(item.get("classification_changed_at"))
            for item in classifications.values()
            if item.get("classification_changed_at")
        ]
        failed = sorted(set(failed_codes))
        return ProviderResult(
            data={
                "classifications": classifications,
                "failed_codes": failed,
                "requested_codes": codes,
            },
            source_name=SOURCE_NAME,
            source_reference=SOURCE_REFERENCE,
            data_type="stock_industry_classification",
            as_of_date=max(dates) if dates else None,
            status="disclosed",
            message=(
                "按申万现行、申万旧版、巨潮、中证、证监会/上市公司协会优先级选择最新分类"
                + (f"；{len(failed)} 只证券分类暂不可用" if failed else "")
            ),
        )

    def _fetch_one(self, code: str, token: str) -> list[dict[str, Any]]:
        response = self.post(
            SOURCE_REFERENCE,
            params={"scode": code, "sdate": "1990-01-01", "edate": "2099-12-31"},
            headers={
                "Accept": "*/*",
                "Accept-Enckey": token,
                "Origin": "https://webapi.cninfo.com.cn",
                "Referer": "https://webapi.cninfo.com.cn/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self.request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        records = payload.get("records") if isinstance(payload, dict) else None
        return records if isinstance(records, list) else []
