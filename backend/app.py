"""Vibe-Research 后端 —— A股数据层 HTTP 接口（FastAPI）。

端点全部在 /api 下，前端 vite 代理 /api → localhost:8900。
只读、无状态、按用户传入代码返回客观数据。不预置标的、不建议。

启动：
    uvicorn app:app --host 127.0.0.1 --port 8900
"""

from __future__ import annotations

import json
import math
import os
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Path as ApiPath, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

import astock
import chat as chat_layer
import cli_runtime
import debate as debate_layer
import gstock
import newsradar
import news_source_manager
import portfolio as pf
import fund_portfolio as fpf
from fund_data import service as fund_service
import market
import myreports as mr
import reflection as reflect_layer
import signals
import subscription_ai.service as subscription_ai_service
from subscription_ai.codex_provider import LoginSwitchConfirmationRequired, public_runtime_message


from version import read_version

__version__ = read_version()

app = FastAPI(title="Vibe-Research API", version=__version__)

# 每半小时后台刷新持仓数据
pf.start_scheduler(1800)

# 默认只允许本机浏览器；部署者可显式配置只读来源白名单。
_CONFIGURED_ORIGINS = [origin.strip() for origin in os.environ.get("VR_ALLOW_ORIGINS", "").split(",") if origin.strip()]
_LOOPBACK_ORIGIN_REGEX = r"^https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?$"
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CONFIGURED_ORIGINS,
    allow_origin_regex=None if _CONFIGURED_ORIGINS else _LOOPBACK_ORIGIN_REGEX,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# 可选鉴权：设了 VR_API_KEY 就要求所有 /api/* 带 `Authorization: Bearer <key>`
#   （本地自托管不设=开放；公网部署务必设，否则别人能读你的持仓/调你的后端）。
_API_KEY = os.environ.get("VR_API_KEY", "").strip()
_WRITE_METHODS = {"POST", "PUT", "DELETE"}
_WRITE_HEADER = "x-pp03-write-intent"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _header_values(request: Request, name: str) -> tuple[str, ...]:
    encoded = name.lower().encode("ascii")
    return tuple(value.decode("latin-1") for key, value in request.scope.get("headers", ()) if key.lower() == encoded)


def _loopback_authority(value: str | None) -> bool:
    if type(value) is not str or not value or len(value) > 255 or value != value.strip() or "," in value:
        return False
    try:
        parsed = urlsplit(f"//{value}")
        port = parsed.port
    except ValueError:
        return False
    return parsed.username is None and parsed.password is None and parsed.hostname in _LOOPBACK_HOSTS and (port is None or 1 <= port <= 65535)


def _local_browser_origin(value: str | None) -> bool:
    if type(value) is not str or not value or len(value) > 512 or value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"} and parsed.username is None and parsed.password is None
        and parsed.hostname in _LOOPBACK_HOSTS and parsed.path == "" and parsed.query == ""
        and parsed.fragment == "" and (port is None or 1 <= port <= 65535)
    )


