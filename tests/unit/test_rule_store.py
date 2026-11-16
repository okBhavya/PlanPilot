"""Unit tests for versioned RuleStore and ScheduleDiff impact evaluation."""

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
from planpilot.rules.dsl import PinToMachine
from planpilot.rules.store import RuleStore


@pytest.fixture
def store_plant() -> Plant:
    """Fixture with two machines and two orders."""
    m1 = Machine(id="M1", work_center_id="WC1")
    m2 = Machine(id="M2", work_center_id="WC1")
    wc = WorkCenter(id="WC1", machine_ids=["M1", "M2"])

    mat = Material(id="MAT_TI", family=MaterialFamily.TITANIUM)

    op1 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1", "M2"], processing_time=20)
    rtg1 = Routing(id="RTG1", product_material_id="MAT_TI", operations=[op1])

    op2 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1", "M2"], processing_time=15)
    rtg2 = Routing(id="RTG2", product_material_id="MAT_TI", operations=[op2])

    ord1 = Order(id="ORD1", routing_id="RTG1", material_id="MAT_TI", due_date=30)
    ord2 = Order(id="ORD2", routing_id="RTG2", material_id="MAT_TI", due_date=30)

    return Plant(
        id="P_STORE",
        machines=[m1, m2],
        work_centers=[wc],
        materials=[mat],
        routings=[rtg1, rtg2],
        orders=[ord1, ord2],
    )


class TestRuleStore:
    def test_add_and_version_rule(self, store_plant: Plant) -> None:
        """Adding rules validates and assigns incremental versions."""
        store = RuleStore(store_plant)

        rule1 = PinToMachine(id="R1", order_id="ORD1", operation_id="OP1", machine_id="M1")
        val_res1, stored1 = store.add_rule(rule1, author="planner_alice", comment="Pin ORD1 to M1")

        assert val_res1.valid is True
        assert stored1.id == "R1"
        assert len(store.rules) == 1
        assert len(store.history) == 1
        assert store.history[0].version == 1
        assert store.history[0].author == "planner_alice"

        rule2 = PinToMachine(id="R2", order_id="ORD2", operation_id="OP1", machine_id="M2")
        val_res2, stored2 = store.add_rule(rule2, author="planner_bob")

        assert val_res2.valid is True
        assert len(store.rules) == 2
        assert len(store.history) == 2
        assert store.history[1].version == 2

    def test_enable_disable_rule(self, store_plant: Plant) -> None:
        """Rules can be enabled and disabled."""
        store = RuleStore(store_plant)
        rule = PinToMachine(id="R1", order_id="ORD1", operation_id="OP1", machine_id="M1")
        store.add_rule(rule)

        assert store.rules[0].enabled is True

        store.disable_rule("R1")
        assert len(store.rules) == 0  # store.rules returns enabled rules only
        assert len(store.all_rules) == 1
        assert store.all_rules[0].enabled is False

        store.enable_rule("R1")
        assert len(store.rules) == 1
        assert store.rules[0].enabled is True

    def test_remove_rule(self, store_plant: Plant) -> None:
        """Removing rule deletes it from store."""
        store = RuleStore(store_plant)
        rule = PinToMachine(id="R1", order_id="ORD1", operation_id="OP1", machine_id="M1")
        store.add_rule(rule)

        assert store.remove_rule("R1") is True
        assert len(store.all_rules) == 0
        assert store.remove_rule("R1") is False

    def test_evaluate_rule_impact(self, store_plant: Plant) -> None:
        """Evaluate rule impact produces before/after schedules and KPI diff."""
        store = RuleStore(store_plant)

        candidate = PinToMachine(id="R_PIN", order_id="ORD1", operation_id="OP1", machine_id="M2")
        before, after, diff = store.evaluate_rule_impact(candidate)

        assert before.solver_stats.status.value in ("optimal", "feasible")
        assert after.solver_stats.status.value in ("optimal", "feasible")

        assert diff.kpi_diff is not None
        # Checks that movements are recorded
        as_after = next(a for a in after.assignments if a.order_id == "ORD1")
        assert as_after.machine_id == "M2"
