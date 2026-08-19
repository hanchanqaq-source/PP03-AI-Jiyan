# Phase 1 source-family matrix

## Reading this matrix

This is a fixed Catalog record at Phase 1 HEAD `d61090f2167ac421e7aafe27e333c0d8fbb7d629`, not a live-provider report. `registered`, `configured`, and `catalog_only` describe registration/configuration boundaries only; none means connected. No live request, paid request, credential validation, or health run was made for this qualification. Actual connection for every row below is therefore **unobserved**; missing observations must render `unexamined`/`尚未体检`, not a fabricated success or failure.

Stable identity is `source_family_id + adapter_id + capability_id`; display names and fund holdings do not form identity. `configured_reference` is a catalog public reference and remains available after a failed probe. `observed_final_reference` is absent until a successful, safely redirected request and must be sanitized; neither credential values, cookies, authorization fields, tokens, nor sensitive query parameters belong in this document.

`configured` means a public, no-key catalog path is registered, not currently connected. `catalog_only` means registered but not a Phase 1 runtime integration. `disabled` means default-off and is not a health failure. No Phase 1 row is `connected`, `unconfigured`, or `license_required`.

## Fixed registration snapshot

| Field | Value |
| --- | --- |
| Families | 112 |
| Adapters | 114 |
| Capabilities | 8 |
| RSS/Atom feed adapters | 108 |
| Registration fingerprint | `665d677dea1706a5c8ce0675a771de03d72f081f250e1a086dd3e40c82718fde` |
| Family / adapter ID uniqueness | deterministic qualification assertion |
| Holding relation effect | none; `[]` and `["017811"]` have the identical fingerprint |

## Non-news families and adapters

All non-news adapters are `free_no_key`, `auth_type=none`, key requirement **none**, and are catalog descriptors only in Phase 1 (actual connection: unobserved). Public upstream terms apply; quota is not declared, so reasonable public-entry rate limiting is required. No automatic purchase or upgrade is permitted.

| Family ID / role / independent evidence | Adapter ID / capability IDs | Catalog/default | License, limits, and replacement |
| --- | --- | --- | --- |
| `eastmoney` / `primary_data`, `fallback_data`, `market_data` / **no** | `eastmoney-direct` / `search`, `profile`, `nav_history`, `holdings`, `industry_allocation`, `stock_snapshot` | `configured` / enabled | Public Eastmoney entry. Primary path; no live connection asserted. |
| `eastmoney` / same family / **no** | `akshare-eastmoney` / `search`, `profile`, `nav_history`, `holdings`, `industry_allocation` | `configured` / enabled | AKShare is an Eastmoney access path, not independent evidence; fallback to direct path only. |
| `eastmoney` / same family / **no** | `efinance-eastmoney` / `stock_snapshot` | `disabled` / **disabled** | efinance is an Eastmoney access path, not independent evidence; shell-only catalog registration in Phase 1. License and upstream terms must be checked before any enablement; it is not a replacement or a second evidence family. |
| `tencent` / `primary_data`, `market_data` / yes | `tencent-quote` / `stock_snapshot` | `configured` / enabled | Public Tencent quote entry; primary `stock_snapshot`, with Eastmoney configured as fallback. Connection unobserved. |
| `cninfo` / `official_evidence`, `primary_data` / yes | `cninfo-industry` / `stock_industry_classification` | `configured` / enabled | Public CNInfo entry; primary official classification. Connection unobserved. |
| `danjuan` / `fallback_data` / yes | `akshare-danjuan` / `search`, `profile`, `nav_history`, `holdings` | `configured` / enabled | AKShare access path to public Danjuan data; fallback for the listed fund capabilities. Connection unobserved. |

The configured public references are, respectively, the Catalog's Eastmoney fund entry, Tencent quote entry, CNInfo web API entry, and Danjuan entry. They are intentionally described rather than copied with parameters: the API/UI must use sanitized `configured_reference` and only conditionally expose sanitized `observed_final_reference`.

