# PP03 A2-W1 Phase 3 Credentialed, Paid, and Enterprise Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement secure credential state, Free-only and budget enforcement, independent keyed/paid Provider adapters, and an honest enterprise-license Catalog.

**Architecture:** Credentials and budgets are server-side services that authorize a Provider request before any network call. Keyed and paid providers retain separate request/parsing modules; enterprise providers remain capability-rich Catalog entries until a future licensed Work supplies SDKs and credentials.

**Tech Stack:** Python, system Keyring/Windows Credential Manager abstraction, FastAPI, pytest, React/TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-08-19-pp03-a2-multi-source-provider-hub-design.md`

## Global Constraints

- Default `free_only=true` and all paid/enterprise adapters are disabled.
- Missing credentials produce `unconfigured`, no network call, no health failure, and no abnormal log.
- Credentials never enter Git, logs, screenshots, responses, localStorage, URL query parameters, acceptance documents, or plaintext databases.
- Production credential priority is Windows Credential Manager/system Keyring, then read-only environment variables, then a Git-ignored secure local backend only when explicitly configured.
- No paid request unless the user explicitly enables it, a credential validates, Free-only is off, and daily/monthly/request budgets pass.
- No test automatically purchases, renews, upgrades, or incurs cost. Live paid tests additionally require `VR_ALLOW_PAID_PROVIDER_TESTS=1`.
- Enterprise SDKs are not added without a real license and separate Work.
- Each Provider adapter is independent; shared transport/authorization helpers must not contain provider-specific parsing.

---

### Task 1: Credential store and redacted configuration state

**Files:**
- Create: `backend/data_sources/credentials.py`
- Create: `backend/data_sources/config_store.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_data_source_credentials.py`
- Test: `backend/tests/test_data_source_config_store.py`

**Interfaces:**
- Produces: `CredentialStore`, `KeyringCredentialStore`, `EnvironmentCredentialStore`, `MemoryCredentialStore`, `CredentialState`, and `DataSourceConfigStore`.
- Consumes: Adapter `credential_env_names` and `%VR_DATA_DIR%` for non-secret settings.

- [ ] **Step 1: Write failing credential-secrecy tests**

```python
def test_credential_state_never_serializes_secret(memory_credentials):
    memory_credentials.set("fred", "FRED_API_KEY", "secret-value")
    document = memory_credentials.state("fred").to_dict()
    assert document == {
        "configured": True,
        "status": "stored",
        "last_validated_at": None,
        "credential_source": "memory",
    }
    assert "secret-value" not in json.dumps(document)


def test_environment_store_is_read_only(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "secret-value")
    store = EnvironmentCredentialStore()
    assert store.get("FRED_API_KEY") == "secret-value"
    with pytest.raises(CredentialWriteNotSupported):
        store.set("FRED_API_KEY", "replacement")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_credentials.py backend\tests\test_data_source_config_store.py -q -p no:cacheprovider`

Expected: missing credential/config modules.

- [ ] **Step 3: Implement the credential abstraction**

```python
@dataclass(frozen=True, slots=True)
class CredentialState:
    configured: bool
    status: str
    last_validated_at: str | None
    credential_source: str


class CredentialStore(Protocol):
    def get(self, adapter_id: str, env_name: str) -> str | None: ...
    def set(self, adapter_id: str, env_name: str, value: str) -> None: ...
    def delete(self, adapter_id: str, env_name: str) -> None: ...
    def state(self, adapter_id: str) -> CredentialState: ...
```

Use service names scoped to PP03 and Adapter ID. Do not expose a method that lists raw secrets. Add the system Keyring dependency; if the OS backend is unavailable, the service reports `credential_store_unavailable` rather than writing plaintext.

- [ ] **Step 4: Persist only non-secret configuration**

`DataSourceConfigStore` atomically writes enable flags, Free-only, budgets, usage mode, and validation timestamps under `%VR_DATA_DIR%\data-sources\v1\config.json`. Reject unknown Adapter IDs and numeric booleans/fractions for integer budget counters.

- [ ] **Step 5: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_credentials.py backend\tests\test_data_source_config_store.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/credentials.py backend/data_sources/config_store.py backend/requirements.txt backend/tests/test_data_source_credentials.py backend/tests/test_data_source_config_store.py
git commit -m "feat(pp03): secure provider credential state"
```

