# PP03｜数据源扩展实验室 A1 实施计划  
# 现有数据源体检、统一健康注册中心与低风险修复

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐项实施。各步骤使用复选框追踪。  
> 默认采用子代理驱动并进行任务级审查；若当前环境不支持，再在本会话连续执行。不要把技术执行方式交给用户选择。

**Goal：** 对 PP03 当前全部基金、行情、行业和资讯数据源进行一次实时全量体检，建立统一健康注册中心与持续健康记录，完成明确、可逆、不改变数据语义的低风险修复，并在“市场资讯 → 数据说明”中增加数据源健康入口。

**Architecture：** 新增独立的 `source_health` 层，统一登记现有 Provider 和 RSS／Atom 来源，按“来源 × 能力”执行探测、评分、失败归类、修复价值判断和本地历史记录。健康中心只能观察和报告现有数据链，不复制业务数据、不改变 Provider 优先级、不在运行时修改代码或数据源配置。

**Tech Stack：** Python 3.12、FastAPI、requests、线程池、JSON／JSONL 原子存储、pytest、React 19、TypeScript、Tailwind CSS、Vitest、Testing Library、Vite。

---

## 一、Work 身份与基线

```text
项目：PP03｜AI Jiyan
Work：数据源扩展实验室 A1
名称：现有数据源体检与低风险修复
正式项目目录：D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan
GitHub：https://github.com/hanchanqaq-source/PP03-AI-Jiyan.git

基线分支：codex/pp03-workspace-layout
基线 Commit：d189fcf75fcaa7181285eeb8e1113e6b275a5baf

新分支：codex/pp03-data-source-lab-a1-health
```

开始前必须：

```powershell
cd "D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan"
git fetch --all --prune
git status --short
git rev-parse HEAD
git rev-parse origin/codex/pp03-workspace-layout
```

只有同时满足以下条件才可继续：

```text
本地工作区 clean
本地基线与远程基线一致
基线 Commit 为 d189fcf75fcaa7181285eeb8e1113e6b275a5baf
```

随后创建：

```powershell
git switch -c codex/pp03-data-source-lab-a1-health `
  d189fcf75fcaa7181285eeb8e1113e6b275a5baf
```

不得：

```text
修改 main
force push
合并其他业务分支
开始 V0.2-W3
进入“新增金融数据源”阶段
进入“AI API / 免费额度网关”阶段
```

---

# 二、全局约束

## 2.1 本 Work 允许做

```text
现有数据源能力矩阵
一次实时全量体检
统一健康状态记录
失败原因分类
响应时间与字段完整率检测
数据新鲜度检测
持续90天健康历史
页面数据源健康入口
失效公开URL更新
永久重定向地址更新
请求头补充
合理超时
有限重试和退避
RSS / Atom / XML解析兼容
gzip与字符编码兼容
来源身份冲突修复
完全重复配置清理
错误原因脱敏
缓存和实时状态误标修复
明确且不改变字段含义的解析修复
```

## 2.2 本 Work 禁止做

```text
新增 efinance、yfinance、FRED、EIA、SEC、RSSHub 等正式 Provider
申请、读取或写入 API Key
改变现有 Provider 优先级
改变主源 / 备用源顺序
使用登录 Cookie
关闭 TLS 校验
绕过反爬、登录、权限或付费墙
抓取禁止自动访问的页面
根据基金名称推断行业
根据股票名称推断行业
把官方行业配置当作重仓股穿透结果
把产业链标签当作行业配置比例
归一化未知、未披露股票或非股票资产
运行时自动修改源码或数据源配置
```

## 2.3 用户数据边界

健康体检不得读取或记录：

```text
用户持仓金额
用户持仓成本
用户买入日期
用户备注
用户自定义标签
API Key
Cookie
账号
浏览器状态
本地模型凭据
```

固定使用公开测试样本和公开来源。

---

# 三、先固化设计与计划

## Task 0：保存规格文档和实施计划

**Files：**

```text
Create:
docs/superpowers/specs/2026-08-18-pp03-source-health-a1-design.md
docs/superpowers/plans/2026-08-18-pp03-source-health-a1.md
```

- [ ] **Step 1：保存已确认设计**

规格文档必须完整记录：

```text
统一健康注册中心
来源 × 能力评级
基金公开测试样本
资讯逐源探测
初始评级与稳定评级分离
90天JSONL历史
启动快速检查
手动全量体检
数据说明中的健康入口
允许的低风险修复
禁止的高风险变更
```

- [ ] **Step 2：保存本实施计划**

将当前任务书原样保存到：

```text
docs/superpowers/plans/2026-08-18-pp03-source-health-a1.md
```

- [ ] **Step 3：自审规格和计划**

检查：

```text
无 TBD
无 TODO
无占位字段
无旧 D:\AI_Workspace\04-Projects 活动路径
无旧 C 盘活动路径
规格与计划没有范围冲突
未包含用户数据或密钥
```

- [ ] **Step 4：提交规格与计划**

```powershell
git add docs/superpowers/specs/2026-08-18-pp03-source-health-a1-design.md `
        docs/superpowers/plans/2026-08-18-pp03-source-health-a1.md
git commit -m "docs(pp03): define source health lab a1"
```

