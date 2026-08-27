# PP03 原生架构收敛与资讯雷达数据源设计

## 决策与边界

本设计执行用户 2026-08-27 任务书。Vibe-Research 原项目继续作为唯一产品壳、页面主干和数据主干；PP03 不再保留 `/research-home`、`/market-news`、`/industry-research`、`/portfolio-analysis`、`/evidence-center` 五个平行页面，也不再维护 Catalog、Evidence Center、Provider Center、通用 Adapter、独立资讯流水线或基金/行业平行后端。

固定起点为 `810204efd317000aafa9836f8e2996ae4b1600ca`，工作分支为 `codex/pp03-native-radar-convergence`。只读 upstream 为 `simonlin1212/Vibe-Research` 的 `ab4ffa077e0b1806fc53164dc7b28731f834e79e`。不整体 merge upstream；仅移植原生导航、产业信号、资讯雷达及与本 Work 直接相关的修复，并保留 MIT 许可证和作者归属。

旧 PP03 用户文件不删除、不迁移、不重写。失去页面入口的基金持仓、证据、资讯流水线、行业研究和来源体检数据统一记录为 `PRESERVED_BUT_NOT_CURRENTLY_EXPOSED`。

## 产品架构

前端恢复 upstream 当前导航：每日复盘、资讯雷达、产业信号、板块中心、个股数据、多空辩论、自选股、我的持仓、我的研报、研究记录、接入 AI。根路由回到 `/daily-review`。`/intel` 与 `/intel/:tab` 是唯一资讯产品入口。

资讯雷达保留原有四个内容栏目，并新增 `sources` 子栏目“数据源管理”。它是同一页面内的 Tab，不是新路由产品。左侧资讯雷达子导航可进入 `/intel/sources`，仍由 `Intel` 页面渲染。

后端继续以 `astock.py`、`gstock.py`、`market.py`、`newsradar.py`、`signals.py`、`app.py`、`tools.py`、`chat.py`、`mcp_server.py`、`debate.py` 为权威实现。新增的 `news_source_manager.py` 只负责资讯来源配置、持久化、安全探测和状态投影；它不承接行情、财务、公告、基金或通用 Provider 能力。

## 来源配置与数据流

`backend/news_sources.json` 是仓库内置来源的只读定义。用户来源和本地开关保存到 `%VR_DATA_DIR%/news-sources.custom.json`；未设置 `VR_DATA_DIR` 时使用 `%USERPROFILE%/.vibe-research/news-sources.custom.json`。文件使用 schema v1：

```json
{
  "schema_version": 1,
  "disabled_builtin_ids": [],
  "custom_sources": [],
  "health": {}
}
```

保存采用同目录临时文件、flush、`os.replace` 的原子发布。文件损坏时不覆盖原文件；API 返回 `custom_config_status=corrupt` 和明确提示，但仍展示并运行内置来源。

运行时数据流为：

```text
news_sources.json + news-sources.custom.json
  -> 规范化 URL 与稳定 source_id
  -> 按 URL 去重（内置优先）
  -> 应用本地启停
  -> newsradar 抓取
  -> 按 source_id 回写最近检查/成功/错误/条数/耗时
  -> 保留失败来源的既有缓存
```

内置来源不可删除，只能本地停用；自定义来源可删除。配置地址在 API/UI 中对 `token`、`key`、`secret`、`password`、`credential`、`signature` 等查询参数脱敏。

## RSS/Atom 安全探测

新增来源保存前必须通过生产同路的探测器：

- 只接受 `http`/`https`，拒绝用户名/密码 URL、无主机、无效端口和 fragment；
- DNS 解析得到的每个地址都必须是公网地址，拒绝 loopback、private、link-local、reserved、multicast 和 unspecified；
- 每次重定向重新校验 URL 与解析地址，最多 3 次；
- TLS 使用默认验证，不提供关闭开关；
- 总响应体上限 1 MiB，读取超过上限立即失败；
- HTTP 必须为 2xx；XML 必须是 RSS/RDF/Atom，且至少解析出标题或链接；
- 错误只返回分类和脱敏消息，不返回本机路径、Authorization、Cookie 或敏感查询值。

API 类型不提供通用 JSON 猜测器。第一阶段 UI 只支持 RSS/Atom；未知 API 类型显示“该 API 类型尚未支持”。

## API 合同

```text
GET    /api/radar/sources
POST   /api/radar/sources/test
POST   /api/radar/sources
POST   /api/radar/sources/{source_id}/test
PUT    /api/radar/sources/{source_id}/enabled
DELETE /api/radar/sources/{source_id}
```

写端点继续遵守原生 `VR_API_KEY`；另要求本地浏览器来源和 `X-PP03-Write-Intent: 1`，避免网页跨站触发本地配置写入。所有响应包在 `{"data": ...}` 中。删除内置来源返回 409，重复 URL 返回 409，损坏自定义配置上的写入返回 409。

## 前端状态与错误处理

`RadarSourceManager` 加载来源列表，展示内置/用户来源、类型、赛道、启用状态、最近成功、最近失败原因、最近抓取条数、最后检查、脱敏配置地址，并提供测试、启停、添加和自定义来源删除。

添加表单字段为名称、RSS/Atom URL、赛道、标签/关键词、启用。提交时先调用测试端点；测试通过后才允许保存。网络或配置失败保留现有列表和输入，显示来源级错误。保存成功后重新加载列表；页面刷新后由本地 JSON 恢复。

## 删除与保留

删除生产代码前以 `removed-parallel-modules.md` 记录调用方、原生替代、数据状态和 Git 回退。历史验收文档与截图保留，避免抹除既有证据；仅删除只服务旧页面的启动器、Fixture 和自动化测试。

所有被废弃的用户数据目录只停止读取，不执行删除。回退方式是切回固定提交/旧 worktree，或从本分支父提交恢复代码；不需要恢复用户数据，因为本 Work 不改动它们。

## 验证

实现严格按 RED→GREEN：先让路由/导航、来源存储、安全探测、API 和 UI 测试因能力缺失而失败，再写最小实现。定向测试通过后启动真实 `127.0.0.1:8900` / `127.0.0.1:5899`，使用隔离的 `VR_DATA_DIR`、`VR_REPORTS_DIR` 和 `VR_NEWS_CACHE_DIR`，完成浏览器验收并保持服务运行。

用户确认真实页面后，再运行 Backend 全量离线测试、Frontend 全量、Legacy、production build、浏览器 console/pageerror/requestfailed 分类、用户数据哈希对比、秘密扫描、`git diff --check` 和独立代码复审。
