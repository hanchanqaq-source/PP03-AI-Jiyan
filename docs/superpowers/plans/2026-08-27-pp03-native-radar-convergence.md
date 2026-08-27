# PP03 Native Radar Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the five parallel PP03 product/data-hub surfaces and add safe, persistent RSS source management inside the native Vibe-Research information radar.

**Architecture:** Restore the current upstream shell and native signal/radar pattern file-by-file from upstream `ab4ffa0`, then add one focused `news_source_manager.py` consumed by `newsradar.py` and small `/api/radar/sources` endpoints. Delete only modules with no remaining production callers; preserve all user data and historical evidence.

**Tech Stack:** React 19, TypeScript, Vite/Vitest, FastAPI, Python 3.12 standard-library RSS/XML and HTTP handling, JSON local storage.

**Spec:** `docs/superpowers/specs/2026-08-27-pp03-native-radar-convergence-design.md`

## Global Constraints

- Base commit is exactly `810204efd317000aafa9836f8e2996ae4b1600ca`; branch is `codex/pp03-native-radar-convergence`.
- Do not merge upstream wholesale; transplant only task-relevant files from `ab4ffa077e0b1806fc53164dc7b28731f834e79e` and record the SHA.
- Do not delete or rewrite portfolios, fund holdings, reports, watchlists, notes, AI settings, keys, non-cache user files, or old evidence.
- Do not use real user credentials, paid APIs, enterprise endpoints, or real-cost services.
- No PR, main merge, tag, release, deploy, force push, or next Work.
- Every production behavior begins with a failing test and a verified RED reason.

---

### Task 1: Freeze convergence evidence and native shell contract

**Files:**
- Create: `docs/acceptance/native-convergence/feature-1-to-5-map.md`
- Create: `docs/acceptance/native-convergence/removed-parallel-modules.md`
- Test: `frontend/src/features/shell/__tests__/nativeConvergenceRoutes.test.tsx`

**Interfaces:**
- Produces: route contract where `/` redirects to `/daily-review`, `/intel/:tab` renders `Intel`, `/signals/:tab` renders `Signals`, and the five PP03 routes are absent.

- [ ] Write the route test with literal expected paths and visible nav labels; make it fail against the fixed baseline because the five PP03 routes still exist and `/signals` is absent.
- [ ] Run `npm run test:run -- frontend/src/features/shell/__tests__/nativeConvergenceRoutes.test.tsx` and record the expected RED diff.
- [ ] Transplant upstream `router.tsx`, `Layout.tsx`, `Signals.tsx`, `EChart.tsx`, `signals.py`, `signals_gpu_seed.json`, and `test_signals.py`; merge only signal API/tool types into `app.py`, `tools.py`, and `frontend/src/lib/api.ts`.
- [ ] Restore upstream `Intel.tsx` tab routing before adding the new source-manager tab.
- [ ] Run the route test and upstream signal tests to GREEN.
- [ ] Run `git diff --check` and commit the evidence plus shell contract.

### Task 2: Implement safe source registry and atomic local persistence

**Files:**
- Create: `backend/news_source_manager.py`
- Test: `backend/tests/test_news_source_manager.py`
- Modify: `backend/newsradar.py`

**Interfaces:**
- Produces: `SourceManager.list_sources()`, `test_candidate(payload)`, `add_custom_source(payload)`, `set_enabled(source_id, enabled)`, `delete_custom_source(source_id)`, `runtime_config()`, and `record_statuses(rows)`.
- Produces: custom schema v1 at `%VR_DATA_DIR%/news-sources.custom.json`.

- [ ] Write storage RED tests for built-in-first URL dedupe, stable IDs, corrupt custom-file fallback, atomic replacement, built-in non-deletion, custom deletion, enable/disable persistence, and sensitive query redaction.
- [ ] Run the new test file and verify failures are missing-module/behavior failures.
- [ ] Implement the minimal registry and persistence code using temp file + flush + `os.replace`.
- [ ] Run storage tests to GREEN.
- [ ] Write security RED tests for private/loopback/link-local/reserved DNS results, userinfo URLs, redirect-to-private, redirect limit, TLS verification path, 1 MiB cap, non-2xx, malformed XML, and feeds with neither title nor link.
- [ ] Implement the bounded redirect-aware probe and error redaction, then run security tests to GREEN.
- [ ] Write RED integration tests proving `newsradar` receives enabled built-in plus custom sources and retains cached items on one-source failure.
- [ ] Change `newsradar._load_source_config()` to consume `SourceManager.runtime_config()` and record collection statuses; keep `newsradar` as the only fetch/cache authority.
- [ ] Run `backend/tests/test_news_source_manager.py backend/tests/test_newsradar.py` to GREEN and commit.

### Task 3: Add source-management API under the native radar

**Files:**
- Modify: `backend/app.py`
- Test: `backend/tests/test_radar_sources_api.py`

**Interfaces:**
- Produces the six `/api/radar/sources` endpoints from the design.
- Consumes `SourceManager` methods from Task 2.

