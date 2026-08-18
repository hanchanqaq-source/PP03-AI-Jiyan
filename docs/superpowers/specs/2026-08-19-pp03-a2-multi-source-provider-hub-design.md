# PP03 A2-W1 多源数据中台设计

日期：2026-08-19
基线分支：`codex/pp03-a1-1-evidence-verification`
基线提交：`9dfd8246290a04555ed1886ded92c39de9a001f4`
实现分支：`codex/pp03-a2-multi-source-provider-hub`

## 1. 目标与边界

本 Work 建立统一的多源数据中台，把“来源是否登记”“如何访问”“提供什么能力”“本次是否健康”“是否有凭据或许可证”“是否与持仓相关”拆成独立事实。最终结果必须满足：无基金时仍能看到完整来源目录；同一上游的不同技术路径只属于一个来源家族；失败能力不污染健康能力；资讯抓取、确定性核验和可信发布使用同一原始快照；最近 90 天的完整事件和证据状态可查询。

本 Work 不改变行业证据三层合同，不降低资讯真实性准入标准，不让待核验内容进入可信资讯流，不使用 AI 判断真假，不读取用户持仓金额、成本、备注、账号或凭据，不自动购买套餐，不创建 PR，不合并 `main`，也不进入 A3、V0.2-W3 或其他业务页面开发。

## 2. 方案选择

采用“独立 Catalog + 独立 Provider Adapter + 健康桥接 + 异步资讯流水线”的方案。

不采用直接扩展 `source_health` 的方案，因为 Catalog、凭据、费用、许可、持仓关系和健康观测会再次耦合；也不引入数据库和消息队列，因为本轮现有文件存储、单机后台任务和原子指针切换已经能够提供所需的一致性，引入额外基础设施会扩大迁移与运维风险。

## 3. 后端组件边界

新增 `backend/data_sources/`，其职责限定为来源目录、Adapter 契约、配置状态、预算、路由和面向 API 的聚合服务：

- `models.py`：稳定枚举和不可变目录模型。
- `catalog.py`：来源家族、Adapter、Capability 和角色的权威注册目录。
- `credentials.py`：凭据状态与安全存储抽象，永不向调用方返回明文。
- `budgets.py`：Free-only、日预算、月预算、请求预估费用和阻止原因。
- `routing.py`：每项 Capability 的主源、备用源和交叉检查源。
- `references.py`：配置地址、观测地址和敏感查询参数脱敏。
- `health_bridge.py`：把 Catalog 身份映射到现有 `source_health` 探测与评分。
- `service.py`：Catalog、家族、能力、配置、使用量、费用和刷新 API 的业务边界。
- `providers/`：每个外部 Provider 的独立 Adapter 模块，共享网络安全基础设施但不共享伪通用业务解析器。

`source_health` 继续负责探测、评分、历史和运行调度，但不再决定来源是否存在。它接收 Catalog 生成的探测描述，并以 `adapter_id + capability_id` 保存观测。Catalog 是来源数量和身份的唯一事实源。

新增 `backend/news_pipeline/`，负责异步资讯刷新状态机和快照编排。它调用现有 `newsradar` 采集能力、`evidence_verification` 确定性核验能力和 `news_intelligence` 可信资讯建模能力，但不把编排逻辑继续写入这些模块。

## 4. 统一来源数据合同

### 4.1 Catalog 模型

`SourceFamily` 至少包含：

- `source_family_id`
- `source_family_name`
- `region`
- `market`
- `source_roles[]`
- `independent_evidence_eligible`
- `commercial_use_status`

`AdapterDescriptor` 至少包含：

- `adapter_id`
- `adapter_name`
- `source_family_id`
- `provider_type`
- `capabilities[]`
- `billing_model`
- `auth_type`
- `credential_env_names[]`
- `default_enabled`
- `license_note`
- `usage_note`
- `data_delay`
- `quota_policy`
- `cost_policy`
- `configured_reference`
- `current_provider_priority`
- `catalog_status`

`CapabilityDescriptor` 使用稳定 `capability_id`，并保存能力名称、数据类别、新鲜度阈值、是否可探测、主备路由与单位/频率约束。

固定枚举与权威任务书一致：

- `billing_model`：`free_no_key`、`free_key`、`freemium`、`paid_api`、`enterprise_license`、`internal_only`。
- `source_role`：`official_evidence`、`primary_data`、`fallback_data`、`cross_check`、`macro_data`、`market_data`、`news_publisher`、`industry_media`、`collector`、`candidate`。
- `catalog_status`：`connected`、`configured`、`unconfigured`、`catalog_only`、`license_required`、`disabled`。