---

# 四、统一健康数据合同

## Task 1：健康模型与数据源注册表

**Files：**

```text
Create:
backend/source_health/__init__.py
backend/source_health/models.py
backend/source_health/registry.py
backend/source_health/samples.py

Test:
backend/tests/test_source_health_registry.py
```

## 4.1 接口

`backend/source_health/models.py` 至少定义：

```python
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SourceGroup = Literal["fund", "quote", "industry", "news"]
ProbeStatus = Literal["success", "partial", "failure"]
HealthLabel = Literal["healthy", "usable", "degraded", "failed"]
RatingConfidence = Literal["initial", "growing", "stable"]
RepairValue = Literal[
    "none",
    "immediate_fix",
    "worth_fixing",
    "observe",
    "replace_candidate",
    "disable_candidate",
]

ErrorType = Literal[
    "none",
    "timeout",
    "dns",
    "tls",
    "connection",
    "http",
    "redirect",
    "rate_limit",
    "authentication",
    "parse",
    "empty_payload",
    "schema_changed",
    "stale_data",
    "unknown",
]


@dataclass(frozen=True)
class SourceDescriptor:
    source_id: str
    source_name: str
    group: SourceGroup
    capability: str
    source_reference: str
    priority: int
    critical: bool
    requires_api_key: bool
    probe_kind: str
    probe_args: dict[str, Any] = field(default_factory=dict)
    freshness_max_age_seconds: int | None = None


@dataclass
class ProbeObservation:
    source_id: str
    source_name: str
    group: SourceGroup
    capability: str
    started_at: str
    finished_at: str
    latency_ms: int
    probe_status: ProbeStatus
    error_type: ErrorType
    error_message_redacted: str
    http_status: int | None
    returned_items: int
    data_as_of_date: str | None
    freshness_seconds: int | None
    field_completeness_pct: float | None
    used_cache: bool
    cache_status: str
    fallback_available: bool
    redirected: bool
    final_reference: str | None
    rating_score: float = 0.0
    rating: HealthLabel = "failed"
    rating_confidence: RatingConfidence = "initial"
    repair_value: RepairValue = "none"
    repair_reason: str = ""
    consecutive_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

## 4.2 固定 Provider 注册条目

注册表必须从实际 Provider 类和其 `capabilities` 自动生成，预期至少覆盖：

```text
cninfo-industry
  stock_industry_classification

eastmoney-direct
  search
  nav_history
  stock_snapshot

tencent-quote
  stock_snapshot

akshare-eastmoney
  search
  profile
  nav_history
  holdings
  industry_allocation

akshare-danjuan
  profile
```

不得在注册表中复制一套新的主备优先级；优先级只读取现有 Provider 的 `priority`。

## 4.3 资讯来源身份

资讯来源 ID 必须由下列三个字段共同生成：

```text
hint
name
url
```

示例：

```python
import hashlib

def news_source_id(hint: str, name: str, url: str) -> str:
    raw = f"{hint.strip()}|{name.strip()}|{url.strip()}"
    return "news:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
```

同一 URL 出现在不同赛道时仍是不同配置身份。

## 4.4 公共测试样本

创建：

```text
backend/source_health/source-health-samples.json
```

由一次公开目录发现脚本生成，至少覆盖：

```text
主动混合型
股票型
指数型
ETF联接
债券型
货币型
QDII
FOF
新成立基金
公开披露不完整基金
```

每条必须保存：

```json
{
  "category": "QDII",
  "code": "实际验证后的六位代码",
  "name": "实际公开基金名称",
  "fund_type": "实际公开类型",
  "verified_at": "ISO时间",
  "source_name": "公开目录来源"
}
```

样本选择规则：

```text
从公开基金目录读取
按基金类型匹配
代码升序选择首个可成功取得 profile 的样本
不使用用户持仓
不因基金名称推断行业
```

若某一类别在实时目录中找不到可靠样本，记录：

```text
sample_status = unavailable
reason = 实际原因
```

不得伪造代码。

## 4.5 TDD

- [ ] **Step 1：先写失败测试**

至少测试：

```python
def test_registry_expands_provider_by_capability():
    rows = build_provider_descriptors(fake_providers())
    ids = {row.source_id for row in rows}
    assert "fund:eastmoney-direct:search" in ids
    assert "fund:eastmoney-direct:nav_history" in ids
    assert "industry:cninfo-industry:stock_industry_classification" in ids


