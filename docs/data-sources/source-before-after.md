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

## Phase 3 Catalog overlay

Phase 3 begins at reviewed Phase 2 end `8f830f4a82b4844103e78e54dd9cc004ff94edce`
and is qualified after the separate credential-URL security fix at
`0f04e493d3fb071c105cd2f9ac78b3665369637f`. The runtime query used the unchanged
108-source `backend/news_sources.json`; it did not read holdings or user data.

| Measure | Phase 2 qualified | Phase 3 runtime Catalog | Phase 3 change |
| --- | ---: | ---: | ---: |
| RSS/Atom configurations | 108 | 108 | 0 |
| Catalog families | 125 | 147 | +22 |
| Catalog adapters | 128 | 150 | +22 |
| Capabilities | 29 | 30 | +1 |
| Feed-capability adapters | 108 | 108 | 0 |
| Registration fingerprint | `90eb099370e7054a04dfd0ce3983e88179961f2992c8071da86854022910d7b5` | `b51d725f8ea653b54fb6ac39bea47e29fa592549cc5a07805087121726ed6f79` | Static Catalog change only |

Phase 3 adds 2 `free_key`, 6 `freemium` (including Tushare), 5 `paid_api`,
and 9 `enterprise_license` adapters. The full adapter billing totals are 128
`free_no_key`, 2 `free_key`, 6 `freemium`, 5 `paid_api`, and 9
`enterprise_license`.

| Static Catalog state | Families | Adapters | Connection meaning |
| --- | ---: | ---: | --- |
| `configured` | 13 | 15 | Configuration exists; not a Phase 3 Live connection claim |
| `unconfigured` | 15 | 15 | Credentialed Adapter is registered but lacks a usable configured boundary |
| `license_required` | 9 | 9 | Enterprise static shell; no SDK, health, or connection |
| `catalog_only` | 109 | 109 | Includes the unchanged 108 feed registrations and IMF |
| `disabled` | 1 | 2 | Default-disabled Catalog boundary |

The fresh isolated no-key qualification probed 13 credentialed adapters and 17
capabilities and made zero network calls; all were `unconfigured` and none was
a health failure. Connected Phase 3 credentialed, paid, and enterprise
providers: 0. This overlay does not overwrite the authoritative Phase 2
108-source audit (`85 success / 16 partial / 7 failure` before and after), does
not claim a news-source repair, and does not relabel any parser fixture as Live.

Detailed boundaries: [credentialed/freemium matrix](freemium-provider-matrix.md),
[paid matrix](paid-provider-matrix.md), and
[enterprise Catalog](enterprise-provider-catalog.md).

## Phase 4 final 26-measure qualification

This section is the final Task 7 measurement overlay at
`09c4145334b4502dde55185b8b72653c1e53d2f9`. It keeps every earlier phase
table above as history. The original Task 7 network run remains a failed Live
observation; the archive value after the compatibility fix comes from a new
isolated **offline** archive of that same saved snapshot, not from a Live rerun.

### Reproducible measurement commands

All commands below were run from the repository root. They are read-only except
for the separately identified offline archive producer `A1`, whose output is
confined to the preserved Task 7 diagnostic root.

`C1` — current deterministic Catalog and holdings-independence measurement,
exit 0 at `2026-08-24T15:10:00.635911+00:00`:

```powershell
$env:PYTHONPATH=(Resolve-Path 'backend').Path
backend\.venv\Scripts\python.exe -c "from datetime import datetime,timezone; from data_sources.catalog import build_catalog; from source_health.registry import load_news_config; c=build_catalog(news_config=load_news_config()); f0=c.registration_fingerprint(holding_ids=[]); f1=c.registration_fingerprint(holding_ids=['017811']); feed=sum('feed' in x.capability_ids for x in c.adapters); print('measured_at='+datetime.now(timezone.utc).isoformat()); print('families='+str(len(c.families))); print('adapters='+str(len(c.adapters))); print('capabilities='+str(len(c.capabilities))); print('eastmoney_entries_before='+str(len(c.adapters_for_family('eastmoney')))); print('eastmoney_families_after='+str(sum(x.source_family_id=='eastmoney' for x in c.families))); print('visible_news_without_funds='+str(feed)); print('visible_news_with_fund='+str(feed)); print('fingerprint_without='+f0); print('fingerprint_with='+f1); print('registration_equal='+str(f0==f1).lower()); print('unconfigured_key='+str(sum(x.catalog_status.value=='unconfigured' and bool(x.credential_env_names) for x in c.adapters))); print('license_required='+str(sum(x.catalog_status.value=='license_required' for x in c.adapters)))"
```

`C1` returned fingerprint
`b51d725f8ea653b54fb6ac39bea47e29fa592549cc5a07805087121726ed6f79`
for both holding sets. The same Catalog was captured in Live artifact `L1`
with `backend/news_sources.json` SHA-256
`2d53b3d7064688823a0fdd41c1bbd29c0d67f40c072ea6975ae4226015779f7a`.

`R1` — matched Phase 2 audit comparison, exit 0 at
`2026-08-24T15:10:15.3521238Z`:

