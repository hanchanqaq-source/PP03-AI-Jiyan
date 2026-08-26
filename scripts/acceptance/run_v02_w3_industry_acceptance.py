from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler


REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_ROOT = (REPO_ROOT / ".tmp" / "acceptance" / "v0.2-w3").resolve()

# The first filesystem action in the outer runner is creation of the absolute,
# task-owned acceptance root. Only standard-library imports occur above it.
ACCEPTANCE_ROOT.mkdir(parents=True, exist_ok=True)

_ISOLATED_PATHS = {
    "TEMP": ACCEPTANCE_ROOT / "temp",
    "TMP": ACCEPTANCE_ROOT / "tmp",
    "VR_DATA_DIR": ACCEPTANCE_ROOT / "data",
    "VR_REPORTS_DIR": ACCEPTANCE_ROOT / "reports",
    "VR_NEWS_CACHE_DIR": ACCEPTANCE_ROOT / "news",
    "VR_EVIDENCE_DIR": ACCEPTANCE_ROOT / "evidence",
    "VR_ACCEPTANCE_DIR": ACCEPTANCE_ROOT / "acceptance",
    "VR_LOG_DIR": ACCEPTANCE_ROOT / "logs",
}
ISOLATED_ENVIRONMENT_VARIABLES = tuple(_ISOLATED_PATHS)
for _name, _path in _ISOLATED_PATHS.items():
    _path.mkdir(parents=True, exist_ok=True)
    os.environ[_name] = str(_path.resolve())

_CREDENTIAL_NAME = re.compile(
    r"(?:API[_-]?KEY|ACCESS[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH(?:ORIZATION)?)",
    re.IGNORECASE,
)
for _name in tuple(os.environ):
    if _CREDENTIAL_NAME.search(_name):
        os.environ.pop(_name, None)

os.environ["VR_ALLOW_PAID_PROVIDER_TESTS"] = "0"
os.environ["VR_SOURCE_HEALTH_STARTUP"] = "0"
os.environ["VR_OFFLINE"] = "1"

SOURCE_FIXTURE = (
    REPO_ROOT / "scripts" / "acceptance" / "fixtures" / "v02_w3_industry_snapshots.json"
).resolve()
RUNTIME_FIXTURE = (ACCEPTANCE_ROOT / "run" / "v02_w3_industry_snapshots.json").resolve()
BROWSER_SCRIPT = (REPO_ROOT / "scripts" / "acceptance" / "v02_w3_industry_browser.mjs").resolve()
PLAYWRIGHT_TOOLS = (ACCEPTANCE_ROOT / "tools").resolve()
PLAYWRIGHT_BROWSERS = (ACCEPTANCE_ROOT / "playwright-browsers").resolve()


class AcceptanceBoundaryError(RuntimeError):
    pass


@dataclass(slots=True)
class ReservedLoopbackPort:
    host: str
    port: int
    socket: socket.socket

    def close(self) -> None:
        self.socket.close()


def reserve_loopback_ports() -> tuple[ReservedLoopbackPort, ReservedLoopbackPort]:
    reservations: list[ReservedLoopbackPort] = []
    try:
        for _ in range(2):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            host, port = listener.getsockname()
            reservations.append(ReservedLoopbackPort(str(host), int(port), listener))
        if reservations[0].port == reservations[1].port:
            raise AcceptanceBoundaryError("OS returned duplicate dynamic ports")
        return reservations[0], reservations[1]
    except BaseException:
        for reservation in reservations:
            reservation.close()
        raise


def playwright_install_commands() -> tuple[list[str], list[str]]:
    return (
        [
            "npm", "install", "--prefix", str(PLAYWRIGHT_TOOLS),
            "--no-save", "playwright@1.62.1",
        ],
        [
            str(PLAYWRIGHT_TOOLS / "node_modules" / ".bin" / "playwright.cmd"),
            "install", "chromium",
        ],
    )


def backend_test_command() -> list[str]:
    return [
        sys.executable, "-m", "pytest", "backend/tests", "-m", "not live",
        "-q", "-p", "no:cacheprovider",
    ]


def frontend_test_commands() -> list[list[str]]:
    return [
        ["npm", "run", "test:run"],
        ["npm", "run", "test:legacy"],
        ["npm", "run", "build"],
    ]


def cleanup_targets() -> tuple[Path, ...]:
    return validate_cleanup_targets([
        ACCEPTANCE_ROOT / name
        for name in (
            "acceptance", "cache", "data", "evidence", "logs", "news",
            "playwright-browsers", "profile", "reports", "results", "run",
            "temp", "tmp", "tools",
        )
    ])


def _inside_acceptance_root(path: Path) -> bool:
    return path.resolve().is_relative_to(ACCEPTANCE_ROOT)


def clean_child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in tuple(environment):
        if _CREDENTIAL_NAME.search(name):
            environment.pop(name, None)
    environment["VR_ALLOW_PAID_PROVIDER_TESTS"] = "0"
    environment["VR_SOURCE_HEALTH_STARTUP"] = "0"
    environment["VR_OFFLINE"] = "1"
    return environment


def validate_child_environment(environment: dict[str, str]) -> None:
    for name, value in environment.items():
        if _CREDENTIAL_NAME.search(name) and value:
            raise AcceptanceBoundaryError("credential variable must be empty before child launch")
    if environment.get("VR_ALLOW_PAID_PROVIDER_TESTS") != "0":
        raise AcceptanceBoundaryError("paid provider tests must remain disabled")
    if environment.get("VR_OFFLINE") != "1":
        raise AcceptanceBoundaryError("offline mode must remain enabled")
    for name in ISOLATED_ENVIRONMENT_VARIABLES:
        value = environment.get(name)
        if not value or not _inside_acceptance_root(Path(value)):
            raise AcceptanceBoundaryError(f"isolated child path escaped acceptance root: {name}")


def copy_source_fixture(destination: Path = RUNTIME_FIXTURE) -> Path:
    destination = destination.resolve()
    if not _inside_acceptance_root(destination):
        raise AcceptanceBoundaryError("fixture runtime copy must stay under acceptance root")
    if destination == SOURCE_FIXTURE:
        raise AcceptanceBoundaryError("source fixture cannot be overwritten")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE_FIXTURE, destination)
    return destination


