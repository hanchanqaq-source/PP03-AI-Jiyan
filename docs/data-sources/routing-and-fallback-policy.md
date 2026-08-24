# A2-W1 source routing and fallback policy

## Qualification boundary

This is the qualified routing contract at `09c4145334b4502dde55185b8b72653c1e53d2f9`.
Catalog registration, runtime configuration, connector implementation, one
bounded connection observation, and evidence admission are separate facts.
The preserved Task 7 Live observation ran at starting HEAD `57fd3384` from
`2026-08-24T20:45:56.372963+08:00` through
`2026-08-24T20:46:46.912056+08:00`; the archive compatibility repair was
qualified offline and was not represented as a second Live run.

| State | Routing meaning |
| --- | --- |
| `registered` / `configured` | A stable family, Adapter and capability route exists. It does not assert a current connection. |
| `connected` | The named Adapter completed a bounded permitted request in the recorded observation window only. |
| connector-only | A validation/link connector is registered, but no executable Provider health request established a connection. |
| `unconfigured` | A required Key/contact/allowlist boundary is absent. It is skipped before transport and is not a health failure. |
| `license_required` | A static enterprise shell cannot route until an explicit license and integration review exist. |
| `catalog_only` | Discoverable registration without a qualified runtime connection path. |
| `disabled` | Default-off registration; not a failed health observation. |

## Capability-first selection

1. Select by stable `capability_id`, then by the Catalog's primary, fallback,
   and cross-check family lists. Display names, fund holdings and health labels
   never create identity or change registration.
2. A route is eligible only when its Adapter is enabled, the capability is
   supported, configuration/credential/license gates pass, the usage mode is
   allowed, and exact Free-only and budget checks pass before transport.
3. A primary transport failure may advance only to a declared fallback for the
   same capability. It does not convert the fallback into an official source,
   an independent source, or a successful primary observation.
4. Unknown plan, entitlement, cost, credential, license or authentication
   transport facts fail closed. There is no automatic purchase, plan upgrade,
   package installation, credential invention, paywall bypass or TLS disable.
5. Health is capability-local. An unexamined, failed or blocked capability does
   not contaminate a different capability, and one successful capability does
   not mark the entire family connected.

## Family and evidence independence

- `eastmoney-direct`, `akshare-eastmoney` and `efinance-eastmoney` are three
  access paths in one `eastmoney` family. AKShare/efinance fallback therefore
  adds no source independence.
- Tencent, CNInfo, Danjuan, BaoStock and other distinct Catalog families retain
  their own identities, but independence eligibility still depends on the
  requested evidence role and capability.
- GDELT, Finnhub and NewsAPI are collectors/candidate discovery paths. RSSHub,
  aggregation, syndication or a collector does not replace the original
  publisher identity and cannot supply an independent corroboration by itself.
- `content_source` plus `origin_cluster`, not Adapter count or copied URLs,
  controls publisher independence for news verification.
- Fund holdings, official fund industry allocation, and official stock
  industry classification are separate evidence layers. A fallback quote or
  news source cannot replace CNInfo classification or disclosed fund evidence.

## Current capability routes

| Capability group | Primary | Declared fallback / cross-check | Boundary |
| --- | --- | --- | --- |
| fund search/profile/NAV/holdings | Eastmoney | Danjuan fallback | Same capability only; no inferred holdings or NAV. |
| official fund industry allocation | Eastmoney | none | Must retain provider disclosure and as-of provenance. |
| stock snapshot | Tencent | Eastmoney, Finnhub fallback | Finnhub remains unconfigured and non-official. |
| stock history/financial/calendar | BaoStock | capability-specific credentialed fallbacks | Task 7 observed BaoStock's optional dependency unavailable; no fallback was promoted. |
| stock industry classification | CNInfo | none | Kept separate from fund allocation and media-derived industry tags. |
| macro indicator/series | World Bank / OECD | IMF, FRED, EIA, Nasdaq Data Link as declared | Task 7 connected World Bank only; other current states remain explicit. |
| news discovery | none | GDELT, Finnhub, NewsAPI candidates | Candidate collection is never trusted admission. |
| publisher feed | each original publisher family | no cross-publisher silent replacement | Same-publisher migration requires public identity and HTTPS proof. |

## News pipeline and fallback behavior

Collection durably saves one raw snapshot before deterministic verification of
that exact snapshot. Only `verified` and `corroborated` events may enter the
trusted market-news projection. `pending`, `conflicting`, `corrected` and
`disproved` events remain visible in Evidence Center/archive under their actual
statuses; they are never silently upgraded by a source-health result.

During queued, fetching, verifying, interrupted or failed runs, the previous
trusted pointer remains authoritative. A raw or evidence persistence failure
does not publish a partial trusted snapshot. Recovery may use only allowlisted
legacy radar/evidence paths or an event's existing public link, counts cache
recovery and public refetch separately, and never fabricates missing identity,
time, status or provenance.

The original Task 7 Live run reached durable `evidence_saved` and then failed
with `evidence_compatibility_failed`; it therefore published no trusted
snapshot. The repair at `09c4145` qualified the same saved 507-event evidence
snapshot through an isolated offline archive first write and exact retry. That
proves archive compatibility for the saved evidence, not a new network result
or a successful rerun of the whole Live pipeline.
