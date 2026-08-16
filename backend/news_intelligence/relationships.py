from __future__ import annotations

import re
from typing import Any

from news_intelligence.models import MarketNewsEvent


def _event_blob(event: MarketNewsEvent) -> str:
    return " ".join(
        [event.title, event.summary]
        + [source.title for source in event.sources]
        + [source.summary for source in event.sources]
    ).lower()


def _classification_text(evidence: dict[str, Any]) -> str:
    return " / ".join(str(evidence.get(field) or "") for field in (
        "primary_industry", "secondary_industry", "detail_industry", "fine_industry",
    ))


def _evidence_row(
    fund_code: str,
    fund_name: str,
    disclosure_date: str | None,
    stock: dict[str, Any],
    classification: dict[str, Any],
    matched_kind: str,
    matched_value: str,
    holding_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = holding_meta if matched_kind in {"company", "company_code", "company_ticker"} else classification
    return {
        "fund_code": fund_code,
        "fund_name": fund_name,
        "holding_disclosure_date": classification.get("holding_disclosure_date") or disclosure_date,
        "stock_code": str(stock.get("stock_code") or classification.get("stock_code") or ""),
        "stock_name": str(stock.get("stock_name") or classification.get("stock_name") or ""),
        "industry_classification": _classification_text(classification),
        "classification_standard": str(classification.get("classification_standard") or ""),
        "matched_kind": matched_kind,
        "matched_value": matched_value,
        "source_name": str(source.get("source_name") or ""),
        "source_reference": str(source.get("source_reference") or ""),
    }


def _add_unique(rows: list[dict[str, Any]], row: dict[str, Any], keys: tuple[str, ...]) -> None:
    signature = tuple(row.get(key) for key in keys)
    if all(tuple(existing.get(key) for key in keys) != signature for existing in rows):
        rows.append(row)


def relate_events(
    events: list[MarketNewsEvent],
    portfolio_analysis: dict[str, Any] | None,
    selected_tag_ids: list[str],
) -> list[MarketNewsEvent]:
    funds = (portfolio_analysis or {}).get("holdings") or []
    selected = set(selected_tag_ids)
    for event in events:
        event.related_companies = []
        event.related_funds = []
        event.relation_evidence = []
        event.relation_level = "none"
        event.confidence = "unavailable"
        event.impact_basis = []
        blob = _event_blob(event)
        direct_matches: list[dict[str, Any]] = []
        industry_matches: list[dict[str, Any]] = []

        for fund in funds:
            analysis = fund.get("analysis") or {}
            holdings_section = analysis.get("holdings") or {}
            disclosed = holdings_section.get("data") or {}
            holdings_meta = holdings_section.get("meta") or {}
            disclosure_date = disclosed.get("disclosure_date")
            exposure = ((analysis.get("industry_exposure") or {}).get("data") or {})
            classifications = {
                str(row.get("stock_code") or ""): row
                for row in exposure.get("holding_industry_evidence") or []
            }
            fund_code = str(fund.get("code") or "")
            fund_name = str(fund.get("name") or fund_code)
            for stock in disclosed.get("holdings") or []:
                stock_code = str(stock.get("stock_code") or "")
                stock_name = str(stock.get("stock_name") or "").strip()
                classification = classifications.get(stock_code, {})
                if stock_name and len(stock_name) >= 2 and stock_name.lower() in blob:
                    direct_matches.append(_evidence_row(
                        fund_code, fund_name, disclosure_date, stock, classification, "company", stock_name, holdings_meta,
                    ))
                    continue
                if stock_code and stock_code.lower() in blob:
                    direct_matches.append(_evidence_row(
                        fund_code, fund_name, disclosure_date, stock, classification, "company_code", stock_code, holdings_meta,
                    ))
                    continue
                ticker = stock_code.lstrip("0")
                if ticker and ticker.isalpha() and re.search(rf"(?<![a-z0-9]){re.escape(ticker.lower())}(?![a-z0-9])", blob):
                    direct_matches.append(_evidence_row(
                        fund_code, fund_name, disclosure_date, stock, classification, "company_ticker", ticker, holdings_meta,
                    ))
                    continue
                classification_blob = _classification_text(classification).lower()
                relationship_tags = event.related_tags[1:] if len(event.related_tags) > 1 else event.related_tags
                for tag in relationship_tags:
                    tag_name = str(tag.get("name") or "").lower()
                    if tag_name and tag_name in classification_blob:
                        industry_matches.append(_evidence_row(
                            fund_code, fund_name, disclosure_date, stock, classification, "industry", str(tag.get("name") or ""),
                        ))
                        break

        chosen = direct_matches or industry_matches
        if chosen:
            event.relation_level = "direct_holding" if direct_matches else "industry_relation"
            event.confidence = "high" if direct_matches else "medium"
            event.impact_basis = [
                "新闻直接命中最新公开重仓公司" if direct_matches else "新闻命中重仓公司来源化行业分类"
            ]
            event.relation_evidence = chosen
            for row in chosen:
                _add_unique(event.related_funds, {
                    "fund_code": row["fund_code"], "fund_name": row["fund_name"],
                }, ("fund_code",))
                _add_unique(event.related_companies, {
                    "stock_code": row["stock_code"], "stock_name": row["stock_name"],
                }, ("stock_code",))
            continue

        watched = next((tag for tag in event.related_tags if tag.get("id") in selected), None)
        if watched:
            event.relation_level = "watch_tag"
            event.confidence = "low"
            event.impact_basis = ["新闻命中用户在市场资讯页主动选择的标签"]
            event.relation_evidence = [{
                "matched_kind": "watch_tag",
                "matched_value": watched.get("name") or "",
                "tag_id": watched.get("id") or "",
            }]
    return events
