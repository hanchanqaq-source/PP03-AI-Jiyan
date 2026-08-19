# PP03 A2-W1 Phase 4 News Pipeline, Archive, and Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make news refresh an asynchronous raw/evidence/trusted pipeline, preserve the last trusted snapshot during verification, restore/query 90-day evidence history, and complete isolated browser acceptance and branch publication.

**Architecture:** A new `news_pipeline` orchestrator owns run state and snapshot ordering while reusing existing radar collection, deterministic verification, and trusted-news services. Evidence archive storage is append/merge by event ID, and current trusted publication uses an atomic pointer after all durable artifacts exist.

**Tech Stack:** Python thread executor/file storage, FastAPI, pytest, React/TypeScript, Vitest, temporary isolated Playwright Chromium.

**Spec:** `docs/superpowers/specs/2026-08-19-pp03-a2-multi-source-provider-hub-design.md`

## Global Constraints

- `raw_snapshot_id` is allocated once and shared by raw, evidence, trusted, pipeline status, and archive records.
- Only `verified` and `corroborated` events enter the trusted market-news snapshot.
- `unverified`, `conflicting`, `corrected`, and `disproved` remain visible in Evidence Center and archive.
- During verification or failure, the previous successful trusted snapshot remains the market-news read target.
- If raw count is positive and admitted count is zero, show the explicit pending-evidence message; do not show a misleading blank state.
- Archive retains 90 days, deduplicates by `event_id`, preserves correction/disproof history, and does not store full copyrighted article bodies.
- Recovery reads only allowlisted radar/evidence files and never portfolio amounts, costs, notes, accounts, or credentials.
- Temporary Playwright tools, browser binaries, npm cache, screenshots, and isolated data stay under `.tmp/acceptance/a2-w1` and are removed after evidence is copied to tracked locations.
- Final work pushes only `codex/pp03-a2-multi-source-provider-hub`; no PR, merge, force push, or later Work.

---

### Task 1: Pipeline models and atomic storage ordering

**Files:**
- Create: `backend/news_pipeline/__init__.py`
- Create: `backend/news_pipeline/models.py`
- Create: `backend/news_pipeline/storage.py`
- Modify: `backend/evidence_verification/models.py`
- Modify: `backend/evidence_verification/storage.py`
- Test: `backend/tests/test_news_pipeline_storage.py`
- Test: `backend/tests/test_evidence_storage.py`

**Interfaces:**
- Produces: `PipelinePhase`, `PipelineRun`, `RawSnapshot`, `TrustedSnapshot`, and `NewsPipelineStorage`.
- Extends: `EvidenceSnapshot.raw_snapshot_id` with backward-compatible deserialization.

- [ ] **Step 1: Write failing snapshot identity and crash-order tests**

```python
def test_all_pipeline_artifacts_share_raw_snapshot_id(tmp_path):
    storage = NewsPipelineStorage(tmp_path)
    storage.write_raw(raw_snapshot("raw-1"))
    storage.write_evidence(evidence_snapshot("raw-1"))
    storage.publish_trusted(trusted_snapshot("raw-1"))
    assert storage.load_current_trusted().raw_snapshot_id == "raw-1"


def test_current_pointer_is_not_switched_before_trusted_snapshot_is_durable(tmp_path, monkeypatch):
    storage = NewsPipelineStorage(tmp_path)
    storage.publish_trusted(trusted_snapshot("old"))
    monkeypatch.setattr(storage, "_write_current_pointer", raise_io_error)
    with pytest.raises(OSError):
        storage.publish_trusted(trusted_snapshot("new"))
    assert storage.load_current_trusted().raw_snapshot_id == "old"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_news_pipeline_storage.py backend\tests\test_evidence_storage.py -q -p no:cacheprovider`

Expected: missing `news_pipeline` module and missing `raw_snapshot_id`.

- [ ] **Step 3: Implement exact state models**

```python
class PipelinePhase(str, Enum):
    QUEUED = "queued"
    FETCHING = "fetching"
    RAW_SAVED = "raw_saved"
    VERIFYING = "verifying"
    EVIDENCE_SAVED = "evidence_saved"
    TRUSTED_PUBLISHED = "trusted_published"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class PipelineCounts:
    raw_event_count: int = 0
    verified_count: int = 0
    corroborated_count: int = 0
    pending_count: int = 0
    conflicting_count: int = 0
    corrected_count: int = 0
    disproved_count: int = 0
    failed_source_count: int = 0
```

