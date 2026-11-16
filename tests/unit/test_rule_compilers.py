"""Unit tests verifying all 9 Rule DSL constraint compilers against CP-SAT solver."""

from __future__ import annotations

import pytest

from planpilot.domain.models import (
    Machine,
    Material,
    MaterialFamily,
    ObjectiveType,
    Operation,
    Order,
    Plant,
    Routing,
    SolverStatus,
    WorkCenter,
)
from planpilot.rules.dsl import (
    BatchTogether,
    ForbidMachine,
    ForbidSequence,
    HardDueDate,
    MachineUnavailable,
    MaxConsecutiveRuns,
    MinChangeover,
    OrderPrecedence,
    PinToMachine,
)
from planpilot.solver.scheduler import solve


@pytest.fixture
def base_two_order_plant() -> Plant:
    """Fixture with two orders, two materials, single work center, two machines."""
    m1 = Machine(id="M1", work_center_id="WC1")
    m2 = Machine(id="M2", work_center_id="WC1")
    wc = WorkCenter(id="WC1", machine_ids=["M1", "M2"])

    mat_ti = Material(id="MAT_TI", name="Titanium Alloy", family=MaterialFamily.TITANIUM)
    mat_al = Material(id="MAT_AL", name="Aluminium 6061", family=MaterialFamily.ALUMINIUM)
    mat_st = Material(id="MAT_ST", name="Stainless Steel", family=MaterialFamily.STEEL)

    # Routing for Order 1 (Titanium)
    op1 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1", "M2"], processing_time=20)
    rtg1 = Routing(id="RTG1", product_material_id="MAT_TI", operations=[op1])

    # Routing for Order 2 (Aluminium)
    op2 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1", "M2"], processing_time=20)
    rtg2 = Routing(id="RTG2", product_material_id="MAT_AL", operations=[op2])

    # Routing for Order 3 (Steel)
    op3 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1", "M2"], processing_time=20)
    rtg3 = Routing(id="RTG3", product_material_id="MAT_ST", operations=[op3])

    ord1 = Order(id="ORD1", routing_id="RTG1", material_id="MAT_TI", due_date=100)
    ord2 = Order(id="ORD2", routing_id="RTG2", material_id="MAT_AL", due_date=100)
    ord3 = Order(id="ORD3", routing_id="RTG3", material_id="MAT_ST", due_date=100)

    return Plant(
        id="PLANT_TEST",
        name="Rule Test Plant",
        machines=[m1, m2],
        work_centers=[wc],
        materials=[mat_ti, mat_al, mat_st],
        routings=[rtg1, rtg2, rtg3],
        orders=[ord1, ord2, ord3],
    )


