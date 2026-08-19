# Task 6 report — failed/degraded news-source repair ledger

## Status

Completed as an evidence-led no-config-repair outcome. The starting branch was
`codex/pp03-a2-multi-source-provider-hub` at
`e9185c4c262d278f15b8cb0e9c4e04c1f8bc0135`, clean before Task 6. This task
does not start Task 7 or Phase 3.

## Isolated Before/After evidence

Both audits used the exact current 108-source configuration hash
`fbb2239676aa22d16a755780a80621a433b9ceb60f19499967440eac20c1f33e`,
8-second per-source timeout, 500,000-byte maximum response, 12 workers and
TLS verification. Every `VR_DATA_DIR`, reports, news-cache, log and acceptance
path was explicitly under the corresponding task-local directory.

| Audit | News success | Partial | Failure | Provider adapters | Evidence |
| --- | ---: | ---: | ---: | ---: | --- |
| Before | 88 | 13 | 7 | 7 | `.tmp/acceptance/a2-w1-phase2-before/public-audit.json` |
| After | 88 | 13 | 7 | 7 | `.tmp/acceptance/a2-w1-phase2-after/public-audit.json` |

The matched audit had no changed news/provider status or error taxonomy, so
there is no code-repair attribution and no observed network variance. The
isolated evidence contains redacted URLs/errors and no response bodies.

## Repairs and unresolved observations

No `news_sources.json` or `newsradar.py` edit was made. No failed source had
both a minimal redacted public parser fixture with named RED and a validated
same-publisher HTTPS identity migration; no one-in/one-out replacement was
therefore qualified. The decision ledger records every 7 failure and 13 partial
observation plus the named healthy 钛媒体. It retains TLS/DNS/connection cases
as observations, does not delete a source after one failure, and does not
disable TLS.

The provider matrix records only honest states: BaoStock/yfinance optional
dependency unavailable, IMF catalog-only, GDELT timeout, OECD HTTP client
error, SEC authentication without a fabricated contact, and World Bank schema
mismatch. No Adapter is claimed connected.

## RED/GREEN and security

No parser or feed migration was implemented, so no parser RED/GREEN cycle was
appropriate. `test_news_source_repairs.py` adds the stable source-identity and
108-count guard for this explicitly no-change outcome. Required focused GREEN:
`83 passed in 2.37s` for repair/newsradar/source-health probes.

No key, Cookie, Authorization header, login, CAPTCHA/paywall bypass, paid or
enterprise service, dependency installation, TLS relaxation, portfolio/holding
read, user-data read, AI truth-admission change, push, PR or merge occurred.

## Commit

Commit subject: `fix(pp03): repair qualified public data sources`.
The commit contains the ledger, provider/license boundaries, task report and
the no-change identity regression test; audit evidence remains ignored under
the task-scoped `.tmp/acceptance` roots for review.
