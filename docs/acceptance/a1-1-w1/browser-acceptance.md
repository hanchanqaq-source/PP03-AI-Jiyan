# PP03 A1.1-W1 浏览器验收

初次验收日期：2026-08-18

本轮重新验收日期：2026-08-19

分支：`codex/pp03-a1-1-evidence-verification`

本轮重新验收基线 HEAD：`1ac7241edc63c925c331fd198a738c2913d0b605`

初次验收浏览器：Codex 内置 Browser Use

本轮重新验收工具：临时隔离 Playwright `1.62.1` / Chromium `151.0.7922.34`

本轮 Codex Browser Use 因 workspace policy 禁用，未重试 Computer Use、Browser Use、`browser-service.mjs` 或 Full CDP，也未修改或绕过工作区安全策略。

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
| 2 | 两个独立来源的“多源印证”事件 | PASS | 两个相互独立的来源链提供了一致证据 |
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
6. [待核验字段隔离](../../screenshots/a1-1-w1/06-unverified-field-isolation.png) — `201374439B0FA836898719D8BE83921DB5B140D267DFF400A16F801172F9C022`
7. [中文用户文案](../../screenshots/a1-1-w1/07-chinese-evidence-copy.png) — `56FFB6D14530B260B6F951E700C65ED9C3B2EC51487D697DBFD0EDB8058EFACC`
8. [资讯健康快照状态](../../screenshots/a1-1-w1/08-news-health-snapshot-state.png) — `9AEB84A3CEA42EE1A1F17A67CE22B4E3623C6181FF486B1C5BA05FA1607B513C`

## 2026-08-19 重新验收

| 场景 | 结果 | 可见断言 |
|---|---|---|
| 待核验字段隔离 | PASS | 已核验事件标题、核心主张、摘要和市场资讯卡片不含 `12亿元`；该值仅在证据详情“关键字段”显示并标记“待核验” |
| 中文用户文案 | PASS | 页面可见“独立来源链”“确定性证据”“隔离验收快照”，不显示 `origin cluster`、`DETERMINISTIC EVIDENCE`、`acceptance` 或 `acceptance snapshot` |
| 资讯健康真实观测 | PASS | 隔离 API 返回 `registered=108`、`observed=2`、`loaded=true`，页面显示健康 1、降级 1，其余为 0 |
| 资讯健康缺失快照 | PASS | 隔离 API 返回 `registered=108`、`observed=0`、`loaded=false`；数据源健康页和证据中心右侧摘要均显示“尚未载入资讯来源健康快照”，未显示 `0/0/0/0` |

本轮 Playwright 使用全新无状态 Chromium context，只访问 `127.0.0.1:5899`；未连接用户 Chrome/Edge 会话，未读取账号、历史记录、Cookie 或登录状态。脚本以页面文字、角色和语义区域定位，不使用固定坐标；断言失败会返回非 0 退出码。

事件与健康状态来自 `.tmp/acceptance/a1-1-w1` 的虚构隔离数据：事件快照为 `acceptancew1snapshot`；健康统计先用两条隔离 news 观测验证真实统计，再重启隔离后端验证 news 组缺失状态。未读取用户持仓金额、成本、备注、账号、凭据或正式缓存。

浏览器遥测：`console error=0`、`console warn=0`、`pageerror=0`、`failed request=0`。三项场景均通过；没有静默删除真实失败。

## 本轮 fresh 自动化与清理

- 受影响后端测试：174 passed，1 个既有 Starlette 弃用警告。
- 前端全量：26 files / 124 tests passed。
- 旧版兼容：16 passed。
- 生产构建：1912 modules transformed，构建成功；保留既有空 `vendor-charts` 与大于 500 kB chunk 提示。
- 已停止本轮拥有的 backend/frontend 服务；8900、5899 端口均已释放。
- Playwright、npm cache、Chromium、测试结果和临时截图均只位于 `.tmp/acceptance-tools/a1-1-w1-playwright`；该目录已删除（808 files / 792,758,499 bytes）。
- 隔离验收数据 `.tmp/acceptance/a1-1-w1` 已删除（10 files / 20,775 bytes）。
- 临时工具未加入项目依赖；`frontend/package.json` 与 `frontend/package-lock.json` 哈希保持 `18CCC9E...1426F` / `3B5D77F...356EF`，且没有 Git diff。
- 仅保留实现、正式测试、本文档与上述 8 张正式截图。