---

### Task 2: Budget guard, usage, and cost ledger

**Files:**
- Create: `backend/data_sources/budgets.py`
- Create: `backend/data_sources/usage_store.py`
- Test: `backend/tests/test_data_source_budgets.py`
- Test: `backend/tests/test_data_source_usage.py`

**Interfaces:**
- Produces: `BudgetPolicy`, `BudgetDecision`, `BudgetGuard.authorize()`, `UsageRecord`, and `UsageStore`.
- Consumes: Adapter billing model, enabled/configured state, Free-only, estimated cost, daily/monthly budgets.

- [ ] **Step 1: Write failing preflight tests**

```python
def test_free_only_blocks_paid_before_network():
    decision = guard.authorize(paid_adapter, estimated_cost=Decimal("0.01"), now=NOW)
    assert decision.allowed is False
    assert decision.reason == "free_only"
    assert transport.calls == []


@pytest.mark.parametrize("used_field,reason", [("daily_used", "daily_budget_exhausted"), ("monthly_used", "monthly_budget_exhausted")])
def test_exhausted_budget_blocks_request(used_field, reason):
    assert guard_with_usage(used_field).authorize(paid_adapter, Decimal("0.01"), NOW).reason == reason
```

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_budgets.py backend\tests\test_data_source_usage.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement decimal budget checks and atomic usage**

```python
class BudgetGuard:
    def authorize(self, adapter: AdapterDescriptor, *, estimated_cost: Decimal, now: datetime) -> BudgetDecision: ...
    def record(self, adapter_id: str, *, actual_cost: Decimal, request_count: int, now: datetime) -> None: ...
```

Require explicit enabled/configured status, `free_only=False`, positive daily/monthly budgets, and request estimate within remaining budget. Store decimal strings, never floats. Record request count, status, units, and cost without request secrets or user data.

- [ ] **Step 4: Test restart, boundary, and concurrency behavior**

Add exact-boundary, one-cent-over, midnight/month rollover, corrupt ledger fail-closed, and concurrent authorization tests. Reserve estimated cost before transport and reconcile actual cost after response so parallel calls cannot overspend.

- [ ] **Step 5: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_budgets.py backend\tests\test_data_source_usage.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/budgets.py backend/data_sources/usage_store.py backend/tests/test_data_source_budgets.py backend/tests/test_data_source_usage.py
git commit -m "feat(pp03): enforce provider cost budgets"
```

---

### Task 3: FRED, EIA, and Tushare Pro adapters

**Files:**
- Create: `backend/data_sources/providers/fred.py`
- Create: `backend/data_sources/providers/eia.py`
- Create: `backend/data_sources/providers/tushare.py`
- Modify: `backend/data_sources/catalog.py`
- Modify: `backend/data_sources/provider_registry.py`
- Test: `backend/tests/test_provider_fred.py`
- Test: `backend/tests/test_provider_eia.py`
- Test: `backend/tests/test_provider_tushare.py`

**Interfaces:**
- Consumes: `SafeHttpClient`, `CredentialStore`, `BudgetGuard` for status-only authorization, and Provider request/value contracts.
- Produces: `FredAdapter`, `EiaAdapter`, and `TushareAdapter`.

- [ ] **Step 1: Write failing no-Key and parser tests**

Every adapter test must assert zero transport calls when its environment credential is absent. FRED preserves series ID, observation/realtime dates, unit and frequency; EIA preserves series, unit, frequency and forecast/actual status; Tushare records per-Capability account permission and does not classify permission denial as Provider failure.

```python
def test_tushare_capability_denial_is_account_permission_not_failure():
    result = TushareAdapter(client=permission_denied_client, credentials=configured).probe("fund_holdings")
    assert result["status"] == "plan_unavailable"
    assert result["health_failure"] is False