def test_news_source_identity_includes_track():
    left = news_source_id("ai", "Same", "https://example.com/feed")
    right = news_source_id("semi", "Same", "https://example.com/feed")
    assert left != right


def test_registry_never_contains_credentials():
    rows = build_registry(fake_providers(), fake_news_config())
    serialized = str([row.to_dict() for row in rows])
    assert "api_key" not in serialized.lower()
    assert "cookie" not in serialized.lower()
```

- [ ] **Step 2：运行 RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_source_health_registry.py -q
```

预期：模块不存在或接口不存在。

- [ ] **Step 3：实现模型、注册表与样本选择**

- [ ] **Step 4：运行 GREEN**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_source_health_registry.py -q
```

- [ ] **Step 5：提交**

```powershell
git add backend/source_health backend/tests/test_source_health_registry.py
git commit -m "feat(pp03): add source health registry"
```

---

# 五、生产同路径探测器

## Task 2：基金 Provider 与资讯来源探测

**Files：**

```text
Create:
backend/source_health/probe_errors.py
backend/source_health/probes/__init__.py
backend/source_health/probes/fund_provider.py
backend/source_health/probes/news_source.py

Modify:
backend/newsradar.py

Test:
backend/tests/test_source_health_probes.py
backend/tests/test_newsradar.py
```

## 5.1 基金 Provider 探测

健康探测必须直接调用单个 Provider 的：

```python
provider.fetch(capability, **probe_args)
```

不得调用 `FundDataService._fetch()`，否则无法区分到底哪个 Provider 成功。

每项能力必须验证：

```text
search
  返回 list
  至少一条包含 code、name
  code 为六位数字

profile
  包含 code、name、fund_type
  不要求所有可选字段都有值

nav_history
  points 非空
  latest.unit_nav 为正数
  latest.nav_date 可解析

holdings
  有 report_period
  holdings 为 list
  股票行包含 stock_code、stock_name、weight_pct

industry_allocation
  有 as_of_date
  industries 为 list
  行业行包含 name、weight_pct

stock_industry_classification
  requested_codes 非空
  至少一个 classifications 结果
  分类必须带 source_name / source_reference
  不从 SECNAME 或股票名推断分类

stock_snapshot
  返回 mapping
  股票代码与请求代码可对应
  price / change_pct 缺失时计入字段完整率，不伪造数值
```

## 5.2 资讯探测必须复用生产解析路径

在 `backend/newsradar.py` 暴露一个不写正式缓存的接口：

```python
def probe_source_config(
    source: dict,
    *,
    per_source: int = 2,
    recent_days: int = 30,
    timeout: float | None = None,
) -> dict:
    """
    使用与生产抓取相同的请求、解码和 RSS/Atom 解析逻辑。
    不写 radar.json，不读取用户数据，不合并事件。
    """
```

返回：

```python
{
    "status": "success" | "partial" | "failure",
    "items": [...],
    "http_status": 200,
    "error_type": "none",
    "error_message_redacted": "",
    "latency_ms": 123,
    "redirected": False,
    "final_url": "...",
    "content_type": "application/rss+xml",
    "returned_items": 2,
    "latest_published_at": "...",
}
```

生产刷新和健康探测必须共用同一个底层函数，避免“健康探测能解析、正式页面不能解析”。

## 5.3 错误分类

按异常映射：

```text
requests.Timeout / TimeoutError      → timeout
socket.gaierror                      → dns
SSLError                             → tls
ConnectionError                     → connection
HTTP 401 / 403                       → authentication
HTTP 408 / 429                       → rate_limit
HTTP 404 / 410                       → http
HTTP 5xx                             → http
XML / RSS / JSON 结构解析失败         → parse
成功连接但无有效内容                  → empty_payload
返回字段不符合现有合同                → schema_changed
数据日期超过能力新鲜度阈值            → stale_data
其余                                 → unknown
```

错误信息必须：

```text
最多 200 字符
移除 Authorization
移除 Cookie
移除 query 中可能的 token/key
移除 Windows 用户目录
移除完整本地路径
不返回 traceback
```

## 5.4 重试规则

只允许一次有限重试：

```text
timeout
connection
TLS临时错误
HTTP 408
HTTP 429
HTTP 500/502/503/504
```

规则：

```text
遵守 Retry-After
最大等待5秒
无 Retry-After 时等待0.5秒
401/403/404/410不重试
解析错误不重试
绝不 verify=False
```

## 5.5 TDD

- [ ] **Step 1：写失败测试**

覆盖：

```text
基金能力字段完整率
Provider 异常归类
永久重定向
HTTP 429 重试一次
TLS错误不关闭校验
RSS 2.0
Atom
带命名空间 Atom
gzip
GBK / 非UTF-8声明
XML解析失败
成功但空Feed
错误脱敏
探测不写 radar.json
```

示例：

```python
def test_probe_redacts_credentials_and_local_paths():
    error = RuntimeError(
        "GET https://x.test/feed?token=secret "
        "Authorization: Bearer abc "
        r"C:\Users\26365\private"
    )
    result = classify_probe_error(error)
    assert "secret" not in result.message
    assert "Bearer" not in result.message
    assert "26365" not in result.message