@app.middleware("http")
async def _protect_radar_source_writes(request: Request, call_next):
    if not request.url.path.startswith("/api/radar/sources"):
        return await call_next(request)
    if request.method == "OPTIONS":
        requested = _header_values(request, "access-control-request-method")
        if not requested or requested[0].upper() not in _WRITE_METHODS:
            return await call_next(request)
    elif request.method not in _WRITE_METHODS:
        return await call_next(request)
    host_values = _header_values(request, "host")
    origin_values = _header_values(request, "origin")
    if len(host_values) != 1 or len(origin_values) != 1 or not _loopback_authority(host_values[0]) or not _local_browser_origin(origin_values[0]):
        return JSONResponse({"detail": "本地写操作来源验证失败"}, status_code=403)
    if request.method == "OPTIONS":
        requested_headers = ",".join(_header_values(request, "access-control-request-headers")).lower()
        if _WRITE_HEADER not in {item.strip() for item in requested_headers.split(",")}:
            return JSONResponse({"detail": "本地写操作来源验证失败"}, status_code=403)
        return await call_next(request)
    write_values = _header_values(request, _WRITE_HEADER)
    if len(write_values) != 1 or write_values[0] != "1":
        return JSONResponse({"detail": "本地写操作来源验证失败"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def _require_api_key(request: Request, call_next):
    if (
        _API_KEY
        and request.method != "OPTIONS"
        and request.url.path.startswith("/api/")
        and request.url.path != "/api/health"
    ):
        if request.headers.get("authorization", "") != f"Bearer {_API_KEY}":
            return JSONResponse({"detail": "未授权：缺少或错误的 API Key（VR_API_KEY）"}, status_code=401)
    return await call_next(request)

_CODE_RE = r"^\d{6}$"


def _validate(code: str) -> str:
    code = (code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "代码必须是 6 位数字")
    return code


@app.get("/api/health")
def health():
    return {"ok": True, "service": "vibe-research-api", "version": __version__}


def get_subscription_ai_service() -> subscription_ai_service.SubscriptionAIService:
    return subscription_ai_service.get_service()


def _subscription_local_actions_allowed(request: Request) -> bool:
    host_values = _header_values(request, "host")
    if len(host_values) != 1 or not _loopback_authority(host_values[0]):
        return False
    client_host = request.client.host if request.client is not None else ""
    if client_host not in _LOOPBACK_HOSTS:
        return False
    origin_values = _header_values(request, "origin")
    if len(origin_values) > 1:
        return False
    return not origin_values or _local_browser_origin(origin_values[0])


def _require_subscription_local_action(request: Request) -> None:
    if not _subscription_local_actions_allowed(request):
        raise HTTPException(403, "Codex 登录和连接测试只能由本机 loopback 页面使用")


class CodexLoginReq(BaseModel):
    confirm_switch: bool = False


@app.get("/api/subscription-ai/providers")
def subscription_ai_providers(request: Request, refresh: bool = Query(False)):
    return get_subscription_ai_service().list_providers(force=refresh)


@app.post("/api/subscription-ai/codex/login")
def subscription_ai_codex_login(body: CodexLoginReq, request: Request):
    _require_subscription_local_action(request)
    try:
        data = get_subscription_ai_service().start_codex_login(confirm_switch=body.confirm_switch)
    except LoginSwitchConfirmationRequired as error:
        raise HTTPException(409, str(error)) from error
    except OSError as error:
        raise HTTPException(500, "无法启动 Codex 官方登录，请在本机终端运行 codex login") from error
    return data


@app.post("/api/subscription-ai/codex/test")
def subscription_ai_codex_test(request: Request):
    _require_subscription_local_action(request)
    return get_subscription_ai_service().test_codex()


@app.post("/api/subscription-ai/codex/cancel")
def subscription_ai_codex_cancel(request: Request):
    _require_subscription_local_action(request)
    return get_subscription_ai_service().cancel_codex_test()


class LLMConfig(BaseModel):
    provider: str = ""       # cli-* = 订阅接入（调本机 CLI）；其余 = API 接入
    baseURL: str = ""        # 订阅接入时留空
    apiKey: str = ""         # 订阅接入时留空
    model: str


class ChatReq(BaseModel):
    messages: list[dict]
    context: str = ""
    llm: LLMConfig


@app.post("/api/chat")
def chat(req: ChatReq):
    """系统 AI 对话，**流式** NDJSON（每行一个事件 {type: tool|delta|done|error}）。

    - API 接入：OpenAI 兼容 function-calling，边流答案边推工具调用事件。
    - 订阅接入（provider=cli-*）：调本机已登录的 CLI，stdout 边出边流（数据靠 context）。
    配置错误（缺 key / 未装 CLI）走 HTTP 400；运行时错误走流内 error 事件。用户配置随请求传入，后端不持久化。
    """
    if not req.messages:
        raise HTTPException(400, "messages 不能为空")
    cfg = _check_llm(req.llm)
    is_cli = req.llm.provider.startswith("cli-")

    def gen():
        try:
            events = (chat_layer.run_chat_cli_stream if is_cli else chat_layer.run_chat_stream)(cfg, req.messages, req.context)
            for ev in events:
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:  # noqa: BLE001 — 运行时错误以流内事件上报，不中断连接
            message = public_runtime_message(e) if cfg.get("provider") == "cli-codex" else f"对话失败：{e}"
            yield json.dumps({"type": "error", "message": message}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


def _check_llm(llm: LLMConfig) -> dict:
    """校验模型配置并返回 cfg（chat / debate / reflect 三个流式端点共用）。

    配置问题走 HTTP 400（前端能弹提示引导去「接入 AI」页），运行时错误留给流内 error 事件。
    """
    if not llm.model:
        raise HTTPException(400, "缺少模型配置，请先在「接入 AI」里选择")
    if llm.provider == "cli-codex":
        status = get_subscription_ai_service().codex_status(force=True)
        if not status.installed:
            raise HTTPException(400, "未检测到 Codex CLI，请先安装官方 Codex。")
        if status.auth_status == "installed_not_logged_in":
            raise HTTPException(400, "Codex 尚未登录，请先完成官方 ChatGPT 登录。")
        if status.auth_status == "logged_in_api_key":
            raise HTTPException(400, "当前 Codex 使用 API Key，不属于会员额度接入。")
        if status.auth_status != "logged_in_chatgpt" or not status.available:
            raise HTTPException(400, "Codex 登录方式无法安全确认，请先在「接入 AI」重新检测。")
    elif llm.provider.startswith("cli-"):
        kind = llm.provider[4:]
        if not cli_runtime.detect_cli(kind):
            raise HTTPException(400, f"未检测到「{kind}」对应的本机命令。请先安装并登录该 CLI，或改用「API 接入」。")
    elif not llm.apiKey or not llm.baseURL:
        raise HTTPException(400, "缺少 Base URL 或 API Key，请先在「接入 AI」里填写")
    return llm.model_dump()


def _ndjson(events, cfg: dict | None = None):
    """把事件生成器包成 NDJSON 流；运行时异常转成流内 error 事件，不中断连接。"""
    def gen():
        try:
            for ev in events():
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:  # noqa: BLE001
            message = public_runtime_message(e) if cfg and cfg.get("provider") == "cli-codex" else str(e)
            yield json.dumps({"type": "error", "message": message}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


class DebateReq(BaseModel):
    code: str
    rounds: int = 1
    llm: LLMConfig


@app.post("/api/debate")
def debate(req: DebateReq):
    """多空辩论：后端先拉客观事实底稿，再让多方 / 空方 / 中立主持依次发言，**流式** NDJSON。

    刻意不产出买卖结论——终点是「分歧点 + 验证清单」，判断留给用户自己。
    """
    code = _validate(req.code)
    cfg = _check_llm(req.llm)
    rounds = 2 if req.rounds >= 2 else 1
    return _ndjson(lambda: debate_layer.run_debate_stream(cfg, code, rounds), cfg)


class ReflectReq(BaseModel):
    source: str
    title: str = ""
    llm: LLMConfig


@app.post("/api/reflect")
def reflect(req: ReflectReq):
    """反思：对一段已写好的分析做推理审计（哪些有数据支撑、最脆弱一环、验证清单），流式 NDJSON。"""
    if not (req.source or "").strip():
        raise HTTPException(400, "source 不能为空")
    cfg = _check_llm(req.llm)
    return _ndjson(lambda: reflect_layer.run_reflection_stream(cfg, req.source, req.title), cfg)


class HoldingIn(BaseModel):
    code: str
    shares: float
    cost: float


@app.get("/api/portfolio")
def portfolio_get():
    """持仓 + 实时盈亏（浮动盈亏红涨绿跌）。"""
    try:
        return {"data": pf.get_portfolio()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"持仓读取异常：{e}") from e


@app.post("/api/portfolio/holding")
def portfolio_add(h: HoldingIn):
    """加一笔持仓（同代码按加权平均成本合并）。存本地，不上传。"""
    code = (h.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "代码必须是 6 位数字")
    if h.shares <= 0:
        raise HTTPException(400, "数量必须大于 0")
    # 成本价不限正负：融券 / 返息 / 摊薄后为负成本等情形按结果计算，用户想怎么输就怎么输。
    return {"data": pf.add_holding(code, h.shares, h.cost)}


@app.delete("/api/portfolio/holding")
def portfolio_remove(code: str = Query(...)):
    return {"data": pf.remove_holding(code.strip())}


class FundHoldingIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")
    input_mode: str = Field(default="shares_cost", pattern=r"^(amount_pnl|shares_cost)$")
    amount_snapshot: float | None = Field(default=None, allow_inf_nan=False)
    cumulative_pnl_snapshot: float | None = Field(default=None, allow_inf_nan=False)
    shares: float | None = Field(default=None, allow_inf_nan=False)
    avg_unit_cost: float | None = Field(default=None, allow_inf_nan=False)
    avg_cost: float | None = Field(default=None, allow_inf_nan=False)
    buy_date: str = ""
    notes: str = Field(default="", max_length=1000)
    custom_tag_ids: list[str] = Field(default_factory=list, max_length=50)
    verification_status: str = Field(default="verified", pattern=r"^(verified|manual_unverified)$")
    manual_name: str | None = Field(default=None, max_length=100)
    replace: bool = False

    @field_validator("notes")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("manual_name")
    @classmethod
    def _manual_name(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("buy_date")
    @classmethod
    def _date_format(cls, value: str) -> str:
        from datetime import date
        if not value:
            return ""
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("买入日期格式应为 YYYY-MM-DD") from None
        return value

    @field_validator("custom_tag_ids")
    @classmethod
    def _tag_ids(cls, value: list[str]) -> list[str]:
        clean = [item.strip() for item in value]
        if any(not item for item in clean):
            raise ValueError("标签 ID 不能为空")
        return list(dict.fromkeys(clean))

    @model_validator(mode="after")
    def _mode_fields(self):
        if self.verification_status == "manual_unverified" and not self.manual_name:
            raise ValueError("手动录入模式必须填写基金名称")
        if self.input_mode == "amount_pnl":
            if self.amount_snapshot is None or not math.isfinite(self.amount_snapshot) or self.amount_snapshot <= 0:
                raise ValueError("快速模式的当前持有金额必须大于 0")
            if self.cumulative_pnl_snapshot is not None and not math.isfinite(self.cumulative_pnl_snapshot):
                raise ValueError("当前累计盈亏必须是有限数")
            return self
        if self.shares is None or not math.isfinite(self.shares) or self.shares <= 0:
            raise ValueError("精确模式的持有份额必须大于 0")
        unit_cost = self.avg_unit_cost if self.avg_unit_cost is not None else self.avg_cost
        if unit_cost is None or not math.isfinite(unit_cost) or unit_cost < 0:
            raise ValueError("精确模式的平均单位成本必须大于等于 0")
        if not self.buy_date:
            raise ValueError("精确模式必须填写买入日期")
        self.avg_unit_cost = unit_cost
        self.avg_cost = unit_cost
        return self


def _fund_portfolio_or_409() -> dict:
    data = fpf.list_fund_holdings()
    if data.get("data_status") == "corrupt":
        raise HTTPException(409, "基金持仓文件已损坏；只读加载已失败，未执行任何写入")
    return data


@app.get("/api/funds/search")
def funds_search(q: str = Query(..., min_length=1, max_length=100)):
    return {"data": fund_service.get_service().search_funds(q)}


@app.get("/api/funds/{code}/analysis")
def fund_analysis(code: str = ApiPath(..., pattern=r"^\d{6}$")):
    return {"data": fund_service.get_service().get_fund_analysis(code)}


@app.post("/api/funds/{code}/refresh")
def fund_refresh(code: str = ApiPath(..., pattern=r"^\d{6}$")):
    return {"data": fund_service.get_service().get_fund_analysis(code, force_refresh=True)}


@app.get("/api/fund-portfolio")
def fund_portfolio_get():
    return {"data": _fund_portfolio_or_409()}


@app.get("/api/fund-portfolio/analysis")
def fund_portfolio_analysis():
    holdings = _fund_portfolio_or_409()["holdings"]
    return {"data": fund_service.get_service().get_portfolio_analysis(holdings)}


@app.post("/api/fund-portfolio/holding")
def fund_portfolio_upsert(holding: FundHoldingIn):
    try:
        payload = holding.model_dump(exclude={"replace"})
        if holding.input_mode == "amount_pnl":
            payload.update({
                "shares": None,
                "shares_source": None,
                "basis_nav": None,
                "basis_nav_date": None,
                "shares_inference_note": None,
            })
            try:
                analysis = fund_service.get_service().get_fund_analysis(holding.code)
                latest_section = analysis.get("latest_nav") or {}
                latest = latest_section.get("data") or {}
                meta = latest_section.get("meta") or {}
                nav = latest.get("unit_nav")
                nav_date = latest.get("nav_date")
                reliable = (
                    meta.get("status") == "official"
                    and not meta.get("is_stale", False)
                    and isinstance(nav, (int, float))
                    and math.isfinite(nav)
                    and nav > 0
                    and bool(nav_date)
                )
                if reliable:
                    payload.update({
                        "shares": holding.amount_snapshot / nav,
                        "shares_source": "inferred",
                        "basis_nav": nav,
                        "basis_nav_date": nav_date,
                        "shares_inference_note": f"按用户录入金额快照与 {nav_date} 正式净值推算，非用户确认份额",
                    })
            except Exception:
                # Public NAV lookup is optional; the user's explicit amount remains writable.
                pass
        else:
            payload["shares_source"] = "user"
        return {"data": fpf.upsert_fund_holding(payload, replace=holding.replace)}
    except fpf.FundAlreadyExists as error:
        raise HTTPException(409, str(error)) from error
    except fpf.FundPortfolioCorrupt as error:
        raise HTTPException(409, str(error)) from error


@app.delete("/api/fund-portfolio/holding")
def fund_portfolio_delete(code: str = Query(..., pattern=r"^\d{6}$")):
    try:
        return {"data": fpf.delete_fund_holding(code)}
    except fpf.FundPortfolioCorrupt as error:
        raise HTTPException(409, str(error)) from error


# ---- 我的研报（用户上传自己的研报，存本地、不上传、不进开源仓库）----

class ReportIn(BaseModel):
    name: str
    content_b64: str


@app.get("/api/myreports")
def myreports_list():
    return {"data": mr.list_reports()}


@app.post("/api/myreports")
def myreports_upload(r: ReportIn):
    """上传一份研报（base64）→ 存本地 + 按文件名自动打行业标签。"""
    try:
        return {"data": mr.save_report(r.name, r.content_b64)}
    except mr.ReportError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/myreports/file/{rid}")
def myreports_file(rid: str):
    """下载/预览某份研报原文件。"""
    hit = mr.report_path(rid)
    if not hit:
        raise HTTPException(404, "研报不存在")
    path, name = hit
    return FileResponse(str(path), filename=name)


@app.delete("/api/myreports/{rid}")
def myreports_delete(rid: str):
    return {"data": {"ok": mr.delete_report(rid)}}


class CloseIn(BaseModel):
    code: str
    date: str
    price: float
    shares: float
    cost: float


@app.post("/api/portfolio/close")
def portfolio_close(c: CloseIn):
    """记一笔已清仓（已实现盈亏）。存本地。"""
    code = (c.code or "").strip()
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(400, "代码必须是 6 位数字")
    if c.price <= 0 or c.shares <= 0:
        raise HTTPException(400, "清仓价与股数必须大于 0")
    # 买入成本不限正负（同持仓录入）：按 (清仓价 - 成本) × 股数 的结果计算已实现盈亏。
    date = (c.date or "").strip()
    if not date:
        raise HTTPException(400, "请填清仓日期")
    from datetime import datetime
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "清仓日期格式应为 YYYY-MM-DD") from None
    return {"data": pf.close_position(code, date, c.price, c.shares, c.cost)}


@app.delete("/api/portfolio/close")
def portfolio_close_remove(index: int = Query(...)):
    return {"data": pf.remove_closed(index)}


@app.post("/api/portfolio/refresh")
def portfolio_refresh():
    """手动刷新：立即重拉行情算盈亏。"""
    try:
        return {"data": pf.get_portfolio()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"刷新失败：{e}") from e


@app.get("/api/radar")
def radar():
    """资讯雷达：12 赛道公开 RSS 资讯（读缓存，无缓存返回赛道骨架）。"""
    try:
        return {"data": newsradar.get_radar(force=False)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"资讯雷达异常：{e}") from e


@app.post("/api/radar/refresh")
def radar_refresh():
    """强制重抓全部 RSS 源（耗时约 20-40s），更新缓存。"""
    try:
        return {"data": newsradar.fetch_radar()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"资讯雷达刷新失败：{e}") from e


class RadarSourceInput(BaseModel):
    source_type: Literal["rss", "api"] = "rss"
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2048)
    hint: str = Field(min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=64)
    api_adapter: str | None = Field(default=None, max_length=64)


class RadarSourceEnabledInput(BaseModel):
    enabled: bool


def get_news_source_manager() -> news_source_manager.SourceManager:
    return news_source_manager.SourceManager()


def _raise_news_source_error(error: Exception) -> None:
    if isinstance(error, news_source_manager.SourceNotFoundError):
        status_code = 404
    elif isinstance(
        error,
        (
            news_source_manager.SourceStoreCorruptError,
            news_source_manager.DuplicateSourceError,
            news_source_manager.BuiltinSourceDeletionError,
        ),
    ):
        status_code = 409
    else:
        status_code = 400
    raise HTTPException(status_code, str(error)) from error


@app.get("/api/radar/sources")
def radar_sources_list():
    try:
        return {"data": get_news_source_manager().list_sources()}
    except news_source_manager.SourceManagerError as error:
        _raise_news_source_error(error)


@app.post("/api/radar/sources/test")
def radar_sources_test(source: RadarSourceInput):
    try:
        return {"data": get_news_source_manager().test_definition(source.model_dump())}
    except news_source_manager.SourceManagerError as error:
        _raise_news_source_error(error)


@app.post("/api/radar/sources", status_code=201)
def radar_sources_add(source: RadarSourceInput):
    try:
        return {"data": get_news_source_manager().add_source(source.model_dump())}
    except news_source_manager.SourceManagerError as error:
        _raise_news_source_error(error)


@app.post("/api/radar/sources/{source_id}/test")
def radar_source_test_existing(source_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,64}$")):
    try:
        return {"data": get_news_source_manager().test_source(source_id)}
    except news_source_manager.SourceManagerError as error:
        _raise_news_source_error(error)


@app.put("/api/radar/sources/{source_id}/enabled")
def radar_source_set_enabled(body: RadarSourceEnabledInput, source_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,64}$")):
    try:
        return {"data": get_news_source_manager().set_enabled(source_id, body.enabled)}
    except news_source_manager.SourceManagerError as error:
        _raise_news_source_error(error)


@app.delete("/api/radar/sources/{source_id}")
def radar_source_delete(source_id: str = ApiPath(pattern=r"^[A-Za-z0-9_-]{1,64}$")):
    try:
        return {"data": get_news_source_manager().delete_source(source_id)}
    except news_source_manager.SourceManagerError as error:
        _raise_news_source_error(error)


@app.get("/api/signals/gpu-rent")
def signals_gpu_rent():
    """GPU 租金信号（算力温度计）：读缓存，无缓存返回结构骨架。"""
    try:
        return {"data": signals.get_gpu_rent(force=False)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"GPU 租金信号异常：{e}") from e


@app.post("/api/signals/gpu-rent/refresh")
def signals_gpu_rent_refresh():
    """强制重抓 Vast 现货 + Kalshi 远期（约 10-20s：远期逐档串行限流），更新缓存。"""
    try:
        return {"data": signals.fetch_gpu_rent()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"GPU 租金信号刷新失败：{e}") from e


@app.get("/api/market/overview")
def market_overview():
    """市场情绪 + 板块资金流（板块/大盘级，全站共享缓存 5 分钟）。"""
    try:
        return {"data": market.get_overview()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"市场总览异常：{e}") from e


@app.get("/api/market/emotion")
def market_emotion():
    """短线情绪：连板梯队 / 最高连板 / 炸板率 / 封板率 / 晋级率 / 涨跌停家数。

    含连板梯队个股清单（code/name/连板数等）——2026-07-05 起如实展示客观公开榜单（东财同款），
    只呈现事实，不附推荐/评分/预测/买卖时机。全站共享缓存 5 分钟。
    """
    try:
        return {"data": market.get_short_term_emotion()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"短线情绪异常：{e}") from e


@app.get("/api/market/turnover-top")
def market_turnover_top():
    """全市场成交额榜 Top20（客观公开榜单数据，非推荐/非预测/不评分）。全站共享缓存 5 分钟。"""
    try:
        return {"data": market.get_turnover_top()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"成交额榜异常：{e}") from e


@app.get("/api/global/indices")
def global_indices():
    """全球指数快照（道指 / 标普500 / 纳斯达克 / 恒生 / 恒生科技）—— A 股看隔夜外围脸色。缓存 5 分钟。"""
    try:
        return {"data": market.get_global_indices()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"全球指数异常：{e}") from e


@app.get("/api/global/stock")
def global_stock(symbol: str = Query(..., min_length=1, max_length=16)):
    """美股 / 港股个股聚合：行情 + 关键财务指标（东财域内源）。symbol 如 AAPL / BABA / 00700。"""
    try:
        data = gstock.us_hk_stock(symbol.strip())
        if not data:
            raise HTTPException(404, f"未找到美股/港股代码「{symbol}」")
        return {"data": data}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"美港股查询异常：{e}") from e


@app.get("/api/global/hk/cashflow")
def global_hk_cashflow(symbol: str = Query(..., min_length=1, max_length=16)):
    """港股现金流量表（东财域内源 RPT_HKSK_FN_CASHFLOW）：经营/投资/筹资/净增加，多期。symbol 如 00700。"""
    try:
        data = gstock.hk_cashflow(symbol.strip())
        if not data:
            raise HTTPException(404, f"未找到港股「{symbol}」的现金流数据（仅港股支持）")
        return {"data": data}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"港股现金流查询异常：{e}") from e


@app.get("/api/indices")
def indices():
    """A股大盘指数实时行情（上证/深证成指/创业板指/沪深300）。仅标准库。"""
    try:
        return {"data": astock.index_quote()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"指数行情异常：{e}") from e


@app.get("/api/quote")
def quote(codes: str = Query(..., description="逗号分隔的 6 位代码")):
    """实时行情：现价/涨跌/PE/PB/市值/换手/涨跌停。仅标准库，永远可用。"""
    lst = [c.strip() for c in codes.split(",") if c.strip()]
    if not lst or any(not c.isdigit() or len(c) != 6 for c in lst):
        raise HTTPException(400, "codes 必须是逗号分隔的 6 位数字")
    try:
        return {"data": astock.tencent_quote(lst)}
    except Exception as e:  # noqa: BLE001 — 边界统一兜底
        raise HTTPException(502, f"行情源异常：{e}") from e


import time as _time
_PCT_CACHE: dict = {}


@app.get("/api/valuation/percentile")
def valuation_percentile(code: str = Query(...)):
    """PE-TTM / PB 历史分位（近5年）。全站缓存 30 分钟/代码（历史序列日频、变化慢）。"""
    code = _validate(code)
    hit = _PCT_CACHE.get(code)
    if hit and _time.time() - hit[0] < 1800:
        return {"data": hit[1]}
    try:
        data = astock.valuation_percentile(code)
        _PCT_CACHE[code] = (_time.time(), data)
        return {"data": data}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"估值分位异常：{e}") from e


