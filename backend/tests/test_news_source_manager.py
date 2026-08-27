from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

import newsradar
import news_source_manager as sources


def _write_builtins(path: Path) -> None:
    path.write_text(json.dumps({
        "fetch": {"per_source": 6, "recent_days": 7},
        "default_region": "GLOBAL",
        "industries": [
            {"key": "ai", "name": "AI", "accent": "#fff"},
            {"key": "semi", "name": "半导体", "accent": "#000"},
        ],
        "sources": [
            {"name": "Builtin AI", "url": "https://example.com/ai.xml", "hint": "ai"},
            {"name": "Builtin Semi", "url": "https://example.com/semi.xml", "hint": "semi"},
        ],
    }, ensure_ascii=False), encoding="utf-8")


def _ok_probe(definition: dict) -> dict:
    return {
        "ok": True,
        "http_status": 200,
        "feed_format": "rss",
        "sample_title": "Public item",
        "sample_link": "https://publisher.example/item",
        "item_count": 1,
        "checked_at": "2026-08-27T00:00:00+00:00",
    }


def _manager(tmp_path: Path) -> sources.SourceManager:
    builtins = tmp_path / "news_sources.json"
    _write_builtins(builtins)
    return sources.SourceManager(builtins_path=builtins, data_dir=tmp_path / "data", probe=_ok_probe)


def test_custom_store_is_under_vr_data_dir_and_builtins_survive(tmp_path, monkeypatch):
    builtins = tmp_path / "news_sources.json"
    _write_builtins(builtins)
    monkeypatch.setenv("VR_DATA_DIR", str(tmp_path / "profile"))

    manager = sources.SourceManager(builtins_path=builtins, probe=_ok_probe)
    listing = manager.list_sources()

    assert manager.store_path == tmp_path / "profile" / "news-sources.custom.json"
    assert listing["store_status"] == "missing"
    assert listing["summary"] == {"total": 2, "built_in": 2, "custom": 0, "enabled": 2}
    assert all(row["built_in"] and row["enabled"] for row in listing["sources"])


def test_corrupt_store_is_reported_and_never_overwritten(tmp_path):
    manager = _manager(tmp_path)
    manager.store_path.parent.mkdir(parents=True)
    original = b'{"schema_version":1,"custom_sources":['
    manager.store_path.write_bytes(original)

    listing = manager.list_sources()
    assert listing["store_status"] == "corrupt"
    assert listing["summary"]["built_in"] == 2

    with pytest.raises(sources.SourceStoreCorruptError):
        manager.add_source({
            "source_type": "rss", "name": "Custom", "url": "https://feed.example/rss", "hint": "ai",
        })
    assert manager.store_path.read_bytes() == original


@pytest.mark.parametrize("payload", [
    b"\xff\xfe\x00\x00",
    json.dumps({
        "schema_version": 1,
        "disabled_builtin_ids": [],
        "custom_sources": [{
            "id": "manual-private",
            "source_type": "rss",
            "name": "Manual private source",
            "url": "http://127.0.0.1/feed",
            "hint": "ai",
            "region": "GLOBAL",
            "enabled": True,
        }],
        "health": {},
    }).encode("utf-8"),
    json.dumps({
        "schema_version": 1,
        "disabled_builtin_ids": [],
        "custom_sources": [{
            "id": "0000000000000000",
            "source_type": "rss",
            "name": 123,
            "url": "https://feed.example/rss",
            "hint": "ai",
            "region": "GLOBAL",
            "enabled": True,
            "created_at": "2026-08-28T00:00:00+00:00",
        }],
        "health": {},
    }).encode("utf-8"),
])
def test_non_utf8_or_semantically_invalid_store_degrades_without_runtime_merge_or_overwrite(tmp_path, payload):
    manager = _manager(tmp_path)
    manager.store_path.parent.mkdir(parents=True)
    manager.store_path.write_bytes(payload)

    listing = manager.list_sources()
    runtime = manager.runtime_config()

    assert listing["store_status"] == "corrupt"
    assert listing["summary"] == {"total": 2, "built_in": 2, "custom": 0, "enabled": 2}
    assert [row["name"] for row in runtime["sources"]] == ["Builtin AI", "Builtin Semi"]
    with pytest.raises(sources.SourceStoreCorruptError):
        manager.add_source({
            "source_type": "rss", "name": "Custom", "url": "https://feed.example/rss", "hint": "ai",
        })
    assert manager.store_path.read_bytes() == payload


