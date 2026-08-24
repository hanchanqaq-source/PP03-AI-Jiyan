# A2-W1 Phase 2 Task 7 — free Provider and connector matrix

Task 6's corrected audit-data point is
`08b4b72ab41be9c513824e118010e9ddcf617fb3`. Task 7 pre-fix qualification
code/tests base is `547b2a72c1312f6f853edc81e9b091d67cf91df2`. The Phase 2
Task 7 qualified implementation and fresh backend-test HEAD is
`957aa58aea051d8faf7a2cf4e2d2b477de369c31`. This documentation-only
traceability overlay does not supersede or relabel that tested implementation
SHA. `registered` / Catalog state, connector implementation, optional
dependency availability, and `connected` are deliberately separate. Connected
means one successful bounded public request; it is neither finance truth nor
evidence admission.

| Adapter | Family | Catalog state | Task 6 observation | Connected | Qualification boundary |
| --- | --- | --- | --- | --- | --- |
| `baostock` | `baostock` | configured | dependency-missing | no | Optional package was absent; no installation or unbounded login occurred. |
| `yahoo-finance` | `yahoo_finance` | disabled | dependency-missing | no | Non-official, personal-research-only and not enabled. |
| `sec-edgar` | `sec_edgar` | configured | `unconfigured_contact` | no | Public contact was not fabricated; this is a barrier, not an adapter failure. |
| `world-bank` | `world_bank` | configured | success | yes | The one connected free Provider; empty official unit is `unknown` and missing numeric values stay missing. |
| `oecd` | `oecd` | configured | `http_client_error` | no | Bounded public request only; no fallback scraping. |
| `imf` | `imf` | catalog-only | catalog-only | no | No live request under this phase; documented interface validation remains pending. |
| `gdelt` | `gdelt` | configured | timeout | no | Collector/candidate only; never independent-evidence eligible. |
| `sse-official-evidence` | `sse` | configured | unexamined connector | no | Bounded official-link connector is registered; no permitted Task 6 validation was run. |
| `szse-official-evidence` | `szse` | configured | unexamined connector | no | Bounded official-link connector is registered; no permitted Task 6 validation was run. |
| `cninfo-official-evidence` | `cninfo` | configured | unexamined connector | no | Bounded official-link connector is registered; no permitted Task 6 validation was run. |
| `hkexnews-official-evidence` | `hkexnews` | configured | unexamined connector | no | Bounded official-link connector is registered; no permitted Task 6 validation was run. |
| `csrc-official-evidence` | `csrc` | configured | unexamined connector | no | Bounded official-link connector is registered; no permitted Task 6 validation was run. |
| `fund-company-official-evidence` | `fund_company_official` | unconfigured | unconfigured | no | No allowlisted company host is registered; no request is allowed. |
| `index-company-official-evidence` | `index_company_official` | unconfigured | unconfigured | no | No allowlisted index-company host is registered; no request is allowed. |

Only seven adapters are lazy runtime ProviderRegistry entries (`baostock`,
`yahoo-finance`, `sec-edgar`, `world-bank`, `oecd`, `imf`, and `gdelt`). The
official-evidence rows are separate registered validation connectors; their
Catalog presence does not assert health or connection.

## News audit / repair boundary

The corrected matched v2 news audits use the unchanged 108-source configuration
and hash `fbb2239676aa22d16a755780a80621a433b9ceb60f19499967440eac20c1f33e`:
Before = 85 success / 16 partial / 7 failure; After = 85 / 16 / 7. The audit
was bounded (8 seconds, 500,000 bytes, 12 workers), TLS-verified for HTTPS,
and did not request configured HTTP references.

Repairs, parser/config updates, replacements, and disables are all 0. The 23
degraded/failure source observations are unresolved by design: 21 `观察`, one
`需要凭据` (动点科技 403), and one `需要许可证` (WSJ Markets stale public feed).
The three HTTP feeds remain partial `insecure_transport`, not connected. This
matrix does not claim all sources fixed, any optional package installed, SEC
configured, IMF connected, or GDELT independent evidence.

## Phase 4 Task 7 Live observation

The Phase 2 matrix above remains historical. The current bounded observation
used only free/no-Key public paths at starting HEAD `57fd3384` and ran from
`2026-08-24T20:45:56.372963+08:00` to
`2026-08-24T20:46:46.912056+08:00`. The archive compatibility repair at
`09c4145` did not rerun providers or network requests.

| Adapter | Current Catalog boundary | Task 7 status | Connected | Duration / rows | Qualification |
| --- | --- | --- | --- | --- | --- |
| `baostock` | configured Provider | `optional_dependency_unavailable` | no | 2 ms / 0 | Optional package was not installed. |
| `yahoo-finance` | disabled Provider | `optional_dependency_unavailable` | no | 1 ms / 0 | Personal-research-only and still default-off. |
| `imf` | catalog-only Provider | `catalog_only` | no | 1 ms / 0 | No public request was made. |
| `sec-edgar` | configured Provider with contact gate | `unconfigured_contact` | no | 371 ms / 0 | A truthful public contact was not fabricated. |
| `world-bank` | configured Provider | `success` | yes | 1336 ms / 2 | The only connected formal free Provider in this window. |
| `oecd` | configured Provider | `http_client_error` | no | 1765 ms / 0 | Bounded request failure; no alternate scraping. |
| `gdelt` | configured candidate collector | `timeout` | no | 8255 ms / 0 | Discovery only; never independent evidence. |

The SSE, SZSE, CNInfo, HKEXnews and CSRC official-link Adapters remain
registered connector-only entries without a Task 7 connection observation.
The fund-company and index-company official connectors remain unconfigured.
They are neither Provider failures nor connected official evidence.

Two configured-news passes over all 108 source IDs used configuration SHA-256
`2d53b3d7064688823a0fdd41c1bbd29c0d67f40c072ea6975ae4226015779f7a`.
At `2026-08-24T20:46:27.375149+08:00`, results were 85 success / 17
partial / 6 failure (99 connected). At
`2026-08-24T20:46:36.342191+08:00`, results were 84 / 17 / 7 (98 connected).
`news:44726681bff6e16e` alone changed from `success/none` to
`failure/authentication`; no repair or disable is inferred from that transient
variance. Publisher-feed accessibility also does not establish that any
particular event is verified.
