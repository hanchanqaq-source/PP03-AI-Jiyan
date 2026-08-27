# PP03 原生架构与资讯雷达来源迁移

日期：2026-08-27
基线：`810204efd317000aafa9836f8e2996ae4b1600ca`
目标分支：`codex/pp03-native-radar-convergence`

## 结论

PP03 不再维护 Vibe-Research 之外的五个平行产品入口与通用数据中台。页面回到原生 `/daily-review`、`/intel`、`/signals`、`/sectors`、`/portfolio` 等边界；来源管理作为 `/intel/sources` 子栏目存在。

## 三份来源基线对比

| 基线 | 配置/结果 | 迁移判定 |
| --- | --- | --- |
| 固定基线 `810204e` | `news_sources.json` 108 行来源；按 URL/名称去重后 106 个；Engadget 与少数派各重复 1 次 | 去掉两个重复条目，保留 106 个唯一内置 RSS 源及已有 CN/region 元数据 |
| 当前 `upstream/main` `ab4ffa0` | 106 个唯一内置来源；包含 investment-news v1.0.2 同步、去重修复与原生产业信号 | 采纳 106 源集合、URL 查询重编码修复、48 小时同标题去重、Signals/GPU 租金页面/API/工具链 |
| A2 历史审计 | 147 source families、150 adapters、108 news-source 行；85 success、16 partial、7 failure，共 23 个降级项；无 repair/update/replace/disable 结论 | 历史审计保留作证据，不把 147/150 Catalog 或付费/Mock 壳迁入原生运行时，不把历史 partial/failure 改写为成功 |

## 新运行时配置

- 内置配置：`backend/news_sources.json`，仓库版本控制，106 个唯一来源。
- 用户配置：`%VR_DATA_DIR%\news-sources.custom.json`；未设置 `VR_DATA_DIR` 时使用 `~/.vibe-research/news-sources.custom.json`。
- 自定义文档只保存 `schema_version`、`disabled_builtin_ids`、`custom_sources` 和脱敏健康状态；原子临时文件 + `os.replace`。
- 文件缺失：正常使用全部内置源；文件损坏：继续使用内置源但拒绝写入，避免覆盖可恢复数据。
- 内置源不可删除，只能停用；自定义源删除采用 UI 二次确认。

## 连接与安全边界

- RSS/Atom：仅 HTTP/HTTPS；拒绝凭据 URL、fragment、本机、内网、链路本地、保留地址和解析到非公网 IP 的域名；主机尾点/IDNA 归一化阻止重复绕过。
- 产品刷新、单源重试与来源测试共用一个运输：禁用环境代理，将 socket 连到同一步已验证的公网 DNS 地址，每次重定向重检，最多 3 次，默认 TLS 证书验证。
- 压缩响应与 gzip 解压后输出均按 2 MiB 有界读取；响应必须是 RSS/Atom XML，且至少一个条目同时有 `title` 与 `link`。
- 存储加载对 UTF-8、逐条字段/ID/安全 URL及健康状态 enum/ISO 时间/HTTP 状态执行语义校验；API/UI 的来源地址只提供 scheme + host，Radar cache/API 仅用 canonical `source_id` 关联、不保存 feed URL。原始抓取 URL 仅保留在隔离存储与抓取进程内存。
- API：只允许登记过的显式适配器；当前适配器表为空，因此 UI 会明确显示“当前没有已实现的 API 适配器”，不会执行通用 JSON 猜测。
- 所有来源写接口需要 `X-PP03-Write-Intent: 1`、唯一的本机 Host 与本机浏览器 Origin；若配置 `VR_API_KEY`，仍需全局 Bearer 鉴权。

## 旧数据处理

以下用户数据不删除、不枚举、不自动转写，统一状态为 `PRESERVED_BUT_NOT_CURRENTLY_EXPOSED`：

- `fund-portfolio.json` 与 fund cache；
- `evidence-verification/v1`；
- `source-health`；
- `data-sources/v1`、usage/budget/config；
- `industry-research`；
- 研报、自选、笔记、股票持仓、AI 配置和 API Key。

不自动把旧 Catalog 条目导入 RSS manager：Catalog 的 provider capability、许可、凭据和预算语义与 RSS feed 不等价，自动转换可能造成错误启用、凭据暴露或伪成功。

## 回退

- 代码：切回固定基线 worktree/父提交；本 Work 的删除均可由 Git 恢复。
- 用户配置：`news-sources.custom.json` 独立于内置配置；回退旧版本时文件会保留但不会被旧代码读取。
- 旧数据：始终原位保留，无需反向迁移。

## upstream 采纳记录

- `e45fb3e`：产业信号与 GPU 租金原生链路。
- `6dac1ca`：内嵌数据源同步到 investment-news v1.0.2。
- `0b28982`：数据准确性与健壮性修复；本 Work 采纳 URL 查询重编码、同标题滚动基准去重和 AI 工具结果裁剪。
- `ab4ffa0`：文档/截图更新，不作为运行时能力来源。

## Stage C 最终迁移清单

- 106 行逐源账本见 `docs/acceptance/native-convergence/news-source-migration.md`；与 `ab4ffa0` 的名称 + URL 精确匹配为 106/106。
- A2 冻结的 23 个非成功决策逐项保留：16 partial、7 failure；21 `观察`、1 `需要凭据`、1 `需要许可证`；修复、更新、替代和从 A2 决策触发的停用均为 0。
- 正式 upstream SHA 与 MIT blob 记录见 `docs/acceptance/native-convergence/upstream-reference.md`。
- Stage B/Stage C 实测、浏览器终验和隔离数据边界见 `docs/acceptance/native-convergence/stage-c-final-acceptance.md`。