运行时的健康、凭据、费用和持仓关联不写回静态身份字段，而是作为独立覆盖层返回。

### 4.2 数据值信封

Provider 返回的业务数据必须包裹为 `ProviderValue`：

- `value`
- `source_family_id`
- `adapter_id`
- `capability_id`
- `as_of_date`
- `fetched_at`
- `data_status`
- `license`
- `priority`
- `difference_from_primary`
- `unit`
- `frequency`

多个来源的值不能平均、静默覆盖或只保留最后一次返回。路由层选择主值时仍返回备用值及差异元数据。不同日期、单位或口径的值不得直接比较。

### 4.3 家族健康聚合

- 全部可探测能力没有观测：`尚未体检`。
- 至少一个健康/基本可用能力，同时存在降级/失败能力：`部分降级`。
- 只有实际启用且应被探测的能力全部失败：`失败`。
- 未配置 Key、套餐无权限、预算阻止和需要许可证不计为健康失败。
- `source_name` 只作能力显示名称，不能参与身份或分组。
- 添加基金只增加关联基金、重仓公司、行业、持仓证据和相关资讯，不改变任何目录注册数量。

### 4.4 公开地址

`configured_reference` 始终来自 Catalog；即使探测失败，也必须通过 API 和页面显示。`observed_final_reference` 只在真实访问成功或经过安全检查的重定向后保存。两者都执行精确敏感查询参数脱敏，不保存 Key、Token、Cookie、Authorization 或登录信息。跨域重定向需要重新验证发布者身份；HTTP 降级、登录页和付费墙不会被自动接受。

## 5. 来源家族与 Adapter

东方财富使用一个 `eastmoney` 家族，包含 `eastmoney-direct`、`akshare-eastmoney` 和默认关闭的 `efinance-eastmoney`。基金搜索、目录、档案、正式净值、净值历史、持仓、行业配置和股票行情分别建模为 Capability。AKShare 与 efinance 都不能增加来源独立性。

腾讯行情、巨潮资讯、蛋卷基金维持独立家族。现有 108 个 RSS/Atom 来源进入统一 Catalog，来源数量与基金持仓彻底解耦。

第一批免费无需 Key 的正式 Adapter：BaoStock、SEC EDGAR、World Bank、OECD、IMF、GDELT、yfinance，以及上交所、深交所、巨潮资讯、港交所披露易、中国证监会、基金公司和指数公司的官方证据入口。IMF 当前公开接口若已退役或需要授权，必须降为真实 Catalog 状态；GDELT 只负责候选发现；yfinance 默认只在个人研究模式启用。

SEC Adapter 使用低于官方上限的限速、`Retry-After`、有限范围请求和明确 User-Agent。默认联系标识使用项目名与公开 GitHub 项目地址，不伪造邮箱；如果当前官方接口明确拒绝该联系形式，Adapter 保持未配置并报告真实阻断。

免费 Key/Freemium Adapter：FRED、EIA、Tushare Pro、Alpha Vantage、Finnhub、Twelve Data、Nasdaq Data Link、NewsAPI。没有 Key 时不发请求，状态为未配置且不计失败。

付费 Adapter：Financial Modeling Prep、Polygon/Massive、Tiingo、EODHD、Databento。每个 Provider 独立实现契约和 Mock；默认关闭，只有显式启用、凭据已验证且预算允许时才能请求。

企业 Catalog：Bloomberg、LSEG、FactSet、Wind、Choice、iFinD、Morningstar Direct、S&P Capital IQ、CSMAR。没有合同、SDK、凭据和测试授权时只显示 `license_required`，不添加不可用 SDK，不进行网页替代抓取。

## 6. 凭据、许可和费用

`CredentialStore` 提供状态查询、写入、删除和验证接口。生产优先使用 Windows Credential Manager/系统 Keyring；环境变量是只读后备；本地文件后备必须被 Git 忽略。测试只使用内存实现。

前端凭据表单只在用户主动提供 Key 时提交一次。请求体不得记录，响应只包含 `configured`、`status`、`last_validated_at` 和 `credential_source`。Key 不进入 URL、日志、截图、API 响应、localStorage、验收文档或明文数据库。

默认 `free_only=true`。付费请求必须通过以下预检：用户显式启用、凭据已验证、Free-only 已关闭、日/月预算已设置且未耗尽、本次预计费用在预算内。预算失败在网络请求前发生；系统不自动续费、购买额度或升级套餐。

