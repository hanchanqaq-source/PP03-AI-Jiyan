# PP03 A2-W1 Task 8 浏览器验收

- 验收日期：2026-08-25（Asia/Shanghai）
- 分支：`codex/pp03-a2-multi-source-provider-hub`
- 结论：`PASS_DUAL_EVIDENCE`
- 固定实现 HEAD：`d70b0b22c81427452c77cd217f7aa6e0af2c924b`
- 正式证据 Commit：`SELF`（包含本文的正式证据提交；Git commit 不能在自身内容中记录自身 SHA，Task 9 报告记录实际 SHA）
- 工具：临时隔离 Playwright `1.62.1` / Chromium `151.0.7922.34`（revision `1234`）
- 页面：`http://127.0.0.1:5899/evidence-center`、`http://127.0.0.1:5899/market-news`
- 本地 API：`http://127.0.0.1:8900/api`

本结论采用三层证据：旧 HEAD 上恰好一次真实 RSS 刷新用于发现问题；同一冻结 raw 在修复 HEAD 上离线重放；修复 HEAD 上不触发 Live 的完整浏览器回归。它不是第二次 Live 25/25，也不把旧 Live 的失败终态称为成功。

## HEAD 与证据链

| 层 | HEAD | 结果 | 解释 |
|---|---|---|---|
| 原始 Live discovery | `32d618651945ae82108b9701402993e99ba465e4` | **NON-PASS** | Playwright 恰好发出一次生产资讯刷新 POST；虽旧脚本记录 25 个 UI 场景为 PASS，但流水线终态为 `failed / evidence_compatibility_failed`，因此不能作为成功验收 |
| matcher 修复 | `4a045a191c9c9ff7462a95e6f8844d7645c204b7` | 修复节点 | 保留被关键字段引用的 evidence，闭合引用关系；该节点不是最终浏览器证据 HEAD |
| storage 与 fixed browser | `d70b0b22c81427452c77cd217f7aa6e0af2c924b` | **PASS** | 保持 1 MiB 上限，以确定性紧凑 JSON 存储 499 条 evidence；冻结 raw 离线重放及不触发 Live 的 25 项浏览器回归均通过 |
| 正式证据 | `SELF` | 独立只读审查 **READY** | 包含本文、manifest 和 7 张 run-02 成功截图；Git commit 不可自引用，Task 9 报告记录实际 SHA |

## 原始 Live discovery：明确为 NON-PASS

- 运行：`23556432f3d54c4399c524b5a6ddc47f`
- raw：`d3a684c0c0354e8b89a7ab6da2de5809`
- raw SHA-256：`a252e5e0e3b740601e355ecac73d4b53b9bf9301eb52dc7e536651782f7abf86`
- 时长：`9341 ms`
- 来源状态：108 条 RSS；103 `ok`、5 `failed`；原始事件 499 条
- 终态计数：已核验 27、多源印证 1、待核验 457、存在冲突 14、来源失败 5；可信准入 28
- 终态：`failed`；`compatibility_error=evidence_compatibility_failed`；新 trusted snapshot 未发布，上一份可信快照 `a2acceptdisplayraw001` 保持可见

失败来源均为公开 RSS，记录的是该次网络观测，不代表永久状态：

| 来源 | 类型 | 脱敏原因 |
|---|---|---|
| arXiv cs.AI | `tls` | 来源 TLS 连接失败 |
| 国际能源网 | `connection` | 来源连接失败 |
| 36氪 | `rss_parse` | 来源 RSS / XML 解析失败 |
| 虎嗅 | `tls` | 来源 TLS 连接失败 |
| 动点科技 | `http_status` | 来源返回 HTTP 403 |

该次 Live 之后未再次触发 `POST /api/market-news/refresh`，也未再次调用 provider collection。后续所有验证都复用上述冻结 raw，不覆盖或改写这份历史观测。

## Fixed-HEAD 离线重放

`offline-replay-run-05` 在 `d70b0b22...` 使用 `NewsPipelineService` 现有注入接口和冻结 `RadarCollection`，document fetch 与外部 transport 均 fail-closed 且计数为 0。

