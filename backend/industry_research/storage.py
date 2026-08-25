from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping

from cache_io_lock import CACHE_IO_LOCK
from evidence_verification.storage import (
    _cleanup_owned_temp,
    _close_owned_descriptor,
    _ensure_directory,
    _replace_durable,
    _safe_directory,
)

from .models import (
    ConclusionStatus,
    DataCompleteness,
    DisplayedTrustedReport,
    FundResolutionEmptyReason,
    FundSelectionScope,
    IndustryChainNode,
    IndustryCompanyRelation,
    IndustryConclusion,
    IndustryEvidenceEvent,
    IndustryFundRelation,
    IndustryFundRelationResolution,
    IndustryMetricObservation,
    ReportCounts,
    SourceCoverage,
    TemplateStatus,
    VerificationStatus,
)


_MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
_INDUSTRY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REPORT_KEYS = {
    "industry_id", "template_status", "trusted_snapshot_id",
    "displayed_trusted_snapshot_id", "generated_at", "demo",
    "source_coverage", "counts", "overview", "cycle", "chain", "metrics",
    "capital", "companies", "fund_selection", "funds", "news_risk",
}
_LINEAGE_KEYS = {
    "industry_id", "raw_snapshot_id", "evidence_snapshot_id", "trusted_snapshot_id",
}
_REQUIRED_SECTION_KEYS = {
    "overview", "cycle", "chain", "metrics", "capital", "companies", "funds", "news_risk",
}


