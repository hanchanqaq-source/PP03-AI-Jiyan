# PP03 双持仓 Stage C 最终工程验收报告

日期：2026-08-28

项目：PP03｜AI Jiyan

Work：`DUAL_HOLDINGS`

分支：`codex/pp03-dual-holdings`

稳定基线：`0a44a031486d5f88e4da9808a04fcc2958da4c7b`

基金持仓历史参考：`810204efd317000aafa9836f8e2996ae4b1600ca`

## 判定

Stage B 用户实页验收已明确通过，用户原话为“页面通过”。Stage C 的定向测试、完整回归、隔离浏览器验收、Git/秘密/依赖漂移检查和独立复审全部通过。最终独立复审为 `Critical=0 / Important=0 / Minor=0`，`OPEN_FINDINGS=0`。

## 原生双持仓收口

- 侧栏只保留一个“我的持仓”入口，指向 `/portfolio/funds`。
- `/portfolio` 重定向 `/portfolio/funds`；`/portfolio-analysis` 兼容重定向 `/portfolio/funds`。
- `/portfolio/funds` 为默认基金持仓；`/portfolio/stocks` 保留原生股票持仓。
- 页面顶部只有“基金持仓”和“股票持仓”两个页签；公共标题为“我的持仓”，页签内标题分别说明当前数据类型，没有两个重复页面标题。
- 基金页支持基金代码与当前持有金额快速添加、总览、列表、详情、正式净值、盘中估算、历史净值、重合持仓、行业曝光、编辑和删除。
- 股票页继续使用股票代码、股数和成本价模型，保留实时盈亏、加仓、清仓记录、已清仓历史和既有本地存储规则。
- 基金文件为 `%VR_DATA_DIR%/fund-portfolio.json`，股票文件为 `%VR_DATA_DIR%/portfolio.json`；基金变更测试验证股票文件的 SHA-256、大小和 mtime 均不变化。
- 未恢复 `backend/data_sources/`、Provider Catalog、Evidence Center、Provider Center、Source Health Center、A2/W3 新闻流水线、行业研究系统或通用 Provider Hub。

## 旧 schema 安全验收

`backend/tests/test_fund_portfolio_storage_safety.py` 共 39 项通过，覆盖以下固定规则：

- v1、v2 仅在内存转换；打开、查看、组合分析路径和多次刷新均不写盘，原文件 SHA-256、大小、mtime 不变。
- v3 普通重复读取不产生无意义写入。
- 只有主动添加、编辑或删除基金才会触发旧 schema 迁移。
- 首次主动写入前完整校验旧文档，在同一数据目录创建唯一带时间戳备份，按字节数与 SHA-256 校验备份，再通过同目录临时文件和原子替换写入 v3；既有备份不覆盖，备份不自动删除。
- 备份写入失败、备份读取失败、备份哈希不匹配和原子替换失败时，原文件均保持不变，失败备份或临时文件被清理，写入失败关闭。
- 损坏 v1/v2/v3、重复基金代码、非法字段类型、v2 成本与 `cost_confirmation_required` 冲突均失败关闭且零写盘。
- 规范化 v3 在任何备份或写入之前再次验证；无效内存模型不能被误报为已持久化。
- v1 的 `manual_unverified` 身份在主动编辑迁移时保持；只修改备注/标签不会重算经济推断或改写快照来源。

## 浏览器终验

浏览器使用项目 `.tmp/acceptance` 下的隔离 `VR_DATA_DIR` 和 Codex 应用内隔离标签页；未读取真实浏览器 Profile、Cookie、账号、历史记录或真实持仓。

| 场景 | 结果 |
| --- | --- |
| 默认路由与侧栏 | PASS；`/portfolio/funds`，侧栏“我的持仓”仅 1 个 |
| 基金总览、列表、详情 | PASS；隔离数据中 2 只真实公开基金，正式净值与估算字段分开显示 |
| 重合持仓与行业曝光 | PASS；显示公开披露口径、数据日期、来源和质量说明 |
| 股票页签 | PASS；原生股票页面完整，基金/股票切换不覆盖数据 |
| 股票草稿跨页签 | PASS；基金/股票切换后未提交股票输入仍保留 |
| 基金添加、编辑、删除 | PASS；通过隔离数据完成，刷新后持久化，最终恢复 2 只验收基金 |
| 后端失败与部分覆盖 | PASS；不伪造数据；部分盘中覆盖显示 `—` 和明确的 `1/2` 覆盖说明 |
| 路由刷新 | PASS；刷新后保持 `/portfolio/funds`，兼容重定向正确 |
| Console error / warning | `0 / 0`；仅 Vite debug 和 React DevTools info |
| Pageerror | `0 observed` |
| Blocking app-local request failure | `0 observed`；最终核心 API 为 2xx |
| 平行数据中台入口 | PASS；Evidence/Provider/Catalog 等入口和活动调用均未恢复 |

## 自动化回归

- 双持仓 Backend 定向：`90 passed, 1 warning`。
- 双持仓 Frontend 定向：`7 files / 40 passed`。
- schema 存储安全：`39 passed`。
- Backend 全量非 Live：`310 passed, 12 deselected, 1 warning`。
- Frontend 全量：`9 files / 45 passed`。
- Legacy：`16 passed`。
- Production build：exit 0，`2472 modules transformed`；仅保留既有 chunk-size 建议告警。
- `git diff --cached --check`：exit 0。
- staged 新增行秘密扫描：0 命中。
- 依赖与 lockfile 漂移：0。
- LICENSE/README 归属文件漂移：0；原项目 MIT 许可和上游归属保持。
- 禁止模块/入口新增扫描：0；仓库跟踪的 `backend/data_sources/**` 文件数为 0。

## 独立复审

首轮完整复审发现 `2 Critical / 6 Important / 3 Minor`，涉及旧 schema 失败关闭、编辑时经济字段保持、缓存来源元数据、Mutation 错误可见性、不可用数据降级、强制刷新缓存、部分盘中合计、披露日期语义、历史年度查询和备份失败清理。全部按 RED—最小修复—定向回归闭环。

首轮修复复核确认 10 项关闭，保留 1 个旧 v2 交叉字段 Critical：数值成本与 `cost_confirmation_required=true` 的矛盾状态可能在迁移后产生不可读 v3。最终补强 v2 交叉字段校验，并在 `_persist_mutation` 的备份/写入前验证规范化 v3；新增两项 RED→GREEN 回归。最终独立复审结论：`PASS`，`Critical=0 / Important=0 / Minor=0`，`OPEN_FINDINGS=0`。

## 数据与外部动作边界

- Stage B/Stage C 只使用 `.tmp/acceptance` 隔离数据；未打开、复制、截图、迁移、修改或删除真实基金/股票持仓。
- 未读取用户 API Key，未调用付费或企业接口，未产生真实费用。
- 未整体恢复或 cherry-pick `810204e`；只恢复基金页面实际调用的最小模块、接口和测试。
- 未创建 PR、未合并 main、未 Force Push、未创建 Tag/Release、未部署、未开始下一 Work。
