# PP03 A1.1-W1 Evidence Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build deterministic field-level evidence verification for existing PP03 market-news events, admit only trusted events to Market News, and replace the W0 Evidence Center fixtures with real API data.

**Architecture:** A new `backend/evidence_verification/` package reads existing radar events without portfolio data, classifies source identity, extracts typed claims, applies fixed deterministic rules, and atomically stores the last successful snapshot plus append-only history. Market News consumes only its admission projection; the React Evidence Center consumes dedicated APIs while retaining the existing A1 source-health workspace.

**Tech Stack:** Python 3.11, FastAPI, dataclasses/stdlib URL and HTML parsing, pytest, React 18, TypeScript, Vitest, Testing Library, Vite.

**Spec:** `docs/superpowers/plans/2026-08-18-evidence-verification-design.md` and the authoritative attachment `C:\Users\26365\.codex\attachments\1250ac19-c938-4c80-bf9b-01b47abacc92\pasted-text.txt`.

## Global Constraints

- Base exactly on remote W0 commit `050e28604e46f05d1975bee54078c0242bcbe964`; branch is `codex/pp03-a1-1-evidence-verification`.
- Inputs are limited to the existing 108 sources, existing cache, event raw public links, and existing official/regulatory/disclosure links.
- No web search, new source/provider, API key, login cookie, access bypass, TLS bypass, AI truth judgment, or lowered verification threshold.
- Evidence refresh must not load or persist portfolio amount, cost, note, account, or credential data.
- Do not edit `backend/news_sources.json`; do not start A1.1-W2, A2, A3, or V0.2-W3.
- Keep Source Health and the original four research pages behaviorally unchanged.
- No production code before a focused failing test for that behavior.

---

### Task 1: Evidence contracts and source identity

**Files:**
- Create: `backend/evidence_verification/__init__.py`
- Create: `backend/evidence_verification/models.py`
- Create: `backend/evidence_verification/source_identity.py`
- Test: `backend/tests/test_evidence_source_identity.py`

**Interfaces:**
- Produces: `VerificationStatus`, `FieldVerificationStatus`, `SourceRole`, `KeyField`, `EvidenceItem`, `StatusTransition`, `EvidenceEvent`.
- Produces: `canonicalize_public_url(url: str) -> str | None` and `identify_evidence(source: NewsSourceItem) -> EvidenceItem`.
- Consumes only the existing `NewsSourceItem` public metadata.

- [ ] **Step 1: Write focused failing contract tests**

```python
def test_same_publisher_copies_share_one_origin_cluster():
    left = identify_evidence(source("collector-a", "https://wire.example/a?id=1"))
    right = identify_evidence(source("collector-b", "https://wire.example/a?id=1&utm_source=x"))
    assert left.collector_source != right.collector_source
    assert left.origin_cluster == right.origin_cluster

def test_private_or_credentialed_links_are_rejected():
    assert canonicalize_public_url("http://127.0.0.1/report") is None
    assert canonicalize_public_url("https://user:secret@example.com/report") is None
```

- [ ] **Step 2: Run RED**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_evidence_source_identity.py -q -p no:cacheprovider`

Expected: collection fails because `evidence_verification` does not exist.

- [ ] **Step 3: Implement immutable models and fail-closed identity**

```python
class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    CORROBORATED = "corroborated"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"
    CORRECTED = "corrected"
    DISPROVED = "disproved"

def canonicalize_public_url(url: str) -> str | None:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    try:
        if not ip_address(parsed.hostname).is_global:
            return None
    except ValueError:
        if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
            return None
    query = urlencode(sorted(
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}
    ))
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", query, ""))
```

- [ ] **Step 4: Run GREEN and mutation boundary cases**

Run the focused test, including duplicate collectors, same-domain different articles, IPv4/IPv6 local addresses, malformed ports, query secrets and official-domain role cases.

- [ ] **Step 5: Commit**

Commit subject: `feat(pp03): define evidence verification contracts`

---

### Task 2: Typed claim fields and deterministic matcher

**Files:**
- Create: `backend/evidence_verification/claim_fields.py`
- Create: `backend/evidence_verification/evidence_matcher.py`
- Test: `backend/tests/test_evidence_claim_fields.py`
- Test: `backend/tests/test_evidence_matcher.py`

**Interfaces:**
- Produces: `extract_core_claim(title: str, excerpt: str) -> str`.
- Produces: `extract_key_fields(title: str, excerpt: str) -> Sequence[KeyField]`.
- Produces: `match_evidence(core_claim: str, fields: Sequence[KeyField], evidence: Sequence[EvidenceItem]) -> MatchResult`.
- Match input excludes translated text and AI summaries.

- [ ] **Step 1: Write RED tests with hand-derived literals**

```python
def test_money_percentage_quantity_and_date_remain_independent():
    fields = extract_key_fields(
        "公司公告投资12亿元建设项目",
        "预计产能提升20%，新增3条产线，2026年8月18日生效",
    )
    assert [(row.field_name, row.normalized_value) for row in fields] == [
        ("money", "CNY:1200000000"),
        ("percentage", "20%"),
        ("quantity", "3:产线"),
        ("effective_date", "2026-08-18"),
    ]

