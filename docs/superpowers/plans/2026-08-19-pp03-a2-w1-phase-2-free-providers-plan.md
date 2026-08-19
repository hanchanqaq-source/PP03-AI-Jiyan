# PP03 A2-W1 Phase 2 Free Providers and Source Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independently implemented free/no-Key Provider adapters, connect them to health and routing, and resolve every current failed/degraded news source with evidence-backed decisions.

**Architecture:** Provider modules share a bounded safe HTTP/session utility but own their request construction and parsing. Live checks run only in an isolated `VR_DATA_DIR`; failed news sources use publisher-identity-preserving fixtures and one-in/one-out replacement rules.

**Tech Stack:** Python, requests, optional BaoStock/yfinance libraries, pytest Mock/monkeypatch, existing source-health probes, official public APIs.

**Spec:** `docs/superpowers/specs/2026-08-19-pp03-a2-multi-source-provider-hub-design.md`

## Global Constraints

- Start from the clean, reviewed Phase 1 Commit on `codex/pp03-a2-multi-source-provider-hub`.
- Use current official documentation and official endpoints only when validating technical contracts; do not use shared keys, cookies, logins, CAPTCHA bypass, paywall bypass, or disabled TLS verification.
- Live checks have finite timeout, finite response size, explicit User-Agent, rate limits, and isolated storage.
- GDELT is a collector/candidate source; yfinance is non-official and personal-research-only by default.
- SEC official files may support verified evidence, but the SEC adapter must not perform unbounded crawling.
- BaoStock does not replace CNInfo industry evidence or official fund NAV.
- Public failure or instability must remain visible as an honest status; Catalog registration is not proof of connection.
- Every parser change starts with a minimal redacted fixture and failing test.
- Do not change the trusted-news admission rule or industry evidence layers.

---

### Task 1: Safe Provider HTTP foundation and error taxonomy

**Files:**
- Create: `backend/data_sources/http.py`
- Create: `backend/data_sources/provider_errors.py`
- Create: `backend/data_sources/providers/__init__.py`
- Create: `backend/data_sources/providers/base.py`
- Test: `backend/tests/test_data_source_http.py`

**Interfaces:**
- Consumes: `requests.Session`, `source_health.probe_errors.redact_probe_message`, and Catalog reference redaction.
- Produces: `SafeHttpClient.get_json()`, `get_bytes()`, `ProviderError`, `ProviderRateLimited`, `ProviderSchemaChanged`, and `ProviderUnavailable`.

- [ ] **Step 1: Write failing network-safety tests**

```python
def test_http_client_rejects_oversized_response(fake_session):
    client = SafeHttpClient(session=fake_session, max_bytes=1024, timeout_seconds=3)
    fake_session.response(content_length=2048)
    with pytest.raises(ProviderUnavailable, match="response_too_large"):
        client.get_bytes("https://example.test/data")


def test_http_client_never_disables_tls(fake_session):
    SafeHttpClient(session=fake_session).get_json("https://example.test/data")
    assert fake_session.last_kwargs["verify"] is True
```

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_http.py -q -p no:cacheprovider`

Expected: missing `data_sources.http` module.

- [ ] **Step 3: Implement bounded requests**

```python
class SafeHttpClient:
    def __init__(self, *, session=None, timeout_seconds=10.0, max_bytes=2_000_000, user_agent="PP03-AI-Jiyan/1.0") -> None: ...
    def get_json(self, url: str, *, headers: Mapping[str, str] | None = None, params: Mapping[str, object] | None = None) -> object: ...
    def get_bytes(self, url: str, *, headers: Mapping[str, str] | None = None, params: Mapping[str, object] | None = None) -> bytes: ...
