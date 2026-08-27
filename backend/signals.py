"""产业信号数据层 —— 每期移植一个「一句话信号」小栏目。第一期：GPU 租金。

两个零鉴权公开端口，纯标准库：
- 500.farm（Vast.ai 公开市场数据的社区统计站，exporter 开源）：B200 / H100 / A100
  近一年逐日中位价序列（历史走势）+ 当前挂单卡数。**现货卡直接取曲线的最后一个点**
  ——同一次请求、同一条数据，两处数字严格一致。
  租金是算力冷热的价格侧证据：供应链数据只说明「货在走」，租金说明「装了之后卖不卖得掉」。
  ⚠️ 沿革：曾用 Vast bundles API 直取现货（挂单列表直接取中位），与曲线的统计算法
  （按机型切片各算中位再聚合）对同一时刻会算出**两个不同的「中位价」**（实测同屏
  $8.13 vs $6.95），2026-08-20 起改为从曲线终点派生，彻底消除同屏打架。
  ⚠️ p10/p90 分位指标经切片聚合后**不保序**（实测 H100 的 p10 聚合值 3.06 > 中位 2.40），
  不能当区间展示——现货卡改配挂单卡数（vastai_v2_gpu_count）做规模读数。
- Kalshi 公开事件合约（KXB200MS 系列）：B200 月均租金的阶梯合约 → 远期预期。
  按结算月分组；由阶梯反推**概率分布 / 隐含预期中位价 / 最可能区间**；
  已结算月份给出月均实际落点区间（可与历史曲线互证）。
  ⚠️ 结算依据是 **Ornn 跨平台指数的小时值整月算术平均**（官方结算源
  dashboard.ornnai.com），**不是 Vast**——与现货卡是两个市场、两种时间口径，
  数值不能直接对减，UI 里也刻意不把远期画进 Vast 历史曲线（跨口径拼线会误导）。

三条口径边界（防误读，UI 与 AI 工具输出都带着）：
1. 现货是 Vast 单一撮合市场**此刻**的挂单价（盘中会动）；远期合约按 Ornn 跨平台指数的
   **整月平均**结算——市场与时间口径都不同，两个数字不能直接对减。
2. 撮合市场报价分散 → 看**中位数**不看均价；某型号「无在租报价」是市场状态，
   不是数据故障。
3. **前沿卡紧与旧卡松可以同时为真**（B200 与 H100 价差长期数倍）——
   只看一根线，别下全市场结论。

本模块只呈现价格事实与公开合约报价，不产出「过剩 / 短缺」的结论——判断留给用户自己的 AI。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "signals_gpu.json")
# 随仓库分发的数据快照（发版前刷新一份拷进来）：clone 下来不用先等 40 秒刷新，
# 打开就有截至发布日的完整历史；用户点刷新后以 .cache 里自己拉的最新数据为准。
SEED_FILE = os.path.join(HERE, "data", "signals_gpu_seed.json")
SCHEMA = 4  # 缓存结构版本：结构变更时 +1，旧缓存/旧快照自动作废重抓

HOW_TO_READ = [
    "现货卡显示的就是走势曲线的最新一点——同一数据源、同一算法，两处数字必然一致；口径为 Vast 可租挂单按机型档位分组统计后聚合的中位价（含平台费，非逐张挂单的精确中位）。统计站数据有小时级延迟，卡上标注了观测时点。",
    "远期合约按 Ornn 跨平台指数的「整月平均」结算——与「此刻」的现货价是两个市场、两种时间口径，数字不能直接对减。",
    "撮合市场报价分散、盘中波动大，看中位数不看单一挂单；某型号暂无统计序列是市场状态，不是数据故障。",
    "前沿卡紧与旧卡松可以同时为真（B200 与 H100 价差长期数倍），只看一根线别下全市场结论。",
]

# ⚠️ 必须用**精确型号名**：写 "H100" 永远查不到（市场上挂的是 "H100 SXM" /
#    "H100 NVL" / "H100 PCIE" 细分名），看起来像「没人在租」，其实是查错了名字。
SPOT_GPUS = ("B200", "H100 SXM", "A100 SXM4")
SPOT_SOURCE = ("现货 = 走势曲线的最新采样点（与曲线同源同算法，数字严格一致）；"
               "另附当前市场挂单卡数做规模读数")

# 500.farm 的 Prometheus 查询代理（其站点前端同款入口，匿名可读；exporter 开源：
# github.com/500farm/prometheus-vastai）。查询表达式与其官方面板同源。
HIST_BASE = ("https://500.farm/vastai/grafana.v2/api/datasources/proxy/uid/"
             "EdgV2xcnz/api/v1")
# rented="no" = 当前可租的挂单；quantile(0.5, …) 跨机型切片（datacenter / 卡数段 /
# verified）聚合。⚠️ 切片等权——这是「切片中位的中位」，不是逐张挂单的精确中位，
# 文案上一律称「分组统计后聚合的中位」，别写成「全市场挂单中位价」。
HIST_QUERY = ('quantile(0.5, vastai_v2_ondemand_price_median_dollars'
              '{gpu_name="%s", rented="no"})')
HIST_DAYS = 365
HIST_SOURCE = ("500.farm 对 Vast.ai 可租挂单的逐日中位统计（按机型档位分组统计后聚合，"
               "含平台费；曲线为每日定时采样，现货卡即其最新一点）")

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SERIES = "KXB200MS"
FORWARD_SOURCE = ("Kalshi 公开事件合约：B200 月均租金价位阶梯"
                  "（按 Ornn 跨平台指数的整月平均结算，零鉴权只读）")
# 全部在市结算月都取（实测 13 个月 × 9-15 档全部有报价）。每档一次日 K 请求，
# 串行要 2 分钟以上 → 小并发拉取；4 线程实测 123 张约 40s，对公共接口保持克制。
FORWARD_WORKERS = 4
_KALSHI_UA = {"User-Agent": "Mozilla/5.0"}
_MONTH_ABBR = {"JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
               "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"}


def _get_json(url: str, headers: dict | None = None, timeout: int = 30) -> dict:
    """统一网络出口（测试只需 monkeypatch 这一处）。"""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ——— 历史与现货（500.farm / Prometheus，同源）———

def _farm_history(gpu: str) -> dict:
    """单卡近一年逐日中位价序列。points = [[unix秒, 美元价], ...] 升序。"""
    now = int(time.time())
    url = (HIST_BASE + "/query_range?"
           + urllib.parse.urlencode({
               "query": HIST_QUERY % gpu,
               "start": now - HIST_DAYS * 86400, "end": now, "step": 86400}))
    result = _get_json(url, headers=_KALSHI_UA, timeout=60).get("data", {}).get("result") or []
    if not result:
        return {"gpu": gpu, "unavailable": True,
                "note": "统计站暂无该型号的历史序列（市场状态或型号名变更）"}
    points = []
    for ts, val in result[0].get("values") or []:
        try:
            price = float(val)
        except (TypeError, ValueError):
            continue
        # Prometheus 在切片空窗时会给 "NaN"/"Inf"——非有限值一旦进缓存，
        # FastAPI 的 JSON 序列化会整个拒绝，refresh 和后续 GET 全部报错
        if not math.isfinite(price):
            continue
        points.append([int(ts), round(price, 2)])
    if not points:
        raise ValueError("返回了序列但无一个点可解析（上游契约可能已变）")
    return {"gpu": gpu, "n_points": len(points), "points": points,
            "latest": points[-1][1]}


def _farm_count(gpu: str) -> dict | None:
    """当前市场挂单卡数：{available: 可租, total: 全市场}。

    装饰性规模读数（不是信号本体）：拉取失败返回 None，UI 上该字段自然缺席即可见，
    不单独进 errors。一次查询按 rented label 分组同时拿 no（可租）与 any（全部）。
    """
    url = (HIST_BASE + "/query?"
           + urllib.parse.urlencode({"query": f'sum by (rented) (vastai_v2_gpu_count{{gpu_name="{gpu}"}})'}))
    result = _get_json(url, headers=_KALSHI_UA, timeout=30).get("data", {}).get("result") or []
    by_rented = {}
    for r in result:
        try:
            by_rented[(r.get("metric") or {}).get("rented")] = int(float(r["value"][1]))
        except (KeyError, TypeError, ValueError):
            continue
    if "no" not in by_rented and "any" not in by_rented:
        return None
    return {"available": by_rented.get("no"), "total": by_rented.get("any")}


def _spot_from_history(hist_row: dict, count: dict | None) -> dict:
    """现货卡 = 走势曲线的最后一个点（同一条数据派生，保证两处数字严格一致）。

    历史行的 stale / 错误状态原样传染给现货——它们本来就是同一份数据。
    """
    base = {"gpu": hist_row.get("gpu")}
    for key in ("stale", "fetch_error", "observed_at"):
        if hist_row.get(key) is not None:
            base[key] = hist_row[key]
    if hist_row.get("err"):
        return {**base, "err": hist_row["err"]}
    if hist_row.get("unavailable") or not hist_row.get("points"):
        return {**base, "unavailable": True,
                "note": hist_row.get("note") or "暂无统计序列（市场状态，非故障）"}
    ts, price = hist_row["points"][-1]
    return {**base, "median": price, "asof_ts": ts,
            "available_gpus": (count or {}).get("available"),
            "total_gpus": (count or {}).get("total")}


# ——— 远期（Kalshi）———

def _month_label(tag: str) -> str:
    """'26AUG' → '2026-08'；认不出就原样返回（宁可显示原始 tag 也不显示错月份）。"""
    if len(tag) >= 5 and tag[:2].isdigit() and tag[2:5] in _MONTH_ABBR:
        return f"20{tag[:2]}-{_MONTH_ABBR[tag[2:5]]}"
    return tag


def _last_candle(ticker: str) -> tuple[float, float, float | None] | None:
    """取合约最近一根日 K 的 (yes_bid 收盘, yes_ask 收盘, 持仓量)；无 K 线返回 None。

    窗口只放 2 天（容错周末/时差）：实测每张在市合约每天都会生成带 bid/ask 的日 K，
    超过 2 天没有 K 说明连做市报价都停了——那样的旧价不能当「当前预期」用，
    如实落进「无报价」分支。
    """
    now = int(time.time())
    url = (f"{KALSHI_BASE}/series/{KALSHI_SERIES}/markets/{ticker}/candlesticks"
           f"?start_ts={now - 86400 * 2}&end_ts={now}&period_interval=1440")
    candles = _get_json(url, headers=_KALSHI_UA).get("candlesticks") or []
    if not candles:
        return None
    c = candles[-1]

    def _f(section: str) -> float:
        # 三态分清：字段缺席=该侧无报价（0）；有值但解析不出/非有限=数据异常，
        # 让异常冒泡计入拉取失败——静默转成 0 会把「垃圾数值」伪装成「无报价」，
        # 绕过混合失败保护、让空结果覆盖好缓存
        raw = (c.get(section) or {}).get("close_dollars")
        if raw is None:      # Kalshi 缺席字段给 null；空串不是它的正常输出——
            return 0.0       # "" 交给 float 抛错按故障处理，别当成「无报价」
        v = float(raw)
        if not math.isfinite(v):
            raise ValueError(f"{section} 返回非有限值 {raw!r}")
        return v

    oi = None
    try:
        oi_v = float(c.get("open_interest_fp"))
        if math.isfinite(oi_v):
            oi = round(oi_v, 1)
    except (TypeError, ValueError):
        pass  # 持仓量是装饰性读数，缺席即可见
    return _f("yes_bid"), _f("yes_ask"), oi


def _monotonize(rungs: list[dict]) -> list[dict]:
    """把阶梯概率强制为单调不增（running-min）。

    「月均 ≥ $X」的概率理论上随 X 单调递减；相邻档报价的点差/噪声会造成局部上翘，
    直接差分会出负概率、clamp 掉又会让分布总和超过 1（如 p_above=[0.4,0.9,0.2]
    差分 clamp 后总和 150%）。单调化后再派生分布与隐含中位，总和恒为 1。
    """
    out, prev = [], 1.0
    for r in rungs:
        p = min(r["p_above"], prev)
        out.append({**r, "p_above": round(p, 3)})
        prev = p
    return out


def _implied_median(rungs: list[dict]) -> dict | None:
    """由阶梯反推市场隐含的月均预期中位价：p_above 跨过 0.5 处线性插值。

    bound: exact=插值命中 / above=最高档概率仍 ≥0.5（预期在最高档之上）/
    below=最低档概率已 <0.5（预期在最低档之下）。
    """
    if not rungs:
        return None
    if rungs[0]["p_above"] < 0.5:
        return {"value": rungs[0]["strike"], "bound": "below"}
    if rungs[-1]["p_above"] >= 0.5:
        return {"value": rungs[-1]["strike"], "bound": "above"}
    for a, b in zip(rungs, rungs[1:]):
        if a["p_above"] >= 0.5 > b["p_above"]:
            span = a["p_above"] - b["p_above"]
            frac = (a["p_above"] - 0.5) / span if span else 0.0
            return {"value": round(a["strike"] + frac * (b["strike"] - a["strike"]), 2),
                    "bound": "exact"}
    return None


def _distribution(rungs: list[dict]) -> list[dict]:
    """阶梯差分 → 月均落在各价位区间的概率分布（含头尾开放区间）。

    报价噪声可能让相邻档差分出现微小负值，一律钳到 0——分布里不该有负概率。
    """
    first, last = rungs[0], rungs[-1]
    bins = [{"label": f"<${first['strike']:g}", "lo": None, "hi": first["strike"],
             "p": round(max(1 - first["p_above"], 0.0), 3)}]
    for a, b in zip(rungs, rungs[1:]):
        bins.append({"label": f"${a['strike']:g}~{b['strike']:g}",
                     "lo": a["strike"], "hi": b["strike"],
                     "p": round(max(a["p_above"] - b["p_above"], 0.0), 3)})
    bins.append({"label": f"≥${last['strike']:g}", "lo": last["strike"], "hi": None,
                 "p": round(max(last["p_above"], 0.0), 3)})
    return bins


def _kalshi_markets(status: str) -> list[dict]:
    """拉某状态的全部合约，跟随 cursor 翻页。

    单页上限 200；该系列每月挂 9-15 张新合约，在市数迟早超过一页——
    不翻页会静默丢掉整月（页边界还可能把一个月的阶梯劈成两半）。
    """
    out: list[dict] = []
    cursor = ""
    for _ in range(10):  # 防御上限 10 页 = 2000 张，远超该系列可能规模
        params = {"series_ticker": KALSHI_SERIES, "status": status, "limit": 200}
        if cursor:
            params["cursor"] = cursor  # 不透明 token 可能含保留字符，必须 urlencode
        d = _get_json(f"{KALSHI_BASE}/markets?{urllib.parse.urlencode(params)}",
                      headers=_KALSHI_UA)
        out.extend(d.get("markets") or [])
        cursor = str(d.get("cursor") or "")
        if not cursor:
            break
    return out


def _kalshi_settled() -> list[dict]:
    """已结算月份的月均实际落点区间：最大 yes 档 = 下界，最小 no 档 = 上界。"""
    ms = _kalshi_markets("settled")
    bymonth: dict[str, list[tuple[float, str]]] = {}
    for m in ms:
        parts = str(m.get("ticker", "")).split("-")
        result = m.get("result")
        if len(parts) != 3 or result not in ("yes", "no"):
            continue
        try:
            bymonth.setdefault(parts[1], []).append((float(parts[2]), result))
        except ValueError:
            continue
    out = []
    for tag, rows in bymonth.items():
        yes = [s for s, r in rows if r == "yes"]
        no = [s for s, r in rows if r == "no"]
        out.append({"month": _month_label(tag),
                    "lo": max(yes) if yes else None,
                    "hi": min(no) if no else None})
    out.sort(key=lambda x: x["month"])
    return out


def _kalshi_forward() -> dict:
    """B200 月均租金阶梯 → 按结算月分组的预期概率 + 分布 + 隐含中位 + 已结算落点。

    ⚠️ 取价路径（2026-08-20 实测）：`/markets` 列表与 `/markets/trades` 对未认证请求
    **不再内联价格**（yes_bid / last_price 全 null——连最活跃的利率系列也一样，
    所以是接口行为、不是市场没成交）；orderbook 快照也常为空。零鉴权还能拿到价格的
    是 **candlesticks**（日 K 自带 yes_bid / yes_ask 收盘价与持仓量），走它。
    ⚠️ ticker 价位段小数位不统一（`-3.00` 与 `-4.000` 并存），解析一律用 float。
    """
    ms = _kalshi_markets("open")
    bymonth: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for m in ms:
        parts = str(m.get("ticker", "")).split("-")
        if len(parts) != 3:
            continue
        try:
            strike = float(parts[2])
        except ValueError:
            continue
        key = (str(m.get("close_time", ""))[:10], parts[1])
        bymonth.setdefault(key, []).append((strike, str(m["ticker"])))
    if not bymonth:
        # 三分，不是两分：合约在市但 ticker 全认不出 ⇒ 格式变了，是真故障；
        # 列表本身为空 ⇒ 该系列没有在市合约，是市场状态。
        if ms:
            raise ValueError(f"返回 {len(ms)} 个合约但无一可解析出价位档（ticker 格式可能已变）")
        return {"unavailable": True, "note": "该系列当前无在市合约"}

    # 全部合约的日 K 并发拉取。单档失败不拖垮整体（按缺档处理并计数出声）；
    # 全部失败才判定为真故障——那说明是接口/网络级问题，不是个别合约没数据。
    tickers = [t for rungs in bymonth.values() for _s, t in rungs]

    def _safe_candle(ticker: str):
        # 并发下偶发抖动/限流（实测 123 张约 8% 失败率）→ 退避后最多重试两次再放弃
        for attempt in (0, 1, 2):
            try:
                return _last_candle(ticker), None
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    return None, f"{type(e).__name__}: {e}"
                time.sleep(0.8 * (attempt + 1))

    with ThreadPoolExecutor(max_workers=FORWARD_WORKERS) as ex:
        fetched = dict(zip(tickers, ex.map(_safe_candle, tickers)))
    fail_errors = [err for _c, err in fetched.values() if err]
    if fail_errors and len(fail_errors) == len(tickers):
        raise ValueError(f"全部 {len(tickers)} 档日 K 拉取失败（如：{fail_errors[0]}）")

    months_out = []
    for close_date, mon_tag in sorted(bymonth):
        rungs = []
        for strike, ticker in sorted(bymonth[(close_date, mon_tag)]):
            candle, _err = fetched.get(ticker, (None, None))
            if candle is None:
                continue
            bid, ask, oi = candle
            if bid <= 0 and ask <= 0:
                continue
            p_above = (bid + ask) / 2 if bid > 0 and ask > 0 else (bid or ask)
            rungs.append({"strike": strike, "p_above": round(p_above, 3), "open_interest": oi})
        if not rungs:
            continue
        rungs = _monotonize(rungs)  # 报价噪声上翘会让分布总和超 1，先单调化
        bins = _distribution(rungs)
        months_out.append({
            "month": _month_label(mon_tag), "close_date": close_date, "rungs": rungs,
            "lowest_strike": rungs[0]["strike"],
            "p_below_lowest": round(1 - rungs[0]["p_above"], 3),
            "implied_median": _implied_median(rungs),
            "distribution": bins,
            "most_likely": max(bins, key=lambda x: x["p"]),
        })

    # 已结算月的实际落点（同源 Kalshi；失败不拖垮远期主体，但要出声）
    try:
        settled = _kalshi_settled()
        settled_err = None
    except Exception as e:  # noqa: BLE001
        settled, settled_err = [], str(e)

    if not months_out:
        # 「一个有效档位都没有」时必须分清成因：只要有拉取失败混在里面，就不能
        # 断言「市场无报价」——按故障抛出，让上层走 stale 回填而不是用空结果覆盖好缓存
        if fail_errors:
            raise ValueError(f"{len(fail_errors)}/{len(tickers)} 档日 K 拉取失败、"
                             f"其余无报价，无法判定市场状态（如：{fail_errors[0]}）")
        return {"unavailable": True, "n_contracts": len(ms),
                "note": "合约在市但各价位档均无报价（市场状态，非故障）",
                "settled": settled, "settled_error": settled_err}
    return {"months": months_out, "n_contracts": len(ms), "n_months": len(bymonth),
            "candle_failures": len(fail_errors) or None,
            "settled": settled, "settled_error": settled_err}


# ——— 合并 / 缓存 ———

def _base_payload() -> dict:
    return {
        "schema": SCHEMA,
        "how_to_read": HOW_TO_READ,
        "spot_source": SPOT_SOURCE,
        "history_source": HIST_SOURCE,
        "forward_source": FORWARD_SOURCE,
    }


def _merge_section(fresh_fn, key_name: str, previous_row: dict | None,
                   errors: list[str], label: str):
    """跑一个抓取函数；失败时回填上一次的好数据并标 stale + 出声。

    返回 (row, fresh_ok)。
    """
    try:
        return fresh_fn(), True
    except Exception as e:  # noqa: BLE001 — 网络/契约错误都走同一条回填路径
        errors.append(f"{label}: {e}")
        if isinstance(previous_row, dict) and not previous_row.get("err"):
            return {**previous_row, "stale": True, "fetch_error": str(e)}, False
        return {key_name: label, "err": str(e)}, False


# 刷新约 40 秒：并发触发时各自快照旧缓存再落盘，慢的那个会用旧数据覆盖新结果
# （原子写只防交错损坏、防不了丢更新）——整个刷新串行化。
_FETCH_LOCK = threading.Lock()


def fetch_gpu_rent() -> dict:
    """抓 历史+现货(500.farm) + 远期(Kalshi)，合并落盘。

    失败纪律（fail-loud）：
    - 某一块新抓失败 → 该块回填上一次的好数据并标 `stale: true` + 失败原因；
    - 部分失败在顶层 `errors` 出声，不静默；
    - 全部失败 → 不落盘（绝不用坏数据覆盖好缓存），把带错误标记的结果直接返回。
    """
    with _FETCH_LOCK:
        return _fetch_gpu_rent_locked()


def _fetch_gpu_rent_locked() -> dict:
    previous = load_cache() or {}
    prev_generated = previous.get("generated_at")
    errors: list[str] = []
    fresh_ok = False

    old_hist = {g.get("gpu"): g for g in previous.get("history", {}).get("gpus", [])
                if isinstance(g, dict) and not g.get("err")}

    spot_gpus, hist_gpus = [], []
    for gpu in SPOT_GPUS:
        row, ok = _merge_section(lambda g=gpu: _farm_history(g), "gpu",
                                 old_hist.get(gpu), errors, f"500.farm {gpu}")
        if not ok and row.get("stale"):
            row.setdefault("observed_at", prev_generated)
        if row.get("err"):
            row["gpu"] = gpu
        hist_gpus.append(row)
        fresh_ok = fresh_ok or ok

        # 现货 = 曲线最新点（同一份数据派生）；挂单卡数是装饰性读数，失败置 None
        count = None
        if ok:
            try:
                count = _farm_count(gpu)
            except Exception:  # noqa: BLE001 — 规模读数缺席在 UI 上可见，不拦主流程
                count = None
        spot_gpus.append(_spot_from_history(row, count))

    old_fw = previous.get("forward")
    forward, ok = _merge_section(_kalshi_forward, "source",
                                 old_fw if isinstance(old_fw, dict) else None,
                                 errors, "Kalshi")
    if not ok and forward.get("stale"):
        forward.setdefault("observed_at", prev_generated)
    fresh_ok = fresh_ok or ok
    if forward.get("settled_error"):
        errors.append(f"Kalshi 已结算月: {forward['settled_error']}")
    if forward.get("candle_failures"):
        errors.append(f"Kalshi 远期: {forward['candle_failures']} 档合约日 K 拉取失败（该档按缺档跳过）")

    data = {
        **_base_payload(),
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "spot": {"gpus": spot_gpus},
        "history": {"gpus": hist_gpus, "days": HIST_DAYS},
        "forward": forward,
        "errors": errors or None,
    }
    if fresh_ok:
        _save_cache(data)
    return data


def _save_cache(data: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CACHE_FILE)  # 原子改名，防并发刷新交错写坏缓存
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _load_json_checked(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    # 结构版本不符的旧数据直接作废（当作没有），避免前端读到旧形状
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return data


def load_cache() -> dict | None:
    """取数顺序：用户自己刷新的缓存 → 仓库自带的发布快照 → None（前端提示刷新）。

    口径/来源文案用代码里的 `_base_payload()` 覆盖：快照与旧缓存里存的是生成时的
    旧文案，措辞修正后若原样返回，UI 与 AI 工具会展示与当前实现矛盾的口径描述。
    """
    data = _load_json_checked(CACHE_FILE) or _load_json_checked(SEED_FILE)
    return {**data, **_base_payload()} if data else None


def skeleton() -> dict:
    """无缓存时返回结构骨架（前端据此提示先点刷新）。"""
    return {**_base_payload(), "generated_at": None,
            "spot": {"gpus": []}, "history": {"gpus": [], "days": HIST_DAYS},
            "forward": None, "errors": None}


def get_gpu_rent(force: bool = False) -> dict:
    if force:
        return fetch_gpu_rent()
    return load_cache() or skeleton()