| 根 | 运行 | 结果 | Archive |
|---|---|---|---|
| fresh | `task8fixedfresh0001` | `trusted_published`；compatibility/redacted error 均为 null | 0 → 499 |
| seeded | `task8fixedseeded001` | `trusted_published`；compatibility/redacted error 均为 null | 4 → 503 |

两条重放均得到同一 evidence snapshot `b949fe3f902d563d30dd`、trusted snapshot `d3a684...`、499 = 27 已核验 + 1 多源印证 + 457 待核验 + 14 冲突，可信事件 28 条。所有关键字段 evidence 引用闭合；目标事件 `86029352f47b95f93493` 仍为 `unverified`，百分比字段为 `corroborated`，CNBC 字段 evidence 的 `supports_claim=false`，没有把字段佐证误升级为事件主张佐证。

Archive exact retry 保留一个已知 **Minor** 边界：第一次到第二次仅事件 `d2234d0020191a1251ef` 的 independent evidence 顺序与派生 `content_digest` 改变；`raw_input_digest`、状态、历史和 lineage 均不变。第二次到第三次事件文档、索引与 bucket 稳定，仅 `state.json` generation 前进。因此本验收记录为“语义收敛”，不称为全字节幂等（`full_byte_idempotent=false`）。

## 浏览器 run-01 失败与验收夹具根因

`browser-run-01` 在 `d70b0b22...` 得到 23 PASS / 2 FAIL。场景 18 在 `evidence_saved` 后进入 `failed / evidence_compatibility_failed`；诊断 wrapper 捕获 `OSError("storage_corrupt")`。根因是验收 seed 使用真实时钟，archive authority 时间比冻结 verifier 时钟约晚 93 分 25 秒，被时间完整性校验正确拒绝；这不是 production storage 容量回归。

场景 16 同时暴露一个次要验收选择器问题：注入组件使用 `.last()`，可能选中页面原生旧 region。验收夹具修正为：seed 固定在 `VERIFY_TIME - 5 min`，并分别 scope 到 `#task8-offline-status-root` 与 `#task8-offline-terminal-root`。没有为此修改 production 代码或放宽 archive 校验。run-01 的失败截图没有复制到正式截图目录。

## Fixed-HEAD 浏览器结果

唯一 `browser-run-02`：`task8fixedbrowser001`，25 PASS / 0 FAIL / 0 SKIP / 25 encoded，退出码 0。流水线阶段为 `fetching → raw_saved → verifying → evidence_saved → trusted_published`，耗时 `6402 ms`；compatibility、redacted 和 publisher error 均为 null。