def test_add_toggle_delete_and_runtime_merge_are_atomic(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(sources.os, "replace", recording_replace)
    custom = manager.add_source({
        "source_type": "rss", "name": "Custom AI", "url": "https://feed.example/rss", "hint": "ai",
    })
    assert custom["built_in"] is False and custom["enabled"] is True
    assert replacements and all(destination == manager.store_path for _, destination in replacements)
    assert not list(manager.store_path.parent.glob("*.tmp"))

    runtime = manager.runtime_config()
    assert [row["name"] for row in runtime["sources"]] == ["Builtin AI", "Builtin Semi", "Custom AI"]

    manager.set_enabled(custom["id"], False)
    assert "Custom AI" not in [row["name"] for row in manager.runtime_config()["sources"]]
    manager.set_enabled(custom["id"], True)
    manager.delete_source(custom["id"])
    assert manager.list_sources()["summary"]["custom"] == 0


def test_duplicate_source_url_has_a_distinct_conflict_error(tmp_path):
    manager = _manager(tmp_path)

    with pytest.raises(sources.DuplicateSourceError, match="URL 已存在"):
        manager.add_source({
            "source_type": "rss",
            "name": "Duplicate Builtin",
            "url": "https://example.com/ai.xml",
            "hint": "ai",
        })


def test_duplicate_canonicalization_strips_a_trailing_dns_dot(tmp_path):
    manager = _manager(tmp_path)

    with pytest.raises(sources.DuplicateSourceError, match="URL 已存在"):
        manager.add_source({
            "source_type": "rss",
            "name": "Duplicate trailing dot",
            "url": "https://example.com./ai.xml",
            "hint": "ai",
        })


def test_api_projection_never_exposes_sensitive_query_values(tmp_path):
    manager = _manager(tmp_path)
    secret = "stage-c-super-secret"
    custom = manager.add_source({
        "source_type": "rss",
        "name": "Signed feed",
        "url": f"https://feed.example/rss?api_key={secret}&page=1",
        "hint": "ai",
    })

    listing = manager.list_sources()
    projected = next(row for row in listing["sources"] if row["id"] == custom["id"])
    tested = manager.test_definition({
        "source_type": "rss",
        "name": "Signed feed test",
        "url": f"https://feed.example/rss?token={secret}",
        "hint": "ai",
    })

    assert "url" not in custom and "url" not in projected
    assert projected["display_url"] == "https://feed.example"
    assert secret not in json.dumps([custom, listing, tested], ensure_ascii=False)
    stored = json.loads(manager.store_path.read_text(encoding="utf-8"))
    assert stored["custom_sources"][0]["url"].endswith(f"api_key={secret}&page=1")


def test_public_source_projection_hides_path_query_and_url_userinfo(tmp_path):
    private_value = "private-segment"

    def projecting_probe(definition: dict) -> dict:
        return {
            **_ok_probe(definition),
            "final_url": definition["url"],
            "source_url": definition["url"],
            "error_message": (
                f"signature={private_value} "
                f"https://user:password@feed.example/private/{private_value}/rss?sig={private_value}"
            ),
        }

    builtins = tmp_path / "news_sources.json"
    _write_builtins(builtins)
    manager = sources.SourceManager(
        builtins_path=builtins,
        data_dir=tmp_path / "data",
        probe=projecting_probe,
    )
    tested = manager.test_definition({
        "source_type": "rss",
        "name": "Signed feed",
        "url": f"https://feed.example/private/{private_value}/rss?sig={private_value}&page=1",
        "hint": "ai",
    })

    assert tested["final_url"] == "https://feed.example"
    assert tested["source_url"] == "https://feed.example"
    serialized = json.dumps(tested, ensure_ascii=False)
    assert private_value not in serialized
    assert "user:password" not in serialized


def test_builtin_can_be_disabled_but_not_deleted(tmp_path):
    manager = _manager(tmp_path)
    builtin = manager.list_sources()["sources"][0]

    manager.set_enabled(builtin["id"], False)
    assert builtin["name"] not in [row["name"] for row in manager.runtime_config()["sources"]]
    with pytest.raises(sources.BuiltinSourceDeletionError):
        manager.delete_source(builtin["id"])


def test_unsupported_api_source_is_rejected_without_generic_json_guessing(tmp_path):
    manager = _manager(tmp_path)
    with pytest.raises(sources.SourceValidationError, match="显式适配器"):
        manager.add_source({
            "source_type": "api", "name": "Unknown JSON", "url": "https://api.example/data", "hint": "ai",
        })


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/feed",
    "http://[::1]/feed",
    "http://169.254.169.254/latest/meta-data",
    "file:///etc/passwd",
    "https://user:password@example.com/feed",
    "https://example.com/feed#fragment",
])
def test_public_url_validation_rejects_ssrf_and_credentials(url):
    with pytest.raises(sources.SourceValidationError):
        sources.validate_public_url(url)


