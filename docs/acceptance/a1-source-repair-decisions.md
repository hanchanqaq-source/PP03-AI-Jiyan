# A1 来源修复决策

## Fix Round 1：权威返回契约、脱敏与完整率

| 字段 | 证据 |
|---|---|
| source_id | 所有 108 个新闻 source_id；重定向验收重点为下表 12 个；URL 脱敏契约覆盖所有来源引用 |
| 修复前状态 | 初始修复 `617ed601bdd30f5ab29ab79036474228ee3ebd98` 将权威 `final_url` 从 news probe 返回值中移除；runner 又只消费 `final_reference`。非空 items 被直接计为 100%；精确查询键 `code` 可把 opaque UUID 留在审计引用中 |
| 根因 | news probe 与健康 runner 的字段边界混淆；完整率缺少 item 级字段定义；脱敏敏感键集合未包含精确 `code` |
| 修改文件 | `backend/newsradar.py`、`backend/source_health/runner.py`、`backend/source_health/probe_errors.py` 及三组对应测试 |
| 修改内容 | news probe 保留 `final_url`；runner 兼容 `raw.final_reference` 或 `raw.final_url` 并落为健康 `final_reference`；按 `title`/`url`/`published_at` 三字段计算实际百分比；仅精确查询键 `code` 脱敏，`decode`/`postcode` 不受影响 |
| 为什么属于低风险 | 只修正健康诊断返回契约、引用脱敏和可观测指标；不改源配置、请求、解析器、缓存、Provider 顺序或业务数据 |
| 对应测试 | RED：4 个 focused 测试均失败（`final_url` KeyError、100 vs 66.67、runner 引用为空、`code` 未脱敏）；GREEN：同组 `4 passed`，相关确定性回归 `62 passed in 1.89s` |
| 修复后状态 | 权威 `final_url` 契约保留，runner 可审计最终引用；缺字段 item 不再伪报 100%；opaque `code` 不持久化，近似键值保持原样 |
| 是否改变业务数据含义 | 否；只改变诊断元数据和指标计算 |
| 提交 | `1470a40d640ef304692a4421c6626641879287bf` `fix(pp03): preserve news probe contracts` |

原 After run `e9cc914472e5a2ef74e9` 已被本轮 fresh run `7f744e9abe6eb7238238` 取代；两者仍使用相同的 119 个
source_id、配置和样本哈希。计数与时延差异只记录为公网波动，不作为修复收益。

## 301/308 逐项决策

| source_id | 来源 | 状态链 | 最终公开地址 | 决策 |
|---|---|---:|---|---|
| `news:68779a6ad6e56d73` | SemiAnalysis | 301 | https://newsletter.semianalysis.com/feed | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:6e3127956b63d8c3` | Robotics Business Review | 301 | http://www.therobotreport.com/feed/ | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:905d4f7cc34ee856` | Canary Media | 301 | https://www.canarymedia.com/rss.rss | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:54bb6004ad8b405b` | Endpoints News | 301 | https://endpoints.news/feed/ | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:57bf88dafb0d5ce8` | Space.com | 301 | https://www.space.com/feeds.xml | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:77eba0e4610108d3` | 白鲸出海 | 301 | https://www.baijing.cn/feed | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:82d0ae94ec76adac` | DPReview | 301 | https://www.dpreview.com/feed/ | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:3443a9fa0e028f34` | Financial Times | 301 | https://www.ft.com/rss/home/international | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:e15e8e0612acee25` | MarketWatch | 301 | https://feeds.content.dowjones.io/public/rss/mw_topstories | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:37085a92d14c67c4` | Quanta Magazine | 301 | https://www.quantamagazine.org/feed/ | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:3017e8411834b32a` | New Scientist | 301 | https://www.newscientist.com/feed/ | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |
| `news:6ee1acdae59fe508` | Live Science | 301 | https://www.livescience.com/feeds.xml | observe；未改配置，保持同输入；逐项复核域名／TLS／条款后再决定 |

上述 12 个 301 满足“实际观察到永久跳转”这一必要条件，但本轮不修改配置：After 必须与 Before 使用完全相同的配置和 source_id。
此外 Robotics Business Review、Space.com、Live Science 的链路含 HTTP 降级；Endpoints 独立标准头请求的最终地址返回 403；多个项目跨域。
因此没有把“301”单独当作全部硬条件已满足。

