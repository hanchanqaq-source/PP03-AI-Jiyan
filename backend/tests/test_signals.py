"""产业信号（signals.py）离线测试：mock 统一网络出口 `_get_json`，不联网。

重点盯四类行为（都是数据管道的历史教训）：
- 解析：Vast 多卡整机折算单卡价、中位数；Kalshi ticker 小数位不统一、按结算月分组；
  500.farm Prometheus range 序列；
- 三分判别：「市场没报价」是状态、「字段/格式变了」是故障、正常数据是数据——不许混；
- 推导：隐含预期中位价插值（含两端越界）、阶梯差分出概率分布（负差分钳 0）、
  已结算月的落点区间；
- 失败纪律：部分失败回填旧值并出声（stale + errors）、全部失败绝不覆盖好缓存、
  缓存结构版本不符自动作废。
"""
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import signals


# ——— mock 工厂 ———

def _candle(bid: float, ask: float, oi: float = 100.0) -> dict:
    return {
        "yes_bid": {"close_dollars": f"{bid:.4f}"},
        "yes_ask": {"close_dollars": f"{ask:.4f}"},
        "open_interest_fp": f"{oi:.2f}",
    }


def _market(month: str, strike: str, close: str, result: str | None = None) -> dict:
    m = {"ticker": f"KXB200MS-{month}-{strike}", "close_time": f"{close}T00:00:00Z"}
    if result:
        m["result"] = result
    return m


