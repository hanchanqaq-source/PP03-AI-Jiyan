# A2-W1 Phase 2 Task 6 — source-repair decisions

## Evidence boundary

This is a decision ledger, not a claim that a public publisher is currently
healthy. The isolated Before audit ran on 2026-08-19 with the unchanged
108-source configuration (`fbb2239676aa22d16a755780a80621a433b9ceb60f19499967440eac20c1f33e`),
8-second per-request timeout, 500,000-byte cap and 12 workers. HTTPS requests
used TLS verification; configured HTTP references were not requested and are
classified as insecure transport. It wrote only redacted public metadata to
`.tmp/acceptance/a2-w1-phase2-before/public-audit.json` while every data,
news-cache, report, log and acceptance path was explicitly scoped below that
directory. No portfolio, holdings, notes, credentials, formal cache or prior
health history was read.

`连续失败` and `最后成功` are deliberately `unknown` unless obtained in this
isolated run: a single fresh observation cannot establish either fact. Prior
A1/A1.1 material is historical context only and is not treated as current
truth. `官方替代` means a publisher-owned public feed or an official primary
source, not an unrelated media outlet. A redirect is retained as an
observation until publisher identity, HTTPS final URL and stable source-family
identity are independently proved.

| Source ID | Source | Current result | Consecutive failure | Last success | Unique content value | Official alternative | Repair cost | Conclusion |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `news:f9d263013a6a3f38` | arXiv cs.AI | failure / TLS / 0 items | unknown; one isolated TLS observation | unknown | primary research preprints | `export.arxiv.org` publisher feed remains configured; no verified same-publisher migration | low investigation, no safe code fix | 观察 |
| `news:587895b853ada36c` | SemiAnalysis | partial / redirect / 1 item | unknown; not a failure | observed in this run | semiconductor analysis | final `newsletter.semianalysis.com` requires identity validation before migration | low | 观察 |
| `news:f3bb9157e305e62b` | Robotics Business Review | partial / redirect / 1 item; final URL is HTTP | unknown; not a failure | observed in this run | robotics business coverage | no verified publisher-equivalent HTTPS feed | medium; HTTP final URL cannot be promoted | 观察 |
| `news:d6518a19715b1938` | Canary Media | partial / redirect / 1 item | unknown; not a failure | observed in this run | clean-energy reporting | final `canarymedia.com` needs identity check before migration | low | 观察 |
| `news:79d68f94d68b4c97` | 国际能源网 | failure / connection / 0 items | unknown; one isolated connection observation | unknown | China energy trade coverage | configured publisher feed only; no verified public same-publisher alternative | medium | 观察 |
| `news:6f1d8221e9a0d627` | Endpoints News | partial / redirect / 1 item | unknown; not a failure | observed in this run | biopharma trade coverage | final `endpoints.news` needs publisher-identity proof before migration | low | 观察 |
| `news:7c39ec31de57818b` | FierceBiotech | failure / empty_payload / HTTP 200 | unknown; one isolated empty-payload observation | unknown | biotech industry reporting | configured publisher RSS only; no verified replacement feed | medium; parser change requires public fixture and RED | 观察 |
| `news:eb1e1cb662ddcc39` | FiercePharma | failure / empty_payload / HTTP 200 | unknown; one isolated empty-payload observation | unknown | pharmaceutical industry reporting | configured publisher RSS only; no verified replacement feed | medium; parser change requires public fixture and RED | 观察 |
| `news:c38a8cf77ae3fbe6` | Space.com | partial / redirect / 1 item | unknown; not a failure | observed in this run | space industry reporting | final `space.com/feeds.xml` needs identity validation before migration | low | 观察 |
| `news:7a3eb02aedca459a` | 36氪 | failure / parse / 0 items | unknown; one isolated parse observation | unknown | Chinese technology business coverage | configured `36kr.com` feed only; no verified same-publisher feed migration | medium; requires redacted public fixture plus named RED | 观察 |
| `news:e9780af8b41b4a97` | 钛媒体 | success / HTTP 200 / 1 item | not applicable; healthy in this isolated observation | observed in this run | Chinese technology business coverage | configured publisher feed is already the observed public endpoint | no repair needed | 观察 |
| `news:6b195fa321259338` | 虎嗅 | failure / TLS / 0 items | unknown; one isolated TLS observation | unknown | Chinese technology business coverage | configured publisher feed only; no verified migration | low investigation, never disable TLS | 观察 |
| `news:3fb71517e26dc86e` | 动点科技 | failure / HTTP 403 / 0 items | unknown; one isolated public-access observation | unknown | Chinese technology/startup coverage | configured `cn.technode.com` feed only; no credentialless verified alternative | medium; credentials/cookies are prohibited | 需要凭据 |
| `news:4ec6e685d9c8fe08` | 东方财富股票 | partial / insecure_transport / 0 items | unknown; HTTP was not requested | unknown | China market-news feed | no verified same-publisher HTTPS migration | medium; HTTPS identity proof required | 观察 |
| `news:ea5cf31f8cf75d8a` | 东方财富资讯 | partial / insecure_transport / 0 items | unknown; HTTP was not requested | unknown | China market-news feed | no verified same-publisher HTTPS migration | medium; HTTPS identity proof required | 观察 |
| `news:6a2e186b891ab2cb` | 经济观察网 | partial / insecure_transport / 0 items | unknown; HTTP was not requested | unknown | China macro/business reporting | no verified same-publisher HTTPS migration | medium; HTTPS identity proof required | 观察 |
| `news:8a479d79f4e2ffb4` | 白鲸出海 | partial / redirect / 1 item | unknown; not a failure | observed in this run | Chinese outbound-tech coverage | final `baijing.cn` needs identity validation before migration | low | 观察 |
| `news:39177cbe7653d141` | DPReview | partial / redirect / 1 item | unknown; not a failure | observed in this run | imaging/consumer technology coverage | final `dpreview.com/feed` needs identity validation before migration | low | 观察 |
| `news:4cd1631448626c47` | Financial Times | partial / redirect / 1 item | unknown; not a failure | observed in this run | global macro/business reporting | final FT public RSS route needs identity validation before migration | low | 观察 |
| `news:f866188ff19975be` | WSJ Markets | partial / stale_data / HTTP 200, latest 2025-01-28 | unknown; not a failure | latest visible item is stale, not a success within freshness policy | US market news | publisher feed remains configured; no verified fresh public same-publisher feed | medium; license/paywall boundary must be preserved | 需要许可证 |
| `news:46f90d3daa6d5418` | MarketWatch | partial / redirect / 1 item | unknown; not a failure | observed in this run | market news | final Dow Jones public feed needs identity validation before migration | low | 观察 |
| `news:af3c7261885dccd1` | Quanta Magazine | partial / redirect / 1 item | unknown; not a failure | observed in this run | science reporting | final `quantamagazine.org` needs identity validation before migration | low | 观察 |
| `news:a6e9ca8efe07139b` | New Scientist | partial / redirect / 1 item | unknown; not a failure | observed in this run | science reporting | final `newscientist.com/feed` needs identity validation before migration | low | 观察 |
| `news:7fec20f7d954a1ce` | Live Science | partial / redirect / 1 item | unknown; not a failure | observed in this run | science reporting | final `livescience.com/feeds.xml` needs identity validation before migration | low | 观察 |

## Decision outcome

No `news_sources.json` or `newsradar.py` change is warranted from this audit.
There is no minimal public parser fixture and named RED proving a parser repair,
no verified same-publisher HTTPS migration, and no one-in/one-out ordinary-media
replacement that has been validated before disabling an existing source. A
single failure therefore caused neither deletion nor TLS relaxation. Network
conditions, not code, may explain variation in a later matched After audit and
will be reported separately.
