from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from .models import KeyField


_MONEY_RE = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>万亿|亿元|万元|元)")
_PERCENT_RE = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*(?:%|％|个百分点)")
_QUANTITY_RE = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>条产线|台设备|万吨|万片|座|项|家)")
_DATE_RE = re.compile(r"(?P<year>20\d{2})[年\-/\.](?P<month>1[0-2]|0?[1-9])[月\-/\.](?P<day>3[01]|[12]\d|0?[1-9])日?")
_REPORT_RE = re.compile(r"(?P<year>20\d{2})年(?P<period>年度|半年度|第一季度|第二季度|第三季度|第四季度|一季度|二季度|三季度|四季度)")
_CYCLE_RE = re.compile(r"(?:建设周期|建设期|工期|周期)\s*(?P<number>[一二三四五六七八九十\d]+)\s*(?P<unit>年|个月|月|天)")
_COUNTERPARTY_RE = re.compile(r"(?:与|向)(?P<name>[\u3400-\u9fffA-Za-z0-9·（）()]{2,32}?)(?:签署|签订|采购|供应|合作)")
_CN_NUMBER = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _money_value(number: str, unit: str) -> str | None:
    multipliers = {"元": Decimal(1), "万元": Decimal(10_000), "亿元": Decimal(100_000_000), "万亿": Decimal(1_000_000_000_000)}
    try:
        value = Decimal(number) * multipliers[unit]
    except (InvalidOperation, KeyError):
        return None
    return f"CNY:{_decimal_text(value)}"


def _cycle_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value in _CN_NUMBER:
        return _CN_NUMBER[value]
    if value.startswith("十") and len(value) == 2 and value[1] in _CN_NUMBER:
        return 10 + _CN_NUMBER[value[1]]
    if len(value) == 2 and value[0] in _CN_NUMBER and value[1] == "十":
        return _CN_NUMBER[value[0]] * 10
    return None


def extract_key_fields(title: str, excerpt: str) -> tuple[KeyField, ...]:
    text = unicodedata.normalize("NFKC", f"{title or ''}。{excerpt or ''}")
    found: list[tuple[int, int, KeyField]] = []
    for match in _MONEY_RE.finditer(text):
        normalized = _money_value(match.group("number"), match.group("unit"))
        if normalized:
            found.append((0, match.start(), KeyField("money", match.group(0), normalized)))
    for match in _PERCENT_RE.finditer(text):
        normalized = f"{_decimal_text(Decimal(match.group('number')))}%"
        found.append((1, match.start(), KeyField("percentage", match.group(0), normalized)))
    for match in _QUANTITY_RE.finditer(text):
        number = _decimal_text(Decimal(match.group("number")))
        unit = {"条产线": "产线"}.get(match.group("unit"), match.group("unit"))
        found.append((2, match.start(), KeyField("quantity", match.group(0), f"{number}:{unit}")))
    for match in _REPORT_RE.finditer(text):
        periods = {"年度": "FY", "半年度": "H1", "第一季度": "Q1", "一季度": "Q1", "第二季度": "Q2", "二季度": "Q2", "第三季度": "Q3", "三季度": "Q3", "第四季度": "Q4", "四季度": "Q4"}
        found.append((3, match.start(), KeyField("report_period", match.group(0), f"{match.group('year')}-{periods[match.group('period')]}")))
    for match in _DATE_RE.finditer(text):
        normalized = f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
        context = text[max(0, match.start() - 10):min(len(text), match.end() + 10)]
        field_name = "effective_date" if "生效" in context or "实施" in context else "date"
        found.append((4 if field_name == "effective_date" else 5, match.start(), KeyField(field_name, match.group(0), normalized)))
    for match in _CYCLE_RE.finditer(text):
        number = _cycle_number(match.group("number"))
        if number is not None:
            unit = {"年": "year", "个月": "month", "月": "month", "天": "day"}[match.group("unit")]
            found.append((6, match.start(), KeyField("build_cycle", match.group(0), f"{number}:{unit}")))
    for match in _COUNTERPARTY_RE.finditer(text):
        name = match.group("name").strip(" ，,。；;")
        found.append((7, match.start(), KeyField("counterparty", name, unicodedata.normalize("NFKC", name).casefold())))
    result: list[KeyField] = []
    seen: set[tuple[str, str]] = set()
    for _, _, row in sorted(found, key=lambda item: (item[0], item[1])):
        key = (row.field_name, row.normalized_value)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return tuple(result)


def extract_core_claim(title: str, excerpt: str) -> str:
    value = unicodedata.normalize("NFKC", (title or "").strip())
    if not value:
        value = re.split(r"[。！？!?]", unicodedata.normalize("NFKC", excerpt or ""), maxsplit=1)[0]
    for field in extract_key_fields(value, ""):
        value = value.replace(field.raw_value, "")
    value = re.sub(r"[\s，,。；;：:（）()【】\[\]]+", "", value)
    return value
