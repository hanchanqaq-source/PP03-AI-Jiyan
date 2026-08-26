from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "scripts" / "acceptance" / "run_v02_w3_industry_acceptance.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("v02_w3_acceptance_runner", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_bootstrap_places_every_runtime_path_under_the_absolute_acceptance_root() -> None:
    runner = _load_runner()

    assert runner.ACCEPTANCE_ROOT.is_absolute()
    assert runner.ACCEPTANCE_ROOT == (
        REPO_ROOT / ".tmp" / "acceptance" / "v0.2-w3"
    ).resolve()
    for name in runner.ISOLATED_ENVIRONMENT_VARIABLES:
        value = Path(os.environ[name]).resolve()
        assert value.is_relative_to(runner.ACCEPTANCE_ROOT)
    assert os.environ["VR_ALLOW_PAID_PROVIDER_TESTS"] == "0"
    assert os.environ["VR_OFFLINE"] == "1"
    assert os.environ["VR_SOURCE_HEALTH_STARTUP"] == "0"


def test_child_environment_rejects_any_nonempty_credential_like_variable() -> None:
    runner = _load_runner()
    unsafe = runner.clean_child_environment()
    unsafe["PROVIDER_API_KEY"] = "must-not-be-read-or-forwarded"

    with pytest.raises(runner.AcceptanceBoundaryError, match="credential"):
        runner.validate_child_environment(unsafe)


def test_runtime_fixture_copy_must_be_inside_acceptance_root() -> None:
    runner = _load_runner()

    with pytest.raises(runner.AcceptanceBoundaryError, match="fixture"):
        runner.copy_source_fixture(REPO_ROOT / ".tmp" / "outside-task10-fixture.json")


def test_source_fixture_is_tracked_input_and_can_never_be_a_cleanup_target() -> None:
    runner = _load_runner()
    source = runner.SOURCE_FIXTURE.resolve()

    assert source.is_file()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(source.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    # During RED/GREEN the source may be uncommitted, but it must be a repository
    # input and never under the disposable acceptance root.
    assert tracked.returncode in {0, 1}
    assert not source.is_relative_to(runner.ACCEPTANCE_ROOT)
    with pytest.raises(runner.AcceptanceBoundaryError, match="source fixture"):
        runner.validate_cleanup_targets([source])


@pytest.mark.parametrize(
    "entry",
    [
        {"url": "https://enterprise.example/api", "method": "GET"},
        {"url": "https://paid.example/prices", "method": "GET"},
        {"url": "http://127.0.0.1:49152/api/industry-research/storage/refresh", "method": "POST"},
    ],
)
def test_network_gate_rejects_enterprise_paid_or_live_refresh_requests(entry: dict[str, str]) -> None:
    runner = _load_runner()

    with pytest.raises(runner.AcceptanceBoundaryError, match="network"):
        runner.validate_network_log([entry], allowed_origins={"http://127.0.0.1:49152"})


def test_network_gate_allows_loopback_reads_and_exact_product_font_assets() -> None:
    runner = _load_runner()
    summary = runner.validate_network_log(
        [
            {"url": "http://127.0.0.1:49152/industry-research", "method": "GET"},
            {"url": "http://127.0.0.1:49152/api/industry-research/storage?window_days=90", "method": "GET"},
            {"url": "http://127.0.0.1:49153/api/industry-research/robotics?window_days=30", "method": "GET"},
            {
                "url": "https://fonts.googleapis.com/css2?family=Inter:wght@400&display=swap",
                "method": "GET",
            },
            {
                "url": "https://fonts.gstatic.com/s/inter/v20/example.woff2",
                "method": "GET",
            },
        ],
        allowed_origins={"http://127.0.0.1:49152", "http://127.0.0.1:49153"},
    )

    assert summary == {
        "loopback_request_count": 3,
        "static_font_request_count": 2,
        "static_font_origins": ["https://fonts.googleapis.com", "https://fonts.gstatic.com"],
    }


@pytest.mark.parametrize(
    "entry",
    [
        {"url": "https://fonts.googleapis.com/metadata/fonts", "method": "GET"},
        {"url": "https://fonts.googleapis.com/css2?family=Inter", "method": "POST"},
        {"url": "https://fonts.gstatic.com/s/inter/v20/example.js", "method": "GET"},
        {"url": "https://fonts.gstatic.com/s/inter/v20/example.woff2?token=secret", "method": "GET"},
    ],
)
def test_network_gate_rejects_non_font_traffic_on_static_font_origins(entry: dict[str, str]) -> None:
    runner = _load_runner()

    with pytest.raises(runner.AcceptanceBoundaryError, match="network"):
        runner.validate_network_log([entry], allowed_origins={"http://127.0.0.1:49152"})


def test_cleanup_rejects_outside_paths_and_accepts_owned_disposable_descendants() -> None:
    runner = _load_runner()
    owned = runner.ACCEPTANCE_ROOT / "results" / "owned.json"

    assert runner.validate_cleanup_targets([owned]) == (owned.resolve(),)
    with pytest.raises(runner.AcceptanceBoundaryError, match="cleanup"):
        runner.validate_cleanup_targets([REPO_ROOT / ".tmp" / "outside-task10-cleanup.json"])


def test_default_app_rejects_demo_and_only_explicit_fixture_service_allows_it(monkeypatch) -> None:
    runner = _load_runner()
    service = runner.build_fixture_service()

    import app as app_module
    import industry_research.api as industry_api

    monkeypatch.setattr(
        industry_api,
        "create_production_industry_research_service",
        lambda: service,
    )
    production = TestClient(
        app_module.create_app(),
        base_url="http://127.0.0.1:49153",
    ).get("/api/industry-research/storage?window_days=90")
    acceptance = TestClient(
        app_module.create_app(industry_research_service=service),
        base_url="http://127.0.0.1:49153",
    ).get("/api/industry-research/storage?window_days=90")

    assert production.status_code == 503
    assert production.json() == {"detail": "demo_fixture_rejected"}
    assert acceptance.status_code == 200
    assert acceptance.json()["displayed_trusted_report"]["demo"] is True


def test_fixture_loader_uses_storage_publication_api_and_preserves_lineage() -> None:
    runner = _load_runner()
    service = runner.build_fixture_service()

    response = service.read_report("storage", 90)

    assert service.storage.load_current("storage") == response.displayed_trusted_report
    assert response.displayed_trusted_report is not None
    assert response.displayed_trusted_report.demo is True
    assert response.displayed_trusted_report.trusted_snapshot_id == "DEMO-S-TRUSTED-001"
    assert response.refresh_run.displayed_trusted_snapshot_id == "DEMO-S-TRUSTED-001"


def test_fixture_document_contains_no_private_financial_fields() -> None:
    runner = _load_runner()
    document = json.loads(runner.SOURCE_FIXTURE.read_text(encoding="utf-8"))
    forbidden = {"amount", "cost", "account", "notes", "note", "password", "api_key", "token"}

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()), set())
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value), set())
        return set()

    assert not (keys(document) & forbidden)


