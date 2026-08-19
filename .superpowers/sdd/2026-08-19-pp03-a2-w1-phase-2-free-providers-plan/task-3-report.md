# Phase 2 Task 3 Report — Official Evidence Adapters

## Scope and commit

- Scope completed: SEC EDGAR JSON/index metadata and finite Chinese/Hong Kong official-link validation only.
- Commit subject: `feat(pp03): add official evidence adapters`.
- No Live requests were sent; every provider result used an injected deterministic fake transport and minimum public-shaped fixtures.

## Strict TDD evidence

- RED: after correcting a test-only parametrization collection error, the first focused run produced **26 expected failures**, all from the absent `sec_edgar` and `official_evidence` modules.
- RED refinement: two additional security cases failed as expected before their implementation: mismatched CIK/accession reached the fake transport, and a secret-query link was accepted.
- GREEN: `test_provider_sec_edgar.py`, `test_provider_official_evidence.py`, and `test_data_source_catalog.py` passed **34 tests**.
- Compatibility GREEN: provider, Catalog, SafeHttp, evidence verifier, and evidence service tests passed **90 tests**.
- Final deterministic backend regression: `pytest backend/tests -m "not live" -q -p no:cacheprovider` passed **552**, deselected **13** Live tests, with **1 existing Starlette TestClient deprecation warning**.
- `compileall` was not a pass/fail gate: it could not write existing `__pycache__` directories (`PermissionError: WinError 5`); source execution and the complete pytest regression above succeeded.

## Security and product boundaries

- SEC permits only validated CIK, accession, and 10-K/10-Q/8-K/13F inputs; it requests submissions, Company Facts, or a single filing `index.json`, never filing bodies or crawls.
- SEC User-Agent contains only `PP03-AI-Jiyan` and the approved public GitHub URL (no fabricated email); rate gate is 8 req/s, and Retry-After is capped at one retry and 60 seconds. A rejected official contact reports `unconfigured_contact`.
- Official links require HTTPS, standard port, exact domain-or-subdomain matching for the fixed official allowlist or explicitly injected company hosts. IDN/lookalikes, secret queries, downgrade/cross-publisher redirects, protected pages, unsupported media, and oversized bodies fail closed.
- Existing evidence-verifier publisher admission was deliberately unchanged: link validation metadata does not promote a source to verified evidence. Catalog records only `configured`/`unconfigured` identities; no fixture creates a connection claim.
- No keys, cookies, login data, portfolio data, `news_sources.json`, or protected-page content were read or changed.
