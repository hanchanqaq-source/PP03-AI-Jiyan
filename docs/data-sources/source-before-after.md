# Phase 1 source Catalog before/after

## Scope and evidence boundary

Baseline is `9dfd8246290a04555ed1886ded92c39de9a001f4`; Phase 1 evidence is serialized at `d61090f2167ac421e7aafe27e333c0d8fbb7d629`. This comparison is registration evidence, not live-source evidence. No provider, RSS feed, credential, enterprise license, paid endpoint, or holding was read or changed for it.

| Measure | Baseline (`9dfd824`) | Phase 1 (`d61090f`) | Meaning |
| --- | ---: | ---: | --- |
| Existing RSS/Atom configurations | 108 | 108 | Preserved; `backend/news_sources.json` is unchanged. |
| Catalog families | not serialized (no Catalog) | 112 | 4 non-news families + 108 publisher families. |
| Catalog adapters | not serialized (no Catalog) | 114 | 6 non-news adapters + 108 feed adapters. |
| Capabilities | not serialized (no Catalog) | 8 | Stable Catalog capability registration. |
| Feed-capability adapters | not serialized (no Catalog) | 108 | Exact qualified feed registration count. |
| Eastmoney families | not serialized (no Catalog) | 1 | One `eastmoney` family only. |
| Eastmoney adapters | not serialized (no Catalog) | 3 | `eastmoney-direct`, `akshare-eastmoney`, `efinance-eastmoney`; the last is default-disabled. |
| Registration fingerprint | not applicable | `665d677dea1706a5c8ce0675a771de03d72f081f250e1a086dd3e40c82718fde` | Deterministic static registration digest. |
| Fingerprint after sample fund relation `017811` | not applicable | identical | Relation input is deliberately ignored by the registration fingerprint. |
| Live connected sources | not measured | not measured | No live validation occurred; do not substitute registration for connection. |

## What changed in Phase 1

Phase 1 introduced the holdings-independent Catalog, stable family/adapter/capability IDs, the health overlay bridge, compatible Catalog read APIs, and a family-first UI. The baseline retained the 108 RSS/Atom configuration records but had no Catalog serialization, so catalog counts cannot honestly be backfilled for it.

The observed-health overlay remains separate from registration. A missing observation is `unexamined`/`尚未体检`; `catalog_only` and `disabled` are configuration states, not health failures. `configured_reference` stays visible from the Catalog, while `observed_final_reference` exists only after a real, safe successful request. Both paths must be sanitized and never contain credential values, Authorization, Cookie, Token, or sensitive query parameters.

## Qualification assertions at Phase 1 HEAD

`backend/tests/test_data_source_catalog.py` asserts unique family IDs and adapter IDs, exactly 108 feed registrations, exactly one Eastmoney family with exactly three adapters, and the same registration fingerprint before/after `["017811"]`. The production contract was already GREEN when this qualification test was added, so no artificial RED was recorded. This is coverage for a pre-existing Phase 1 contract, not a runtime code change.

The matrix in [source-family-matrix.md](source-family-matrix.md) contains every registered family/adapter pair, capability routing, billing/key/license/default boundary, actual unobserved connection state, shell-only status, limits, and replacement rules.