_ANN_CACHE: dict = {}


@app.get("/api/announcements")
def announcements(code: str = Query(...)):
    """个股近期公告（东财，仅 requests）。缓存 15 分钟/代码。"""
    code = _validate(code)
    hit = _ANN_CACHE.get(code)
    if hit and _time.time() - hit[0] < 900:
        return {"data": hit[1]}
    try:
        data = astock.announcements(code)
        _ANN_CACHE[code] = (_time.time(), data)
        return {"data": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"公告源异常：{e}") from e


_FIN_CACHE: dict = {}


@app.get("/api/financials")
def financials(code: str = Query(...)):
    """财务关键指标（同花顺财务摘要，最新报告期）。缓存 30 分钟/代码。"""
    code = _validate(code)
    hit = _FIN_CACHE.get(code)
    if hit and _time.time() - hit[0] < 1800:
        return {"data": hit[1]}
    try:
        data = astock.financials(code)
        _FIN_CACHE[code] = (_time.time(), data)
        return {"data": data}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"财务摘要异常：{e}") from e


@app.get("/api/valuation")
def valuation(code: str = Query(...)):
    """完整估值：行情 + 一致预期 + 前向PE/PEG/消化年数。"""
    code = _validate(code)
    try:
        return {"data": astock.full_valuation(code)}
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"估值计算异常：{e}") from e