- [ ] Write API RED tests for list, pre-save test, add-after-test, enabled mutation, custom deletion, built-in deletion rejection, duplicate rejection, corrupt-config write rejection, and `X-PP03-Write-Intent`/loopback enforcement.
- [ ] Run the API test file and confirm expected 404/contract failures.
- [ ] Add Pydantic request models and thin endpoint adapters; map validation to 422, duplicates/conflicts to 409, missing IDs to 404, and probe failures to a truthful non-success result.
- [ ] Run API tests to GREEN, then run native `test_api.py`, `test_reports_and_security.py`, `test_newsradar.py`, and upstream `test_signals.py`.
- [ ] Commit the focused backend API change.

### Task 4: Add the data-source manager inside `Intel`

**Files:**
- Create: `frontend/src/features/radar-sources/types.ts`
- Create: `frontend/src/features/radar-sources/RadarSourceManager.tsx`
- Create: `frontend/src/features/radar-sources/__tests__/RadarSourceManager.test.tsx`
- Modify: `frontend/src/pages/Intel.tsx`
- Modify: `frontend/src/components/layout/Layout.tsx`
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces the `/intel/sources` tab and source CRUD/test client methods.
- Consumes the Task 3 API response shape.

- [ ] Write UI RED tests that exercise the real component: built-in/custom rows, visible health fields, add form, mandatory successful test, immediate list refresh, reload persistence, enable/disable, custom-only delete, API unsupported copy, and corrupt-config warning.
- [ ] Run the component test and confirm missing component/API behavior causes RED.
- [ ] Implement types, bounded decoders, API methods with write-intent headers, and the smallest accessible UI.
- [ ] Wire `sources` into `Intel` and the nested native radar links without creating a standalone route.
- [ ] Run the component, Intel, and route tests to GREEN; run a mutation check against wrong built-in/custom deletion and skipped connection test.
- [ ] Commit the native radar UI change.

### Task 5: Remove five pages and parallel production modules

**Files:**
- Delete: `frontend/src/pages/{ResearchHome,MarketNews,IndustryResearch,PortfolioAnalysis}.tsx`
- Delete: `frontend/src/features/{evidence-center,fund-portfolio,industry,market-news,news,source-catalog,source-health,tags}/`
- Delete: `backend/{data_sources,evidence_verification,fund_data,industry_research,news_intelligence,news_pipeline,source_health}/`
- Delete: `backend/{fund_portfolio.py,news_translation.py,cache_management.py}` when the caller scan is empty.
- Modify: `backend/app.py`, `frontend/src/lib/api.ts`, `backend/requirements.txt`, and affected test inventories.

**Interfaces:**
- Consumes Tasks 1–4 as replacements.
- Produces no `/research-home`, `/market-news`, `/industry-research`, `/portfolio-analysis`, `/evidence-center`, `/api/data-sources`, `/api/evidence`, `/api/market-news`, `/api/fund-portfolio`, or `/api/industry-research` product surface.

- [ ] Run `rg` caller scans and update `removed-parallel-modules.md` before deletion.
- [ ] Extend route/API RED tests to reject every removed route and endpoint while confirming native routes/endpoints remain.
- [ ] Delete only caller-free directories, old page-only fixtures, acceptance launchers, and obsolete tests; retain historical docs/screenshots and every local user-data file.
- [ ] Remove stale imports/types/dependencies and verify `rg` has no production caller for removed modules or paths.
- [ ] Run the route/API tests, then targeted Backend and Frontend suites to GREEN.
- [ ] Commit the convergence deletion separately for auditable rollback.

### Task 6: Migrate source decisions and qualify real product mode

**Files:**
- Create: `docs/acceptance/native-convergence/news-source-migration.md`
- Create: `docs/acceptance/native-convergence/upstream-reference.md`
- Create: `docs/acceptance/native-convergence/targeted-test-results.md`

**Interfaces:**
- Produces one row per source with publisher, URL, type, track, upstream/current/A2 status, access/license/key state, and allowed conclusion.

- [ ] Generate the migration ledger from upstream/current JSON plus A2 repair decisions; collapse the two exact duplicate rows and keep the 106 canonical upstream sources.
- [ ] Mark 23 historical degraded observations truthfully (21 observation, one credential, one license) without relabeling them as current success.
- [ ] Run Backend source-manager/radar/API/signal tests, Frontend route/Intel/source-manager tests, and Legacy tests with isolated data paths.
- [ ] Start Backend on `127.0.0.1:8900` and Frontend on `127.0.0.1:5899` with isolated `.tmp/acceptance/native-convergence-real` data/cache/report roots and credential-like variables removed.
- [ ] Open `/intel/sources` in the default browser; verify content, source list, add/test/toggle/delete/reload, removed routes/nav, console/pageerror/request failures, and capture screenshots.
- [ ] Keep services running and stop for user acceptance. Do not claim `USER_HAS_SEEN_PAGE=YES` or `USER_ACCEPTANCE` until the user confirms.

### Task 7: Final engineering closeout after user acceptance

**Files:**
- Update: acceptance documents with final evidence only.

- [ ] Run Backend full offline, Frontend full, Legacy, production build, browser regression, secret scan, user-data before/after hash check, `git diff --check`, and independent code review.
- [ ] Fix any discovered bug with a new RED test before production code.
- [ ] Re-run every affected verification command and read the explicit exit code/summary.
- [ ] Commit final evidence and implementation, push only `codex/pp03-native-radar-convergence` without force, verify remote SHA equality, and stop.