@dataclass(frozen=True, slots=True)
class TrustedPublicationResult:
    displayed_report: DisplayedTrustedReport | None
    published_trusted_snapshot_id: str | None
    previous_trusted_snapshot_id: str | None
    error_code: str | None


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _mapping(value: object, *, keys: set[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"invalid {name} schema")
    return value


def _sequence(value: object, name: str) -> list[Any]:
    if type(value) is not list:
        raise ValueError(f"invalid {name} collection")
    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    rows = _sequence(value, name)
    if any(type(row) is not str for row in rows):
        raise ValueError(f"invalid {name}")
    return tuple(rows)


def _conclusion(value: object) -> IndustryConclusion:
    row = _mapping(value, keys={
        "conclusion_id", "industry_id", "rule_version", "status", "cycle_stage",
        "outlook_direction", "confidence_level", "data_completeness", "text",
        "basis_metric_ids", "evidence_ids", "invalidating_conditions",
    }, name="conclusion")
    completeness = _mapping(
        row["data_completeness"],
        keys={"verified_metric_count", "required_metric_count", "ratio"},
        name="data completeness",
    )
    return IndustryConclusion(
        conclusion_id=row["conclusion_id"],
        industry_id=row["industry_id"],
        rule_version=row["rule_version"],
        status=ConclusionStatus(row["status"]),
        cycle_stage=row["cycle_stage"],
        outlook_direction=row["outlook_direction"],
        confidence_level=row["confidence_level"],
        data_completeness=DataCompleteness(**completeness),
        text=row["text"],
        basis_metric_ids=_strings(row["basis_metric_ids"], "basis metric IDs"),
        evidence_ids=_strings(row["evidence_ids"], "conclusion evidence IDs"),
        invalidating_conditions=_strings(row["invalidating_conditions"], "conclusion invalidating conditions"),
    )


def _chain_node(value: object) -> IndustryChainNode:
    row = _mapping(value, keys={
        "industry_id", "node_id", "label", "observation_ids", "evidence_ids", "status",
    }, name="chain node")
    return IndustryChainNode(
        industry_id=row["industry_id"], node_id=row["node_id"], label=row["label"],
        observation_ids=_strings(row["observation_ids"], "observation IDs"),
        evidence_ids=_strings(row["evidence_ids"], "chain evidence IDs"),
        status=ConclusionStatus(row["status"]),
    )


def _company(value: object) -> IndustryCompanyRelation:
    row = _mapping(value, keys={
        "industry_id", "security_code", "company_name", "chain_node_id", "relation_type",
        "key_metric_ids", "evidence_ids", "as_of_date", "observation_only",
    }, name="company relation")
    return IndustryCompanyRelation(
        industry_id=row["industry_id"], security_code=row["security_code"],
        company_name=row["company_name"], chain_node_id=row["chain_node_id"],
        relation_type=row["relation_type"],
        key_metric_ids=_strings(row["key_metric_ids"], "company metric IDs"),
        evidence_ids=_strings(row["evidence_ids"], "company evidence IDs"),
        as_of_date=row["as_of_date"], observation_only=row["observation_only"],
    )


def _fund_selection(value: object) -> FundSelectionScope:
    row = _mapping(value, keys={"selection_id", "fund_code", "selected_in_request"}, name="fund selection")
    return FundSelectionScope(**row)


def _fund_resolution(value: object) -> IndustryFundRelationResolution:
    row = _mapping(value, keys={"selection_id", "fund_code", "relation", "empty_reason"}, name="fund resolution")
    relation_value = row["relation"]
    relation = None
    if relation_value is not None:
        relation_row = _mapping(relation_value, keys={
            "industry_id", "fund_code", "relation_layer", "exposure_value", "exposure_unit",
            "disclosure_date", "evidence_ids", "status",
        }, name="fund relation")
        relation = IndustryFundRelation(
            industry_id=relation_row["industry_id"], fund_code=relation_row["fund_code"],
            relation_layer=relation_row["relation_layer"], exposure_value=relation_row["exposure_value"],
            exposure_unit=relation_row["exposure_unit"], disclosure_date=relation_row["disclosure_date"],
            evidence_ids=_strings(relation_row["evidence_ids"], "fund evidence IDs"),
            status=VerificationStatus(relation_row["status"]),
        )
    empty = row["empty_reason"]
    return IndustryFundRelationResolution(
        selection_id=row["selection_id"], fund_code=row["fund_code"], relation=relation,
        empty_reason=FundResolutionEmptyReason(empty) if empty is not None else None,
    )


def _event(value: object) -> IndustryEvidenceEvent:
    row = _mapping(value, keys={
        "industry_id", "event_id", "status", "occurred_at", "evidence_ids", "roles",
    }, name="industry event")
    return IndustryEvidenceEvent(
        industry_id=row["industry_id"], event_id=row["event_id"],
        status=VerificationStatus(row["status"]), occurred_at=row["occurred_at"],
        evidence_ids=_strings(row["evidence_ids"], "event evidence IDs"),
        roles=_strings(row["roles"], "event roles"),
    )


def _report_from_document(value: object) -> DisplayedTrustedReport:
    row = _mapping(value, keys=_REPORT_KEYS, name="trusted report")
    if not _REQUIRED_SECTION_KEYS.issubset(row):
        raise ValueError("trusted snapshot requires all required report sections")
    coverage = _mapping(row["source_coverage"], keys={
        "unit", "total", "configured", "healthy", "partial_failure", "failed", "unconfigured",
    }, name="source coverage")
    counts = _mapping(row["counts"], keys={"verified", "corroborated"}, name="report counts")
    return DisplayedTrustedReport(
        industry_id=row["industry_id"],
        template_status=TemplateStatus(row["template_status"]),
        trusted_snapshot_id=row["trusted_snapshot_id"],
        displayed_trusted_snapshot_id=row["displayed_trusted_snapshot_id"],
        generated_at=row["generated_at"],
        demo=row["demo"],
        source_coverage=SourceCoverage(**coverage),
        counts=ReportCounts(**counts),
        overview=_conclusion(row["overview"]),
        cycle=tuple(IndustryMetricObservation.from_dict(item) for item in _sequence(row["cycle"], "cycle")),
        chain=tuple(_chain_node(item) for item in _sequence(row["chain"], "chain")),
        metrics=tuple(IndustryMetricObservation.from_dict(item) for item in _sequence(row["metrics"], "metrics")),
        capital=tuple(IndustryMetricObservation.from_dict(item) for item in _sequence(row["capital"], "capital")),
        companies=tuple(_company(item) for item in _sequence(row["companies"], "companies")),
        fund_selection=tuple(
            _fund_selection(item) for item in _sequence(row["fund_selection"], "fund selection")
        ),
        funds=tuple(_fund_resolution(item) for item in _sequence(row["funds"], "funds")),
        news_risk=tuple(_event(item) for item in _sequence(row["news_risk"], "news risk")),
    )


def _validate_report(report: DisplayedTrustedReport) -> None:
    if type(report) is not DisplayedTrustedReport:
        raise TypeError("trusted snapshot requires DisplayedTrustedReport")
    if not report.trusted_snapshot_id or report.trusted_snapshot_id != report.displayed_trusted_snapshot_id:
        raise ValueError("trusted report snapshot identity mismatch")
    if not report.generated_at:
        raise ValueError("trusted report requires generated_at")
    generated = datetime.fromisoformat(report.generated_at.replace("Z", "+00:00"))
    if generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError("trusted report generated_at must be timezone-aware")
    observations = report.cycle + report.metrics + report.capital
    expected = ReportCounts(
        verified=sum(row.verification_status is VerificationStatus.VERIFIED for row in observations),
        corroborated=sum(row.verification_status is VerificationStatus.CORROBORATED for row in observations),
    )
    if report.counts != expected:
        raise ValueError("trusted report counts do not match observations")
    raw_ids = {row.raw_snapshot_id for row in observations}
    evidence_ids = {row.evidence_snapshot_id for row in observations}
    if len(raw_ids) > 1 or len(evidence_ids) > 1:
        raise ValueError("trusted report observations must share one raw/evidence lineage")


def _lineage_document(
    report: DisplayedTrustedReport,
    *,
    expected_industry_id: str,
    expected_raw_snapshot_id: str,
    expected_evidence_snapshot_id: str,
) -> dict[str, str]:
    values = {
        "industry_id": expected_industry_id,
        "raw_snapshot_id": expected_raw_snapshot_id,
        "evidence_snapshot_id": expected_evidence_snapshot_id,
        "trusted_snapshot_id": report.trusted_snapshot_id or "",
    }
    if any(
        not isinstance(value, str) or not value.strip() or value != value.strip()
        for value in values.values()
    ):
        raise ValueError("trusted publication lineage must not be blank")
    if report.industry_id != expected_industry_id:
        raise ValueError("trusted publication industry_id mismatch")
    observations = report.cycle + report.metrics + report.capital
    if any(row.raw_snapshot_id != expected_raw_snapshot_id for row in observations):
        raise ValueError("trusted observation raw lineage mismatch")
    if any(row.evidence_snapshot_id != expected_evidence_snapshot_id for row in observations):
        raise ValueError("trusted observation evidence lineage mismatch")
    return values
class IndustryResearchStorage:
    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        production: bool = True,
    ) -> None:
        if root is not None:
            selected = Path(root)
        elif os.environ.get("VR_DATA_DIR"):
            selected = Path(os.environ["VR_DATA_DIR"]) / "industry-research" / "v1"
        else:
            profile = Path(os.environ.get("USERPROFILE") or Path.home())
            selected = profile / ".vibe-research" / "industry-research" / "v1"
        self.root = Path(os.path.abspath(selected))
        self.production = production

    @staticmethod
    def _component(industry_id: str) -> str:
        if not isinstance(industry_id, str) or not _INDUSTRY_ID.fullmatch(industry_id):
            raise ValueError("invalid industry_id for storage")
        return industry_id

    def trusted_snapshot_path(self, industry_id: str) -> Path:
        path = self.root / self._component(industry_id) / "trusted_snapshot.json"
        if os.path.commonpath((str(self.root), os.path.abspath(path))) != str(self.root):
            raise ValueError("industry storage path escapes root")
        return path

    def _inside_root(self, path: Path) -> bool:
        try:
            return os.path.commonpath((str(self.root), os.path.abspath(path))) == str(self.root)
        except (OSError, ValueError):
            return False

    def _verify_directory_chain(self, directory: Path) -> None:
        if not self._inside_root(directory):
            raise OSError("storage_error")
        relative = directory.relative_to(self.root)
        cursor = self.root
        if not _safe_directory(cursor):
            raise OSError("storage_error")
        for component in relative.parts:
            cursor = cursor / component
            if not _safe_directory(cursor):
                raise OSError("storage_error")

    def _verify_parent(self, path: Path) -> None:
        if not self._inside_root(path):
            raise OSError("storage_error")
        self._verify_directory_chain(path.parent)

    def _atomic_write(self, path: Path, payload: bytes) -> None:
        descriptor: int | None = None
        temp_path: Path | None = None
        identity: tuple[int, int] | None = None
        try:
            _ensure_directory(path.parent)
            self._verify_parent(path)
            descriptor, raw_temp = tempfile.mkstemp(
                prefix=".trusted_snapshot.", suffix=".tmp", dir=path.parent,
            )
            temp_path = Path(raw_temp)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            try:
                handle = os.fdopen(descriptor, "wb")
            except BaseException:
                _close_owned_descriptor(descriptor, identity)
                descriptor = None
                raise
            descriptor = None
            with handle:
                written = handle.write(payload)
                if written != len(payload):
                    raise OSError("storage_error")
                handle.flush()
                os.fsync(handle.fileno())
            _replace_durable(temp_path, path)
            temp_path = None
        except OSError:
            raise OSError("storage_error") from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temp_path is not None and identity is not None:
                _cleanup_owned_temp(temp_path, identity)

    def load_current(self, industry_id: str) -> DisplayedTrustedReport | None:
        path = self.trusted_snapshot_path(industry_id)
        descriptor: int | None = None
        identity: tuple[int, int] | None = None
        try:
            with CACHE_IO_LOCK:
                self._verify_parent(path)
                before = path.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or getattr(before, "st_reparse_tag", 0)
                    or before.st_nlink != 1
                ):
                    return None
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags)
                opened = os.fstat(descriptor)
                identity = (opened.st_dev, opened.st_ino)
                if identity != (before.st_dev, before.st_ino) or opened.st_nlink != 1:
                    return None
                try:
                    handle = os.fdopen(descriptor, "rb")
                except BaseException:
                    _close_owned_descriptor(descriptor, identity)
                    descriptor = None
                    raise
                descriptor = None
                with handle:
                    raw = handle.read(_MAX_SNAPSHOT_BYTES + 1)
            if len(raw) > _MAX_SNAPSHOT_BYTES:
                return None
            document = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON number")),
            )
            outer = _mapping(
                document,
                keys={"schema_version", "checksum", "lineage", "report"},
                name="snapshot",
            )
            if outer["schema_version"] != 1 or type(outer["checksum"]) is not str:
                return None
            signed_payload = {"lineage": outer["lineage"], "report": outer["report"]}
            if hashlib.sha256(_canonical(signed_payload)).hexdigest() != outer["checksum"]:
                return None
            lineage = _mapping(outer["lineage"], keys=_LINEAGE_KEYS, name="snapshot lineage")
            report = _report_from_document(outer["report"])
            _validate_report(report)
            report.validate_for_mode(production=self.production)
            expected_lineage = _lineage_document(
                report,
                expected_industry_id=lineage["industry_id"],
                expected_raw_snapshot_id=lineage["raw_snapshot_id"],
                expected_evidence_snapshot_id=lineage["evidence_snapshot_id"],
            )
            if expected_lineage != lineage or report.industry_id != industry_id:
                return None
            return report
        except (
            AttributeError,
            FileNotFoundError,
            OSError,
            OverflowError,
            RecursionError,
            UnicodeDecodeError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ):
            return None
        finally:
            if descriptor is not None:
                if identity is None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                else:
                    _close_owned_descriptor(descriptor, identity)

    def publish(
        self,
        report: DisplayedTrustedReport,
        *,
        expected_industry_id: str,
        expected_raw_snapshot_id: str,
        expected_evidence_snapshot_id: str,
    ) -> TrustedPublicationResult:
        _validate_report(report)
        report.validate_for_mode(production=self.production)
        lineage = _lineage_document(
            report,
            expected_industry_id=expected_industry_id,
            expected_raw_snapshot_id=expected_raw_snapshot_id,
            expected_evidence_snapshot_id=expected_evidence_snapshot_id,
        )
        return self._publish_validated(report, lineage)

    def publish_document(
        self,
        document: Mapping[str, object],
        *,
        expected_industry_id: str,
        expected_raw_snapshot_id: str,
        expected_evidence_snapshot_id: str,
    ) -> TrustedPublicationResult:
        if type(document) is not dict or not _REQUIRED_SECTION_KEYS.issubset(document):
            raise ValueError("trusted snapshot requires all required report sections")
        report = _report_from_document(document)
        _validate_report(report)
        report.validate_for_mode(production=self.production)
        lineage = _lineage_document(
            report,
            expected_industry_id=expected_industry_id,
            expected_raw_snapshot_id=expected_raw_snapshot_id,
            expected_evidence_snapshot_id=expected_evidence_snapshot_id,
        )
        return self._publish_validated(report, lineage)

    def _publish_validated(
        self,
        report: DisplayedTrustedReport,
        lineage: dict[str, str],
    ) -> TrustedPublicationResult:
        with CACHE_IO_LOCK:
            previous = self.load_current(report.industry_id)
            previous_id = previous.trusted_snapshot_id if previous is not None else None
            report_document = report.to_dict()
            signed_payload = {"lineage": lineage, "report": report_document}
            document = {
                "schema_version": 1,
                "checksum": hashlib.sha256(_canonical(signed_payload)).hexdigest(),
                "lineage": lineage,
                "report": report_document,
            }
            payload = _canonical(document) + b"\n"
            if len(payload) > _MAX_SNAPSHOT_BYTES:
                raise ValueError("trusted snapshot is too large")
            try:
                self._atomic_write(self.trusted_snapshot_path(report.industry_id), payload)
            except OSError:
                return TrustedPublicationResult(previous, None, previous_id, "storage_error")
            return TrustedPublicationResult(report, report.trusted_snapshot_id, previous_id, None)