@app.get("/api/reports")
def reports(code: str = Query(...), pages: int = Query(2, ge=1, le=5)):
    """个股研报列表（东财，含 PDF 链接）。仅需 requests。"""
    code = _validate(code)
    try:
        rows = astock.eastmoney_reports(code, max_pages=pages)
        for r in rows:
            r["pdfUrl"] = astock.pdf_url(r.get("infoCode", "")) if r.get("infoCode") else None
        return {"data": rows}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"研报源异常：{e}") from e


@app.get("/api/news")
def news(code: str = Query(...), limit: int = Query(20, ge=1, le=50)):
    """个股新闻（东财，需 akshare）。"""
    code = _validate(code)
    try:
        return {"data": astock.stock_news(code, limit=limit)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"新闻源异常：{e}") from e


@app.get("/api/info")
def info(code: str = Query(...)):
    """个股基本面：行业/股本/上市时间（需 akshare）。"""
    code = _validate(code)
    try:
        return {"data": astock.individual_info(code)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"基本面源异常：{e}") from e


@app.get("/api/disclosure")
def disclosure(code: str = Query(...)):
    """巨潮公告列表（需 akshare）。"""
    code = _validate(code)
    try:
        return {"data": astock.disclosure(code)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"公告源异常：{e}") from e


@app.get("/api/kline")
def kline(code: str = Query(...), category: int = Query(4), offset: int = Query(60, ge=1, le=800)):
    """K线（需 mootdx）。category 4=日 5=周 6=月 11=60分钟。"""
    code = _validate(code)
    try:
        return {"data": astock.kline(code, category=category, offset=offset)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"K线源异常：{e}") from e


