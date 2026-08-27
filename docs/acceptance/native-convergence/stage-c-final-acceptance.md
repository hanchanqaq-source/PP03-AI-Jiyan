# Stage C 最终工程验收报告

日期：2026-08-28
项目：PP03｜AI Jiyan
Work：`NATIVE_DATA_AND_RADAR_CONVERGENCE`
分支：`codex/pp03-native-radar-convergence`
基线：`810204efd317000aafa9836f8e2996ae4b1600ca`

## 判定

Stage B 用户实页验收已明确通过：`USER_PAGE_ACCEPTANCE=PASS`，用户原话为“目前看起来还可以”。Stage C 定向验证、完整回归和浏览器终验均通过。首轮独立复审提出 1 Critical、4 Important、1 Minor，第二轮提出 0 Critical、4 Important、1 Minor；均按失败测试—最小修复—全量重测闭环，最终独立复审结论见本报告末尾。

## 架构收敛

- 原生导航恢复；根路径与未知/已移除旧路径统一回落 `/daily-review`；`/intel` 及未知 `/intel/:tab` 定向跳转到唯一资讯入口 `/intel/investment-news`。
- `/intel/investment-news` 是 Investment News 真实资讯入口；`/intel/sources` 是内置/自定义来源管理入口。
- 1—5 五个平行入口、独立 Evidence Center、独立 Provider Center 均无生产路由、导航或活动调用。
- 292 个仅服务平行运行时树的跟踪文件被移除；历史正式文档、截图和用户数据未删除。
- 原生保留 `astock.py`、`gstock.py`、`market.py`、`newsradar.py`、`signals.py`、`tools.py`、`chat.py`、`mcp_server.py` 与 `debate.py`。

## 资讯来源与安全

- 内置来源 106 个，12 个赛道；固定基线的 Engadget 与少数派重复行各折叠 1 条。
- A2 的 23 个历史降级/失败决策保持为 21 `观察`、1 `需要凭据`、1 `需要许可证`；未改写为当前成功。
- 自定义来源只写 `%VR_DATA_DIR%/news-sources.custom.json`；仓库内置 `backend/news_sources.json` 在浏览器验收前后 SHA-256 均为 `6D0C489E62396C7E140488B7F29F40C7A83FDBC07C54F2D76E4362D24219DCB0`。
- 内置来源不可删除，真实 HTTP 验证为 `409 Conflict`；重复 URL 同样为 `409 Conflict`。
- RSS/Atom 产品刷新、单源重试与来源测试共用同一安全传输：连接 socket 固定到已验证的公网 DNS 解析结果，禁用环境代理，每次重定向重检，最大 3 次重定向，默认 TLS 验证，阻止本机/内网/链路本地/保留地址。
- 压缩前与 gzip 解压后均以 2 MiB 有界读取；产品刷新必须验证 RSS/Atom 根元素且至少有一个同时含 `title`/`link` 的条目。
- 自定义存储同时校验 UTF-8、顶层结构、逐条字段类型、ID、URL 静态安全，以及健康状态枚举、ISO 时间、HTTP 状态类型与错误类型；任何损坏都只回退到内置源并禁写。
- Source Manager 与 Radar 统一使用 canonical URL 派生的 `source_id`；runtime config 显式携带 ID。Radar 缓存和 `/api/radar` 只保留 `source_id`，不保存或输出原始 feed URL。
- API 列表/添加/测试的来源地址只返回 `scheme://host[:port]` 形式的 `display_url`，不返回路径、查询值、URL userinfo 或可抓取的原始密签 URL；UI 仅以非链接文本展示。URL fragment 被拒绝，主机 IDNA/尾点归一化用于重复检查与 ID。
- API 采用显式适配器 allowlist；当前适配器为空，UI 明确显示不支持，不猜测任意 JSON 结构。
- 损坏自定义配置时内置来源继续可用，但写入被禁用，避免覆盖可恢复文件。

## 浏览器终验

运行边界：项目 `.tmp/acceptance/native-convergence-stage-c-browser-final` 下的隔离 data、news cache、reports，以及项目 `.tmp/acceptance/native-convergence-stage-c-browser` 下的隔离浏览器 profile；使用 Codex 应用内隔离浏览器标签页，不读取真实浏览器 Profile、Cookie、账号或历史记录。

