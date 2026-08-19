# Phase 4 preflight Task 0 report

Date: 2026-08-20

Status: **IMPLEMENTATION PASS - READY FOR INDEPENDENT REVIEW**

Branch: `codex/pp03-a2-multi-source-provider-hub`

Verified starting HEAD: `d0990ed135a12b78c39e5b3d2d74f7eef7cb3ebe`

Scope: Phase 3 breaker carry-forward only. No Phase 4 news-pipeline task,
Provider transport, credential, Key, paid/Live request, SDK, dependency, UI
feature, push, PR, merge, Tag, Release, or deployment was used.

## Root causes and minimal decisions

### Finding A - pre-warmed Service missed later shared-ledger state

`DataSourceService._assert_usage_reconciliation_ready()` returned immediately
after any earlier `ready` observation. That process-local point-in-time latch
therefore hid a fresh or stale reservation written later by another
`UsageStore`/process. Without a request limit, the pre-warmed Service could
reserve and invoke a second probe.

Decision: remove only the permanent-ready short circuit. Every controlled
authorization boundary now performs one bounded, payload-free, non-recursive
shared-ledger recovery pass and then checks the current result. A fresh open
reservation is pending and blocks before Registry/provider access. A stale
orphan remains blocked. After the owner settles, the next call rechecks current
ledger truth and proceeds as a separate authorization. The no-limit
interleaving proves exactly one owner probe plus one later caller probe, two
unique reservations, and one request count per legitimate invocation; recovery
itself adds no probe or count.

### Finding B - exact already-settled legacy zero sidecar

The Round-4 compatibility parser rejected
`validation_not_attempted/request_count=0/units=0` before it could compare the
document with the trusted ledger. The later inert-match check also hard-coded
`request_count=1`.

Decision: the external compatibility parser recognizes one additional exact
legacy combination only: zero estimated/actual cost, exact
`validation_not_attempted`, exact integer request count zero, and exact zero
units. It still performs all schema, identity, timestamp, size, version,
service, guard-scope, adapter-allowlist and duplicate checks. Under the existing
cross-process lock, the parsed sidecar is inert only when every field exactly
matches an already-settled ledger row. The comparison uses the parsed request
count instead of granting a fixed count. The sidecar is retained byte-for-byte
and never enters the staged-ledger reconciliation batch.

Against an open/staged row, unknown identity, mismatch, hostile/corrupt input,
oversize, future schema/time, or an untrusted adapter, recovery stays blocked
and performs no ledger write. The legacy sidecar has no settlement authority.

## TDD RED checkpoint

Existing baseline command:

`backend\.venv\Scripts\python.exe -m pytest backend\tests\test_usage_reconciliation_recovery.py -q -p no:cacheprovider`

Result before new tests: **44 passed, 1 existing warning**.

New RED command, before any production edit:

`backend\.venv\Scripts\python.exe -m pytest backend\tests\test_usage_reconciliation_recovery.py -q -p no:cacheprovider -k "prewarmed_service or exact_legacy_zero"`

Result: **5 failed, 44 deselected, 1 existing warning**.

Failure mapping:

1. Pre-warmed Service plus a later fresh reservation did not raise and reached
   a second probe path; no request limit was configured.
2. In the real two-Service owner interleaving, the pre-warmed Service did not
   remain pending; no request limit was configured.
3. Pre-warmed Service plus a later stale orphan did not block; no request limit
   was configured.
4. An exact legacy zero sidecar matching a settled zero ledger row reported
   `blocked` instead of inert `closed`.
5. The same exact zero sidecar against an open reservation reported zero
   retained blockers instead of the exact retained intent plus open row.

The RED result and causes were checkpointed before production changes.

## GREEN and focused verification

- New five-behavior selection: **5 passed, 44 deselected, 1 warning**.
- Complete recovery suite: **49 passed, 1 warning**.
- Focused recovery, Service/runtime authorization, UsageStore and budget group:
  **251 passed, 1 warning**.

The warning is the existing FastAPI TestClient Starlette/httpx deprecation.

## Concurrency, boundedness and atomic semantics

- The pre-warmed-boundary tests count exactly one recovery call per attempted
  authorization. There is no retry loop or recursive call into authorization.
- Service-local recovery remains serialized by `_usage_recovery_lock`.
  Ledger reads/comparisons remain under `CACHE_IO_LOCK` plus the existing
  cross-process `.usage.lock`.
- Authorization holds its existing context lock before recovery. Recovery never
  enters authorization contexts, so the change adds no reverse lock order.
- Fresh open rows remain unchanged and pending through the five-minute window;
  stale rows remain unchanged and blocked after the boundary.
- Owner settlement remains the existing stage-then-idempotent-reconcile flow.
  A waiting Service merely observes it on the next bounded pass.
- Exact matching legacy sidecars cause no ledger rewrite. Open/mismatched
  sidecars return before any staged-ledger write. Only server-staged ledger rows
  can enter the existing recovery batch of at most 128.

## Fresh full gates

- Backend deterministic:
  `backend\.venv\Scripts\python.exe -m pytest backend\tests -m "not live" -q -p no:cacheprovider`
  -> **1470 passed, 3 skipped, 13 deselected, 1 warning** in 26.83 s.
- Frontend full: `npm run test:run`
  -> **29 files, 180 tests passed**.
- Frontend legacy: `npm run test:legacy`
  -> **16 passed, 0 failed**.
- Production build: `npm run build`
  -> **PASS**, 1,917 modules transformed. The existing empty
  `vendor-charts` and large main-chunk warnings remain; the main chunk is
  799.61 kB minified.

## Diff, security and scope scans

- `git diff --check`: PASS; only configured LF-to-CRLF checkout notices.
- Production added lines scanned: 21.
- Production added-line secret assignment: 0.
- Added URL: 0.
- Added credential URL/query construction: 0.
- Added direct-network call: 0.
- Added delete call: 0.
- Added logging/browser-storage call: 0.
- Added paid/Live/enterprise gate: 0.
- Package/lock/requirements/pyproject changes: 0.
- News-pipeline/newsradar/evidence-verification file changes: 0.

Changed implementation/test scope is limited to:

- `backend/data_sources/service.py`
- `backend/data_sources/usage_store.py`
- `backend/tests/test_usage_reconciliation_recovery.py`
- this report

CORS, routing, ConfigStore, Registry wiring, Catalog registration, guard policy,
credential truth, keyed/freemium/paid/enterprise fail-closed barriers, UI and
the Phase 2 authoritative source audit remain unchanged.

## Self-review and concerns

Self-review found no remaining Critical, Important or Minor failure in Task 0
scope. The realistic mutation checks are covered: restoring the ready-latch
short circuit fails three no-limit tests; rejecting the exact legacy zero shape
fails the settled/inert test; treating it as authoritative or failing to retain
the open blocker fails the open-row immutability/count test.

Known non-blocking concerns:

1. Every authorization now intentionally pays one bounded shared-ledger
   read/lock pass; this is the correctness cost of cross-process revalidation.
2. A legitimate probe exceeding the existing five-minute in-flight window is
   observed as blocked by another Service until the owner settles; no settlement
   is guessed.
3. The existing TestClient deprecation and Vite empty/large-chunk warnings
   remain.
4. The ledger boundary still does not claim tamper resistance against an actor
   able to rewrite the trusted ledger or execute code as the application owner.

Independent review remains required before Task 1. Task 1 was not started. No
remote action occurred.
