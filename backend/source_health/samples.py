from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SAMPLE_CATEGORIES = (
    "主动混合型",
    "股票型",
    "指数型",
    "ETF联接",
    "债券型",
    "货币型",
    "QDII",
    "FOF",
    "新成立基金",
    "公开披露不完整基金",
)

DEFAULT_SOURCE_NAME = "AKShare / 东方财富基金目录"
DEFAULT_OUTPUT = Path(__file__).with_name("source-health-samples.json")


def _catalog_rows(catalog: Any) -> list[dict[str, str]]:
    if hasattr(catalog, "iterrows"):
        raw_rows: Iterable[Any] = (row for _, row in catalog.iterrows())
    else:
        raw_rows = catalog or []

    rows = []
    for raw in raw_rows:
        if isinstance(raw, dict):
            code = raw.get("code") or raw.get("基金代码")
            name = raw.get("name") or raw.get("基金简称") or raw.get("基金名称")
            fund_type = raw.get("fund_type") or raw.get("基金类型")
        else:
            values = list(raw.iloc[:5]) if hasattr(raw, "iloc") else list(raw)
            if len(values) < 4:
                continue
            code, name, fund_type = values[0], values[2], values[3]
        normalized_code = str(code or "").strip().zfill(6)
        if len(normalized_code) != 6 or not normalized_code.isdigit():
            continue
        rows.append({
            "code": normalized_code,
            "name": str(name or normalized_code).strip(),
            "fund_type": str(fund_type or "").strip(),
        })
    return sorted(rows, key=lambda row: row["code"])


def _matches(category: str, fund_type: str) -> bool:
    upper = fund_type.upper()
    if category == "主动混合型":
        return fund_type.startswith("混合型") and "指数" not in fund_type
    if category == "股票型":
        return fund_type.startswith("股票型") and "指数" not in fund_type and "联接" not in fund_type
    if category == "指数型":
        return fund_type.startswith("指数型") and "联接" not in fund_type
    if category == "ETF联接":
        return "联接" in fund_type
    if category == "债券型":
        return fund_type.startswith("债券型")
    if category == "货币型":
        return fund_type.startswith("货币型")
    if category == "QDII":
        return "QDII" in upper
    if category == "FOF":
        return "FOF" in upper
    return False


def _profile_data(result: Any) -> dict[str, Any] | None:
    data = getattr(result, "data", result)
    return data if isinstance(data, dict) and data else None


def _unavailable(category: str, reason: str, source_name: str) -> dict[str, Any]:
    return {
        "category": category,
        "sample_status": "unavailable",
        "reason": reason,
        "source_name": source_name,
    }


def select_public_samples(
    catalog: Any,
    profile_fetcher: Callable[[str], Any],
    *,
    verified_at: str,
    source_name: str = DEFAULT_SOURCE_NAME,
) -> list[dict[str, Any]]:
    """Select the lowest-code profile-verified row within each public fund type."""
    rows = _catalog_rows(catalog)
    selected: list[dict[str, Any]] = []
    for category in SAMPLE_CATEGORIES:
        if category == "新成立基金":
            selected.append(_unavailable(
                category,
                "公开基金目录不含可用于可靠判定新成立基金的成立日期字段",
                source_name,
            ))
            continue
        if category == "公开披露不完整基金":
            selected.append(_unavailable(
                category,
                "公开基金目录与基础 profile 不足以可靠证明披露不完整",
                source_name,
            ))
            continue

        candidates = [row for row in rows if _matches(category, row["fund_type"])]
        if not candidates:
            selected.append(_unavailable(
                category,
                "公开基金目录未找到可按基金类型可靠匹配的条目",
                source_name,
            ))
            continue

        verified = None
        for candidate in candidates:
            try:
                profile = _profile_data(profile_fetcher(candidate["code"]))
            except Exception:
                profile = None
            profile_code = str((profile or {}).get("code") or "").strip().zfill(6)
            if profile is not None and profile_code == candidate["code"]:
                verified = {
                    "category": category,
                    "code": candidate["code"],
                    "name": candidate["name"],
                    "fund_type": candidate["fund_type"],
                    "verified_at": verified_at,
                    "source_name": source_name,
                    "sample_status": "available",
                }
                break
        selected.append(verified or _unavailable(
            category,
            f"按代码升序尝试的 {len(candidates)} 个匹配条目均未成功取得公开 profile",
            source_name,
        ))
    return selected


def _write_json_atomic(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def discover_public_samples(output: str | Path = DEFAULT_OUTPUT) -> list[dict[str, Any]]:
    from fund_data.providers.akshare_provider import AkshareEastmoneyProvider, _bounded_requests

    provider = AkshareEastmoneyProvider(request_timeout=15)
    with _bounded_requests(provider.request_timeout):
        catalog = provider.ak.fund_name_em()
    verified_at = datetime.now(timezone.utc).isoformat()
    samples = select_public_samples(
        catalog,
        lambda code: provider.fetch("profile", code=code),
        verified_at=verified_at,
        source_name=DEFAULT_SOURCE_NAME,
    )
    _write_json_atomic(Path(output), samples)
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover public fund health-check samples")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    samples = discover_public_samples(args.output)
    summary = {
        "output": str(Path(args.output).resolve()),
        "available": sum(row.get("sample_status") == "available" for row in samples),
        "unavailable": sum(row.get("sample_status") == "unavailable" for row in samples),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
