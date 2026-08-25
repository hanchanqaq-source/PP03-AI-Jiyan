from __future__ import annotations

from dataclasses import dataclass

from .models import TemplateStatus, WireModel, _require_enum


REPORT_SECTION_IDS = (
    "overview",
    "cycle",
    "chain",
    "metrics",
    "capital",
    "companies",
    "funds",
    "news_risk",
)


@dataclass(frozen=True, slots=True)
class IndustryReportTemplate(WireModel):
    industry_id: str
    label: str
    status: TemplateStatus
    section_ids: tuple[str, ...]
    cycle_metric_ids: tuple[str, ...]
    chain_node_ids: tuple[str, ...]
    core_metric_ids: tuple[str, ...] = ()
    capital_metric_ids: tuple[str, ...] = ()
    empty_reason: str | None = None

    def __post_init__(self) -> None:
        _require_enum(self.status, TemplateStatus, "status")
        if self.status is TemplateStatus.BUILDING:
            if (
                self.section_ids or self.cycle_metric_ids or self.chain_node_ids
                or self.core_metric_ids or self.capital_metric_ids
            ):
                raise ValueError("building template must not inherit industry fields")
            if not self.empty_reason:
                raise ValueError("building template requires empty_reason")
        elif self.section_ids != REPORT_SECTION_IDS:
            raise ValueError("registered template requires the fixed eight report sections")


_STORAGE_CYCLE_METRICS = (
    "dram_price",
    "nand_price",
    "hbm_demand",
    "inventory_level",
    "capacity_utilization",
    "manufacturer_capex",
    "server_demand",
    "consumer_electronics_demand",
)

_CAPITAL_METRICS = (
    "sector_fund_flow",
    "etf_share",
    "industry_valuation",
    "historical_valuation_percentile",
)


_TEMPLATES = {
    "storage": IndustryReportTemplate(
        industry_id="storage",
        label="存储",
        status=TemplateStatus.COMPLETE_LAYOUT,
        section_ids=REPORT_SECTION_IDS,
        cycle_metric_ids=_STORAGE_CYCLE_METRICS,
        chain_node_ids=(
            "equipment_materials",
            "memory_design_manufacturing",
            "packaging_testing",
            "modules_controllers",
            "end_applications",
        ),
        core_metric_ids=_STORAGE_CYCLE_METRICS,
        capital_metric_ids=_CAPITAL_METRICS,
    ),
    "semiconductor": IndustryReportTemplate(
        industry_id="semiconductor",
        label="半导体",
        status=TemplateStatus.PARTIAL_LAYOUT,
        section_ids=REPORT_SECTION_IDS,
        cycle_metric_ids=(
            "equipment_book_to_bill",
            "wafer_fab_utilization",
            "foundry_revenue",
            "design_activity",
            "packaging_demand",
            "end_market_demand",
        ),
        chain_node_ids=(
            "equipment_materials",
            "design",
            "wafer_manufacturing",
            "packaging_testing",
            "end_market",
        ),
        core_metric_ids=(
            "equipment_book_to_bill",
            "wafer_fab_utilization",
            "foundry_revenue",
            "design_activity",
            "packaging_demand",
            "end_market_demand",
        ),
        capital_metric_ids=_CAPITAL_METRICS,
    ),
    "robotics": IndustryReportTemplate(
        industry_id="robotics",
        label="机器人",
        status=TemplateStatus.PARTIAL_LAYOUT,
        section_ids=REPORT_SECTION_IDS,
        cycle_metric_ids=(
            "prototype_progress",
            "orders",
            "delivery",
            "mass_production",
        ),
        chain_node_ids=(
            "core_components",
            "complete_machine",
            "software_vision",
            "applications",
        ),
        core_metric_ids=(
            "prototype_progress",
            "orders",
            "delivery",
            "mass_production",
        ),
        capital_metric_ids=_CAPITAL_METRICS,
    ),
}


def get_industry_template(industry_id: str) -> IndustryReportTemplate:
    template = _TEMPLATES.get(industry_id)
    if template is not None:
        return template
    return IndustryReportTemplate(
        industry_id=industry_id,
        label=industry_id,
        status=TemplateStatus.BUILDING,
        section_ids=(),
        cycle_metric_ids=(),
        chain_node_ids=(),
        core_metric_ids=(),
        capital_metric_ids=(),
        empty_reason="template_building",
    )


def registered_industry_templates() -> tuple[IndustryReportTemplate, ...]:
    return tuple(_TEMPLATES.values())


def validate_metric_section_shape(
    *,
    industry_id: str,
    cycle_metric_ids: tuple[str, ...],
    core_metric_ids: tuple[str, ...],
    capital_metric_ids: tuple[str, ...],
) -> None:
    template = get_industry_template(industry_id)
    actual = (cycle_metric_ids, core_metric_ids, capital_metric_ids)
    expected = (
        template.cycle_metric_ids,
        template.core_metric_ids,
        template.capital_metric_ids,
    )
    if actual != expected:
        raise ValueError("report sections require exact canonical metric rows")