def test_public_url_validation_rejects_dns_that_resolves_private(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
    ])
    with pytest.raises(sources.SourceValidationError, match="公网"):
        sources.validate_public_url("https://looks-public.example/feed")


def test_parse_rss_and_atom_require_title_and_link():
    rss = sources.parse_feed_payload(b"""<rss><channel><item><title>One</title><link>https://x.example/1</link></item></channel></rss>""")
    atom = sources.parse_feed_payload(b"""<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>Two</title><link href='https://x.example/2'/></entry></feed>""")
    assert (rss["feed_format"], rss["sample_title"], rss["sample_link"]) == ("rss", "One", "https://x.example/1")
    assert (atom["feed_format"], atom["sample_title"], atom["sample_link"]) == ("atom", "Two", "https://x.example/2")
    with pytest.raises(sources.SourceValidationError, match="title.*link"):
        sources.parse_feed_payload(b"<rss><channel><item><title>Missing link</title></item></channel></rss>")


def test_feed_response_size_and_redirect_count_are_bounded():
    class OversizedResponse:
        headers = {}

        def read(self, limit):
            return b"x" * limit

    with pytest.raises(sources.SourceValidationError, match="大小上限"):
        sources._read_bounded(OversizedResponse())

    redirects = sources._SafeRedirectHandler()
    redirects.count = sources.MAX_REDIRECTS
    with pytest.raises(sources.SourceValidationError, match="重定向"):
        redirects.redirect_request(None, None, 302, "Found", {}, "https://example.com/next")


def test_public_socket_connects_to_the_already_validated_resolved_address(monkeypatch):
    connected = []
    resolver_calls = []

    def resolver(host, port, **kwargs):
        resolver_calls.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    class FakeSocket:
        def __init__(self, *args): self.args = args
        def settimeout(self, timeout): self.timeout = timeout
        def bind(self, address): self.bound = address
        def connect(self, address): connected.append(address)
        def close(self): pass

    monkeypatch.setattr(sources.socket, "socket", FakeSocket)

    sock = sources._create_public_connection(("feed.example", 443), 7, resolver=resolver)

    assert sock is not None
    assert resolver_calls == [("feed.example", 443)]
    assert connected == [("93.184.216.34", 443)]


def test_rss_probe_uses_default_verified_tls_context(monkeypatch):
    marker = object()
    captured = {}
    payload = b"<rss><channel><item><title>One</title><link>https://x.example/1</link></item></channel></rss>"

    class Response:
        status = 200
        headers = {}

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def getcode(self): return 200
        def geturl(self): return "https://feed.example/rss"
        def read(self, limit): return payload

    class Opener:
        def open(self, request, timeout): return Response()

    monkeypatch.setattr(sources.ssl, "create_default_context", lambda: marker)
    def build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(sources.urllib.request, "build_opener", build_opener)
    resolver = lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    result = sources.probe_rss_source({"url": "https://feed.example/rss"}, resolver=resolver)
    assert result["ok"] is True
    assert isinstance(captured["handlers"][0], sources.urllib.request.ProxyHandler)
    assert captured["handlers"][0].proxies == {}
    assert isinstance(captured["handlers"][3], sources._PinnedHTTPSHandler)
    assert captured["handlers"][3]._context is marker


def test_pinned_https_handler_passes_verified_context_without_runtime_private_attributes(monkeypatch):
    marker = object()
    handler = sources._PinnedHTTPSHandler(context=marker, resolver=lambda *args, **kwargs: [])
    captured = {}

    def do_open(factory, request, **kwargs):
        captured.update(kwargs)
        return "opened"

    monkeypatch.setattr(handler, "do_open", do_open)

    assert handler.https_open(object()) == "opened"
    assert captured == {"context": marker}


def test_recorded_health_is_returned_without_exposing_secrets(tmp_path):
    manager = _manager(tmp_path)
    custom = manager.add_source({
        "source_type": "rss", "name": "Custom AI", "url": "https://feed.example/rss", "hint": "ai",
    })
    manager.record_statuses([{
        "source_id": custom["id"], "status": "failed", "error_type": "connection",
        "error_reason": "token=secret Authorization: Bearer abc C:\\Users\\private\\file",
        "last_success_at": None,
    }])
    row = next(item for item in manager.list_sources()["sources"] if item["id"] == custom["id"])
    serialized = json.dumps(row, ensure_ascii=False)
    assert row["health"]["status"] == "failed"
    assert "secret" not in serialized and "Bearer abc" not in serialized and "C:\\Users" not in serialized


