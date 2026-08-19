# PP03 A2-W1 Phase 1 Catalog and Source Families Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the canonical source Catalog, stable source-family aggregation, health overlay, and holdings-independent source page.

**Architecture:** Add a focused `backend/data_sources` package as the identity and catalog authority. Existing `source_health` remains the observation engine and is bridged by stable family/adapter/capability IDs; the frontend reads Catalog rows with health overlays instead of treating observed rows as the registry.

**Tech Stack:** Python 3 dataclasses/enums, FastAPI, pytest, React 19, TypeScript, Vitest, Testing Library.

**Spec:** `docs/superpowers/specs/2026-08-19-pp03-a2-multi-source-provider-hub-design.md`

## Global Constraints

- Baseline is exactly `9dfd8246290a04555ed1886ded92c39de9a001f4`; implementation branch is `codex/pp03-a2-multi-source-provider-hub`.
- Catalog identity and registration counts must not depend on portfolio holdings.
- The 108 existing RSS/Atom configurations remain registered; Phase 1 does not change `backend/news_sources.json`.
- Eastmoney direct, AKShare/Eastmoney, and efinance/Eastmoney are one source family and never independent evidence.
- `configured_reference` is always available from Catalog; `observed_final_reference` is observation-only and redacted.
- Missing health observations render `尚未体检`; unconfigured/license states are not health failures.
- No API response exposes credentials, authorization headers, cookies, tokens, or sensitive query parameters.
- Existing `/api/source-health/*` consumers and the original five pages must remain compatible.
- Use TDD: capture RED before production edits, then focused GREEN, deterministic backend regression, frontend full tests, legacy tests, and production build.

---

### Task 1: Canonical Catalog models and static registry

**Files:**
- Create: `backend/data_sources/__init__.py`
- Create: `backend/data_sources/models.py`
- Create: `backend/data_sources/catalog.py`
- Create: `backend/data_sources/provider_contract.py`
- Test: `backend/tests/test_data_source_catalog.py`

**Interfaces:**
- Consumes: `source_health.registry.load_news_config(path=None) -> dict[str, Any]`.
- Produces: `BillingModel`, `SourceRole`, `CatalogStatus`, `SourceFamily`, `AdapterDescriptor`, `CapabilityDescriptor`, `ProviderValue`, `ProviderAdapter`, and `DataSourceCatalog`.
- Produces: `build_catalog(news_config: Mapping[str, Any] | None = None) -> DataSourceCatalog`.

- [ ] **Step 1: Write the failing Catalog identity tests**

```python
def test_catalog_registers_108_news_adapters_without_holdings():
    catalog = build_catalog(news_config=load_news_config())
    assert len([row for row in catalog.adapters if "feed" in row.capability_ids]) == 108
    assert catalog.registration_fingerprint(holding_ids=[]) == catalog.registration_fingerprint(holding_ids=["017811"])


def test_eastmoney_access_paths_share_one_family():
    catalog = build_catalog(news_config={"sources": []})
    family = catalog.family("eastmoney")
    assert {row.adapter_id for row in catalog.adapters_for_family(family.source_family_id)} == {
        "eastmoney-direct", "akshare-eastmoney", "efinance-eastmoney"
    }
    assert family.independent_evidence_eligible is False
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_catalog.py -q -p no:cacheprovider`

Expected: collection fails with `ModuleNotFoundError: No module named 'data_sources'`.

- [ ] **Step 3: Implement the exact Catalog types**

