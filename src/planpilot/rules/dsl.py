"""Rule DSL for PlanPilot production scheduling.

Implements a type-safe discriminated union of domain-specific scheduling rules:
- ForbidSequence: Forbid material transition on a machine unless cleanout gap met.
- MinChangeover: Enforce minimum changeover time on a machine.
- MachineUnavailable: Mark a planned downtime window on a machine.
- PinToMachine: Pin an operation to a specific eligible machine.
- ForbidMachine: Prevent an operation from running on a specific machine.
- OrderPrecedence: Enforce completion-to-start ordering between two orders.
- MaxConsecutiveRuns: Limit consecutive operations of a material family on a machine.
- HardDueDate: Set a strict completion deadline for an order.
- BatchTogether: Require a set of orders to run contiguously on the same machine.
"""

from __future__ import annotations

import enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from planpilot.domain.models import MachineId, MaterialFamily, OperationId, OrderId


class RuleType(enum.StrEnum):
    """Discriminator types for rule models."""

    FORBID_SEQUENCE = "forbid_sequence"
    MIN_CHANGEOVER = "min_changeover"
    MACHINE_UNAVAILABLE = "machine_unavailable"
    PIN_TO_MACHINE = "pin_to_machine"
    FORBID_MACHINE = "forbid_machine"
    ORDER_PRECEDENCE = "order_precedence"
    MAX_CONSECUTIVE_RUNS = "max_consecutive_runs"
    HARD_DUE_DATE = "hard_due_date"
    BATCH_TOGETHER = "batch_together"


class BaseRule(BaseModel):
    """Base model for all scheduling rules."""

    model_config = ConfigDict(frozen=True)

    id: str = ""
    name: str = ""
    description: str = ""
    enabled: bool = True


class ForbidSequence(BaseRule):
    """Forbid running `to_material` after `from_material` on `machine_id` without cleanout."""

    type: Literal[RuleType.FORBID_SEQUENCE] = RuleType.FORBID_SEQUENCE
    machine_id: MachineId
    from_material: MaterialFamily | str
    to_material: MaterialFamily | str
    unless_cleanout_min: int = Field(default=0, ge=0)


class MinChangeover(BaseRule):
    """Enforce a minimum changeover duration whenever switching materials on a machine."""

    type: Literal[RuleType.MIN_CHANGEOVER] = RuleType.MIN_CHANGEOVER
    machine_id: MachineId
    min_setup_min: int = Field(gt=0)


class MachineUnavailable(BaseRule):
    """Downtime / maintenance window during which the machine cannot operate."""

    type: Literal[RuleType.MACHINE_UNAVAILABLE] = RuleType.MACHINE_UNAVAILABLE
    machine_id: MachineId
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    reason: str = ""

    @model_validator(mode="after")
    def start_before_end(self) -> MachineUnavailable:
        if self.start >= self.end:
            raise ValueError(f"start ({self.start}) must be strictly less than end ({self.end})")
        return self


class PinToMachine(BaseRule):
    """Pin an operation to run on a specific eligible machine."""

    type: Literal[RuleType.PIN_TO_MACHINE] = RuleType.PIN_TO_MACHINE
    order_id: OrderId
    operation_id: OperationId
    machine_id: MachineId


class ForbidMachine(BaseRule):
    """Forbid an operation from executing on a specific machine."""

    type: Literal[RuleType.FORBID_MACHINE] = RuleType.FORBID_MACHINE
    order_id: OrderId
    operation_id: OperationId
    machine_id: MachineId


class OrderPrecedence(BaseRule):
    """Order `before_order_id` must finish before `after_order_id` can start."""

    type: Literal[RuleType.ORDER_PRECEDENCE] = RuleType.ORDER_PRECEDENCE
    before_order_id: OrderId
    after_order_id: OrderId
    min_gap_min: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def different_orders(self) -> OrderPrecedence:
        if self.before_order_id == self.after_order_id:
            raise ValueError("before_order_id and after_order_id cannot be the same order")
        return self


class MaxConsecutiveRuns(BaseRule):
    """Limit the number of consecutive runs of a material family on a machine."""

    type: Literal[RuleType.MAX_CONSECUTIVE_RUNS] = RuleType.MAX_CONSECUTIVE_RUNS
    machine_id: MachineId
    material_family: MaterialFamily | str
    max_runs: int = Field(gt=0)


class HardDueDate(BaseRule):
    """Enforce a hard completion deadline on an order."""

    type: Literal[RuleType.HARD_DUE_DATE] = RuleType.HARD_DUE_DATE
    order_id: OrderId
    deadline: int = Field(gt=0)


class BatchTogether(BaseRule):
    """Require a group of orders to execute contiguously on the same machine."""

    type: Literal[RuleType.BATCH_TOGETHER] = RuleType.BATCH_TOGETHER
    order_ids: list[OrderId] = Field(min_length=2)
    machine_id: MachineId | None = None

    @model_validator(mode="after")
    def unique_orders(self) -> BatchTogether:
        if len(self.order_ids) != len(set(self.order_ids)):
            raise ValueError("order_ids must be unique")
        return self


# Discriminated union of all concrete rules
Rule = Annotated[
    ForbidSequence
    | MinChangeover
    | MachineUnavailable
    | PinToMachine
    | ForbidMachine
    | OrderPrecedence
    | MaxConsecutiveRuns
    | HardDueDate
    | BatchTogether,
    Field(discriminator="type"),
]
