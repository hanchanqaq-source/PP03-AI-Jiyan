# A2-W1 source Catalog — Phase 1 before / Phase 2 qualified after

## Scope and evidence boundary

Task 6's corrected audit-data point is
`08b4b72ab41be9c513824e118010e9ddcf617fb3`. Task 7 pre-fix qualification
code/tests base is `547b2a72c1312f6f853edc81e9b091d67cf91df2`. The Phase 2
Task 7 qualified implementation and fresh backend-test HEAD is
`957aa58aea051d8faf7a2cf4e2d2b477de369c31`. This documentation-only
traceability overlay does not supersede or relabel that tested implementation
SHA. Phase 1 started from
`9dfd8246290a04555ed1886ded92c39de9a001f4` and its reviewed end / Phase 2
start is `e4c0d930e80266dc8ebaacd2b26807690e830daa`.

Registration is not a connection claim. Catalog states (`configured`,
`unconfigured`, `catalog_only`, and `disabled`), optional-package availability,
and isolated live observations are separate facts. No fund, portfolio, user
note, credential, key, cookie, paid endpoint, enterprise service, or Phase 3
provider was read or used in this phase.

| Measure | Phase 1 | Task 7 qualified implementation (`957aa58`) | Meaning |
| --- | ---: | ---: | --- |
| RSS/Atom configurations | 108 | 108 | Preserved exactly; Task 6 made no parser/config migration. |
| Catalog families | 112 | 125 | 17 static families plus 108 publisher families; no holdings relation affects this count. |
| Catalog adapters | 114 | 128 | 20 static adapters plus 108 feed adapters. |
| Capabilities | 8 | 29 | 21 Phase 2 capabilities were added with stable IDs. |
| Feed-capability adapters | 108 | 108 | Publisher registration remains one family/adapter per configured feed identity. |
| Family state totals | not recorded here | 13 configured; 2 unconfigured; 1 disabled; 109 catalog-only | State is static Catalog configuration, not health. |
| Adapter state totals | not recorded here | 15 configured; 2 unconfigured; 2 disabled; 109 catalog-only | The 109 catalog-only adapters are 108 feeds plus IMF. |
| Registration fingerprint | `665d677dea1706a5c8ce0675a771de03d72f081f250e1a086dd3e40c82718fde` | `90eb099370e7054a04dfd0ce3983e88179961f2992c8071da86854022910d7b5` | Deterministic static registration digest; it remains holdings-independent. |
| Connected free Provider families | not measured | 1: `world_bank` | Connection means a successful bounded public request only. |

## Phase 2 registrations and boundaries

Phase 2 adds 13 static families, 14 static adapters, and 21 capabilities.
The 14 adapter registrations are BaoStock, Yahoo Finance/yfinance, SEC EDGAR,
seven official-evidence link entries (SSE, SZSE, CNInfo, HKEXnews, CSRC, fund
company, index company), World Bank, OECD, IMF, and GDELT. The lazy runtime
registry exposes only the seven executable Provider adapters: BaoStock,
yfinance, SEC EDGAR, World Bank, OECD, IMF, and GDELT. Official-link entries
are implemented as bounded connector/validation adapters; they were registered
but not treated as connected without a successful, permitted validation.

BaoStock is independent of Eastmoney/Tencent but does not replace CNInfo
industry evidence or official fund NAV. yfinance is disabled unless explicitly
used for `personal_research` and remains non-official. GDELT is collector /
candidate only; its routing cannot make it independent evidence. IMF remains
catalog-only. Catalog health does not promote official-evidence eligibility or
trusted-news admission.

## Corrected isolated news audit

Task 6's corrected matched v2 audit is authoritative for Phase 2 live-news
evidence. It used the unchanged 108-source configuration hash
`fbb2239676aa22d16a755780a80621a433b9ceb60f19499967440eac20c1f33e`,
8-second bounds, a 500,000-byte cap, 12 workers, TLS verification for HTTPS,
and no request for configured HTTP references. All data, cache, log and report
paths were task-local.

| Audit | Success | Partial | Failure | Evidence |
| --- | ---: | ---: | ---: | --- |
| Before v2 | 85 | 16 | 7 | `.tmp/acceptance/a2-w1-phase2-before/public-audit.json` |
| After v2 | 85 | 16 | 7 | `.tmp/acceptance/a2-w1-phase2-after/public-audit.json` |

The v1 audit files are superseded. The matched v2 runs have the same source-ID
set, configuration hash, limits, and aggregate result; this is not a claim of
network repair or a GDELT independent-evidence result.

## Repair result and unresolved observations

No news parser or configuration change was evidence-qualified: repaired 0,
updated 0, replaced 0, and disabled 0. Of the 23 degraded/failure observations,
21 remain `观察` pending a minimal RED fixture or same-publisher HTTPS/identity
proof; 动点科技 is `需要凭据` after its public 403; WSJ Markets is `需要许可证`
because a stale public feed does not establish fresh-content rights. The three
configured HTTP feeds are partial `insecure_transport` and were deliberately
not requested. TLS, DNS, redirect, 502, parse and empty-payload observations
remain visible rather than being converted into successful registration claims.

The companion [free provider matrix](free-provider-matrix.md) records the
individual Provider/connector state, while
[source repair decisions](source-repair-decisions.md) retains every news-source
decision row.