def _fake_get_json(kalshi_markets=None, candles=None, settled=None,
                   hist=None, counts=None):
    """按 URL 分发的 _get_json 替身。
    candles: {ticker: [...]}, hist: {gpu: [[ts, val], ...]}（None 表示该 gpu 无序列），
    counts: {gpu: {"no": n, "any": m}}（挂单卡数，缺省返回空结果）"""
    def fake(url, headers=None, timeout=30):
        if url.startswith(signals.HIST_BASE):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            query = qs["query"][0]
            gpu = query.split('gpu_name="')[1].split('"')[0]
            if "gpu_count" in query:
                by = (counts or {}).get(gpu) or {}
                return {"data": {"result": [
                    {"metric": {"rented": k}, "value": [0, str(v)]} for k, v in by.items()]}}
            series = (hist or {}).get(gpu)
            return {"data": {"result": [] if series is None else [{"values": series}]}}
        if "/candlesticks" in url:
            ticker = url.split("/markets/")[1].split("/candlesticks")[0]
            return {"candlesticks": (candles or {}).get(ticker, [])}
        if "status=settled" in url:
            return {"markets": settled or []}
        if "/markets?" in url:
            return {"markets": kalshi_markets or []}
        raise AssertionError(f"unexpected url: {url}")
    return fake


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """缓存与发布快照都隔离到临时目录 + 关掉限流 sleep（离线测试不需要等）。"""
    monkeypatch.setattr(signals, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(signals, "CACHE_FILE", str(tmp_path / "signals_gpu.json"))
    monkeypatch.setattr(signals, "SEED_FILE", str(tmp_path / "seed.json"))
    monkeypatch.setattr("time.sleep", lambda s: None)


# ——— 现货（曲线终点派生）———

def test_spot_gpus_use_exact_model_names():
    # 写 "H100" 永远查不到（市场上挂的是 "H100 SXM" 等细分名）——防止回退成裸名
    assert "H100" not in signals.SPOT_GPUS
    assert "A100" not in signals.SPOT_GPUS


def test_spot_from_history_is_last_point():
    # 现货 = 曲线最后一点（同一份数据），卡数为附加规模读数
    hist = {"gpu": "B200", "points": [[100, 5.0], [200, 6.95]], "latest": 6.95}
    r = signals._spot_from_history(hist, {"available": 67, "total": 379})
    assert r == {"gpu": "B200", "median": 6.95, "asof_ts": 200,
                 "available_gpus": 67, "total_gpus": 379}


def test_spot_from_history_propagates_states():
    # 历史行的 unavailable / err / stale 原样传染给现货——本来就是同一份数据
    assert signals._spot_from_history({"gpu": "X", "unavailable": True, "note": "n"}, None) == \
        {"gpu": "X", "unavailable": True, "note": "n"}
    assert signals._spot_from_history({"gpu": "X", "err": "boom"}, None) == \
        {"gpu": "X", "err": "boom"}
    stale = {"gpu": "X", "points": [[1, 2.0]], "stale": True,
             "fetch_error": "down", "observed_at": "2026-08-19 10:00"}
    r = signals._spot_from_history(stale, None)
    assert r["stale"] is True and r["median"] == 2.0 and r["available_gpus"] is None


def test_farm_count_parses_rented_groups(monkeypatch):
    monkeypatch.setattr(signals, "_get_json",
                        _fake_get_json(counts={"B200": {"no": 67, "any": 379}}))
    assert signals._farm_count("B200") == {"available": 67, "total": 379}
    assert signals._farm_count("H100 SXM") is None  # 无分组结果 → None


# ——— 500.farm 历史 ———

def test_farm_history_parses_series(monkeypatch):
    series = [[1700000000, "7.50"], [1700086400, "7.60"]]
    monkeypatch.setattr(signals, "_get_json", _fake_get_json(hist={"B200": series}))
    r = signals._farm_history("B200")
    assert r["n_points"] == 2
    assert r["points"] == [[1700000000, 7.5], [1700086400, 7.6]]
    assert r["latest"] == 7.6


def test_farm_history_no_series_is_state(monkeypatch):
    monkeypatch.setattr(signals, "_get_json", _fake_get_json(hist={}))
    assert signals._farm_history("B200")["unavailable"] is True


def test_farm_history_drops_non_finite_samples(monkeypatch):
    # Prometheus 切片空窗会给 "NaN"/"Inf"——进缓存会让 FastAPI JSON 序列化整体失败
    series = [[100, "NaN"], [200, "7.5"], [300, "+Inf"]]
    monkeypatch.setattr(signals, "_get_json", _fake_get_json(hist={"B200": series}))
    r = signals._farm_history("B200")
    assert r["points"] == [[200, 7.5]]


def test_last_candle_non_finite_quote_is_failure(monkeypatch):
    # 报价字段出现 NaN/Inf 是数据异常，必须按拉取失败冒泡（计入 candle_failures，
    # 触发混合失败保护）——静默转 0 会伪装成「无报价」，让空结果覆盖好缓存
    candles = {"KXB200MS-26SEP-3.00": [{
        "yes_bid": {"close_dollars": "NaN"},
        "yes_ask": {"close_dollars": "0.9"},
        "open_interest_fp": "Inf",
    }]}
    monkeypatch.setattr(signals, "_get_json", _fake_get_json(candles=candles))
    with pytest.raises(ValueError, match="非有限值"):
        signals._last_candle("KXB200MS-26SEP-3.00")


def test_last_candle_empty_string_quote_is_failure(monkeypatch):
    # Kalshi 缺席字段给 null，空串不是它的正常输出——必须按故障冒泡而不是当「无报价」
    candles = {"KXB200MS-26SEP-3.00": [{
        "yes_bid": {"close_dollars": ""},
        "yes_ask": {"close_dollars": "0.9"},
    }]}
    monkeypatch.setattr(signals, "_get_json", _fake_get_json(candles=candles))
    with pytest.raises(ValueError):
        signals._last_candle("KXB200MS-26SEP-3.00")


def test_last_candle_missing_side_is_zero(monkeypatch):
    # 字段缺席=该侧无报价（合法状态），持仓量非法只置 None 不拦主值
    candles = {"KXB200MS-26SEP-3.00": [{
        "yes_ask": {"close_dollars": "0.9"},
        "open_interest_fp": "Inf",
    }]}
    monkeypatch.setattr(signals, "_get_json", _fake_get_json(candles=candles))
    assert signals._last_candle("KXB200MS-26SEP-3.00") == (0.0, 0.9, None)


def test_farm_history_unparseable_points_raise(monkeypatch):
    monkeypatch.setattr(signals, "_get_json",
                        _fake_get_json(hist={"B200": [["x", None]]}))
    with pytest.raises(ValueError, match="无一个点可解析"):
        signals._farm_history("B200")


# ——— 远期推导 ———

def test_month_label():
    assert signals._month_label("26AUG") == "2026-08"
    assert signals._month_label("27JAN") == "2027-01"
    assert signals._month_label("XXX") == "XXX"  # 认不出原样返回，不猜


def test_implied_median_interpolates():
    rungs = [{"strike": 5.5, "p_above": 0.9}, {"strike": 6.0, "p_above": 0.9},
             {"strike": 6.5, "p_above": 0.1}]
    m = signals._implied_median(rungs)
    # 0.9 → 0.1 跨过 0.5：6.0 + (0.9-0.5)/(0.9-0.1)*0.5 = 6.25
    assert m == {"value": 6.25, "bound": "exact"}


def test_implied_median_bounds():
    assert signals._implied_median([{"strike": 3.0, "p_above": 0.4}]) == \
        {"value": 3.0, "bound": "below"}
    assert signals._implied_median([{"strike": 7.0, "p_above": 0.8}]) == \
        {"value": 7.0, "bound": "above"}
    assert signals._implied_median([]) is None


def test_monotonize_forces_non_increasing():
    # 报价噪声上翘（0.4→0.9）会让差分分布总和超 1 —— 单调化后分布恒归一
    rungs = [{"strike": 3.0, "p_above": 0.4}, {"strike": 3.5, "p_above": 0.9},
             {"strike": 4.0, "p_above": 0.2}]
    mono = signals._monotonize(rungs)
    assert [r["p_above"] for r in mono] == [0.4, 0.4, 0.2]
    bins = signals._distribution(mono)
    assert abs(sum(b["p"] for b in bins) - 1.0) < 1e-9


def test_forward_rungs_are_monotonized(monkeypatch):
    # forward 输出的 rungs / 分布 / 中位都基于单调化后的概率
    markets = [_market("26SEP", "3.00", "2026-10-01"),
               _market("26SEP", "3.50", "2026-10-01")]
    candles = {"KXB200MS-26SEP-3.00": [_candle(0.40, 0.40)],
               "KXB200MS-26SEP-3.50": [_candle(0.90, 0.90)]}  # 上翘噪声
    monkeypatch.setattr(signals, "_get_json",
                        _fake_get_json(kalshi_markets=markets, candles=candles))
    m = signals._kalshi_forward()["months"][0]
    assert [r["p_above"] for r in m["rungs"]] == [0.4, 0.4]
    assert abs(sum(b["p"] for b in m["distribution"]) - 1.0) < 1e-9


def test_distribution_bins_and_clamp():
    rungs = [{"strike": 3.0, "p_above": 0.98}, {"strike": 3.5, "p_above": 0.99},  # 噪声上翘
             {"strike": 4.0, "p_above": 0.30}]
    bins = signals._distribution(rungs)
    assert bins[0] == {"label": "<$3", "lo": None, "hi": 3.0, "p": 0.02}
    assert bins[1]["p"] == 0.0            # 负差分钳到 0，不出负概率
    assert bins[2] == {"label": "$3.5~4", "lo": 3.5, "hi": 4.0, "p": 0.69}
    assert bins[3] == {"label": "≥$4", "lo": 4.0, "hi": None, "p": 0.30}
    assert abs(sum(b["p"] for b in bins) - 1.01) < 1e-9  # 噪声钳 0 后允许略过 1


def test_kalshi_markets_follows_pagination(monkeypatch):
    """在市合约超单页 200 上限后必须跟 cursor 翻页——否则整月静默丢失。"""
    page1 = [_market("26SEP", "3.00", "2026-10-01")]
    page2 = [_market("26OCT", "3.00", "2026-11-01")]

    def paged(url, headers=None, timeout=30):
        if "cursor=NEXT" in url:
            return {"markets": page2, "cursor": ""}
        if "/markets?" in url:
            return {"markets": page1, "cursor": "NEXT"}
        raise AssertionError(url)

    monkeypatch.setattr(signals, "_get_json", paged)
    ms = signals._kalshi_markets("open")
    assert [m["ticker"] for m in ms] == ["KXB200MS-26SEP-3.00", "KXB200MS-26OCT-3.00"]


def test_kalshi_settled_ranges(monkeypatch):
    settled = [
        _market("26JUN", "4.50", "2026-07-01", "yes"),
        _market("26JUN", "5.00", "2026-07-01", "no"),
        _market("26JUL", "6.00", "2026-08-01", "yes"),  # 全 yes ⇒ hi=None
    ]
    monkeypatch.setattr(signals, "_get_json", _fake_get_json(settled=settled))
    r = signals._kalshi_settled()
    assert r == [{"month": "2026-06", "lo": 4.5, "hi": 5.0},
                 {"month": "2026-07", "lo": 6.0, "hi": None}]


def test_kalshi_forward_groups_by_month(monkeypatch):
    # 小数位混用（3.00 / 4.000）+ 三个结算月全覆盖；无 K 线的月份整月跳过
    markets = [
        _market("26AUG", "3.00", "2026-09-01"),
        _market("26AUG", "4.000", "2026-09-01"),
        _market("26SEP", "3.00", "2026-10-01"),
        _market("27AUG", "3.00", "2027-09-01"),
    ]
    candles = {
        "KXB200MS-26AUG-3.00": [_candle(0.98, 1.0, 500)],
        "KXB200MS-26AUG-4.000": [_candle(0.8, 0.82, 300)],
        "KXB200MS-26SEP-3.00": [_candle(0.9, 0.92)],
        # 27AUG 无 K 线 → 无报价，整月跳过
    }
    monkeypatch.setattr(signals, "_get_json",
                        _fake_get_json(kalshi_markets=markets, candles=candles))
    r = signals._kalshi_forward()
    assert r["n_contracts"] == 4 and r["n_months"] == 3
    assert [m["month"] for m in r["months"]] == ["2026-08", "2026-09"]
    assert r["candle_failures"] is None   # 无 K 线是市场状态，不算拉取失败
    aug = r["months"][0]
    assert aug["rungs"] == [
        {"strike": 3.0, "p_above": 0.99, "open_interest": 500.0},
        {"strike": 4.0, "p_above": 0.81, "open_interest": 300.0},
    ]
    assert aug["p_below_lowest"] == pytest.approx(0.01)
    assert aug["implied_median"]["bound"] == "above"     # 最高档仍 0.81 ≥ 0.5
    assert aug["most_likely"] == {"label": "≥$4", "lo": 4.0, "hi": None, "p": 0.81}
    assert len(aug["distribution"]) == 3


def test_kalshi_forward_no_markets_is_state(monkeypatch):
    monkeypatch.setattr(signals, "_get_json", _fake_get_json(kalshi_markets=[]))
    assert signals._kalshi_forward()["unavailable"] is True


def test_kalshi_forward_unparseable_tickers_raise(monkeypatch):
    markets = [{"ticker": "WEIRD_FORMAT", "close_time": "2026-09-01T00:00:00Z"}]
    monkeypatch.setattr(signals, "_get_json", _fake_get_json(kalshi_markets=markets))
    with pytest.raises(ValueError, match="ticker"):
        signals._kalshi_forward()


def test_kalshi_forward_all_unquoted_is_state(monkeypatch):
    # 合约在市、K 线为空 ⇒ 无人报价，是市场状态非故障
    markets = [_market("26SEP", "3.00", "2026-10-01")]
    monkeypatch.setattr(signals, "_get_json",
                        _fake_get_json(kalshi_markets=markets, candles={}))
    r = signals._kalshi_forward()
    assert r["unavailable"] is True and r["n_contracts"] == 1


def test_kalshi_forward_one_sided_quote(monkeypatch):
    # 只有单边报价时用该边，不用 (bid+0)/2 把概率腰斩
    markets = [_market("26SEP", "5.50", "2026-10-01")]
    candles = {"KXB200MS-26SEP-5.50": [_candle(0.7, 0.0)]}
    monkeypatch.setattr(signals, "_get_json",
                        _fake_get_json(kalshi_markets=markets, candles=candles))
    assert signals._kalshi_forward()["months"][0]["rungs"][0]["p_above"] == 0.7


def test_kalshi_forward_partial_candle_failure_skips_and_counts(monkeypatch):
    # 单档日 K 拉取异常 → 该档按缺档跳过 + candle_failures 计数出声，不拖垮整体
    markets = [_market("26SEP", "3.00", "2026-10-01"),
               _market("26SEP", "3.50", "2026-10-01")]
    inner = _fake_get_json(kalshi_markets=markets,
                           candles={"KXB200MS-26SEP-3.00": [_candle(0.98, 1.0)]})

    def flaky(url, headers=None, timeout=30):
        if "3.50/candlesticks" in url:
            raise OSError("one candle down")
        return inner(url, headers, timeout)

    monkeypatch.setattr(signals, "_get_json", flaky)
    r = signals._kalshi_forward()
    assert r["candle_failures"] == 1
    assert [x["strike"] for x in r["months"][0]["rungs"]] == [3.0]


def test_kalshi_forward_mixed_failure_with_no_rungs_raises(monkeypatch):
    # 部分档抛错 + 其余空 K 线 ⇒ 无法断言「市场无报价」，必须按故障抛出走 stale 回填，
    # 否则空结果会以「无报价」姿态覆盖有效的旧缓存
    markets = [_market("26SEP", "3.00", "2026-10-01"),
               _market("26SEP", "3.50", "2026-10-01")]
    inner = _fake_get_json(kalshi_markets=markets, candles={})  # 3.00 空 K 线

    def flaky(url, headers=None, timeout=30):
        if "3.50/candlesticks" in url:
            raise OSError("one candle down")
        return inner(url, headers, timeout)

    monkeypatch.setattr(signals, "_get_json", flaky)
    with pytest.raises(ValueError, match="无法判定市场状态"):
        signals._kalshi_forward()


def test_kalshi_forward_all_candles_failing_raises(monkeypatch):
    # 全部档位拉取失败 = 接口/网络级故障，必须 raise 而不是静默返回「无报价」
    markets = [_market("26SEP", "3.00", "2026-10-01")]
    inner = _fake_get_json(kalshi_markets=markets)

    def dead_candles(url, headers=None, timeout=30):
        if "/candlesticks" in url:
            raise OSError("all candles down")
        return inner(url, headers, timeout)

    monkeypatch.setattr(signals, "_get_json", dead_candles)
    with pytest.raises(ValueError, match="日 K 拉取失败"):
        signals._kalshi_forward()


def test_kalshi_settled_failure_does_not_kill_forward(monkeypatch):
    markets = [_market("26SEP", "3.00", "2026-10-01")]
    candles = {"KXB200MS-26SEP-3.00": [_candle(0.98, 1.0)]}
    inner = _fake_get_json(kalshi_markets=markets, candles=candles)

    def flaky(url, headers=None, timeout=30):
        if "status=settled" in url:
            raise OSError("settled endpoint down")
        return inner(url, headers, timeout)

    monkeypatch.setattr(signals, "_get_json", flaky)
    r = signals._kalshi_forward()
    assert r["months"] and r["settled"] == [] and "settled endpoint down" in r["settled_error"]


# ——— 合并与失败纪律 ———

def _ok_get_json():
    hist = {g: [[1700000000, "5.0"], [1700086400, "6.0"]] for g in signals.SPOT_GPUS}
    counts = {"B200": {"no": 67, "any": 379}}   # 其余型号无卡数 → 字段为 None
    markets = [_market("26AUG", "3.00", "2026-09-01")]
    candles = {"KXB200MS-26AUG-3.00": [_candle(0.98, 1.0)]}
    settled = [_market("26JUN", "4.50", "2026-07-01", "yes")]
    return _fake_get_json(kalshi_markets=markets, candles=candles,
                          settled=settled, hist=hist, counts=counts)


def test_fetch_all_fresh_writes_cache(monkeypatch):
    monkeypatch.setattr(signals, "_get_json", _ok_get_json())
    data = signals.fetch_gpu_rent()
    assert data["errors"] is None
    # 现货 = 曲线最后一点：两处数字必然一致
    assert data["spot"]["gpus"][0]["median"] == 6.0
    assert data["spot"]["gpus"][0]["median"] == data["history"]["gpus"][0]["latest"]
    assert data["spot"]["gpus"][0]["available_gpus"] == 67
    assert data["spot"]["gpus"][1]["available_gpus"] is None  # 卡数缺席不拦主流程
    assert [g["latest"] for g in data["history"]["gpus"]] == [6.0, 6.0, 6.0]
    assert data["forward"]["months"][0]["p_below_lowest"] == pytest.approx(0.01)
    assert data["forward"]["settled"] == [{"month": "2026-06", "lo": 4.5, "hi": None}]
    assert signals.load_cache() == data


def test_fetch_partial_failure_backfills_stale(monkeypatch):
    # 先落一份好缓存，再让 Kalshi 挂掉：远期应回填旧值 + stale + errors 出声
    monkeypatch.setattr(signals, "_get_json", _ok_get_json())
    good = signals.fetch_gpu_rent()

    def flaky(url, headers=None, timeout=30):
        if "kalshi" in url:
            raise OSError("network down")
        return _ok_get_json()(url, headers, timeout)

    monkeypatch.setattr(signals, "_get_json", flaky)
    data = signals.fetch_gpu_rent()
    assert data["errors"] and any("Kalshi" in e for e in data["errors"])
    assert data["forward"]["stale"] is True
    assert data["forward"]["months"] == good["forward"]["months"]
    assert data["forward"]["observed_at"] == good["generated_at"]
    assert signals.load_cache() == data  # 现货/历史仍新鲜 → 照常落盘


def test_fetch_total_failure_never_overwrites_cache(monkeypatch):
    monkeypatch.setattr(signals, "_get_json", _ok_get_json())
    good = signals.fetch_gpu_rent()

    def dead(url, headers=None, timeout=30):
        raise OSError("network down")

    monkeypatch.setattr(signals, "_get_json", dead)
    data = signals.fetch_gpu_rent()
    # 3 现货 + 3 历史 + 1 Kalshi，各出一声
    assert len(data["errors"]) == len(signals.SPOT_GPUS) + 1  # 3 历史(现货随之) + 1 Kalshi
    assert all(g.get("stale") for g in data["spot"]["gpus"])   # 内存结果回填旧值可用
    assert all(g.get("stale") for g in data["history"]["gpus"])
    assert signals.load_cache() == good  # 但缓存必须还是上一份好数据


def test_fetch_total_failure_no_cache_reports_errors(monkeypatch):
    def dead(url, headers=None, timeout=30):
        raise OSError("network down")

    monkeypatch.setattr(signals, "_get_json", dead)
    data = signals.fetch_gpu_rent()
    assert all(g.get("err") for g in data["spot"]["gpus"])
    assert data["forward"]["err"]
    assert signals.load_cache() is None  # 没有好数据可回填，也绝不落盘坏数据


def test_load_cache_rejects_old_schema(tmp_path, monkeypatch):
    # 结构版本不符的旧缓存必须作废，避免前端读到旧形状
    monkeypatch.setattr(signals, "CACHE_FILE", str(tmp_path / "old.json"))
    with open(signals.CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"generated_at": "2026-08-20 08:00", "spot": {"gpus": []}}, f)
    assert signals.load_cache() is None