```

- [ ] **Step 2：运行 RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_source_health_probes.py `
  backend/tests/test_newsradar.py -q
```

- [ ] **Step 3：实现探测器和生产共用解析入口**

- [ ] **Step 4：运行 GREEN**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_source_health_probes.py `
  backend/tests/test_newsradar.py -q
```

- [ ] **Step 5：提交**

```powershell
git add backend/source_health/probe_errors.py `
        backend/source_health/probes `
        backend/newsradar.py `
        backend/tests/test_source_health_probes.py `
        backend/tests/test_newsradar.py
git commit -m "feat(pp03): add production-path source probes"
```

---

# 六、评分、修复价值与历史存储

## Task 3：健康评分、修复建议和90天历史

**Files：**

```text
Create:
backend/source_health/scoring.py
backend/source_health/repair_advisor.py
backend/source_health/storage.py

Modify:
backend/cache_management.py

Test:
backend/tests/test_source_health_scoring.py
backend/tests/test_source_health_storage.py
backend/tests/test_cache_management.py
```

## 6.1 评分维度

默认权重：

```python
DEFAULT_WEIGHTS = {
    "availability": 0.35,
    "freshness": 0.20,
    "completeness": 0.20,
    "latency": 0.10,
    "failure_streak": 0.10,
    "fallback": 0.05,
}
```

某一能力不适用新鲜度或缓存维度时：

```text
不把它计为0
移除该维度
对剩余权重按比例重新归一化
```

## 6.2 单项分数

可用性：

```text
success = 100
partial = 60
failure = 0
```

响应速度：

```text
<= 2秒     = 100
<= 5秒     = 80
<= 10秒    = 60
<= 15秒    = 40
> 15秒     = 10
超时       = 0
```

连续失败：

```text
0次 = 100
1次 = 70
2次 = 40
3次及以上 = 0
```

评级：

```text
85—100：healthy
70—84.99：usable
45—69.99：degraded
0—44.99：failed
```

前端中文：

```text
healthy   → 健康
usable    → 基本可用
degraded  → 降级
failed    → 失败
```

## 6.3 评级置信度

```text
initial：
  少于3个不同日期，或少于5个样本

growing：
  至少3个不同日期且至少5个样本，
  但不足7个不同日期或20个样本

stable：
  至少7个不同日期且至少20个样本
```

本 Work 不等待7天。

首次交付必须显示：

```text
初始评级 · 样本不足
```

## 6.4 修复价值判断

`repair_advisor.py` 固定规则：

```text
immediate_fix
  301/308 永久跳转且新地址为同一公开来源
  缺少标准请求头导致明确失败
  已确认的编码 / gzip / RSS / Atom兼容问题
  来源ID冲突
  完全相同的重复配置
  缓存状态误标

worth_fixing
  高价值来源出现可复现 schema_changed / parse
  公开入口变化但需要有限适配
  同一能力没有其他可靠备用源

observe
  单次 timeout / DNS / TLS / 5xx
  429 且有 Retry-After
  当前可使用可靠缓存

replace_candidate
  404/410
  域名长期不可解析
  页面不再提供公开Feed
  连续多日失败且无缓存
  修复需要绕过权限

disable_candidate
  完全重复且没有独立赛道意义
  内容与配置标签长期不符
  存在访问合规问题

none
  当前健康或没有足够证据
```

运行时只给建议，不自动修改代码或配置。

## 6.5 存储位置

默认：

```text
%VR_DATA_DIR%\source-health\
├─ current-summary.json
├─ last-run.json
└─ history\
   ├─ YYYY-MM-DD.jsonl
   └─ ...
```

未设置 `VR_DATA_DIR` 时沿用：

```text
%USERPROFILE%\.vibe-research\source-health
```

写入规则：

```text
current-summary.json：临时文件 + os.replace
last-run.json：临时文件 + os.replace
JSONL：CACHE_IO_LOCK 内追加
不写入用户持仓文件
不写入源码目录
```

## 6.6 保留策略

```text
current-summary.json 永久保留当前版
last-run.json 永久保留当前版
history 保留90天
超过90天自动删除
纳入500MB缓存总上限
```

修改 `cache_management.py`：

```text
新增 source_health 类别
只回收超过90天的 history
不得删除 current-summary.json
不得删除 last-run.json
不得删除健康配置或正式验收文档
```

## 6.7 TDD

- [ ] **Step 1：写失败测试**

至少覆盖：

```text
不适用维度权重重分配
评分边界
置信度升级
永久跳转立即修复
403建议替换而非绕过
单次超时只观察
90天历史清理
current-summary不被清理
原子写
并发追加不破坏JSONL
缓存管理不删除用户文件
```

- [ ] **Step 2：运行 RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_source_health_scoring.py `
  backend/tests/test_source_health_storage.py `
  backend/tests/test_cache_management.py -q
```

