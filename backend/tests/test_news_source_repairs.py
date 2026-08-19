"""Task 6 regression guard for the no-change repair decision.

Any future feed migration must deliberately update this contract after proving
publisher identity and one-in/one-out discipline with its own RED/GREEN case.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import newsradar
import pytest


EXPECTED_CONFIG_HASH = "fbb2239676aa22d16a755780a80621a433b9ceb60f19499967440eac20c1f33e"
EXPECTED_SOURCE_IDS = frozenset("""
008707c0556f71bc 0739605a0ad3b617 0f594cc0ea5b2a2b 0f5baab1254d10ff
16ad2e105ad4df58 1815873038d2c2d7 183080275420ad58 1b79136c8634e736
1d0cac1e573dc061 1e7d2427e3b6b544 2232be9b281e977b 232aa8ffa9628eeb
24fd51d7e67d3778 2749128068067e66 2afd258afce65815 2f080609f735a112
313aea5bc1321ca0 32a28102a676c580 335d7ab524d21846 35a43559fe5cf574
39177cbe7653d141 3929b8db4b8ada59 3f5b60356cf96e79 3fb71517e26dc86e
46f90d3daa6d5418 475525004624cf6d 4cd1631448626c47 4deb82b3a3db7fb6
4ec6e685d9c8fe08 4fdb04f4cc0f0892 5875785e820000c4 587895b853ada36c
59056edfdcdf83d7 5c4a8594f6f66341 5d07e07f312cc70f 6135ff9733ecf9d5
62a82f773dccf12f 691b530c5e618b31 6a2e186b891ab2cb 6b195fa321259338
6cdecb222ce1243e 6f1d8221e9a0d627 6f6e19aecb8e9761 71b999d16723c012
739f54860b7b2146 748dd8262d0336b6 776887e7107a719f 79d68f94d68b4c97
7a3eb02aedca459a 7bfb3e8bcc5676e6 7c39ec31de57818b 7cf12bf9df06b361
7d4da2221027190a 7fec20f7d954a1ce 89af2bef11e51533 8a3da3907d7b121b
8a479d79f4e2ffb4 8aac644f73158689 8d3e5073fa1692a1 8d5fa4033ed31e04
90a7d0ba5f79bbb3 9195286e07ecf617 92ba53da29c406c2 965c5128a5db12e4
98e26f0be2ac816d 992d4182396561ae 9bfa4df282061764 9dd5136f8c85b2d7
a5fb7c29da6ec087 a6e9ca8efe07139b a7a0ee6421ff0d5c a7a3cabc10aa5241
ae0a91d1302c315b af3c7261885dccd1 b1575a162fe534ed b1fd1273fe956d70
bcec134592684591 bed6f05a0c400d40 bf1f7eadc41cfd9c c38a8cf77ae3fbe6
c6a889787a803395 c7cf23ecb89c8f42 c91c5cf39d2bbfcd cb83732db1151431
cbcee25f839422c5 d2c9381f4c1ebd06 d6518a19715b1938 d7461ff01cec7502
d8360e70661b2f81 d9446068f542351e d98bd074914ca555 da4e1f3ef1c8814c
df43bdc3a6481e35 e09466a6ab94eb0a e5fa1c3b7223cca1 e9780af8b41b4a97
ea5cf31f8cf75d8a ea731e0d790a42b8 eb1e1cb662ddcc39 ec0405c313f0ad70
eceece704d4276b3 f0231360ac33639d f35a3c91d3ab7411 f3bb9157e305e62b
f669e6165c992531 f866188ff19975be f9b24fe83f4a727c f9d263013a6a3f38
""".split())


@pytest.mark.parametrize(
    ("name", "host", "stable_source_id"),
    [
        ("36氪", "36kr.com", "7a3eb02aedca459a"),
        ("动点科技", "cn.technode.com", "3fb71517e26dc86e"),
        ("国际能源网", "www.in-en.com", "79d68f94d68b4c97"),
        ("虎嗅", "rss.huxiu.com", "6b195fa321259338"),
        ("钛媒体", "www.tmtpost.com", "e9780af8b41b4a97"),
        ("arXiv cs.AI", "export.arxiv.org", "f9d263013a6a3f38"),
        ("FierceBiotech", "www.fiercebiotech.com", "7c39ec31de57818b"),
        ("FiercePharma", "www.fiercepharma.com", "eb1e1cb662ddcc39"),
        ("WSJ Markets", "feeds.a.dj.com", "f866188ff19975be"),
    ],
)
def test_unrepaired_named_feed_keeps_publisher_identity(name, host, stable_source_id):
    """Catch accidental host/source-ID drift while an audit decision is observe-only."""
    sources = newsradar._load_source_config()["sources"]
    source = next(row for row in sources if row["name"] == name)

    assert urlsplit(source["url"]).hostname == host
    assert source["type"] == "rss"
    assert newsradar.source_id(source) == stable_source_id


def test_no_change_repair_audit_keeps_exact_108_source_identity_and_config():
    """Catch unreviewed source growth, deletion, identity drift or config edit."""
    raw = json.loads(Path(newsradar.SOURCES_FILE).read_text(encoding="utf-8"))
    sources = raw["sources"]

    assert len(sources) == 108
    assert hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() == EXPECTED_CONFIG_HASH
    assert {newsradar.source_id(source) for source in sources} == EXPECTED_SOURCE_IDS