```

Always pass `verify=True`, use streaming reads, stop above `max_bytes`, accept only HTTPS unless a Catalog entry explicitly documents a safe local/test URL, enforce a finite redirect count, classify 401/403/429/5xx, honor bounded `Retry-After`, and redact exception text before surfacing it.

- [ ] **Step 4: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_http.py backend\tests\test_source_health_probes.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/http.py backend/data_sources/provider_errors.py backend/data_sources/providers backend/tests/test_data_source_http.py
git commit -m "feat(pp03): add bounded provider transport"
```

---

### Task 2: BaoStock and yfinance adapters

**Files:**
- Create: `backend/data_sources/providers/baostock.py`
- Create: `backend/data_sources/providers/yahoo_finance.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/data_sources/catalog.py`
- Test: `backend/tests/test_provider_baostock.py`
- Test: `backend/tests/test_provider_yahoo_finance.py`

**Interfaces:**
- Consumes: `ProviderAdapter`, `ProviderRequest`, `ProviderValue`, and injected BaoStock/yfinance clients.
- Produces: `BaoStockAdapter` and `YahooFinanceAdapter`.

- [ ] **Step 1: Write failing BaoStock tests**

```python
def test_baostock_normalizes_history_with_metadata(fake_baostock):
    adapter = BaoStockAdapter(client=fake_baostock)
    rows = adapter.fetch(ProviderRequest("stock_history", {"code": "sh.600000", "start_date": "2026-08-01"}))
    assert rows[0].source_family_id == "baostock"
    assert rows[0].unit == "CNY"
    assert rows[0].as_of_date.isoformat() == "2026-08-18"


def test_baostock_logs_out_after_failure(fake_baostock):
    fake_baostock.raise_during_query = True
    with pytest.raises(ProviderUnavailable):
        BaoStockAdapter(client=fake_baostock).fetch(request)
    assert fake_baostock.logout_calls == 1
```

- [ ] **Step 2: Write failing yfinance boundary tests**

Assert that the adapter is disabled outside `personal_research`, is marked non-official, preserves the upstream timestamp/currency, and never upgrades official evidence.

- [ ] **Step 3: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_provider_baostock.py backend\tests\test_provider_yahoo_finance.py -q -p no:cacheprovider`

Expected: missing provider modules.

- [ ] **Step 4: Implement independent adapters**

`BaoStockAdapter.fetch` must use a fresh login/query/logout lifecycle, normalize historical/adjusted/financial/industry/index-calendar/valuation capabilities, and never store login state. `YahooFinanceAdapter.fetch` must normalize only overseas stock/ETF/index/profile reference capabilities and require `usage_mode="personal_research"`.

Add the minimum runtime packages to `backend/requirements.txt` without importing them at app startup; missing optional packages yield an explicit unavailable capability rather than crashing Catalog APIs.

- [ ] **Step 5: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_provider_baostock.py backend\tests\test_provider_yahoo_finance.py backend\tests\test_data_source_catalog.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/providers/baostock.py backend/data_sources/providers/yahoo_finance.py backend/data_sources/catalog.py backend/requirements.txt backend/tests/test_provider_baostock.py backend/tests/test_provider_yahoo_finance.py
git commit -m "feat(pp03): add baostock and yahoo adapters"
```

---

### Task 3: SEC EDGAR and Chinese/Hong Kong official evidence adapters

**Files:**
- Create: `backend/data_sources/providers/sec_edgar.py`
- Create: `backend/data_sources/providers/official_evidence.py`
- Modify: `backend/data_sources/catalog.py`
- Test: `backend/tests/test_provider_sec_edgar.py`
- Test: `backend/tests/test_provider_official_evidence.py`

**Interfaces:**
- Consumes: `SafeHttpClient`, project GitHub URL as the default public contact identifier, and evidence URL identity rules.
- Produces: `SecEdgarAdapter` and `OfficialEvidenceLinkAdapter`.

- [ ] **Step 1: Write failing SEC contract tests**