def test_manually_edited_health_is_sanitized_in_memory_without_overwriting_store(tmp_path):
    manager = _manager(tmp_path)
    custom = manager.add_source({
        "source_type": "rss", "name": "Custom AI", "url": "https://feed.example/rss", "hint": "ai",
    })
    document = json.loads(manager.store_path.read_text(encoding="utf-8"))
    document["health"][custom["id"]]["status"] = "failed"
    document["health"][custom["id"]]["error_type"] = "connection"
    document["health"][custom["id"]]["error_message"] = "token=stage-c-secret C:\\Users\\private\\file"
    manager.store_path.write_text(json.dumps(document), encoding="utf-8")
    original = manager.store_path.read_bytes()

    listing = manager.list_sources()

    assert listing["store_status"] == "ok"
    assert "stage-c-secret" not in json.dumps(listing, ensure_ascii=False)
    assert "C:\\Users" not in json.dumps(listing, ensure_ascii=False)
    assert manager.store_path.read_bytes() == original


@pytest.mark.parametrize(("field", "value"), [
    ("status", ["failed"]),
    ("error_type", "private_exception"),
    ("checked_at", "not-an-iso-timestamp"),
    ("last_success_at", "not-an-iso-timestamp"),
    ("http_status", "200"),
])
def test_semantically_invalid_health_degrades_to_builtins_and_disables_writes(tmp_path, field, value):
    manager = _manager(tmp_path)
    custom = manager.add_source({
        "source_type": "rss", "name": "Custom AI", "url": "https://feed.example/rss", "hint": "ai",
    })
    document = json.loads(manager.store_path.read_text(encoding="utf-8"))
    document["health"][custom["id"]][field] = value
    manager.store_path.write_text(json.dumps(document), encoding="utf-8")
    original = manager.store_path.read_bytes()

    listing = manager.list_sources()
    runtime = manager.runtime_config()

    assert listing["store_status"] == "corrupt"
    assert listing["summary"] == {"total": 2, "built_in": 2, "custom": 0, "enabled": 2}
    assert [row["name"] for row in runtime["sources"]] == ["Builtin AI", "Builtin Semi"]
    with pytest.raises(sources.SourceStoreCorruptError):
        manager.set_enabled(custom["id"], False)
    assert manager.store_path.read_bytes() == original


def test_manager_runtime_radar_refresh_and_health_share_one_source_id(tmp_path, monkeypatch):
    builtins = tmp_path / "news_sources.json"
    _write_builtins(builtins)
    data_dir = tmp_path / "data"
    manager = sources.SourceManager(builtins_path=builtins, data_dir=data_dir, probe=_ok_probe)
    custom = manager.add_source({
        "source_type": "rss",
        "name": "Canonical custom",
        "url": "https://Feed.Example:443/custom.xml",
        "hint": "ai",
    })
    runtime_custom = next(row for row in manager.runtime_config()["sources"] if row["name"] == "Canonical custom")

    monkeypatch.setenv("VR_DATA_DIR", str(data_dir))
    monkeypatch.setattr(newsradar, "SOURCES_FILE", str(builtins))
    monkeypatch.setattr(newsradar, "CACHE_FILE", str(tmp_path / "radar.json"))
    monkeypatch.setattr(newsradar, "_fetch_source", lambda src, per, cutoff, redline: {
        "status": "ok",
        "items": [],
        "fetched_at": "2026-08-28T00:00:00+00:00",
        "error_type": None,
        "error_reason": None,
    })

    radar = newsradar.fetch_radar()
    refreshed = next(row for row in manager.list_sources()["sources"] if row["id"] == custom["id"])

    assert runtime_custom["id"] == custom["id"]
    assert newsradar.source_id(runtime_custom) == custom["id"]
    assert custom["id"] in {row["source_id"] for row in radar["source_statuses"]}
    assert refreshed["health"]["status"] == "ok"


def test_untrusted_probe_health_metadata_is_bounded_to_public_types():
    health = sources.SourceManager._health_from_probe({
        "ok": False,
        "checked_at": ["not", "a", "timestamp"],
        "http_status": "200",
        "error_type": ["private_exception"],
        "error_message": "signature=private-value https://user:password@feed.example/private/path",
    })

    assert health["status"] == "failed"
    assert health["http_status"] is None
    assert health["error_type"] == "unknown"
    assert sources._valid_iso_timestamp(health["checked_at"])
    assert "private-value" not in health["error_message"]
    assert "user:password" not in health["error_message"]
