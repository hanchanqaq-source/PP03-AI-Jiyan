# PP03 A1.1-W1 浏览器验收

验收日期：2026-08-18  
分支：`codex/pp03-a1-1-evidence-verification`  
实现 Commit：`9a7cef1`  
浏览器：Codex 内置 Browser Use  
页面：`http://127.0.0.1:5899/market-news`、`http://127.0.0.1:5899/evidence-center`

## 隔离边界

- 后端使用 `.tmp/acceptance/a1-1-w1/data`，资讯缓存使用 `.tmp/acceptance/a1-1-w1/news-cache`。
- 验收快照 `acceptancew1snapshot` 只包含明确标注的隔离验收事件，不代表真实外部公司事实。
- 资讯采集身份只使用现有来源配置；没有全网搜索、刷新真实资讯源、新增来源、API Key、Cookie、TLS 绕过或 AI 真伪判断。
- 隔离组合状态为 `empty`，未读取用户真实持仓金额、成本、备注、账号或凭据。

## 验收结果

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 有官方证据的“已核验”事件 | PASS | 证据详情显示交易所一手证据，事件状态为“已核验” |
| 2 | 两个独立来源的“多源印证”事件 | PASS | GitHub Blog 与 Google Research 属于两个不同 origin cluster |
| 3 | “待核验”事件不进入市场资讯主流 | PASS | 主资讯流只有已核验与多源印证两条事件 |
| 4 | 未核验关键金额在市场资讯隐藏 | PASS | 原始 `12亿元` 未进入可信标题、摘要、影响依据或情绪判断 |
| 5 | 冲突事件只在证据中心出现 | PASS | 冲突事件在核验列表与更正记录可见，在市场资讯不可见 |
| 6 | 从市场资讯进入对应证据 | PASS | “查看证据”导航到 `/evidence-center?event_id=a4193eaf7510bdffdc3e` 并自动打开单事件抽屉 |
| 7 | 核心主张与关键字段状态 | PASS | 金额显示“待核验”，日期显示“已核验” |
| 8 | 一手证据、独立来源与转载链 | PASS | 抽屉分别展示三类来源，转载不计为独立证据 |
| 9 | 状态历史与更正记录 | PASS | 显示“已核验→已更正”和“多源印证→存在冲突”两条真实快照迁移 |
| 10 | 数据源健康页不回归 | PASS | 健康快照、来源库、Provider 聚合与全量体检入口正常显示 |
| 11 | 浏览器控制台 | PASS | `error=0`、`warn=0`；验收服务日志未发现 4xx/5xx 页面 API 请求 |

## 快照计数

- 事件：已核验 1、多源印证 1、待核验 1、存在冲突 1、已更正 1、已证伪 1。
- 关键字段：已核验 1、多源印证 0、待核验 2、存在冲突 0。
- 市场资讯准入：2；隔离到证据中心：4。
- 市场资讯使用 `storage` 文章标签后返回 2 条，状态严格为 `corroborated, verified`。

## 截图

1. [证据中心概览](../../screenshots/a1-1-w1/01-evidence-overview.jpg) — `CB8928FEBC5AE1624DF1271A3BC249637D9A7F3CB76E4EBB414AE7A256EF9C3E`
2. [已核验证据抽屉](../../screenshots/a1-1-w1/02-verified-evidence-drawer.jpg) — `7D9FAB1F42ADF84E983BE03D96EC8B08AF87B84EE1D58711ABC078982A3B3D40`
3. [市场资讯可信准入](../../screenshots/a1-1-w1/03-market-news-admission.jpg) — `0FEC2588DF223E6B835B725D86114ECBF3DC8D0D6C258788396791F43CC883CD`
4. [更正记录](../../screenshots/a1-1-w1/04-corrections.jpg) — `3A0206C7B977948606CC71ECCC729D7159A44A977AAAB73A073312F1B2788664`
5. [数据源健康回归](../../screenshots/a1-1-w1/05-source-health.jpg) — `F916B5ADF01C508BFDF3891F34C0A112B96792ADA378C99C2A1296E530CACD1E`

验收完成后仅保留上述跟踪文档与截图；隔离服务进程和 `.tmp/acceptance/a1-1-w1` 数据在最终收口时清理。
