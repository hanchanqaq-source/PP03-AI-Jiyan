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