def test_official_document_can_verify_money_without_verifying_cycle():
    result = match_evidence(claim, fields, (official_document_without_cycle,))
    assert result.fields["money"].verification_status == "verified"
    assert result.fields["build_cycle"].verification_status == "unverified"
```

- [ ] **Step 2: Run RED**

Run both new test modules and confirm failures name the missing extraction/matching behavior.

- [ ] **Step 3: Implement conservative extraction and comparison**

Use NFKC normalization, `Decimal`, explicit Chinese/ISO date patterns and typed units. Unknown currency/unit/ambiguous date remains raw and `unverified`. Claim support requires deterministic subject/action token overlap; numeric coincidence alone is insufficient.

- [ ] **Step 4: Run GREEN and negative cases**

Cover conflicting money, year versus money ambiguity, repeated fields, malformed values, same origin repeated ten times, and AI/source-health metadata having no effect.

- [ ] **Step 5: Commit**

Commit subject: `feat(pp03): match evidence claims and fields`

---

### Task 3: Fixed-status verifier and history transitions

**Files:**
- Create: `backend/evidence_verification/verifier.py`
- Test: `backend/tests/test_evidence_verifier.py`

**Interfaces:**
- Consumes `MatchResult` and an optional previous `EvidenceEvent`.
- Produces `verify_event(event, evidence, previous: EvidenceEvent | None, now: datetime) -> EvidenceEvent`.
- Decision priority: disproved, corrected, conflicting, verified, corroborated, unverified.

- [ ] **Step 1: Write one RED table for all six statuses**

```python
@pytest.mark.parametrize(("case", "expected"), [
    (official_support, "verified"),
    (two_independent_supporters, "corroborated"),
    (single_nonofficial_supporter, "unverified"),
    (reliable_disagreement, "conflicting"),
    (official_correction, "corrected"),
    (official_denial, "disproved"),
])
def test_fixed_status_decision_table(case, expected):
    assert verify_event(**case).verification_status == expected
```

- [ ] **Step 2: Run RED**

Confirm the missing verifier causes the failure and not fixture construction.

- [ ] **Step 3: Implement the decision table and append-only transitions**

No condition may consult AI status, translations, source-health score, raw collector count, or importance score. Append a transition only when status/reason changes; preserve prior transitions byte-for-byte.

- [ ] **Step 4: Run GREEN**

Include ten-copy syndication, official correction over prior verified state, corrected old-version non-admission marker, and transition idempotency.

- [ ] **Step 5: Commit**

Commit subject: `feat(pp03): classify deterministic evidence states`

---

### Task 4: Atomic storage and refresh orchestration

**Files:**
- Create: `backend/evidence_verification/storage.py`
- Create: `backend/evidence_verification/service.py`
- Test: `backend/tests/test_evidence_storage.py`
- Test: `backend/tests/test_evidence_service.py`

**Interfaces:**
- `EvidenceStorage(root: Path | None, now: Callable[[], datetime])` owns `current.json`, `last-refresh.json`, and daily JSONL history under `VR_DATA_DIR/evidence-verification/v1`.
- `EvidenceVerificationService.refresh() -> EvidenceSnapshot` reads radar/normalizes/clusters without a portfolio loader.
- `summary()`, `list_events(filters)`, `get_event(event_id)`, and `admit(events)` read the last complete snapshot.
- Exact-link retrieval is dependency-injected as `document_fetcher(url) -> PublicDocument` and enforces default TLS, redirect/public-host checks, timeout and byte limits.

- [ ] **Step 1: Write storage/service RED tests**

```python
def test_failed_refresh_preserves_previous_snapshot_bytes(tmp_path):
    storage = EvidenceStorage(tmp_path)
    storage.publish(successful_snapshot)
    before = storage.current_path.read_bytes()
    service = EvidenceVerificationService(storage=storage, event_loader=raising_loader)
    with pytest.raises(RuntimeError):
        service.refresh()
    assert storage.current_path.read_bytes() == before

