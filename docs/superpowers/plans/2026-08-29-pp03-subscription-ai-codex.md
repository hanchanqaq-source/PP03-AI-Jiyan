# PP03 Subscription AI W1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a PP03 user connect a ChatGPT-backed Codex login stored only in PP03's private data directory, test it safely, set it as the default AI only after success, and use it for existing page-context answers.

**Architecture:** Keep the PP03 Python/FastAPI/React stack. A focused `subscription_ai` provider owns status, login, test, cancel, sanitization, and the public response contract; the existing CLI runtime owns the common product `CODEX_HOME` environment and safe `codex exec` argv so every Codex subprocess shares the same isolation. The Settings page renders the lifecycle and preserves the existing API mode without automatic fallback.

**Tech Stack:** Python 3.12, FastAPI, pytest, React 19, TypeScript, Vitest, Testing Library, official Codex CLI.

**Spec:** User taskbook `SUBSCRIPTION_AI_W1｜ChatGPT 会员通过 Codex 接入`; upstream audit `docs/acceptance/subscription-ai-w1/upstream-selective-migration.md`.

## Global Constraints

- Formal baseline is `codex/pp03-dual-holdings` at `e9f1916fa549e1f74472fb82c3da8ea403867a86`; implementation branch is `codex/pp03-subscription-ai-codex`.
- Upstream reference is read-only commit `64ba4527b6e247ec9d26347cbc75158fefd2bb7b`; never merge or cherry-pick it.
- Product Codex state is `%VR_DATA_DIR%\codex-home`; when `VR_DATA_DIR` is unset, use PP03's existing private default `%USERPROFILE%\.vibe-research\codex-home`.
- Never read, inspect, copy, modify, log, screenshot, or return `%USERPROFILE%\.codex`, auth files, tokens, cookies, keys, browser profiles, or raw environment lists.
- Every Codex subprocess explicitly receives the product `CODEX_HOME`; inherited `CODEX_HOME`, `CODEX_API_KEY`, `CODEX_ACCESS_TOKEN`, and OpenAI API credentials are not passed into membership mode.
- Only `logged_in_chatgpt` may represent membership authentication; API-key auth is visible but unavailable for membership and is never changed without confirmation.
- Connection prompt is exactly `只回复：PP03_CODEX_CONNECTED`; success requires exit code 0 and that marker.
- Successful provider responses contain only `provider_id, installed, version, auth_status, available, test_status, message, last_test_at`.
- Default config is exactly `provider=cli-codex, model=codex, baseURL="", apiKey=""` and is saved only after a successful test.
- Never run `codex logout`, `codex mcp add`, modify any Codex `config.toml`, install Codex, use paid API fallback, or connect PP03 MCP in this Work.
- No full regression, commit, push, PR, tag, release, or deployment before the user replies `页面通过`.

## File Responsibilities

- `backend/cli_runtime.py`: resolve/create PP03 product Codex home, build the minimal sanitized environment, and apply safe Codex argv to both one-shot and streaming page answers.
- `backend/subscription_ai/models.py`: exact public whitelist model and lifecycle/test enums.
- `backend/subscription_ai/codex_provider.py`: version/auth probes, visible official login, test/cancel lifecycle, process-tree cleanup, and sanitized messages.
- `backend/subscription_ai/service.py`: single Codex provider facade; no alternate provider or fallback.
- `backend/app.py`: four loopback endpoints and membership guard for normal AI calls.
- `frontend/src/lib/subscription-ai.ts`: typed direct API responses containing only whitelist fields.
- `frontend/src/pages/Settings.tsx`: membership-first lifecycle UI, polling, test-before-default, and explicit API warning.
- `backend/tests/test_subscription_ai_codex_provider.py`, `backend/tests/test_subscription_ai_api.py`, `backend/tests/test_fixes.py`: offline isolation, contract, process, and normal-runtime coverage.
- `frontend/src/pages/__tests__/Settings.test.tsx`: user-visible lifecycle and persistence coverage.

---

### Task 1: Product CODEX_HOME and public provider contract

