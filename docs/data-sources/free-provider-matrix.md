# A2-W1 Phase 2 Task 6 — free Provider matrix

The states below are intentionally separate: registered/configured describes
the Catalog, while connected is only a successful bounded public request. The
Before audit used a no-key, no-login, TLS-verified 8-second/500,000-byte
transport. Adapter outcomes are current observations, not finance truth or
evidence admission.

| Adapter | Family | Catalog state | Audit mode | Observed state | Connected | Boundary |
| --- | --- | --- | --- | --- | --- | --- |
| `baostock` | `baostock` | configured | adapter probe only | optional dependency unavailable | no | package absent; no installation or unbounded live login attempted |
| `gdelt` | `gdelt` | configured | minimal public fetch | timeout | no | collector/candidate only; cannot become independent evidence |
| `imf` | `imf` | catalog_only | adapter probe only | catalog_only | no | no live request by contract pending separately documented interface validation |
| `oecd` | `oecd` | configured | minimal public fetch | HTTP client error | no | public endpoint request was bounded; no fallback scraping |
| `sec-edgar` | `sec_edgar` | configured | minimal public fetch | authentication | no | public contact was not fabricated; no email, login, cookie or retry beyond contract |
| `world-bank` | `world_bank` | configured | minimal public fetch | schema_changed | no | endpoint response did not meet adapter's documented schema; no empty-success conversion |
| `yahoo-finance` | `yahoo_finance` | disabled | adapter probe only | optional dependency unavailable | no | non-official and personal-research-only; no installation or enabled use |

No Adapter is reported as connected in this task. The three ordinary public
fetch failures and the catalog/dependency states remain visible rather than
being converted into successful registration claims.