- [ ] **Step 3：实现**

- [ ] **Step 4：运行 GREEN**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_source_health_scoring.py `
  backend/tests/test_source_health_storage.py `
  backend/tests/test_cache_management.py -q
```

- [ ] **Step 5：提交**

```powershell
git add backend/source_health/scoring.py `
        backend/source_health/repair_advisor.py `
        backend/source_health/storage.py `
        backend/cache_management.py `
        backend/tests/test_source_health_scoring.py `
        backend/tests/test_source_health_storage.py `
        backend/tests/test_cache_management.py
git commit -m "feat(pp03): score and persist source health"
```

---

# 七、体检运行器与 API

## Task 4：快速检查、全量体检和 API

**Files：**

```text
Create:
backend/source_health/runner.py
backend/source_health/service.py

Modify:
backend/source_health/__init__.py
backend/app.py

Test:
backend/tests/test_source_health_runner.py
backend/tests/test_source_health_api.py
```

## 7.1 两种运行范围

快速检查：

```text
scope = quick
后端启动时最多24小时一次
只检查 critical=True 的关键来源和能力
后台执行
不得阻塞 API 启动
```

全量体检：

```text
scope = full
用户手动启动
检查全部基金能力和全部资讯配置
不等待7天
结果为初始评级
```

## 7.2 并发边界

```text
基金 / 行情 / 行业探测 max_workers = 4
资讯探测 max_workers = 20
单来源 timeout 使用来源配置或默认15秒
同一个 Provider 的非线程安全能力必须串行
全量体检同一时间只能运行一个
```

若已有体检正在运行：

```text
HTTP 409
message = 数据源体检正在运行
```

## 7.3 运行状态

```python
{
    "run_id": "20位稳定ID",
    "scope": "quick" | "full",
    "status": "queued" | "running" | "completed" | "failed",
    "started_at": "...",
    "finished_at": None,
    "total": 120,
    "completed": 35,
    "success": 30,
    "partial": 2,
    "failure": 3,
    "current_source": "..."
}
```

运行状态允许保存在内存中；最近完成状态同步写入 `last-run.json`。

## 7.4 API

新增：

```text
GET  /api/source-health/summary
GET  /api/source-health/sources
POST /api/source-health/runs
GET  /api/source-health/runs/{run_id}
```

### `GET /api/source-health/summary`

返回：

```json
{
  "rating_confidence": "initial",
  "last_run_at": "...",
  "fund": {
    "healthy": 8,
    "usable": 2,
    "degraded": 1,
    "failed": 0
  },
  "news": {
    "healthy": 100,
    "usable": 2,
    "degraded": 3,
    "failed": 3
  },
  "total_sources": 119,
  "reclaimable_bytes": 0
}
```

### `GET /api/source-health/sources`

查询参数：

```text
group=fund|quote|industry|news
rating=healthy|usable|degraded|failed
repair_value=...
```

### `POST /api/source-health/runs`

请求：

```json
{"scope": "full"}
```

返回：

```text
202 Accepted
```

以及 `run_id`。

### `GET /api/source-health/runs/{run_id}`

前端每2秒轮询，完成后停止。

## 7.5 后端启动

扩展现有 `_lifespan`：

```python
@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _run_startup_cache_cleanup()
    source_health.get_service().schedule_quick_if_due()
    yield
    source_health.get_service().shutdown()
```

要求：

```text
快速检查失败不能阻塞后端
启动不等待网络
测试环境可禁用启动体检
VR_SOURCE_HEALTH_STARTUP=0 时禁用
```

## 7.6 TDD

- [ ] **Step 1：写失败测试**

覆盖：

```text
quick只选择critical来源
full选择所有来源
24小时冷却
全量任务互斥
进度递增
部分来源失败不导致整次失败
完成后写summary和history
API 202 / 409 / 404
启动检查不阻塞
shutdown关闭线程池
不读取fund-portfolio.json
```

- [ ] **Step 2：运行 RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_source_health_runner.py `
  backend/tests/test_source_health_api.py -q
```

- [ ] **Step 3：实现**

- [ ] **Step 4：运行 GREEN**

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_source_health_runner.py `
  backend/tests/test_source_health_api.py -q