def test_load_cache_overlays_current_wording():
    # 快照/旧缓存里存的是生成时的口径文案——加载时必须以代码里的当前文案为准，
    # 否则措辞修正后新装用户看到的仍是与实现矛盾的旧口径描述
    stale_wording = {**signals.skeleton(), "generated_at": "2026-08-20 10:00",
                     "how_to_read": ["旧文案"], "spot_source": "旧口径"}
    signals._save_cache(stale_wording)
    loaded = signals.load_cache()
    assert loaded["how_to_read"] == signals.HOW_TO_READ
    assert loaded["spot_source"] == signals.SPOT_SOURCE
    assert loaded["generated_at"] == "2026-08-20 10:00"  # 数据字段保持原样


def test_load_cache_falls_back_to_seed():
    # 无用户缓存时读仓库自带的发布快照（clone 即有数据）；用户刷新后以缓存优先
    seed = {**signals.skeleton(), "generated_at": "2026-08-20 10:00", "marker": "seed"}
    with open(signals.SEED_FILE, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False)
    assert signals.load_cache()["marker"] == "seed"

    cache = {**signals.skeleton(), "generated_at": "2026-08-21 09:00", "marker": "cache"}
    signals._save_cache(cache)
    assert signals.load_cache()["marker"] == "cache"


