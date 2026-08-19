# A2-W1 Phase 3 credentialed and freemium Provider matrix

## Evidence boundary

This matrix records Catalog identity and deterministic runtime truth at
`0f04e493d3fb071c105cd2f9ac78b3665369637f`. Registration, a configured
credential, a parser fixture, and a connected Provider are different states.
No real credential, account plan, licensed dataset, Live request, or paid
request was used. A fresh isolated no-key run probed all 13 credentialed
runtime adapters and 17 capabilities with a trapping transport: every probe
returned `unconfigured`, none was a health failure, and network calls were 0.

All eight rows below are default-disabled and have static Catalog status
`unconfigured`. `free_key` and `freemium` are Catalog billing classes, not
claims about current price, quota, or account entitlement.

| Adapter ID / family | Capabilities | Catalog roles | Billing / credential | No-key runtime | Configured production boundary | Parser evidence | Connected |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `fred` / `fred` | `macro_series` | `macro_data`, `cross_check` | `free_key`; `FRED_API_KEY` | `unconfigured`; zero network | `unsupported_credential_transport` after exact request validation and before BudgetGuard, cache, request parameters, or HTTP | Deterministic observation fixtures preserve series, dates, revisions, units, frequency, missing/stale/cache truth | No |
| `eia` / `eia` | `macro_series` | `macro_data`, `cross_check` | `free_key`; `EIA_API_KEY` | `unconfigured`; zero network | `unsupported_credential_transport` after exact request validation and before BudgetGuard, cache, request parameters, or HTTP | Deterministic fixtures preserve series, period, units, frequency, and actual/forecast/cache truth | No |
| `tushare` / `tushare` | `fund_holdings`, `stock_history`, `stock_financials`, `index_calendar` | `market_data`, `fallback_data`, `cross_check` | `freemium`; `TUSHARE_TOKEN` | `unconfigured`; zero network | Budget, Free-only, cost, and capability gates remain fail-closed. The official contract is POST-only, while production `SafeHttpClient` is GET-only; therefore no production connection is claimed | Bounded parser fixtures and fake-POST tests only; capability denial remains an account-plan state, not a health failure | No |
| `alpha-vantage` / `alpha_vantage` | `stock_history` | `fallback_data`, `market_data`, `cross_check` | `freemium`; `ALPHA_VANTAGE_API_KEY` | `unconfigured`; zero network | `unsupported_credential_transport` before request building, BudgetGuard, cache, or transport | Literal bounded response fixtures; low-frequency fallback only | No |
| `finnhub` / `finnhub` | `stock_snapshot`, `news_discovery` | `market_data`, `fallback_data`, `collector`, `candidate` | `freemium`; `FINNHUB_API_KEY` | `unconfigured`; zero network | `unsupported_credential_transport` before request building, BudgetGuard, cache, or transport | Literal bounded response fixtures; publisher identity remains the origin identity | No |
| `twelve-data` / `twelve_data` | `stock_history` | `fallback_data`, `market_data`, `cross_check` | `freemium`; `TWELVE_DATA_API_KEY` | `unconfigured`; zero network | `unsupported_credential_transport` before request building, BudgetGuard, cache, or transport | Literal bounded fixtures preserve response-reported interval, timezone, unit, and credits | No |
| `nasdaq-data-link` / `nasdaq_data_link` | `macro_series` | `macro_data`, `fallback_data`, `cross_check` | `freemium`; `NASDAQ_DATA_LINK_API_KEY` | `unconfigured`; zero network | `unsupported_credential_transport` before request building, BudgetGuard, cache, or transport | Literal bounded fixtures record dataset-access observations; they do not prove Premium entitlement | No |
| `news-api` / `news_api` | `news_discovery` | `collector`, `candidate` | `freemium`; `NEWS_API_KEY` | `unconfigured`; zero network | `unsupported_credential_transport` before request building, BudgetGuard, cache, or transport | Literal bounded discovery fixtures preserve original publisher metadata | No |

Finnhub and NewsAPI are discovery collectors. Neither is an independent
evidence source, and neither may turn collected content into verified evidence
without the original publisher chain. FRED and EIA were specifically hardened
after a prepared-URL review: credentials can no longer enter a query string or
`PreparedRequest.url`. A future secret-safe authentication transport requires
separate evidence and review; this phase does not infer header authentication.

See [licensing and usage boundaries](licensing-and-usage-boundaries.md) for
credential, Free-only, budget, and account-plan rules, and the
[paid Provider matrix](paid-provider-matrix.md) for the separate paid shells.
