"""Unit tests for the 5-stage deterministic RuleValidator pipeline."""

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
from planpilot.rules.dsl import (
    ForbidMachine,
    HardDueDate,
    MachineUnavailable,
    OrderPrecedence,
    PinToMachine,
)
from planpilot.rules.validator import RuleValidator, ValidationStage


@pytest.fixture
def validator_plant() -> Plant:
    """Fixture with two machines and two orders with known processing times."""
    m1 = Machine(id="M1", work_center_id="WC1")
    m2 = Machine(id="M2", work_center_id="WC1")
    wc = WorkCenter(id="WC1", machine_ids=["M1", "M2"])

    mat = Material(id="MAT_TI", family=MaterialFamily.TITANIUM)

    op1 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1", "M2"], processing_time=20)
    rtg1 = Routing(id="RTG1", product_material_id="MAT_TI", operations=[op1])

    op2 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1", "M2"], processing_time=15)
    rtg2 = Routing(id="RTG2", product_material_id="MAT_TI", operations=[op2])

    ord1 = Order(id="ORD1", routing_id="RTG1", material_id="MAT_TI", release_time=0)
    ord2 = Order(id="ORD2", routing_id="RTG2", material_id="MAT_TI", release_time=10)

    return Plant(
        id="P_VAL",
        machines=[m1, m2],
        work_centers=[wc],
        materials=[mat],
        routings=[rtg1, rtg2],
        orders=[ord1, ord2],
        horizon=1000,
    )