`PipelineRun` includes run ID, raw/evidence/trusted snapshot IDs, phase, counts, timestamps, redacted error, and `displayed_trusted_snapshot_id`.

- [ ] **Step 4: Implement atomic storage**

Write raw, evidence, and trusted documents through temp-file + replace under `%VR_DATA_DIR%\evidence-verification\v1`. Persist a snapshot before advancing phase status. Publish current trusted pointer only after trusted content is durable. On startup, mark queued/fetching/verifying runs interrupted; do not infer success from partial files.

Backward evidence documents without `raw_snapshot_id` deserialize with their existing snapshot ID as the compatibility identity and are marked `legacy_identity=True` in recovery metadata.

- [ ] **Step 5: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_news_pipeline_storage.py backend\tests\test_evidence_storage.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/news_pipeline backend/evidence_verification/models.py backend/evidence_verification/storage.py backend/tests/test_news_pipeline_storage.py backend/tests/test_evidence_storage.py
git commit -m "feat(pp03): persist staged news snapshots"
```

---

### Task 2: Asynchronous pipeline service and APIs

**Files:**
- Create: `backend/news_pipeline/service.py`
- Create: `backend/news_pipeline/api.py`
- Modify: `backend/newsradar.py`
- Modify: `backend/evidence_verification/service.py`
- Modify: `backend/news_intelligence/service.py`
- Modify: `backend/app.py`
- Test: `backend/tests/test_news_pipeline_service.py`
- Test: `backend/tests/test_news_pipeline_api.py`
- Test: `backend/tests/test_news_intelligence.py`

**Interfaces:**
- Consumes: injected `radar_fetcher()`, `deterministic_verifier(raw_snapshot)`, and `trusted_projector(evidence_snapshot)`.
- Produces: `NewsPipelineService.start() -> PipelineRun`, `get_status()`, and required `GET /api/news/pipeline-status`.
- Changes: `POST /api/market-news/refresh`, `POST /api/evidence/refresh`, and `POST /api/radar/refresh` to compatibility entry points for the same HTTP 202 pipeline kickoff while preserving current read response shapes.

- [ ] **Step 1: Write failing same-snapshot and old-trusted tests**

```python
def test_pipeline_verifies_the_exact_raw_snapshot(tmp_path):
    service = pipeline_service(raw_events=[event("a")])
    run = service.start()
    service.wait(run.run_id)
    assert verifier.received_raw_snapshot_id == run.raw_snapshot_id
    assert service.get_status()["trusted_snapshot_id"] == trusted_store.current.raw_snapshot_id


def test_verification_failure_keeps_previous_trusted_snapshot(tmp_path):
    service = pipeline_service(previous_trusted="trusted-old", verifier=raising_verifier)
    run = service.start()
    service.wait(run.run_id)
    assert service.get_status()["phase"] == "failed"
    assert service.current_trusted().snapshot_id == "trusted-old"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_news_pipeline_service.py backend\tests\test_news_pipeline_api.py -q -p no:cacheprovider`

- [ ] **Step 3: Separate radar fetch from cache publication**

Expose a non-mutating collection result from `newsradar` that contains normalized raw events, source statuses, generated time, and failed-source count. The pipeline owns raw snapshot persistence; existing radar endpoints retain compatibility through an adapter that reads/publishes the same result.

- [ ] **Step 4: Implement the single-worker state machine**

```python
class NewsPipelineService:
    def start(self) -> dict[str, object]: ...
    def get_status(self, run_id: str | None = None) -> dict[str, object]: ...
    def current_trusted(self) -> TrustedSnapshot | None: ...
    def close(self) -> None: ...
