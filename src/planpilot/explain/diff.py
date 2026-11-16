"""Schedule diff and movement attribution engine.

Compares Schedule A vs Schedule B:
- Classifies each operation movement (earlier/later, changed machine, setup delta)
- Attributes movements to root causes (rule added, rush order inserted, downtime inserted, knock-on)
- Produces citation-addressable facts [Fn] for explainability
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict

from planpilot.domain.models import Plant, Schedule
from planpilot.explain.facts import Fact, FactType
from planpilot.rules.store import KPIDiff


class MovementType(enum.StrEnum):
    """Classification of assignment displacement between schedules."""

    UNCHANGED = "unchanged"
    MOVED_EARLIER = "moved_earlier"
    MOVED_LATER = "moved_later"
    CHANGED_MACHINE = "changed_machine"
    SETUP_INCREASED = "setup_increased"
    SETUP_DECREASED = "setup_decreased"


class AssignmentDelta(BaseModel):
    """Detailed delta between two schedules for a single operation."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    operation_id: str
    before_machine_id: str
    after_machine_id: str
    before_start: int
    before_end: int
    after_start: int
    after_end: int
    delta_start: int  # after_start - before_start
    delta_end: int  # after_end - before_end
    before_setup: int = 0
    after_setup: int = 0
    movement_type: MovementType
    attributed_cause: str


class ScheduleComparison(BaseModel):
    """Complete structured comparison between two schedules."""

    model_config = ConfigDict(frozen=True)

    kpi_diff: KPIDiff
    deltas: list[AssignmentDelta]
    facts: list[Fact]

    def format_fact_table(self) -> str:
        """Format comparison facts into a citation table."""
        return "\n".join(f"[{f.id}] {f.description}" for f in self.facts)


class ScheduleDiffEngine:
    """Computes differences between schedules and attributes movements to root causes."""

    def compare(
        self,
        before: Schedule,
        after: Schedule,
        plant: Plant,
        reason_hint: str = "",
    ) -> ScheduleComparison:
        """Compare before and after schedules, classifying movements and attributing causes."""
        kpi_diff = KPIDiff.calculate(before.kpis, after.kpis)

        before_map = {(a.order_id, a.operation_id): a for a in before.assignments}
        after_map = {(a.order_id, a.operation_id): a for a in after.assignments}

        all_keys = sorted(set(before_map.keys()) | set(after_map.keys()))
        deltas: list[AssignmentDelta] = []
        facts: list[Fact] = []
        fact_idx = 1

        # Global KPI delta facts
        facts.append(
            Fact.create(
                fact_id=f"F{fact_idx}",
                fact_type=FactType.ROOT_CAUSE,
                description=(
                    f"Makespan changed from {before.kpis.makespan} to {after.kpis.makespan} "
                    f"(delta = {kpi_diff.makespan_delta})"
                ),
            )
        )
        fact_idx += 1

        facts.append(
            Fact.create(
                fact_id=f"F{fact_idx}",
                fact_type=FactType.ROOT_CAUSE,
                description=(
                    f"Total tardiness changed from {before.kpis.total_tardiness} "
                    f"to {after.kpis.total_tardiness} (delta = {kpi_diff.total_tardiness_delta})"
                ),
            )
        )
        fact_idx += 1

        for key in all_keys:
            as_b = before_map.get(key)
            as_a = after_map.get(key)

            if as_b is None or as_a is None:
                continue

            delta_st = as_a.start - as_b.start
            delta_en = as_a.end - as_b.end
            b_setup = as_b.setup_time or 0
            a_setup = as_a.setup_time or 0

            # Determine movement type
            if as_a.machine_id != as_b.machine_id:
                m_type = MovementType.CHANGED_MACHINE
            elif delta_st > 0:
                m_type = MovementType.MOVED_LATER
            elif delta_st < 0:
                m_type = MovementType.MOVED_EARLIER
            elif a_setup > b_setup:
                m_type = MovementType.SETUP_INCREASED
            elif a_setup < b_setup:
                m_type = MovementType.SETUP_DECREASED
            else:
                m_type = MovementType.UNCHANGED

            # Attribution logic
            cause = "No displacement"
            if m_type != MovementType.UNCHANGED:
                if reason_hint:
                    cause = f"Triggered by: {reason_hint}"
                elif m_type == MovementType.CHANGED_MACHINE:
                    cause = f"Reallocated from {as_b.machine_id} to {as_a.machine_id} by solver"
                elif m_type == MovementType.MOVED_LATER:
                    cause = f"Delayed by {delta_st} due to schedule resequencing"
                elif m_type == MovementType.MOVED_EARLIER:
                    cause = f"Advanced by {abs(delta_st)} due to earlier machine availability"

            delta = AssignmentDelta(
                order_id=key[0],
                operation_id=key[1],
                before_machine_id=as_b.machine_id,
                after_machine_id=as_a.machine_id,
                before_start=as_b.start,
                before_end=as_b.end,
                after_start=as_a.start,
                after_end=as_a.end,
                delta_start=delta_st,
                delta_end=delta_en,
                before_setup=b_setup,
                after_setup=a_setup,
                movement_type=m_type,
                attributed_cause=cause,
            )
            deltas.append(delta)

            if m_type != MovementType.UNCHANGED:
                if m_type == MovementType.CHANGED_MACHINE:
                    desc = (
                        f"Operation {key[1]} of order {key[0]} reassigned from "
                        f"machine {as_b.machine_id} to {as_a.machine_id} "
                        f"(start changed from {as_b.start} to {as_a.start})"
                    )
                elif m_type == MovementType.MOVED_LATER:
                    desc = (
                        f"Operation {key[1]} of order {key[0]} moved later by {delta_st} "
                        f"on machine {as_a.machine_id} "
                        f"(start changed from {as_b.start} to {as_a.start})"
                    )
                elif m_type == MovementType.MOVED_EARLIER:
                    desc = (
                        f"Operation {key[1]} of order {key[0]} moved earlier by {abs(delta_st)} "
                        f"on machine {as_a.machine_id} "
                        f"(start changed from {as_b.start} to {as_a.start})"
                    )
                else:
                    desc = (
                        f"Operation {key[1]} of order {key[0]} setup changed from {b_setup} "
                        f"to {a_setup} on machine {as_a.machine_id}"
                    )

                facts.append(
                    Fact.create(
                        fact_id=f"F{fact_idx}",
                        fact_type=FactType.TIGHT_CONSTRAINT,
                        description=desc,
                        order_id=key[0],
                        operation_id=key[1],
                        machine_id=as_a.machine_id,
                    )
                )
                fact_idx += 1

        return ScheduleComparison(
            kpi_diff=kpi_diff,
            deltas=deltas,
            facts=facts,
        )


__all__ = [
    "AssignmentDelta",
    "KPIDiff",
    "MovementType",
    "ScheduleComparison",
    "ScheduleDiffEngine",
]
