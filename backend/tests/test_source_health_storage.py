from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from source_health.storage import SourceHealthStorage


UTC = timezone.utc
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def test_default_root_prefers_vr_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("VR_DATA_DIR", str(tmp_path / "data"))

    assert SourceHealthStorage().root == tmp_path / "data" / "source-health"


def test_default_root_falls_back_to_userprofile(monkeypatch, tmp_path):
    monkeypatch.delenv("VR_DATA_DIR", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))

    assert SourceHealthStorage().root == tmp_path / "profile" / ".vibe-research" / "source-health"


def test_summary_and_last_run_use_atomic_replace(monkeypatch, tmp_path):
    storage = SourceHealthStorage(root=tmp_path / "source-health")
    replaced = []
    real_replace = __import__("os").replace

    def record_replace(source, destination):
        replaced.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr("source_health.storage.os.replace", record_replace)

    storage.write_current_summary({"version": 1})
    storage.write_last_run({"run_id": "run-1"})

    assert [destination.name for _, destination in replaced] == ["current-summary.json", "last-run.json"]
    assert all(source.parent == destination.parent and source != destination for source, destination in replaced)
    assert json.loads(storage.current_summary_path.read_text(encoding="utf-8")) == {"version": 1}
    assert json.loads(storage.last_run_path.read_text(encoding="utf-8")) == {"run_id": "run-1"}
    assert not list(storage.root.glob("*.tmp"))


def test_concurrent_history_append_keeps_every_json_line_valid(tmp_path):
    storage = SourceHealthStorage(root=tmp_path / "source-health")

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda index: storage.append_history({"sample": index}, observed_at=NOW), range(100)))

    lines = storage.history_path(NOW.date()).read_text(encoding="utf-8").splitlines()
    documents = [json.loads(line) for line in lines]
    assert len(documents) == 100
    assert {document["sample"] for document in documents} == set(range(100))


def test_cleanup_history_retains_exactly_ninety_days_and_current_files(tmp_path):
    storage = SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW)
    storage.write_current_summary({"keep": True})
    storage.write_last_run({"keep": True})
    expired = storage.history_path(NOW - timedelta(days=91))
    boundary = storage.history_path(NOW - timedelta(days=90))
    recent = storage.history_path(NOW - timedelta(days=1))
    storage.history_root.mkdir(parents=True)
    for path in (expired, boundary, recent):
        path.write_text('{"fixture":true}\n', encoding="utf-8")

    removed = storage.cleanup_history()

    assert removed == [expired]
    assert not expired.exists()
    assert boundary.exists() and recent.exists()
    assert storage.current_summary_path.exists()
    assert storage.last_run_path.exists()


def test_append_history_does_not_leave_an_expired_backfill(tmp_path):
    storage = SourceHealthStorage(root=tmp_path / "source-health", now=lambda: NOW)

    expired = storage.append_history({"age": 91}, observed_at=NOW - timedelta(days=91))

    assert not expired.exists()