```

Reject a second active refresh with 409. Allocate IDs before submitting the worker. Update counts after each durable phase. Deterministic verification returns all statuses; trusted projection admits only verified/corroborated.

- [ ] **Step 5: Wire FastAPI lifecycle and endpoints**

Mount `news_pipeline.api.router`, close the executor in lifespan `finally`, and make startup recovery best-effort without blocking the app. All three refresh routes call the same `NewsPipelineService.start()` method, return `{run_id, raw_snapshot_id, phase}` with 202, and share the same 409 active-run conflict. `GET /api/news/pipeline-status` returns current/selected run and displayed trusted snapshot.

- [ ] **Step 6: Run focused tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_news_pipeline_service.py backend\tests\test_news_pipeline_api.py backend\tests\test_news_intelligence.py backend\tests\test_newsradar.py backend\tests\test_api.py backend\tests\test_market_news_api.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/news_pipeline backend/newsradar.py backend/evidence_verification/service.py backend/news_intelligence/service.py backend/app.py backend/tests/test_news_pipeline_service.py backend/tests/test_news_pipeline_api.py backend/tests/test_news_intelligence.py
git commit -m "feat(pp03): orchestrate trusted news pipeline"
```

---

### Task 3: Market-news polling and truthful in-progress UI

**Files:**
- Create: `frontend/src/features/market-news/NewsPipelineStatus.tsx`
- Create: `frontend/src/features/market-news/__tests__/NewsPipelineStatus.test.tsx`
- Modify: `frontend/src/pages/MarketNews.tsx`
- Modify: `frontend/src/features/market-news/types.ts`
- Modify: `frontend/src/features/evidence-center/EvidenceCenterReal.tsx`
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/features/market-news/__tests__/MarketNewsEvidence.test.tsx`
- Test: `frontend/src/features/evidence-center/EvidenceCenterRealApi.test.tsx`

**Interfaces:**
- Consumes: 202 refresh response, `GET /api/news/pipeline-status`, and existing trusted market-news events.
- Produces: polling controller that retains prior events until `trusted_published`.

- [ ] **Step 1: Write failing UI lifecycle tests**

```tsx
it("keeps the previous trusted snapshot while verification runs", async () => {
  renderMarketNews({ initialEvents: trustedOld, pipeline: [rawSaved, verifying] });
  await user.click(screen.getByRole("button", { name: "刷新资讯" }));
  expect(await screen.findByText("新资讯已抓取，核验处理中；当前显示上一份可信快照。")).toBeInTheDocument();
  expect(screen.getByText(trustedOld[0].title)).toBeInTheDocument();
});


it("explains positive raw count with zero admitted events", async () => {
  renderMarketNews({ pipeline: [completedWith({ raw: 6, admitted: 0 })] });
  expect(await screen.findByText("本次已抓取 6 条资讯，目前尚无完成核验的内容。待核验资讯可在证据中心查看。")).toBeInTheDocument();
});


