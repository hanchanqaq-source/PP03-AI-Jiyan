# A1 数据源健康 Before 审计

审计时间：`2026-08-17T21:55:36.858847+00:00` 至 `2026-08-17T21:56:08.471342+00:00`；基线 HEAD：`7e0d68d173b3b87d793116a0b8d75d0c5932fd8a`；
隔离根：`D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1`。本次只访问已登记的公开入口，未读取真实用户数据。

## 命令与环境

```powershell
$env:VR_DATA_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1\data"
$env:VR_NEWS_CACHE_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1\news"
$env:VR_ACCEPTANCE_DIR="D:\AI_Workspace\AI_Projects\PP03-AI-Jiyan\.tmp\acceptance\source-health-a1"
$env:VR_SOURCE_HEALTH_STARTUP="0"
```

`backend\.venv\Scripts\python.exe` 导入 `SourceHealthService`，执行
`start_run("full")`，按 1 秒轮询 `get_run(run_id)` 至 `completed`，随后将
`current-snapshot.json` 复制为隔离目录中的 before/after 快照。该命令未启动应用服务、未读取用户目录、未使用缓存。

输入固定证据：

- `backend/news_sources.json` SHA-256：`2D53B3D7064688823A0FDD41C1BBD29C0D67F40C072EA6975AE4226015779F7A`
- `backend/source_health/source-health-samples.json` SHA-256：`C869FD5308247B14EC4FAD4FC00162246B14C65CA573EF87914659658CF3C2D7`
- 注册 source_id：119 个；After 集合与 Before 完全相等

## 汇总

| 指标 | Before |
|---|---:|
| 实际注册／实际完成 | 119 / 119 |
| 基金、行情、行业能力 | 11 |
| 资讯配置 | 108 |
| 成功／部分成功／失败 | 98 / 13 / 8 |
| P50 / P95 | 291 ms / 2287 ms |
| 平均字段完整率 | 82.35% |
| 有日期的新鲜度样本 | 108 / 119 |
| 新鲜度 min / P50 / max | 280s / 5.93h / 1844.91d |
| 跟随到最终地址 | 12 |
| 经独立链路确认的 301/308 | 12 / 0 |
| 五字段完全重复配置 | 0 |
| 总耗时 | 33.077 s |
| 评级置信度 | `initial` |

错误分布：`authentication=1`、`empty_payload=2`、`http=1`、`none=98`、`parse=1`、`redirect=12`、`stale_data=1`、`timeout=1`、`tls=2`。

初始评级分布：`degraded=13`、`failed=8`、`healthy=98`。绿灯仅表示该次探测结果；不代表许可证、长期稳定性或生产集成通过。

## 非成功明细

| source_id | 来源 | 状态 | error | HTTP | 条数 | 日期 | 完整率 | 延迟 | 修复价值 |
|---|---|---:|---|---:|---:|---|---:|---:|---|
| `news:0063c85f43e60076` | 国际能源网 | failure | `timeout` | — | 0 | — | 0.00% | 30973 ms | `observe` |
| `news:44726681bff6e16e` | 动点科技 | failure | `authentication` | 403 | 0 | — | 0.00% | 165 ms | `replace_candidate` |
| `news:46c5aa60dc040dba` | 36氪 | failure | `parse` | — | 0 | — | 0.00% | 1049 ms | `none` |
| `news:708f88de6b0fa442` | FierceBiotech | failure | `empty_payload` | 200 | 0 | — | 0.00% | 165 ms | `none` |
| `news:772f466c8c0bc84f` | 虎嗅 | failure | `tls` | — | 0 | — | 0.00% | 441 ms | `observe` |
| `news:df3335a4a80a661d` | arXiv cs.AI | failure | `tls` | — | 0 | — | 0.00% | 160 ms | `observe` |
| `news:f4c0e57e06463c06` | FiercePharma | failure | `empty_payload` | 200 | 0 | — | 0.00% | 203 ms | `none` |
| `quote:eastmoney-direct:stock_snapshot` | eastmoney-direct | failure | `http` | 502 | 0 | — | 0.00% | 2094 ms | `observe` |
| `news:3017e8411834b32a` | New Scientist | partial | `redirect` | 200 | 2 | 2026-08-17T23:48:20+08:00 | 0.00% | 598 ms | `none` |
| `news:3443a9fa0e028f34` | Financial Times | partial | `redirect` | 200 | 2 | 2026-08-18T01:06:13+08:00 | 0.00% | 477 ms | `none` |
| `news:37085a92d14c67c4` | Quanta Magazine | partial | `redirect` | 200 | 2 | 2026-08-17T23:11:17+08:00 | 0.00% | 534 ms | `none` |
| `news:54bb6004ad8b405b` | Endpoints News | partial | `redirect` | 200 | 2 | 2026-08-18T04:20:28+08:00 | 0.00% | 292 ms | `none` |
| `news:57bf88dafb0d5ce8` | Space.com | partial | `redirect` | 200 | 2 | 2026-08-18T05:00:00+08:00 | 0.00% | 214 ms | `none` |
| `news:68779a6ad6e56d73` | SemiAnalysis | partial | `redirect` | 200 | 2 | 2026-08-17T06:27:59+08:00 | 0.00% | 430 ms | `none` |
| `news:6e3127956b63d8c3` | Robotics Business Review | partial | `redirect` | 200 | 2 | 2026-08-18T05:15:46+08:00 | 0.00% | 319 ms | `none` |
| `news:6ee1acdae59fe508` | Live Science | partial | `redirect` | 200 | 2 | 2026-08-18T05:30:15+08:00 | 0.00% | 271 ms | `none` |
| `news:77eba0e4610108d3` | 白鲸出海 | partial | `redirect` | 200 | 2 | 2026-08-13T16:17:54+08:00 | 0.00% | 3753 ms | `none` |
| `news:82d0ae94ec76adac` | DPReview | partial | `redirect` | 200 | 2 | 2026-08-18T02:18:53+08:00 | 0.00% | 282 ms | `none` |
| `news:905d4f7cc34ee856` | Canary Media | partial | `redirect` | 200 | 2 | 2026-08-17T15:30:00+08:00 | 0.00% | 392 ms | `none` |
| `news:a85c2e4387773228` | WSJ Markets | partial | `stale_data` | 200 | 0 | 2025-01-28T03:26:00+08:00 | 0.00% | 172 ms | `none` |
| `news:e15e8e0612acee25` | MarketWatch | partial | `redirect` | 200 | 2 | 2026-08-18T05:30:00+08:00 | 0.00% | 528 ms | `none` |

## 永久跳转核验

对 Before 的 12 个重定向源，使用 `requests.get(..., allow_redirects=True, stream=True, timeout=15)`，仅发送
`User-Agent`、`Accept`、`Accept-Encoding` 三个标准头，逐项保存状态链；12 个均观察到 301，0 个观察到 308。
其中存在跨域、HTTPS 降级、最终 403 等差异，因此“观察到永久跳转”不自动等于“满足可改 URL 的全部硬条件”。

## 结论

Before 有 12 个 RSS 已解析出条目却被记录为 0% 完整率，且最终公开地址没有进入 `final_reference`。
这是健康诊断契约错配；其余 TLS、403、502、超时、连接、空载荷、陈旧数据和解析失败均保持真实失败／部分成功，未伪造 PASS。