```

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_provider_fred.py backend\tests\test_provider_eia.py backend\tests\test_provider_tushare.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement separate request builders/parsers**

Each module owns its endpoints and response schema. The shared credential helper may inject the secret into a header/parameter at transport time but must not mutate configured public references or serialize the request URL. Mark adapters `unconfigured` before request construction when credentials are absent.

- [ ] **Step 4: Cover timeout, rate limit, empty, schema, and cache behavior**

Add explicit tests for all taskbook failure modes and for source family/date/unit metadata.

- [ ] **Step 5: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_provider_fred.py backend\tests\test_provider_eia.py backend\tests\test_provider_tushare.py backend\tests\test_data_source_credentials.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/providers/fred.py backend/data_sources/providers/eia.py backend/data_sources/providers/tushare.py backend/data_sources/catalog.py backend/data_sources/provider_registry.py backend/tests/test_provider_fred.py backend/tests/test_provider_eia.py backend/tests/test_provider_tushare.py
git commit -m "feat(pp03): add configured macro and china adapters"
```

---

### Task 4: Freemium market and news adapters

**Files:**
- Create: `backend/data_sources/providers/alpha_vantage.py`
- Create: `backend/data_sources/providers/finnhub.py`
- Create: `backend/data_sources/providers/twelve_data.py`
- Create: `backend/data_sources/providers/nasdaq_data_link.py`
- Create: `backend/data_sources/providers/news_api.py`
- Modify: `backend/data_sources/catalog.py`
- Modify: `backend/data_sources/provider_registry.py`
- Test: `backend/tests/test_provider_alpha_vantage.py`
- Test: `backend/tests/test_provider_finnhub.py`
- Test: `backend/tests/test_provider_twelve_data.py`
- Test: `backend/tests/test_provider_nasdaq_data_link.py`
- Test: `backend/tests/test_provider_news_api.py`

**Interfaces:**
- Produces five independent Provider adapters with actual account-plan status.
- NewsAPI output always returns the original publisher identity and `collector` role.

- [ ] **Step 1: Write failing contract tests per Provider**

Test no-Key short-circuit, normal/empty/timeout/auth/rate-limit/plan-denied/schema/cache, date, unit, quota and source metadata. Nasdaq Data Link must distinguish free dataset codes from Premium entitlement. NewsAPI must not count itself as `content_source` or an independent publisher.

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_provider_alpha_vantage.py backend\tests\test_provider_finnhub.py backend\tests\test_provider_twelve_data.py backend\tests\test_provider_nasdaq_data_link.py backend\tests\test_provider_news_api.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement independent modules**

Alpha Vantage is low-frequency fallback; Finnhub news never replaces SEC evidence; Twelve Data stores actual plan availability; Nasdaq Data Link stores dataset code and entitlement; NewsAPI returns discovery candidates tied to original publisher URL and delay metadata.

