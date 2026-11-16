"""CP-SAT constraint compilers for the Rule DSL.

Compiles high-level scheduling rules into CP-SAT mathematical constraints:
- ForbidSequence
- MinChangeover
- MachineUnavailable
- PinToMachine
- ForbidMachine
- OrderPrecedence
- MaxConsecutiveRuns
- HardDueDate
- BatchTogether
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ortools.sat.python import cp_model

from planpilot.domain.models import Plant
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
    Rule,
    RuleType,
)


@dataclass
class CompilerContext:
    """Context holding CP-SAT variables and lookups for compilation."""

    plant: Plant
    model: cp_model.CpModel
    # op_vars[(order_id, op_id, machine_id)] = (start_var, end_var, presence_var, proc_time)
    op_vars: dict[tuple[str, str, str], tuple[cp_model.IntVar, cp_model.IntVar, Any, int]]
    # op_alternatives[(order_id, op_id)] = list of (machine_id, start_var, end_var, presence_var)
    op_alternatives: dict[tuple[str, str], list[tuple[str, cp_model.IntVar, cp_model.IntVar, Any]]]
    # machine_ops[mid] = list of (ord_id, op_id, st_var, en_var, pres_var, proc_time)
    machine_ops: dict[str, list[tuple[str, str, cp_model.IntVar, cp_model.IntVar, Any, int]]]
    # order_op_sequences[order_id] = list of (order_id, op_id)
    order_op_sequences: dict[str, list[tuple[str, str]]]
    horizon: int


def compile_rule(rule: Rule, ctx: CompilerContext) -> None:
    """Dispatch compilation for a concrete rule instance."""
    if not rule.enabled:
        return

    compilers = {
        RuleType.FORBID_SEQUENCE: _compile_forbid_sequence,
        RuleType.MIN_CHANGEOVER: _compile_min_changeover,
        RuleType.MACHINE_UNAVAILABLE: _compile_machine_unavailable,
        RuleType.PIN_TO_MACHINE: _compile_pin_to_machine,
        RuleType.FORBID_MACHINE: _compile_forbid_machine,
        RuleType.ORDER_PRECEDENCE: _compile_order_precedence,
        RuleType.MAX_CONSECUTIVE_RUNS: _compile_max_consecutive_runs,
        RuleType.HARD_DUE_DATE: _compile_hard_due_date,
        RuleType.BATCH_TOGETHER: _compile_batch_together,
    }

    compiler = compilers.get(rule.type)
    if compiler is not None:
        compiler(rule, ctx)  # type: ignore[operator]


def _get_op_material_family(order_id: str, op_id: str, ctx: CompilerContext) -> str | None:
    """Look up material family of an operation."""
    routing = None
    order = ctx.plant.order_by_id(order_id)
    if order:
        routing = ctx.plant.routing_by_id(order.routing_id)
    if not routing:
        return None

    for op in routing.operations:
        if op.id == op_id:
            mat_id = op.material_id or (order.material_id if order else None)
            if mat_id:
                mat = ctx.plant.material_by_id(mat_id)
                if mat:
                    return str(mat.family.value)
    return None


def _compile_forbid_sequence(rule: ForbidSequence, ctx: CompilerContext) -> None:
    """Compile ForbidSequence rule.

    If op_a (from_material) runs before op_b (to_material) on rule.machine_id,
    they must be separated by at least rule.unless_cleanout_min if adjacent, or forbidden.
    """
    ops = ctx.machine_ops.get(rule.machine_id, [])
    from_fam = str(
        rule.from_material.value if hasattr(rule.from_material, "value") else rule.from_material
    ).lower()
    to_fam = str(
        rule.to_material.value if hasattr(rule.to_material, "value") else rule.to_material
    ).lower()

    for i in range(len(ops)):
        for j in range(len(ops)):
            if i == j:
                continue
            ord_a, op_id_a, st_a, en_a, pres_a, _ = ops[i]
            ord_b, op_id_b, st_b, en_b, pres_b, _ = ops[j]

            mat_a = _get_op_material_family(ord_a, op_id_a, ctx)
            mat_b = _get_op_material_family(ord_b, op_id_b, ctx)

            if mat_a and mat_b and mat_a.lower() == from_fam and mat_b.lower() == to_fam:
                # A is from_material, B is to_material on this machine
                cleanout = rule.unless_cleanout_min
                options: list[cp_model.LiteralT] = []

                # Option 1: B runs before A
                b_before_a = ctx.model.new_bool_var(
                    f"forbid_seq_b_before_a_{ord_a}_{op_id_a}_{ord_b}_{op_id_b}"
                )
                ctx.model.add(st_a >= en_b).only_enforce_if(b_before_a)
                options.append(b_before_a)

                # Option 2: Cleanout gap satisfied
                if cleanout > 0:
                    gap_ok = ctx.model.new_bool_var(
                        f"forbid_seq_gap_ok_{ord_a}_{op_id_a}_{ord_b}_{op_id_b}"
                    )
                    ctx.model.add(st_b >= en_a + cleanout).only_enforce_if(gap_ok)
                    options.append(gap_ok)

                # Option 3: Another operation C runs between A and B
                for k in range(len(ops)):
                    if k in (i, j):
                        continue
                    ord_c, op_id_c, st_c, en_c, pres_c, _ = ops[k]
                    c_between = ctx.model.new_bool_var(
                        f"forbid_seq_c_between_{ord_a}_{op_id_a}_{ord_b}_{op_id_b}_{ord_c}_{op_id_c}"
                    )
                    ctx.model.add(st_c >= en_a).only_enforce_if(c_between)
                    ctx.model.add(st_b >= en_c).only_enforce_if(c_between)
                    if not isinstance(pres_c, int):
                        ctx.model.add_implication(c_between, pres_c)
                    options.append(c_between)

                enforce_lits: list[cp_model.LiteralT] = []
                if not isinstance(pres_a, int):
                    enforce_lits.append(pres_a)
                if not isinstance(pres_b, int):
                    enforce_lits.append(pres_b)

                if enforce_lits:
                    ctx.model.add_bool_or(options).only_enforce_if(enforce_lits)
                else:
                    ctx.model.add_bool_or(options)


def _compile_min_changeover(rule: MinChangeover, ctx: CompilerContext) -> None:
    """Compile MinChangeover rule.

    Enforces minimum setup time between any distinct materials on rule.machine_id.
    """
    ops = ctx.machine_ops.get(rule.machine_id, [])
    for i in range(len(ops)):
        for j in range(i + 1, len(ops)):
            ord_a, op_id_a, st_a, en_a, pres_a, _ = ops[i]
            ord_b, op_id_b, st_b, en_b, pres_b, _ = ops[j]

            mat_a = _get_op_material_family(ord_a, op_id_a, ctx)
            mat_b = _get_op_material_family(ord_b, op_id_b, ctx)

            if mat_a != mat_b:
                min_setup = rule.min_setup_min
                a_before_b = ctx.model.new_bool_var(f"min_co_{ord_a}_{op_id_a}_{ord_b}_{op_id_b}")
                enforce_ab: list[cp_model.LiteralT] = [a_before_b]
                enforce_ba: list[cp_model.LiteralT] = [a_before_b.negated()]
                if not isinstance(pres_a, int):
                    enforce_ab.append(pres_a)
                    enforce_ba.append(pres_a)
                if not isinstance(pres_b, int):
                    enforce_ab.append(pres_b)
                    enforce_ba.append(pres_b)

                ctx.model.add(st_b >= en_a + min_setup).only_enforce_if(enforce_ab)
                ctx.model.add(st_a >= en_b + min_setup).only_enforce_if(enforce_ba)


def _compile_machine_unavailable(rule: MachineUnavailable, ctx: CompilerContext) -> None:
    """Compile MachineUnavailable rule.

    Creates an unavailable interval on rule.machine_id.
    """
    ops = ctx.machine_ops.get(rule.machine_id, [])
    for ord_id, op_id, st, en, pres, _ in ops:
        b = ctx.model.new_bool_var(f"unavail_{ord_id}_{op_id}_{rule.start}")
        if isinstance(pres, int):
            ctx.model.add(en <= rule.start).only_enforce_if(b)
            ctx.model.add(st >= rule.end).only_enforce_if(b.negated())
        else:
            ctx.model.add(en <= rule.start).only_enforce_if(b, pres)
            ctx.model.add(st >= rule.end).only_enforce_if(b.negated(), pres)


def _compile_pin_to_machine(rule: PinToMachine, ctx: CompilerContext) -> None:
    """Compile PinToMachine rule.

    Forces operation to execute on rule.machine_id.
    """
    key = (rule.order_id, rule.operation_id)
    alts = ctx.op_alternatives.get(key, [])
    for mid, _, _, pres in alts:
        if mid == rule.machine_id:
            if not isinstance(pres, int):
                ctx.model.add(pres == 1)
        else:
            if not isinstance(pres, int):
                ctx.model.add(pres == 0)


def _compile_forbid_machine(rule: ForbidMachine, ctx: CompilerContext) -> None:
    """Compile ForbidMachine rule.

    Prevents operation from executing on rule.machine_id.
    """
    key = (rule.order_id, rule.operation_id)
    alts = ctx.op_alternatives.get(key, [])
    for mid, _, _, pres in alts:
        if mid == rule.machine_id and not isinstance(pres, int):
            ctx.model.add(pres == 0)


def _compile_order_precedence(rule: OrderPrecedence, ctx: CompilerContext) -> None:
    """Compile OrderPrecedence rule.

    Before order must complete before after order starts, plus optional min gap.
    """
    before_seq = ctx.order_op_sequences.get(rule.before_order_id, [])
    after_seq = ctx.order_op_sequences.get(rule.after_order_id, [])

    if not before_seq or not after_seq:
        return

    last_before_key = before_seq[-1]
    first_after_key = after_seq[0]

    last_before_alts = ctx.op_alternatives.get(last_before_key, [])
    first_after_alts = ctx.op_alternatives.get(first_after_key, [])

    for _, _, en_before, pres_b in last_before_alts:
        for _, st_after, _, pres_a in first_after_alts:
            gap = rule.min_gap_min
            if isinstance(pres_b, int) and isinstance(pres_a, int):
                ctx.model.add(st_after >= en_before + gap)
            elif isinstance(pres_b, int):
                ctx.model.add(st_after >= en_before + gap).only_enforce_if(pres_a)
            elif isinstance(pres_a, int):
                ctx.model.add(st_after >= en_before + gap).only_enforce_if(pres_b)
            else:
                ctx.model.add(st_after >= en_before + gap).only_enforce_if(pres_b, pres_a)


def _compile_max_consecutive_runs(rule: MaxConsecutiveRuns, ctx: CompilerContext) -> None:
    """Compile MaxConsecutiveRuns rule.

    No more than rule.max_runs consecutive jobs of material_family on machine_id.
    """
    import itertools

    ops = ctx.machine_ops.get(rule.machine_id, [])
    fam = str(
        rule.material_family.value
        if hasattr(rule.material_family, "value")
        else rule.material_family
    ).lower()
    matching_ops = []
    other_ops = []

    for ord_id, op_id, st, en, pres, pt in ops:
        mat_fam = _get_op_material_family(ord_id, op_id, ctx)
        if mat_fam and mat_fam.lower() == fam:
            matching_ops.append((ord_id, op_id, st, en, pres, pt))
        else:
            other_ops.append((ord_id, op_id, st, en, pres, pt))

    k = rule.max_runs + 1
    if len(matching_ops) < k:
        return

    # In any subset of k matching operations, they cannot all be scheduled consecutively
    # without an intervening operation of another material.
    for subset in itertools.combinations(matching_ops, k):
        enforce_lits: list[cp_model.IntVar] = []
        for _, _, _, _, pres_u, _ in subset:
            if isinstance(pres_u, cp_model.IntVar):
                enforce_lits.append(pres_u)

        if not other_ops:
            if enforce_lits:
                ctx.model.add_bool_or([lit.negated() for lit in enforce_lits])
            continue

        intervening_lits: list[cp_model.LiteralT] = []
        for ord_v, op_v, st_v, en_v, pres_v, _ in other_ops:
            sub_id = "_".join(f"{u[0]}_{u[1]}" for u in subset)
            is_int = ctx.model.new_bool_var(f"mcr_int_{ord_v}_{op_v}_{sub_id}")

            after_u_lits: list[cp_model.LiteralT] = []
            before_u_lits: list[cp_model.LiteralT] = []
            for ord_u, op_u, st_u, en_u, _, _ in subset:
                b_aft = ctx.model.new_bool_var(f"mcr_aft_{ord_v}_{op_v}_{ord_u}_{op_u}")
                ctx.model.add(st_v >= en_u).only_enforce_if(b_aft)
                ctx.model.add(st_v < en_u).only_enforce_if(b_aft.negated())
                after_u_lits.append(b_aft)

                b_bef = ctx.model.new_bool_var(f"mcr_bef_{ord_v}_{op_v}_{ord_u}_{op_u}")
                ctx.model.add(en_v <= st_u).only_enforce_if(b_bef)
                ctx.model.add(en_v > st_u).only_enforce_if(b_bef.negated())
                before_u_lits.append(b_bef)

            ctx.model.add_bool_or(after_u_lits).only_enforce_if(is_int)
            ctx.model.add_bool_or(before_u_lits).only_enforce_if(is_int)
            if not isinstance(pres_v, int):
                ctx.model.add_implication(is_int, pres_v)
            intervening_lits.append(is_int)

        if enforce_lits:
            ctx.model.add_bool_or(intervening_lits).only_enforce_if(enforce_lits)
        else:
            ctx.model.add_bool_or(intervening_lits)


def _compile_hard_due_date(rule: HardDueDate, ctx: CompilerContext) -> None:
    """Compile HardDueDate rule.

    Order completion must not exceed deadline.
    """
    seq = ctx.order_op_sequences.get(rule.order_id, [])
    if not seq:
        return

    last_key = seq[-1]
    alts = ctx.op_alternatives.get(last_key, [])
    for _, _, en, pres in alts:
        if isinstance(pres, int):
            ctx.model.add(en <= rule.deadline)
        else:
            ctx.model.add(en <= rule.deadline).only_enforce_if(pres)


def _compile_batch_together(rule: BatchTogether, ctx: CompilerContext) -> None:
    """Compile BatchTogether rule.

    Selected orders should execute contiguously on the specified machine.
    """
    if not rule.machine_id:
        return

    # Find the ops of these orders eligible for the machine
    batch_ops = []
    for ord_id in rule.order_ids:
        seq = ctx.order_op_sequences.get(ord_id, [])
        for k in seq:
            alts = ctx.op_alternatives.get(k, [])
            for mid, st, en, pres in alts:
                if mid == rule.machine_id:
                    batch_ops.append((ord_id, st, en, pres))

    if len(batch_ops) < 2:
        return

    # Force all batch ops present on this machine
    for _, _, _, pres in batch_ops:
        if not isinstance(pres, int):
            ctx.model.add(pres == 1)

    # For any operation outside this batch on this machine, it cannot run between
    # the start of the batch and the end of the batch.
    machine_ops = ctx.machine_ops.get(rule.machine_id, [])
    batch_order_set = set(rule.order_ids)

    min_batch_start = ctx.model.new_int_var(0, ctx.horizon, "min_batch_st")
    max_batch_end = ctx.model.new_int_var(0, ctx.horizon, "max_batch_en")
    ctx.model.add_min_equality(min_batch_start, [st for _, st, _, _ in batch_ops])
    ctx.model.add_max_equality(max_batch_end, [en for _, _, en, _ in batch_ops])

    for ord_id, _op_id, st, en, pres, _ in machine_ops:
        if ord_id not in batch_order_set:
            # Must finish before min_batch_start or start after max_batch_end
            b = ctx.model.new_bool_var(f"outside_batch_{ord_id}")
            if isinstance(pres, int):
                ctx.model.add(en <= min_batch_start).only_enforce_if(b)
                ctx.model.add(st >= max_batch_end).only_enforce_if(b.negated())
            else:
                ctx.model.add(en <= min_batch_start).only_enforce_if(b, pres)
                ctx.model.add(st >= max_batch_end).only_enforce_if(b.negated(), pres)