it("uses the shared pipeline when Evidence Center runs verification", async () => {
  renderEvidenceCenter({ pipeline: [rawSaved, evidenceSaved, trustedPublished] });
  await user.click(screen.getByRole("button", { name: "运行核验" }));
  expect(api.marketNewsRefresh).toHaveBeenCalledTimes(1);
  expect(api.evidenceRefresh).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run tests and verify RED**

Run in `frontend`: `npm run test:run -- src/features/market-news/__tests__/NewsPipelineStatus.test.tsx src/features/market-news/__tests__/MarketNewsEvidence.test.tsx src/features/evidence-center/EvidenceCenterRealApi.test.tsx`

- [ ] **Step 3: Add typed API methods and polling**

```ts
marketNewsRefresh: () => request<NewsPipelineStarted>("/market-news/refresh", "POST"),
newsPipelineStatus: (runId?: string) => get<NewsPipelineStatus>(`/news/pipeline-status${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`),
```

Poll only while queued/fetching/raw_saved/verifying/evidence_saved. Stop on unmount, failure, interrupted, or trusted publication. On trusted publication, fetch the new trusted event list once. On failure, retain old events and show the redacted failure state.

The Evidence Center “运行核验” action must call the same kickoff and polling controller rather than a separate synchronous evidence refresh. Its list reloads from the evidence snapshot only after `evidence_saved` or terminal failure, while the market-news list switches only after `trusted_published`.

- [ ] **Step 4: Render exact counts and snapshot IDs**

Display raw, verified, corroborated, pending, conflicting, corrected, disproved and failed-source counts plus raw/evidence/trusted IDs. Pending/conflicting/corrected/disproved never enter the trusted event list.

- [ ] **Step 5: Run focused tests and commit**

Run in `frontend`:

`npm run test:run -- src/features/market-news/__tests__/NewsPipelineStatus.test.tsx src/features/market-news/__tests__/MarketNewsEvidence.test.tsx src/features/evidence-center/EvidenceCenterRealApi.test.tsx`

Commit:

```powershell
git add frontend/src/features/market-news frontend/src/features/evidence-center/EvidenceCenterReal.tsx frontend/src/features/evidence-center/EvidenceCenterRealApi.test.tsx frontend/src/pages/MarketNews.tsx frontend/src/lib/api.ts
git commit -m "feat(pp03): show staged news refresh progress"
```

---

### Task 4: Full event archive and history merge

**Files:**
- Create: `backend/evidence_verification/archive.py`
- Modify: `backend/evidence_verification/storage.py`
- Modify: `backend/evidence_verification/service.py`
- Test: `backend/tests/test_evidence_archive.py`
- Test: `backend/tests/test_evidence_verifier.py`
- Test: `backend/tests/test_evidence_service.py`

**Interfaces:**
- Produces: `EvidenceArchive.upsert(snapshot)`, `get(event_id)`, `query(days, status)`, and `count()`.
- Consumes: `event_document()` with trusted-text filtering and complete evidence/status metadata.

- [ ] **Step 1: Write failing dedupe and history-preservation tests**

```python
def test_archive_deduplicates_by_event_id_and_preserves_disproof(tmp_path):
    archive = EvidenceArchive(tmp_path, now=lambda: NOW)
    archive.upsert(snapshot(event("e1", status="verified", history=[verified_transition])))
    archive.upsert(snapshot(event("e1", status="disproved", history=[verified_transition, disproved_transition])))
    rows = archive.query(days=90)
    assert len(rows) == 1
    assert rows[0]["verification_status"] == "disproved"
    assert len(rows[0]["status_history"]) == 2


def test_archive_does_not_store_full_article_body(tmp_path):
    archive.upsert(snapshot(event_with_excerpt("x" * 10000)))
    assert "x" * 5000 not in archive.raw_text()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_evidence_archive.py backend\tests\test_evidence_verifier.py backend\tests\test_evidence_service.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement date-bucketed atomic archive**

Use one JSONL file per UTC date under `archive/`. Upsert through an event index plus atomic bucket rewrite. Merge status history by transition identity, evidence by evidence ID/canonical URL, key fields by field name/status, and retain the most recent title/summary only when supplied by a real snapshot. Bound excerpt length to the existing evidence limit.

- [ ] **Step 4: Add query validation**

Accept only `days in {1,3,7,30,90}` and known verification statuses. Sort by published/verified time descending. Corrupt rows are skipped with a redacted diagnostic count, not allowed to fail the whole query.

- [ ] **Step 5: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_evidence_archive.py backend\tests\test_evidence_verifier.py backend\tests\test_evidence_service.py backend\tests\test_evidence_storage.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/evidence_verification/archive.py backend/evidence_verification/storage.py backend/evidence_verification/service.py backend/tests/test_evidence_archive.py backend/tests/test_evidence_verifier.py backend/tests/test_evidence_service.py
git commit -m "feat(pp03): archive complete evidence events"
```

---

### Task 5: Archive API, recovery importer, and cache cleanup

**Files:**
- Create: `backend/evidence_verification/recovery.py`
- Modify: `backend/news_pipeline/api.py`
- Modify: `backend/cache_management.py`
- Test: `backend/tests/test_evidence_recovery.py`
- Test: `backend/tests/test_news_archive_api.py`
- Test: `backend/tests/test_cache_management.py`

**Interfaces:**
- Produces: `GET /api/news/archive?days=&verification_status=`, `HistoryRecovery.scan()`, and `HistoryRecovery.import_records()`.
- Extends cache management with evidence archive category and 90-day deletion candidates.

- [ ] **Step 1: Write failing recovery allowlist tests**

```python
def test_recovery_reads_only_allowlisted_evidence_files(tmp_path):
    paths = create_radar_evidence_and_portfolio_files(tmp_path)
    report = HistoryRecovery(tmp_path).scan()
    assert paths.portfolio not in report.opened_paths
    assert paths.credentials not in report.opened_paths


def test_recovery_counts_cache_refetch_and_unrecoverable_separately(tmp_path):
    report = HistoryRecovery(tmp_path).import_records()
    assert report.to_dict().keys() >= {"cache_recovered", "public_refetched", "unrecoverable", "reasons"}
```

- [ ] **Step 2: Write failing archive API and cleanup tests**

Test all five day windows, status filter, correction/disproof retention, boundary day preservation, old archive deletion, current/trusted/config/user-data protection, and archive bytes counted toward cache totals.

- [ ] **Step 3: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_evidence_recovery.py backend\tests\test_news_archive_api.py backend\tests\test_cache_management.py -q -p no:cacheprovider`

- [ ] **Step 4: Implement allowlisted recovery**

Allow only explicitly supplied paths named radar cache, evidence current, evidence history, and recognized legacy evidence snapshots. Require real event ID/title/link/time/status fields before import. Public refetch is allowed only through the event's existing public link and is labeled `public_refetched`; missing or ambiguous records become unrecoverable with a reason.

- [ ] **Step 5: Implement archive API and cleanup**

Return list metadata and recovery provenance without full copyrighted bodies. Add archive date buckets older than 90 days to cleanup candidates; current/evidence/trusted snapshots and user files remain protected.

- [ ] **Step 6: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_evidence_recovery.py backend\tests\test_news_archive_api.py backend\tests\test_cache_management.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/evidence_verification/recovery.py backend/news_pipeline/api.py backend/cache_management.py backend/tests/test_evidence_recovery.py backend/tests/test_news_archive_api.py backend/tests/test_cache_management.py
git commit -m "feat(pp03): recover and retain evidence history"
```

---

### Task 6: Evidence Center history filters

**Files:**
- Modify: `frontend/src/features/evidence-center/EvidenceCenterReal.tsx`
- Modify: `frontend/src/features/evidence-center/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/features/evidence-center/EvidenceCenterRealApi.test.tsx`

**Interfaces:**
- Consumes: `GET /api/news/archive` for 1/3/7/30/90 days and verification status.
- Produces: time filter controls that query archive and preserve current event detail behavior.

- [ ] **Step 1: Write failing history UI tests**

Test five time buttons, exact API query, 30/90-day event visibility, correction/conflict/disproof visibility, loading/error/no-results states, and event detail opening from archived rows.

- [ ] **Step 2: Run tests and verify RED**

Run in `frontend`: `npm run test:run -- src/features/evidence-center/EvidenceCenterRealApi.test.tsx`

- [ ] **Step 3: Implement typed archive query and controls**

```ts
newsArchive: (query: { days: 1 | 3 | 7 | 30 | 90; verification_status?: VerificationStatus }) =>
  get<EvidenceEventList>(newsArchivePath(query)),
```

Keep current snapshot summary separate from archive list counts. Do not relabel archived corrected/disproved events as trusted admissions.

- [ ] **Step 4: Run focused/full/legacy/build and commit**

Run in `frontend`:

```powershell
npm run test:run -- src/features/evidence-center/EvidenceCenterRealApi.test.tsx
npm run test:run
npm run test:legacy
npm run build
```

Commit:

```powershell
git add frontend/src/features/evidence-center frontend/src/lib/api.ts
git commit -m "feat(pp03): query ninety day evidence history"
```

---

### Task 7: Final documents and deterministic qualification

**Files:**
- Create: `docs/data-sources/routing-and-fallback-policy.md`
- Create: `docs/data-sources/news-pipeline-and-history.md`
- Modify: `docs/data-sources/source-before-after.md`
- Modify: `docs/data-sources/source-repair-decisions.md`
- Modify: all Phase 1-3 provider matrices when current facts changed.

**Interfaces:**
- Produces all ten required `docs/data-sources/` documents and the final Before/After dataset.

- [ ] **Step 1: Run fresh deterministic backend suite**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests -m "not live" -q -p no:cacheprovider`

Record the fresh passed/deselected/warning counts and exact HEAD. Do not reuse earlier phase numbers.

- [ ] **Step 2: Run fresh frontend suite, legacy, and build**

Run in `frontend`:

```powershell
npm run test:run
npm run test:legacy
npm run build
```

- [ ] **Step 3: Run isolated free-no-key Live audit and history recovery**

Use `.tmp/acceptance/a2-w1/live-data` for `VR_DATA_DIR`, caches, logs, and outputs. Do not set paid-test authorization. Record source ID/config hashes, actual connection statuses, pipeline counts, cache-recovered/public-refetched/unrecoverable counts, and transient network variance.

- [ ] **Step 4: Complete all required documents**

Ensure these ten files exist and distinguish registered, connected, connector-only, unconfigured and licensed states:

```text
docs/data-sources/source-family-matrix.md
docs/data-sources/free-provider-matrix.md
docs/data-sources/freemium-provider-matrix.md
docs/data-sources/paid-provider-matrix.md
docs/data-sources/enterprise-provider-catalog.md
docs/data-sources/licensing-and-usage-boundaries.md
docs/data-sources/routing-and-fallback-policy.md
docs/data-sources/source-repair-decisions.md
docs/data-sources/news-pipeline-and-history.md
docs/data-sources/source-before-after.md
```

`source-before-after.md` must contain all 26 exact measures: family count, Adapter count, capability count, Eastmoney entries before aggregation, Eastmoney families after aggregation, visible sources without funds, visible sources with a fund, registration equality, failures before repair, successfully repaired, official entries updated, alternatives added, old sources disabled, unresolved, unresolved reasons, unconfigured-Key count, license-required count, raw news count, verified count, corroborated count, pending count, conflicting count, archived event count, cache-recovered count, public-refetched count, and unrecoverable-history count. Every number includes its measurement command/snapshot ID and time.

- [ ] **Step 5: Perform whole-branch review**

Review `9dfd824..HEAD` for every PASS requirement, API compatibility, source independence, industry evidence separation, trusted admission, reference redaction, credential/budget safety, archive retention, recovery privacy, and stale Fixture removal. Scan for unfinished markers, run `git diff --check`, run the sensitive-pattern scan, and verify configuration hashes. Fix any Critical/Important finding with RED/GREEN before browser acceptance.

- [ ] **Step 6: Commit qualification documents**

```powershell
git add docs/data-sources
git commit -m "docs(pp03): record a2 source hub qualification"
```

---

### Task 8: Temporary Playwright acceptance and screenshots

**Files:**
- Create and later delete: `.tmp/acceptance/a2-w1/playwright/`
- Create: `docs/acceptance/a2-w1/browser-acceptance.md`
- Create: `docs/screenshots/a2-w1/` (formal task-scoped screenshots)

**Interfaces:**
- Consumes: isolated backend on `127.0.0.1:8900`, frontend on `127.0.0.1:5899`, isolated acceptance data.
- Produces: browser evidence for all 25 required scenarios and console/network counts.

- [ ] **Step 1: Create an isolated Playwright tool directory**

Set npm cache, Playwright package, browser binaries, results, raw screenshots, logs, and `VR_DATA_DIR` under `.tmp/acceptance/a2-w1`. Install only Playwright Chromium. Do not modify frontend `package.json` or lockfile and do not connect to user browser profiles.

- [ ] **Step 2: Start isolated services with owned process/session IDs**

Inspect ports first. If they are occupied by previously recorded PP03 baseline sessions, verify their command line and worktree before stopping only those owned sessions; never kill an unknown listener. Start backend from `backend` with `app:app` on 8900 and frontend on 5899. Verify listeners and `/api/health`. Record exact commands/PIDs so only owned processes are stopped later.

- [ ] **Step 3: Write a temporary semantic acceptance script**

The script creates a new Chromium context, registers `console`, `pageerror`, and `requestfailed`, navigates using roles/text/test IDs, and exits nonzero on assertion failure. It must assert all 25 scenarios explicitly:

1. Complete Catalog is visible with no funds.
2. All registered news sources are visible with no funds.
3. Adding an isolated test fund does not change source registration count.
4. Adding that fund adds only holdings relationships.
5. Eastmoney renders as one source family.
6. Eastmoney Adapters and capabilities can be expanded.
7. One failed Eastmoney capability yields partial degradation, not whole-family failure.
8. `configured_reference` remains visible for a failed capability.
9. BaoStock is an independent family.
10. SEC, World Bank, and OECD display as free formal sources with their actual connection state.
11. FRED, EIA, and Tushare display unconfigured when no Key is present.
12. Alpha Vantage, Finnhub, and Twelve Data display free-quota/plan status without claiming permanent quotas.
13. Nasdaq Data Link distinguishes free and Premium datasets.
14. Bloomberg, LSEG, FactSet, Wind, and other enterprise rows display license-required.
15. Free-only mode records zero paid transport calls.
16. News refresh displays raw, verified, and pending counts separately.
17. Positive raw count with zero trusted admission shows the explicit Evidence Center message.
18. Previous trusted events remain visible during verification.
19. The 30-day archive filter returns eligible archived events.
20. The 90-day archive filter returns eligible archived events.
21. Corrected, conflicting, and disproved history remains visible after a newer snapshot.
22. Console error count is zero.
23. Console warning count is zero.
24. Pageerror count is zero.
25. Every failed request is recorded with a reason and none is page-blocking.

- [ ] **Step 4: Run acceptance and save formal evidence**

Require console errors/warnings/pageerrors all zero. List each failed request and classify whether blocking; no blocking request may remain. Copy only successful, relevant screenshots to `docs/screenshots/a2-w1/` and record dimensions/hashes.

- [ ] **Step 5: Write browser acceptance documentation**

Record temporary Playwright/Chromium versions, service URLs, exact scenarios, screenshots, console/pageerror/requestfailed counts, isolation boundaries, and that Browser Use was not used. Do not include credentials or user data.

- [ ] **Step 6: Retire W0 demo Fixtures only after the first real acceptance passes**

Run `rg -n "evidenceFixtures|prototype" frontend/src/features/evidence-center frontend/src/pages/MarketNews.tsx` and inspect every hit. If production still imports the W0 demo Fixture, remove that fallback and its now-unused Fixture only after the real API scenarios above passed; keep test-only builders. Re-run Evidence Center/Market News focused tests, full frontend, production build, and the three API-backed browser scenarios. If no production import remains, record that the removal gate was already satisfied and make no deletion.

- [ ] **Step 7: Stop owned services and clean temporary tools/data**

Stop only recorded PIDs/sessions. Resolve and verify the exact `.tmp/acceptance/a2-w1` path is inside this worktree, then permanently remove the temporary Playwright package, Chromium binaries, npm cache, results, raw screenshots and isolated data. Confirm ports 8900/5899 have no owned listeners and frontend package/lock files have no Playwright diff.

- [ ] **Step 8: Commit formal browser evidence and any qualified Fixture retirement**

```powershell
git add docs/acceptance/a2-w1/browser-acceptance.md docs/screenshots/a2-w1 frontend/src/features/evidence-center frontend/src/pages/MarketNews.tsx
git commit -m "test(pp03): accept a2 multi-source provider hub"
```

---

### Task 9: Final verification, push, and stop gate

**Files:**
- Modify only if evidence requires correction: `docs/data-sources/source-before-after.md`
- Modify only if evidence requires correction: `docs/acceptance/a2-w1/browser-acceptance.md`

**Interfaces:**
- Produces the final A2-W1 report bound to the pushed Commit.

- [ ] **Step 1: Re-run fresh final verification after the last tracked change**

Run deterministic backend, frontend full, legacy, production build, `git diff --check`, secret scan, ten-document assertions, screenshot hashes, archive/recovery count assertions, and worktree status. Use only results from this final run.

- [ ] **Step 2: Verify Git scope and branch**

Confirm branch is `codex/pp03-a2-multi-source-provider-hub`, merge base is the exact baseline, no `main` worktree changed, all four phases have reviewed commits, and tracked worktree is clean.

- [ ] **Step 3: Push the current branch without force**

Run:

```powershell
git push -u origin codex/pp03-a2-multi-source-provider-hub
```

Do not create a PR. If authentication fails, preserve all commits and report the exact Git error rather than changing credentials or remotes.

- [ ] **Step 4: Verify remote SHA equality**

Fetch only the new branch, compare local `HEAD` with `refs/remotes/origin/codex/pp03-a2-multi-source-provider-hub`, and require zero ahead/behind. Confirm worktree clean and no services/temporary acceptance data remain.

- [ ] **Step 5: Deliver the exact taskbook final report**

Report PASS/PARTIAL/BLOCKED, baseline/current branch and Commit, push state, family/Adapter/capability/registered/connected/shell/Key/license counts, Eastmoney aggregation, provider groups, per-source repair outcomes, zero-/with-holdings comparison, pipeline counts, archive recovery counts, fresh test results, Playwright evidence, explicit non-actions, clean state, and that work remains at A2-W1.

Stop after the report. Do not create PR, merge `main`, delete the feature worktree/branch, or begin another Work.
