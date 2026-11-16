"""Grounded fact extraction and critical-chain lateness tracing from solved schedules.

Extracts typed, citation-addressable facts:
- [F1] Order ORD-1 completed at t=65
- [F2] Order ORD-1 due date was t=40 (tardiness = 25m)
- [F3] Operation OP1 on Machine M1 was delayed by planned downtime from t=10 to t=30
Backtracks tight constraints to isolate exact root causes without LLM guesswork.
"""

from __future__ import annotations

import enum
import re

from pydantic import BaseModel, ConfigDict, Field

from planpilot.domain.models import Plant, Schedule, TightConstraint


class FactType(enum.StrEnum):
    """Categorization of scheduling facts."""

    ORDER_COMPLETION = "order_completion"
    ORDER_DUE_DATE = "order_due_date"
    ORDER_RELEASE = "order_release"
    MACHINE_OCCUPIED = "machine_occupied"
    SETUP_GAP = "setup_gap"
    DOWNTIME_WINDOW = "downtime_window"
    TIGHT_CONSTRAINT = "tight_constraint"
    CRITICAL_CHAIN_STEP = "critical_chain_step"
    ROOT_CAUSE = "root_cause"


class Fact(BaseModel):
    """Typed, citation-addressable fact from the solver output."""

    model_config = ConfigDict(frozen=True)

    id: str  # e.g. "F1", "F2"
    fact_type: FactType
    description: str
    order_id: str | None = None
    operation_id: str | None = None
    machine_id: str | None = None
    numeric_values: list[float] = Field(default_factory=list)

    @classmethod
    def create(
        cls,
        fact_id: str,
        fact_type: FactType,
        description: str,
        order_id: str | None = None,
        operation_id: str | None = None,
        machine_id: str | None = None,
        extra_numbers: list[float | int] | None = None,
    ) -> Fact:
        """Create fact and automatically extract all numeric values from description."""
        # Extract all numbers from description (integers and floats)
        extracted = [float(num) for num in re.findall(r"\b\d+(?:\.\d+)?\b", description)]
        if extra_numbers:
            extracted.extend([float(n) for n in extra_numbers])

        # Remove duplicates while preserving order
        seen: set[float] = set()
        deduped: list[float] = []
        for n in extracted:
            if n not in seen:
                seen.add(n)
                deduped.append(n)

        return cls(
            id=fact_id,
            fact_type=fact_type,
            description=description,
            order_id=order_id,
            operation_id=operation_id,
            machine_id=machine_id,
            numeric_values=deduped,
        )


