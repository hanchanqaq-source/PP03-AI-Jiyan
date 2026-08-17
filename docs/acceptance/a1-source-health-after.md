# A1 数据源健康 After 审计

审计时间：`2026-08-17T21:59:01.113876+00:00` 至 `2026-08-17T21:59:11.522573+00:00`；修复 HEAD：`617ed601bdd30f5ab29ab79036474228ee3ebd98`；
隔离根：`D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1`。本次仍只访问同一组公开入口，未读取真实用户数据。

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
| 成功／部分成功／失败 | 97 / 14 / 8 |
| P50 / P95 | 287 ms / 1800 ms |
| 平均字段完整率 | 92.44% |
| 有日期的新鲜度样本 | 108 / 119 |
| 新鲜度 min / P50 / max | 300s / 5.98h / 1844.92d |
| 总耗时 | 11.927 s |
| 评级置信度 | `initial` |

错误分布：`authentication=1`、`connection=1`、`empty_payload=2`、`http=1`、`none=97`、`parse=1`、`redirect=13`、`stale_data=1`、`tls=2`。

初始评级分布：`degraded=1`、`failed=8`、`healthy=97`、`usable=13`。

## Before / After 比较

| 指标 | Before | After | 变化／解释 |
|---|---:|---:|---|
| 成功 | 98 | 97 | -1；Nature 临时出现重定向，属实时波动 |
| 部分成功 | 13 | 14 | +1；同上 |
| 失败 | 8 | 8 | 0 |
| P50 | 291 ms | 287 ms | -4 ms |
| P95 | 2287 ms | 1800 ms | -487 ms |
| 平均字段完整率 | 82.35% | 92.44% | +10.08 个百分点 |
| 跟随到最终地址 | 12 | 13 | +1；实时波动 |
| 独立确认 301/308 | 12 / 0 | 12 / 0 | 0；未改配置 URL |
| parse 错误 | 1 | 1 | 0 |

12 个 Before 已解析重定向源的字段完整率均由 0% 纠正为 100%，并保留脱敏后的最终公开地址。
After 新增的 Nature 重定向是 303/302 Cookie 往返，不是永久跳转，不构成 URL 自动修复依据。
成功率从 82.35% 变为 81.51%，失败率保持 6.72%；这不是回归证明，而是同一时段的公共网络波动。

## After 非成功明细

| source_id | 来源 | 状态 | error | HTTP | 条数 | 日期 | 完整率 | 延迟 | 修复价值 |
|---|---|---:|---|---:|---:|---|---:|---:|---|
| `news:0063c85f43e60076` | 国际能源网 | failure | `connection` | — | 0 | — | 0.00% | 3314 ms | `none` |
| `news:44726681bff6e16e` | 动点科技 | failure | `authentication` | 403 | 0 | — | 0.00% | 105 ms | `replace_candidate` |
| `news:46c5aa60dc040dba` | 36氪 | failure | `parse` | — | 0 | — | 0.00% | 1012 ms | `none` |
| `news:708f88de6b0fa442` | FierceBiotech | failure | `empty_payload` | 200 | 0 | — | 0.00% | 70 ms | `none` |
| `news:772f466c8c0bc84f` | 虎嗅 | failure | `tls` | — | 0 | — | 0.00% | 582 ms | `observe` |
| `news:df3335a4a80a661d` | arXiv cs.AI | failure | `tls` | — | 0 | — | 0.00% | 104 ms | `observe` |
| `news:f4c0e57e06463c06` | FiercePharma | failure | `empty_payload` | 200 | 0 | — | 0.00% | 70 ms | `none` |
| `quote:eastmoney-direct:stock_snapshot` | eastmoney-direct | failure | `http` | 502 | 0 | — | 0.00% | 2101 ms | `observe` |
| `news:3017e8411834b32a` | New Scientist | partial | `redirect` | 200 | 2 | 2026-08-17T23:48:20+08:00 | 100.00% | 649 ms | `none` |
| `news:3443a9fa0e028f34` | Financial Times | partial | `redirect` | 200 | 2 | 2026-08-18T01:06:13+08:00 | 100.00% | 531 ms | `none` |
| `news:37085a92d14c67c4` | Quanta Magazine | partial | `redirect` | 200 | 2 | 2026-08-17T23:11:17+08:00 | 100.00% | 396 ms | `none` |
| `news:54bb6004ad8b405b` | Endpoints News | partial | `redirect` | 200 | 2 | 2026-08-18T04:20:28+08:00 | 100.00% | 492 ms | `none` |
| `news:57bf88dafb0d5ce8` | Space.com | partial | `redirect` | 200 | 2 | 2026-08-18T05:00:00+08:00 | 100.00% | 214 ms | `none` |
| `news:68779a6ad6e56d73` | SemiAnalysis | partial | `redirect` | 200 | 2 | 2026-08-17T06:27:59+08:00 | 100.00% | 255 ms | `none` |
| `news:6a9740a8d6f94e81` | Nature News | partial | `redirect` | 200 | 2 | 2026-08-17T08:00:00+08:00 | 100.00% | 777 ms | `none` |
| `news:6e3127956b63d8c3` | Robotics Business Review | partial | `redirect` | 200 | 2 | 2026-08-18T05:15:46+08:00 | 100.00% | 236 ms | `none` |
| `news:6ee1acdae59fe508` | Live Science | partial | `redirect` | 200 | 2 | 2026-08-18T05:30:15+08:00 | 100.00% | 308 ms | `none` |
| `news:77eba0e4610108d3` | 白鲸出海 | partial | `redirect` | 200 | 2 | 2026-08-13T16:17:54+08:00 | 100.00% | 2781 ms | `none` |
| `news:82d0ae94ec76adac` | DPReview | partial | `redirect` | 200 | 2 | 2026-08-18T02:18:53+08:00 | 100.00% | 209 ms | `none` |
| `news:905d4f7cc34ee856` | Canary Media | partial | `redirect` | 200 | 2 | 2026-08-17T15:30:00+08:00 | 100.00% | 180 ms | `none` |
| `news:a85c2e4387773228` | WSJ Markets | partial | `stale_data` | 200 | 0 | 2025-01-28T03:26:00+08:00 | 0.00% | 215 ms | `none` |
| `news:e15e8e0612acee25` | MarketWatch | partial | `redirect` | 200 | 2 | 2026-08-18T05:30:00+08:00 | 100.00% | 352 ms | `none` |

## 未解决问题

- `403`、TLS、Cookie 跳转、最终 403、502、连接失败、空载荷与解析失败均未绕过；继续保持 `observe` 或 `replace_candidate`。
- 12 个 301 未修改配置 URL：全局验收约束要求 Before/After 使用完全相同的配置与 source_id；另有跨域、HTTPS 降级或独立请求最终 403 的个案，不能合并视为无风险。
- 实时审计只有一次 Before 和一次 After，评级必须继续显示 `initial`，不能推导长期可用性。
