# A2-W1 Phase 2 Task 6 — licensing and usage boundaries

- RSS/Atom observations are limited to each configured publisher's public
  endpoint. They do not grant republication rights; published content and
  feeds remain subject to publisher terms.
- The configured HTTP feeds 东方财富股票、东方财富资讯 and 经济观察网 are classified as
  `insecure_transport` without an HTTP request until a same-publisher HTTPS
  migration is independently proved.
- `WSJ Markets` is kept behind a `需要许可证` decision because a stale public
  feed does not establish rights to obtain fresh content through a paid,
  licensed or bypassed route.
- GDELT is discovery/candidate metadata only. An original publisher must be
  independently verified before any evidence use; GDELT routing cannot make
  it independent evidence.
- SEC EDGAR requests must use a truthful public contact identity and fair
  access. This task did not create an email, use a shared key, use a login or
  bypass an access response.
- World Bank, OECD, IMF and BaoStock remain subject to their upstream public
  terms. `catalog_only`, optional dependency absence and a failed bounded
  request are not authorizations to scrape alternate interfaces.
- yfinance/Yahoo Finance remains disabled except for explicitly permitted
  `personal_research`; it is non-official reference data and cannot upgrade
  official evidence.
- No API key, Cookie, Authorization header, CAPTCHA/paywall bypass, paid or
  enterprise service, license purchase, TLS disablement or automatic package
  installation occurred in Task 6.

## Phase 3 credential, account, and cost boundaries

- Credential names are allowlisted per stable Adapter ID from Catalog
  metadata. Raw values remain server-side. Public credential state exposes
  only configured/status/last-validation/source fields; errors, API responses,
  config files, logs, URLs, UI state, and usage records must not contain a raw
  value.
- The system keyring is the writable production store. If it is unavailable,
  the service reports `credential_store_unavailable`; it does not fall back to
  plaintext. Environment credentials are declared-name-only and read-only.
  Non-secret configuration under `VR_DATA_DIR` stores enablement, usage mode,
  Free-only, bounded decimal budgets, request limits, and validation time only.
- Credentials are entered as one-time masked UI values and are not retained in
  localStorage or sessionStorage. Mutation responses are redacted state, not
  credential echoes. Credential-store failures are compensated under the
  adapter/process lock or become an explicit recovery-required state.
- Data-source mutations are loopback-only by default: the server validates the
  Host and any browser Origin and requires a non-simple write-intent header.
  Originless local automation must still provide the loopback Host and header.
- `free_only` defaults to true. Billing model, credential presence, account
  plan/license, per-request budget, daily budget, monthly budget, enablement,
  and supported authentication transport are independent gates. Missing or
  unknown facts fail closed and are not counted as Provider-health failures.
- Money is represented as bounded exact Decimal strings. Usage reservations
  are atomically persisted under process/file locks and must be reconciled
  exactly once by an authorized test or future production transport. Current
  FRED/EIA, five Task 4 freemium adapters, and five paid adapters stop before
  reservation, so they create no open usage record.
- FRED and EIA do not put credentials into query parameters. Their configured
  boundary is `unsupported_credential_transport`; no undocumented header
  authentication was invented. Tushare's declared API is POST-only and the
  production GET-only safe client cannot execute it, so fake-POST parser tests
  are not a connected claim.
- Paid tests require the exact isolated test gate
  `VR_ALLOW_PAID_PROVIDER_TESTS=1`, fake transport, fake entitlement, and
  explicit budgets. The gate was absent in qualification. No Live paid test,
  purchase, charge, automatic upgrade, or current price/quota claim occurred.
- The nine enterprise entries are license-required static Catalog shells. No
  enterprise SDK, credential, health probe, direct request, account-plan
  inference, or connected state exists in this phase. A shell may only become
  a possible independent-evidence source after the applicable license is
  obtained and the integrated source is independently validated.

Provider-level truth is listed in the
[credentialed/freemium matrix](freemium-provider-matrix.md),
[paid matrix](paid-provider-matrix.md), and
[enterprise Catalog](enterprise-provider-catalog.md).