```powershell
$b=Get-Content -LiteralPath '.tmp\acceptance\a2-w1-phase2-before\public-audit.json' -Raw|ConvertFrom-Json
$a=Get-Content -LiteralPath '.tmp\acceptance\a2-w1-phase2-after\public-audit.json' -Raw|ConvertFrom-Json
$bm=@{}; $b.news|ForEach-Object{$bm[$_.source_id]=$_}
$am=@{}; $a.news|ForEach-Object{$am[$_.source_id]=$_}
$degraded=@($b.news|Where-Object{$_.status-ne'success'})
$repaired=@($degraded|Where-Object{$am[$_.source_id].status-eq'success'})
$updated=@($b.news|Where-Object{$am.ContainsKey($_.source_id)-and(($am[$_.source_id].configuration_hash-ne$_.configuration_hash)-or($am[$_.source_id].source_reference-ne$_.source_reference))})
$added=@($a.news|Where-Object{-not $bm.ContainsKey($_.source_id)})
$removed=@($b.news|Where-Object{-not $am.ContainsKey($_.source_id)})
"failures_before_repair=$($degraded.Count) successfully_repaired=$($repaired.Count) official_entries_updated=$($updated.Count) alternatives_added=$($added.Count) old_sources_disabled=$($removed.Count) unresolved=$($degraded.Count-$repaired.Count)"
```

The exact input snapshots are schema
`pp03-a2-w1-task6-public-audit-v2`, observed at
`2026-08-19T18:17:13+08:00` and `2026-08-19T18:17:57+08:00`, with identical
108-source hash
`fbb2239676aa22d16a755780a80621a433b9ceb60f19499967440eac20c1f33e`.
`R2` parsed only the 24 decision-table rows in
`source-repair-decisions.md` at `2026-08-24T15:10:22.4996562Z`, excluded its
one `success` observation, and grouped the remaining conclusion column:

