"""资讯雷达数据层 —— 移植自 investment-news。

抓 12 赛道 108 个公开 RSS 源 → 合规过滤（赌/预测市场/加密/色情）+ 最近 N 天
+ 按赛道分组、时间倒序。纯标准库 + 线程池，零 key、零个股字段。

AI「今日要点」不在此模块——复用 Vibe-Research 的可插拔 AI 层（前端调 /api/chat，
把某赛道资讯打包给用户自己的模型提炼）。本模块只出客观资讯。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(HERE, "news_sources.json")
CACHE_DIR = os.environ.get("VR_NEWS_CACHE_DIR") or os.path.join(HERE, ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "radar.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BEIJING = timezone(timedelta(hours=8))


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _parse_dt(s: str):
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fetch_source(src: dict, per: int, cutoff, redline: list[str]):
    """抓单个 RSS 源；失败只返回来源级状态，不抛出整页异常。"""
    try:
        fetched_at = datetime.now(BEIJING).isoformat(timespec="seconds")
        req = urllib.request.Request(src["url"], headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*",
        })
        with urllib.request.urlopen(req, timeout=14) as r:
            raw = r.read()
        root = ET.fromstring(raw)
        out = []
        for n in [e for e in root.iter() if _local(e.tag) in ("item", "entry")]:
            if len(out) >= per:
                break
            d = {
                "title": "", "url": "", "time": "", "ts": 0, "summary": "", "source": src["name"],
                "source_name": src["name"], "source_url": src["url"], "original_url": "",
                "published_at": None, "fetched_at": fetched_at, "summary_or_excerpt": "",
                "language": src.get("language") or "unknown", "region": src.get("region") or "unknown",
                "data_status": "realtime",
            }
            rawtime = ""
            for c in n:
                t = _local(c.tag)
                if t == "title" and not d["title"]:
                    d["title"] = (c.text or "").strip()
                elif t == "link" and not d["url"]:
                    d["url"] = c.get("href") or (c.text or "").strip()
                elif t in ("pubDate", "published", "updated", "date") and not rawtime:
                    rawtime = (c.text or "").strip()
                elif t in ("description", "summary", "content") and not d["summary"]:
                    d["summary"] = _strip_html(c.text or "")[:160]
            if not d["title"]:
                continue
            blob = (d["title"] + " " + d["summary"]).lower()
            if any(k in blob for k in redline):  # 合规红线过滤
                continue
            dt = _parse_dt(rawtime)
            if dt is not None:
                if cutoff and dt < cutoff:
                    continue
                d["time"] = dt.astimezone(BEIJING).strftime("%m-%d %H:%M")
                d["ts"] = int(dt.timestamp())
                d["published_at"] = dt.astimezone(BEIJING).isoformat(timespec="seconds")
            else:
                d["time"] = "—"
            d["original_url"] = d["url"]
            d["summary_or_excerpt"] = d["summary"]
            out.append(d)
        return {"status": "ok", "items": out}
    except Exception:
        return {"status": "failed", "items": []}


def _cached_items_for_source(cache: dict | None, industry_key: str, src: dict) -> list[dict]:
    if not cache:
        return []
    industry = next((row for row in cache.get("industries") or [] if row.get("key") == industry_key), None)
    if not industry:
        return []
    out = []
    for item in industry.get("items") or []:
        if item.get("source_url") == src["url"] or item.get("source_name") == src["name"] or item.get("source") == src["name"]:
            copied = dict(item)
            copied["data_status"] = "stale"
            out.append(copied)
    return out


def fetch_radar() -> dict:
    """抓全部源，返回 12 赛道数据并落盘缓存。"""
    with open(SOURCES_FILE, encoding="utf-8") as source_file:
        cfg = json.load(source_file)
    previous = load_cache()
    days = cfg.get("fetch", {}).get("recent_days", 7)
    per = cfg.get("fetch", {}).get("per_source", 6)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    redline = [k.lower() for k in cfg.get("redline_keywords", [])]

    byhint: dict[str, list] = {}
    for s in cfg["sources"]:
        byhint.setdefault(s["hint"], []).append(s)

    industries, tasks = [], []
    for i, ind in enumerate(cfg["industries"]):
        pool = byhint.get(ind["key"], [])
        industries.append({"key": ind["key"], "name": ind["name"], "accent": ind["accent"], "total": len(pool), "items": []})
        for s in pool:
            tasks.append((i, s))

    with ThreadPoolExecutor(max_workers=40) as ex:
        results = list(ex.map(lambda t: (t[0], t[1], _fetch_source(t[1], per, cutoff, redline)), tasks))

    failed = 0
    succeeded = 0
    source_statuses = []
    for idx, src, result in results:
        items = result["items"]
        source_statuses.append({
            "source_name": src["name"],
            "source_url": src["url"],
            "status": result["status"],
            "item_count": len(items),
        })
        if result["status"] == "failed":
            failed += 1
            industries[idx]["items"].extend(_cached_items_for_source(previous, industries[idx]["key"], src))
            continue
        succeeded += 1
        industries[idx]["items"].extend(items)

    if succeeded == 0:
        if previous:
            fallback = json.loads(json.dumps(previous, ensure_ascii=False))
            fallback["cache_status"] = "stale"
            fallback["source_statuses"] = source_statuses
            fallback.setdefault("stats", {})["failed_sources"] = failed
            return fallback
        fallback = skeleton()
        fallback["cache_status"] = "source_failure"
        fallback["source_statuses"] = source_statuses
        fallback["stats"]["failed_sources"] = failed
        return fallback

    for ind in industries:
        ind["items"].sort(key=lambda x: x.get("ts", 0), reverse=True)

    data = {
        "generated_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "recent_days": days,
        "industries": industries,
        "stats": {"industries": len(cfg["industries"]), "total_sources": len(cfg["sources"]), "failed_sources": failed},
        "cache_status": "partial" if failed else "realtime",
        "source_statuses": source_statuses,
    }
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)  # 原子改名，防两次并发刷新交错写坏缓存
    return data


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        generated_at = _parse_dt(str(data.get("generated_at") or ""))
        recent_days = int(data.get("recent_days") or 7)
        is_stale = bool(generated_at and datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc) > timedelta(days=recent_days))
        data["cache_status"] = "stale" if is_stale else "cache"
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def skeleton() -> dict:
    """无缓存时返回赛道骨架（空 items），前端提示点刷新。"""
    cfg = json.load(open(SOURCES_FILE, encoding="utf-8"))
    byhint: dict[str, int] = {}
    for s in cfg["sources"]:
        byhint[s["hint"]] = byhint.get(s["hint"], 0) + 1
    return {
        "generated_at": None,
        "recent_days": cfg.get("fetch", {}).get("recent_days", 7),
        "industries": [{"key": i["key"], "name": i["name"], "accent": i["accent"], "total": byhint.get(i["key"], 0), "items": []} for i in cfg["industries"]],
        "stats": {"industries": len(cfg["industries"]), "total_sources": len(cfg["sources"]), "failed_sources": 0},
        "cache_status": "empty",
        "source_statuses": [],
    }


def get_radar(force: bool = False) -> dict:
    if force:
        return fetch_radar()
    return load_cache() or skeleton()