```python
def test_sec_uses_contact_user_agent_and_bounded_rate(fake_http, clock):
    adapter = SecEdgarAdapter(http=fake_http, clock=clock)
    adapter.fetch(ProviderRequest("company_submissions", {"cik": "0000320193"}))
    assert "PP03-AI-Jiyan" in fake_http.last_headers["User-Agent"]
    assert "github.com/hanchanqaq-source/PP03-AI-Jiyan" in fake_http.last_headers["User-Agent"]
    assert fake_http.calls_per_second < 10


def test_sec_retry_after_is_respected_without_unbounded_retry(fake_http):
    fake_http.responses = [rate_limited(retry_after="2"), json_response(submissions_fixture)]
    assert SecEdgarAdapter(http=fake_http, sleeper=fake_sleep).probe("company_submissions")["status"] == "success"
    assert fake_sleep.calls == [2.0]
```

- [ ] **Step 2: Write failing official-link identity tests**

Cover SSE, SZSE, CNInfo, HKEXnews, CSRC, fund-company announcement, and index-company announcement host allowlists. Safe same-publisher redirects pass; login, CAPTCHA, HTTP downgrade, unrelated domains, and oversized documents fail closed.

- [ ] **Step 3: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_provider_sec_edgar.py backend\tests\test_provider_official_evidence.py -q -p no:cacheprovider`

- [ ] **Step 4: Implement SEC JSON/index capabilities**

Support company submissions, filing index lookup, 10-K, 10-Q, 8-K, 13F, and Company Facts metadata. Fetch only explicitly requested CIK/form/index paths; do not crawl filing bodies. Return official evidence metadata and canonical public links.

If the current SEC service rejects the public project contact identifier, return `unconfigured_contact` and do not fabricate an email.

- [ ] **Step 5: Implement finite official-link validation**

`OfficialEvidenceLinkAdapter.validate(url)` performs HEAD or bounded GET only for allowlisted official hosts and returns identity, final public reference, content type, published metadata when available, and validation status. It does not scrape protected pages.

- [ ] **Step 6: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_provider_sec_edgar.py backend\tests\test_provider_official_evidence.py backend\tests\test_evidence_verifier.py backend\tests\test_evidence_service.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/providers/sec_edgar.py backend/data_sources/providers/official_evidence.py backend/data_sources/catalog.py backend/tests/test_provider_sec_edgar.py backend/tests/test_provider_official_evidence.py
git commit -m "feat(pp03): add official evidence adapters"
```

---

### Task 4: World Bank, OECD, IMF, and GDELT adapters

**Files:**
- Create: `backend/data_sources/providers/world_bank.py`
- Create: `backend/data_sources/providers/oecd.py`
- Create: `backend/data_sources/providers/imf.py`
- Create: `backend/data_sources/providers/gdelt.py`
- Modify: `backend/data_sources/catalog.py`
- Test: `backend/tests/test_provider_world_bank.py`
- Test: `backend/tests/test_provider_oecd.py`
- Test: `backend/tests/test_provider_imf.py`
- Test: `backend/tests/test_provider_gdelt.py`

**Interfaces:**
- Consumes: `SafeHttpClient` and provider-specific public response fixtures.
- Produces: four independent adapters returning `ProviderValue` tuples.

- [ ] **Step 1: Capture minimal official response fixtures and write failing tests**

Fixtures contain only schema-minimum public fields. Test date, country/series key, unit, frequency, source family, and missing-value preservation for World Bank/OECD/IMF. Test that GDELT output is always `collector/candidate` and never `independent_evidence_eligible`.