```python
class BillingModel(str, Enum):
    FREE_NO_KEY = "free_no_key"
    FREE_KEY = "free_key"
    FREEMIUM = "freemium"
    PAID_API = "paid_api"
    ENTERPRISE_LICENSE = "enterprise_license"
    INTERNAL_ONLY = "internal_only"


class CatalogStatus(str, Enum):
    CONNECTED = "connected"
    CONFIGURED = "configured"
    UNCONFIGURED = "unconfigured"
    CATALOG_ONLY = "catalog_only"
    LICENSE_REQUIRED = "license_required"
    DISABLED = "disabled"


class SourceRole(str, Enum):
    OFFICIAL_EVIDENCE = "official_evidence"
    PRIMARY_DATA = "primary_data"
    FALLBACK_DATA = "fallback_data"
    CROSS_CHECK = "cross_check"
    MACRO_DATA = "macro_data"
    MARKET_DATA = "market_data"
    NEWS_PUBLISHER = "news_publisher"
    INDUSTRY_MEDIA = "industry_media"
    COLLECTOR = "collector"
    CANDIDATE = "candidate"


@dataclass(frozen=True, slots=True)
class SourceFamily:
    source_family_id: str
    source_family_name: str
    region: str
    market: str
    source_roles: tuple[SourceRole, ...]
    independent_evidence_eligible: bool
    commercial_use_status: str
    catalog_status: CatalogStatus
    health_status: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    capability_id: str
    capability_name: str
    data_category: str
    freshness_max_age_seconds: int | None
    probe_enabled: bool
    unit_policy: str
    frequency_policy: str
    primary_families: tuple[str, ...]
    fallback_families: tuple[str, ...]
    cross_check_families: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    adapter_id: str
    adapter_name: str
    source_family_id: str
    provider_type: str
    source_roles: tuple[SourceRole, ...]
    capability_ids: tuple[str, ...]
    billing_model: BillingModel
    auth_type: str
    credential_env_names: tuple[str, ...]
    default_enabled: bool
    license_note: str
    usage_note: str
    data_delay: str
    quota_policy: str
    cost_policy: str
    configured_reference: str
    current_provider_priority: int
    catalog_status: CatalogStatus


@dataclass(frozen=True, slots=True)
class ProviderValue:
    value: object
    source_family_id: str
    adapter_id: str
    capability_id: str
    as_of_date: date | None
    fetched_at: datetime
    data_status: str
    license: str
    priority: int
    difference_from_primary: Decimal | None
    unit: str
    frequency: str


class DataSourceCatalog:
    @property
    def families(self) -> tuple[SourceFamily, ...]: ...
    @property
    def adapters(self) -> tuple[AdapterDescriptor, ...]: ...
    @property
    def capabilities(self) -> tuple[CapabilityDescriptor, ...]: ...
    def family(self, family_id: str) -> SourceFamily: ...
    def adapter(self, adapter_id: str) -> AdapterDescriptor: ...
    def adapters_for_family(self, family_id: str) -> tuple[AdapterDescriptor, ...]: ...
    def registration_fingerprint(self, holding_ids: Sequence[str] = ()) -> str: ...
```

Validate unique family IDs, unique adapter IDs, known family references, known capability IDs, and stable sorted serialization in `DataSourceCatalog.__init__`. `registration_fingerprint` intentionally ignores `holding_ids`; the parameter exists only to prove holdings cannot change registry identity.

- [ ] **Step 4: Register Phase 1 families and all existing news sources**

Create deterministic family records for `eastmoney`, `tencent`, `cninfo`, and `danjuan`. Register the three Eastmoney access paths, existing Tencent/CNInfo/Danjuan adapters, and a stable news family/adapter per configured publisher. Derive news IDs from the existing exact five-field configuration identity, not from holdings or runtime observations.

`efinance-eastmoney` must be `default_enabled=False`, `catalog_status=disabled`, and must include its non-independent and license-use notes.

- [ ] **Step 5: Define the Provider Adapter protocol**

```python
@dataclass(frozen=True, slots=True)
class ProviderRequest:
    capability_id: str
    parameters: Mapping[str, object]


class ProviderAdapter(Protocol):
    descriptor: AdapterDescriptor

    def fetch(self, request: ProviderRequest) -> tuple[ProviderValue, ...]: ...
    def probe(self, capability_id: str) -> Mapping[str, object]: ...
```

The protocol contains no provider-specific parsing and no credential values. Later provider modules implement it independently.