def test_refresh_never_calls_portfolio_loader():
    service.refresh()
    assert portfolio_loader_calls == 0
```

- [ ] **Step 2: Run RED**

Expected failure: missing storage/service modules.

- [ ] **Step 3: Implement staged refresh and bounded exact-link fetch**

Write temporary files in the target directory, `fsync`, then `os.replace`. Publish history/current before the success marker. Reject redirects or DNS/IP destinations that become private/local, oversize responses, unsupported content types, malformed encodings and TLS errors; never retry permission/paywall responses as success.

- [ ] **Step 4: Run GREEN plus restart/concurrency cases**

Verify restart restoration, same-event history append, failed marker without current deletion, concurrent read during replace, no full-body persistence, and serialized output free of portfolio/credential keys.

- [ ] **Step 5: Commit**

Commit subject: `feat(pp03): persist evidence verification snapshots`

---

### Task 5: Evidence API and trusted market-news admission

**Files:**
- Modify: `backend/app.py`
- Modify: `backend/news_intelligence/models.py`
- Modify: `backend/news_intelligence/service.py`
- Test: `backend/tests/test_evidence_api.py`
- Modify: `backend/tests/test_market_news_api.py`

**Interfaces:**
- Registers the four `/api/evidence` endpoints.
- `MarketNewsService` receives an evidence-admission dependency and emits `verification_status`, `verification_reason`, `verified_at`, trusted key fields and a sanitized display projection.
- Unknown/no-snapshot events are treated as `unverified` and excluded.

- [ ] **Step 1: Write API/admission RED tests**

```python
def test_market_news_admits_only_verified_or_corroborated(client):
    data = client.get("/api/market-news/events?mode=global_tech&days=30").json()["data"]
    assert {row["verification_status"] for row in data["events"]} <= {"verified", "corroborated"}

def test_unverified_money_is_absent_from_market_projection(client):
    event = next(row for row in trusted_events if row["event_id"] == partial_field_event_id)
    assert "12亿元" not in event["title"]
    assert "12亿元" not in event["summary"]
    assert event["impact_tendency"] == "unclear"
```

- [ ] **Step 2: Run RED**

Run evidence API and market-news API modules; verify failures are missing endpoints/admission.

- [ ] **Step 3: Add thin routes and evidence consumption**

Keep decision logic in `evidence_verification`. Preserve canonical market filters and sorting after trusted admission. Return the mandated trusted-empty message and do not fall back to raw events.

- [ ] **Step 4: Run GREEN and full deterministic backend**

Run focused API tests, all evidence tests, then `backend\.venv\Scripts\python.exe -m pytest backend\tests -q -m "not live" -p no:cacheprovider`.

- [ ] **Step 5: Commit**

Commit subject: `feat(pp03): admit verified market news`

---

### Task 6: Real Evidence Center and Market News UI

**Files:**
- Create: `frontend/src/features/evidence-center/types.ts`
- Create: `frontend/src/features/evidence-center/EvidenceDrawer.tsx`
- Modify: `frontend/src/features/evidence-center/EvidenceCenter.tsx`
- Modify: `frontend/src/features/evidence-center/EvidenceCenter.test.tsx`
- Modify: `frontend/src/features/market-news/types.ts`
- Modify: `frontend/src/features/market-news/EventCard.tsx`
- Modify: `frontend/src/features/market-news/__tests__/EventCard.test.tsx`
- Modify: `frontend/src/pages/MarketNews.tsx`
- Modify: `frontend/src/pages/__tests__/CorePages.test.tsx`
- Modify: `frontend/src/lib/api.ts`
- Delete only after real API GREEN/browser PASS: `frontend/src/features/evidence-center/fixtures.ts`
- Delete only after real API GREEN/browser PASS: `frontend/src/features/market-news/prototype.ts`

**Interfaces:**
- Uses backend status codes and maps them to Chinese labels in one frontend map.
- Evidence Center loads summary/list/detail/refresh independently from Source Health.
- Query `event_id` opens the matching API detail; drawer restores focus and traps keyboard navigation.

- [ ] **Step 1: Write UI RED tests**

Test trusted badges/link, unverified exclusion, numeric hiding, API summary/list/detail, primary/independent/syndicated/conflicting chains, correction transitions, request error/empty states, query opening and A1 Source Health unchanged.

- [ ] **Step 2: Run RED**

Run the focused Evidence Center, EventCard, Market News and CorePages suites. Expected failures identify fixture-backed values and missing API methods.

- [ ] **Step 3: Implement API-backed UI while fixtures still exist but are unused**

No fixture fallback on request failure. Render an explicit unavailable state. Keep existing page layout/filter panels and Source Health workspace.

- [ ] **Step 4: Run focused GREEN, full frontend and build**

Run `npm run test:run`, `npm run test:legacy`, and `npm run build`. Only after these and browser acceptance pass, delete fixture/prototype files and rerun the same commands.

- [ ] **Step 5: Commit**

Commit subject: `feat(pp03): connect evidence center verification`

---

### Task 7: Isolated browser acceptance and evidence artifacts

**Files:**
- Create: `.tmp/acceptance/a1-1-w1/` only as temporary runtime data.
- Create: `docs/acceptance/a1-1-w1/` screenshots/report files required by the taskbook.

**Interfaces:**
- Backend uses isolated `VR_DATA_DIR` and deterministic acceptance source documents.
- Browser target is `http://127.0.0.1:5899/market-news` and `/evidence-center`.

