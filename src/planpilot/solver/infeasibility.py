"""Infeasibility analysis using CP-SAT assumption literals.

Identifies minimal conflict sets (unsatisfiable cores) in over-constrained
production scheduling instances. Uses CP-SAT assumption literals to pinpoint
conflicts between due dates, release times, machine downtime windows, horizon limits,
and routing requirements, mapping solver variable indices to human-readable explanations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field

from planpilot.domain.models import Plant

if TYPE_CHECKING:
    pass


class InfeasibilityReport(BaseModel):
    """Detailed diagnosis of why a plant instance cannot be scheduled."""

    is_infeasible: bool
    core_constraints: list[str] = Field(default_factory=list)
    explanation: str = ""


def diagnose_infeasibility(
    plant: Plant,
    enforce_due_dates_as_hard: bool = True,
    time_limit_seconds: float = 10.0,
) -> InfeasibilityReport:
    """Analyze an over-constrained plant and extract human-readable conflict core.

    Args:
        plant: The Plant model to diagnose.
        enforce_due_dates_as_hard: Whether to treat order due dates as hard deadlines.
        time_limit_seconds: Solver time limit for core extraction.

    Returns:
        InfeasibilityReport containing minimal core constraints and a human-readable summary.
    """
    model = cp_model.CpModel()
    horizon = plant.horizon

    # Map assumption literal index to human-readable constraint description
    assumption_map: dict[int, str] = {}
    assumptions: list[cp_model.IntVar] = []

    # 1. Variables per operation & machine
    op_vars: dict[
        tuple[str, str, str], tuple[cp_model.IntVar, cp_model.IntVar, cp_model.IntVar]
    ] = {}
    op_alternatives: dict[
        tuple[str, str],
        list[tuple[str, cp_model.IntVar, cp_model.IntVar, cp_model.IntVar]],
    ] = {}
    machine_intervals: dict[str, list[cp_model.IntervalVar]] = {m.id: [] for m in plant.machines}
    order_last_ops: dict[str, list[cp_model.IntVar]] = {o.id: [] for o in plant.orders}
    order_first_ops: dict[str, list[cp_model.IntVar]] = {o.id: [] for o in plant.orders}

    order_op_sequences: dict[str, list[tuple[str, str]]] = {}

    for order in plant.orders:
        routing = plant.routing_by_id(order.routing_id)
        if routing is None:
            continue

        seq: list[tuple[str, str]] = []
        for op in routing.operations:
            key_op = (order.id, op.id)
            seq.append(key_op)
            alts: list[tuple[str, cp_model.IntVar, cp_model.IntVar, cp_model.IntVar]] = []

            for mid in op.eligible_machine_ids:
                suffix = f"{order.id}_{op.id}_{mid}"
                start_var = model.new_int_var(0, horizon, f"start_{suffix}")
                end_var = model.new_int_var(0, horizon, f"end_{suffix}")

                if len(op.eligible_machine_ids) == 1:
                    pres_var = model.new_constant(1)
                    interval = model.new_interval_var(
                        start_var, op.processing_time, end_var, f"iv_{suffix}"
                    )
                else:
                    pres_var = model.new_bool_var(f"pres_{suffix}")
                    interval = model.new_optional_interval_var(
                        start_var, op.processing_time, end_var, pres_var, f"iv_{suffix}"
                    )

                op_vars[(order.id, op.id, mid)] = (start_var, end_var, pres_var)
                alts.append((mid, start_var, end_var, pres_var))
                machine_intervals[mid].append(interval)

            op_alternatives[key_op] = alts

            if len(alts) > 1:
                model.add_exactly_one(pres for _, _, _, pres in alts)

        order_op_sequences[order.id] = seq
        if seq:
            first_key = seq[0]
            last_key = seq[-1]
            order_first_ops[order.id] = [st for _, st, _, _ in op_alternatives[first_key]]
            order_last_ops[order.id] = [en for _, _, en, _ in op_alternatives[last_key]]

    # 2. Routing precedence
    for _order_id, seq in order_op_sequences.items():
        for i in range(len(seq) - 1):
            curr_alts = op_alternatives[seq[i]]
            next_alts = op_alternatives[seq[i + 1]]
            for _, _, curr_end, curr_pres in curr_alts:
                for _, nxt_start, _, nxt_pres in next_alts:
                    if isinstance(curr_pres, int) and isinstance(nxt_pres, int):
                        model.add(nxt_start >= curr_end)
                    elif isinstance(curr_pres, int):
                        model.add(nxt_start >= curr_end).only_enforce_if(nxt_pres)
                    elif isinstance(nxt_pres, int):
                        model.add(nxt_start >= curr_end).only_enforce_if(curr_pres)
                    else:
                        model.add(nxt_start >= curr_end).only_enforce_if(curr_pres, nxt_pres)

    # 3. Machine NoOverlap
    for _mid, intervals in machine_intervals.items():
        if intervals:
            model.add_no_overlap(intervals)

    # 4. Release time assumptions
    for order in plant.orders:
        if order.release_time > 0 and order_first_ops.get(order.id):
            rel_lit = model.new_bool_var(f"assump_rel_{order.id}")
            assumptions.append(rel_lit)
            assumption_map[rel_lit.index] = (
                f"Order {order.id}: release time >= {order.release_time}"
            )
            for _, st, _, pres in op_alternatives[order_op_sequences[order.id][0]]:
                if isinstance(pres, int):
                    model.add(st >= order.release_time).only_enforce_if(rel_lit)
                else:
                    model.add(st >= order.release_time).only_enforce_if(rel_lit, pres)

    # 5. Due date assumptions
    if enforce_due_dates_as_hard:
        for order in plant.orders:
            if order.due_date is not None and order_last_ops.get(order.id):
                due_lit = model.new_bool_var(f"assump_due_{order.id}")
                assumptions.append(due_lit)
                assumption_map[due_lit.index] = (
                    f"Order {order.id}: completion <= due date {order.due_date}"
                )
                for _, _, en, pres in op_alternatives[order_op_sequences[order.id][-1]]:
                    if isinstance(pres, int):
                        model.add(en <= order.due_date).only_enforce_if(due_lit)
                    else:
                        model.add(en <= order.due_date).only_enforce_if(due_lit, pres)

    # 6. Downtime assumptions
    for dt in plant.downtimes:
        if dt.machine_id in machine_intervals:
            dt_lit = model.new_bool_var(f"assump_dt_{dt.machine_id}_{dt.start}")
            assumptions.append(dt_lit)
            assumption_map[dt_lit.index] = (
                f"Machine {dt.machine_id}: downtime window [{dt.start}, {dt.end}]"
                + (f" ({dt.reason})" if dt.reason else "")
            )
            for (ord_id, op_id, mid), (st, en, pres) in op_vars.items():
                if mid != dt.machine_id:
                    continue
                # Op must be before downtime start or after downtime end
                b = model.new_bool_var(f"b_dt_{ord_id}_{op_id}_{dt.start}")
                if isinstance(pres, int):
                    model.add(en <= dt.start).only_enforce_if(dt_lit, b)
                    model.add(st >= dt.end).only_enforce_if(dt_lit, b.negated())
                else:
                    model.add(en <= dt.start).only_enforce_if(dt_lit, pres, b)
                    model.add(st >= dt.end).only_enforce_if(dt_lit, pres, b.negated())

    # 7. Horizon assumption
    horizon_lit = model.new_bool_var("assump_horizon")
    assumptions.append(horizon_lit)
    assumption_map[horizon_lit.index] = f"Plant scheduling horizon <= {horizon}"
    for order in plant.orders:
        for en in order_last_ops.get(order.id, []):
            model.add(en <= horizon).only_enforce_if(horizon_lit)

    model.add_assumptions(assumptions)

    # Solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_workers = 4
    status = solver.solve(model)

    if status != cp_model.INFEASIBLE:
        return InfeasibilityReport(
            is_infeasible=False,
            core_constraints=[],
            explanation="Instance is feasible. All constraints can be satisfied simultaneously.",
        )

    # Extract minimal unsatisfiable core
    core_indices = solver.sufficient_assumptions_for_infeasibility()
    core_constraints: list[str] = []

    for idx in core_indices:
        # Check both positive and negated literal indices
        desc = assumption_map.get(idx) or assumption_map.get(~idx)
        if desc and desc not in core_constraints:
            core_constraints.append(desc)

    if not core_constraints:
        # Structural infeasibility (not isolated to assumptions)
        core_constraints.append(
            "Inherent structural conflict in routings, machine capacity, or horizon"
        )

    explanation = "Schedule is infeasible due to conflicting constraints:\n" + "\n".join(
        f"- {c}" for c in core_constraints
    )

    return InfeasibilityReport(
        is_infeasible=True,
        core_constraints=core_constraints,
        explanation=explanation,
    )