```python
def test_world_bank_keeps_missing_values_empty():
    rows = WorldBankAdapter(http=fixture_http).fetch(request)
    assert rows[-1].value is None
    assert rows[-1].data_status == "missing"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_provider_world_bank.py backend\tests\test_provider_oecd.py backend\tests\test_provider_imf.py backend\tests\test_provider_gdelt.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement provider-specific request builders and parsers**

World Bank preserves indicator/country/unit/date; OECD preserves dataset/series key/unit/frequency/revision time; IMF first targets the currently documented public SDMX endpoint and reports `catalog_only` if that interface is retired or authorization-gated; GDELT returns discovery candidates with original publisher URLs and origin metadata.

- [ ] **Step 4: Test empty, timeout, schema-change, cache, and rate-limit paths**

Add explicit cases to each provider test. A schema mismatch raises `ProviderSchemaChanged`; it cannot silently return an empty success.

- [ ] **Step 5: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_provider_world_bank.py backend\tests\test_provider_oecd.py backend\tests\test_provider_imf.py backend\tests\test_provider_gdelt.py backend\tests\test_data_source_http.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/providers/world_bank.py backend/data_sources/providers/oecd.py backend/data_sources/providers/imf.py backend/data_sources/providers/gdelt.py backend/data_sources/catalog.py backend/tests/test_provider_world_bank.py backend/tests/test_provider_oecd.py backend/tests/test_provider_imf.py backend/tests/test_provider_gdelt.py
git commit -m "feat(pp03): add public macro and discovery adapters"
```

---

### Task 5: Health registration and capability routing for free Providers

**Files:**
- Create: `backend/data_sources/provider_registry.py`
- Create: `backend/data_sources/routing.py`
- Modify: `backend/data_sources/health_bridge.py`
- Modify: `backend/source_health/probes/__init__.py`
- Create: `backend/source_health/probes/data_source_adapter.py`
- Test: `backend/tests/test_data_source_provider_registry.py`
- Test: `backend/tests/test_data_source_routing.py`
- Test: `backend/tests/test_source_health_probes.py`

**Interfaces:**
- Consumes: independent Adapter classes and Catalog descriptors.
- Produces: `ProviderRegistry.adapter(adapter_id)`, `CapabilityRouter.route(capability_id)`, and `probe_data_source_adapter()`.

- [ ] **Step 1: Write failing registry and routing tests**

Assert unique Adapter implementation registration, no eager import/network, per-capability primary/fallback/cross-check order, BaoStock independence from Eastmoney/Tencent, SEC official priority, and GDELT candidate-only routing.

- [ ] **Step 2: Run tests and verify RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_provider_registry.py backend\tests\test_data_source_routing.py backend\tests\test_source_health_probes.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement lazy Provider registration and probes**

```python
class ProviderRegistry:
    def adapter(self, adapter_id: str) -> ProviderAdapter: ...
    def available_adapter_ids(self) -> tuple[str, ...]: ...


class CapabilityRouter:
    def route(self, capability_id: str) -> CapabilityRoute: ...
```

Optional dependency failures affect only the corresponding Adapter. Convert provider results/errors to health observation metadata without changing content verification status.

