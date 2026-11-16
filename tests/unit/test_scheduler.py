"""Unit tests for CP-SAT flexible job-shop scheduler and infeasibility diagnosis."""

from __future__ import annotations

from planpilot.domain.generator import PlantGenerator
from planpilot.domain.models import (
    ChangeoverEntry,
    ChangeoverMatrix,
    DowntimeWindow,
    Machine,
    Material,
    MaterialFamily,
    ObjectiveType,
    Operation,
    Order,
    OrderPriority,
    Plant,
    Routing,
    SolverStatus,
    TightConstraint,
    WorkCenter,
)
from planpilot.solver.benchmarks import KNOWN_OPTIMA, load_benchmark
from planpilot.solver.infeasibility import diagnose_infeasibility
from planpilot.solver.scheduler import solve


class TestOrLibraryBenchmarks:
    def test_ft06_optimal_makespan(self) -> None:
        """ft06 must solve to the known optimal makespan of exactly 55."""
        plant = load_benchmark("ft06")
        schedule = solve(plant, objective=ObjectiveType.MAKESPAN, time_limit_seconds=15.0)

        assert schedule.solver_stats.status == SolverStatus.OPTIMAL
        assert schedule.kpis.makespan == KNOWN_OPTIMA["ft06"]
        assert schedule.kpis.makespan == 55
        assert len(schedule.assignments) == 36  # 6 jobs x 6 ops

    def test_ft10_optimal_or_bounded(self) -> None:
        """ft10 reaches 930 or reports the optimality gap within time limit."""
        plant = load_benchmark("ft10")
        schedule = solve(plant, objective=ObjectiveType.MAKESPAN, time_limit_seconds=20.0)

        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        assert schedule.kpis.makespan <= KNOWN_OPTIMA["ft10"]
        if schedule.solver_stats.status == SolverStatus.OPTIMAL:
            assert schedule.kpis.makespan == 930
        else:
            assert schedule.solver_stats.gap_pct is not None
        assert len(schedule.assignments) == 100  # 10 jobs x 10 ops


