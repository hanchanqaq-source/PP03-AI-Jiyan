# A1 数据源健康 After 审计

审计时间：`2026-08-17T22:21:29.872679+00:00` 至 `2026-08-17T22:21:40.933935+00:00`；修复 HEAD：`1470a40d640ef304692a4421c6626641879287bf`；
隔离根：`D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1`。本次仍只访问同一组公开入口，未读取真实用户数据。

权威 After run_id：`7f744e9abe6eb7238238`；`after-snapshot.json` SHA-256：
`2C60B0BC10337363DA04E19C6AB5503DC1E4051F62F6EB63CCF2EF4FAB188577`。

本快照是 Fix Round 1 后的权威 After，取代 run `e9cc914472e5a2ef74e9`（快照 SHA-256
`F9A9BDA316FF84AC66AE02612F9971974B21A04DEFAD52E7BDF7DA001C8EC596`）。旧快照只作为历史证据保留在
`after-snapshot-superseded-d218a29.json`；不得再用于最终能力矩阵或改善归因。

## 同输入证明

```powershell
$env:VR_DATA_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1\data"
$env:VR_NEWS_CACHE_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1\news"
$env:VR_ACCEPTANCE_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1"
$env:VR_SOURCE_HEALTH_STARTUP="0"
```

`backend\.venv\Scripts\python.exe` 导入 `SourceHealthService`，执行
`start_run("full")`，按 1 秒轮询 `get_run(run_id)` 至 `completed`，随后将
`current-snapshot.json` 复制为隔离目录中的 before/after 快照。该命令未启动应用服务、未读取用户目录、未使用缓存。

- 新闻配置 SHA-256：Before = After = `2D53B3D7064688823A0FDD41C1BBD29C0D67F40C072EA6975AE4226015779F7A`
- 样本目录 SHA-256：Before = After = `C869FD5308247B14EC4FAD4FC00162246B14C65CA573EF87914659658CF3C2D7`
- source_id 集合：Before = After = 119 个（集合严格相等）
- 五字段完全重复配置：Before = After = 0

## After 汇总

| 指标 | After |
|---|---:|
| 实际注册／实际完成 | 119 / 119 |
| 基金、行情、行业能力 | 11 |
| 资讯配置 | 108 |
| 成功／部分成功／失败 | 99 / 13 / 7 |
| P50 / P95 | 309 ms / 2484 ms |
| 平均字段完整率 | 93.28% |
| 有日期的新鲜度样本 | 109 / 119 |
| 新鲜度 min / P50 / max | 100s / 6.36h / 1844.93d |
| 总耗时 | 12.984 s |
| 评级置信度 | `initial` |

错误分布：`connection=1`、`empty_payload=2`、`http=1`、`none=99`、`parse=1`、`redirect=12`、`stale_data=1`、`tls=2`。

初始评级分布：`degraded=1`、`failed=7`、`healthy=99`、`usable=12`。

## Before / After 比较

| 指标 | Before | After | 变化／解释 |
|---|---:|---:|---|
| 成功 | 98 | 99 | +1；公网观测波动，不归因于修复 |
| 部分成功 | 13 | 13 | 0 |
| 失败 | 8 | 7 | -1；公网观测波动，不归因于修复 |
| P50 | 291 ms | 309 ms | +18 ms |
| P95 | 2287 ms | 2484 ms | +197 ms |
| 平均字段完整率 | 82.35% | 93.28% | +10.92 个百分点 |
| 跟随到最终地址 | 12 | 12 | 0 |
| 独立确认 301/308 | 12 / 0 | 12 / 0 | 0；未改配置 URL |
| parse 错误 | 1 | 1 | 0 |

新闻完整率现在按每条 item 的 `title`、`url`、`published_at` 三个核心公开字段逐字段计算，不再以
“items 非空”推定 100%。本轮成功返回的实时样本恰好没有缺少这三个核心字段；确定性回归以一个完整 item
和一个仅有 title 的 item 证明结果为 66.67%，未伪造完整率。

成功／失败数量与时延的变化是不同时间点的公共网络观测，不能归因于本轮诊断契约修复。可归因的确定性结果仅为：
`probe_source_config` 保留权威 `final_url`；runner 将原始 `final_reference` 或 `final_url` 映射为健康记录的
`final_reference`；完整率按三个核心字段计算；URL 查询参数仅对精确键 `code` 脱敏。