## 7. 路由和来源独立性

每个 Capability 独立配置 `primary_families[]`、`fallback_families[]` 和 `cross_check_families[]`。默认优先级为官方一手证据、正式持牌/付费主数据、稳定独立公开数据、非官方公共接入路径、缓存、空状态。

来源独立性以真实 `content_source` 和 `origin_cluster` 计算。东方财富直连、AKShare/东方财富和 efinance 只算一个家族；RSSHub、NewsAPI 和 GDELT 不能与其返回的原发布者重复计数；同一通讯稿的转载不能增加独立来源数。SEC 等官方文件可以参与“已核验”，但健康状态本身不能提高内容真实性等级。

## 8. 异步资讯流水线

`POST /api/market-news/refresh` 创建后台运行，预分配 `run_id` 与 `raw_snapshot_id` 并返回 HTTP 202。状态依次为：

`queued -> fetching -> raw_saved -> verifying -> evidence_saved -> trusted_published`

所有阶段共享同一 `raw_snapshot_id`。原始快照必须先原子落盘，确定性核验随后读取该快照；可信快照只有在完整持久化后才原子切换当前指针。

`GET /api/news/pipeline-status` 返回阶段、原始抓取数、已核验数、多源印证数、待核验数、冲突数、已更正数、已证伪数、失败来源数，以及当前 raw/evidence/trusted snapshot ID。

核验期间市场资讯继续读取上一份成功可信快照，并显示“新资讯已抓取，核验处理中；当前显示上一份可信快照。”核验失败也不删除旧可信快照。若 `raw_event_count > 0` 且 `admitted_count = 0`，页面显示本次抓取数量并引导用户到证据中心，不能显示空白或把待核验内容混入可信主流。

抓取失败、核验失败和持久化失败分别记录。单个来源失败不终止整轮；重启后未完成运行标记为中断，只有已完整持久化的可信快照能够恢复为成功。

## 9. 快照与 90 天归档

文件布局：

```text
%VR_DATA_DIR%\evidence-verification\v1\
├─ raw\
├─ evidence\
├─ trusted\
├─ archive\
├─ current.json
└─ pipeline-status.json
```

`current.json` 保存当前可信快照指针和兼容读取信息。归档按 `event_id` 去重，保存标题、必要摘要/摘录、原始链接、发布时间、发布来源、采集渠道、标签、核验状态、关键字段状态、证据链接、独立来源链、更正/证伪历史和最后更新时间，不保存整篇受版权保护正文。

状态合并采用追加语义：更正、冲突和证伪不能因新快照覆盖而消失。归档支持 1、3、7、30、90 天查询。90 天之外的归档文件只作为现有缓存清理机制的候选，当前快照和用户数据不进入删除候选。

历史恢复先只读盘点 `radar.json`、evidence `current.json`、现有 history 和合规旧快照，再按真实字段完整度导入。输出分别统计缓存恢复、公开来源重新抓取和无法恢复；重新抓取不能冒充缓存恢复，无法确定标题、时间、状态或链接的记录不得伪造。

## 10. 失败与降级来源修复

当前快照中的全部失败/降级来源以及任务书点名的 36氪、动点科技、国际能源网、虎嗅、钛媒体、arXiv cs.AI、FierceBiotech、FiercePharma、WSJ Markets逐项建立决策表。

解析变化必须先保存最小脱敏 Fixture，再经过 RED/GREEN 修复。Feed 迁移只接受同一发布者的公开合法入口；临时 TLS、DNS、502 使用有限重试并继续观察，不关闭 TLS；登录、验证码、付费墙不绕过。旧 Feed 停止时，只有同机构替代入口通过健康检查后才停用旧源。普通媒体遵循一进一出，不追求来源总数增长。结论只能是修复、观察、替换、停用、需要凭据或需要许可证。

## 11. API

新增任务书规定的接口：

- `GET /api/data-sources/catalog`
- `GET /api/data-sources/families`
- `GET /api/data-sources/families/{family_id}`
- `GET /api/data-sources/capabilities`
- `GET /api/data-sources/config`
- `POST /api/data-sources/{adapter_id}/enable`
- `POST /api/data-sources/{adapter_id}/disable`
- `POST /api/data-sources/{adapter_id}/validate`
- `GET /api/data-sources/usage`
- `GET /api/data-sources/cost`
- `POST /api/data-sources/refresh`
- `GET /api/news/pipeline-status`
- `GET /api/news/archive`