- [ ] **Step 4: Run focused tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_provider_alpha_vantage.py backend\tests\test_provider_finnhub.py backend\tests\test_provider_twelve_data.py backend\tests\test_provider_nasdaq_data_link.py backend\tests\test_provider_news_api.py backend\tests\test_data_source_routing.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/providers/alpha_vantage.py backend/data_sources/providers/finnhub.py backend/data_sources/providers/twelve_data.py backend/data_sources/providers/nasdaq_data_link.py backend/data_sources/providers/news_api.py backend/data_sources/catalog.py backend/data_sources/provider_registry.py backend/tests/test_provider_alpha_vantage.py backend/tests/test_provider_finnhub.py backend/tests/test_provider_twelve_data.py backend/tests/test_provider_nasdaq_data_link.py backend/tests/test_provider_news_api.py
git commit -m "feat(pp03): add freemium provider adapters"
```

---

### Task 5: Paid API adapters and enterprise Catalog

Provider mapping is exact:

- `fmp` / `FmpAdapter` = Financial Modeling Prep.
- `massive` / `MassiveAdapter` = Polygon/Massive.
- `tiingo` / `TiingoAdapter` = Tiingo.
- `eodhd` / `EodhdAdapter` = EODHD.
- `databento` / `DatabentoAdapter` = Databento.
- Enterprise families are Bloomberg Data License/B-PIPE, LSEG Data Platform/Workspace, FactSet, Wind, Choice, iFinD, Morningstar Direct, S&P Capital IQ, and CSMAR.

**Files:**
- Create: `backend/data_sources/providers/fmp.py`
- Create: `backend/data_sources/providers/massive.py`
- Create: `backend/data_sources/providers/tiingo.py`
- Create: `backend/data_sources/providers/eodhd.py`
- Create: `backend/data_sources/providers/databento.py`
- Create: `backend/data_sources/enterprise_catalog.py`
- Modify: `backend/data_sources/catalog.py`
- Modify: `backend/data_sources/provider_registry.py`
- Test: `backend/tests/test_paid_provider_adapters.py`
- Test: `backend/tests/test_enterprise_provider_catalog.py`

**Interfaces:**
- Produces one module/class per paid Provider and static enterprise descriptors.
- Consumes: credential and budget authorization before transport.

- [ ] **Step 1: Write failing paid-adapter contract tests**

Parameterize shared behavior while instantiating each real class. Assert unique endpoints/parsers, default disabled, no-Key no-call, Free-only no-call, budget no-call, plan unavailable, normal fixture parsing, and usage recording. No Live calls occur in this test.

- [ ] **Step 2: Write failing enterprise honesty tests**

```python
@pytest.mark.parametrize("family_id", ["bloomberg", "lseg", "factset", "wind", "choice", "ifind", "morningstar-direct", "sp-capital-iq", "csmar"])
def test_enterprise_source_requires_license_and_is_not_healthy(family_id):
    catalog = build_enterprise_catalog()
    row = catalog.family(family_id)
    adapter = catalog.adapters_for_family(family_id)[0]
    assert row.catalog_status == CatalogStatus.LICENSE_REQUIRED
    assert row.health_status is None
    assert adapter.default_enabled is False
```

- [ ] **Step 3: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_paid_provider_adapters.py backend\tests\test_enterprise_provider_catalog.py -q -p no:cacheprovider`

- [ ] **Step 4: Implement paid Adapter shells with real parsers against Mock fixtures**

Each Provider module implements its own request builder and parser. All calls pass `CredentialStore` and `BudgetGuard` before transport. Do not add enterprise SDK dependencies or web scraping.

- [ ] **Step 5: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_paid_provider_adapters.py backend\tests\test_enterprise_provider_catalog.py backend\tests\test_data_source_budgets.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/providers/fmp.py backend/data_sources/providers/massive.py backend/data_sources/providers/tiingo.py backend/data_sources/providers/eodhd.py backend/data_sources/providers/databento.py backend/data_sources/enterprise_catalog.py backend/data_sources/catalog.py backend/data_sources/provider_registry.py backend/tests/test_paid_provider_adapters.py backend/tests/test_enterprise_provider_catalog.py
git commit -m "feat(pp03): catalog paid and enterprise providers"
```

---

### Task 6: Configuration, enablement, usage, and cost APIs

**Files:**
- Modify: `backend/data_sources/service.py`
- Modify: `backend/data_sources/api.py`
- Test: `backend/tests/test_data_source_config_api.py`
- Test: `backend/tests/test_data_source_cost_api.py`

**Interfaces:**
- Implements: required enable/disable/validate/config/usage/cost endpoints.
- Adds: `PUT /api/data-sources/{adapter_id}/credentials` and `DELETE /api/data-sources/{adapter_id}/credentials`.

- [ ] **Step 1: Write failing API secrecy and budget tests**

Assert credential PUT returns only state, GET config contains no secret, unknown IDs are 404, licensed source enable is 409, missing Key validation is a no-call `unconfigured`, paid enable without budgets is 409, and usage/cost responses contain decimal strings and period metadata.

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_config_api.py backend\tests\test_data_source_cost_api.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement API orchestration**

Use FastAPI secret request models whose `repr` hides values. Do not echo the body in exceptions. Validation resolves credentials server-side, performs one bounded Provider validation call when permitted, and records only status/time/source.

- [ ] **Step 4: Run focused API tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_config_api.py backend\tests\test_data_source_cost_api.py backend\tests\test_data_source_api.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/service.py backend/data_sources/api.py backend/tests/test_data_source_config_api.py backend/tests/test_data_source_cost_api.py
git commit -m "feat(pp03): expose safe provider configuration"
```

---