def validate_cleanup_targets(paths: list[Path]) -> tuple[Path, ...]:
    validated: list[Path] = []
    for candidate in paths:
        resolved = candidate.resolve()
        if resolved == SOURCE_FIXTURE:
            raise AcceptanceBoundaryError("source fixture is never a cleanup target")
        if resolved == ACCEPTANCE_ROOT or not _inside_acceptance_root(resolved):
            raise AcceptanceBoundaryError("cleanup target escaped owned acceptance descendants")
        validated.append(resolved)
    return tuple(validated)


def validate_network_log(
    entries: list[dict[str, str]],
    *,
    allowed_origins: set[str],
) -> None:
    normalized_origins = {origin.rstrip("/") for origin in allowed_origins}
    for entry in entries:
        url = entry.get("url", "")
        method = entry.get("method", "GET").upper()
        parsed = urlsplit(url)
        if parsed.scheme in {"data", "blob", "about"}:
            continue
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in normalized_origins:
            raise AcceptanceBoundaryError("network request left approved loopback origins")
        if method not in {"GET", "HEAD", "OPTIONS"}:
            raise AcceptanceBoundaryError("network request attempted a write or Live refresh")
        if parsed.hostname != "127.0.0.1":
            raise AcceptanceBoundaryError("network request was not loopback-only")
        if parsed.path.endswith("/refresh"):
            raise AcceptanceBoundaryError("network request attempted production refresh")


def _load_fixture_document() -> dict[str, object]:
    runtime = copy_source_fixture()
    document = json.loads(runtime.read_text(encoding="utf-8"))
    if type(document) is not dict or document.get("demo") is not True:
        raise AcceptanceBoundaryError("fixture must be an explicit demo document")
    return document


def _metric_evidence(metric_id: str, industry_id: str, *, corroborated: bool):
    from industry_research.models import EvidenceReference

    suffixes = ("A", "B") if corroborated else ("OFFICIAL",)
    return tuple(
        EvidenceReference(
            evidence_id=f"DEMO-{industry_id.upper()}-{metric_id}-{suffix}",
            source_family_id=f"demo-family-{suffix.casefold()}",
            content_source=f"demo-source-{suffix.casefold()}.invalid",
            origin_cluster=f"demo-origin-{suffix.casefold()}",
            collector_source=f"demo-collector-{suffix.casefold()}",
            final_url=f"https://demo-source-{suffix.casefold()}.invalid/{metric_id}",
            is_official=not corroborated,
            is_official_attested=not corroborated,
            supports_claim=True,
            supports_fields=(metric_id,),
            contradicts_claim=False,
            as_of_date="2026-08-25",
            verified_at="2026-08-26T00:00:00+00:00",
        )
        for suffix in suffixes
    )


def _trusted_metric(industry_id: str, metric_id: str, config: dict[str, object], raw_id: str, evidence_id: str):
    from industry_research.models import (
        AvailabilityStatus,
        FreshnessStatus,
        HistoricalPosition,
        IndustryMetricObservation,
        MetricChange,
        SourceRunStatus,
        VerificationStatus,
    )

    corroborated = config["verification_status"] == "corroborated"
    evidence = _metric_evidence(metric_id, industry_id, corroborated=corroborated)
    source_families = tuple(item.source_family_id for item in evidence) if corroborated else ()
    content_sources = tuple(item.content_source for item in evidence) if corroborated else ()
    origin_clusters = tuple(item.origin_cluster for item in evidence) if corroborated else ()
    return IndustryMetricObservation(
        industry_id=industry_id,
        metric_id=metric_id,
        label=str(config["label"]),
        current_value=config["current_value"],
        unit=config.get("unit"),
        change=MetricChange(float(config["change_value"]), "mom"),
        historical_position=HistoricalPosition(72.0, "36个月", "隔离演示分位"),
        availability_status=AvailabilityStatus.AVAILABLE,
        verification_status=(
            VerificationStatus.CORROBORATED if corroborated else VerificationStatus.VERIFIED
        ),
        freshness_status=FreshnessStatus.FRESH,
        source_run_status=SourceRunStatus.HEALTHY,
        empty_reason=None,
        as_of_date="2026-08-25",
        fetched_at="2026-08-26T00:00:00+00:00",
        methodology="隔离 fixture 的同口径演示快照；不代表真实市场数据",
        judgment_basis=(f"{metric_id} 仅用于验收真值展示",),
        invalidating_conditions=("隔离 fixture 被替换或来源口径变化",),
        evidence=evidence,
        independent_source_families=source_families,
        independent_content_sources=content_sources,
        independent_origin_clusters=origin_clusters,
        raw_snapshot_id=raw_id,
        evidence_snapshot_id=evidence_id,
        expires_at=None,
    )


_METRIC_LABELS = {
    "dram_price": "DRAM 价格",
    "nand_price": "NAND 价格",
    "hbm_demand": "HBM 需求",
    "inventory_level": "库存水平",
    "capacity_utilization": "产能利用率",
    "manufacturer_capex": "厂商资本开支",
    "server_demand": "服务器需求",
    "consumer_electronics_demand": "消费电子需求",
    "equipment_book_to_bill": "设备订单与出货",
    "wafer_fab_utilization": "晶圆厂利用率",
    "foundry_revenue": "晶圆代工收入",
    "design_activity": "芯片设计活跃度",
    "packaging_demand": "封装测试需求",
    "end_market_demand": "终端需求",
    "prototype_progress": "样机进展",
    "orders": "订单",
    "delivery": "交付",
    "mass_production": "量产进度",
    "sector_fund_flow": "板块资金",
    "etf_share": "ETF 份额",
    "industry_valuation": "估值水平",
    "historical_valuation_percentile": "历史分位",
}

_CHAIN_LABELS = {
    "equipment_materials": "设备与材料",
    "memory_design_manufacturing": "存储设计与制造",
    "packaging_testing": "封装测试",
    "modules_controllers": "模组与控制器",
    "end_applications": "服务器 / 手机 / PC / 汽车终端",
    "design": "芯片设计",
    "wafer_manufacturing": "晶圆制造",
    "end_market": "终端需求",
    "core_components": "核心零部件",
    "complete_machine": "整机",
    "software_vision": "软件与机器视觉",
    "applications": "应用",
}