def test_dynamic_ports_are_reserved_by_the_os_on_distinct_loopback_sockets() -> None:
    runner = _load_runner()

    backend, frontend = runner.reserve_loopback_ports()
    try:
        assert backend.host == frontend.host == "127.0.0.1"
        assert backend.port != frontend.port
        assert backend.port > 0 and frontend.port > 0
        assert backend.socket.getsockname() == (backend.host, backend.port)
        assert frontend.socket.getsockname() == (frontend.host, frontend.port)
    finally:
        backend.close()
        frontend.close()


def test_playwright_commands_are_pinned_project_local_and_lockfile_neutral() -> None:
    runner = _load_runner()
    install, browser_install = runner.playwright_install_commands()

    assert install == [
        "npm", "install", "--prefix", str(runner.PLAYWRIGHT_TOOLS),
        "--no-save", "playwright@1.62.1",
    ]
    assert browser_install == [
        str(runner.PLAYWRIGHT_TOOLS / "node_modules" / ".bin" / "playwright.cmd"),
        "install", "chromium",
    ]
    assert runner.PLAYWRIGHT_TOOLS.is_relative_to(runner.ACCEPTANCE_ROOT)
    assert runner.PLAYWRIGHT_BROWSERS.is_relative_to(runner.ACCEPTANCE_ROOT)


def test_browser_script_has_exact_preflight_loader_and_single_browser_lifecycle() -> None:
    runner = _load_runner()
    source = runner.BROWSER_SCRIPT.read_text(encoding="utf-8")

    assert "createRequire(path.join(toolsRoot, \"package.json\"))" in source
    assert 'packageJson.version !== "1.62.1"' in source
    assert "PLAYWRIGHT_BROWSERS_PATH" in source
    assert source.count("chromium.launchPersistentContext") == 1
    assert "--preflight" in source
    assert "route(" not in source