## Capabilities and routing

| Capability ID | Role / category | Probe / freshness | Unit and frequency | Primary / fallback / cross-check |
| --- | --- | --- | --- | --- |
| `feed` | news publisher / news | enabled / no fixed max age | publisher-native / publisher-native | — / — / — |
| `search` | fund | enabled / 86,400 s | provider-native / daily | eastmoney / danjuan / — |
| `profile` | fund | enabled / 86,400 s | provider-native / daily | eastmoney / danjuan / — |
| `nav_history` | fund | enabled / 604,800 s | fund_nav / daily | eastmoney / danjuan / — |
| `holdings` | fund | enabled / 17,280,000 s | weight_pct / quarterly | eastmoney / danjuan / — |
| `industry_allocation` | industry | enabled / 17,280,000 s | weight_pct / quarterly | eastmoney / — / — |
| `stock_snapshot` | quote | enabled / 259,200 s | market_quote / intraday | tencent / eastmoney / — |
| `stock_industry_classification` | industry | enabled / 34,560,000 s | classification / event_driven | cninfo / — / — |

`holdings`, official `industry_allocation`, and `stock_industry_classification` remain different evidence layers. The Catalog neither reads nor mutates holding amounts, cost, notes, accounts, or credentials; a fund relation may add only a relation overlay.

## Registered RSS/Atom publisher families (108)

Each line is one complete family/adapter pair: role `news_publisher`; capability `feed`; billing `free_no_key`; key requirement none; public-publisher terms/license boundary; default enabled; catalog status `catalog_only`; actual connection **unobserved**; shell-only Catalog registration; public-rate limit not declared; no replacement relationship asserted. These are registered sources, not connected data and not live validation.

