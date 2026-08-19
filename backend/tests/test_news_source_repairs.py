"""Task 6 regression guard for the no-change repair decision.

Any future feed migration must deliberately update this contract after proving
publisher identity and one-in/one-out discipline with its own RED/GREEN case.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import newsradar
import pytest


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


def test_no_change_repair_audit_keeps_qualified_feed_count_at_108():
    """Catch unreviewed source growth or deletion in a task with no validated replacement."""
    assert len(newsradar._load_source_config()["sources"]) == 108
