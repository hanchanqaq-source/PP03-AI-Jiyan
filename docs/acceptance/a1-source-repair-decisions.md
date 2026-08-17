# A1 来源修复决策

## 已实施：健康诊断字段契约修正

| 字段 | 证据 |
|---|---|
| source_id | Before 的 12 个 `error_type=redirect` 新闻 source_id（见下表）；修复作用域为所有新闻健康探测结果 |
| 修复前状态 | 公开 Feed 已解析并返回 2 条，但 `field_completeness_pct=0.0`、`final_reference=null`，评级为 `degraded` |
| 根因 | `newsradar.probe_source_config` 输出 `final_url`，`SourceHealthRunner` 只消费 `final_reference`；probe 未输出完整率，runner 对 partial 默认 0% |
| 修改文件 | `backend/newsradar.py`、`backend/tests/test_newsradar.py` |
| 修改内容 | 将已脱敏 `final_url` 规范为 `final_reference`；有已解析 items 时完整率为 100%，否则 0%；不保留内部字段名 `final_url` |
| 为什么属于低风险 | 只修正诊断元数据；不改 URL、请求头、解析器、缓存、来源、Provider 顺序或业务数据 |
| 对应测试 | RED：focused 测试以 `KeyError: final_reference` 失败；GREEN：focused `1 passed`，相关确定性回归 `58 passed` |
| 修复后状态 | 12 个持续重定向源保持 `partial/redirect`，完整率纠正为 100%，评级为 `usable`，最终公开地址可审计 |
| 是否改变业务数据含义 | 否 |
| 提交 | `617ed601bdd30f5ab29ab79036474228ee3ebd98` `fix(pp03): correct source health diagnostics` |

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

- `news:44726681bff6e16e`（动点科技）HTTP 403：`replace_candidate`，不补 Cookie/Authorization，不伪装浏览器。
- `news:df3335a4a80a661d`（arXiv cs.AI）与 `news:772f466c8c0bc84f`（虎嗅）TLS：`observe`，不关闭 TLS 校验。
- `quote:eastmoney-direct:stock_snapshot` HTTP 502：`observe`，不调整 Provider 优先级；腾讯公开行情仍是现有 fallback。
- FierceBiotech / FiercePharma 空载荷、36氪 parse、WSJ stale、能源资讯源超时／连接失败：保留原配置与真实状态；没有满足“最小实际响应 fixture + RED”解析器硬门槛。
- 五字段完全重复组为 0，因此没有去重提交。
- 未发现请求头缺失的确定因果证据，因此没有新增请求头。

除上述诊断修正外，无第二项修复同时满足全部硬条件；未强行制造代码变更。