```

- [ ] **Step 5：提交**

```powershell
git add backend/source_health `
        backend/app.py `
        backend/tests/test_source_health_runner.py `
        backend/tests/test_source_health_api.py
git commit -m "feat(pp03): expose source health audit api"
```

---

# 八、市场资讯中的健康入口

## Task 5：数据说明中的健康摘要和详情

**Files：**

```text
Create:
frontend/src/features/source-health/types.ts
frontend/src/features/source-health/SourceHealthSummary.tsx
frontend/src/features/source-health/SourceHealthDrawer.tsx
frontend/src/features/source-health/__tests__/SourceHealthSummary.test.tsx
frontend/src/features/source-health/__tests__/SourceHealthDrawer.test.tsx

Modify:
frontend/src/features/market-news/DataInfoDialog.tsx
frontend/src/pages/MarketNews.tsx
frontend/src/lib/api.ts
frontend/src/features/pages/__tests__/CorePages.test.tsx
```

## 8.1 数据说明摘要

在现有“数据说明”弹窗中增加：

```text
数据源健康

基金与行情
8 健康 / 2 基本可用 / 1 降级 / 0 失败

资讯来源
100 健康 / 2 基本可用 / 3 降级 / 3 失败

最后体检
2026-08-18 14:30

评级状态
初始评级 · 样本不足

[查看详情] [运行全量体检]
```

若尚未运行：

```text
尚未完成数据源体检
[运行首次全量体检]
```

## 8.2 详情抽屉

分组：

```text
基金与行情 Provider
资讯来源
```

每行显示：

```text
来源名称
能力
状态
响应时间
返回条数
数据日期
字段完整率
最近成功
连续失败
修复价值
```

失败项可展开显示：

```text
错误类型
脱敏原因
最终公开地址
是否重定向
是否有备用来源
建议处理
```

不得显示：

```text
请求头
Cookie
Token
完整异常堆栈
本地隐私路径
```

## 8.3 全量体检交互

点击后：

```text
按钮变为“体检中”
显示 completed / total
每2秒轮询
完成后自动刷新摘要和详情
失败时保留上一次成功报告
```

不允许重复启动。

## 8.4 无障碍与焦点

```text
Escape 关闭详情抽屉
关闭后焦点回到“查看详情”
运行按钮有 aria-label
状态不只依赖颜色
加载、完成、失败使用可读文字
```

## 8.5 TDD

- [ ] **Step 1：写失败测试**

覆盖：

```text
没有体检记录
初始评级
摘要计数
按组查看
按状态过滤
失败原因展开
启动全量体检
轮询进度
409 已在运行
运行失败保留旧摘要
Escape与焦点恢复
不显示敏感字段
```

- [ ] **Step 2：运行 RED**

```powershell
npm --prefix frontend run test:run -- `
  src/features/source-health/__tests__/SourceHealthSummary.test.tsx `
  src/features/source-health/__tests__/SourceHealthDrawer.test.tsx `
  src/features/pages/__tests__/CorePages.test.tsx
```

- [ ] **Step 3：实现**

`DataInfoDialog` 不直接嵌套第二个模态层。推荐接口：

```tsx
export function DataInfoDialog({
  open,
  onClose,
  onOpenSourceHealth,
}: {
  open: boolean;
  onClose: () => void;
  onOpenSourceHealth: () => void;
})
```

`MarketNews` 管理：

```tsx
const [infoOpen, setInfoOpen] = useState(false);
const [sourceHealthOpen, setSourceHealthOpen] = useState(false);
```

点击健康详情时：

```tsx
setInfoOpen(false);
setSourceHealthOpen(true);
```

- [ ] **Step 4：运行 GREEN**

```powershell
npm --prefix frontend run test:run -- `
  src/features/source-health/__tests__ `
  src/features/pages/__tests__/CorePages.test.tsx
```

- [ ] **Step 5：提交**

```powershell
git add frontend/src/features/source-health `
        frontend/src/features/market-news/DataInfoDialog.tsx `
        frontend/src/pages/MarketNews.tsx `
        frontend/src/lib/api.ts `
        frontend/src/features/pages/__tests__/CorePages.test.tsx
git commit -m "feat(pp03): add source health ui"
```

---

# 九、首次全量体检与低风险修复

## Task 6：执行 before／after 审计并修复明确问题

**Files：**

```text
Create:
docs/acceptance/a1-source-capability-matrix.md
docs/acceptance/a1-source-health-before.md
docs/acceptance/a1-source-health-after.md
docs/acceptance/a1-source-repair-decisions.md
docs/acceptance/a1-source-replacement-candidates.md