**Interfaces:**
- Produces `cli_runtime.product_codex_home(env=None) -> Path`.
- Produces `cli_runtime.codex_subprocess_env(env=None, codex_home=None) -> dict[str, str]`.
- Produces `SubscriptionProviderStatus.to_public_dict()` with the exact eight-field whitelist.
- Produces auth states `not_installed | installed_not_logged_in | logged_in_chatgpt | logged_in_api_key | unsupported_version | status_failed | available`.

- [ ] **Step 1: Write RED tests**

Add literal assertions that an inherited `CODEX_HOME=C:\\Users\\sentinel\\.codex` and fake Codex/API credential variables are replaced or removed for `--version`, `login status`, `login`, connection test, and normal `cli-codex` runs. Assert the product directory is created and the public dictionary key set is exactly:

```python
{
    "provider_id", "installed", "version", "auth_status",
    "available", "test_status", "message", "last_test_at",
}
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest backend/tests/test_subscription_ai_codex_provider.py backend/tests/test_fixes.py -q
```

Expected: failures show the inherited/global `CODEX_HOME` is still used and extra public fields still exist.

- [ ] **Step 3: Implement the minimal runtime and model changes**

Keep only stable process variables required for executable discovery, Windows operation, proxy, locale, temporary files, and CA certificates. Always set `CODEX_HOME` last to the resolved product directory and never add API credentials in membership mode.

- [ ] **Step 4: Verify GREEN**

Run the same pytest command and require all selected tests to pass.

### Task 2: Status, login, test, cancel, and normal page invocation

**Interfaces:**
- `CodexSubscriptionProvider.get_status(force=False) -> SubscriptionProviderStatus`.
- `start_login(confirm_switch=False) -> SubscriptionProviderStatus`.
- `test_connection() -> SubscriptionProviderStatus`.
- `cancel_test() -> SubscriptionProviderStatus`.
- Safe Codex exec argv begins with global approval flags before `exec`, then uses `--ignore-user-config --ignore-rules --ephemeral --sandbox read-only --skip-git-repo-check -`.

- [ ] **Step 1: Write RED tests**

Cover missing CLI, parsed version, not logged in, ChatGPT login, API-key login, unsupported status command, generic status failure, already logged-in no-op, API-key switch confirmation, visible Windows login, duplicate login, exact test prompt/argv, empty temporary cwd, success, unexpected output, rate limit, timeout, cancel, nonzero exit, process-tree cleanup, temporary directory cleanup, and sanitized diagnostics. Add a normal-runtime test proving `cli_runtime.run_cli_stream("codex", ...)` uses the same product home and safe argv.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest backend/tests/test_subscription_ai_codex_provider.py backend/tests/test_fixes.py -q
```

Expected: new lifecycle names, product-home env assertions, `--ignore-rules`, or direct whitelist responses fail against the prior implementation.

- [ ] **Step 3: Implement minimal provider behavior**

Do not inspect auth files. Parse only `codex login status` exit code and public output. Login uses the official `codex login` process with product env, no stdio capture, and no logout. Connection tests use a fresh empty temporary directory and never retain complete stderr.

- [ ] **Step 4: Verify GREEN**

Run the same selected pytest command and require all tests to pass.

### Task 3: Loopback HTTP API and chat guard

**Interfaces:**
- `GET /api/subscription-ai/providers` returns a list of whitelist provider rows.
- Login, test, and cancel POST endpoints return one whitelist provider row.
- Socket peer, Host, and optional Origin must all be loopback for POST actions.
- Any `cli-codex` chat/debate/reflection call is rejected unless product-home status is ChatGPT membership, with no API fallback.

- [ ] **Step 1: Write RED API tests**

Assert exact response key sets for all success endpoints, loopback allow/remote spoof reject, API-key switch conflict, and membership guard behavior. Assert no response contains `auth.json`, `CODEX_HOME`, an executable path, username directory, raw stderr, or environment variables.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest backend/tests/test_subscription_ai_api.py backend/tests/test_api.py -q
```

