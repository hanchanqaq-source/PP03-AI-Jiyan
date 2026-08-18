# A1 来源替代候选（仅文档，不接入）

核验日期：2026-08-18。下表只记录候选能力、Key、许可证／访问边界、可能替代的失败能力与不接入原因；
没有安装包、申请 Key、调用需凭据端点、修改 Provider 顺序或新增 Provider。链接均优先使用官方文档、官方项目页或发布页。

| 候选 | 候选能力 | Key | 许可证 / 访问边界 | 预计替代能力 | 为什么暂不接入 | 官方证据 |
|---|---|---|---|---|---|---|
| efinance | A股／基金目录、行情、净值候选 | 否（库本身） | 客户端 MIT；上游数据权利另行核验 | `quote:eastmoney-direct:stock_snapshot` 与中国基金公开能力 | 仍依赖上游公开页面，需逐能力实测稳定性和数据权利；不因 MIT 推定数据可再分发 | [官方说明](https://pypi.org/project/efinance/) |
| yfinance | 全球证券、ETF／基金历史与持仓候选 | 否 | 工具开源；官方文档明确 Yahoo 数据仅个人使用 | 行情与海外基金候选 | 个人使用边界与 PP03 分发／缓存场景未完成合规审查 | [官方说明](https://ranaroussi.github.io/yfinance/index.html) |
| FRED | 宏观经济时间序列 | 是 | FRED API Terms；系列可能有第三方版权，且限制 AI 与缓存用途 | 宏观资讯／指标能力 | 需要用户 Key；条款与本地缓存、AI 使用边界不符合本轮无 Key 接入门槛 | [官方说明](https://fred.stlouisfed.org/legal/terms/) |
| EIA | 能源供需、价格、库存宏观数据 | 是（免费注册） | EIA API Terms；需署名并遵守 reuse policy | 能源资讯源失败时的结构化事实候选 | 需要 Key；它不是新闻 Feed 的语义等价替换 | [官方说明](https://www.eia.gov/opendata/documentation.php) |
| SEC EDGAR | 公司申报、XBRL 财务与 submissions | 否 | 公开 API；遵守 SEC Fair Access，当前指引不超过总计 10 req/s | SEC／公司披露类资讯能力 | 不是所有新闻源的等价替换；需明确公司映射、User-Agent 与缓存策略 | [官方说明](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) |
| RSSHub | 为缺失／失效页面提供 RSS 路由 | 自托管通常否；第三方实例边界各异 | AGPL-3.0；路由目标站条款另行适用 | empty_payload／parse 的新闻源候选 | 公共实例稳定性与目标站条款未知；自托管会新增运维和合规面 | [官方说明](https://github.com/DIYgod/RSSHub) |
| Finnhub | 股票、ETF／基金、持仓与行业暴露 | 是 | 专有服务；注册页限定非专业个人使用，商业／专业需书面批准 | 行情、持仓、行业能力 | 需要 Key 且许可范围需批准；不得用外部共享 Key | [官方说明](https://finnhub.io/register) |
| Twelve Data | 全球股票、ETF、共同基金行情与 profile | 是 | 专有 Terms；默认有限内部使用，外显／再分发依订阅层级 | 行情与 profile 能力 | 需要 Key；免费层不得商业使用，缓存／展示权需逐层核验 | [官方说明](https://twelvedata.com/terms) |
| Alpha Vantage | 行情、基本面、宏观与资讯 API | 是 | 专有 Terms；默认个人非商业许可，其他用途需书面约定 | 行情或部分新闻能力 | 需要 Key；PP03 使用边界与速率／许可未确认 | [官方说明](https://www.alphavantage.co/terms_of_service/) |
| Tushare | 中国股票、基金、指数与公告 | 是（Token） | Tushare 用户／数据服务协议；接口受积分和独立权限约束 | 中国行情、基金或公告能力 | 需账号 Token，部分能力需积分／付费独立权限；不符合本轮无 Key 门槛 | [官方说明](https://tushare.pro/document/1?doc_id=409) |

## 裁决

这些候选不是已验证 Provider。需要 Key、积分、付费、个人使用限制、再分发限制、目标站条款或非等价数据语义的候选，
均不满足 Task 6 自动接入边界。后续若评估，必须逐能力执行最小公开请求、检查字段／日期／shape，并重新做许可与缓存边界审查。

## Task 7 最终审阅

- 页面验收 run `bd6d73cfe908309fd03a` 在另一公网时点观察到 98 success、13 partial、8 failure；其中认证、TLS、超时、连接、空载荷、解析和陈旧状态均未被包装为 PASS。
- 本次波动没有使任何候选满足“语义等价、公开可访问、许可清晰、无需用户凭据、确定性测试已覆盖”的接入门槛；候选仍只留在文档。
- 浏览器已复核失败详情、公开 URL、备用来源与 repair value，未发现 API Key、Token、Cookie、Authorization、本地隐私路径或用户持仓。
- 最终审阅未新增 Provider、未接入凭据、未调整主备优先级、未改变金融数据语义；截图证据见 [资讯失败详情](../screenshots/source-health-a1/03-news-source-details.png)。