## 明确未自动修复

- `news:44726681bff6e16e`（动点科技）旧 After 曾为 HTTP 403，本次 fresh After 恢复成功：只记公网波动，不补 Cookie/Authorization，不伪装浏览器。
- `news:df3335a4a80a661d`（arXiv cs.AI）与 `news:772f466c8c0bc84f`（虎嗅）TLS：`observe`，不关闭 TLS 校验。
- `quote:eastmoney-direct:stock_snapshot` HTTP 502：`observe`，不调整 Provider 优先级；腾讯公开行情仍是现有 fallback。
- FierceBiotech / FiercePharma 空载荷、36氪 parse、WSJ stale、能源资讯源超时／连接失败：保留原配置与真实状态；没有满足“最小实际响应 fixture + RED”解析器硬门槛。
- 五字段完全重复组为 0，因此没有去重提交。
- 未发现请求头缺失的确定因果证据，因此没有新增请求头。

除上述诊断契约、精确脱敏和指标修正外，无其他修复同时满足全部硬条件；未强行制造代码变更。

## Task 7 最终数据合同与修复边界审阅

| 不变式 | 回归证据 |
|---|---|
| 官方行业配置仍独立 | 后端 `test_analysis_separates_official_allocation_from_stock_lookthrough`；前端独立“官方行业配置”区域 |
| 重仓股穿透仍独立 | 后端分类结果单独进入 `lookthrough`；前端独立“重仓股穿透后的行业暴露”区域 |
| 产业链标签仍独立 | `industry_chain_tags` 独立断言与“产业链 / 主题标签”区域 |
| 未知部分未归一化 | `test_analysis_distinguishes_other_from_unknown_constituents` 保留未知持仓原始权重 |
| 未披露股票未归一化 | 后端保持 `undisclosed_stock_pct=20.0`；前端直接显示未披露股票资产比例 |
| 非股票资产未归一化 | 后端保持 `non_stock_pct=20.0`；没有并入已识别行业 |
| 不按基金名或股票名生成行业事实 | 腾讯行情回归保持 `industry=None`；前端明确显示“基金名称未参与行业事实判断” |

聚焦合同命令结果为后端 `5 passed`、前端 `2 files / 5 tests passed`；它们已包含在 Task 7 页面验收前运行的更广后端 `357 passed` 和前端 `97 passed` 中。Task 7 没有新增低风险修复：页面验收 run 的 TLS、认证、超时、连接、空载荷、解析和陈旧状态继续作为真实观测保留，不据此修改 URL、请求头、TLS、Provider 顺序或业务语义。

启动审阅另发现任务书根目录 `backend.app:app` 命令与现有 backend-local import 方式不兼容；本轮只用 README 已有的 backend cwd `app:app` 入口完成验收，没有借机修改业务代码。浏览器证据与工具替代裁决见 [After 页面验收观测](a1-source-health-after.md#task-7-页面验收观测不覆盖权威-after)。

## Final branch review fixes

最终分支审查只修正诊断与观测证据链，没有修改 Provider、Provider 顺序、来源配置、金融业务合同或 Task 6 权威 Before／After 数字：

1. `cb9eb26` `fix(pp03): connect source health evidence`：补齐 90 天历史、评级置信度、连续失败和 `last_success_at` 证据；按能力设置 freshness；把生产探测证据接入 repair advisor。
2. `b1a6199` `fix(pp03): resolve source health fallback evidence`：以整轮实际观测计算可靠备用，并只读正式 radar cache 元数据形成可靠缓存证据；拒绝 probe 自报或陈旧、错配、畸形缓存。
3. `194f1ad` `fix(pp03): align source health runtime evidence`：用真实 radar ID 映射 health ID 与 canonical URL；分离增量 probe progress 和最终 advice callback；对正式 radar cache 做有界、fail-closed 解析。

三轮 scoped re-review 最终 `PASS`，0 个新增 Critical 或 Important。以下 5 个 deferred Minor 维持非阻塞且未在本轮扩展范围：Provider profile wrapper 状态校验；history retention 文件名解析一致性；process-global service 生命周期；`wait=False` 时已运行 probe 的退出证明；前端时间格式／零来源报告边界。它们不改变上述诊断链路修复结论，也未触发公网重跑或覆盖权威 After。
