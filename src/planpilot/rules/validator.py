"""Deterministic multi-stage validation pipeline for scheduling rules.

Implements the five-stage validation pipeline:
1. Schema validation (Pydantic model validation)
2. Entity existence resolution (fuzzy matching against plant entities)
3. Semantic checks (valid bounds, non-trivial windows)
4. Conflict detection against active rules (cycle detection, contradictory pins, etc.)
5. Feasibility check (re-solving with a short time limit + assumption core extraction)
"""

from __future__ import annotations

import difflib
import enum
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from planpilot.domain.models import MaterialFamily, Plant, SolverStatus
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
)


class ValidationStage(enum.StrEnum):
    """Validation stages in the pipeline."""

    SCHEMA = "schema"
    ENTITY = "entity"
    SEMANTIC = "semantic"
    CONFLICT = "conflict"
    FEASIBILITY = "feasibility"


class ValidationResult(BaseModel):
    """Result of validating a scheduling rule."""

    valid: bool
    stage: ValidationStage | None = None
    error: str | None = None
    resolved_rule: Rule | None = None
    infeasibility_core: list[str] = Field(default_factory=list)


class RuleValidator:
    """Multi-stage validator for scheduling rules against a target Plant."""

    def __init__(self, plant: Plant) -> None:
        self.plant = plant
        self._machine_ids = {m.id for m in plant.machines}
        self._order_ids = {o.id for o in plant.orders}
        self._material_families = {f.value.lower() for f in MaterialFamily}
        self._operation_ids: set[tuple[str, str]] = set()
        for o in plant.orders:
            r = plant.routing_by_id(o.routing_id)
            if r:
                for op in r.operations:
                    self._operation_ids.add((o.id, op.id))

    def resolve_entity(self, query: str, entity_type: str) -> str | None:
        """Alias for fuzzy_match_entity."""
        return self.fuzzy_match_entity(query, entity_type)

    def fuzzy_match_entity(self, query: str, entity_type: str) -> str | None:
        """Fuzzy match natural language entity mention to plant entity ID."""
        q = query.strip().lower()
        if entity_type == "machine":
            for mid in self._machine_ids:
                if q == mid.lower() or q in mid.lower():
                    return mid
                m = self.plant.machine_by_id(mid)
                if m and q == m.name.lower():
                    return mid
            matches = difflib.get_close_matches(
                q, [m.lower() for m in self._machine_ids], n=1, cutoff=0.5
            )
            if matches:
                for mid in self._machine_ids:
                    if mid.lower() == matches[0]:
                        return mid

        elif entity_type == "material":
            for f in MaterialFamily:
                if q == f.value.lower() or f.value.lower() in q:
                    return f.value
            matches = difflib.get_close_matches(q, list(self._material_families), n=1, cutoff=0.5)
            if matches:
                return matches[0]

        elif entity_type == "order":
            for oid in self._order_ids:
                if q == oid.lower() or q in oid.lower():
                    return oid
            matches = difflib.get_close_matches(
                q, [o.lower() for o in self._order_ids], n=1, cutoff=0.5
            )
            if matches:
                for oid in self._order_ids:
                    if oid.lower() == matches[0]:
                        return oid

        return None

    def validate(
        self,
        candidate_rule_data: dict[str, Any] | Rule,
        active_rules: list[Rule] | None = None,
        check_feasibility: bool = True,
        feasibility_time_limit: float = 3.0,
    ) -> ValidationResult:
        """Run all 5 validation stages on a candidate rule."""
        active = active_rules or []

        # Stage 1: Schema validation
        rule: Rule
        if isinstance(candidate_rule_data, dict):
            try:
                from pydantic import TypeAdapter

                adapter = TypeAdapter(Rule)
                rule = adapter.validate_python(candidate_rule_data)
            except ValidationError as e:
                return ValidationResult(
                    valid=False,
                    stage=ValidationStage.SCHEMA,
                    error=f"Schema validation failed: {e.errors()[0]['msg']}",
                )
        else:
            rule = candidate_rule_data

        # Stage 2: Entity existence resolution
        entity_error = self._check_entities(rule)
        if entity_error:
            return ValidationResult(
                valid=False,
                stage=ValidationStage.ENTITY,
                error=entity_error,
            )

        # Stage 3: Semantic checks
        semantic_error = self._check_semantics(rule)
        if semantic_error:
            return ValidationResult(
                valid=False,
                stage=ValidationStage.SEMANTIC,
                error=semantic_error,
            )

        # Stage 4: Conflict detection against active rules
        conflict_error = self._check_conflicts(rule, active)
        if conflict_error:
            return ValidationResult(
                valid=False,
                stage=ValidationStage.CONFLICT,
                error=conflict_error,
            )

        # Stage 5: Feasibility check (re-solve)
        if check_feasibility:
            from planpilot.solver.scheduler import solve

            schedule = solve(
                self.plant,
                rules=active + [rule],
                time_limit_seconds=feasibility_time_limit,
                diagnose_if_infeasible=True,
            )
            if schedule.solver_stats.status == SolverStatus.INFEASIBLE:
                core = schedule.solver_stats.infeasibility_core
                return ValidationResult(
                    valid=False,
                    stage=ValidationStage.FEASIBILITY,
                    error="Rule causes the schedule to become mathematically infeasible",
                    infeasibility_core=core,
                )

        return ValidationResult(
            valid=True,
            resolved_rule=rule,
        )

    def _check_entities(self, rule: Rule) -> str | None:
        """Verify that all referenced entities exist in the plant."""
        if (
            isinstance(
                rule, (ForbidSequence, MinChangeover, MachineUnavailable, MaxConsecutiveRuns)
            )
            and rule.machine_id not in self._machine_ids
        ):
            return f"Machine '{rule.machine_id}' does not exist in plant"

        if isinstance(rule, ForbidSequence):
            f_val = str(
                rule.from_material.value
                if hasattr(rule.from_material, "value")
                else rule.from_material
            ).lower()
            t_val = str(
                rule.to_material.value if hasattr(rule.to_material, "value") else rule.to_material
            ).lower()
            if f_val not in self._material_families:
                return f"Material family '{rule.from_material}' is not recognized"
            if t_val not in self._material_families:
                return f"Material family '{rule.to_material}' is not recognized"

        if isinstance(rule, MaxConsecutiveRuns):
            m_val = str(
                rule.material_family.value
                if hasattr(rule.material_family, "value")
                else rule.material_family
            ).lower()
            if m_val not in self._material_families:
                return f"Material family '{rule.material_family}' is not recognized"

        if isinstance(rule, (PinToMachine, ForbidMachine)):
            if rule.machine_id not in self._machine_ids:
                return f"Machine '{rule.machine_id}' does not exist in plant"
            if rule.order_id not in self._order_ids:
                return f"Order '{rule.order_id}' does not exist in plant"
            if (rule.order_id, rule.operation_id) not in self._operation_ids:
                return f"Operation '{rule.operation_id}' does not exist for order '{rule.order_id}'"

        if isinstance(rule, OrderPrecedence):
            if rule.before_order_id not in self._order_ids:
                return f"Order '{rule.before_order_id}' does not exist in plant"
            if rule.after_order_id not in self._order_ids:
                return f"Order '{rule.after_order_id}' does not exist in plant"

        if isinstance(rule, HardDueDate) and rule.order_id not in self._order_ids:
            return f"Order '{rule.order_id}' does not exist in plant"

        if isinstance(rule, BatchTogether):
            for oid in rule.order_ids:
                if oid not in self._order_ids:
                    return f"Order '{oid}' in batch does not exist in plant"
            if rule.machine_id and rule.machine_id not in self._machine_ids:
                return f"Machine '{rule.machine_id}' does not exist in plant"

        return None

    def _check_semantics(self, rule: Rule) -> str | None:
        """Verify semantic invariants."""
        if isinstance(rule, MachineUnavailable) and rule.end > self.plant.horizon:
            return (
                f"Unavailable window end ({rule.end}) exceeds plant horizon ({self.plant.horizon})"
            )

        if isinstance(rule, HardDueDate):
            order = self.plant.order_by_id(rule.order_id)
            if order and order.release_time >= rule.deadline:
                return (
                    f"Hard due date ({rule.deadline}) <= release time ({order.release_time})"
                )

        if isinstance(rule, OrderPrecedence) and rule.before_order_id == rule.after_order_id:
            return "Precedence cannot be established between an order and itself"

        return None

    def _check_conflicts(self, rule: Rule, active_rules: list[Rule]) -> str | None:
        """Detect direct contradictions and cyclic dependencies against active rules."""
        for active in active_rules:
            if not active.enabled:
                continue

            # Pin vs Forbid conflict
            if (
                isinstance(rule, PinToMachine)
                and isinstance(active, ForbidMachine)
                and rule.order_id == active.order_id
                and rule.operation_id == active.operation_id
                and rule.machine_id == active.machine_id
            ):
                return (
                    f"Conflict: Operation {rule.operation_id} of order {rule.order_id} "
                    f"is forbidden on machine {rule.machine_id} by existing rule"
                )

            if (
                isinstance(rule, ForbidMachine)
                and isinstance(active, PinToMachine)
                and rule.order_id == active.order_id
                and rule.operation_id == active.operation_id
                and rule.machine_id == active.machine_id
            ):
                return (
                    f"Conflict: Operation {rule.operation_id} of order {rule.order_id} "
                    f"is already pinned to machine {rule.machine_id} by existing rule"
                )

            # Conflicting Pin to different machines
            if (
                isinstance(rule, PinToMachine)
                and isinstance(active, PinToMachine)
                and (
                    rule.order_id == active.order_id
                    and rule.operation_id == active.operation_id
                    and rule.machine_id != active.machine_id
                )
            ):
                return (
                    f"Conflict: Operation {rule.operation_id} is already pinned to "
                    f"{active.machine_id}, cannot pin to {rule.machine_id}"
                )

            # Precedence cycle detection: A -> B and B -> A
            if (
                isinstance(rule, OrderPrecedence)
                and isinstance(active, OrderPrecedence)
                and rule.before_order_id == active.after_order_id
                and rule.after_order_id == active.before_order_id
            ):
                return (
                    f"Conflict: OrderPrecedence cycle detected between "
                    f"{rule.before_order_id} and {rule.after_order_id}"
                )

        # General cycle detection for OrderPrecedence
        if isinstance(rule, OrderPrecedence):
            edges: dict[str, list[str]] = {}
            for r in active_rules:
                if isinstance(r, OrderPrecedence) and r.enabled:
                    edges.setdefault(r.before_order_id, []).append(r.after_order_id)
            edges.setdefault(rule.before_order_id, []).append(rule.after_order_id)

            visited = set()
            stack = [rule.after_order_id]
            while stack:
                curr = stack.pop()
                if curr == rule.before_order_id:
                    return (
                        f"Conflict: Precedence {rule.before_order_id} -> {rule.after_order_id} "
                        f"creates an indirect cycle"
                    )
                if curr not in visited:
                    visited.add(curr)
                    stack.extend(edges.get(curr, []))

        return None