| 场景 | 结果 |
| --- | --- |
| 原生导航与 Investment News | PASS；显示真实公开资讯、来源和更新时间 |
| 12 个赛道切换 | PASS；最终真实刷新中 12/12 赛道均有公开资讯，每项显示对应“今日要点” |
| 来源管理与 106 个内置来源 | PASS |
| BBC 隔离 RSS：测试、解析、保存、刷新持久化 | PASS；测试解析 20 条 |
| 停用后退出抓取、启用后恢复 | PASS；抓取源数 107→106→107，恢复后可见 BBC 条目 |
| feed URL 隐私边界 | PASS；验收 BBC 使用 `sig` 查询；保存后 UI 只显示 host，Radar cache 中 `source_url` 和该查询值出现次数均为 0 |
| 删除自定义来源 | PASS；最终来源页为 106 内置 / 0 自定义 |
| 错误 RSS | PASS；准确显示 `来源返回 HTTP 404`，保存保持禁用 |
| 损坏配置降级 | PASS；显示“自定义配置已损坏；内置来源继续可用，写入已禁用” |
| 不支持的 API | PASS；显示没有已实现适配器，不尝试 JSON 猜测 |
| 已移除旧入口 | PASS；7 条代表性旧路径均回落 `/daily-review` |
| 未知 Intel 子路由 | PASS；`/intel/foo` 显式重定向 `/intel/investment-news` |
| Console error / warning | `0 / 0`（修复后新建干净标签页） |
| Pageerror | `0 observed`（修复后新建标签页） |
| Blocking app-local request failure | `0 observed`；最终服务日志中验收页面/核心 API 入站请求均为 2xx |

截图：`docs/screenshots/native-convergence/stage-c-investment-news.png`（SHA-256 `335D1DC0AAA7ABC07B35D120DC2135B245606D24EAB2CEA5267A87659944980F`）、`docs/screenshots/native-convergence/stage-c-source-management.png`（SHA-256 `0B04D621BF5CE20ED7F0318296F90C03C305F587B5CAE44DCDDCB56D94B3FABD`）。

非阻塞压力诊断观察：一次每 120ms 连续注入 7 个已移除 URL 的异常节奏造成既有 DailyReview/`py_mini_racer` 路径进程崩溃；该路径不属于本 Work 的 Radar/来源管理改动，生产导航也不存在这些入口。全新进程按真实用户节奏逐页等待 1.4 秒复验为 7/7 重定向、相关 API 全部 2xx、无 fetch error 且服务存活。独立复审判定其不是本 Work 的开放问题；若未来定义 rapid-navigation/stress SLO，应另开 Work 复现既有并发稳定性。

## 自动化回归

- Backend 非 Live：`220 passed, 12 deselected, 1 warning`。
- Frontend：`3 files / 18 tests passed`。
- Legacy：`16 passed`。
- Production build：exit 0，2462 modules；保留 chunk size 建议告警。
- 完整 staged diff（包含原 untracked 新文件与 staged 删除）的 `git diff --cached --check`：exit 0。
- 依赖/lockfile 漂移：NONE。
- 生产旧调用 caller scan：0。

## 数据与外部动作边界

- Stage B 的 BBC 自定义 RSS 只存在于 C 盘工作区 `.tmp` 预览目录；Stage C 使用 D 盘 worktree 的独立 `.tmp/acceptance` 数据。两者都属于验收数据，将在最终提交后清理。
- 未枚举、读取、迁移或删除真实用户数据；旧基金、证据、来源健康、通用数据源、行业研究数据保持 `PRESERVED_BUT_NOT_CURRENTLY_EXPOSED`。
- 未读取用户 Key，未调用付费/企业 Provider，未产生真实费用。
- 未创建 PR、未合并 main、未 Force Push、未创建 Tag/Release、未部署、未开始下一 Work。

## 独立复审

首轮结论：`FAIL`，发现 1 Critical、4 Important、1 Minor。已修复：

1. 产品 radar 刷新/重试共用固定 DNS socket、重定向重检、有界压缩/解压读取与 feed 合同验证。
2. 损坏 store 扩展到 Unicode、逐条语义、ID/类型/健康状态校验与内存脱敏。
3. 来源 API/UI 不再暴露原始密签 URL。
4. `/intel` 收敛为 `/intel/investment-news` 定向入口，父级导航也指向该路径。
5. 补全 fragment 拒绝、IDNA/尾点归一化与全量 staged diff 检查。
6. 浏览器发现的 Python HTTPS handler 运行时兼容性问题也已按 TDD 修复，并用 BBC HTTPS RSS 重走全流程。

第二轮结论：`FAIL`，发现 0 Critical、4 Important、1 Minor。已修复：

1. Radar item/status/cache/API 全部移除原始 `source_url`，改以统一 `source_id` 关联；带 `sig` 查询的验收缓存扫描为 0 泄漏。
2. `display_url` 收敛到 scheme + host；错误文本统一清除 URL userinfo、路径/查询与 credential/signature/sig 等自由文本。
3. 手工健康状态加入 enum、ISO 时间、HTTP 状态与字段类型校验；不可哈希类型亦安全降级为 corrupt，不产生 500。
4. Manager/Radar 只保留一个 canonical ID 实现，refresh→health→retry 集成测试通过。
5. `/intel/foo` 等未知子路由显式重定向唯一 `/intel/investment-news`。

第三轮最终只读快照：`PASS`，`Critical=0 / Important=0 / Minor=0`，`OPEN_FINDINGS=0`。复审确认第二轮 5 项全部关闭，323 个候选文件统一 staged、unstaged/untracked 均为 0，cached/full-base diff check 均为 exit 0；120ms 压力诊断只作为超范围既有并发稳定性线索记录，不计本 Work finding。