def _empty_metric(industry_id: str, metric_id: str, *, reason: str = "source_unconfigured"):
    from industry_research.models import (
        AvailabilityStatus,
        EmptyReason,
        FreshnessStatus,
        IndustryMetricObservation,
        SourceRunStatus,
        VerificationStatus,
    )

    empty_reason = EmptyReason(reason)
    source_status = (
        SourceRunStatus.FAILED if empty_reason is EmptyReason.SOURCE_FAILED
        else SourceRunStatus.NOT_CONFIGURED
    )
    availability = (
        AvailabilityStatus.UNCONFIGURED if empty_reason is EmptyReason.SOURCE_UNCONFIGURED
        else AvailabilityStatus.UNAVAILABLE
    )
    return IndustryMetricObservation(
        industry_id=industry_id,
        metric_id=metric_id,
        label=_METRIC_LABELS[metric_id],
        current_value=None,
        unit=None,
        change=None,
        historical_position=None,
        availability_status=availability,
        verification_status=VerificationStatus.NOT_EVALUATED,
        freshness_status=FreshnessStatus.UNKNOWN,
        source_run_status=source_status,
        empty_reason=empty_reason,
        as_of_date=None,
        fetched_at=None,
        methodology="尚无可准入来源；隔离演示保持准确空态",
        judgment_basis=(),
        invalidating_conditions=(),
        evidence=(),
        independent_source_families=(),
        independent_content_sources=(),
        independent_origin_clusters=(),
        raw_snapshot_id=None,
        evidence_snapshot_id=None,
        expires_at=None,
    )


def _build_report(industry_id: str, config: dict[str, object], document: dict[str, object]):
    from industry_research.models import (
        ConclusionStatus,
        DataCompleteness,
        DisplayedTrustedReport,
        IndustryChainNode,
        IndustryCompanyRelation,
        IndustryConclusion,
        IndustryEvidenceEvent,
        ReportCounts,
        SourceCoverage,
        VerificationStatus,
        render_conclusion_text,
    )
    from industry_research.templates import get_industry_template

    template = get_industry_template(industry_id)
    raw_id = str(config["raw_snapshot_id"])
    evidence_id = str(config["evidence_snapshot_id"])
    trusted_config = config["trusted_metrics"]
    assert isinstance(trusted_config, dict)
    trusted = {
        metric_id: _trusted_metric(industry_id, metric_id, row, raw_id, evidence_id)
        for metric_id, row in trusted_config.items()
        if isinstance(row, dict)
    }
    cycle = tuple(
        trusted.get(metric_id) or _empty_metric(industry_id, metric_id)
        for metric_id in template.cycle_metric_ids
    )
    metrics = tuple(
        trusted.get(metric_id) or _empty_metric(industry_id, metric_id)
        for metric_id in template.core_metric_ids
    )
    capital_reasons = (
        "source_failed", "license_required", "user_key_not_configured", "insufficient_history",
    )
    capital = tuple(
        _empty_metric(industry_id, metric_id, reason=capital_reasons[index])
        for index, metric_id in enumerate(template.capital_metric_ids)
    )
    basis = tuple(metric_id for metric_id in ("dram_price", "nand_price") if metric_id in trusted)
    evidence_ids = tuple(
        item.evidence_id for metric_id in basis for item in trusted[metric_id].evidence
    )
    invalidating_conditions = tuple(dict.fromkeys(
        condition
        for metric_id in basis
        for condition in trusted[metric_id].invalidating_conditions
    ))
    completeness = DataCompleteness(len(basis), 2, len(basis) / 2)
    conclusion_values = dict(
        conclusion_id=f"DEMO-{industry_id.upper()}-CONCLUSION-001",
        industry_id=industry_id,
        rule_version=f"demo-{industry_id}-cycle-v1",
        status=ConclusionStatus.VERIFIED if len(basis) == 2 else ConclusionStatus.UNAVAILABLE,
        cycle_stage="recovery" if len(basis) == 2 else None,
        outlook_direction="improving" if len(basis) == 2 else None,
        confidence_level="medium" if len(basis) == 2 else None,
        data_completeness=completeness,
        basis_metric_ids=basis,
        evidence_ids=evidence_ids,
        invalidating_conditions=invalidating_conditions,
    )
    overview = IndustryConclusion(
        **conclusion_values,
        text=render_conclusion_text(**conclusion_values),
    )
    chain = tuple(
        IndustryChainNode(
            industry_id=industry_id,
            node_id=node_id,
            label=_CHAIN_LABELS[node_id],
            observation_ids=(),
            evidence_ids=(),
            status=ConclusionStatus.UNAVAILABLE,
        )
        for node_id in template.chain_node_ids
    )
    companies = ()
    company = config.get("company")
    if isinstance(company, dict):
        companies = (
            IndustryCompanyRelation(
                industry_id=industry_id,
                security_code=str(company["security_code"]),
                company_name=str(company["company_name"]),
                chain_node_id=str(company["chain_node_id"]),
                relation_type=str(company["relation_type"]),
                key_metric_ids=("dram_price",),
                evidence_ids=(str(company["evidence_id"]),),
                as_of_date="2026-08-25",
                observation_only=True,
            ),
        )
    prefix = {"storage": "S", "semiconductor": "H", "robotics": "R"}[industry_id]
    news_rows = document["trusted_news"]
    assert isinstance(news_rows, list)
    news = tuple(
        IndustryEvidenceEvent(
            industry_id=industry_id,
            event_id=f"DEMO-{prefix}-NEWS-{row['suffix']}",
            status=VerificationStatus(str(row["status"])),
            occurred_at=str(row["occurred_at"]),
            evidence_ids=(f"DEMO-{prefix}-NEWS-EVIDENCE-{row['suffix']}",),
            roles=tuple(row["roles"]),
        )
        for row in news_rows if isinstance(row, dict)
    )
    coverage = config["source_coverage"]
    assert isinstance(coverage, dict)
    counts = ReportCounts(
        sum(row.verification_status is VerificationStatus.VERIFIED for row in trusted.values()),
        sum(row.verification_status is VerificationStatus.CORROBORATED for row in trusted.values()),
    )
    snapshot_id = str(config["trusted_snapshot_id"])
    return DisplayedTrustedReport(
        industry_id=industry_id,
        template_status=template.status,
        trusted_snapshot_id=snapshot_id,
        displayed_trusted_snapshot_id=snapshot_id,
        raw_snapshot_id=raw_id,
        evidence_snapshot_id=evidence_id,
        generated_at=str(document["generated_at"]),
        demo=True,
        source_coverage=SourceCoverage(unit="capability", **coverage),
        counts=counts,
        overview=overview,
        cycle=cycle,
        chain=chain,
        metrics=metrics,
        capital=capital,
        companies=companies,
        fund_selection=(),
        funds=(),
        news_risk=news,
    )