- [ ] **Step 1: Seed isolated public-link fixtures through test-only acceptance inputs**

Include one official support, two independent origins, one unverified event with unresolved money, one conflict, one correction and one disproof. Labels must clearly identify acceptance isolation; none are presented as live external claims.

- [ ] **Step 2: Start backend/frontend with exact isolated environment**

Verify ports 8900/5899 are unused first. Record PIDs and log paths. Do not read the default user portfolio or radar cache.

- [ ] **Step 3: Use Browser Use for all eleven acceptance checks**

Capture the trusted Market News stream, verified detail, corroborated chains, withheld unverified event/amount, conflict-only Evidence Center, correction history and Source Health regression. Record console `error=0`, `warn=0` and failed API requests.

- [ ] **Step 4: Stop only owned processes and clean temporary data**

Resolve absolute paths, validate they are within `.tmp/acceptance/a1-1-w1`, stop recorded PIDs, then remove the isolated runtime directory. Preserve requested screenshots/report.

- [ ] **Step 5: Commit acceptance artifacts**

Commit subject: `docs(pp03): record evidence verification acceptance`

---

### Task 8: Final branch qualification and push

**Files:**
- Create/update the taskbook-requested W1 report under `.superpowers/sdd/` if that directory is ignored; do not force ignored evidence into Git.

**Interfaces:**
- Final report uses the exact format in authoritative spec section 16.

- [ ] **Step 1: Run fresh complete qualification**

Run backend deterministic full suite, frontend full suite, legacy suite, production build, focused security/path tests, `git diff --check`, and sensitive-pattern scans over the W1 diff.

- [ ] **Step 2: Audit immutable boundaries**

Confirm no diff to `backend/news_sources.json`, no provider/API-key/TLS/search additions, no user data or temporary runtime files, no PR/main/W2/A2/A3/W3 work, and all fixture/prototype imports removed.

- [ ] **Step 3: Write the exact final report and verify links/hashes**

Include baseline/branch/current commits, six event-status counts, three field-status counts, admission/isolation counts, exact test/build/browser results, console counts, explicit non-actions, cleanup and clean-worktree evidence.

- [ ] **Step 4: Commit remaining scoped files**

Commit subject: `chore(pp03): qualify evidence verification w1`

- [ ] **Step 5: Push without force and stop**

Push `codex/pp03-a1-1-evidence-verification`, verify local/remote SHA and ahead/behind `0 0`, create no PR, merge nothing, and remain at A1.1-W1.

## Self-review result

- Spec coverage: every requirement in sections 3-16 maps to Tasks 1-8.
- Placeholder scan: implementation steps define concrete interfaces and failure/pass commands; no unresolved product decision remains.
- Type consistency: backend uses English fixed codes; frontend alone maps codes to Chinese labels. `event_id` is the shared key for market-news admission, Evidence Center detail and query navigation.