class CriticalChainStep(BaseModel):
    """Step in the backward critical chain explaining delay."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    operation_id: str
    machine_id: str
    start: int
    end: int
    tight_constraint: TightConstraint
    cause_description: str
    predecessor_order_id: str | None = None
    predecessor_op_id: str | None = None


class LatenessDiagnosis(BaseModel):
    """Diagnosis of why an order is late, grounded in solver decisions."""

    order_id: str
    tardiness: int
    completion_time: int
    due_date: int
    root_cause_type: str  # "competing_order", "release_delay", "changeover_setup", etc.
    root_cause_description: str
    critical_chain: list[CriticalChainStep]
    facts: list[Fact]

    def format_fact_table(self) -> str:
        """Format facts into a numbered citation table for LLM consumption."""
        lines = [f"[{f.id}] {f.description}" for f in self.facts]
        return "\n".join(lines)


class FactExtractor:
    """Extracts ground-truth facts and traces root causes from schedules."""

    def extract_facts(self, schedule: Schedule, plant: Plant) -> list[Fact]:
        """Extract global facts from a solved schedule."""
        facts: list[Fact] = []
        idx = 1

        # Global makespan
        facts.append(
            Fact.create(
                fact_id=f"F{idx}",
                fact_type=FactType.ORDER_COMPLETION,
                description=(
                    f"Schedule makespan is {schedule.kpis.makespan} "
                    f"with {len(schedule.assignments)} operations executed"
                ),
            )
        )
        idx += 1

        # Order completions & due dates
        for order in plant.orders:
            assts = [a for a in schedule.assignments if a.order_id == order.id]
            if not assts:
                continue
            comp_time = max(a.end for a in assts)
            tardiness = max(0, comp_time - order.due_date) if order.due_date else 0

            facts.append(
                Fact.create(
                    fact_id=f"F{idx}",
                    fact_type=FactType.ORDER_COMPLETION,
                    description=f"Order {order.id} completed at {comp_time}",
                    order_id=order.id,
                )
            )
            idx += 1

            if order.due_date:
                facts.append(
                    Fact.create(
                        fact_id=f"F{idx}",
                        fact_type=FactType.ORDER_DUE_DATE,
                        description=(
                            f"Order {order.id} due date was {order.due_date} "
                            f"(tardiness = {tardiness})"
                        ),
                        order_id=order.id,
                    )
                )
                idx += 1

        return facts

    def diagnose_order_lateness(
        self, schedule: Schedule, plant: Plant, order_id: str
    ) -> LatenessDiagnosis:
        """Trace the critical chain backwards from late order to isolate true root cause."""
        order = plant.order_by_id(order_id)
        if order is None:
            raise ValueError(f"Order '{order_id}' not found in plant")

        order_assts = [a for a in schedule.assignments if a.order_id == order_id]
        if not order_assts:
            raise ValueError(f"No assignments found for order '{order_id}'")

        # Sort assignments by sequence
        routing = plant.routing_by_id(order.routing_id)
        op_seq_map: dict[str, int] = {}
        if routing:
            for op in routing.operations:
                op_seq_map[op.id] = op.sequence

        order_assts.sort(key=lambda a: op_seq_map.get(a.operation_id, 0))

        completion_time = max(a.end for a in order_assts)
        due_date = order.due_date or completion_time
        tardiness = max(0, completion_time - due_date)

        critical_chain: list[CriticalChainStep] = []
        facts: list[Fact] = []
        fact_idx = 1

        # [F1] Completion fact
        f_comp = Fact.create(
            fact_id=f"F{fact_idx}",
            fact_type=FactType.ORDER_COMPLETION,
            description=f"Order {order_id} completed at {completion_time}",
            order_id=order_id,
        )
        facts.append(f_comp)
        fact_idx += 1

        # [F2] Due date fact
        f_due = Fact.create(
            fact_id=f"F{fact_idx}",
            fact_type=FactType.ORDER_DUE_DATE,
            description=f"Order {order_id} due date was {due_date} (tardiness = {tardiness})",
            order_id=order_id,
        )
        facts.append(f_due)
        fact_idx += 1

        if order.release_time > 0:
            facts.append(
                Fact.create(
                    fact_id=f"F{fact_idx}",
                    fact_type=FactType.ORDER_RELEASE,
                    description=f"Order {order_id} has release time {order.release_time}",
                    order_id=order_id,
                )
            )
            fact_idx += 1

        # Backtrack along assignments of the order starting from final operation
        root_cause_type = "routing_cascade"
        root_cause_desc = "Accumulation of processing times along the routing exceeded due date"

        # Check for single-operation or multi-operation backtracking
        for curr_asst in reversed(order_assts):
            tight = (
                curr_asst.tight_constraints[0]
                if curr_asst.tight_constraints
                else TightConstraint.NONE
            )

            # Check 1: Did planned downtime block this operation?
            downtime_hit = None
            for dt in plant.downtimes:
                if dt.machine_id == curr_asst.machine_id and (
                    curr_asst.start == dt.end
                    or (curr_asst.start < dt.end and curr_asst.end > dt.start)
                ):
                    downtime_hit = dt
                    break

            if downtime_hit or tight == TightConstraint.DOWNTIME_BOUNDARY:
                dt_start = downtime_hit.start if downtime_hit else 0
                dt_end = downtime_hit.end if downtime_hit else curr_asst.start
                root_cause_type = "downtime_window"
                root_cause_desc = (
                    f"Machine {curr_asst.machine_id} had planned downtime from {dt_start} "
                    f"to {dt_end}, preventing operation {curr_asst.operation_id} from running"
                )
                step = CriticalChainStep(
                    order_id=order_id,
                    operation_id=curr_asst.operation_id,
                    machine_id=curr_asst.machine_id,
                    start=curr_asst.start,
                    end=curr_asst.end,
                    tight_constraint=TightConstraint.DOWNTIME_BOUNDARY,
                    cause_description=root_cause_desc,
                )
                critical_chain.append(step)
                facts.append(
                    Fact.create(
                        fact_id=f"F{fact_idx}",
                        fact_type=FactType.DOWNTIME_WINDOW,
                        description=(
                            f"Machine {curr_asst.machine_id} was down for maintenance "
                            f"from {dt_start} to {dt_end}"
                        ),
                        machine_id=curr_asst.machine_id,
                    )
                )
                fact_idx += 1
                break

            # Check 2: Did setup cleanout push it?
            if curr_asst.setup_time and curr_asst.setup_time > 15:
                root_cause_type = "changeover_setup"
                root_cause_desc = (
                    f"Required material changeover setup of {curr_asst.setup_time} on "
                    f"machine {curr_asst.machine_id} delayed operation {curr_asst.operation_id}"
                )
                step = CriticalChainStep(
                    order_id=order_id,
                    operation_id=curr_asst.operation_id,
                    machine_id=curr_asst.machine_id,
                    start=curr_asst.start,
                    end=curr_asst.end,
                    tight_constraint=TightConstraint.MACHINE_PREDECESSOR_SETUP,
                    cause_description=root_cause_desc,
                )
                critical_chain.append(step)
                facts.append(
                    Fact.create(
                        fact_id=f"F{fact_idx}",
                        fact_type=FactType.SETUP_GAP,
                        description=(
                            f"Operation {curr_asst.operation_id} on machine {curr_asst.machine_id} "
                            f"incurred changeover setup of {curr_asst.setup_time}"
                        ),
                        order_id=order_id,
                        operation_id=curr_asst.operation_id,
                        machine_id=curr_asst.machine_id,
                    )
                )
                fact_idx += 1
                break

            # Check 3: Machine occupied by competing order?
            # Look for predecessor assignment on the same machine
            machine_assts = [
                a
                for a in schedule.assignments
                if a.machine_id == curr_asst.machine_id and a.end <= curr_asst.start
            ]
            if machine_assts:
                prev_on_machine = max(machine_assts, key=lambda a: a.end)
                if prev_on_machine.order_id != order_id:
                    # An order from another customer/job occupied the machine
                    other_order = plant.order_by_id(prev_on_machine.order_id)
                    is_competing = False
                    if other_order and order:
                        # Higher priority or earlier arrival
                        is_competing = True

                    if is_competing and (
                        prev_on_machine.end == curr_asst.start
                        or tight == TightConstraint.MACHINE_PREDECESSOR_SETUP
                    ):
                        root_cause_type = "competing_order"
                        root_cause_desc = (
                            f"Machine {curr_asst.machine_id} was occupied by "
                            f"order {prev_on_machine.order_id} "
                            f"from {prev_on_machine.start} to {prev_on_machine.end}, "
                            f"delaying operation {curr_asst.operation_id}"
                        )
                        step = CriticalChainStep(
                            order_id=order_id,
                            operation_id=curr_asst.operation_id,
                            machine_id=curr_asst.machine_id,
                            start=curr_asst.start,
                            end=curr_asst.end,
                            tight_constraint=TightConstraint.MACHINE_PREDECESSOR_SETUP,
                            cause_description=root_cause_desc,
                            predecessor_order_id=prev_on_machine.order_id,
                            predecessor_op_id=prev_on_machine.operation_id,
                        )
                        critical_chain.append(step)
                        facts.append(
                            Fact.create(
                                fact_id=f"F{fact_idx}",
                                fact_type=FactType.MACHINE_OCCUPIED,
                                description=(
                                    f"Machine {curr_asst.machine_id} was occupied by order "
                                    f"{prev_on_machine.order_id} from {prev_on_machine.start} "
                                    f"to {prev_on_machine.end}"
                                ),
                                order_id=prev_on_machine.order_id,
                                machine_id=curr_asst.machine_id,
                            )
                        )
                        fact_idx += 1
                        break

            # Check 4: Was release time the binding bottleneck?
            if (
                tight == TightConstraint.RELEASE_TIME
                or (curr_asst == order_assts[0] and curr_asst.start == order.release_time)
            ) and order.release_time > 0 and (due_date - order.release_time) < sum(
                a.processing_time for a in order_assts
            ):
                root_cause_type = "release_delay"
                root_cause_desc = (
                    f"Order {order_id} had late release time of {order.release_time}, "
                    f"leaving only {due_date - order.release_time} before due date {due_date}"
                )
                step = CriticalChainStep(
                    order_id=order_id,
                    operation_id=curr_asst.operation_id,
                    machine_id=curr_asst.machine_id,
                    start=curr_asst.start,
                    end=curr_asst.end,
                    tight_constraint=TightConstraint.RELEASE_TIME,
                    cause_description=root_cause_desc,
                )
                critical_chain.append(step)
                break

            # Record routing step
            critical_chain.append(
                CriticalChainStep(
                    order_id=order_id,
                    operation_id=curr_asst.operation_id,
                    machine_id=curr_asst.machine_id,
                    start=curr_asst.start,
                    end=curr_asst.end,
                    tight_constraint=tight,
                    cause_description=(
                        f"Operation {curr_asst.operation_id} executed from "
                        f"{curr_asst.start} to {curr_asst.end} on {curr_asst.machine_id}"
                    ),
                )
            )

        # Total cumulative duration check for routing cascade
        total_proc = sum(a.processing_time for a in order_assts)
        facts.append(
            Fact.create(
                fact_id=f"F{fact_idx}",
                fact_type=FactType.TIGHT_CONSTRAINT,
                description=(
                    f"Order {order_id} total processing time across "
                    f"routing operations is {total_proc}"
                ),
                order_id=order_id,
            )
        )
        fact_idx += 1

        # Summary root cause fact
        facts.append(
            Fact.create(
                fact_id=f"F{fact_idx}",
                fact_type=FactType.ROOT_CAUSE,
                description=f"Root cause of lateness for order {order_id}: {root_cause_desc}",
                order_id=order_id,
            )
        )

        return LatenessDiagnosis(
            order_id=order_id,
            tardiness=tardiness,
            completion_time=completion_time,
            due_date=due_date,
            root_cause_type=root_cause_type,
            root_cause_description=root_cause_desc,
            critical_chain=critical_chain,
            facts=facts,
        )
