from __future__ import annotations

import re
from typing import Any

from news_intelligence.models import MarketNewsEvent


COMPANY_ACTIONS = (
    "发布", "公告", "披露", "宣布", "收购", "并购", "签署", "获得", "推出", "量产", "暂停",
    "回应", "上调", "下调", "财报", "业绩", "营收", "融资", "上市", "shares", "stock", "earnings",
    "表示", "传来",
)


def _event_blob(event: MarketNewsEvent) -> str:
    return " ".join(
        [event.title, event.summary]
        + [source.title for source in event.sources]
        + [source.summary for source in event.sources]
    ).lower()


def _event_texts(event: MarketNewsEvent) -> list[str]:
    return [event.title, event.summary] + [source.title for source in event.sources] + [source.summary for source in event.sources]


def _complete_code_match(code: str, blob: str) -> bool:
    return bool(code and re.search(rf"(?<![a-z0-9]){re.escape(code.lower())}(?![a-z0-9])", blob))


def _company_name_match(name: str, event: MarketNewsEvent) -> bool:
    candidate = name.strip()
    if len(candidate) < 2:
        return False
    if re.search(r"[a-z]", candidate, re.I):
        return any(_complete_code_match(candidate, text.lower()) for text in _event_texts(event))
    action = "|".join(re.escape(word) for word in COMPANY_ACTIONS)
    escaped = re.escape(candidate)
    connector = r"(?:公司|集团|股份有限公司|股份)?(?:\s*[：:，,·-]\s*)?"
    entity_char = r"\u3400-\u9fffA-Za-z0-9"
    punctuation = r"\s，。；：、,:;（）()《》\[\]【】"
    company_tail = r"(?:的)?(?:目标价|股价|市值|财报|业绩|营收|产品|芯片|模块)"
    patterns = (
        rf"(?<![{entity_char}]){escaped}(?![{entity_char}])",
        rf"(?<![{entity_char}]){escaped}{connector}(?:{action})",
        rf"(?:{action})(?:\s*[：:，,·-]\s*)?{escaped}(?:公司|集团|股份有限公司|股份)?(?=$|[{punctuation}]|{company_tail})",
    )
    return any(re.search(pattern, text, re.I) for text in _event_texts(event) for pattern in patterns)


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
    relate_holding_evidence(events, portfolio_analysis)
    apply_watch_relations(events, selected_tag_ids)
    return events


def relate_holding_evidence(
    events: list[MarketNewsEvent],
    portfolio_analysis: dict[str, Any] | None,
) -> list[MarketNewsEvent]:
    funds = (portfolio_analysis or {}).get("holdings") or []
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
                if _company_name_match(stock_name, event):
                    direct_matches.append(_evidence_row(
                        fund_code, fund_name, disclosure_date, stock, classification, "company", stock_name, holdings_meta,
                    ))
                    continue
                if _complete_code_match(stock_code, blob):
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
                relationship_tags = [
                    tag for tag in event.tag_evidence if tag.get("provenance") == "article_text"
                ]
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
    return events


def apply_watch_relations(events: list[MarketNewsEvent], selected_tag_ids: list[str]) -> list[MarketNewsEvent]:
    selected = set(selected_tag_ids)
    for event in events:
        if event.relation_level != "none":
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