- [ ] **Step 6: Run focused tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_catalog.py -q -p no:cacheprovider`

Expected: all Catalog tests pass, including exact 108-news count and holdings-independent fingerprint.

Commit:

```powershell
git add backend/data_sources backend/tests/test_data_source_catalog.py
git commit -m "feat(pp03): establish canonical source catalog"
```

---

### Task 2: Stable health bridge and dual public references

**Files:**
- Create: `backend/data_sources/health_bridge.py`
- Create: `backend/data_sources/references.py`
- Modify: `backend/source_health/models.py`
- Modify: `backend/source_health/registry.py`
- Modify: `backend/source_health/runner.py`
- Modify: `backend/source_health/service.py`
- Test: `backend/tests/test_data_source_health_bridge.py`
- Test: `backend/tests/test_source_health_registry.py`
- Test: `backend/tests/test_source_health_runner.py`
- Test: `backend/tests/test_source_health_api.py`

**Interfaces:**
- Consumes: `DataSourceCatalog`, existing `ProbeObservation`, existing provider/news probes.
- Produces: `HealthOverlay`, `catalog_probe_descriptors(catalog, providers, news_config)`, and `merge_health(catalog, observations)`.
- Extends: `SourceDescriptor` and `ProbeObservation` with stable identity and dual reference fields.

- [ ] **Step 1: Write failing stable-ID and reference tests**

```python
def test_bridge_preserves_configured_reference_on_failed_probe():
    observation = failed_observation(final_reference=None)
    row = merge_health(catalog, [observation]).adapter("eastmoney-direct").capability("stock_snapshot")
    assert row.configured_reference == "https://fund.eastmoney.com/"
    assert row.observed_final_reference is None


def test_runtime_source_name_does_not_change_family_identity():
    first = descriptor_for(provider_name="东方财富基金档案")
    second = descriptor_for(provider_name="东方财富-天天基金")
    assert first.source_family_id == second.source_family_id == "eastmoney"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_health_bridge.py backend\tests\test_source_health_registry.py backend\tests\test_source_health_runner.py -q -p no:cacheprovider`

Expected: failures show missing `source_family_id`, `adapter_id`, `capability_id`, `configured_reference`, and `observed_final_reference`.

- [ ] **Step 3: Extend the health contracts compatibly**

Add these required fields to `SourceDescriptor`:

```python
source_family_id: str
adapter_id: str
capability_id: str
configured_reference: str
```

Keep `source_id`, `source_name`, `group`, `capability`, and `source_reference` during Phase 1 compatibility. Make `source_reference == configured_reference` for newly built descriptors.

Add to `ProbeObservation`:

```python
configured_reference: str
observed_final_reference: str | None
```

Keep serialized `final_reference` as a compatibility alias of `observed_final_reference` until all frontend consumers migrate.

- [ ] **Step 4: Build descriptors from Catalog identity**

Replace provider-name-derived source IDs with `f"{adapter_id}:{capability_id}"`. Map current provider classes by their stable adapter IDs. Build news descriptors from Catalog adapters and keep the current exact configuration-identity collision protection.

- [ ] **Step 5: Implement health overlay aggregation**

```python
@dataclass(frozen=True, slots=True)
class HealthOverlay:
    probe_status: str | None
    health_status: str
    observed_final_reference: str | None
    last_success_at: str | None
    latency_ms: int | None
    error_type: str | None
    error_message_redacted: str


def family_health(rows: Sequence[HealthOverlay], catalog_statuses: Sequence[CatalogStatus]) -> str:
    # no observations -> unexamined; mixed success/failure -> partial_degraded
```

Exclude `unconfigured`, `license_required`, `catalog_only`, and `disabled` adapters from the failure denominator.

- [ ] **Step 6: Run focused and compatibility suites**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_health_bridge.py backend\tests\test_source_health_registry.py backend\tests\test_source_health_runner.py backend\tests\test_source_health_api.py -q -p no:cacheprovider`

Expected: all tests pass; existing source-health response fields remain present.

- [ ] **Step 7: Commit the bridge**

```powershell
git add backend/data_sources/health_bridge.py backend/source_health backend/tests/test_data_source_health_bridge.py backend/tests/test_source_health_registry.py backend/tests/test_source_health_runner.py backend/tests/test_source_health_api.py
git commit -m "feat(pp03): bridge catalog identity into source health"
```

---

### Task 3: Catalog service and read APIs

**Files:**
- Create: `backend/data_sources/service.py`
- Create: `backend/data_sources/api.py`
- Modify: `backend/app.py`
- Test: `backend/tests/test_data_source_api.py`

**Interfaces:**
- Consumes: `build_catalog()`, `SourceHealthService.list_sources()`, and `SourceHealthService.start_run()`.
- Produces: `DataSourceService.catalog_document()`, `families_document()`, `family_document(family_id)`, `capabilities_document()`, and `refresh()`.
- Produces the required `/api/data-sources/*` read and refresh endpoints; enable/disable/validate return honest Phase 1 capability states and are expanded in Phase 3.

