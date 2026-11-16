"""CP-SAT flexible job-shop scheduler.

Builds a CP-SAT model for the flexible job-shop scheduling problem:
- Interval variables per operation × eligible machine (optional intervals)
- Exactly-one machine assignment per operation
- Precedence constraints within routings
- NoOverlap per machine with sequence-dependent setup times
- Release times, machine downtime windows, due dates
- Objectives: makespan, total weighted tardiness, lexicographic
- Decision trace: tight constraint tracking per assignment
- Infeasibility diagnosis via CP-SAT assumption literals
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from planpilot.domain.models import (
    Assignment,
    KPIs,
    ObjectiveType,
    Plant,
    Schedule,
    SolverStats,
    SolverStatus,
    TightConstraint,
)
from planpilot.rules.compilers import CompilerContext, compile_rule
from planpilot.rules.dsl import Rule
from planpilot.solver.infeasibility import diagnose_infeasibility


# ---------------------------------------------------------------------------
# Internal data structures for model building
# ---------------------------------------------------------------------------
class _OpVar:
    """Variables associated with one operation on one eligible machine."""

    __slots__ = (
        "order_id",
        "operation_id",
        "machine_id",
        "processing_time",
        "interval",
        "start",
        "end",
        "presence",
    )

    def __init__(
        self,
        order_id: str,
        operation_id: str,
        machine_id: str,
        processing_time: int,
        interval: cp_model.IntervalVar,
        start: cp_model.IntVar,
        end: cp_model.IntVar,
        presence: cp_model.IntVar,
    ) -> None:
        self.order_id = order_id
        self.operation_id = operation_id
        self.machine_id = machine_id
        self.processing_time = processing_time
        self.interval = interval
        self.start = start
        self.end = end
        self.presence = presence


def _map_solver_status(status: cp_model.CpSolverStatus | int) -> SolverStatus:
    """Map OR-Tools status int to our enum."""
    if status == cp_model.OPTIMAL:
        return SolverStatus.OPTIMAL
    if status == cp_model.FEASIBLE:
        return SolverStatus.FEASIBLE
    if status == cp_model.INFEASIBLE:
        return SolverStatus.INFEASIBLE
    if status == cp_model.MODEL_INVALID:
        return SolverStatus.MODEL_INVALID
    return SolverStatus.UNKNOWN


def solve(
    plant: Plant,
    objective: ObjectiveType = ObjectiveType.MAKESPAN,
    rules: list[Rule] | None = None,
    time_limit_seconds: float = 60.0,
    num_workers: int = 8,
    diagnose_if_infeasible: bool = True,
) -> Schedule:
    """Solve the flexible job-shop scheduling problem.

    Args:
        plant: Plant instance with machines, routings, orders, etc.
        objective: Optimization objective.
        time_limit_seconds: Solver wall-clock time limit.
        num_workers: Number of solver worker threads.
        diagnose_if_infeasible: Whether to compute unsatisfiable core if infeasible.

    Returns:
        Schedule with assignments, KPIs, and solver stats.
    """
    model = cp_model.CpModel()
    horizon = plant.horizon

    # -----------------------------------------------------------------------
    # 1. Build interval variables
    # -----------------------------------------------------------------------
    # op_vars[(order_id, op_id, machine_id)] → _OpVar
    op_vars: dict[tuple[str, str, str], _OpVar] = {}

    # For each operation, track all its alternative _OpVars
    op_alternatives: dict[tuple[str, str], list[_OpVar]] = {}

    # Machine → list of _OpVars assigned to it
    machine_ops: dict[str, list[_OpVar]] = {m.id: [] for m in plant.machines}

    # Order-level data
    order_op_sequences: dict[str, list[tuple[str, str]]] = {}

    for order in plant.orders:
        routing = plant.routing_by_id(order.routing_id)
        if routing is None:
            continue

        op_seq: list[tuple[str, str]] = []
        for op in routing.operations:
            alternatives: list[_OpVar] = []
            key_op = (order.id, op.id)

            for mid in op.eligible_machine_ids:
                suffix = f"{order.id}_{op.id}_{mid}"
                is_single = len(op.eligible_machine_ids) == 1

                if is_single:
                    start_var = model.new_int_var(0, horizon, f"start_{suffix}")
                    end_var = model.new_int_var(0, horizon, f"end_{suffix}")
                    interval_var = model.new_interval_var(
                        start_var, op.processing_time, end_var, f"iv_{suffix}"
                    )
                    presence = model.new_constant(1)
                else:
                    presence = model.new_bool_var(f"pres_{suffix}")
                    start_var = model.new_int_var(0, horizon, f"start_{suffix}")
                    end_var = model.new_int_var(0, horizon, f"end_{suffix}")
                    interval_var = model.new_optional_interval_var(
                        start_var, op.processing_time, end_var, presence, f"iv_{suffix}"
                    )

                ov = _OpVar(
                    order_id=order.id,
                    operation_id=op.id,
                    machine_id=mid,
                    processing_time=op.processing_time,
                    interval=interval_var,
                    start=start_var,
                    end=end_var,
                    presence=presence,
                )
                op_vars[(order.id, op.id, mid)] = ov
                alternatives.append(ov)
                machine_ops[mid].append(ov)

            op_alternatives[key_op] = alternatives
            op_seq.append(key_op)

            # Exactly-one machine constraint (for multi-machine ops)
            if len(alternatives) > 1:
                model.add_exactly_one(ov.presence for ov in alternatives)

        order_op_sequences[order.id] = op_seq

        # Release time constraint
        if order.release_time > 0 and op_seq:
            for ov in op_alternatives[op_seq[0]]:
                if isinstance(ov.presence, int):
                    model.add(ov.start >= order.release_time)
                else:
                    model.add(ov.start >= order.release_time).only_enforce_if(ov.presence)

    # -----------------------------------------------------------------------
    # 2. Precedence constraints within routings
    # -----------------------------------------------------------------------
    for _ord_id, op_seq in order_op_sequences.items():
        for i in range(len(op_seq) - 1):
            curr_alts = op_alternatives[op_seq[i]]
            next_alts = op_alternatives[op_seq[i + 1]]
            for curr in curr_alts:
                for nxt in next_alts:
                    if isinstance(curr.presence, int) and isinstance(nxt.presence, int):
                        model.add(nxt.start >= curr.end)
                    elif isinstance(curr.presence, int):
                        model.add(nxt.start >= curr.end).only_enforce_if(nxt.presence)
                    elif isinstance(nxt.presence, int):
                        model.add(nxt.start >= curr.end).only_enforce_if(curr.presence)
                    else:
                        model.add(nxt.start >= curr.end).only_enforce_if(
                            curr.presence, nxt.presence
                        )

    # -----------------------------------------------------------------------
    # 3. NoOverlap per machine (with sequence-dependent setups & downtimes)
    # -----------------------------------------------------------------------
    op_material: dict[tuple[str, str], str | None] = {}
    for order in plant.orders:
        routing = plant.routing_by_id(order.routing_id)
        if routing is None:
            continue
        for op in routing.operations:
            op_material[(order.id, op.id)] = op.material_id

    # Create downtime intervals per machine
    downtime_intervals: dict[str, list[cp_model.IntervalVar]] = {m.id: [] for m in plant.machines}
    for dt in plant.downtimes:
        if dt.machine_id in downtime_intervals:
            dt_iv = model.new_interval_var(
                dt.start,
                dt.end - dt.start,
                dt.end,
                f"downtime_{dt.machine_id}_{dt.start}_{dt.end}",
            )
            downtime_intervals[dt.machine_id].append(dt_iv)

    for mid, ovs in machine_ops.items():
        all_intervals = [ov.interval for ov in ovs] + downtime_intervals[mid]
        if not all_intervals:
            continue

        # Add native NoOverlap for the machine
        model.add_no_overlap(all_intervals)

        # Check if changeover matrix applies to this machine
        has_setups = False
        if plant.changeover_matrix.entries:
            for entry in plant.changeover_matrix.entries:
                if entry.machine_id == mid:
                    has_setups = True
                    break

        if has_setups and len(ovs) > 1:
            # Pairwise ordering literals with setup times
            for i in range(len(ovs)):
                for j in range(i + 1, len(ovs)):
                    ov_a = ovs[i]
                    ov_b = ovs[j]

                    mat_a = op_material.get((ov_a.order_id, ov_a.operation_id))
                    mat_b = op_material.get((ov_b.order_id, ov_b.operation_id))

                    if mat_a is None or mat_b is None:
                        continue

                    mat_obj_a = plant.material_by_id(mat_a)
                    mat_obj_b = plant.material_by_id(mat_b)
                    if mat_obj_a is None or mat_obj_b is None:
                        continue

                    setup_ab = plant.changeover_matrix.get_setup_time(
                        mid, mat_obj_a.family, mat_obj_b.family
                    )
                    setup_ba = plant.changeover_matrix.get_setup_time(
                        mid, mat_obj_b.family, mat_obj_a.family
                    )

                    if setup_ab == 0 and setup_ba == 0:
                        continue

                    a_before_b = model.new_bool_var(
                        f"order_{ov_a.order_id}_{ov_a.operation_id}_before_"
                        f"{ov_b.order_id}_{ov_b.operation_id}_on_{mid}"
                    )

                    if isinstance(ov_a.presence, int) and isinstance(ov_b.presence, int):
                        model.add(ov_b.start >= ov_a.end + setup_ab).only_enforce_if(a_before_b)
                        model.add(ov_a.start >= ov_b.end + setup_ba).only_enforce_if(
                            a_before_b.negated()
                        )
                    else:
                        enforce_ab: list[cp_model.LiteralT] = [a_before_b]
                        enforce_ba: list[cp_model.LiteralT] = [a_before_b.negated()]
                        if not isinstance(ov_a.presence, int):
                            enforce_ab.append(ov_a.presence)
                            enforce_ba.append(ov_a.presence)
                        if not isinstance(ov_b.presence, int):
                            enforce_ab.append(ov_b.presence)
                            enforce_ba.append(ov_b.presence)

                        model.add(ov_b.start >= ov_a.end + setup_ab).only_enforce_if(enforce_ab)
                        model.add(ov_a.start >= ov_b.end + setup_ba).only_enforce_if(enforce_ba)

    # -----------------------------------------------------------------------
    # 4. Objective
    # -----------------------------------------------------------------------
    if objective == ObjectiveType.MAKESPAN:
        makespan = model.new_int_var(0, horizon, "makespan")
        for _ord_id, op_seq in order_op_sequences.items():
            if op_seq:
                last_key = op_seq[-1]
                for ov in op_alternatives[last_key]:
                    if isinstance(ov.presence, int):
                        model.add(makespan >= ov.end)
                    else:
                        model.add(makespan >= ov.end).only_enforce_if(ov.presence)
        model.minimize(makespan)

    elif objective == ObjectiveType.TOTAL_WEIGHTED_TARDINESS:
        tardiness_vars: list[cp_model.IntVar] = []
        for order in plant.orders:
            if order.due_date is None:
                continue
            op_seq = order_op_sequences.get(order.id, [])
            if not op_seq:
                continue
            last_key = op_seq[-1]
            completion = model.new_int_var(0, horizon, f"completion_{order.id}")
            for ov in op_alternatives[last_key]:
                if isinstance(ov.presence, int):
                    model.add(completion >= ov.end)
                else:
                    model.add(completion >= ov.end).only_enforce_if(ov.presence)

            tardiness = model.new_int_var(0, horizon, f"tardiness_{order.id}")
            model.add_max_equality(tardiness, [completion - order.due_date, model.new_constant(0)])
            weighted = model.new_int_var(0, horizon * 100, f"wtard_{order.id}")
            model.add(weighted == int(order.weight) * tardiness)
            tardiness_vars.append(weighted)

        if tardiness_vars:
            model.minimize(sum(tardiness_vars))
        else:
            makespan = model.new_int_var(0, horizon, "makespan")
            for _ord_id, op_seq in order_op_sequences.items():
                if op_seq:
                    last_key = op_seq[-1]
                    for ov in op_alternatives[last_key]:
                        if isinstance(ov.presence, int):
                            model.add(makespan >= ov.end)
                        else:
                            model.add(makespan >= ov.end).only_enforce_if(ov.presence)
            model.minimize(makespan)

    elif objective == ObjectiveType.LEXICOGRAPHIC:
        makespan = model.new_int_var(0, horizon, "makespan")
        for _ord_id, op_seq in order_op_sequences.items():
            if op_seq:
                last_key = op_seq[-1]
                for ov in op_alternatives[last_key]:
                    if isinstance(ov.presence, int):
                        model.add(makespan >= ov.end)
                    else:
                        model.add(makespan >= ov.end).only_enforce_if(ov.presence)

        tardiness_vars_lex: list[cp_model.IntVar] = []
        for order in plant.orders:
            if order.due_date is None:
                continue
            op_seq = order_op_sequences.get(order.id, [])
            if not op_seq:
                continue
            last_key = op_seq[-1]
            completion = model.new_int_var(0, horizon, f"completion_{order.id}")
            for ov in op_alternatives[last_key]:
                if isinstance(ov.presence, int):
                    model.add(completion >= ov.end)
                else:
                    model.add(completion >= ov.end).only_enforce_if(ov.presence)
            tardiness = model.new_int_var(0, horizon, f"tardiness_{order.id}")
            model.add_max_equality(tardiness, [completion - order.due_date, model.new_constant(0)])
            weighted = model.new_int_var(0, horizon * 100, f"wtard_{order.id}")
            model.add(weighted == int(order.weight) * tardiness)
            tardiness_vars_lex.append(weighted)

        big_m = horizon * len(plant.orders) * 100
        total_tardiness_lex = model.new_int_var(0, horizon * len(plant.orders) * 100, "total_tard")
        if tardiness_vars_lex:
            model.add(total_tardiness_lex == sum(tardiness_vars_lex))
        else:
            model.add(total_tardiness_lex == 0)
        model.minimize(big_m * makespan + total_tardiness_lex)

    # -----------------------------------------------------------------------
    # 5. Compile Domain Rules
    # -----------------------------------------------------------------------
    if rules:
        ctx = CompilerContext(
            plant=plant,
            model=model,
            op_vars={
                k: (ov.start, ov.end, ov.presence, ov.processing_time) for k, ov in op_vars.items()
            },
            op_alternatives={
                k: [(ov.machine_id, ov.start, ov.end, ov.presence) for ov in alts]
                for k, alts in op_alternatives.items()
            },
            machine_ops={
                m: [
                    (
                        ov.order_id,
                        ov.operation_id,
                        ov.start,
                        ov.end,
                        ov.presence,
                        ov.processing_time,
                    )
                    for ov in ovs
                ]
                for m, ovs in machine_ops.items()
            },
            order_op_sequences=order_op_sequences,
            horizon=horizon,
        )
        for r in rules:
            compile_rule(r, ctx)

    # -----------------------------------------------------------------------
    # 6. Solve
    # -----------------------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_workers = num_workers

    status_code = solver.solve(model)
    status = _map_solver_status(status_code)

    # Infeasibility handling
    infeasibility_core: list[str] = []
    if status == SolverStatus.INFEASIBLE and diagnose_if_infeasible:
        diag = diagnose_infeasibility(plant, time_limit_seconds=min(time_limit_seconds, 10.0))
        infeasibility_core = diag.core_constraints

    # -----------------------------------------------------------------------
    # 6. Extract solution
    # -----------------------------------------------------------------------
    assignments: list[Assignment] = []

    if status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
        # First gather all selected operations grouped by machine
        selected_by_machine: dict[str, list[tuple[int, int, _OpVar]]] = {
            m.id: [] for m in plant.machines
        }

        for (_ord_id, _op_id, mid), ov in op_vars.items():
            is_present = (isinstance(ov.presence, int) and ov.presence == 1) or (
                not isinstance(ov.presence, int) and solver.value(ov.presence) == 1
            )
            if is_present:
                st = solver.value(ov.start)
                en = solver.value(ov.end)
                selected_by_machine[mid].append((st, en, ov))

        # Sort each machine's operations by start time to compute setup times
        for m in plant.machines:
            selected_by_machine[m.id].sort(key=lambda x: x[0])

        # Map each operation to its predecessor on the same machine
        machine_prev_op: dict[tuple[str, str, str], tuple[int, _OpVar] | None] = {}
        for mid, m_ops in selected_by_machine.items():
            for idx, (_st, _en, ov) in enumerate(m_ops):
                key = (ov.order_id, ov.operation_id, mid)
                if idx > 0:
                    prev_end = m_ops[idx - 1][1]
                    prev_ov = m_ops[idx - 1][2]
                    machine_prev_op[key] = (prev_end, prev_ov)
                else:
                    machine_prev_op[key] = None

        # Build assignments with decision trace & setup times
        for mid, m_ops in selected_by_machine.items():
            for st, en, ov in m_ops:
                key = (ov.order_id, ov.operation_id, mid)
                tight: list[TightConstraint] = []

                # Calculate sequence-dependent setup time
                setup_time = 0
                setup_start: int | None = None
                prev_info = machine_prev_op.get(key)

                if prev_info is not None:
                    prev_end, prev_ov = prev_info
                    prev_mat = op_material.get((prev_ov.order_id, prev_ov.operation_id))
                    curr_mat = op_material.get((ov.order_id, ov.operation_id))
                    if prev_mat and curr_mat:
                        mat_obj_p = plant.material_by_id(prev_mat)
                        mat_obj_c = plant.material_by_id(curr_mat)
                        if mat_obj_p and mat_obj_c:
                            setup_time = plant.changeover_matrix.get_setup_time(
                                mid, mat_obj_p.family, mat_obj_c.family
                            )
                    if setup_time > 0:
                        setup_start = max(0, st - setup_time)

                    # Check MACHINE_PREDECESSOR_SETUP tight constraint
                    if st == prev_end + setup_time:
                        tight.append(TightConstraint.MACHINE_PREDECESSOR_SETUP)

                # Check RELEASE_TIME tight constraint
                order_obj = plant.order_by_id(ov.order_id)
                if (
                    order_obj is not None
                    and order_obj.release_time > 0
                    and st == order_obj.release_time
                ):
                    tight.append(TightConstraint.RELEASE_TIME)

                # Check PREDECESSOR_END tight constraint
                op_seq = order_op_sequences.get(ov.order_id, [])
                op_idx = None
                for idx, k in enumerate(op_seq):
                    if k == (ov.order_id, ov.operation_id):
                        op_idx = idx
                        break
                if op_idx is not None and op_idx > 0:
                    prev_key = op_seq[op_idx - 1]
                    for prev_ov in op_alternatives[prev_key]:
                        prev_present = (
                            isinstance(prev_ov.presence, int) and prev_ov.presence == 1
                        ) or (
                            not isinstance(prev_ov.presence, int)
                            and solver.value(prev_ov.presence) == 1
                        )
                        if prev_present:
                            prev_end_val = solver.value(prev_ov.end)
                            if st == prev_end_val:
                                tight.append(TightConstraint.PREDECESSOR_END)
                            break

                # Check DOWNTIME_BOUNDARY tight constraint
                for dt in plant.downtimes:
                    if dt.machine_id == mid and (st == dt.end or en == dt.start):
                        tight.append(TightConstraint.DOWNTIME_BOUNDARY)

                if not tight:
                    tight.append(TightConstraint.NONE)

                assignments.append(
                    Assignment(
                        order_id=ov.order_id,
                        operation_id=ov.operation_id,
                        machine_id=mid,
                        start=st,
                        end=en,
                        setup_start=setup_start,
                        setup_time=setup_time,
                        processing_time=ov.processing_time,
                        tight_constraints=tight,
                    )
                )

    assignments.sort(key=lambda a: (a.start, a.order_id, a.operation_id))

    # -----------------------------------------------------------------------
    # 7. Compute KPIs
    # -----------------------------------------------------------------------
    kpis = _compute_kpis(plant, assignments)

    # -----------------------------------------------------------------------
    # 8. Build solver stats
    # -----------------------------------------------------------------------
    obj_val = None
    best_bound = None
    gap_pct = None

    if status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
        obj_val = int(solver.objective_value)
        best_bound = int(solver.best_objective_bound)
        gap_pct = round(abs(obj_val - best_bound) / obj_val * 100, 2) if obj_val > 0 else 0.0

    num_vars = len(model.proto.variables) if hasattr(model.proto, "variables") else 0
    num_constraints = len(model.proto.constraints) if hasattr(model.proto, "constraints") else 0

    solver_stats = SolverStats(
        status=status,
        objective_value=obj_val,
        best_bound=best_bound,
        gap_pct=gap_pct,
        wall_time_seconds=round(solver.wall_time, 3),
        num_variables=num_vars,
        num_constraints=num_constraints,
        num_branches=solver.num_branches,
        infeasibility_core=infeasibility_core,
    )

    return Schedule(
        plant_id=plant.id,
        assignments=assignments,
        kpis=kpis,
        solver_stats=solver_stats,
        objective_type=objective,
    )


def _compute_kpis(plant: Plant, assignments: list[Assignment]) -> KPIs:
    """Compute KPIs from a list of assignments."""
    if not assignments:
        return KPIs()

    makespan = max(a.end for a in assignments)

    order_completion: dict[str, int] = {}
    for a in assignments:
        if a.order_id not in order_completion or a.end > order_completion[a.order_id]:
            order_completion[a.order_id] = a.end

    total_tardiness = 0
    weighted_tardiness = 0.0
    num_tardy = 0

    for order in plant.orders:
        completion = order_completion.get(order.id)
        if completion is None or order.due_date is None:
            continue
        tardiness = max(0, completion - order.due_date)
        total_tardiness += tardiness
        weighted_tardiness += tardiness * order.weight
        if tardiness > 0:
            num_tardy += 1

    orders_with_due_dates = sum(
        1 for o in plant.orders if o.due_date is not None and o.id in order_completion
    )
    on_time_pct = (
        round((orders_with_due_dates - num_tardy) / orders_with_due_dates * 100, 1)
        if orders_with_due_dates > 0
        else 100.0
    )

    machine_util: dict[str, float] = {}
    for m in plant.machines:
        m_assignments = [a for a in assignments if a.machine_id == m.id]
        if not m_assignments:
            machine_util[m.id] = 0.0
            continue
        busy_time = sum(a.processing_time for a in m_assignments)
        machine_util[m.id] = round(busy_time / makespan * 100, 1) if makespan > 0 else 0.0

    avg_util = round(sum(machine_util.values()) / len(machine_util), 1) if machine_util else 0.0

    total_setup = sum(a.setup_time for a in assignments)
    throughput = round(len(order_completion) / makespan, 4) if makespan > 0 else 0.0

    return KPIs(
        makespan=makespan,
        total_tardiness=total_tardiness,
        weighted_tardiness=round(weighted_tardiness, 2),
        on_time_pct=on_time_pct,
        num_tardy_orders=num_tardy,
        total_setup_time=total_setup,
        throughput=throughput,
        machine_utilization=machine_util,
        avg_utilization=avg_util,
    )