@app.get("/api/finance")
def finance(code: str = Query(...)):
    """季报财务快照（需 mootdx）。"""
    code = _validate(code)
    try:
        return {"data": astock.finance(code)}
    except astock.DependencyMissing as e:
        raise HTTPException(501, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"财务源异常：{e}") from e


# ---------------------------------------------------------------------------
# 资金面 / 筹码 / 信号（东财数据中心，v3.3 并入）—— 均为「用户查的那只股」的公开数据。
# 东财有 1s 限流，这些多为日/季级静态数据，统一走 30 分钟缓存，进一步降低被封风险。
# ---------------------------------------------------------------------------

_DC_CACHE: dict = {}  # key=(endpoint, code) -> (ts, data)


def _cached(endpoint: str, code: str, ttl: int, fetch):
    key = (endpoint, code)
    hit = _DC_CACHE.get(key)
    if hit and _time.time() - hit[0] < ttl:
        return hit[1]
    data = fetch()
    _DC_CACHE[key] = (_time.time(), data)
    return data


@app.get("/api/margin")
def margin(code: str = Query(...)):
    """融资融券明细（东财，日级）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("margin", code, 1800, lambda: astock.margin_trading(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"融资融券异常：{e}") from e


@app.get("/api/block-trade")
def block_trade(code: str = Query(...)):
    """大宗交易（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("block", code, 1800, lambda: astock.block_trade(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"大宗交易异常：{e}") from e


@app.get("/api/holders")
def holders(code: str = Query(...)):
    """股东户数变化（东财，季度级）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("holders", code, 1800, lambda: astock.holder_num_change(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"股东户数异常：{e}") from e


@app.get("/api/dividend")
def dividend(code: str = Query(...)):
    """分红送转历史（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("dividend", code, 1800, lambda: astock.dividend_history(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"分红送转异常：{e}") from e


@app.get("/api/fund-flow")
def fund_flow(code: str = Query(...)):
    """个股资金流（东财 push2his，120 日主力净流入）。缓存 15 分钟。
    注：push2his 对部分大陆住宅 IP 有间歇风控，可能返回空（非代码问题）。"""
    code = _validate(code)
    try:
        return {"data": _cached("fundflow", code, 900, lambda: astock.stock_fund_flow_120d(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"资金流异常：{e}") from e


@app.get("/api/dragon-tiger")
def dragon_tiger(code: str = Query(...)):
    """龙虎榜：该股近期上榜记录 + 买卖席位 + 机构净买（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("dt", code, 1800, lambda: astock.dragon_tiger_board(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"龙虎榜异常：{e}") from e


@app.get("/api/lockup")
def lockup(code: str = Query(...)):
    """限售解禁日历：历史解禁 + 未来 90 天待解禁（东财）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("lockup", code, 1800, lambda: astock.lockup_expiry(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"解禁日历异常：{e}") from e


@app.get("/api/blocks")
def blocks(code: str = Query(...)):
    """个股所属板块/概念归属（东财 slist）。缓存 30 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("blocks", code, 1800, lambda: astock.concept_blocks(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"板块归属异常：{e}") from e


@app.get("/api/hot-concepts")
def hot_concepts(code: str = Query(...)):
    """个股当下被市场归到哪些概念在炒（东财热门概念命中）。缓存 15 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("hotcon", code, 900, lambda: astock.hot_concepts(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"热门概念异常：{e}") from e


@app.get("/api/investor-qa")
def investor_qa(code: str = Query(...)):
    """互动易问答（巨潮）：投资者提问 + 公司回复。缓存 15 分钟。"""
    code = _validate(code)
    try:
        return {"data": _cached("irm", code, 900, lambda: astock.investor_qa(code))}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"互动易异常：{e}") from e


@app.get("/api/industry")
def industry(top: int = Query(20, ge=5, le=50)):
    """全行业涨跌幅排名（东财行业板块，板块级、零个股名单）。缓存 5 分钟。"""
    key = ("industry", str(top))
    hit = _DC_CACHE.get(key)
    if hit and _time.time() - hit[0] < 300:
        return {"data": hit[1]}
    try:
        data = astock.industry_comparison(top_n=top)
        _DC_CACHE[key] = (_time.time(), data)
        return {"data": data}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"行业排名异常：{e}") from e