def _build_storage_candidate(config: dict[str, object], document: dict[str, object]):
    from industry_research.models import (
        AvailabilityStatus,
        CandidateEvidenceCounts,
        CandidateEvidencePanel,
        CandidateIndustryEvidenceEvent,
        ConflictingObservation,
        ConflictingSourceValue,
        EvidenceReference,
        FreshnessStatus,
        IndustryMetricObservation,
        SourceRunStatus,
        VerificationStatus,
    )

    raw_id = str(config["candidate_raw_snapshot_id"])
    evidence_id = str(config["candidate_evidence_snapshot_id"])
    candidate_id = str(config["candidate_snapshot_id"])
    metric_config = document["candidate_metric"]
    assert isinstance(metric_config, dict)
    metric_id = str(metric_config["metric_id"])
    candidate_metric = IndustryMetricObservation(
        industry_id="storage", metric_id=metric_id, label=str(metric_config["label"]),
        current_value=metric_config["current_value"], unit=None, change=None,
        historical_position=None, availability_status=AvailabilityStatus.PARTIAL,
        verification_status=VerificationStatus.UNVERIFIED,
        freshness_status=FreshnessStatus.FRESH,
        source_run_status=SourceRunStatus.PARTIAL_FAILURE,
        empty_reason=None, as_of_date="2026-08-25",
        fetched_at="2026-08-26T00:00:00+00:00",
        methodology="隔离候选，仅用于验证待核验分层",
        judgment_basis=("候选证据尚未准入",),
        invalidating_conditions=("候选被证伪",),
        evidence=(EvidenceReference(
            evidence_id="DEMO-S-PENDING-EVIDENCE-001", source_family_id="demo-pending-family",
            content_source="demo-pending.invalid", origin_cluster="demo-pending-origin",
            collector_source="demo-pending-collector", final_url="https://demo-pending.invalid/signal",
            is_official=False, is_official_attested=False, supports_claim=True,
            supports_fields=(metric_id,), contradicts_claim=False,
            as_of_date="2026-08-25", verified_at="2026-08-26T00:00:00+00:00",
        ),),
        independent_source_families=(), independent_content_sources=(),
        independent_origin_clusters=(), raw_snapshot_id=raw_id,
        evidence_snapshot_id=evidence_id, expires_at=None,
    )
    conflict_config = document["conflicting_metric"]
    assert isinstance(conflict_config, dict)
    source_values_config = conflict_config["source_values"]
    assert isinstance(source_values_config, list)
    conflict = ConflictingObservation(
        industry_id="storage", metric_id=str(conflict_config["metric_id"]), aggregate_value=None,
        source_values=tuple(
            ConflictingSourceValue(
                evidence_id=str(row["evidence_id"]), source_family_id=str(row["source_family_id"]),
                value=str(row["value"]), unit=None, as_of_date=str(row["as_of_date"]),
            )
            for row in source_values_config if isinstance(row, dict)
        ),
        raw_snapshot_id=raw_id, evidence_snapshot_id=evidence_id,
    )
    candidate_news = document["candidate_news"]
    assert isinstance(candidate_news, dict)
    def events(kind: str, status: VerificationStatus):
        rows = candidate_news[kind]
        assert isinstance(rows, list)
        return tuple(
            CandidateIndustryEvidenceEvent(
                industry_id="storage", event_id=f"DEMO-S-{row['suffix']}", status=status,
                occurred_at=str(row["occurred_at"]),
                evidence_ids=(f"DEMO-S-{row['suffix']}-SUPPORT",) + (
                    (f"DEMO-S-{row['suffix']}-CONTRADICT",)
                    if status is VerificationStatus.CONFLICTING else ()
                ),
                supporting_evidence_ids=(f"DEMO-S-{row['suffix']}-SUPPORT",),
                contradicting_evidence_ids=(
                    (f"DEMO-S-{row['suffix']}-CONTRADICT",)
                    if status is VerificationStatus.CONFLICTING else ()
                ),
                roles=tuple(row["roles"]), candidate_snapshot_id=candidate_id,
                raw_snapshot_id=raw_id, evidence_snapshot_id=evidence_id,
            )
            for row in rows if isinstance(row, dict)
        )
    pending_events = events("unverified", VerificationStatus.UNVERIFIED)
    conflict_events = events("conflicting", VerificationStatus.CONFLICTING)
    return CandidateEvidencePanel(
        industry_id="storage", candidate_snapshot_id=candidate_id,
        counts=CandidateEvidenceCounts(1, 1, len(pending_events), len(conflict_events)),
        unverified=(candidate_metric,), conflicting=(conflict,),
        unverified_events=pending_events, conflicting_events=conflict_events,
        raw_snapshot_id=raw_id, evidence_snapshot_id=evidence_id,
    )


class AcceptanceFixtureService:
    def __init__(self, storage, candidates, configs):
        self.storage = storage
        self._candidates = candidates
        self._configs = configs
        self._now = datetime(2026, 8, 26, tzinfo=timezone.utc)

    def read_report(self, industry_id: str, window_days: int):
        from industry_research.models import CandidateEvidenceCounts, IndustryReportResponse, RefreshPhase, RefreshRun

        report = self.storage.load_current(industry_id)
        config = self._configs.get(industry_id)
        if report is None or config is None:
            return IndustryReportResponse(
                industry_id, None, None, None,
                RefreshRun(industry_id, None, None, None, None, RefreshPhase.IDLE, None, None, None),
            )
        cutoff = self._now - timedelta(days=window_days)
        report = replace(
            report,
            news_risk=tuple(
                event for event in report.news_risk
                if cutoff <= datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00")) <= self._now
            ),
        )
        candidate = self._candidates.get(industry_id)
        if candidate is not None:
            unverified_events = tuple(
                event for event in candidate.unverified_events
                if cutoff <= datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00")) <= self._now
            )
            conflicting_events = tuple(
                event for event in candidate.conflicting_events
                if cutoff <= datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00")) <= self._now
            )
            candidate = replace(
                candidate,
                counts=CandidateEvidenceCounts(
                    len(candidate.unverified), len(candidate.conflicting),
                    len(unverified_events), len(conflicting_events),
                ),
                unverified_events=unverified_events,
                conflicting_events=conflicting_events,
            )
        phase = RefreshPhase(str(config["refresh_phase"]))
        refresh = RefreshRun(
            industry_id=industry_id,
            run_id=f"DEMO-{industry_id.upper()}-RUN-002" if candidate is not None else None,
            raw_snapshot_id=candidate.raw_snapshot_id if candidate is not None else None,
            evidence_snapshot_id=candidate.evidence_snapshot_id if candidate is not None else None,
            candidate_snapshot_id=candidate.candidate_snapshot_id if candidate is not None else None,
            phase=phase,
            error_code=config.get("refresh_error_code"),
            displayed_trusted_snapshot_id=report.trusted_snapshot_id,
            published_trusted_snapshot_id=None,
            displayed_raw_snapshot_id=report.raw_snapshot_id,
            displayed_evidence_snapshot_id=report.evidence_snapshot_id,
        )
        return IndustryReportResponse(industry_id, industry_id, report, candidate, refresh)

    def has_qualified_refresh(self, industry_id: str) -> bool:
        del industry_id
        return False

    def request_refresh(self, industry_id: str):
        del industry_id
        raise RuntimeError("source_unconfigured")

    def resolve_fund_relations(self, industry_id: str, fund_codes: tuple[str, ...]):
        from industry_research.models import FundResolutionEmptyReason, FundSelectionScope, IndustryFundRelationResolution
        from industry_research.relationships import FundRelationProjection

        del industry_id
        if not fund_codes:
            return FundRelationProjection("no_holdings", (), (), ())
        selections = tuple(FundSelectionScope(f"demo-selection-{index}", code, True) for index, code in enumerate(fund_codes, 1))
        resolutions = tuple(
            IndustryFundRelationResolution(item.selection_id, item.fund_code, None, FundResolutionEmptyReason.SOURCE_UNAVAILABLE)
            for item in selections
        )
        return FundRelationProjection("resolved", selections, resolutions, ())

    def shutdown(self) -> None:
        return None