class TestSchedulerFeatures:
    def test_multi_machine_alternatives(self) -> None:
        """Operations with multiple eligible machines pick exactly one."""
        m1 = Machine(id="M1", work_center_id="WC1")
        m2 = Machine(id="M2", work_center_id="WC1")
        mat = Material(id="MAT1")
        op1 = Operation(
            id="OP10", sequence=10, eligible_machine_ids=["M1", "M2"], processing_time=15
        )
        op2 = Operation(
            id="OP20", sequence=20, eligible_machine_ids=["M1", "M2"], processing_time=10
        )
        routing = Routing(id="RTG1", product_material_id="MAT1", operations=[op1, op2])
        order = Order(id="ORD1", routing_id="RTG1")
        plant = Plant(
            id="P_ALT",
            machines=[m1, m2],
            work_centers=[WorkCenter(id="WC1", machine_ids=["M1", "M2"])],
            materials=[mat],
            routings=[routing],
            orders=[order],
            horizon=200,
        )

        schedule = solve(plant)
        assert schedule.solver_stats.status == SolverStatus.OPTIMAL
        assert len(schedule.assignments) == 2

        # Precedence must hold
        a1 = [a for a in schedule.assignments if a.operation_id == "OP10"][0]
        a2 = [a for a in schedule.assignments if a.operation_id == "OP20"][0]
        assert a2.start >= a1.end
        assert a1.machine_id in ("M1", "M2")
        assert a2.machine_id in ("M1", "M2")

    def test_release_time_respected(self) -> None:
        """Order first operation does not start before release time."""
        m = Machine(id="M1", work_center_id="WC1")
        mat = Material(id="MAT1")
        op = Operation(id="OP10", sequence=10, eligible_machine_ids=["M1"], processing_time=10)
        routing = Routing(id="RTG1", product_material_id="MAT1", operations=[op])
        order = Order(id="ORD1", routing_id="RTG1", release_time=25)
        plant = Plant(
            id="P_REL",
            machines=[m],
            work_centers=[WorkCenter(id="WC1", machine_ids=["M1"])],
            materials=[mat],
            routings=[routing],
            orders=[order],
            horizon=100,
        )

        schedule = solve(plant)
        assert schedule.solver_stats.status == SolverStatus.OPTIMAL
        assert schedule.assignments[0].start == 25
        assert TightConstraint.RELEASE_TIME in schedule.assignments[0].tight_constraints

    def test_downtime_window_respected(self) -> None:
        """Operations cannot execute during planned downtime window."""
        m = Machine(id="M1", work_center_id="WC1")
        mat = Material(id="MAT1")
        op1 = Operation(id="OP10", sequence=10, eligible_machine_ids=["M1"], processing_time=20)
        op2 = Operation(id="OP20", sequence=20, eligible_machine_ids=["M1"], processing_time=20)
        r1 = Routing(id="RTG1", product_material_id="MAT1", operations=[op1])
        r2 = Routing(id="RTG2", product_material_id="MAT1", operations=[op2])
        o1 = Order(id="ORD1", routing_id="RTG1")
        o2 = Order(id="ORD2", routing_id="RTG2")
        # Downtime from 15 to 35 on M1
        dt = DowntimeWindow(machine_id="M1", start=15, end=35, reason="Inspection")
        plant = Plant(
            id="P_DT",
            machines=[m],
            work_centers=[WorkCenter(id="WC1", machine_ids=["M1"])],
            materials=[mat],
            routings=[r1, r2],
            orders=[o1, o2],
            downtimes=[dt],
            horizon=200,
        )

        schedule = solve(plant)
        assert schedule.solver_stats.status == SolverStatus.OPTIMAL
        for a in schedule.assignments:
            # Operation must either end before downtime starts or start after downtime ends
            assert a.end <= dt.start or a.start >= dt.end

    def test_sequence_dependent_setup_times(self) -> None:
        """Transitions between materials incur setup time."""
        m = Machine(id="M1", work_center_id="WC1")
        mat_steel = Material(id="MAT_STEEL", family=MaterialFamily.STEEL)
        mat_al = Material(id="MAT_AL", family=MaterialFamily.ALUMINIUM)
        op_steel = Operation(
            id="OP_S",
            sequence=10,
            eligible_machine_ids=["M1"],
            processing_time=10,
            material_id="MAT_STEEL",
        )
        op_al = Operation(
            id="OP_A",
            sequence=10,
            eligible_machine_ids=["M1"],
            processing_time=10,
            material_id="MAT_AL",
        )
        r1 = Routing(id="RTG1", product_material_id="MAT_STEEL", operations=[op_steel])
        r2 = Routing(id="RTG2", product_material_id="MAT_AL", operations=[op_al])
        o1 = Order(id="ORD1", routing_id="RTG1", material_id="MAT_STEEL")
        o2 = Order(id="ORD2", routing_id="RTG2", material_id="MAT_AL")

        # Setup between steel and aluminium is 12 time units
        cm = ChangeoverMatrix(
            entries=[
                ChangeoverEntry(
                    machine_id="M1",
                    from_material=MaterialFamily.STEEL,
                    to_material=MaterialFamily.ALUMINIUM,
                    setup_time=12,
                ),
                ChangeoverEntry(
                    machine_id="M1",
                    from_material=MaterialFamily.ALUMINIUM,
                    to_material=MaterialFamily.STEEL,
                    setup_time=12,
                ),
            ]
        )

        plant = Plant(
            id="P_SETUP",
            machines=[m],
            work_centers=[WorkCenter(id="WC1", machine_ids=["M1"])],
            materials=[mat_steel, mat_al],
            routings=[r1, r2],
            orders=[o1, o2],
            changeover_matrix=cm,
            horizon=100,
        )

        schedule = solve(plant)
        assert schedule.solver_stats.status == SolverStatus.OPTIMAL
        assert len(schedule.assignments) == 2
        a_first, a_second = schedule.assignments[0], schedule.assignments[1]
        assert a_second.start >= a_first.end + 12
        assert a_second.setup_time == 12
        assert a_second.setup_start == a_second.start - 12
        assert TightConstraint.MACHINE_PREDECESSOR_SETUP in a_second.tight_constraints
        assert schedule.kpis.total_setup_time == 12

    def test_weighted_tardiness_objective(self) -> None:
        """Weighted tardiness minimizes penalty over due dates."""
        m = Machine(id="M1", work_center_id="WC1")
        mat = Material(id="MAT1")
        op1 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1"], processing_time=20)
        op2 = Operation(id="OP2", sequence=10, eligible_machine_ids=["M1"], processing_time=20)
        r1 = Routing(id="RTG1", product_material_id="MAT1", operations=[op1])
        r2 = Routing(id="RTG2", product_material_id="MAT1", operations=[op2])
        # ORD1 has due date 25, high weight 10. ORD2 has due date 25, low weight 1.
        # ORD1 should be scheduled first to avoid high tardiness penalty!
        o1 = Order(
            id="ORD1",
            routing_id="RTG1",
            due_date=25,
            weight=10.0,
            priority=OrderPriority.CRITICAL,
        )
        o2 = Order(
            id="ORD2",
            routing_id="RTG2",
            due_date=25,
            weight=1.0,
            priority=OrderPriority.LOW,
        )
        plant = Plant(
            id="P_TARD",
            machines=[m],
            work_centers=[WorkCenter(id="WC1", machine_ids=["M1"])],
            materials=[mat],
            routings=[r1, r2],
            orders=[o1, o2],
            horizon=100,
        )

        schedule = solve(plant, objective=ObjectiveType.TOTAL_WEIGHTED_TARDINESS)
        assert schedule.solver_stats.status == SolverStatus.OPTIMAL
        # ORD1 finishes first at 20 (on time, 0 tardiness)
        ord1_a = [a for a in schedule.assignments if a.order_id == "ORD1"][0]
        ord2_a = [a for a in schedule.assignments if a.order_id == "ORD2"][0]
        assert ord1_a.end <= 25
        assert ord2_a.start >= ord1_a.end

    def test_lexicographic_objective(self) -> None:
        """Lexicographic objective completes successfully."""
        plant = PlantGenerator(num_orders=5, num_machines=3, seed=123).generate()
        schedule = solve(plant, objective=ObjectiveType.LEXICOGRAPHIC, time_limit_seconds=10.0)
        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
        assert schedule.kpis.makespan > 0