- [ ] **Step 1: Write failing API contract tests**

```python
def test_catalog_api_is_complete_without_portfolio(client, empty_portfolio):
    response = client.get("/api/data-sources/catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["registration"]["news_sources"] == 108
    assert body["portfolio_relation"]["status"] == "unavailable_no_holdings"


def test_family_api_aggregates_eastmoney(client):
    body = client.get("/api/data-sources/families/eastmoney").json()
    assert body["source_family_id"] == "eastmoney"
    assert len(body["adapters"]) == 3
```

- [ ] **Step 2: Run API tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_api.py -q -p no:cacheprovider`

Expected: 404 responses for `/api/data-sources/*`.

- [ ] **Step 3: Implement the service response contracts**

```python
class DataSourceService:
    def catalog_document(self) -> dict[str, object]: ...
    def families_document(self) -> list[dict[str, object]]: ...
    def family_document(self, family_id: str) -> dict[str, object]: ...
    def capabilities_document(self) -> list[dict[str, object]]: ...
    def refresh(self) -> dict[str, object]: ...
```

`catalog_document` returns Catalog registration counts separately from observed counts. A family/adapter/capability with no overlay returns `health_status="unexamined"`, never zero-valued fake health observations.

- [ ] **Step 4: Add an APIRouter and preserve existing routes**

Mount `data_sources.api.router` in `backend/app.py`. Return 404 for unknown family/adapter IDs, 409 for conflicting refresh runs, and redacted validation errors. In Phase 1, enable/disable only changes adapters whose Catalog contract allows local toggling; credentialed and licensed adapters return their real configuration barrier.

- [ ] **Step 5: Run focused API and existing app tests**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_api.py backend\tests\test_source_health_api.py backend\tests\test_api.py -q -p no:cacheprovider`

Expected: new and compatibility APIs pass.

- [ ] **Step 6: Commit the API**

```powershell
git add backend/data_sources/service.py backend/data_sources/api.py backend/app.py backend/tests/test_data_source_api.py
git commit -m "feat(pp03): expose source catalog api"
```

---

### Task 4: Holdings-independent Catalog UI and family cards

**Files:**
- Create: `frontend/src/features/source-catalog/types.ts`
- Create: `frontend/src/features/source-catalog/SourceCatalogWorkspace.tsx`
- Create: `frontend/src/features/source-catalog/SourceFamilyCard.tsx`
- Create: `frontend/src/features/source-catalog/SourceCatalogFilters.tsx`
- Create: `frontend/src/features/source-catalog/__tests__/SourceCatalogWorkspace.test.tsx`
- Modify: `frontend/src/features/source-health/SourceHealthWorkspace.tsx`
- Modify: `frontend/src/features/source-health/SourceHealthDrawer.tsx`
- Modify: `frontend/src/features/source-health/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/features/source-health/__tests__/SourceHealthWorkspace.test.tsx`
- Test: `frontend/src/__tests__/CorePages.test.tsx`

**Interfaces:**
- Consumes: `GET /api/data-sources/catalog`, `families`, and family detail.
- Produces: `DataSourceCatalogResponse`, `SourceFamilyView`, `AdapterView`, and `CapabilityView` TypeScript contracts.
- `SourceHealthWorkspace` delegates its list area to `SourceCatalogWorkspace` while retaining the existing compact health summary.

- [ ] **Step 1: Write failing UI behavior tests**

```tsx
it("shows the complete catalog with no holdings", async () => {
  renderWorkspace({ portfolio: [], catalog: catalogWith108News });
  expect(await screen.findByText("东方财富数据家族")).toBeInTheDocument();
  expect(screen.getByText("108 个资讯来源")).toBeInTheDocument();
  expect(screen.getByText("暂无基金持仓，无法建立关联")).toBeInTheDocument();
});

it("expands eastmoney adapters without duplicating family cards", async () => {
  renderWorkspace({ catalog: eastmoneyCatalog });
  expect(screen.getAllByLabelText("来源家族 东方财富数据家族")).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "展开接入方式 东方财富数据家族" }));
  expect(screen.getByText("AKShare／东方财富")).toBeInTheDocument();
  expect(screen.getByText("东方财富直连")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run focused UI tests and verify RED**

Run: `npm run test:run -- src/features/source-catalog/__tests__/SourceCatalogWorkspace.test.tsx src/features/source-health/__tests__/SourceHealthWorkspace.test.tsx`

Workdir: `frontend`

Expected: missing module/API/type failures.

- [ ] **Step 3: Add typed API clients**

```ts
dataSourceCatalog: () => get<DataSourceCatalogResponse>("/data-sources/catalog"),
dataSourceFamilies: () => get<SourceFamilyView[]>("/data-sources/families"),
dataSourceFamily: (familyId: string) => get<SourceFamilyView>(`/data-sources/families/${encodeURIComponent(familyId)}`),
dataSourceRefresh: () => request<SourceHealthRunStarted>("/data-sources/refresh", "POST"),
```

Keep current `sourceHealthSummary`, `sourceHealthSources`, and run polling clients for compatibility.

- [ ] **Step 4: Implement family-first rendering**

Render each family once, followed by adapter and capability rows only after expansion. Use Catalog status labels independent of health status. Always render configured references as safe external links; render observed references only when supplied. Show `尚未体检` when no overlay exists.

Filters must cover family name, Adapter name, capability, billing model, Catalog status, and health status. Portfolio relations are supplementary badges and never filter registration.

- [ ] **Step 5: Preserve drawer and accessibility behavior**

Update the detail drawer to show stable family/adapter/capability IDs and both public references. Keep focus return, Escape, backdrop close, Tab containment, and single-source detail behavior.

- [ ] **Step 6: Run focused, full, legacy, and build verification**

Run in `frontend`:

```powershell
npm run test:run -- src/features/source-catalog/__tests__/SourceCatalogWorkspace.test.tsx src/features/source-health/__tests__/SourceHealthWorkspace.test.tsx src/__tests__/CorePages.test.tsx
npm run test:run
npm run test:legacy
npm run build
```

Expected: all tests pass; the build may report only the already-known bundle-size warning, not errors.

- [ ] **Step 7: Commit the UI**

```powershell
git add frontend/src/features/source-catalog frontend/src/features/source-health frontend/src/lib/api.ts frontend/src/__tests__/CorePages.test.tsx
git commit -m "feat(pp03): render catalog by source family"
```

---

### Task 5: Phase 1 documentation, regression, and review gate

**Files:**
- Create: `docs/data-sources/source-family-matrix.md`
- Create: `docs/data-sources/source-before-after.md`
- Modify: `docs/data-sources/source-family-matrix.md`
- Modify: `docs/data-sources/source-before-after.md`

**Interfaces:**
- Consumes: current Catalog serialization and focused test evidence.
- Produces: Phase 1 identity matrix and baseline Before/After counts used by Phase 4 closeout.

- [ ] **Step 1: Generate deterministic matrix assertions**

Add a backend test that serializes the Catalog and asserts unique family/adapter IDs, exact 108 news registration, one Eastmoney family, and registration fingerprint equality before/after adding a sample fund relation.

- [ ] **Step 2: Run deterministic backend regression**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests -m "not live" -q -p no:cacheprovider`

Expected: all deterministic backend tests pass; live tests are deselected, not represented as offline success.

- [ ] **Step 3: Write Phase 1 evidence documents**

Record each family, Adapter, capability, role, billing type, Key requirement, license boundary, default status, actual connection status, shell-only status, limits, and replacement relationship. Record baseline and Phase 1 counts separately; do not label catalog registration as connected data.

- [ ] **Step 4: Perform the scoped review**

Inspect `git diff 9dfd824..HEAD` plus uncommitted Phase 1 docs for identity drift, sensitive URLs, holdings coupling, duplicated Eastmoney cards, and compatibility breaks. Run `git diff --check` and a secret-pattern scan over only changed files. Fix findings through RED/GREEN before proceeding.

- [ ] **Step 5: Commit Phase 1 qualification**

```powershell
git add docs/data-sources/source-family-matrix.md docs/data-sources/source-before-after.md backend/tests/test_data_source_catalog.py
git commit -m "docs(pp03): qualify source catalog phase"
```

Checkpoint: tracked worktree clean; no push, PR, merge, live paid request, or Phase 2 production edit has occurred.