## After 非成功明细

| source_id | 来源 | 状态 | error | HTTP | 条数 | 日期 | 完整率 | 延迟 | 修复价值 |
|---|---|---:|---|---:|---:|---|---:|---:|---|
| `news:0063c85f43e60076` | 国际能源网 | failure | `connection` | — | 0 | — | 0.00% | 2898 ms | `none` |
| `news:46c5aa60dc040dba` | 36氪 | failure | `parse` | — | 0 | — | 0.00% | 998 ms | `none` |
| `news:708f88de6b0fa442` | FierceBiotech | failure | `empty_payload` | 200 | 0 | — | 0.00% | 139 ms | `none` |
| `news:772f466c8c0bc84f` | 虎嗅 | failure | `tls` | — | 0 | — | 0.00% | 584 ms | `observe` |
| `news:df3335a4a80a661d` | arXiv cs.AI | failure | `tls` | — | 0 | — | 0.00% | 74 ms | `observe` |
| `news:f4c0e57e06463c06` | FiercePharma | failure | `empty_payload` | 200 | 0 | — | 0.00% | 150 ms | `none` |
| `quote:eastmoney-direct:stock_snapshot` | eastmoney-direct | failure | `http` | 502 | 0 | — | 0.00% | 2216 ms | `observe` |
| `news:3017e8411834b32a` | New Scientist | partial | `redirect` | 200 | 2 | 2026-08-17T23:48:20+08:00 | 100.00% | 946 ms | `none` |
| `news:3443a9fa0e028f34` | Financial Times | partial | `redirect` | 200 | 2 | 2026-08-18T01:06:13+08:00 | 100.00% | 511 ms | `none` |
| `news:37085a92d14c67c4` | Quanta Magazine | partial | `redirect` | 200 | 2 | 2026-08-17T23:11:17+08:00 | 100.00% | 490 ms | `none` |
| `news:54bb6004ad8b405b` | Endpoints News | partial | `redirect` | 200 | 2 | 2026-08-18T04:20:28+08:00 | 100.00% | 425 ms | `none` |
| `news:57bf88dafb0d5ce8` | Space.com | partial | `redirect` | 200 | 2 | 2026-08-18T05:00:00+08:00 | 100.00% | 351 ms | `none` |
| `news:68779a6ad6e56d73` | SemiAnalysis | partial | `redirect` | 200 | 2 | 2026-08-17T06:27:59+08:00 | 100.00% | 270 ms | `none` |
| `news:6e3127956b63d8c3` | Robotics Business Review | partial | `redirect` | 200 | 2 | 2026-08-18T05:15:46+08:00 | 100.00% | 284 ms | `none` |
| `news:6ee1acdae59fe508` | Live Science | partial | `redirect` | 200 | 2 | 2026-08-18T05:30:15+08:00 | 100.00% | 308 ms | `none` |
| `news:77eba0e4610108d3` | 白鲸出海 | partial | `redirect` | 200 | 2 | 2026-08-13T16:17:54+08:00 | 100.00% | 4346 ms | `none` |
| `news:82d0ae94ec76adac` | DPReview | partial | `redirect` | 200 | 2 | 2026-08-18T02:18:53+08:00 | 100.00% | 254 ms | `none` |
| `news:905d4f7cc34ee856` | Canary Media | partial | `redirect` | 200 | 2 | 2026-08-17T15:30:00+08:00 | 100.00% | 567 ms | `none` |
| `news:a85c2e4387773228` | WSJ Markets | partial | `stale_data` | 200 | 0 | 2025-01-28T03:26:00+08:00 | 0.00% | 177 ms | `none` |
| `news:e15e8e0612acee25` | MarketWatch | partial | `redirect` | 200 | 2 | 2026-08-18T05:30:00+08:00 | 100.00% | 309 ms | `none` |

## 未解决问题

- TLS、502、连接失败、空载荷与解析失败均未绕过；此前 After 观察到的 403 在本轮公网请求中恢复，仅属于波动，不构成修复证明。
- 12 个 301 未修改配置 URL：全局验收约束要求 Before/After 使用完全相同的配置与 source_id；另有跨域、HTTPS 降级或独立请求最终 403 的个案，不能合并视为无风险。
- 实时审计只有一次 Before 和一次 After，评级必须继续显示 `initial`，不能推导长期可用性。