| # | 验收场景 | 结果 | run-02 证据 |
|---:|---|---|---|
| 1 | 无基金时显示完整 Catalog | PASS | 147 families、150 adapters、30 capabilities、108 news sources，基金数 0 |
| 2 | 无基金时仍显示全部注册资讯源 | PASS | 108 news sources / families / feed adapters |
| 3 | 添加隔离测试基金不改变来源注册 | PASS | 注册 fingerprint 与四项计数前后一致 |
| 4 | 添加基金只改变隔离 holdings 状态 | PASS | holding count +1；来源注册不变 |
| 5 | Eastmoney 显示为一个来源 family | PASS | family `eastmoney`，三个 adapters |
| 6 | Eastmoney adapters 与 capabilities 可展开 | PASS | 3 adapters；6 项 direct capabilities |
| 7 | 单个 Eastmoney capability 失败只造成部分降级 | PASS | family `partial_degraded`；成功与失败 capability 同时可见 |
| 8 | 失败 capability 仍显示 `configured_reference` | PASS | `stock_snapshot` 保留 `https://fund.eastmoney.com/` |
| 9 | BaoStock 是独立 family | PASS | family `baostock`；独立 evidence eligible |
| 10 | SEC、World Bank、OECD 显示免费正式源的实际状态 | PASS | SEC `unconfigured_contact`；World Bank `success`；OECD `http_client_error`，未把失败包装成已连接 |
| 11 | FRED、EIA、Tushare 无 Key 时显示 unconfigured | PASS | 三者 credential 均未配置，runtime 为 `credential_store_unavailable` |
| 12 | Alpha Vantage、Finnhub、Twelve Data 不宣称永久配额 | PASS | UI 明确以账户实际套餐、配额或 credits 为准 |
| 13 | Nasdaq Data Link 区分 public 与 Premium dataset | PASS | Premium 明确需要账户 entitlement |
| 14 | Enterprise 行显示 license-required | PASS | 9 families；抽查 Bloomberg、LSEG、FactSet、Wind |
| 15 | Free-only 模式 paid transport 为 0 | PASS | 14 paid/enterprise rows 均未发 paid transport；只观察到 2 个 Google Fonts UI asset 请求 |
| 16 | raw、verified、pending 分栏显示 | PASS | `trusted_published`；499 / 27 / 1 / 457 / 14 / 0 / 0 / 5，准入 28 |
| 17 | raw > 0 且准入 0 时显示明确 Evidence Center 文案 | PASS | 真实组件显示“本次已抓取 1 条资讯，目前尚无完成核验的内容。待核验资讯可在证据中心查看。” |
| 18 | 核验期间保留上一份可信内容 | PASS | pause 时 `a2acceptdisplayraw001` 仍可见；release 后切换到 `d3a684...` |
| 19 | 30 天 archive 返回 eligible 事件 | PASS | 3 条：上一份可信、已更正、冲突 |
| 20 | 90 天 archive 返回 eligible 事件 | PASS | 4 条，额外包含已证伪历史 |
| 21 | 已更正、冲突、已证伪历史保留 | PASS | 三类各自 snapshot/status history 均为 2 |
| 22 | Console error 为 0 | PASS | 0 |
| 23 | Console warning 为 0 | PASS | 0 |
| 24 | Pageerror 为 0 | PASS | 0 |
| 25 | 所有 requestfailed 有原因且不阻塞页面 | PASS | 6 条，均为 loopback GET `net::ERR_ABORTED`、`blocking=false` |

场景 16 与 18 的主要成功证据来自 fixed-HEAD offline replay 与 run-02 的暂停/释放 gate；旧 Live 的 raw 计数和“上一份可信内容仍可见”只作为历史补充，不能与 run-02 合并成第二次 Live 25/25。

## Console 与网络

- 浏览器请求总数：438；bad response：0。
- Console：error 0、warning 0、其他记录 15；pageerror 0。
- requestfailed：6，全部为 `127.0.0.1:5899` 的页面切换取消请求，原因均为 `net::ERR_ABORTED`，全部 `blocking=false`：
  1. `GET /api/evidence/summary`
  2. `GET /api/evidence/events?days=7`
  3. `GET /api/news/archive?days=7&limit=100`
  4. `GET /api/market-news/events?...&tag_id=storage`
  5. `GET /api/market-news/events?...&tag_id=artificial-intelligence`
  6. 同一 artificial-intelligence 查询在后续页面切换中再次被取消
- run-02 backend guard：production market-news refresh POST 0、其他 production refresh POST 0、provider collection 0、document fetch 0、external provider transport 0。
- 离线控制调用：offline start 1、release 1、verifier entry 1、frozen radar fetch 1。
- 浏览器加载了 Google Fonts 的 1 个 stylesheet 与 1 个 font；它们是 UI asset，不是 provider、资讯采集或付费 transport。该边界被显式记录，没有把整次浏览器运行称为“物理断网”。

## 正式截图

以下 7 张均只来自成功的 `browser-run-02/raw-screenshots`；实际像素均为 1440 × 1100。源文件与 tracked 副本的 bytes/hash 已逐一相等复核。