class TestRuleValidatorStages:
    # -------------------------------------------------------------------------
    # Stage 1: Schema Validation
    # -------------------------------------------------------------------------
    def test_stage1_schema_pass(self, validator_plant: Plant) -> None:
        """Valid schema dict parses cleanly."""
        validator = RuleValidator(validator_plant)
        candidate = {
            "type": "pin_to_machine",
            "order_id": "ORD1",
            "operation_id": "OP1",
            "machine_id": "M1",
        }
        res = validator.validate(candidate, check_feasibility=False)
        assert res.valid is True
        assert res.stage is None
        assert res.error is None
        assert res.resolved_rule is not None
        assert isinstance(res.resolved_rule, PinToMachine)

    def test_stage1_schema_fail(self, validator_plant: Plant) -> None:
        """Missing required fields or invalid rule type fails at schema stage."""
        validator = RuleValidator(validator_plant)
        # Missing machine_id and operation_id
        candidate = {
            "type": "pin_to_machine",
            "order_id": "ORD1",
        }
        res = validator.validate(candidate, check_feasibility=False)
        assert res.valid is False
        assert res.stage == ValidationStage.SCHEMA
        assert "Schema validation failed" in (res.error or "")

    # -------------------------------------------------------------------------
    # Stage 2: Entity Existence Resolution
    # -------------------------------------------------------------------------
    def test_stage2_entity_pass(self, validator_plant: Plant) -> None:
        """Valid existing entities pass entity validation."""
        validator = RuleValidator(validator_plant)
        rule = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")
        res = validator.validate(rule, check_feasibility=False)
        assert res.valid is True
        assert res.stage is None

    def test_stage2_entity_fail(self, validator_plant: Plant) -> None:
        """Referencing non-existent machine or order fails at entity stage."""
        validator = RuleValidator(validator_plant)
        rule = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="NON_EXISTENT_M999")
        res = validator.validate(rule, check_feasibility=False)
        assert res.valid is False
        assert res.stage == ValidationStage.ENTITY
        assert "Machine 'NON_EXISTENT_M999' does not exist" in (res.error or "")

    def test_stage2_entity_resolution_helper(self, validator_plant: Plant) -> None:
        """Entity resolution helper resolves fuzzy casing and names."""
        validator = RuleValidator(validator_plant)
        assert validator.resolve_entity("m1", "machine") == "M1"
        assert validator.resolve_entity("ord1", "order") == "ORD1"
        assert validator.resolve_entity("titanium", "material") == "titanium"

    # -------------------------------------------------------------------------
    # Stage 3: Semantic Checks
    # -------------------------------------------------------------------------
    def test_stage3_semantic_pass(self, validator_plant: Plant) -> None:
        """Valid semantic boundaries pass semantic check."""
        validator = RuleValidator(validator_plant)
        rule = HardDueDate(order_id="ORD1", deadline=100)
        res = validator.validate(rule, check_feasibility=False)
        assert res.valid is True
        assert res.stage is None

    def test_stage3_semantic_fail(self, validator_plant: Plant) -> None:
        """Semantic invariants violated (e.g. deadline <= release_time or window > horizon)."""
        validator = RuleValidator(validator_plant)
        # ORD2 has release_time=10, deadline=5 violates release_time constraint
        rule = HardDueDate(order_id="ORD2", deadline=5)
        res = validator.validate(rule, check_feasibility=False)
        assert res.valid is False
        assert res.stage == ValidationStage.SEMANTIC
        assert "release time" in (res.error or "")

        # Machine unavailable beyond horizon
        dt_rule = MachineUnavailable(machine_id="M1", start=100, end=999999)
        dt_res = validator.validate(dt_rule, check_feasibility=False)
        assert dt_res.valid is False
        assert dt_res.stage == ValidationStage.SEMANTIC
        assert "exceeds plant horizon" in (dt_res.error or "")

    # -------------------------------------------------------------------------
    # Stage 4: Conflict Detection Against Active Rules
    # -------------------------------------------------------------------------
    def test_stage4_conflict_pass(self, validator_plant: Plant) -> None:
        """Non-conflicting rules pass conflict stage."""
        validator = RuleValidator(validator_plant)
        active = [PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")]
        candidate = PinToMachine(order_id="ORD2", operation_id="OP1", machine_id="M2")
        res = validator.validate(candidate, active_rules=active, check_feasibility=False)
        assert res.valid is True
        assert res.stage is None

    def test_stage4_conflict_fail_contradictory_pin(self, validator_plant: Plant) -> None:
        """Pinning same op to two different machines triggers conflict error."""
        validator = RuleValidator(validator_plant)
        active = [PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")]
        candidate = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M2")
        res = validator.validate(candidate, active_rules=active, check_feasibility=False)
        assert res.valid is False
        assert res.stage == ValidationStage.CONFLICT
        assert "already pinned" in (res.error or "")

    def test_stage4_conflict_fail_precedence_cycle(self, validator_plant: Plant) -> None:
        """Circular order precedence (A -> B and B -> A) triggers conflict error."""
        validator = RuleValidator(validator_plant)
        active = [OrderPrecedence(before_order_id="ORD1", after_order_id="ORD2")]
        candidate = OrderPrecedence(before_order_id="ORD2", after_order_id="ORD1")
        res = validator.validate(candidate, active_rules=active, check_feasibility=False)
        assert res.valid is False
        assert res.stage == ValidationStage.CONFLICT
        assert "cycle detected" in (res.error or "")

    def test_stage4_conflict_fail_pin_vs_forbid(self, validator_plant: Plant) -> None:
        """Pinning an op to a machine that active rules forbid triggers conflict error."""
        validator = RuleValidator(validator_plant)
        active = [ForbidMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")]
        candidate = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")
        res = validator.validate(candidate, active_rules=active, check_feasibility=False)
        assert res.valid is False
        assert res.stage == ValidationStage.CONFLICT
        assert "is forbidden on machine" in (res.error or "")

    # -------------------------------------------------------------------------
    # Stage 5: Feasibility Check (Re-solve)
    # -------------------------------------------------------------------------
    def test_stage5_feasibility_pass(self, validator_plant: Plant) -> None:
        """Mathematically feasible rule passes full 5-stage validation."""
        validator = RuleValidator(validator_plant)
        rule = HardDueDate(order_id="ORD1", deadline=50)
        res = validator.validate(rule, check_feasibility=True)
        assert res.valid is True
        assert res.stage is None
        assert res.error is None

    def test_stage5_feasibility_fail_with_core(self, validator_plant: Plant) -> None:
        """Mathematically infeasible rule fails at stage 5 and populates core."""
        validator = RuleValidator(validator_plant)
        # ORD1 processing time is 20, deadline is 15 -> infeasible
        rule = HardDueDate(order_id="ORD1", deadline=15)
        res = validator.validate(rule, check_feasibility=True)
        assert res.valid is False
        assert res.stage == ValidationStage.FEASIBILITY
        assert "mathematically infeasible" in (res.error or "")
        assert res.infeasibility_core is not None