### Task 7: Provider configuration and cost UI

**Files:**
- Create: `frontend/src/features/source-catalog/SourceConfigurationDrawer.tsx`
- Create: `frontend/src/features/source-catalog/CostBudgetPanel.tsx`
- Create: `frontend/src/features/source-catalog/__tests__/SourceConfigurationDrawer.test.tsx`
- Create: `frontend/src/features/source-catalog/__tests__/CostBudgetPanel.test.tsx`
- Modify: `frontend/src/features/source-catalog/types.ts`
- Modify: `frontend/src/features/source-catalog/SourceFamilyCard.tsx`
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Consumes: redacted config/usage/cost APIs and one-time credential mutation APIs.
- Produces: accessible configuration drawer with no client-side secret persistence.

- [ ] **Step 1: Write failing UI tests**

Test labels for 未配置/当前套餐不可用/需要许可证, enterprise buttons limited to 配置许可证/查看能力, default Free-only, budget-required paid enable, secret input cleared after submit, no secret in localStorage or rendered response, and focus-trapped drawer behavior.

- [ ] **Step 2: Run tests and verify RED**

Run in `frontend`: `npm run test:run -- src/features/source-catalog/__tests__/SourceConfigurationDrawer.test.tsx src/features/source-catalog/__tests__/CostBudgetPanel.test.tsx`

- [ ] **Step 3: Implement typed clients and UI**

Credential state types contain only `configured`, `status`, `last_validated_at`, and `credential_source`. The input uses password masking and is cleared on success/failure/unmount. Paid enable remains disabled until budgets are valid and explicit confirmation is checked.

- [ ] **Step 4: Run focused/full/legacy/build and commit**

Run in `frontend`:

```powershell
npm run test:run -- src/features/source-catalog/__tests__/SourceConfigurationDrawer.test.tsx src/features/source-catalog/__tests__/CostBudgetPanel.test.tsx
npm run test:run
npm run test:legacy
npm run build
```

Commit:

```powershell
git add frontend/src/features/source-catalog frontend/src/lib/api.ts
git commit -m "feat(pp03): add provider configuration controls"
```

---

### Task 8: Phase 3 documentation, regression, and review gate

**Files:**
- Create: `docs/data-sources/freemium-provider-matrix.md`
- Create: `docs/data-sources/paid-provider-matrix.md`
- Create: `docs/data-sources/enterprise-provider-catalog.md`
- Modify: `docs/data-sources/licensing-and-usage-boundaries.md`
- Modify: `docs/data-sources/source-before-after.md`

**Interfaces:**
- Produces honest matrix rows for connected/configured/unconfigured/catalog-only/license-required/disabled states.

- [ ] **Step 1: Run deterministic backend regression**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests -m "not live" -q -p no:cacheprovider`

- [ ] **Step 2: Run no-Key live grouping without credentials**

Run keyed/freemium/paid/enterprise live groups in an isolated directory. Expected: missing-Key and missing-license cases skip or report unconfigured without network; paid network count remains zero because `VR_ALLOW_PAID_PROVIDER_TESTS` is absent.

- [ ] **Step 3: Complete the matrices**

Record family, Adapter, capabilities, role, billing model, Key requirement, license boundary, default status, connection truth, shell-only truth, current limits and alternatives. Do not copy secrets or hard-code long-term pricing/quotas as permanent facts.

- [ ] **Step 4: Perform security and cost review**

Review credential serialization, request/log redaction, keyring fallback, API exception bodies, localStorage, URL queries, budget atomicity, default disablement, and absence of enterprise SDKs. Run `git diff --check` and secret-pattern scans on all changed files. Fix findings with RED/GREEN tests.

- [ ] **Step 5: Commit Phase 3 qualification**

```powershell
git add docs/data-sources/freemium-provider-matrix.md docs/data-sources/paid-provider-matrix.md docs/data-sources/enterprise-provider-catalog.md docs/data-sources/licensing-and-usage-boundaries.md docs/data-sources/source-before-after.md
git commit -m "docs(pp03): qualify credentialed provider phase"
```

Checkpoint: worktree clean; no paid Live request, automatic purchase, PR, merge, push, or Phase 4 production edit has occurred.