def test_backend_and_frontend_phases_use_only_the_approved_child_commands() -> None:
    runner = _load_runner()

    assert runner.backend_test_command() == [
        sys.executable, "-m", "pytest", "backend/tests", "-m", "not live",
        "-q", "-p", "no:cacheprovider",
    ]
    assert runner.frontend_test_commands() == [
        ["npm", "run", "test:run"],
        ["npm", "run", "test:legacy"],
        ["npm", "run", "build"],
    ]


def test_cleanup_plan_contains_only_disposable_acceptance_descendants() -> None:
    runner = _load_runner()
    targets = runner.cleanup_targets()

    assert targets
    assert runner.SOURCE_FIXTURE not in targets
    assert runner.PLAYWRIGHT_TOOLS in targets
    assert runner.PLAYWRIGHT_BROWSERS in targets
    assert all(target.is_relative_to(runner.ACCEPTANCE_ROOT) for target in targets)
    assert all(not target.is_relative_to(REPO_ROOT / "docs") for target in targets)


def test_fixture_reports_keep_three_differential_templates_and_chinese_labels() -> None:
    runner = _load_runner()
    service = runner.build_fixture_service()

    storage = service.read_report("storage", 90).displayed_trusted_report
    semiconductor = service.read_report("semiconductor", 90).displayed_trusted_report
    robotics = service.read_report("robotics", 90).displayed_trusted_report
    assert storage is not None and semiconductor is not None and robotics is not None
    assert [row.label for row in storage.cycle] == [
        "DRAM 价格", "NAND 价格", "HBM 需求", "库存水平", "产能利用率",
        "厂商资本开支", "服务器需求", "消费电子需求",
    ]
    assert [row.label for row in semiconductor.cycle] == [
        "设备订单与出货", "晶圆厂利用率", "晶圆代工收入", "芯片设计活跃度",
        "封装测试需求", "终端需求",
    ]
    assert [row.label for row in robotics.cycle] == ["样机进展", "订单", "交付", "量产进度"]
    assert semiconductor.trusted_snapshot_id.startswith("DEMO-H-")
    assert robotics.trusted_snapshot_id.startswith("DEMO-R-")
    assert all("DRAM" not in row.label and "NAND" not in row.label for row in semiconductor.cycle + robotics.cycle)


@pytest.mark.skipif(os.name != "nt", reason="Windows command wrapper contract")
def test_windows_spawn_wraps_npm_and_absolute_cmd_without_using_shell_true() -> None:
    runner = _load_runner()
    environment = runner.clean_child_environment()

    npm = runner.spawn_command(["npm", "run", "build"], environment)
    playwright = runner.spawn_command(
        [str(runner.PLAYWRIGHT_TOOLS / "node_modules" / ".bin" / "playwright.cmd"), "install", "chromium"],
        environment,
    )

    assert Path(npm[0]).name.casefold() == "node.exe"
    assert Path(npm[1]).as_posix().casefold().endswith("/node_modules/npm/bin/npm-cli.js")
    assert npm[2:] == ["run", "build"]
    assert Path(playwright[0]).name.casefold() == "node.exe"
    assert Path(playwright[1]) == runner.PLAYWRIGHT_TOOLS / "node_modules" / "playwright" / "cli.js"
    assert playwright[2:] == ["install", "chromium"]


