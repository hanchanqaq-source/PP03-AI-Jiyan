# A2-W1 Phase 3 paid Provider matrix

## Runtime truth

The five paid adapters are bounded contract shells. They are all
`paid_api`, API-key based, default-disabled, and statically `unconfigured`.
No current price, quota, dataset entitlement, or account-plan fact is encoded
here. Missing keys return `unconfigured`; configured production paths return
`unsupported_credential_transport` before BudgetGuard or transport. Therefore
none is connected and no charge is possible from the production path.

| Adapter ID / family | Capability | Catalog roles | Declared credential | Deterministic implementation | Production state |
| --- | --- | --- | --- | --- | --- |
| `fmp` / `fmp` | `stock_snapshot` | `market_data`, `fallback_data`, `cross_check` | `FMP_API_KEY` | Request-bound, bounded Mock-only snapshot parser | Default-disabled; unconfigured or unsupported credential transport; not connected |
| `massive` / `massive` | `stock_history` | `market_data`, `fallback_data`, `cross_check` | `MASSIVE_API_KEY` | Request-bound, bounded Mock-only history parser | Default-disabled; unconfigured or unsupported credential transport; not connected |
| `tiingo` / `tiingo` | `stock_history` | `market_data`, `fallback_data`, `cross_check` | `TIINGO_API_KEY` | Request-bound, bounded Mock-only history parser | Default-disabled; unconfigured or unsupported credential transport; not connected |
| `eodhd` / `eodhd` | `stock_history` | `market_data`, `fallback_data`, `cross_check` | `EODHD_API_KEY` | Request-bound, bounded Mock-only history parser | Default-disabled; unconfigured or unsupported credential transport; not connected |
| `databento` / `databento` | `stock_history` | `market_data`, `fallback_data`, `cross_check` | `DATABENTO_API_KEY` | Request-bound, bounded Mock-only parser with dataset/schema/symbol identity binding | Default-disabled; unconfigured or unsupported credential transport; not connected |

Mock parser coverage is not Live connectivity or entitlement evidence. The
isolated test harness can exercise reservation and reconciliation only with an
exact test-local opt-in, fake transport, fake entitlement, and exact Decimal
cost. `VR_ALLOW_PAID_PROVIDER_TESTS` was absent during qualification; the fresh
no-key run made zero network calls. Production has no automatic purchase,
automatic plan upgrade, or fallback to an unlicensed endpoint.

See [licensing and usage boundaries](licensing-and-usage-boundaries.md) and the
[enterprise Provider Catalog](enterprise-provider-catalog.md).

## Phase 4 final status

All five paid shells remain registered, default-disabled, `unconfigured`, and
not connected at `09c4145334b4502dde55185b8b72653c1e53d2f9`.
`VR_ALLOW_PAID_PROVIDER_TESTS` was absent from the preserved Task 7 boundary;
no credential, entitlement, paid request, charge, reservation, purchase or
plan upgrade occurred. They contribute five rows to the exact 13
unconfigured-Key count but zero connected sources. The archive compatibility
repair changed no Provider, budget, billing or dependency file.