class TestInfeasibilityDiagnosis:
    def test_infeasible_tight_due_date_diagnosed(self) -> None:
        """When an order has an impossible due date, diagnosis isolates the core."""
        m = Machine(id="M1", work_center_id="WC1")
        mat = Material(id="MAT1")
        # 30 units of processing, but due date is 10 units!
        op = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1"], processing_time=30)
        r = Routing(id="RTG1", product_material_id="MAT1", operations=[op])
        o = Order(id="ORD1", routing_id="RTG1", due_date=10)
        plant = Plant(
            id="P_INFEAS_DUE",
            machines=[m],
            work_centers=[WorkCenter(id="WC1", machine_ids=["M1"])],
            materials=[mat],
            routings=[r],
            orders=[o],
            horizon=100,
        )

        report = diagnose_infeasibility(plant, enforce_due_dates_as_hard=True)
        assert report.is_infeasible is True
        assert len(report.core_constraints) > 0
        assert any("ORD1" in c and "due date" in c for c in report.core_constraints)
        assert "ORD1" in report.explanation

    def test_infeasible_downtime_conflict_diagnosed(self) -> None:
        """When downtime covers the entire available window, diagnosis finds conflict."""
        m = Machine(id="M1", work_center_id="WC1")
        mat = Material(id="MAT1")
        op = Operation(id="OP1", sequence=10, eligible_machine_ids=["M1"], processing_time=20)
        r = Routing(id="RTG1", product_material_id="MAT1", operations=[op])
        o = Order(id="ORD1", routing_id="RTG1", release_time=0, due_date=25)
        # Downtime blocks [0, 20]
        dt = DowntimeWindow(machine_id="M1", start=0, end=20, reason="Motor Overhaul")
        plant = Plant(
            id="P_INFEAS_DT",
            machines=[m],
            work_centers=[WorkCenter(id="WC1", machine_ids=["M1"])],
            materials=[mat],
            routings=[r],
            orders=[o],
            downtimes=[dt],
            horizon=100,
        )

        report = diagnose_infeasibility(plant, enforce_due_dates_as_hard=True)
        assert report.is_infeasible is True
        assert any("Motor Overhaul" in c or "M1" in c for c in report.core_constraints)

    def test_feasible_instance_returns_not_infeasible(self) -> None:
        """A feasible instance reports is_infeasible=False."""
        plant = load_benchmark("ft06")
        report = diagnose_infeasibility(plant)
        assert report.is_infeasible is False
        assert len(report.core_constraints) == 0