| 文件 | 内容 | Bytes | SHA-256 |
|---|---|---:|---|
| [01-catalog-eastmoney.png](../../screenshots/a2-w1/01-catalog-eastmoney.png) | Catalog 与 Eastmoney 聚合 | 209961 | `68625522b1c12a00b4dd40fe25013292361b023a6cab076c54c362499eec9f09` |
| [02-enterprise-license.png](../../screenshots/a2-w1/02-enterprise-license.png) | Enterprise license-required | 240112 | `35c7e5106935ce5550b09df31218fb3fa447a7bc1c12079bca199e00396fa6df` |
| [03-archive-30d.png](../../screenshots/a2-w1/03-archive-30d.png) | 30 天 Archive | 256027 | `c515aac5efe969771a4edc80ac88a1b6b1f3132df406981c1276164fc6606a92` |
| [04-archive-90d-history.png](../../screenshots/a2-w1/04-archive-90d-history.png) | 90 天与状态历史 | 262656 | `8d5178d61be4b6b0cb940740363de3c5c0a6fc25109a598c2d841dbf2e21d044` |
| [05-news-pending.png](../../screenshots/a2-w1/05-news-pending.png) | raw > 0、准入 0 的待核验文案 | 136390 | `ea11863d14287eecd12403cdf4a9cfe5e9c953e3e44589307c28ee1b9b5e3d5a` |
| [06-news-verifying-offline.png](../../screenshots/a2-w1/06-news-verifying-offline.png) | 核验中保留上一份可信内容 | 371671 | `fddce192fc56284cec1ef96cb0f0251879c60dccf7bcaf541297029bdf3e969b` |
| [07-news-terminal-offline.png](../../screenshots/a2-w1/07-news-terminal-offline.png) | `trusted_published` 分栏终态 | 511975 | `9d4a7a9d87d35cb695657a4464e93b651540a27abf72305ce07e2ad4be334c86` |

## Fixture gate

执行计划指定命令：

```text
rg -n "evidenceFixtures|prototype" frontend/src/features/evidence-center frontend/src/pages/MarketNews.tsx
```

结果为 0 个匹配（`rg` exit 1 表示无匹配）。production 不再 import W0 demo Fixture，因此 gate 已满足；没有删除 test-only builder 或任何文件，也没有因无变更而额外重跑三项浏览器场景。

## 隔离、版本与非操作

- npm cache、Playwright package、Chromium binaries、results、raw screenshots、logs、profile、`VR_DATA_DIR`、`VR_NEWS_CACHE_DIR` 与 `VR_ACCEPTANCE_DIR` 全部位于本 worktree 的 `.tmp/acceptance/a2-w1/playwright` 下。
- 浏览器使用新的 run-02 profile，不连接用户 Chrome/Edge profile；没有读取账号、Cookie、历史记录或登录态。
- 启动 backend 前清空 credential/token/secret/password/authorization/paid-provider 类环境变量；未设置 paid-test authorization。付费、enterprise 和未配置 adapter 在网络前 fail-closed。
- 未读取用户持仓金额、成本、备注、账号或凭据；唯一 holdings 变更位于隔离 `VR_DATA_DIR`，浏览器 finally 已删除该隔离记录。
- 未使用 Browser Use、Computer Use、用户浏览器或 Playwright route mock；未修改 production 代码。离线重放和浏览器 launcher 通过现有 `radar_fetcher` DI 注入冻结 `RadarCollection`，没有调用 production provider collection。
- Playwright 安装目录没有 Firefox/WebKit；`playwright install chromium` 带入 Chromium、Chromium headless shell 及其 ffmpeg/winldd 运行依赖。
- frontend `package.json` SHA-256 保持 `18ccc9e061716c75c5787bf7d2a3307e2d427f81a8074ffc72055e896ad1426f`；`package-lock.json` 保持 `3b5d77fe477432ea6d4ac2d33b098942c64eb5b4b71c373bd71dbe03b5c356ef`，两者 Git diff 为 0。
- 本次 owned backend/frontend/esbuild/Chromium 进程均已停止，8900/5899 listener 为 0。
- 独立只读审查以 Critical 0 / Important 0 / Minor 0 给出 `READY` 后，已按安全预检永久删除精确目标 `.tmp/acceptance/a2-w1`；正式文档、manifest 与 7 张成功截图保留。8900/5899 listener 为 0，frontend package/lock 仍无 diff。

机器可读证据索引见 [evidence-manifest.json](./evidence-manifest.json)。
