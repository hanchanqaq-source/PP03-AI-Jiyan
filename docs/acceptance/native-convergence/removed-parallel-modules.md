# 平行模块收敛清单

状态：删除前清单。最终删除以实际 caller scan 和 Git diff 为准；历史文档/截图与所有用户数据不在删除范围。

| 删除文件或目录 | 原调用方 | 原生替代 | 数据处理 | 删除理由 | 回退方式 |
| --- | --- | --- | --- | --- | --- |
| `frontend/src/pages/ResearchHome.tsx` | `/research-home`、根路由、主导航 | `/daily-review`、`/intel`、`/portfolio`、`/sectors` | 基金数据保留 | 重复聚合首页 | 从父提交恢复或切回旧 worktree |
| `frontend/src/pages/MarketNews.tsx` | `/market-news` | `/intel` | 旧流水线数据保留 | 重复资讯产品 | 同上 |
| `frontend/src/pages/IndustryResearch.tsx` | `/industry-research` | `/signals`、`/sectors`、`/intel` | 旧行业快照保留 | 平行行业产品 | 同上 |
| `frontend/src/pages/PortfolioAnalysis.tsx` | `/portfolio-analysis` | `/portfolio` | 用户基金持仓保留 | 平行持仓产品 | 同上 |
| `frontend/src/features/evidence-center/` | `/evidence-center` | `/intel/sources` 仅迁移来源管理能力 | 证据数据保留 | 独立证据中心移除 | 同上 |
| `frontend/src/features/source-catalog/` | Evidence Center | 无；结构化数据回到原生模块 | 配置数据保留 | 147-family/150-adapter Catalog UI | 同上 |
| `frontend/src/features/source-health/` | Evidence/Market News | `RadarSourceManager` | 体检历史保留 | 大型来源体检工作台超出原生需求 | 同上 |
| `frontend/src/features/fund-portfolio/` | Research Home/Portfolio Analysis | `/portfolio` | 基金持仓保留 | 平行基金工作台 | 同上 |
| `frontend/src/features/industry/` | Industry Research | `/signals`、`/sectors` | 行业数据保留 | 平行连续报告工作台 | 同上 |
| `frontend/src/features/market-news/`、`features/news/`、`features/tags/` | Market News/Industry/Intel 的 PP03 改造 | upstream `Intel` + `RadarSourceManager` | localStorage 不主动清理 | 页面专属状态、Fixture 与可信流水线 UI | 同上 |
| `backend/data_sources/` | `/api/data-sources/*`、Catalog/Provider Center | `astock.py`、`gstock.py`、`market.py`、`newsradar.py`、`signals.py` | 本地配置/用量保留 | 重复通用 Provider 中台、付费空壳 | 同上 |
| `backend/evidence_verification/` | `/api/evidence/*`、news pipeline、industry | 原生资讯雷达；来源状态小能力迁入 manager | 证据归档保留 | 独立 Evidence Center 后端 | 同上 |
| `backend/source_health/` | `/api/source-health/*`、Catalog/Market News | `news_source_manager.py` | 体检历史保留 | 全平台体检系统超出资讯页需求 | 同上 |
| `backend/news_pipeline/`、`news_intelligence/` | `/api/market-news/*`、`/api/news/archive` | `newsradar.py` | raw/evidence/trusted/runs 保留 | 平行资讯主干 | 同上 |
| `backend/fund_data/`、`fund_portfolio.py` | `/api/fund*`、行业/市场资讯关系 | 原生 `/portfolio` 与结构化市场模块 | 基金持仓/缓存保留 | 平行基金 Provider 层 | 同上 |
| `backend/industry_research/` | `/api/industry-research/*` | `signals.py` 小模块、板块中心 | 行业快照保留 | 大型平行行业数据主干 | 同上 |
| `backend/news_translation.py`、`cache_management.py` | Market News 专属接口 | 原生 AI/chat 与 radar cache | 旧缓存保留 | 无剩余调用方的页面专属服务 | 同上 |
| FMP、Massive/Polygon、Tiingo、EODHD、Databento 等 provider 文件 | Catalog 注册，无生产连接 | 不替代；需要时按原生模块单独接入 | 无用户数据删除 | Mock/付费/许可证空壳 | 同上 |
| 对应 Backend/Frontend 页面专属测试、Fixture、验收启动器 | 仅覆盖上述已删除产品 | 新 route/source-manager/native tests | 历史验收文档和截图保留 | 无实际产品价值的重复自动化 | 同上 |

## 保留并迁移的能力

- 来源启用/停用、测试连接、最近成功、最近错误、条数、耗时、配置地址、简单健康状态。
- 来源失败继续展示既有 radar cache。
- 新增 RSS URL 的公网地址、重定向、大小、XML/RSS/Atom 与 TLS 安全检查。
- 敏感查询参数和错误消息脱敏。
- 自定义来源在用户数据目录原子持久化，损坏时仍可用内置来源。

## 明确保留但不再暴露的数据

以下路径的真实实例不由本 Work 枚举、读取或改写；代码退出后统一状态为 `PRESERVED_BUT_NOT_CURRENTLY_EXPOSED`：基金持仓、`evidence-verification/v1`、`source-health`、`data-sources/v1`、`industry-research`、研报、自选、笔记、AI 配置与 API Key。

## 删除门

执行删除前必须满足：

1. 新原生 route/source-manager tests 已 RED；
2. `rg` 证明被删目录只有被删页面、被删 API 或被删模块互相调用；
3. `app.py`、`frontend/src/lib/api.ts` 和 `Intel.tsx` 已切换到原生实现；
4. 没有文件删除命令指向 `%VR_DATA_DIR%`、`%VR_REPORTS_DIR%`、用户 profile 或仓库外用户目录。
