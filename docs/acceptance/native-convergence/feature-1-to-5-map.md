# PP03 1—5 功能映射

基线：`810204efd317000aafa9836f8e2996ae4b1600ca`

分支：`codex/pp03-native-radar-convergence`
映射结论：Git 路由、导航和页面引入历史与任务书预计的五项完全一致，可继续执行，不需要猜测性删除。

## 1. 投研首页

- 页面/路由/导航：`ResearchHome`，`/research-home`，侧栏“01 投研首页”；根路由当前也跳转到它。
- 前端：`frontend/src/pages/ResearchHome.tsx`、`frontend/src/features/fund-portfolio/__tests__/ResearchHome.test.tsx`、`frontend/src/features/tags/`。
- 后端接口：读取 `/api/fund-portfolio` 和 `/api/radar`。
- 存储：基金持仓数据；`vr-page-tags:*` localStorage 偏好。
- 原生重合：每日复盘、资讯雷达、我的持仓、板块中心已分别承担首页卡片的真实工具职责。
- 删除后保留：基金持仓用户文件原位保留，标记 `PRESERVED_BUT_NOT_CURRENTLY_EXPOSED`；原生资讯缓存与原生页面保留。

## 2. 市场资讯

- 页面/路由/导航：`MarketNews`，`/market-news`，侧栏“02 市场资讯”。
- 前端：`frontend/src/pages/MarketNews.tsx`、`frontend/src/features/market-news/`、`frontend/src/features/news/`、相关 tags/source-health 组件。
- 后端接口：`/api/market-news/*`、`/api/news/archive`、`/api/evidence/*`、`/api/source-health/*`、翻译接口和资讯流水线刷新。
- 存储：`%VR_DATA_DIR%/evidence-verification/v1` 资讯 raw/evidence/trusted/runs；来源体检历史；页面内存缓存与 tag localStorage。
- 原生重合：`/intel`、`backend/newsradar.py`、`backend/news_sources.json` 已提供赛道、RSS 列表、来源、发布时间、刷新、AI 今日要点和自选关联。
- 删除后保留：旧资讯流水线/证据/体检数据原位保留并标记 `PRESERVED_BUT_NOT_CURRENTLY_EXPOSED`；有价值的来源状态、缓存保留和安全探测迁入原生资讯雷达。

## 3. 行业研究

- 页面/路由/导航：`IndustryResearch`，`/industry-research`，侧栏“03 行业研究”。
- 前端：`frontend/src/pages/IndustryResearch.tsx`、`frontend/src/features/industry/`、`frontend/src/features/tags/`。
- 后端接口：`/api/industry-research/*`，并依赖 evidence/news/fund 平行模块。
- 存储：`%VR_DATA_DIR%/industry-research` 下的可信/候选快照、刷新状态和关系投影。
- 原生重合：板块中心、产业信号、个股数据、资讯雷达和研究记录按原生边界分别承载行业观察。
- 删除后保留：旧行业研究文件原位保留，标记 `PRESERVED_BUT_NOT_CURRENTLY_EXPOSED`；本 Work 不迁移、不删除。

## 4. 持仓分析

- 页面/路由/导航：`PortfolioAnalysis`，`/portfolio-analysis`，侧栏“04 持仓分析”。
- 前端：`frontend/src/pages/PortfolioAnalysis.tsx`、`frontend/src/features/fund-portfolio/`。
- 后端接口：`/api/fund-portfolio*`、基金搜索/刷新和穿透分析接口；依赖 `fund_data/`。
- 存储：用户基金持仓 JSON、基金缓存和行业归属数据。
- 原生重合：`/portfolio`“我的持仓”是 Vibe-Research 权威持仓工具；A/美/港股结构化数据分别由 `astock.py`/`gstock.py` 承担。
- 删除后保留：所有基金持仓与缓存之外的用户资料原位保留，标记 `PRESERVED_BUT_NOT_CURRENTLY_EXPOSED`。

## 5. 证据中心/来源中心

- 页面/路由/导航：`EvidenceCenterReal`，`/evidence-center`，侧栏“05 证据中心”。
- 前端：`frontend/src/features/evidence-center/`、`source-health/`、`source-catalog/`。
- 后端接口：`/api/evidence/*`、`/api/source-health/*`、`/api/data-sources/*`。
- 存储：证据快照/历史、来源体检、通用数据源配置/用量/预算；均位于用户数据目录。
- 原生重合：资讯来源属于 `/intel`；行情、财务、公告、研报等属于 `astock.py`、`gstock.py`、`market.py` 与原生工具。
- 删除后保留：旧证据/来源中心数据不删；来源启停、连接测试、最近成功/错误、条数、耗时、配置地址、缓存保留、URL 安全和脱敏能力迁入 `/intel/sources`。

## Git 引入证据摘要

- 五页共同起点：`1b20a7c`（`feat(pp03): complete v0.1 local research prototype`）。
- 持仓分析主要扩展：`58fc501`、`2ac9c25`、`5847cfb`。
- 市场资讯主要扩展：`fbf4a28` 起的 W2 链及后续资讯流水线提交。
- 证据中心主要扩展：`691c978`、`9a7cef1`、`9dfd824` 及 A2 历史链。
- 行业研究主要扩展：`571d7c6` 起的 W3 链至固定基线 `810204e`。

以上历史与路由证据排除了“1—5 其实是另一组功能”的歧义。
