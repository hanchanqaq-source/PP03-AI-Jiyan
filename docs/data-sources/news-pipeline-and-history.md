# A2-W1 news pipeline and 90-day history qualification

## Evidence boundary

The bounded free/no-Key observation used only
`.tmp/acceptance/a2-w1/live-data` and existing adapters at starting HEAD
`57fd3384fb6038af4fca1f11fe60ddce589a644d`. It did not read `.env`,
credential values, user default data, portfolio amounts/cost/notes/accounts,
browser profiles, paid authorization or licensed providers, and it started no
persistent service. Its output
`outputs/live-qualification.json` is 124123 bytes with SHA-256
`B868BB66DF7011A29830687D234D1CB2D1D5AF9A9269D5F3536A080316C6C1B5`.

The observation window was `2026-08-24T20:45:56.372963+08:00` through
`2026-08-24T20:46:46.912056+08:00`. Catalog fingerprint
`b51d725f8ea653b54fb6ac39bea47e29fa592549cc5a07805087121726ed6f79`
and `backend/news_sources.json` SHA-256
`2d53b3d7064688823a0fdd41c1bbd29c0d67f40c072ea6975ae4226015779f7a`
bind the configuration. The later archive repair is commit `09c4145`; no Live
or network command was rerun after the failure.

## Durable stage contract

| Stage | Durable meaning | Publication rule |
| --- | --- | --- |
| `queued` / `fetching` | Run exists; collection is incomplete. | Keep the previous trusted pointer. |
| `raw_saved` | Exact ordered raw snapshot and source outcomes are durable. | Never treat raw rows as trusted. |
| `verifying` | Deterministic verifier consumes that exact raw snapshot ID. | Keep pending/conflicting rows isolated. |
| `evidence_saved` | Evidence snapshot and status counts are durable. | Archive all statuses; only verified/corroborated are eligible for trusted projection. |
| `trusted_published` | Raw, evidence, archive and trusted durability prerequisites passed. | Switch the trusted pointer only now. |
| `failed` / `interrupted` | Terminal cause remains explicit and redacted. | Preserve durable artifacts and the previous trusted pointer. |

One run allocates one `raw_snapshot_id`; raw, evidence, run status, archive and
trusted artifacts must retain that lineage. Positive raw count with zero
admission is shown as pending evidence, not as a misleading empty feed.

## Preserved Live pipeline result

| Field | Observed value |
| --- | --- |
| Run / raw / evidence / trusted IDs | `7fe1e552fd254ee98c6a221e66a31fec` / `87663551b9404a57bd1b5eb7f6f03dc3` / `9cbcceb192697d2f7ce9` / `null` |
| Run interval | `2026-08-24T20:46:36.358024+08:00` to `2026-08-24T20:46:46.639247+08:00` (`10.545s`) |
| Source attempts | 104 ok / 4 failed |
| Evidence counts | 507 raw / 28 verified / 0 corroborated / 467 pending / 12 conflicting / 0 corrected / 0 disproved |
| Durable checkpoint | `evidence_saved` |
| Terminal result | `failed / evidence_compatibility_failed` |
| Trusted publication | 0 events; no trusted snapshot |
| Archive/recovery in that Live run | archive 0; cache-recovered 0; public-refetched 0; unrecoverable 0 |

This failure is historical qualification evidence, not erased by the repair.
The exact evidence artifact reproduced
`ValueError: ambiguous archive evidence identity` in a separate diagnostic
archive. The pipeline normalized the error, preserved `evidence_saved`, and
did not run compatibility publication or move the trusted pointer.

## Repair and offline replay

Commit `09c4145334b4502dde55185b8b72653c1e53d2f9` fixes archive-boundary
identity, timezone and key-field merge compatibility. Independent review was
`SPEC PASS` / `QUALITY READY`; the reviewer archive suite was 546 passed / 16
skipped. The final directed matrix was 30 passed / 532 deselected, the real
ten-file related selection was 410 passed / 9 skipped with one known Starlette
warning, and the full deterministic backend was 2403 passed / 29 skipped / 13
deselected with the same warning.

The first related-test command named two nonexistent paths and honestly exited
1 with `no tests ran`; `rg --files backend/tests` identified the real ten files,
whose corrected invocation exited 0 with the 410/9 result. The failed command
is not counted as qualification success.

The preserved evidence snapshot was then read without network access and
written to a new isolated archive by:

```powershell
backend\.venv\Scripts\python.exe .tmp\acceptance\a2-w1\live-data\diagnostics\archive-fix-offline\run.py
```

The snapshot `9cbcceb192697d2f7ce9` / raw snapshot
`87663551b9404a57bd1b5eb7f6f03dc3` contained 507 events. At artifact
finalization `2026-08-24T14:55:26.4354283Z`, first write archived 507 and exact
retry remained 507; all 12 legal independent/contradicting overlaps normalized
to one relation with `source_role` preserved, every key-field triple and raw
digest remained stable, every history length remained 1, and a synthetic
genuine ambiguity was rejected with zero writes. This is offline compatibility
qualification, not a Live/network rerun and not evidence that provider states
changed.

## Archive retention and privacy

- Archive query windows are exactly 1, 3, 7, 30 and 90 days. Complete event
  metadata, evidence relations, status history and snapshot lineage are kept by
  event ID; newer snapshots do not erase conflicting, corrected or disproved
  history.
- Storage is date-bucketed, bounded, digest-bound and event-ID deduplicated.
  Cleanup considers only whole buckets older than 90 days through the existing
  cache-candidate safety boundary.
- Only bounded public excerpts/summaries and sanitized public references are
  retained. Full copyrighted bodies, credentials, cookies, authorization
  fields, sensitive query parameters and private portfolio fields are excluded.
- Recovery opens only explicit radar/evidence legacy paths. Cache recovery and
  public refetch are counted separately; public refetch is restricted to an
  event's existing public link. Missing identity stays unrecoverable.
- The Live scan/import reported explicit 0/0/0 recovery counts because the
  failed run never reached compatibility publication. The offline archive
  replay did not perform recovery and does not change those three counts.

## Configured-news variance

Two bounded observations of the same 108 configured sources were
`85 success / 17 partial / 6 failure` (99 connected) and
`84 / 17 / 7` (98 connected). Source `news:44726681bff6e16e` changed from
`success/none` to `failure/authentication`. This single transient change did
not justify a parser/config repair, source disable, replacement or license
inference.
