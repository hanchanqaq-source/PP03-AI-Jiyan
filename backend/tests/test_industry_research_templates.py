from __future__ import annotations

from industry_research.models import TemplateStatus
from industry_research.templates import (
    REPORT_SECTION_IDS,
    get_industry_template,
    registered_industry_templates,
)


def test_storage_template_has_eight_sections_eight_cycle_rows_and_five_chain_nodes() -> None:
    # Break caught: storage's complete information architecture loses a fixed row/section.
    template = get_industry_template("storage")

    assert template.status is TemplateStatus.COMPLETE_LAYOUT
    assert template.section_ids == (
        "overview",
        "cycle",
        "chain",
        "metrics",
        "capital",
        "companies",
        "funds",
        "news_risk",
    )
    assert template.section_ids == REPORT_SECTION_IDS
    assert template.cycle_metric_ids == (
        "dram_price",
        "nand_price",
        "hbm_demand",
        "inventory_level",
        "capacity_utilization",
        "manufacturer_capex",
        "server_demand",
        "consumer_electronics_demand",
    )
    assert template.chain_node_ids == (
        "equipment_materials",
        "memory_design_manufacturing",
        "packaging_testing",
        "modules_controllers",
        "end_applications",
    )


def test_semiconductor_and_robotics_templates_are_differentiated_without_field_leakage() -> None:
    # Break caught: one industry's fields are accidentally reused by another template.
    storage = get_industry_template("storage")
    semiconductor = get_industry_template("semiconductor")
    robotics = get_industry_template("robotics")

    assert "wafer_manufacturing" in semiconductor.chain_node_ids
    assert "memory_design_manufacturing" not in semiconductor.chain_node_ids
    assert "prototype_progress" in robotics.cycle_metric_ids
    assert "dram_price" not in robotics.cycle_metric_ids
    assert "software_vision" in robotics.chain_node_ids
    assert set(storage.cycle_metric_ids).isdisjoint(robotics.cycle_metric_ids)
    assert {item.industry_id for item in registered_industry_templates()} == {
        "storage",
        "semiconductor",
        "robotics",
    }


def test_unknown_template_fails_closed_as_building_with_displayable_reason() -> None:
    # Break caught: an unknown label inherits another industry's fields or AI-generated data.
    unknown = get_industry_template("custom-quantum-memory")

    assert unknown.industry_id == "custom-quantum-memory"
    assert unknown.status is TemplateStatus.BUILDING
    assert unknown.section_ids == ()
    assert unknown.cycle_metric_ids == ()
    assert unknown.chain_node_ids == ()
    assert unknown.empty_reason == "template_building"
