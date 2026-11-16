"""Tests for ERP to Plant domain model mapping and CP-SAT solving."""

from planpilot.erp.generator import LegacyExtractGenerator, SAPExtractGenerator
from planpilot.erp.mapper import ERPMapper
from planpilot.erp.models import ERPFormat
from planpilot.solver.scheduler import solve


def test_map_sap_to_plant_and_solve() -> None:
    """Verify mapped SAP extract creates a valid Plant that CP-SAT solves to optimal."""
    gen = SAPExtractGenerator(seed=42)
    clean_data = gen.generate_clean_dataset(
        num_products=3,
        num_components=4,
        num_work_centers=3,
        num_orders=4,
    )

    mapper = ERPMapper()
    plant = mapper.map_to_plant(clean_data, format_type=ERPFormat.SAP)

    # 1. Referential integrity check
    plant.validate_referential_integrity()

    assert len(plant.work_centers) == 3
    assert len(plant.machines) == 3
    assert len(plant.materials) == 7
    assert len(plant.routings) == 3
    assert len(plant.orders) == 4

    # 2. Solve with CP-SAT
    schedule = solve(plant, time_limit_seconds=5.0)
    assert schedule.solver_stats.status.value in {"optimal", "feasible"}
    assert len(schedule.assignments) > 0
    assert schedule.kpis.makespan > 0


def test_map_legacy_to_plant_and_solve() -> None:
    """Verify mapped Legacy extract creates a valid Plant that solves with CP-SAT."""
    gen = LegacyExtractGenerator(seed=42)
    clean_data = gen.generate_clean_dataset(
        num_products=3,
        num_components=4,
        num_work_centers=3,
        num_orders=4,
    )

    mapper = ERPMapper()
    plant = mapper.map_to_plant(clean_data, format_type=ERPFormat.LEGACY)

    plant.validate_referential_integrity()
    assert len(plant.orders) == 4

    schedule = solve(plant, time_limit_seconds=5.0)
    assert schedule.solver_stats.status.value in {"optimal", "feasible"}
    assert schedule.kpis.makespan > 0