@pytest.mark.skipif(os.name != "nt", reason="Windows command wrapper contract")
def test_windows_spawn_command_really_executes_npm_cmd() -> None:
    runner = _load_runner()
    environment = runner.clean_child_environment()

    completed = subprocess.run(
        runner.spawn_command(["npm", "--version"], environment),
        cwd=REPO_ROOT / "frontend",
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip().replace(".", "").isdigit()


def test_console_output_falls_back_safely_when_host_encoding_is_gbk(monkeypatch) -> None:
    runner = _load_runner()

    class StrictGbkConsole:
        encoding = "gbk"

        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def write(self, value: str) -> int:
            encoded = value.encode(self.encoding)
            self.buffer.write(encoded)
            return len(value)

        def flush(self) -> None:
            return None

    console = StrictGbkConsole()
    monkeypatch.setattr(runner.sys, "stdout", console)

    runner.write_console_output("37 files ✔\n")

    assert console.buffer.getvalue().decode("gbk") == "37 files \\u2714\n"


def test_listener_pid_must_equal_or_descend_from_the_owned_process_root() -> None:
    runner = _load_runner()
    netstat = """
      TCP    127.0.0.1:60785        0.0.0.0:0              LISTENING       40988
      TCP    127.0.0.1:60786        0.0.0.0:0              LISTENING       50000
    """

    assert runner.listening_pid_for_port(netstat, 60785) == 40988
    assert runner.pid_is_owned_by(43904, 43904, {}) is True
    assert runner.pid_is_owned_by(43904, 40988, {40988: 43904}) is True
    assert runner.pid_is_owned_by(43904, 40988, {40988: 40000, 40000: 43904}) is True
    assert runner.pid_is_owned_by(43904, 40988, {40988: 1}) is False
    assert runner.pid_is_owned_by(43904, 40988, {40988: 40000, 40000: 40988}) is False
    with pytest.raises(RuntimeError, match="no unique loopback listener"):
        runner.listening_pid_for_port(netstat, 60787)


def test_browser_failure_diagnostics_are_written_without_route_mocks() -> None:
    script = (REPO_ROOT / "scripts" / "acceptance" / "v02_w3_industry_browser.mjs").read_text(
        encoding="utf-8"
    )

    assert "browser-failure.json" in script
    assert 'page.on("response"' in script
    assert "pageText" in script
    assert "consoleMessages" in script
    assert "failedRequests" in script
    assert ".route(" not in script


def test_fixture_overview_derives_evidence_and_expiry_conditions_from_basis_metrics() -> None:
    runner = _load_runner()
    report = runner.build_fixture_service().read_report("storage", 90).displayed_trusted_report
    assert report is not None
    trusted_by_id = {
        metric.metric_id: metric
        for metric in (*report.cycle, *report.metrics, *report.capital)
        if metric.current_value is not None
    }
    basis = [trusted_by_id[metric_id] for metric_id in report.overview.basis_metric_ids]
    expected_evidence = tuple(dict.fromkeys(
        item.evidence_id for metric in basis for item in metric.evidence
    ))
    expected_conditions = tuple(dict.fromkeys(
        condition for metric in basis for condition in metric.invalidating_conditions
    ))

    assert report.overview.evidence_ids == expected_evidence
    assert report.overview.invalidating_conditions == expected_conditions


def test_browser_report_locators_are_scoped_to_the_report_top_level() -> None:
    script = (REPO_ROOT / "scripts" / "acceptance" / "v02_w3_industry_browser.mjs").read_text(
        encoding="utf-8"
    )

    for industry_id in ("storage", "semiconductor", "robotics"):
        assert (
            f'[data-industry-report-top] > article[data-industry-id="{industry_id}"]'
            in script
        )
        assert f"page.locator('article[data-industry-id=\"{industry_id}\"]')" not in script


def test_browser_evidence_drawer_contract_matches_production_labels() -> None:
    script = (REPO_ROOT / "scripts" / "acceptance" / "v02_w3_industry_browser.mjs").read_text(
        encoding="utf-8"
    )

    for label in (
        "原始快照",
        "证据快照",
        "数据日期",
        "数据口径",
        "判断依据",
        "失效条件",
        "来源族",
    ):
        assert f'"{label}"' in script
    assert '"数据来源"' not in script


def test_browser_only_classifies_exact_in_window_industry_aborts_as_expected() -> None:
    script = (REPO_ROOT / "scripts" / "acceptance" / "v02_w3_industry_browser.mjs").read_text(
        encoding="utf-8"
    )

    assert "expectedCancellationWindow" in script
    assert 'entry.method !== "GET"' in script
    assert 'entry.failure !== "net::ERR_ABORTED"' in script
    assert "parsed.origin === baseUrl" in script
    for industry_id in ("storage", "semiconductor", "robotics"):
        assert f'"/api/industry-research/{industry_id}"' in script
    assert 'parsed.searchParams.get("window_days")' in script
    assert "blockingFailedRequests.length === 0" in script
    assert "expectedCancelledRequests" in script

    history_stage = script[
        script.index("const windowCounts") : script.index("const metricsAnchor")
    ]
    assert "page.waitForResponse" in history_stage
    assert 'historyUrl.origin === baseUrl' in history_stage
    assert 'historyUrl.pathname === "/api/industry-research/storage"' in history_stage
    assert 'historyUrl.searchParams.get("window_days") === String(days)' in history_stage
    assert "await historyResponse;" in history_stage