Expected: old envelopes/extra fields and old auth names fail.

- [ ] **Step 3: Implement the direct response contract**

Keep existing PP03 backend-access-key middleware separate from model API keys. Do not add providers, persistence, MCP, or installation endpoints.

- [ ] **Step 4: Verify GREEN**

Run the same pytest command and require all selected tests to pass.

### Task 4: Membership-first Settings experience

**Design tokens:** inherit existing `primary`, `success`, `amber`, `destructive`, `muted`, current typography, GlassCard spacing, keyboard focus, mobile stacking, and reduced-motion behavior. The signature element remains one restrained three-step rail: install, ChatGPT login, connection test.

**Interfaces:**
- Default mode label is `会员 / 免费额度接入`.
- Membership copy is `使用你的 ChatGPT/Codex 套餐额度；额度和限制由 OpenAI 官方管理。`.
- API copy is `API 可能按量计费；系统不会自动从会员接入切换到 API。`.
- Capability copy is `当前能力：分析页面已经提供的数据` and a separate later-tools statement.

- [ ] **Step 1: Write RED component tests**

Cover default membership mode, loading, uninstalled, installed-not-logged-in, ChatGPT login, API-key non-membership, login click plus status polling, test/cancel, default disabled until success, success persistence across remount, failed test preserving an existing config, API advanced mode, and absence of invented plan/remaining-quota labels.

- [ ] **Step 2: Verify RED**

Run:

```powershell
npm --prefix frontend run test:run -- src/pages/__tests__/Settings.test.tsx
```

Expected: lifecycle names, polling, exact copy, or failed-test preservation fails.

- [ ] **Step 3: Implement typed client and UI**

Use only the provider whitelist. Show a symbolic, copyable Windows fallback command without returning the actual product path. Poll only after login starts and stop polling when ChatGPT membership becomes available, the component unmounts, or the bounded login window ends.

- [ ] **Step 4: Verify GREEN and type safety**

Run:

```powershell
npm --prefix frontend run test:run -- src/pages/__tests__/Settings.test.tsx
npm --prefix frontend exec tsc -- --noEmit
```

Expected: both commands pass with no new warning or type error.

### Task 5: Stage A directed verification

- [ ] **Step 1: Run the complete directed backend set**

```powershell
python -m pytest backend/tests/test_subscription_ai_codex_provider.py backend/tests/test_subscription_ai_api.py backend/tests/test_api.py backend/tests/test_fixes.py -q
```

- [ ] **Step 2: Run the directed frontend set and type check**

```powershell
npm --prefix frontend run test:run -- src/pages/__tests__/Settings.test.tsx
npm --prefix frontend exec tsc -- --noEmit
```

- [ ] **Step 3: Inspect the diff**

```powershell
git diff --check
git status --short --branch
```

Do not commit or run full regression.

### Task 6: Stage B browser handoff

- [ ] **Step 1: Start isolated services**

Use `.tmp/acceptance/subscription-ai-w1/data` as `VR_DATA_DIR`, backend `127.0.0.1:8900`, and frontend `127.0.0.1:5899`. Do not reuse or kill unknown services.

- [ ] **Step 2: Open `http://127.0.0.1:5899/settings`**

Verify the page only against the fresh isolated product home. If it is not logged in, stop at the official browser-login gate and ask the user to complete it; do not read or copy global credentials.

- [ ] **Step 3: After product-home ChatGPT login, complete the user path**

Run the minimal connection test, set Codex as default, execute one existing page-context answer, verify API advanced mode still exists, and leave both services running.

- [ ] **Step 4: Report the required Stage B fields and wait**

Report exact branch/head/status evidence with `PRODUCT_CODEX_HOME_USED=YES`, `USER_GLOBAL_CODEX_HOME_READ=NO`, `API_KEY_USED=NO`, `PAID_API_FALLBACK_COUNT=0`, `PP03_MCP_CONNECTED=NO`, and `WAITING_FOR_USER_ACCEPTANCE=YES`. Stop before Stage C until the user replies `页面通过`.
