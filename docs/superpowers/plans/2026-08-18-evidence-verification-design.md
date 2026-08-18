# PP03 A1.1-W1 Evidence Verification Design

## Objective

Connect the A1.1-W0 Evidence Center prototype to a deterministic backend that verifies existing market-news events without searching for new sources, using AI as a truth judge, or reading user portfolio amounts, costs, notes, accounts, or credentials.

The baseline is remote commit `050e28604e46f05d1975bee54078c0242bcbe964` on `codex/pp03-a1-1-evidence-center-prototype`. Work is isolated on `codex/pp03-a1-1-evidence-verification`.

## Accepted boundaries

- Inputs are limited to the configured 108 news sources, the existing radar cache, event source metadata and raw public links already present in those events, and existing official/regulatory/disclosure links.
- Exact existing official links may be fetched with TLS verification, bounded redirects, bounded time, bounded response size, safe public-host validation, and deterministic parsing.
- No web search, ordinary source addition, financial provider addition, API key, login cookie, CAPTCHA/paywall bypass, TLS bypass, AI truth decision, or coverage-driven threshold relaxation.
- Full copyrighted article bodies are never persisted. Storage contains normalized claims, necessary excerpts/fields, evidence metadata, status history, and public links only.

## Architecture

`backend/evidence_verification/` owns all truth-verification policy. `newsradar.py` continues to collect/cache feeds. `news_intelligence.service.MarketNewsService` consumes the evidence admission result but does not implement verification rules.

The evidence refresh pipeline is:

1. Read the current radar snapshot without loading the portfolio.
2. Normalize and cluster raw events using the existing market-news pipeline.
3. Build one core claim from source title/excerpt only; AI summaries and translations are excluded.
4. Extract typed key-field candidates (money, percentage, quantity, date, report period, effective date, build cycle and counterparty).
5. Canonicalize only public HTTP(S) links already attached to the event, identify content source, collector source, source role and origin cluster, and group syndicated copies.
6. Optionally retrieve exact existing official links through a fail-closed bounded fetcher. A fetch failure leaves the evidence unresolved.
7. Compare core claims and normalized fields deterministically.
8. Generate the event status and append transitions without overwriting history.
9. Persist the complete new snapshot atomically only after the whole refresh succeeds. On failure, preserve the last successful snapshot and update only refresh diagnostics.

## Source identity and independence

- `content_source` identifies the publisher of the linked content.
- `collector_source` identifies the configured feed or collection channel that supplied the item.
- `origin_cluster` is derived from the canonical content publisher and canonical article identity, not from collector count.
- Exact canonical links, same publisher/title/date fingerprints, and known feed aliases belong to one origin cluster.
- Ten syndicated copies from one origin cluster count as one evidence chain.
- Corroboration requires at least two supporting evidence items from different origin clusters.
- Official status uses an explicit conservative registry limited to official domains already represented by configured sources or exact existing disclosure links. A source name alone never establishes official status.

## Deterministic decision table

| Condition | Status |
| --- | --- |
| Official evidence explicitly denies the core claim | `disproved` |
| Official evidence explicitly corrects the prior version | `corrected` |
| Reliable evidence disagrees on the core claim or a decisive field | `conflicting` |
| Official evidence directly supports the core claim | `verified` |
| At least two independent origin clusters consistently support it | `corroborated` |
| Anything else | `unverified` |

Status priority is exactly the table order. Source health, AI output, raw source count and repeated syndication cannot upgrade a status.

Each key field has its own status. A verified core claim does not automatically verify its money, percentage, quantity or date. Only `verified`/`corroborated` fields enter the market-news projection; unresolved or conflicting fields remain visible only in Evidence Center.

## Market-news admission

The evidence snapshot is authoritative for admission:

- Admit `verified` and `corroborated` current versions.
- Exclude `unverified`, `conflicting`, `disproved`, and a `corrected` old version until the corrected claim is reverified.
- Sanitize admitted title/summary/impact inputs by removing key fields that are not independently trusted.
- When no event qualifies, show the specified empty-state copy instead of falling back to unverified content.
- Evidence refresh never calls the portfolio loader. Existing market-news relationship calculation may consume admitted events afterward, but evidence storage receives no amount, cost, note, account or credential fields.

## Persistence and API

Evidence files live below `VR_DATA_DIR/evidence-verification/v1/`:

- `current.json`: latest complete successful snapshot.
- `last-refresh.json`: most recent refresh outcome and timestamps.
- `history/YYYY-MM-DD.jsonl`: append-only status transitions.

Public API:

- `GET /api/evidence/summary`
- `GET /api/evidence/events`
- `GET /api/evidence/events/{event_id}`
- `POST /api/evidence/refresh`

The list accepts `verification_status`, `tag_id`, `category`, `days`, and `holding_relevance`. No snapshot returns an explicit empty/unloaded state, never prototype values.

## Frontend transition

Evidence Center retains its three tabs. The verification list, summary, correction history, query-driven drawer and refresh action switch to the real API. Source Health remains the existing A1 component. Market News keeps its current layout and adds only the trusted status badge and evidence link.

The W0 fixtures and market-news prototype adapter remain in place while API integration tests are red/green. They are removed only after real API tests and the isolated browser acceptance pass, preventing a half-migrated user interface.

## Verification strategy

All behavior is developed test-first. Backend tests cover the fixed decision table, field-level isolation, syndication, history, atomic refresh preservation, API filters/admission and the portfolio-read boundary. Frontend tests cover trusted-only admission, number hiding, API-backed Evidence Center, drawer chains/history, corrections, Source Health and the four existing pages. Final qualification adds complete backend/frontend/legacy suites, production build, isolated Browser Use with console error/warn count zero, secret scan, temporary-data cleanup, scoped commit, and non-force branch push.
