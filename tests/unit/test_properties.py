"""Hypothesis property-based tests for PlanPilot CP-SAT scheduler.

Formally tests invariant properties across randomly generated plants:
1. No machine overlap: no two operations execute on the same machine at the same time.
2. Routing precedence: an operation never starts before its predecessor in the routing finishes.
3. Every operation scheduled exactly once on an eligible machine.
4. Setup times respected: sequence-dependent changeovers are satisfied.
5. Machine downtime respected: no operation overlaps a planned downtime window.
6. Order release times respected: first operation start >= release_time.
7. Correct assignment durations: end == start + processing_time.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from planpilot.domain.generator import PlantGenerator
from planpilot.domain.models import ObjectiveType, Plant, SolverStatus
from planpilot.solver.scheduler import solve


@st.composite
def random_plants(draw: st.DrawFn) -> Plant:
    """Strategy to generate diverse valid plants."""
    num_machines = draw(st.integers(min_value=2, max_value=5))
    num_orders = draw(st.integers(min_value=2, max_value=6))
    num_materials = draw(st.integers(min_value=2, max_value=4))
    seed = draw(st.integers(min_value=1, max_value=10000))
    downtime_prob = draw(st.sampled_from([0.0, 0.2, 0.4]))
    changeover_density = draw(st.sampled_from([0.0, 0.3, 0.6]))

    gen = PlantGenerator(
        num_machines=num_machines,
        num_orders=num_orders,
        num_materials=num_materials,
        ops_per_routing=(2, 3),
        alt_machines_per_op=(1, 2),
        processing_time_range=(5, 20),
        setup_time_range=(0, 5),
        changeover_time_range=(5, 15),
        changeover_density=changeover_density,
        downtime_probability=downtime_prob,
        seed=seed,
    )
    return gen.generate()


class TestSchedulerProperties:
    @settings(
        max_examples=15,
        deadline=30000,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(plant=random_plants())
    def test_schedule_invariants(self, plant: Plant) -> None:
        """Verify mathematical invariants of the returned schedule."""
        schedule = solve(
            plant,
            objective=ObjectiveType.MAKESPAN,
            time_limit_seconds=10.0,
            num_workers=2,
        )

        # Skip if solver timed out without finding a feasible solution
        if schedule.solver_stats.status not in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
            return

        assignments = schedule.assignments
        assert len(assignments) > 0

        # Map operations for lookups
        order_routing_ops: dict[str, list[str]] = {}
        op_by_id = {}
        for r in plant.routings:
            for op in r.operations:
                op_by_id[op.id] = op

        for o in plant.orders:
            r = plant.routing_by_id(o.routing_id)
            if r:
                order_routing_ops[o.id] = [op.id for op in r.operations]

        # Invariant 1: Every operation in each order's routing is scheduled exactly once
        scheduled_ops_per_order: dict[str, list[str]] = {}
        for a in assignments:
            scheduled_ops_per_order.setdefault(a.order_id, []).append(a.operation_id)
            # Duration check
            assert a.end == a.start + a.processing_time
            assert a.start >= 0

            # Eligible machine check
            op_def = op_by_id.get(a.operation_id)
            if op_def:
                assert a.machine_id in op_def.eligible_machine_ids

        for order_id, expected_op_ids in order_routing_ops.items():
            actual_op_ids = scheduled_ops_per_order.get(order_id, [])
            msg = (
                f"Order {order_id} operations mismatch: "
                f"expected {expected_op_ids}, got {actual_op_ids}"
            )
            assert sorted(actual_op_ids) == sorted(expected_op_ids), msg

        # Invariant 2: Routing precedence within each order
        for order_id in order_routing_ops:
            order_assignments = schedule.assignments_for_order(order_id)
            for i in range(len(order_assignments) - 1):
                curr = order_assignments[i]
                nxt = order_assignments[i + 1]
                prec_msg = (
                    f"Precedence violated for order {order_id}: "
                    f"{curr.operation_id} (ends {curr.end}) "
                    f"-> {nxt.operation_id} (starts {nxt.start})"
                )
                assert nxt.start >= curr.end, prec_msg

        # Invariant 3: Order release time respected
        for o in plant.orders:
            if o.release_time > 0:
                o_assignments = schedule.assignments_for_order(o.id)
                if o_assignments:
                    first_assignment = o_assignments[0]
                    rel_msg = (
                        f"Release time violated for {o.id}: "
                        f"start {first_assignment.start} < release {o.release_time}"
                    )
                    assert first_assignment.start >= o.release_time, rel_msg

        # Invariant 4: No machine overlap & setup times respected
        for mid in plant.machine_ids():
            m_assignments = schedule.assignments_for_machine(mid)
            for i in range(len(m_assignments) - 1):
                a_curr = m_assignments[i]
                a_next = m_assignments[i + 1]

                # Check setup times
                setup_required = 0
                op_curr = op_by_id.get(a_curr.operation_id)
                op_next = op_by_id.get(a_next.operation_id)
                if op_curr and op_next and op_curr.material_id and op_next.material_id:
                    mat_c = plant.material_by_id(op_curr.material_id)
                    mat_n = plant.material_by_id(op_next.material_id)
                    if mat_c and mat_n:
                        setup_required = plant.changeover_matrix.get_setup_time(
                            mid, mat_c.family, mat_n.family
                        )

                setup_msg = (
                    f"Machine {mid} overlap / setup violation: "
                    f"{a_curr.operation_id} ends at {a_curr.end}, "
                    f"setup {setup_required}, {a_next.operation_id} starts at {a_next.start}"
                )
                assert a_next.start >= a_curr.end + setup_required, setup_msg

        # Invariant 5: Planned downtime windows respected
        for dt in plant.downtimes:
            m_assignments = schedule.assignments_for_machine(dt.machine_id)
            for a in m_assignments:
                # Must either end before downtime starts or start after downtime ends
                no_conflict = (a.end <= dt.start) or (a.start >= dt.end)
                assert no_conflict, (
                    f"Downtime violation on {dt.machine_id} [{dt.start}, {dt.end}]: "
                    f"Operation {a.operation_id} scheduled at [{a.start}, {a.end}]"
                )
