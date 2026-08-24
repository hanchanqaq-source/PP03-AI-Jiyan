# A2-W1 Phase 3 enterprise Provider Catalog

## Static boundary

These nine entries are static Catalog shells only. Every adapter is
`enterprise_license`, default-disabled, `license_required`, has no credential
environment name, no runtime registry factory, no SDK, no health probe, and no
network path. Family health is `null`; none is configured or connected. Actual
capabilities, delay, quota, redistribution rights, and cost depend on the
user's contract and remain unknown until separately authorized and integrated.

| Adapter ID | Catalog name | Roles | Advertised capability boundary |
| --- | --- | --- | --- |
| `bloomberg` | Bloomberg Data License/B-PIPE | `primary_data`, `market_data`, `macro_data` | `stock_snapshot`, `stock_history`, `stock_financials`, `macro_series`, `news_discovery` |
| `lseg` | LSEG Data Platform/Workspace | `primary_data`, `market_data`, `macro_data` | `stock_snapshot`, `stock_history`, `stock_financials`, `macro_series`, `news_discovery` |
| `factset` | FactSet | `primary_data`, `market_data`, `cross_check` | `stock_snapshot`, `stock_history`, `stock_financials`, `fund_holdings` |
| `wind` | Wind | `primary_data`, `market_data`, `macro_data` | `stock_snapshot`, `stock_history`, `stock_financials`, `fund_holdings`, `macro_series` |
| `choice` | Choice | `market_data`, `fallback_data`, `cross_check` | `stock_snapshot`, `stock_history`, `stock_financials`, `fund_holdings` |
| `ifind` | iFinD | `market_data`, `fallback_data`, `cross_check` | `stock_snapshot`, `stock_history`, `stock_financials`, `fund_holdings` |
| `morningstar-direct` | Morningstar Direct | `primary_data`, `cross_check` | `profile`, `nav_history`, `holdings`, `fund_holdings` |
| `sp-capital-iq` | S&P Capital IQ | `primary_data`, `market_data`, `cross_check` | `stock_snapshot`, `stock_history`, `stock_financials`, `macro_series` |
| `csmar` | CSMAR | `primary_data`, `cross_check` | `stock_history`, `stock_financials`, `fund_holdings`, `macro_series` |

The capability lists are routing/catalog boundaries, not proof that the user's
license includes those products. Enabling requires an explicit enterprise
license decision, approved SDK/transport work, and a new security and cost
review. This phase did not install an SDK, read a license, call an enterprise
service, or represent any shell as healthy.
Only after the applicable license is obtained and the resulting source is
independently validated could one of these entries possibly count as
independent evidence; the Catalog shell itself is not usable evidence.

See [licensing and usage boundaries](licensing-and-usage-boundaries.md) for the
common license and purchase rules.

## Phase 4 final status

The final Catalog still contains exactly nine `license_required` enterprise
shells. At `09c4145334b4502dde55185b8b72653c1e53d2f9` they have no
runtime factory, SDK, credential name, health probe or connection. Task 7 made
no licensed or enterprise request and read no entitlement. Registration and an
advertised capability boundary are not license coverage, health, connectivity
or admissible evidence.
