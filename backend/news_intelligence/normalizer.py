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
    ("semiconductor-materials", "半导体材料", ("半导体材料", "光刻胶", "电子特气")),
    ("advanced-packaging", "先进封装", ("先进封装", "chiplet")),
    ("chip-design", "芯片设计", ("芯片设计", "ic设计", "数字芯片")),
    ("pcb", "PCB", ("pcb", "印制电路板")),
    ("ai-computing", "AI算力", ("ai算力", "算力", "gpu", "服务器")),
    ("optical-module", "光模块", ("光模块", "cpo")),
    ("liquid-cooling", "液冷", ("液冷", "liquid cooling")),
    ("data-center", "数据中心", ("数据中心", "data center")),
    ("software", "软件", ("软件", "software")),
    ("robotics", "机器人", ("机器人", "人形机器人", "具身智能", "自动化", "robotics", "robot", "automation")),
    ("reducer", "减速器", ("减速器", "谐波减速器")),
    ("servo-system", "伺服系统", ("伺服系统", "伺服电机")),
    ("machine-vision", "机器视觉", ("机器视觉", "machine vision")),
    ("medical-device", "医疗器械", ("医疗器械", "medical device")),
    ("innovative-drug", "创新药", ("创新药", "innovative drug")),
    ("consumer-electronics", "消费电子", ("消费电子", "consumer electronics")),
    ("food-beverage", "食品饮料", ("食品饮料", "food and beverage")),
    ("new-energy-vehicle", "新能源汽车", ("新能源汽车", "新能源车", "electric vehicle", "ev")),
    ("energy-storage", "储能", ("储能", "energy storage")),
    ("photovoltaic", "光伏", ("光伏", "photovoltaic", "solar")),
    ("industrial-automation", "工业自动化", ("工业自动化", "industrial automation")),
    ("commercial-space", "商业航天", ("商业航天", "commercial space")),
    ("cyclical-resources", "周期资源", ("周期资源", "cyclical resources")),
    ("nonferrous", "有色金属", ("有色金属", "nonferrous")),
    ("real-estate", "房地产", ("房地产", "real estate")),
    ("transportation", "交通运输", ("交通运输", "transportation")),
    ("overseas-market", "海外市场", ("海外市场", "overseas market")),
    ("us-market", "美国市场", ("美国市场", "美股", "us market")),
    ("hk-market", "香港市场", ("香港市场", "港股", "hong kong market")),
    ("semiconductor", "半导体", ("半导体", "芯片", "集成电路", "晶圆", "封装", "semiconductor")),
    ("artificial-intelligence", "人工智能", ("人工智能", "artificial intelligence", "ai")),
    ("sensor", "传感器", ("传感器", "sensor")),
    ("healthcare", "医疗", ("医疗", "医药", "生物医药", "healthcare")),
    ("consumer", "消费", ("消费", "consumer")),
    ("finance", "金融", ("金融", "finance")),
    ("banking", "银行", ("银行", "banking")),
    ("insurance", "保险", ("保险", "insurance")),
    ("new-energy", "新能源", ("新能源", "new energy")),
    ("advanced-manufacturing", "高端制造", ("高端制造", "advanced manufacturing")),
    ("defense", "军工", ("军工", "defense")),
    ("coal", "煤炭", ("煤炭", "coal")),
    ("media", "传媒", ("传媒", "media")),
    ("agriculture", "农业", ("农业", "agriculture")),
    ("utilities", "公用事业", ("公用事业", "utilities")),
    ("technology", "科技", ("科技", "technology")),
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


def _parse_datetime(value, fallback: datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and value > 0:
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            parsed = fallback
    else:
        parsed = fallback
    if parsed is not None and parsed.tzinfo is None:
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


def _related_tags(track_key: str, blob: str) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    tags: list[tuple[str, str]] = []
    text_tags: list[tuple[str, str]] = []
    base = TRACK_TAGS.get(track_key)
    if base:
        tags.append(base)
    lowered = blob.lower()
    for tag_id, name, keywords in SPECIFIC_TAGS:
        if any(_contains_keyword(lowered, keyword) for keyword in keywords):
            tag = (tag_id, name)
            text_tags.append(tag)
            if tag_id not in {existing[0] for existing in tags}:
                tags.append(tag)
    return tuple(tags), tuple(text_tags)


def _contains_keyword(lowered: str, keyword: str) -> bool:
    needle = keyword.lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9+.# -]*", needle):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", lowered))
    return needle in lowered


def _effective_data_status(container_status: str, item_status: str) -> str:
    if container_status == "stale":
        return "stale"
    if container_status == "cache":
        return "stale" if item_status == "stale" else "cache"
    if container_status == "partial":
        return item_status if item_status in {"realtime", "stale", "cache"} else "cache"
    if container_status == "realtime":
        return item_status if item_status in {"realtime", "stale"} else "realtime"
    return container_status or item_status or "cache"


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
            raw_published = raw.get("published_at")
            if not raw_published:
                raw_published = raw.get("ts")
            published = _parse_datetime(raw_published, None)
            fetched = _parse_datetime(raw.get("fetched_at"), fallback_fetched)
            if fetched is None:
                fetched = now
            blob = f"{title} {summary}"
            related_tags, text_related_tags = _related_tags(track_key, blob)
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
                related_tags=related_tags,
                text_related_tags=text_related_tags,
                source_domain=(urlparse(original_url).hostname or urlparse(source_url).hostname or "").lower(),
                data_status=_effective_data_status(default_status, str(raw.get("data_status") or default_status)),
            ))
    return sources