```text
news-publisher:0051088cd113ee41 -> news-feed:0051088cd113ee41; news-publisher:01d47177bbb5a9b7 -> news-feed:01d47177bbb5a9b7; news-publisher:048c8b8b6e600c9f -> news-feed:048c8b8b6e600c9f; news-publisher:0494a2511e3013bc -> news-feed:0494a2511e3013bc
news-publisher:0774fcc5fc3794bd -> news-feed:0774fcc5fc3794bd; news-publisher:0f44c1efa9c37eaa -> news-feed:0f44c1efa9c37eaa; news-publisher:11d5b4381e9d97e3 -> news-feed:11d5b4381e9d97e3; news-publisher:1563fae271e3e425 -> news-feed:1563fae271e3e425
news-publisher:16365a78036fef06 -> news-feed:16365a78036fef06; news-publisher:16698ba3bc825922 -> news-feed:16698ba3bc825922; news-publisher:18c8e9f8d2c151ca -> news-feed:18c8e9f8d2c151ca; news-publisher:1a3a9a5da2f43099 -> news-feed:1a3a9a5da2f43099
news-publisher:1a5edf2a611e02a8 -> news-feed:1a5edf2a611e02a8; news-publisher:1ae51fbdc8fda881 -> news-feed:1ae51fbdc8fda881; news-publisher:1bb722c912f2826f -> news-feed:1bb722c912f2826f; news-publisher:1c94de9e5ea878ce -> news-feed:1c94de9e5ea878ce
news-publisher:1ffef9b1b5324ce9 -> news-feed:1ffef9b1b5324ce9; news-publisher:211d6864a01930ca -> news-feed:211d6864a01930ca; news-publisher:21f51e94e0eef7f8 -> news-feed:21f51e94e0eef7f8; news-publisher:231288ad4148445f -> news-feed:231288ad4148445f
news-publisher:2daf19aec31cf82d -> news-feed:2daf19aec31cf82d; news-publisher:2f7609418abd4431 -> news-feed:2f7609418abd4431; news-publisher:3531b2babab48bbc -> news-feed:3531b2babab48bbc; news-publisher:36fe69de46a6da8e -> news-feed:36fe69de46a6da8e
news-publisher:389198427eb41e80 -> news-feed:389198427eb41e80; news-publisher:39732e24999b00bd -> news-feed:39732e24999b00bd; news-publisher:3de5914ed6bbe860 -> news-feed:3de5914ed6bbe860; news-publisher:3e8b7e93bac65cdc -> news-feed:3e8b7e93bac65cdc
news-publisher:3f386f9405e9653f -> news-feed:3f386f9405e9653f; news-publisher:40594b4d7771aff0 -> news-feed:40594b4d7771aff0; news-publisher:4226dc734920c03e -> news-feed:4226dc734920c03e; news-publisher:466aed115f3f51fb -> news-feed:466aed115f3f51fb
news-publisher:480859d19fc78bf3 -> news-feed:480859d19fc78bf3; news-publisher:486c0d75a16977f5 -> news-feed:486c0d75a16977f5; news-publisher:4af9e945021feab2 -> news-feed:4af9e945021feab2; news-publisher:4c71a74e0d724d40 -> news-feed:4c71a74e0d724d40
news-publisher:4d6b00edf9d5d920 -> news-feed:4d6b00edf9d5d920; news-publisher:4de0812d5c4e9b2c -> news-feed:4de0812d5c4e9b2c; news-publisher:504c12c8dd05860e -> news-feed:504c12c8dd05860e; news-publisher:522c1003e6dae927 -> news-feed:522c1003e6dae927
news-publisher:541883247713529d -> news-feed:541883247713529d; news-publisher:5510ec397dac058d -> news-feed:5510ec397dac058d; news-publisher:58757f8a1fa11a97 -> news-feed:58757f8a1fa11a97; news-publisher:6c9d86df058a8333 -> news-feed:6c9d86df058a8333
news-publisher:71b7903b323e8dd7 -> news-feed:71b7903b323e8dd7; news-publisher:744b2d80d794bf43 -> news-feed:744b2d80d794bf43; news-publisher:77af7120924120c9 -> news-feed:77af7120924120c9; news-publisher:77e8c2f83871d290 -> news-feed:77e8c2f83871d290
news-publisher:786725f6b623e7b8 -> news-feed:786725f6b623e7b8; news-publisher:78a150ff44301ec4 -> news-feed:78a150ff44301ec4; news-publisher:7c33cd9b82dd28d8 -> news-feed:7c33cd9b82dd28d8; news-publisher:7c793c36bb9c8fde -> news-feed:7c793c36bb9c8fde
news-publisher:7e7e124f6df1bde7 -> news-feed:7e7e124f6df1bde7; news-publisher:7eb899ef93852909 -> news-feed:7eb899ef93852909; news-publisher:7f0f511d84cdc486 -> news-feed:7f0f511d84cdc486; news-publisher:81b6af1f0b35fb47 -> news-feed:81b6af1f0b35fb47
news-publisher:840cbd7babfff720 -> news-feed:840cbd7babfff720; news-publisher:86237b6a249e005d -> news-feed:86237b6a249e005d; news-publisher:87681bdeb5f4914f -> news-feed:87681bdeb5f4914f; news-publisher:88f59740a8fd93d6 -> news-feed:88f59740a8fd93d6
news-publisher:89b8350a1aa3032c -> news-feed:89b8350a1aa3032c; news-publisher:8fc1135ff75d5615 -> news-feed:8fc1135ff75d5615; news-publisher:907347c00db5642f -> news-feed:907347c00db5642f; news-publisher:9777cd19ca4d1aee -> news-feed:9777cd19ca4d1aee
news-publisher:9967972beb0b0f6c -> news-feed:9967972beb0b0f6c; news-publisher:9af0984b6de3e90e -> news-feed:9af0984b6de3e90e; news-publisher:9b4f6c751eda85b1 -> news-feed:9b4f6c751eda85b1; news-publisher:9d9d3cb8b27a724e -> news-feed:9d9d3cb8b27a724e
news-publisher:9e13357125f37454 -> news-feed:9e13357125f37454; news-publisher:a10d342e0613a631 -> news-feed:a10d342e0613a631; news-publisher:a13909853a01845e -> news-feed:a13909853a01845e; news-publisher:a74e33ef4162433e -> news-feed:a74e33ef4162433e
news-publisher:ae18d276c192779b -> news-feed:ae18d276c192779b; news-publisher:b682ad65b783c6d1 -> news-feed:b682ad65b783c6d1; news-publisher:b78dcc8aa442c393 -> news-feed:b78dcc8aa442c393; news-publisher:b95cceec8a8e5d80 -> news-feed:b95cceec8a8e5d80
news-publisher:ba1c8fdfa5ba3894 -> news-feed:ba1c8fdfa5ba3894; news-publisher:ba5f6b4a082336b1 -> news-feed:ba5f6b4a082336b1; news-publisher:bc1730e2460d7ed8 -> news-feed:bc1730e2460d7ed8; news-publisher:bc348014d44bf864 -> news-feed:bc348014d44bf864
news-publisher:bf3ecb9a697e7347 -> news-feed:bf3ecb9a697e7347; news-publisher:bf75e74a8ed7d847 -> news-feed:bf75e74a8ed7d847; news-publisher:c4bc0e3c7822500d -> news-feed:c4bc0e3c7822500d; news-publisher:c4eed9dffa3a1cbf -> news-feed:c4eed9dffa3a1cbf
news-publisher:ca3be71410e40cfb -> news-feed:ca3be71410e40cfb; news-publisher:ca8571f6fafdc0b1 -> news-feed:ca8571f6fafdc0b1; news-publisher:cbddb7ac49d4de6e -> news-feed:cbddb7ac49d4de6e; news-publisher:ce6e059159a0eb83 -> news-feed:ce6e059159a0eb83
news-publisher:cee6647fc4c29739 -> news-feed:cee6647fc4c29739; news-publisher:d3eeaa5983af9719 -> news-feed:d3eeaa5983af9719; news-publisher:d59b13c3c66bd69f -> news-feed:d59b13c3c66bd69f; news-publisher:d65b5dbc8e3376e4 -> news-feed:d65b5dbc8e3376e4
news-publisher:d951b7efe87ae825 -> news-feed:d951b7efe87ae825; news-publisher:da89adc483bac0bf -> news-feed:da89adc483bac0bf; news-publisher:db4a54cfb6a762e9 -> news-feed:db4a54cfb6a762e9; news-publisher:e0800c83124fb2af -> news-feed:e0800c83124fb2af
news-publisher:e2f1905a21198f95 -> news-feed:e2f1905a21198f95; news-publisher:e7c4b2118c6ceb07 -> news-feed:e7c4b2118c6ceb07; news-publisher:e855a4310df1092f -> news-feed:e855a4310df1092f; news-publisher:ee857dda513b5786 -> news-feed:ee857dda513b5786
news-publisher:ee984777a6bbc364 -> news-feed:ee984777a6bbc364; news-publisher:f1b048d867f08f8e -> news-feed:f1b048d867f08f8e; news-publisher:f2374e3d8b3a2d58 -> news-feed:f2374e3d8b3a2d58; news-publisher:f8fab42b0786f30b -> news-feed:f8fab42b0786f30b
news-publisher:f9846f6b444ed0fe -> news-feed:f9846f6b444ed0fe; news-publisher:fb15e06f0eb48e62 -> news-feed:fb15e06f0eb48e62; news-publisher:fce81b3767202bbd -> news-feed:fce81b3767202bbd; news-publisher:ff95a15497fbe796 -> news-feed:ff95a15497fbe796
```

The 108 pairs derive from the exact five-field configured identity (`hint`, `name`, `url`, `language`, `region`) and are sorted by stable ID. They preserve the unchanged `backend/news_sources.json` registrations; duplicate display names or URL text never imply duplicate Catalog IDs.