class TestRuleCompilers:
    def test_forbid_sequence_without_cleanout(self, base_two_order_plant: Plant) -> None:
        """ForbidSequence with cleanout=0 strictly forbids adjacent Titanium -> Aluminium."""
        # Pin both ORD1 (Titanium) and ORD2 (Aluminium) to M1
        pin1 = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")
        pin2 = PinToMachine(order_id="ORD2", operation_id="OP1", machine_id="M1")

        # Forbid Aluminium immediately after Titanium on M1
        forbid_rule = ForbidSequence(
            machine_id="M1",
            from_material=MaterialFamily.TITANIUM,
            to_material=MaterialFamily.ALUMINIUM,
            unless_cleanout_min=0,
        )

        schedule = solve(
            base_two_order_plant,
            rules=[pin1, pin2, forbid_rule],
            objective=ObjectiveType.MAKESPAN,
        )

        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        # On M1, since there's no third job on M1, Aluminium (ORD2) MUST precede Titanium (ORD1)
        as_ord1 = next(a for a in schedule.assignments if a.order_id == "ORD1")
        as_ord2 = next(a for a in schedule.assignments if a.order_id == "ORD2")

        assert as_ord2.machine_id == "M1"
        assert as_ord1.machine_id == "M1"
        # ORD2 (Alu) must finish before ORD1 (Titanium) starts
        assert as_ord2.end <= as_ord1.start

    def test_forbid_sequence_with_cleanout(self, base_two_order_plant: Plant) -> None:
        """ForbidSequence with cleanout gap enforces minimum cleanout if adjacent."""
        # Force ORD1 (Titanium) and ORD2 (Aluminium) to M1, and ORD3 (Steel) to M2
        pin1 = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")
        pin2 = PinToMachine(order_id="ORD2", operation_id="OP1", machine_id="M1")
        pin3 = PinToMachine(order_id="ORD3", operation_id="OP1", machine_id="M2")
        prec = OrderPrecedence(before_order_id="ORD1", after_order_id="ORD2", min_gap_min=0)

        cleanout_gap = 35
        forbid_rule = ForbidSequence(
            machine_id="M1",
            from_material=MaterialFamily.TITANIUM,
            to_material=MaterialFamily.ALUMINIUM,
            unless_cleanout_min=cleanout_gap,
        )

        schedule = solve(
            base_two_order_plant,
            rules=[pin1, pin2, pin3, prec, forbid_rule],
            objective=ObjectiveType.MAKESPAN,
        )

        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        as_ord1 = next(a for a in schedule.assignments if a.order_id == "ORD1")
        as_ord2 = next(a for a in schedule.assignments if a.order_id == "ORD2")

        # ORD2 starts at or after ORD1 end + cleanout_gap
        assert as_ord2.start >= as_ord1.end + cleanout_gap

    def test_min_changeover(self, base_two_order_plant: Plant) -> None:
        """MinChangeover enforces minimum setup gap when switching materials on machine."""
        pin1 = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")
        pin2 = PinToMachine(order_id="ORD2", operation_id="OP1", machine_id="M1")
        min_setup = 25
        rule = MinChangeover(machine_id="M1", min_setup_min=min_setup)

        schedule = solve(
            base_two_order_plant,
            rules=[pin1, pin2, rule],
            objective=ObjectiveType.MAKESPAN,
        )

        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        as1 = next(a for a in schedule.assignments if a.order_id == "ORD1")
        as2 = next(a for a in schedule.assignments if a.order_id == "ORD2")

        if as1.end <= as2.start:
            assert as2.start >= as1.end + min_setup
        else:
            assert as1.start >= as2.end + min_setup

    def test_machine_unavailable(self, base_two_order_plant: Plant) -> None:
        """MachineUnavailable creates a strict downtime blackout window."""
        # Force ORD1 to M1
        pin1 = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")
        # Downtime on M1 from 10 to 40
        downtime = MachineUnavailable(
            machine_id="M1", start=10, end=40, reason="Spindle maintenance"
        )

        schedule = solve(
            base_two_order_plant,
            rules=[pin1, downtime],
            objective=ObjectiveType.MAKESPAN,
        )

        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        as1 = next(a for a in schedule.assignments if a.order_id == "ORD1")
        # Assignment on M1 cannot overlap with [10, 40)
        assert as1.end <= 10 or as1.start >= 40

    def test_pin_to_machine(self, base_two_order_plant: Plant) -> None:
        """PinToMachine strictly binds an operation to the requested machine."""
        pin = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M2")

        schedule = solve(
            base_two_order_plant,
            rules=[pin],
            objective=ObjectiveType.MAKESPAN,
        )

        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        as1 = next(a for a in schedule.assignments if a.order_id == "ORD1")
        assert as1.machine_id == "M2"

    def test_forbid_machine(self, base_two_order_plant: Plant) -> None:
        """ForbidMachine ensures an operation is never assigned to that machine."""
        forbid = ForbidMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")

        schedule = solve(
            base_two_order_plant,
            rules=[forbid],
            objective=ObjectiveType.MAKESPAN,
        )

        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        as1 = next(a for a in schedule.assignments if a.order_id == "ORD1")
        assert as1.machine_id != "M1"
        assert as1.machine_id == "M2"

    def test_order_precedence(self, base_two_order_plant: Plant) -> None:
        """OrderPrecedence forces completion of before_order prior to start of after_order."""
        min_gap = 15
        prec = OrderPrecedence(before_order_id="ORD2", after_order_id="ORD1", min_gap_min=min_gap)

        schedule = solve(
            base_two_order_plant,
            rules=[prec],
            objective=ObjectiveType.MAKESPAN,
        )

        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        as1 = next(a for a in schedule.assignments if a.order_id == "ORD1")
        as2 = next(a for a in schedule.assignments if a.order_id == "ORD2")

        assert as1.start >= as2.end + min_gap

    def test_max_consecutive_runs(self) -> None:
        """MaxConsecutiveRuns prevents more than max_runs consecutive operations of a family."""
        m1 = Machine(id="M1", work_center_id="WC1")
        wc = WorkCenter(id="WC1", machine_ids=["M1"])
        mat_ti = Material(id="MAT_TI", family=MaterialFamily.TITANIUM)
        mat_al = Material(id="MAT_AL", family=MaterialFamily.ALUMINIUM)

        op1 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1"], processing_time=10)
        rtg_ti = Routing(id="RTG_TI", product_material_id="MAT_TI", operations=[op1])
        rtg_al = Routing(id="RTG_AL", product_material_id="MAT_AL", operations=[op1])

        ord_t1 = Order(id="ORD_T1", routing_id="RTG_TI", material_id="MAT_TI")
        ord_t2 = Order(id="ORD_T2", routing_id="RTG_TI", material_id="MAT_TI")
        ord_a1 = Order(id="ORD_A1", routing_id="RTG_AL", material_id="MAT_AL")

        plant = Plant(
            id="P_RUNS",
            machines=[m1],
            work_centers=[wc],
            materials=[mat_ti, mat_al],
            routings=[rtg_ti, rtg_al],
            orders=[ord_t1, ord_t2, ord_a1],
        )

        rule = MaxConsecutiveRuns(
            machine_id="M1",
            material_family=MaterialFamily.TITANIUM,
            max_runs=1,
        )

        schedule = solve(plant, rules=[rule])
        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        # Sort assignments by start time on M1
        ordered = sorted(schedule.assignments, key=lambda a: a.start)
        # Sequence must have A1 in between T1 and T2 (e.g. T1 -> A1 -> T2 or T2 -> A1 -> T1)
        assert ordered[1].order_id == "ORD_A1"

    def test_hard_due_date(self, base_two_order_plant: Plant) -> None:
        """HardDueDate enforces strict order completion boundary."""
        deadline = 35
        rule = HardDueDate(order_id="ORD1", deadline=deadline)

        schedule = solve(
            base_two_order_plant,
            rules=[rule],
            objective=ObjectiveType.MAKESPAN,
        )

        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        as1 = next(a for a in schedule.assignments if a.order_id == "ORD1")
        assert as1.end <= deadline

    def test_batch_together(self, base_two_order_plant: Plant) -> None:
        """BatchTogether clusters specified orders contiguously without outside jobs intervening."""
        # Pin ORD1, ORD2, ORD3 all to M1
        pin1 = PinToMachine(order_id="ORD1", operation_id="OP1", machine_id="M1")
        pin2 = PinToMachine(order_id="ORD2", operation_id="OP1", machine_id="M1")
        pin3 = PinToMachine(order_id="ORD3", operation_id="OP1", machine_id="M1")

        # Batch ORD1 and ORD3 together
        batch = BatchTogether(order_ids=["ORD1", "ORD3"], machine_id="M1")

        schedule = solve(
            base_two_order_plant,
            rules=[pin1, pin2, pin3, batch],
            objective=ObjectiveType.MAKESPAN,
        )

        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        as1 = next(a for a in schedule.assignments if a.order_id == "ORD1")
        as2 = next(a for a in schedule.assignments if a.order_id == "ORD2")
        as3 = next(a for a in schedule.assignments if a.order_id == "ORD3")

        min_batch_st = min(as1.start, as3.start)
        max_batch_en = max(as1.end, as3.end)

        # ORD2 must either finish before min_batch_st or start after max_batch_en
        assert as2.end <= min_batch_st or as2.start >= max_batch_en