def build_fixture_service() -> AcceptanceFixtureService:
    backend = str(REPO_ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from industry_research.storage import IndustryResearchStorage

    document = _load_fixture_document()
    reports_config = document["reports"]
    assert isinstance(reports_config, dict)
    storage = IndustryResearchStorage(ACCEPTANCE_ROOT / "run" / "industry-storage", production=False)
    for industry_id, config in reports_config.items():
        if not isinstance(config, dict):
            raise AcceptanceBoundaryError("invalid report fixture")
        report = _build_report(industry_id, config, document)
        result = storage.publish_document(
            report.to_dict(),
            expected_industry_id=industry_id,
            expected_raw_snapshot_id=report.raw_snapshot_id,
            expected_evidence_snapshot_id=report.evidence_snapshot_id,
        )
        if result.error_code is not None or result.published_trusted_snapshot_id != report.trusted_snapshot_id:
            raise AcceptanceBoundaryError("fixture publication failed")
    storage_config = reports_config["storage"]
    assert isinstance(storage_config, dict)
    return AcceptanceFixtureService(
        storage,
        {"storage": _build_storage_candidate(storage_config, document)},
        reports_config,
    )


@dataclass(frozen=True, slots=True)
class CommandEvidence:
    label: str
    command: tuple[str, ...]
    cwd: str
    exit_code: int
    duration_seconds: float
    summary: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, encoding="utf-8",
    ).strip()


