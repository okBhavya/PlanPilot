"""Versioned rule store and schedule comparison engine.

Manages versioned sets of scheduling rules and calculates before/after
schedule and KPI diffs when rules are applied or modified.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from planpilot.domain.models import KPIs, Plant, Schedule
from planpilot.rules.dsl import Rule
from planpilot.rules.validator import RuleValidator, ValidationResult


class KPIDiff(BaseModel):
    """Difference in KPIs between before and after schedules."""

    makespan_delta: int
    total_tardiness_delta: int
    weighted_tardiness_delta: float
    on_time_pct_delta: float
    total_setup_time_delta: int
    throughput_delta: float

    @classmethod
    def calculate(cls, before: KPIs, after: KPIs) -> KPIDiff:
        """Calculate signed deltas (after - before)."""
        return cls(
            makespan_delta=after.makespan - before.makespan,
            total_tardiness_delta=after.total_tardiness - before.total_tardiness,
            weighted_tardiness_delta=round(after.weighted_tardiness - before.weighted_tardiness, 2),
            on_time_pct_delta=round(after.on_time_pct - before.on_time_pct, 2),
            total_setup_time_delta=after.total_setup_time - before.total_setup_time,
            throughput_delta=round(after.throughput - before.throughput, 4),
        )


class AssignmentDiff(BaseModel):
    """Difference in an individual operation assignment between schedules."""

    order_id: str
    operation_id: str
    before_machine_id: str | None
    after_machine_id: str | None
    before_start: int | None
    after_start: int | None
    start_delta: int = 0
    machine_changed: bool = False


class ScheduleDiff(BaseModel):
    """Complete diff comparing two schedules."""

    kpi_diff: KPIDiff
    assignment_diffs: list[AssignmentDiff] = Field(default_factory=list)
    num_jobs_shifted: int = 0
    num_machines_reassigned: int = 0

    @classmethod
    def compare(cls, before: Schedule, after: Schedule) -> ScheduleDiff:
        """Compare before and after schedules."""
        kpi_diff = KPIDiff.calculate(before.kpis, after.kpis)

        before_map = {(a.order_id, a.operation_id): a for a in before.assignments}
        after_map = {(a.order_id, a.operation_id): a for a in after.assignments}

        all_keys = sorted(set(before_map.keys()) | set(after_map.keys()))
        diffs: list[AssignmentDiff] = []
        shifts = 0
        reassignments = 0

        for key in all_keys:
            a_bef = before_map.get(key)
            a_aft = after_map.get(key)

            bef_m = a_bef.machine_id if a_bef else None
            aft_m = a_aft.machine_id if a_aft else None
            bef_st = a_bef.start if a_bef else None
            aft_st = a_aft.start if a_aft else None

            st_delta = (aft_st - bef_st) if (aft_st is not None and bef_st is not None) else 0
            m_changed = bef_m != aft_m

            if st_delta != 0:
                shifts += 1
            if m_changed:
                reassignments += 1

            diffs.append(
                AssignmentDiff(
                    order_id=key[0],
                    operation_id=key[1],
                    before_machine_id=bef_m,
                    after_machine_id=aft_m,
                    before_start=bef_st,
                    after_start=aft_st,
                    start_delta=st_delta,
                    machine_changed=m_changed,
                )
            )

        return cls(
            kpi_diff=kpi_diff,
            assignment_diffs=diffs,
            num_jobs_shifted=shifts,
            num_machines_reassigned=reassignments,
        )


class RuleVersion(BaseModel):
    """Snapshot of a rule change with metadata."""

    version: int
    rule: Rule
    applied_at: datetime
    author: str = "system"
    comment: str = ""


class RuleStore:
    """Versioned store managing active and historical rules for a plant."""

    def __init__(self, plant: Plant) -> None:
        self.plant = plant
        self._rules: dict[str, Rule] = {}
        self._history: list[RuleVersion] = []
        self._version_counter: int = 0
        self.validator = RuleValidator(plant)

    @property
    def rules(self) -> list[Rule]:
        """Return list of active rules."""
        return [r for r in self._rules.values() if r.enabled]

    @property
    def all_rules(self) -> list[Rule]:
        """Return all rules (enabled and disabled)."""
        return list(self._rules.values())

    @property
    def history(self) -> list[RuleVersion]:
        """Return full version history of applied rules."""
        return list(self._history)

    def add_rule(
        self,
        candidate: dict[str, Any] | Rule,
        author: str = "system",
        comment: str = "",
        check_feasibility: bool = True,
    ) -> tuple[ValidationResult, Rule | None]:
        """Validate and add a rule to the store."""
        active = self.rules
        res = self.validator.validate(
            candidate, active_rules=active, check_feasibility=check_feasibility
        )

        if not res.valid or res.resolved_rule is None:
            return res, None

        rule = res.resolved_rule
        rule_id = rule.id or f"RULE-{self._version_counter + 1:04d}"
        # Store copy with assigned ID
        stored_rule = rule.model_copy(update={"id": rule_id})
        self._rules[rule_id] = stored_rule

        self._version_counter += 1
        self._history.append(
            RuleVersion(
                version=self._version_counter,
                rule=stored_rule,
                applied_at=datetime.now(UTC),
                author=author,
                comment=comment,
            )
        )

        return res, stored_rule

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID."""
        if rule_id in self._rules:
            del self._rules[rule_id]
            return True
        return False

    def disable_rule(self, rule_id: str) -> bool:
        """Disable a rule without removing it."""
        if rule_id in self._rules:
            self._rules[rule_id] = self._rules[rule_id].model_copy(update={"enabled": False})
            return True
        return False

    def enable_rule(self, rule_id: str) -> bool:
        """Enable a previously disabled rule."""
        if rule_id in self._rules:
            self._rules[rule_id] = self._rules[rule_id].model_copy(update={"enabled": True})
            return True
        return False

    def evaluate_rule_impact(
        self,
        candidate_rule: Rule,
        time_limit_seconds: float = 10.0,
    ) -> tuple[Schedule, Schedule, ScheduleDiff]:
        """Compute before and after schedules and return the full ScheduleDiff."""
        from planpilot.solver.scheduler import solve

        before_schedule = solve(
            self.plant,
            rules=self.rules,
            time_limit_seconds=time_limit_seconds,
        )
        after_schedule = solve(
            self.plant,
            rules=self.rules + [candidate_rule],
            time_limit_seconds=time_limit_seconds,
        )
        diff = ScheduleDiff.compare(before_schedule, after_schedule)
        return before_schedule, after_schedule, diff
