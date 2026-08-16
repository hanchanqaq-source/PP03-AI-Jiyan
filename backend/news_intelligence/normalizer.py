from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

from news_intelligence.models import NewsSourceItem


TRACK_TAGS = {
    "ai": ("artificial-intelligence", "人工智能"),
    "semi": ("semiconductor", "半导体"),
    "robot": ("robotics", "机器人"),
    "auto": ("new-energy-vehicle", "新能源汽车"),
    "energy": ("new-energy", "新能源"),
    "bio": ("healthcare", "医疗"),
    "space": ("commercial-space", "商业航天"),
    "security": ("technology", "科技"),
    "tech": ("technology", "科技"),
    "consumer": ("consumer-electronics", "消费电子"),
    "macro": ("finance", "金融"),
    "science": ("technology", "科技"),
}

SPECIFIC_TAGS = (
    ("storage", "存储", ("存储", "dram", "nand", "hbm", "memory")),
    ("semiconductor-equipment", "半导体设备", ("半导体设备", "光刻", "刻蚀")),
    ("chip-design", "芯片设计", ("芯片设计", "ic设计", "数字芯片")),
    ("ai-computing", "AI算力", ("ai算力", "算力", "gpu", "服务器")),
    ("software", "软件", ("软件", "software")),
    ("robotics", "机器人", ("机器人", "具身智能", "robotics", "robot")),
)

POLICY_RE = re.compile(r"政策|监管|国务院|部委|规则|规范|法案|禁令|制裁|policy|regulation|government", re.I)
FUND_NOTICE_RE = re.compile(
    r"基金(?:公司)?公告|基金.{0,24}(?:定期报告|季度报告|年度报告|招募说明书|份额)|(?:定期报告|季度报告|年度报告|招募说明书|份额).{0,24}基金|fund notice",
    re.I,
)
COMPANY_RE = re.compile(r"公司|公告|财报|业绩|订单|量产|收购|并购|发布|披露|earnings|revenue|company|announc", re.I)
DEEP_RE = re.compile(r"深度|研报|白皮书|研究报告|产业分析|long read|analysis|report", re.I)
ENTITY_VERBS = r"发布|公告|披露|上调|下调|收购|并购|签署|获得|推出|量产|暂停|回应"
ENTITY_SUFFIX_RE = re.compile(r"(?:20\d{2}年)?(?:半年度报告|年度报告|季度报告|半年报|年报|财报|业绩|产品报价)$")
GENERIC_ANCHORS = {
    "行业", "产业", "市场", "部门", "监管部门", "公司", "企业", "机构", "多家企业",
    "ai", "apod", "ceo", "ipo",
}


def normalize_title(value: str) -> str:
    decoded = html.unescape(value or "")
    normalized = unicodedata.normalize("NFKC", decoded).lower()
    return re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)


def _tokens(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", html.unescape(value or "")).lower()
    tokens = set(re.findall(r"[a-z][a-z0-9-]{1,}|\d{2,}", normalized))
    for run in re.findall(r"[\u3400-\u9fff]{2,}", normalized):
        tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return frozenset(tokens)


def _anchors(title: str) -> frozenset[str]:
    anchors: set[str] = set()
    for match in re.finditer(rf"([\u3400-\u9fffA-Za-z0-9]{{2,20}}?)(?={ENTITY_VERBS})", title):
        candidate = ENTITY_SUFFIX_RE.sub("", match.group(1)).strip()
        if len(candidate) >= 2 and candidate not in GENERIC_ANCHORS:
            anchors.add(candidate.lower())
    anchors.update(
        token.lower()
        for token in re.findall(r"\b[A-Z][A-Z0-9.-]{1,}\b", title)
        if token.lower() not in GENERIC_ANCHORS
    )
    return frozenset(anchors)


def _parse_datetime(value, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            parsed = fallback
    else:
        parsed = fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _category(blob: str) -> str:
    if POLICY_RE.search(blob):
        return "policy"
    if FUND_NOTICE_RE.search(blob):
        return "fund_notice"
    if COMPANY_RE.search(blob):
        return "company"
    if DEEP_RE.search(blob):
        return "deep_content"
    return "industry"


def _related_tags(track_key: str, blob: str) -> tuple[tuple[str, str], ...]:
    tags: list[tuple[str, str]] = []
    base = TRACK_TAGS.get(track_key)
    if base:
        tags.append(base)
    lowered = blob.lower()
    for tag_id, name, keywords in SPECIFIC_TAGS:
        if any(keyword.lower() in lowered for keyword in keywords) and tag_id not in {tag[0] for tag in tags}:
            tags.append((tag_id, name))
    return tuple(tags)


def normalize_radar(radar: dict, now: datetime) -> list[NewsSourceItem]:
    fallback_fetched = _parse_datetime(radar.get("generated_at"), now)
    default_status = str(radar.get("cache_status") or "cache")
    sources: list[NewsSourceItem] = []
    for track in radar.get("industries") or []:
        track_key = str(track.get("key") or "")
        track_name = str(track.get("name") or track_key)
        for raw in track.get("items") or []:
            title = html.unescape(str(raw.get("title") or "")).strip()
            if not title:
                continue
            summary = html.unescape(str(raw.get("summary_or_excerpt") or raw.get("summary") or "")).strip()
            original_url = str(raw.get("original_url") or raw.get("url") or "").strip()
            source_url = str(raw.get("source_url") or "").strip()
            published = _parse_datetime(raw.get("published_at") or raw.get("ts"), fallback_fetched)
            fetched = _parse_datetime(raw.get("fetched_at"), fallback_fetched)
            blob = f"{title} {summary}"
            sources.append(NewsSourceItem(
                source_name=str(raw.get("source_name") or raw.get("source") or "未知公开来源"),
                source_url=source_url,
                original_url=original_url,
                published_at=published,
                fetched_at=fetched,
                title=title,
                summary=summary,
                language=str(raw.get("language") or "unknown"),
                region=str(raw.get("region") or "unknown"),
                track_key=track_key,
                track_name=track_name,
                category=_category(blob),
                normalized_title=normalize_title(title),
                tokens=_tokens(blob),
                anchors=_anchors(title),
                related_tags=_related_tags(track_key, blob),
                source_domain=(urlparse(original_url).hostname or urlparse(source_url).hostname or "").lower(),
                data_status=str(raw.get("data_status") or default_status),
            ))
    return sources