def _summary(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    interesting = [
        line for line in lines
        if any(token in line for token in ("passed", "failed", "skipped", "deselected", "Tests", "# pass", "built in"))
    ]
    return " | ".join((interesting or lines)[-4:])[-1500:]


def write_console_output(output: str) -> None:
    if not output:
        return
    console = sys.stdout
    try:
        console.write(output)
    except UnicodeEncodeError:
        encoding = getattr(console, "encoding", None) or "utf-8"
        safe_bytes = output.encode(encoding, errors="backslashreplace")
        buffer = getattr(console, "buffer", None)
        if buffer is not None:
            buffer.write(safe_bytes)
        else:
            console.write(safe_bytes.decode(encoding))
    console.flush()


def spawn_command(command: list[str], environment: dict[str, str]) -> list[str]:
    if not command:
        raise ValueError("child command must not be empty")
    executable = command[0]
    candidate = Path(executable)
    if candidate.is_absolute():
        resolved = str(candidate)
    else:
        resolved = shutil.which(executable, path=environment.get("PATH")) or executable
    resolved_command = [resolved, *command[1:]]
    if os.name == "nt" and Path(resolved).suffix.casefold() in {".cmd", ".bat"}:
        node = shutil.which("node.exe", path=environment.get("PATH"))
        if not node:
            raise FileNotFoundError("node.exe is required to launch approved Node command shims")
        shim = Path(resolved)
        if shim.name.casefold() == "npm.cmd":
            cli = shim.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        elif shim.name.casefold() == "playwright.cmd":
            cli = shim.parent.parent / "playwright" / "cli.js"
        else:
            raise ValueError(f"unsupported Windows command shim: {shim.name}")
        return [node, str(cli), *command[1:]]
    return resolved_command


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    label: str,
    timeout: int = 900,
) -> tuple[CommandEvidence, str]:
    validate_child_environment(environment)
    started = time.monotonic()
    completed = subprocess.run(
        spawn_command(command, environment),
        cwd=cwd,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    duration = time.monotonic() - started
    output = completed.stdout or ""
    write_console_output(output)
    if output and not output.endswith("\n"):
        write_console_output("\n")
    evidence = CommandEvidence(
        label=label,
        command=tuple(command),
        cwd=str(cwd),
        exit_code=completed.returncode,
        duration_seconds=round(duration, 3),
        summary=_summary(output),
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed with exit {completed.returncode}")
    return evidence, output


def _remove_disposable_targets() -> dict[str, object]:
    removed: list[str] = []
    absent: list[str] = []
    for target in cleanup_targets():
        if not target.exists() and not target.is_symlink():
            absent.append(str(target))
            continue
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        removed.append(str(target))
    remaining = [str(target) for target in cleanup_targets() if target.exists() or target.is_symlink()]
    if remaining:
        raise AcceptanceBoundaryError(f"cleanup incomplete: {remaining}")
    return {"removed": removed, "already_absent": absent, "remaining": remaining}


def _wait_for_http(url: str, process: subprocess.Popen[str], timeout: float = 30.0) -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last_error = "not attempted"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"owned service exited early with {process.returncode}: {url}")
        try:
            with opener.open(url, timeout=1.0) as response:
                if 200 <= response.status < 500:
                    return
        except Exception as error:
            last_error = type(error).__name__
        time.sleep(0.2)
    raise RuntimeError(f"owned service did not become ready: {url} ({last_error})")


def listening_pid_for_port(netstat_output: str, port: int) -> int:
    pattern = re.compile(
        rf"^\s*TCP\s+127\.0\.0\.1:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    matches = {int(match.group(1)) for match in pattern.finditer(netstat_output)}
    if len(matches) != 1:
        raise RuntimeError(f"no unique loopback listener for 127.0.0.1:{port}: {sorted(matches)}")
    return matches.pop()


def pid_is_owned_by(root_pid: int, candidate_pid: int, parent_by_pid: dict[int, int]) -> bool:
    current = candidate_pid
    visited: set[int] = set()
    while current > 0 and current not in visited:
        if current == root_pid:
            return True
        visited.add(current)
        current = parent_by_pid.get(current, 0)
    return False


def _windows_parent_pid_map() -> dict[int, int]:
    if os.name != "nt":
        raise RuntimeError("Windows process ownership snapshot requested on a non-Windows host")
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents: dict[int, int] = {}
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not process_first(snapshot, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        while True:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not process_next(snapshot, ctypes.byref(entry)):
                break
    finally:
        close_handle(snapshot)
    return parents


def _assert_pid_owns_port(pid: int, port: int) -> int:
    if os.name != "nt":
        raise RuntimeError("Task10 PID ownership verifier currently requires the approved Windows host")
    completed = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )
    listener_pid = listening_pid_for_port(completed.stdout, port)
    if not pid_is_owned_by(pid, listener_pid, _windows_parent_pid_map()):
        raise RuntimeError(
            f"PID ownership mismatch for 127.0.0.1:{port}; "
            f"owned root {pid}, listener {listener_pid}"
        )
    return listener_pid


def _terminate_owned_process(process: subprocess.Popen[str] | None, label: str) -> dict[str, object]:
    if process is None:
        return {"label": label, "pid": None, "action": "not_started", "exit_code": None}
    if process.poll() is not None:
        return {"label": label, "pid": process.pid, "action": "already_exited", "exit_code": process.returncode}
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"owned {label} process did not stop") from None
        return {
            "label": label, "pid": process.pid, "action": "taskkill_owned_tree",
            "taskkill_exit": completed.returncode, "exit_code": process.returncode,
        }
    process.terminate()
    process.wait(timeout=15)
    return {"label": label, "pid": process.pid, "action": "terminate", "exit_code": process.returncode}


def _serve_acceptance_backend(port: int, frontend_origin: str) -> int:
    if not (0 < port < 65536) or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]+", frontend_origin):
        raise AcceptanceBoundaryError("invalid internal backend binding")
    os.environ["VR_ALLOW_ORIGINS"] = frontend_origin
    service = build_fixture_service()
    backend = str(REPO_ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from app import create_app
    import uvicorn

    application = create_app(industry_research_service=service)
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    return 0


def _run_backend_phase(evidence: list[CommandEvidence]) -> None:
    result, _ = _run_command(
        backend_test_command(), cwd=REPO_ROOT, environment=clean_child_environment(),
        label="backend-offline-full", timeout=1200,
    )
    evidence.append(result)


def _run_frontend_phase(evidence: list[CommandEvidence]) -> None:
    environment = clean_child_environment()
    environment["npm_config_cache"] = str((ACCEPTANCE_ROOT / "cache" / "npm").resolve())
    for index, command in enumerate(frontend_test_commands(), 1):
        result, _ = _run_command(
            command, cwd=REPO_ROOT / "frontend", environment=environment,
            label=("frontend-main-tests", "frontend-legacy-tests", "frontend-production-build")[index - 1],
            timeout=1200,
        )
        evidence.append(result)


def _run_browser_phase(evidence: list[CommandEvidence]) -> dict[str, object]:
    tools_environment = clean_child_environment()
    tools_environment["npm_config_cache"] = str((ACCEPTANCE_ROOT / "cache" / "npm").resolve())
    tools_environment["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS)
    tools_environment["V02_W3_PLAYWRIGHT_TOOLS"] = str(PLAYWRIGHT_TOOLS)
    install, browser_install = playwright_install_commands()
    for label, command in (
        ("playwright-pinned-install", install),
        ("playwright-chromium-install", browser_install),
    ):
        result, _ = _run_command(
            command, cwd=REPO_ROOT, environment=tools_environment,
            label=label, timeout=1200,
        )
        evidence.append(result)
    preflight, preflight_output = _run_command(
        ["node", str(BROWSER_SCRIPT), "--preflight"],
        cwd=REPO_ROOT, environment=tools_environment,
        label="playwright-preflight", timeout=120,
    )
    evidence.append(preflight)
    preflight_document = json.loads(preflight_output.splitlines()[-1])

    backend_reservation, frontend_reservation = reserve_loopback_ports()
    backend_port = backend_reservation.port
    frontend_port = frontend_reservation.port
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    backend_process: subprocess.Popen[str] | None = None
    frontend_process: subprocess.Popen[str] | None = None
    browser_process: subprocess.Popen[str] | None = None
    log_directory = ACCEPTANCE_ROOT / "logs"
    result_directory = ACCEPTANCE_ROOT / "results"
    profile_directory = ACCEPTANCE_ROOT / "profile"
    log_directory.mkdir(parents=True, exist_ok=True)
    result_directory.mkdir(parents=True, exist_ok=True)
    backend_log_path = log_directory / "backend.log"
    frontend_log_path = log_directory / "vite.log"
    browser_log_path = log_directory / "browser.log"
    process_cleanup: list[dict[str, object]] = []
    browser_exit: int | None = None
    try:
        backend_reservation.close()
        backend_environment = clean_child_environment()
        backend_environment["VR_ALLOW_ORIGINS"] = frontend_url
        validate_child_environment(backend_environment)
        backend_log = backend_log_path.open("w", encoding="utf-8")
        backend_process = subprocess.Popen(
            [
                sys.executable, str(Path(__file__).resolve()), "--internal-backend",
                "--port", str(backend_port), "--frontend-origin", frontend_url,
            ],
            cwd=REPO_ROOT,
            env=backend_environment,
            text=True,
            stdout=backend_log,
            stderr=subprocess.STDOUT,
        )
        frontend_reservation.close()
        frontend_environment = clean_child_environment()
        frontend_environment["VITE_API_URL"] = backend_url
        frontend_environment["npm_config_cache"] = str((ACCEPTANCE_ROOT / "cache" / "npm").resolve())
        validate_child_environment(frontend_environment)
        frontend_log = frontend_log_path.open("w", encoding="utf-8")
        frontend_process = subprocess.Popen(
            [
                "node", str(REPO_ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"),
                "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort",
            ],
            cwd=REPO_ROOT / "frontend",
            env=frontend_environment,
            text=True,
            stdout=frontend_log,
            stderr=subprocess.STDOUT,
        )
        _wait_for_http(f"{backend_url}/api/industry-research/storage?window_days=90", backend_process)
        _wait_for_http(f"{frontend_url}/industry-research", frontend_process)
        backend_listener_pid = _assert_pid_owns_port(backend_process.pid, backend_port)
        frontend_listener_pid = _assert_pid_owns_port(frontend_process.pid, frontend_port)

        browser_environment = dict(tools_environment)
        browser_environment.update({
            "V02_W3_BASE_URL": frontend_url,
            "V02_W3_BACKEND_URL": backend_url,
            "V02_W3_OUTPUT_DIR": str(result_directory.resolve()),
            "V02_W3_PROFILE_DIR": str(profile_directory.resolve()),
        })
        validate_child_environment(browser_environment)
        browser_log = browser_log_path.open("w", encoding="utf-8")
        browser_started = time.monotonic()
        browser_process = subprocess.Popen(
            ["node", str(BROWSER_SCRIPT), "--run"],
            cwd=REPO_ROOT,
            env=browser_environment,
            text=True,
            stdout=browser_log,
            stderr=subprocess.STDOUT,
        )
        try:
            browser_exit = browser_process.wait(timeout=300)
        except subprocess.TimeoutExpired:
            process_cleanup.append(_terminate_owned_process(browser_process, "browser"))
            raise RuntimeError("browser acceptance timed out") from None
        browser_duration = time.monotonic() - browser_started
        browser_log.close()
        browser_log = None
        output = browser_log_path.read_text(encoding="utf-8", errors="replace")
        write_console_output(output)
        evidence.append(CommandEvidence(
            label="browser-matrix", command=("node", str(BROWSER_SCRIPT), "--run"),
            cwd=str(REPO_ROOT), exit_code=browser_exit,
            duration_seconds=round(browser_duration, 3), summary=_summary(output),
        ))
        if browser_exit != 0:
            raise RuntimeError(f"browser acceptance failed with exit {browser_exit}")
        browser_results = json.loads((result_directory / "browser-results.json").read_text(encoding="utf-8"))
        validate_network_log(
            browser_results["network"],
            allowed_origins={frontend_url, backend_url},
        )
        screenshot = result_directory / "01-industry-research-storage.png"
        if not screenshot.is_file():
            raise RuntimeError("formal browser screenshot missing")
        return {
            "preflight": preflight_document,
            "browser_results": browser_results,
            "screenshot_path": screenshot,
            "backend_pid": backend_process.pid,
            "backend_listener_pid": backend_listener_pid,
            "frontend_pid": frontend_process.pid,
            "frontend_listener_pid": frontend_listener_pid,
            "browser_runner_pid": browser_process.pid,
            "backend_port": backend_port,
            "frontend_port": frontend_port,
            "backend_url": backend_url,
            "frontend_url": frontend_url,
            "process_cleanup": process_cleanup,
        }
    finally:
        for resource_name in ("browser_log", "frontend_log", "backend_log"):
            resource = locals().get(resource_name)
            if resource is not None and not resource.closed:
                resource.close()
        if browser_process is not None and browser_process.poll() is None:
            process_cleanup.append(_terminate_owned_process(browser_process, "browser"))
        process_cleanup.append(_terminate_owned_process(frontend_process, "vite"))
        process_cleanup.append(_terminate_owned_process(backend_process, "backend"))
        for reservation in (backend_reservation, frontend_reservation):
            try:
                reservation.close()
            except OSError:
                pass


def _write_acceptance_evidence(
    *,
    implementation_commit: str,
    implementation_tree: str,
    command_evidence: list[CommandEvidence],
    browser: dict[str, object],
    frontend_lock_before: str,
    frontend_lock_after: str,
    cleanup: dict[str, object],
    screenshot_bytes: bytes,
) -> tuple[Path, Path, Path]:
    screenshot_target = REPO_ROOT / "docs" / "screenshots" / "v0.2-w3" / "01-industry-research-storage.png"
    screenshot_target.parent.mkdir(parents=True, exist_ok=True)
    screenshot_target.write_bytes(screenshot_bytes)
    fixture_hash = _sha256(SOURCE_FIXTURE)
    screenshot_hash = _sha256(screenshot_target)
    browser_results = browser["browser_results"]
    assert isinstance(browser_results, dict)
    manifest = {
        "schema_version": 1,
        "work": "PP03 V0.2-W3 industry research",
        "status": "PASS",
        "tested_implementation_commit": implementation_commit,
        "tested_implementation_tree": implementation_tree,
        "evidence_commit": "SELF: the Git commit containing this manifest and generated evidence",
        "commit_relationship": "The clean implementation commit above was tested first; this later evidence commit only adds the report, manifest, and screenshot.",
        "commands": [
            {
                "label": item.label,
                "command": list(item.command),
                "cwd": item.cwd,
                "exit_code": item.exit_code,
                "duration_seconds": item.duration_seconds,
                "summary": item.summary,
            }
            for item in command_evidence
        ],
        "test_counts": {
            "runner_self_tests": "25 passed",
            "backend_offline": next((item.summary for item in command_evidence if item.label == "backend-offline-full"), ""),
            "frontend_main": next((item.summary for item in command_evidence if item.label == "frontend-main-tests"), ""),
            "frontend_legacy": next((item.summary for item in command_evidence if item.label == "frontend-legacy-tests"), ""),
            "production_build": next((item.summary for item in command_evidence if item.label == "frontend-production-build"), ""),
        },
        "browser": {
            "playwright_version": browser["preflight"]["playwrightVersion"],
            "chromium_version": browser_results["chromiumVersion"],
            "chromium_executable": browser["preflight"]["executablePath"],
            "backend_pid": browser["backend_pid"],
            "backend_listener_pid": browser["backend_listener_pid"],
            "frontend_pid": browser["frontend_pid"],
            "frontend_listener_pid": browser["frontend_listener_pid"],
            "browser_runner_pid": browser["browser_runner_pid"],
            "backend_port": browser["backend_port"],
            "frontend_port": browser["frontend_port"],
            "backend_url": browser["backend_url"],
            "frontend_url": browser["frontend_url"],
            "checks": browser_results["checks"],
            "console_errors_or_warnings": browser_results["consoleMessages"],
            "page_errors": browser_results["pageErrors"],
            "request_failures": browser_results["failedRequests"],
        },
        "hashes": {
            "source_fixture_sha256": fixture_hash,
            "formal_screenshot_sha256": screenshot_hash,
            "frontend_package_lock_before": frontend_lock_before,
            "frontend_package_lock_after": frontend_lock_after,
        },
        "truth_boundary": {
            "fixture_demo": True,
            "production_demo_rejected": True,
            "production_live_refresh_called": False,
            "paid_provider_called": False,
            "enterprise_provider_called": False,
            "user_key_used": False,
            "real_user_data_read": False,
            "real_holdings_amount_cost_account_read": False,
        },
        "cleanup": cleanup,
        "limitations": [
            "A2-W1 historical Live terminal remains failed/evidence_compatibility_failed; this run is offline fixture-backed acceptance, not a new Live success.",
            "Industry price, inventory, utilization, capital, funds and valuation fields without qualified sources remain explicit empty states.",
        ],
    }
    manifest_path = REPO_ROOT / "docs" / "acceptance" / "v0.2-w3-industry-evidence-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = REPO_ROOT / "docs" / "acceptance" / "v0.2-w3-industry-research.md"
    report_path.write_text(
        "\n".join([
            "# PP03 V0.2-W3 行业研究隔离浏览器验收",
            "",
            f"- 被测实现提交：`{implementation_commit}`",
            f"- 被测树：`{implementation_tree}`",
            "- 结论：`PASS`（隔离 fixture、离线回归与浏览器真实性闸门通过）",
            "- 证据提交：包含本报告、manifest 与正式截图的后续独立提交；不回写被测实现提交。",
            "",
            "## 浏览器矩阵",
            "",
            *[f"- PASS：{item['name']}" for item in browser_results["checks"]],
            "",
            "## 数据真实性",
            "",
            "页面永久显示“隔离演示”，生产默认 app 拒绝 `demo=true`。本次未触发生产 Live、付费或企业来源，未使用用户 Key、账号登录或真实持仓数据。待核验、冲突与来源失败内容没有进入可信结论；来源失败时仍显示同一旧可信快照 lineage。",
            "",
            "## 已知限制",
            "",
            "- 本验收只证明固定提交上的离线服务、前端与隔离 Chromium 闭环，不代表真实行业数据已可得。",
            "- DRAM/NAND 价格、HBM 需求、库存、产能利用率、行业资本开支、资金、ETF 份额、估值与基金暴露仍按来源资格显示准确空态。",
            "- A2-W1 历史 Live 终态仍为 `failed / evidence_compatibility_failed`，未改写为成功。",
            "",
            "## 清理",
            "",
            "验收后端、Vite 和 Chromium runner 均按自有 PID 精确停止；Profile、缓存、results、运行 fixture、临时数据库、Playwright tools/browser 均已删除。受版本控制的源 fixture 与正式报告、manifest、截图保留。",
            "",
        ]),
        encoding="utf-8",
    )
    return report_path, manifest_path, screenshot_target


def _run_all() -> int:
    _remove_disposable_targets()
    # Recreate the isolated descendants after clearing stale, task-owned state.
    for path in _ISOLATED_PATHS.values():
        path.mkdir(parents=True, exist_ok=True)
    command_evidence: list[CommandEvidence] = []
    implementation_commit = _git("rev-parse", "HEAD")
    implementation_tree = _git("rev-parse", "HEAD^{tree}")
    if _git("status", "--short"):
        raise RuntimeError("all phase requires a clean implementation commit before evidence generation")
    frontend_lock = REPO_ROOT / "frontend" / "package-lock.json"
    frontend_lock_before = _sha256(frontend_lock)
    browser: dict[str, object] | None = None
    try:
        self_test, _ = _run_command(
            [sys.executable, str(Path(__file__).resolve()), "--phase", "runner-self-test", "--keep-runtime"],
            cwd=REPO_ROOT, environment=clean_child_environment(), label="runner-self-test", timeout=180,
        )
        command_evidence.append(self_test)
        _run_backend_phase(command_evidence)
        _run_frontend_phase(command_evidence)
        browser = _run_browser_phase(command_evidence)
        frontend_lock_after = _sha256(frontend_lock)
        if frontend_lock_after != frontend_lock_before:
            raise RuntimeError("frontend package-lock.json drifted during acceptance")
        browser_results = browser["browser_results"]
        if not isinstance(browser_results, dict) or browser_results.get("status") != "pass":
            raise RuntimeError("browser result did not report pass")
        screenshot_source = Path(browser["screenshot_path"])
        screenshot_bytes = screenshot_source.read_bytes()
        cleanup = _remove_disposable_targets()
        cleanup["processes"] = browser["process_cleanup"]
        outputs = _write_acceptance_evidence(
            implementation_commit=implementation_commit,
            implementation_tree=implementation_tree,
            command_evidence=command_evidence,
            browser=browser,
            frontend_lock_before=frontend_lock_before,
            frontend_lock_after=frontend_lock_after,
            cleanup=cleanup,
            screenshot_bytes=screenshot_bytes,
        )
        write_console_output(
            json.dumps({"status": "PASS", "outputs": [str(item) for item in outputs]}, ensure_ascii=False) + "\n"
        )
        return 0
    finally:
        # If any stage fails, remove all task-owned runtime state but never source
        # fixtures or formal evidence from an earlier successful fixed Head.
        _remove_disposable_targets()


def _run_runner_self_test() -> int:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "backend/tests/test_v02_w3_acceptance_runner.py",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    return subprocess.run(command, cwd=REPO_ROOT, env=os.environ.copy(), check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("runner-self-test", "backend-tests", "frontend-tests", "all"),
    )
    parser.add_argument("--keep-runtime", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--internal-backend", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--frontend-origin", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.internal_backend:
        if args.phase is not None or args.port is None or args.frontend_origin is None:
            parser.error("invalid internal backend arguments")
        return _serve_acceptance_backend(args.port, args.frontend_origin)
    if args.phase is None:
        parser.error("--phase is required")
    if args.phase == "runner-self-test":
        try:
            return _run_runner_self_test()
        finally:
            if not args.keep_runtime:
                _remove_disposable_targets()
    if args.phase == "backend-tests":
        try:
            evidence: list[CommandEvidence] = []
            _run_backend_phase(evidence)
            return 0
        finally:
            if not args.keep_runtime:
                _remove_disposable_targets()
    if args.phase == "frontend-tests":
        try:
            evidence = []
            _run_frontend_phase(evidence)
            return 0
        finally:
            if not args.keep_runtime:
                _remove_disposable_targets()
    if args.phase == "all":
        if args.keep_runtime:
            parser.error("all phase always owns final cleanup")
        return _run_all()
    parser.error(f"unsupported phase: {args.phase}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