如前端配置凭据，另提供只接受一次性秘密的 `PUT /api/data-sources/{adapter_id}/credentials` 与删除接口；响应永不包含明文。`POST /api/data-sources/refresh` 只启动来源健康体检，不能与资讯刷新混用。

现有 `/api/source-health/*`、`/api/evidence/*` 和市场资讯读取接口保留兼容路径。`/api/market-news/refresh` 改为异步任务入口，前端改为轮询状态并维持旧可信快照。

## 12. 前端设计

数据源页面直接读取 Catalog，并把健康、凭据、费用和持仓关系作为覆盖信息。无基金时仍显示完整家族、Adapter、Capability、108 个资讯来源、未配置来源、付费来源和企业目录。未观测项显示“尚未体检”，未配置 Key 显示“未配置”，企业源显示“需要许可证”。

来源家族卡片默认聚合；东方财富只显示一张卡，展开后显示 Adapter 和 Capability、优先级、健康、配置地址、观测地址、配额、费用、错误与备用来源。添加基金前后 Catalog 数量必须相同，只增加持仓关联。

市场资讯显示流水线各阶段计数和快照 ID，并在核验中保留旧可信内容。证据中心增加 1、3、7、30、90 天筛选。其他投研页面和行业证据三层展示保持原合同。

## 13. 测试与验收

每个 Provider Adapter 的 Mock 至少覆盖正常、空、超时、鉴权、限流、套餐无权限、字段变化、缓存回退、来源元数据、日期、单位、预算和未配置凭据。无 Key 时断言没有网络调用；Free-only、日预算和月预算在网络前阻止付费调用。

来源独立性测试证明东方财富三种路径只算一个家族，RSSHub/GDELT/NewsAPI 不增加原发布者独立性，不同 `content_source/origin_cluster` 才能多源印证。

页面测试覆盖无基金完整目录、添加基金注册数不变、家族/Adapter/Capability 展开、失败时配置地址可见、各配置/许可状态、异步流水线计数、旧可信快照保留、零准入提示、90 天筛选和原五页回归。

Live 测试按 `free_no_key`、`free_key`、`freemium`、`paid`、`enterprise` 分组。免费无需 Key 来源使用隔离目录；无凭据和无许可证时跳过，不计失败；只有 `VR_ALLOW_PAID_PROVIDER_TESTS=1` 且预算允许时才可请求付费源。Live 结果只代表当次观测。

最终使用临时隔离 Playwright Chromium 完成任务书 25 项页面与控制台验收。浏览器工具、二进制、npm 缓存和隔离数据全部放入项目 `.tmp/acceptance` 范围，验收后按安全边界清理；正式截图和验收文档保留。

## 14. 实施阶段和提交边界

阶段 1：来源家族、Catalog、东方财富聚合、页面目录解耦。
阶段 2：免费无需 Key Provider、失败源逐项修复。
阶段 3：Key/Freemium、付费、企业 Catalog、凭据与预算保护。
阶段 4：异步资讯流水线、90 天历史恢复、最终验收。

每阶段必须先写测试并验证 RED，再实现并验证 GREEN，运行受影响回归和独立审查轮次，最后形成单独 Commit。设计和实施计划可以有独立文档 Commit，但不得把四阶段生产改动压成一个提交。

最终交付十份 `docs/data-sources/` 文档、来源 Before/After、失败源逐项处理表、恢复计数、测试与 Playwright 证据。完成后推送 `codex/pp03-a2-multi-source-provider-hub`，不创建 PR、不合并 `main`，并停留在 A2-W1 等待产品验收。

## 15. 已明确的失败行为

- 公共接口当前不可访问：保留 Catalog，状态反映当次失败或未接通，不伪造数据。
- 官方接口改为需要授权：切换到真实的 `free_key`/`freemium`/`license_required` 状态，不绕过。
- Key 或许可证缺失：跳过 Live 请求，不阻塞其他来源。
- 预算不足：请求前阻止并显示费用原因。
- 单个来源失败：保留其他来源结果和上次可靠缓存。
- 核验失败：保留上一份可信快照和新原始快照，允许证据中心查看真实状态。
- 历史字段不足：记录无法恢复及原因，不补造标题、时间、状态或链接。

以上行为优先保证真实性、可解释性和费用安全，不能为了获得绿色状态、扩大覆盖率或填充页面而降低标准。
