"""Tests for PlanPilot domain models."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from planpilot.domain.models import (
    Assignment,
    BOM,
    BOMComponent,
    ChangeoverEntry,
    ChangeoverMatrix,
    DowntimeWindow,
    KPIs,
    Machine,
    Material,
    MaterialFamily,
    Operation,
    Order,
    OrderPriority,
    Plant,
    Routing,
    Schedule,
    SolverStats,
    SolverStatus,
    TightConstraint,
    WorkCenter,
)


class TestMachine:
    def test_valid_machine(self) -> None:
        m = Machine(id="M1", name="Lathe", work_center_id="WC1")
        assert m.id == "M1"
        assert m.speed_factor == 1.0

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            Machine(id="  ", work_center_id="WC1")

    def test_whitespace_stripped(self) -> None:
        m = Machine(id="  M1  ", work_center_id="WC1")
        assert m.id == "M1"

    def test_negative_speed_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Machine(id="M1", work_center_id="WC1", speed_factor=-1.0)

    def test_zero_speed_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Machine(id="M1", work_center_id="WC1", speed_factor=0.0)


class TestDowntimeWindow:
    def test_valid_downtime(self) -> None:
        dt = DowntimeWindow(machine_id="M1", start=10, end=20, reason="Maintenance")
        assert dt.start == 10
        assert dt.end == 20

    def test_start_after_end_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be before end"):
            DowntimeWindow(machine_id="M1", start=20, end=10)

    def test_start_equals_end_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be before end"):
            DowntimeWindow(machine_id="M1", start=10, end=10)


class TestOperation:
    def test_valid_operation(self) -> None:
        op = Operation(
            id="OP10",
            sequence=10,
            eligible_machine_ids=["M1", "M2"],
            processing_time=20,
        )
        assert op.processing_time == 20
        assert len(op.eligible_machine_ids) == 2

    def test_zero_processing_time_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Operation(
                id="OP10",
                sequence=10,
                eligible_machine_ids=["M1"],
                processing_time=0,
            )

    def test_negative_processing_time_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Operation(
                id="OP10",
                sequence=10,
                eligible_machine_ids=["M1"],
                processing_time=-5,
            )

    def test_empty_eligible_machines_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Operation(
                id="OP10",
                sequence=10,
                eligible_machine_ids=[],
                processing_time=10,
            )

    def test_duplicate_machines_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate"):
            Operation(
                id="OP10",
                sequence=10,
                eligible_machine_ids=["M1", "M1"],
                processing_time=10,
            )

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            Operation(
                id="",
                sequence=10,
                eligible_machine_ids=["M1"],
                processing_time=10,
            )


class TestRouting:
    def test_valid_routing(self) -> None:
        ops = [
            Operation(id="OP10", sequence=10, eligible_machine_ids=["M1"], processing_time=10),
            Operation(id="OP20", sequence=20, eligible_machine_ids=["M2"], processing_time=15),
        ]
        r = Routing(id="RTG1", product_material_id="MAT1", operations=ops)
        assert r.operation_ids() == ["OP10", "OP20"]

    def test_unordered_sequences_rejected(self) -> None:
        ops = [
            Operation(id="OP20", sequence=20, eligible_machine_ids=["M1"], processing_time=10),
            Operation(id="OP10", sequence=10, eligible_machine_ids=["M2"], processing_time=15),
        ]
        with pytest.raises(ValidationError, match="ordered by sequence"):
            Routing(id="RTG1", product_material_id="MAT1", operations=ops)

    def test_duplicate_sequences_rejected(self) -> None:
        ops = [
            Operation(id="OP10A", sequence=10, eligible_machine_ids=["M1"], processing_time=10),
            Operation(id="OP10B", sequence=10, eligible_machine_ids=["M2"], processing_time=15),
        ]
        with pytest.raises(ValidationError, match="unique"):
            Routing(id="RTG1", product_material_id="MAT1", operations=ops)

    def test_empty_operations_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Routing(id="RTG1", product_material_id="MAT1", operations=[])


class TestOrder:
    def test_valid_order(self) -> None:
        o = Order(id="ORD1", routing_id="RTG1", due_date=100)
        assert o.priority == OrderPriority.NORMAL
        assert o.weight == 1.0
        assert o.release_time == 0

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            Order(id="", routing_id="RTG1")


class TestChangeoverMatrix:
    def test_lookup_existing_entry(self) -> None:
        matrix = ChangeoverMatrix(
            entries=[
                ChangeoverEntry(
                    machine_id="M1",
                    from_material=MaterialFamily.STEEL,
                    to_material=MaterialFamily.ALUMINIUM,
                    setup_time=15,
                ),
            ]
        )
        assert matrix.get_setup_time("M1", MaterialFamily.STEEL, MaterialFamily.ALUMINIUM) == 15

    def test_lookup_missing_returns_zero(self) -> None:
        matrix = ChangeoverMatrix(entries=[])
        assert matrix.get_setup_time("M1", MaterialFamily.STEEL, MaterialFamily.ALUMINIUM) == 0

    def test_any_machine_lookup(self) -> None:
        matrix = ChangeoverMatrix(
            entries=[
                ChangeoverEntry(
                    machine_id="M1",
                    from_material=MaterialFamily.STEEL,
                    to_material=MaterialFamily.ALUMINIUM,
                    setup_time=10,
                ),
                ChangeoverEntry(
                    machine_id="M2",
                    from_material=MaterialFamily.STEEL,
                    to_material=MaterialFamily.ALUMINIUM,
                    setup_time=20,
                ),
            ]
        )
        assert matrix.get_setup_time_any_machine(
            MaterialFamily.STEEL, MaterialFamily.ALUMINIUM
        ) == 20


class TestAssignment:
    def test_valid_assignment(self) -> None:
        a = Assignment(
            order_id="ORD1",
            operation_id="OP10",
            machine_id="M1",
            start=0,
            end=10,
            processing_time=10,
        )
        assert a.start == 0
        assert a.end == 10

    def test_start_after_end_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be before end"):
            Assignment(
                order_id="ORD1",
                operation_id="OP10",
                machine_id="M1",
                start=10,
                end=5,
                processing_time=5,
            )


class TestSchedule:
    def _make_schedule(self) -> Schedule:
        return Schedule(
            assignments=[
                Assignment(
                    order_id="ORD1", operation_id="OP10", machine_id="M1",
                    start=0, end=10, processing_time=10,
                ),
                Assignment(
                    order_id="ORD1", operation_id="OP20", machine_id="M2",
                    start=10, end=25, processing_time=15,
                ),
                Assignment(
                    order_id="ORD2", operation_id="OP10", machine_id="M1",
                    start=10, end=20, processing_time=10,
                ),
            ],
        )

    def test_assignments_for_order(self) -> None:
        s = self._make_schedule()
        ord1 = s.assignments_for_order("ORD1")
        assert len(ord1) == 2
        assert ord1[0].operation_id == "OP10"
        assert ord1[1].operation_id == "OP20"

    def test_assignments_for_machine(self) -> None:
        s = self._make_schedule()
        m1 = s.assignments_for_machine("M1")
        assert len(m1) == 2

    def test_order_completion_time(self) -> None:
        s = self._make_schedule()
        assert s.order_completion_time("ORD1") == 25
        assert s.order_completion_time("ORD2") == 20
        assert s.order_completion_time("ORD-NONEXIST") is None


class TestPlant:
    def _make_plant(self) -> Plant:
        machines = [
            Machine(id="M1", work_center_id="WC1"),
            Machine(id="M2", work_center_id="WC1"),
        ]
        materials = [
            Material(id="MAT1", family=MaterialFamily.STEEL),
            Material(id="MAT2", family=MaterialFamily.ALUMINIUM),
        ]
        operations = [
            Operation(
                id="OP10", sequence=10,
                eligible_machine_ids=["M1", "M2"],
                processing_time=10,
                material_id="MAT1",
            ),
            Operation(
                id="OP20", sequence=20,
                eligible_machine_ids=["M1"],
                processing_time=15,
                material_id="MAT1",
            ),
        ]
        routings = [
            Routing(id="RTG1", product_material_id="MAT1", operations=operations),
        ]
        orders = [
            Order(id="ORD1", routing_id="RTG1", due_date=50, material_id="MAT1"),
        ]
        work_centers = [
            WorkCenter(id="WC1", machine_ids=["M1", "M2"]),
        ]
        return Plant(
            id="P1",
            machines=machines,
            work_centers=work_centers,
            materials=materials,
            routings=routings,
            orders=orders,
        )

    def test_referential_integrity_valid(self) -> None:
        plant = self._make_plant()
        errors = plant.validate_referential_integrity()
        assert errors == [], f"Unexpected errors: {errors}"

    def test_referential_integrity_bad_machine_ref(self) -> None:
        plant = Plant(
            id="P1",
            machines=[Machine(id="M1", work_center_id="WC1")],
            materials=[Material(id="MAT1")],
            routings=[
                Routing(
                    id="RTG1",
                    product_material_id="MAT1",
                    operations=[
                        Operation(
                            id="OP10",
                            sequence=10,
                            eligible_machine_ids=["M99"],  # doesn't exist
                            processing_time=10,
                        ),
                    ],
                ),
            ],
        )
        errors = plant.validate_referential_integrity()
        assert any("unknown machine M99" in e for e in errors)

    def test_referential_integrity_bad_routing_ref(self) -> None:
        plant = Plant(
            id="P1",
            machines=[Machine(id="M1", work_center_id="WC1")],
            materials=[Material(id="MAT1")],
            routings=[
                Routing(
                    id="RTG1",
                    product_material_id="MAT1",
                    operations=[
                        Operation(
                            id="OP10",
                            sequence=10,
                            eligible_machine_ids=["M1"],
                            processing_time=10,
                        ),
                    ],
                ),
            ],
            orders=[
                Order(id="ORD1", routing_id="RTG-NOPE"),  # doesn't exist
            ],
        )
        errors = plant.validate_referential_integrity()
        assert any("unknown routing" in e for e in errors)

    def test_lookup_helpers(self) -> None:
        plant = self._make_plant()
        assert plant.machine_by_id("M1") is not None
        assert plant.machine_by_id("M99") is None
        assert plant.routing_by_id("RTG1") is not None
        assert plant.order_by_id("ORD1") is not None
        assert plant.material_by_id("MAT1") is not None

    def test_machine_ids(self) -> None:
        plant = self._make_plant()
        assert set(plant.machine_ids()) == {"M1", "M2"}


class TestRoundTripSerialization:
    """Test that all domain models survive JSON round-trip."""

    def test_machine_roundtrip(self) -> None:
        m = Machine(id="M1", name="Lathe", work_center_id="WC1", speed_factor=1.1)
        data = json.loads(m.model_dump_json())
        m2 = Machine.model_validate(data)
        assert m == m2

    def test_plant_roundtrip(self) -> None:
        plant = Plant(
            id="P1",
            machines=[Machine(id="M1", work_center_id="WC1")],
            materials=[Material(id="MAT1", family=MaterialFamily.STEEL)],
            routings=[
                Routing(
                    id="RTG1",
                    product_material_id="MAT1",
                    operations=[
                        Operation(
                            id="OP10",
                            sequence=10,
                            eligible_machine_ids=["M1"],
                            processing_time=10,
                        ),
                    ],
                ),
            ],
            orders=[Order(id="ORD1", routing_id="RTG1", due_date=100)],
        )
        data = json.loads(plant.model_dump_json())
        plant2 = Plant.model_validate(data)
        assert plant.id == plant2.id
        assert len(plant2.machines) == 1
        assert len(plant2.routings) == 1
        assert len(plant2.orders) == 1

    def test_schedule_roundtrip(self) -> None:
        s = Schedule(
            id="SCH1",
            assignments=[
                Assignment(
                    order_id="ORD1",
                    operation_id="OP10",
                    machine_id="M1",
                    start=0,
                    end=10,
                    processing_time=10,
                    tight_constraints=[TightConstraint.RELEASE_TIME],
                ),
            ],
            kpis=KPIs(makespan=10, on_time_pct=100.0),
            solver_stats=SolverStats(status=SolverStatus.OPTIMAL, objective_value=10),
        )
        data = json.loads(s.model_dump_json())
        s2 = Schedule.model_validate(data)
        assert s2.id == "SCH1"
        assert s2.kpis.makespan == 10
        assert s2.solver_stats.status == SolverStatus.OPTIMAL
        assert s2.assignments[0].tight_constraints == [TightConstraint.RELEASE_TIME]

    def test_changeover_matrix_roundtrip(self) -> None:
        cm = ChangeoverMatrix(
            entries=[
                ChangeoverEntry(
                    machine_id="M1",
                    from_material=MaterialFamily.STEEL,
                    to_material=MaterialFamily.ALUMINIUM,
                    setup_time=15,
                ),
            ]
        )
        data = json.loads(cm.model_dump_json())
        cm2 = ChangeoverMatrix.model_validate(data)
        assert cm2.get_setup_time("M1", MaterialFamily.STEEL, MaterialFamily.ALUMINIUM) == 15