- [ ] **Step 4: Run tests and commit**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_data_source_provider_registry.py backend\tests\test_data_source_routing.py backend\tests\test_source_health_probes.py backend\tests\test_source_health_runner.py -q -p no:cacheprovider`

Commit:

```powershell
git add backend/data_sources/provider_registry.py backend/data_sources/routing.py backend/data_sources/health_bridge.py backend/source_health/probes backend/tests/test_data_source_provider_registry.py backend/tests/test_data_source_routing.py backend/tests/test_source_health_probes.py
git commit -m "feat(pp03): route free provider capabilities"
```

---

### Task 6: Failed/degraded news source repair ledger

**Files:**
- Create: `backend/tests/fixtures/news_sources/` (minimal redacted fixtures only)
- Modify only when evidence supports it: `backend/news_sources.json`
- Modify only when a parser test proves it: `backend/newsradar.py`
- Test: `backend/tests/test_news_source_repairs.py`
- Create: `docs/data-sources/source-repair-decisions.md`
- Create: `docs/data-sources/free-provider-matrix.md`
- Create: `docs/data-sources/licensing-and-usage-boundaries.md`

**Interfaces:**
- Consumes: current health snapshot and isolated live probe results.
- Produces: one decision row per failed/degraded source with an allowed conclusion.

- [ ] **Step 1: Run an isolated diagnostic audit**

Set `VR_DATA_DIR` and all news cache paths to `.tmp/acceptance/a2-w1-phase2-before`. Probe the 108 existing sources plus free Provider adapters with finite limits. Export only redacted public observations and exact source IDs. Do not read portfolio/user files.

- [ ] **Step 2: Create the decision table before changing sources**

For 36氪、动点科技、国际能源网、虎嗅、钛媒体、arXiv cs.AI、FierceBiotech、FiercePharma、WSJ Markets and every additional failed/degraded observation, record current error, consecutive failures, last success, unique content value, official alternative, repair cost, and exactly one conclusion: 修复/观察/替换/停用/需要凭据/需要许可证.

- [ ] **Step 3: Capture RED for each parser or official-feed migration**

For parser changes, save a minimum fixture and a named failing test. For feed migration, assert the alternative host/publisher identity, successful parse, and stable source-family identity before changing `news_sources.json`. TLS/DNS/502 cases remain observation cases and never disable TLS.

- [ ] **Step 4: Implement only evidence-backed repairs**

Update parsers or feed URLs only for passing identity checks. Apply one-in/one-out for ordinary media: validate the replacement before disabling the old entry. Do not add unrelated media to grow total count.

- [ ] **Step 5: Run focused and live After evidence**

Run deterministic:

`backend\.venv\Scripts\python.exe -m pytest backend\tests\test_news_source_repairs.py backend\tests\test_newsradar.py backend\tests\test_source_health_probes.py -q -p no:cacheprovider`

Then rerun the isolated live audit with the same source IDs and record configuration hashes, successful repairs, remaining failures, and network variance separately.

- [ ] **Step 6: Commit repairs and documents**

```powershell
git add backend/newsradar.py backend/news_sources.json backend/tests/fixtures/news_sources backend/tests/test_news_source_repairs.py docs/data-sources/source-repair-decisions.md docs/data-sources/free-provider-matrix.md docs/data-sources/licensing-and-usage-boundaries.md
git commit -m "fix(pp03): repair qualified public data sources"
```

Only add files that actually changed; do not stage unchanged parser/config files.

---

### Task 7: Phase 2 regression and review gate

**Files:**
- Modify: `docs/data-sources/source-before-after.md`
- Modify: `docs/data-sources/free-provider-matrix.md`

**Interfaces:**
- Consumes: Phase 2 deterministic tests and isolated live Before/After.
- Produces: honest connected/catalog-only/failure counts and source-repair evidence.

- [ ] **Step 1: Run deterministic backend regression**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests -m "not live" -q -p no:cacheprovider`

- [ ] **Step 2: Run frontend compatibility and build**

Run in `frontend`:

```powershell
npm run test:run
npm run test:legacy
npm run build
```

- [ ] **Step 3: Update Before/After without overstating Live results**

Record registered families/Adapters/capabilities, actually connected free providers, connector-only providers, repaired/updated/replaced/disabled/observed sources, unresolved count and reasons. Do not write “全部已修复” unless every decision row proves it.

- [ ] **Step 4: Perform scoped security and contract review**

Review changed dependency/import boundaries, response-size limits, TLS verification, redirect identity, redaction, official-source eligibility, and origin independence. Run `git diff --check` and secret-pattern scans on changed files. Correct findings through new RED/GREEN cases.

- [ ] **Step 5: Commit Phase 2 qualification**

```powershell
git add docs/data-sources/source-before-after.md docs/data-sources/free-provider-matrix.md
git commit -m "docs(pp03): qualify free provider phase"
```

Checkpoint: worktree clean; no Key, paid, enterprise Live request, push, PR, merge, or Phase 3 production edit has occurred.