Potentially modify only when supported by audit evidence:
backend/news_sources.json
backend/newsradar.py
backend/fund_data/providers/*.py
backend/source_health/*.py
corresponding tests
```

## 9.1 隔离环境

使用：

```text
D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1
```

环境变量：

```powershell
$env:VR_DATA_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1\data"
$env:VR_NEWS_CACHE_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1\news"
$env:VR_ACCEPTANCE_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1"
$env:VR_SOURCE_HEALTH_STARTUP="0"
```

不得读取真实用户数据。

## 9.2 Before 审计

运行一次全量体检并保存：

```text
实际注册来源数量
基金能力数量
资讯配置数量
成功
部分成功
失败
各错误类型
P50 / P95响应时间
字段完整率
数据新鲜度
永久重定向数量
完全重复配置数量
初始评级
```

能力矩阵至少包含：

```text
来源
能力
现有优先级
是否需要Key
公开入口
当前状态
样本
返回条数
数据日期
字段完整率
延迟
备用来源
许可证 / 访问边界
修复价值
```

## 9.3 低风险修复的硬条件

### 可以修改 URL 的条件

```text
旧地址明确返回301或308
最终地址属于同一官方来源
最终内容仍是公开Feed或公开API
新地址通过解析测试
没有登录、Cookie或付费要求
```

### 可以补请求头的条件

只允许：

```text
User-Agent
Accept
Accept-Encoding
Referer（仅限来源公开页面）
```

不得添加：

```text
Cookie
Authorization
绕过风控的私有头
浏览器指纹
```

### 可以修改解析器的条件

```text
必须保存最小脱敏响应Fixture
先写失败测试
修复只针对实际格式
不靠正则猜测金融数字
```

### 可以删除重复配置的条件

只有以下字段完全一致才可自动去重：

```text
hint
name
url
language
region
```

同一 URL 跨不同赛道不得删除。

### 不得自动修复

```text
401 / 403
登录墙
验证码
Cloudflare挑战
付费Feed
robots或条款限制
需要关闭TLS校验
需要外部共享Key
需要调整Provider优先级
```

这些只能记录为：

```text
observe
replace_candidate
disable_candidate
```

## 9.4 每项修复必须有证据

`a1-source-repair-decisions.md` 每项记录：

```text
source_id
修复前状态
根因
修改文件
修改内容
为什么属于低风险
对应测试
修复后状态
是否改变业务数据含义：否
```

## 9.5 After 审计

使用完全相同的样本和配置再次运行。

比较：

```text
成功率变化
失败数变化
P50 / P95变化
字段完整率变化
永久跳转减少
解析错误减少
未解决问题
```

不能为了提高成功率删除有价值的失败源。

## 9.6 候选替代源

可以把下列候选写进文档，但不得接入：

```text
efinance
yfinance
FRED
EIA
SEC EDGAR
RSSHub
Finnhub
Twelve Data
Alpha Vantage
Tushare
```

每项只记录：

```text
候选能力
是否需要Key
许可证
预计替代哪个失败能力
为什么暂不接入
```

## 9.7 提交低风险修复

每一类修复独立提交，示例：

```powershell
git commit -m "fix(pp03): repair public feed redirects"
git commit -m "fix(pp03): harden rss format compatibility"
git commit -m "fix(pp03): correct source health diagnostics"
```

不得把所有不相关改动压成一个巨大提交。

---

# 十、最终验证与浏览器验收

## Task 7：自动测试、页面验收、证据和推送

**Files：**

```text
Create:
docs/screenshots/source-health-a1/01-health-summary.png
docs/screenshots/source-health-a1/02-fund-provider-details.png
docs/screenshots/source-health-a1/03-news-source-details.png
docs/screenshots/source-health-a1/04-audit-progress.png
docs/screenshots/source-health-a1/05-before-after-summary.png

Modify:
docs/acceptance/a1-source-capability-matrix.md
docs/acceptance/a1-source-health-before.md
docs/acceptance/a1-source-health-after.md
docs/acceptance/a1-source-repair-decisions.md
docs/acceptance/a1-source-replacement-candidates.md
```

## 10.1 后端完整测试

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
```

要求：

```text
所有既有测试通过
所有A1新测试通过
联网体检结果不冒充离线单元测试
```

## 10.2 前端测试

```powershell
npm --prefix frontend run test:run
npm --prefix frontend run test:legacy
```

## 10.3 生产构建

```powershell
npm --prefix frontend run build
```

## 10.4 启动隔离服务

后端：

```powershell
$env:VR_DATA_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1\data"
$env:VR_NEWS_CACHE_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1\news"
$env:VR_ACCEPTANCE_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1"
backend\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8900
```

前端：

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5899
```

## 10.5 Computer Use 浏览器验收

自行完成，用户不操作。

依次验证：

```text
1. 打开 http://127.0.0.1:5899/market-news
2. 打开“数据说明”
3. 查看健康摘要
4. 打开健康详情
5. 查看基金与行情 Provider
6. 查看资讯来源
7. 按失败状态筛选
8. 展开一个失败来源的脱敏原因
9. 启动一次全量体检
10. 查看进度
11. 完成后刷新摘要
12. 确认显示“初始评级 · 样本不足”
13. 确认没有API Key、Cookie或本地隐私路径
14. 确认浏览器控制台0 error / 0 warn
```

## 10.6 页面验收标准

必须满足：

```text
健康摘要可见
基金和资讯分组可见
来源 × 能力结果可见
失败原因可展开
修复价值可见
全量体检可启动
体检期间不可重复启动
体检完成后数据更新
旧成功报告在本次失败时保留
初始评级标识准确
无新增一级导航
无用户数据
无密钥
```

## 10.7 数据合同回归

用既有基金行业测试确认：

```text
官方行业配置仍独立
重仓股穿透仍独立
产业链标签仍独立
未知部分未归一化
未披露股票未归一化
非股票资产未归一化
没有根据基金名或股票名生成行业事实
```

## 10.8 清理隔离数据

停止服务后删除：

```text
D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1
```

保留：

```text
Git跟踪的验收文档
Git跟踪的正式截图
源码
测试
规格和计划
```

## 10.9 最终审查

```powershell
git diff --check
git status --short
git log --oneline `
  d189fcf75fcaa7181285eeb8e1113e6b275a5baf..HEAD
```

执行敏感信息审查：

```text
API Key
Token
Cookie
Authorization
真实用户路径
用户持仓
临时缓存
日志
```

## 10.10 最终提交与推送

```powershell
git add docs/acceptance `
        docs/screenshots/source-health-a1
git commit -m "docs(pp03): record source health a1 acceptance"

git push -u origin codex/pp03-data-source-lab-a1-health
```

确认：

```text
远程 SHA 与本地一致
工作区 clean
未创建 PR
未合并 main
未开始 V0.2-W3
```

---

# 十一、完成标准

以下全部满足才可判定 PASS：

- [ ] 现有 Provider 已按能力注册；
- [ ] 全部资讯配置已注册；
- [ ] 来源 ID 稳定且跨赛道不串线；
- [ ] 基金探测使用公开测试样本；
- [ ] 资讯探测复用生产解析路径；
- [ ] 失败原因已分类和脱敏；
- [ ] 首轮全量实时体检完成；
- [ ] 能力矩阵完成；
- [ ] 初始评级完成；
- [ ] 90天历史记录完成；
- [ ] 后端启动快速检查完成；
- [ ] 全量体检入口完成；
- [ ] 数据说明中的健康摘要完成；
- [ ] 健康详情抽屉完成；
- [ ] 明确低风险问题已修复；
- [ ] Before／After 对比完成；
- [ ] 未解决来源有修复价值判断；
- [ ] 候选替代源仅进入文档；
- [ ] 没有新增正式 Provider；
- [ ] 没有接入 API Key；
- [ ] 没有改变主备优先级；
- [ ] 没有改变金融数据语义；
- [ ] 没有读取用户持仓；
- [ ] 所有测试和构建通过；
- [ ] 浏览器控制台无错误；
- [ ] 验收临时数据已清理；
- [ ] 分支成功推送；
- [ ] 未开始 V0.2-W3。

---

# 十二、停止规则

完成 A1 后必须停止。

不得自动进入：

```text
A2 新增金融数据源
A3 AI API / 免费额度网关
V0.2-W3 行业研究
投研首页
Windows打包
自动更新
```

用户需要先查看：

```text
数据源能力矩阵
修复前后结果
健康页面
建议替换清单
```

再决定下一阶段。

---

# 十三、最终汇报格式

```text
Work：PP03 数据源扩展实验室 A1
状态：PASS / PARTIAL / BLOCKED

基线分支：
基线 Commit：
当前分支：
当前 Commit：
GitHub推送：

注册结果：
基金与行情来源：
基金能力项：
资讯来源配置：
总探测项：

Before体检：
成功：
部分成功：
失败：
P50延迟：
P95延迟：

After体检：
成功：
部分成功：
失败：
P50延迟：
P95延迟：

已完成低风险修复：
1.
2.
3.

仍未解决：
1.
2.
3.

修复价值：
立即修复：
值得修复：
观察：
建议替换：
建议停用：

健康记录：
存储位置：
保留周期：
当前占用：
评级置信度：

页面入口：
浏览器验收：
控制台：

测试结果：
后端：
前端：
旧版兼容：
生产构建：

明确未执行：
- 未新增正式Provider
- 未接入API Key
- 未调整主备顺序
- 未改变行业证据合同
- 未读取用户真实持仓
- 未开始V0.2-W3

临时数据清理：
工作区状态：
```

不要把完整技术日志交给用户；日志和矩阵保存到仓库文档中。