```powershell
$rows=Get-Content -LiteralPath 'docs\data-sources\source-repair-decisions.md'|Where-Object{$_ -match '^\| `news:'}|ForEach-Object{$p=$_ -split '\|'; [pscustomobject]@{Result=$p[3].Trim();Conclusion=$p[9].Trim()}}
$unresolved=@($rows|Where-Object{$_.Result -notmatch '^success'})
$unresolved|Group-Object Conclusion|Sort-Object Name|ForEach-Object{"reason[$($_.Name)]=$($_.Count)"}
```

`P1` — preserved Task 7 Live result reader, exit 0 at
`2026-08-24T15:10:30.2863989Z`:

```powershell
$d=Get-Content '.tmp\acceptance\a2-w1\live-data\outputs\live-qualification.json' -Raw|ConvertFrom-Json
$d.pipeline.counts; $d.recovery
```

`L1` is `pp03-task7-live-qualification-v1`, finished at
`2026-08-24T20:46:46.912056+08:00`, with run
`7fe1e552fd254ee98c6a221e66a31fec`, raw snapshot
`87663551b9404a57bd1b5eb7f6f03dc3`, evidence snapshot
`9cbcceb192697d2f7ce9`, and output SHA-256
`B868BB66DF7011A29830687D234D1CB2D1D5AF9A9269D5F3536A080316C6C1B5`.

`A1` — repair-qualified offline archive producer and state measurement:

```powershell
backend\.venv\Scripts\python.exe .tmp\acceptance\a2-w1\live-data\diagnostics\archive-fix-offline\run.py
$s=Get-Content '.tmp\acceptance\a2-w1\live-data\diagnostics\archive-fix-offline\actual\archive\state.json' -Raw|ConvertFrom-Json
@($s.target_index.PSObject.Properties).Count
```

The isolated schema-v4 state finalized at
`2026-08-24T14:55:26.4354283Z`, generation 2, phase `finalized`, with 8 date
buckets and 507 indexed events. First write and exact retry both returned 507;
all 12 legal overlaps, key-field triples, raw/content digests and history were
checked, while genuine ambiguity was rejected with zero writes.

### Exact measures

| No. | Required measure | Qualified value | Measurement command / snapshot / time | Meaning |
| ---: | --- | ---: | --- | --- |
| 1 | Family count | **147** | `C1`; fingerprint `b51d725f...`; `2026-08-24T15:10:00.635911+00:00` | Static Catalog families, not connected families. |
| 2 | Adapter count | **150** | `C1`; fingerprint `b51d725f...`; `2026-08-24T15:10:00.635911+00:00` | Stable Adapter registrations. |
| 3 | Capability count | **30** | `C1`; fingerprint `b51d725f...`; `2026-08-24T15:10:00.635911+00:00` | Stable capability registrations. |
| 4 | Eastmoney entries before aggregation | **3** | `C1`; fingerprint `b51d725f...`; `2026-08-24T15:10:00.635911+00:00` | Direct, AKShare and efinance access paths. |
| 5 | Eastmoney families after aggregation | **1** | `C1`; fingerprint `b51d725f...`; `2026-08-24T15:10:00.635911+00:00` | Three paths do not add source independence. |
| 6 | Visible sources without funds | **108** | `C1`; fingerprint `b51d725f...`, `holding_ids=[]`; `2026-08-24T15:10:00.635911+00:00` | All registered feed Adapters remain visible. |
| 7 | Visible sources with a fund | **108** | `C1`; fingerprint `b51d725f...`, `holding_ids=['017811']`; `2026-08-24T15:10:00.635911+00:00` | Adding a fund changes only relation overlays. |
| 8 | Registration equality | **true** | `C1`; both fingerprints `b51d725f...`; `2026-08-24T15:10:00.635911+00:00` | Exact registration equality before/after the sample fund relation. |
| 9 | Failures before repair | **23** | `R1`; Before snapshot `2026-08-19T18:17:13+08:00`; measured `2026-08-24T15:10:15.3521238Z` | 16 partial + 7 failure observations; “failure” here means the repair workload. |
| 10 | Successfully repaired | **0** | `R1`; After `2026-08-19T18:17:57+08:00`; measured `2026-08-24T15:10:15.3521238Z` | No degraded row transitioned to success under a qualified config/parser change. |
| 11 | Official entries updated | **0** | `R1`; matched snapshots `2026-08-19T18:17:13+08:00` / `18:17:57+08:00`; measured `2026-08-24T15:10:15.3521238Z` | No official endpoint migration was proven. |
| 12 | Alternatives added | **0** | `R1`; matched snapshots `2026-08-19T18:17:13+08:00` / `18:17:57+08:00`; measured `2026-08-24T15:10:15.3521238Z` | No unqualified replacement was inserted. |
| 13 | Old sources disabled | **0** | `R1`; matched snapshots `2026-08-19T18:17:13+08:00` / `18:17:57+08:00`; measured `2026-08-24T15:10:15.3521238Z` | Single failures did not trigger deletion. |
| 14 | Unresolved | **23** | `R1`; matched snapshots `2026-08-19T18:17:13+08:00` / `18:17:57+08:00`; measured `2026-08-24T15:10:15.3521238Z` | Every degraded/failure decision remains explicit. |
| 15 | Unresolved reasons | **21 observation + 1 credential + 1 license = 23** | `R2`; 24-row decision ledger; `2026-08-24T15:10:22.4996562Z` | The one successful ledger row is not unresolved. |
| 16 | Unconfigured-Key count | **13** | `C1` + `L1` Catalog snapshot; credential-named and `unconfigured`; `2026-08-24T15:10:00.635911+00:00` | 2 free-Key + 6 freemium + 5 paid; excludes 2 no-Key connector-only entries from the Catalog's total 15 unconfigured. |
| 17 | License-required count | **9** | `C1`; fingerprint `b51d725f...`; `2026-08-24T15:10:00.635911+00:00` | Enterprise static shells; none connected. |
| 18 | Raw news count | **507** | `P1` / `L1`; raw snapshot `876635...`; finished `2026-08-24T20:46:46.912056+08:00` | Durable raw events from the preserved Live run. |
| 19 | Verified count | **28** | `P1` / `L1`; evidence snapshot `9cbc...`; `2026-08-24T20:46:46.912056+08:00` | Eligible for trusted projection only after all durability gates. |
| 20 | Corroborated count | **0** | `P1` / `L1`; evidence snapshot `9cbc...`; `2026-08-24T20:46:46.912056+08:00` | No event met corroborated status. |
| 21 | Pending count | **467** | `P1` / `L1`; evidence snapshot `9cbc...`; `2026-08-24T20:46:46.912056+08:00` | Remains Evidence Center/archive only. |
| 22 | Conflicting count | **12** | `P1` / `L1`; evidence snapshot `9cbc...`; `2026-08-24T20:46:46.912056+08:00` | Preserved as conflict, never trusted admission. |
| 23 | Archived event count | **507** | `A1`; same saved `9cbc...` snapshot; finalized `2026-08-24T14:55:26.4354283Z` | Current post-fix offline qualification: first write 507 and exact retry 507. Historical pre-fix Live value was **0** from `P1` because it terminated `evidence_compatibility_failed`; no network rerun occurred. |
| 24 | Cache-recovered count | **0** | `P1` / `L1` recovery import; `2026-08-24T20:46:46.912056+08:00` | Explicit Live recovery result, not inferred from the offline archive. |
| 25 | Public-refetched count | **0** | `P1` / `L1` recovery import; `2026-08-24T20:46:46.912056+08:00` | No public history refetch occurred. |
| 26 | Unrecoverable-history count | **0** | `P1` / `L1` recovery import; `2026-08-24T20:46:46.912056+08:00` | Explicit zero for that empty scan/import boundary, not proof that arbitrary missing history is recoverable. |

The preserved Live run stopped at durable `evidence_saved` with
`failed / evidence_compatibility_failed`, trusted snapshot `null`, and trusted
event count 0. The repaired offline archive result closes the specific archive
compatibility blocker for the saved evidence but does not relabel the failed
Live run, provider timeouts, optional dependency states, or empty recovery as a
successful end-to-end network publication.