def test_seed_file_ships_with_repo():
    # 发布快照必须随仓库存在且结构版本匹配——否则「clone 即用」承诺失效
    import os
    real_seed = os.path.join(os.path.dirname(os.path.abspath(signals.__file__)),
                             "data", "signals_gpu_seed.json")
    with open(real_seed, encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("schema") == signals.SCHEMA
    assert data["spot"]["gpus"] and data["history"]["gpus"]
    assert data["forward"]["months"]


def test_get_gpu_rent_skeleton_when_no_cache():
    data = signals.get_gpu_rent(force=False)
    assert data["generated_at"] is None
    assert data["spot"]["gpus"] == [] and data["forward"] is None
    assert data["history"]["gpus"] == []
    assert data["schema"] == signals.SCHEMA
    assert len(data["how_to_read"]) == 4


# ——— API 契约 ———

def test_api_gpu_rent_get_and_refresh(monkeypatch):
    import app as app_module
    client = TestClient(app_module.app)

    r = client.get("/api/signals/gpu-rent")
    assert r.status_code == 200
    assert len(r.json()["data"]["how_to_read"]) == 4

    monkeypatch.setattr(signals, "_get_json", _ok_get_json())
    r = client.post("/api/signals/gpu-rent/refresh")
    assert r.status_code == 200
    assert r.json()["data"]["spot"]["gpus"][0]["median"] == 6.0


def test_tools_query_gpu_rent(monkeypatch):
    """AI 工具层：query_gpu_rent 走缓存路径，无缓存也要给结构化骨架而不是报错。"""
    import tools
    out = tools.exec_tool("query_gpu_rent", {})
    assert len(out["how_to_read"]) == 4


def test_tools_query_gpu_rent_fits_chat_cap(monkeypatch):
    """工具输出必须裁剪：chat 层单次工具结果上限 6000 字符，全量缓存（40KB 历史点）
    直接返回会被截成非法 JSON。裁剪版不含逐点序列且留有余量。"""
    import chat
    import tools
    monkeypatch.setattr(signals, "_get_json", _ok_get_json())
    signals.fetch_gpu_rent()

    out = tools.exec_tool("query_gpu_rent", {})
    blob = json.dumps(out, ensure_ascii=False)
    assert len(blob) < chat._TOOL_RESULT_CAP * 0.8   # 留 20% 余量防真实数据略胖
    assert '"points"' not in blob                     # 逐点序列不进工具输出
    assert out["history_summary"][0]["latest"]["usd_per_gpu_hr"] == 6.0
    assert out["forward"]["months"][0]["implied_median"] is not None
    assert out["spot"]["gpus"][0]["median"] == 6.0    # 现货原样保留


def test_newsradar_dedup_recurring_title_refreshes_baseline():
    """同名栏目窗口外合法复现后，标题基准必须跟着最近保留的那条走：
    倒序遍历 20h/70h/100h 前的三条同名——70h 那条是另一期（保留），
    100h 那条与 70h 只差 30h（窗内转载，应删）；若基准停在 20h 就会漏删。"""
    import newsradar
    H = 3600
    items = [
        {"title": "每周综述", "url": "https://a.com/1", "ts": 1000 * H - 20 * H},
        {"title": "每周综述", "url": "https://b.com/2", "ts": 1000 * H - 70 * H},
        {"title": "每周综述", "url": "https://c.com/3", "ts": 1000 * H - 100 * H},
    ]
    out = newsradar._dedup(items)
    assert [it["url"] for it in out] == ["https://a.com/1", "https://b.com/2"]


def test_newsradar_normalize_url_reencodes_query():
    """query 值里的转义分隔符必须重新转义：否则 ?id=a%26b%3Dc 与 ?id=a&b=c
    归一化成同一个 key，两篇不同文章被误合并（去重误删）。"""
    import newsradar
    a = newsradar._normalize_url("https://x.com/p?id=a%26b%3Dc")
    b = newsradar._normalize_url("https://x.com/p?id=a&b=c")
    assert a != b
    # 跟踪参数照剥、真实参数保留
    c = newsradar._normalize_url("https://x.com/p?utm_source=rss&id=1")
    assert c == "https://x.com/p?id=1"
