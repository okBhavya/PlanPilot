"""Unit tests for ScheduleDiffEngine and movement attribution."""

from __future__ import annotations

import pytest

from planpilot.domain.models import (
    Machine,
    Material,
    MaterialFamily,
    Operation,
    Order,
    Plant,
    Routing,
    WorkCenter,
)
from planpilot.explain.diff import MovementType, ScheduleDiffEngine
from planpilot.rules.dsl import PinToMachine
from planpilot.solver.scheduler import solve


@pytest.fixture
def diff_plant() -> Plant:
    """Fixture plant with two machines and two orders."""
    m1 = Machine(id="M1", work_center_id="WC1")
    m2 = Machine(id="M2", work_center_id="WC1")
    wc = WorkCenter(id="WC1", machine_ids=["M1", "M2"])

    mat = Material(id="MAT1", family=MaterialFamily.STEEL)

    op1 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1", "M2"], processing_time=20)
    rtg1 = Routing(id="RTG1", product_material_id="MAT1", operations=[op1])

    op2 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1", "M2"], processing_time=15)
    rtg2 = Routing(id="RTG2", product_material_id="MAT1", operations=[op2])

    ord1 = Order(id="ORD1", routing_id="RTG1", material_id="MAT1", due_date=40)
    ord2 = Order(id="ORD2", routing_id="RTG2", material_id="MAT1", due_date=40)

    return Plant(
        id="P_DIFF",
        machines=[m1, m2],
        work_centers=[wc],
        materials=[mat],
        routings=[rtg1, rtg2],
        orders=[ord1, ord2],
    )


class TestScheduleDiffEngine:
    def test_compare_pin_machine_change(self, diff_plant: Plant) -> None:
        """Diff engine detects machine reassignment when an operation is pinned."""
        # Baseline schedule: both operations might go to M1 or M2
        pin1 = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")
        pin2 = PinToMachine(order_id="ORD2", operation_id="OP1", machine_id="M1")
        schedule_before = solve(diff_plant, rules=[pin1, pin2])

        # After schedule: ORD2 pinned to M2
        pin2_new = PinToMachine(order_id="ORD2", operation_id="OP1", machine_id="M2")
        schedule_after = solve(diff_plant, rules=[pin1, pin2_new])

        diff_engine = ScheduleDiffEngine()
        comparison = diff_engine.compare(
            schedule_before,
            schedule_after,
            diff_plant,
            reason_hint="Pin ORD2 to M2",
        )

        assert comparison.kpi_diff is not None
        assert len(comparison.deltas) == 2
        assert len(comparison.facts) >= 2

        delta_ord2 = next(d for d in comparison.deltas if d.order_id == "ORD2")
        assert delta_ord2.movement_type == MovementType.CHANGED_MACHINE
        assert delta_ord2.before_machine_id == "M1"
        assert delta_ord2.after_machine_id == "M2"
        assert "Pin ORD2 to M2" in delta_ord2.attributed_cause

        # Fact table contains citation tags
        table = comparison.format_fact_table()
        assert "[F1]" in table

    def test_compare_identical_schedules_shows_unchanged(self, diff_plant: Plant) -> None:
        """Diffing a schedule with itself results in UNCHANGED movements and zero deltas."""
        schedule = solve(diff_plant)
        diff_engine = ScheduleDiffEngine()
        comparison = diff_engine.compare(schedule, schedule, diff_plant)

        assert comparison.kpi_diff.makespan_delta == 0
        assert comparison.kpi_diff.total_tardiness_delta == 0
        for delta in comparison.deltas:
            assert delta.movement_type == MovementType.UNCHANGED
            assert delta.delta_start == 0
            assert delta.delta_end == 0
