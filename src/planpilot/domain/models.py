"""Core domain models for PlanPilot production scheduling.

All models use Pydantic v2 with strict validation. These represent the
manufacturing domain: plants with work centers and machines, materials,
BOMs, routings with operations, orders, changeover matrices, schedules,
and KPIs.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Identifiers — typed aliases for readability
# ---------------------------------------------------------------------------
MachineId = str
WorkCenterId = str
MaterialId = str
OperationId = str
OrderId = str
RoutingId = str
PlantId = str
BOMId = str
ItemId = str

PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0.0)]
NonNegativeFloat = Annotated[float, Field(ge=0.0)]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class OrderStatus(enum.StrEnum):
    """Order lifecycle status."""

    PENDING = "pending"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OrderPriority(int, enum.Enum):
    """Order priority levels (higher number = higher priority)."""

    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


class SolverStatus(enum.StrEnum):
    """CP-SAT solver result status."""

    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    MODEL_INVALID = "model_invalid"
    UNKNOWN = "unknown"


class ObjectiveType(enum.StrEnum):
    """Supported scheduling objective functions."""

    MAKESPAN = "makespan"
    TOTAL_WEIGHTED_TARDINESS = "total_weighted_tardiness"
    LEXICOGRAPHIC = "lexicographic"


# ---------------------------------------------------------------------------
# Manufacturing domain models
# ---------------------------------------------------------------------------
class Machine(BaseModel):
    """A single machine within a work center."""

    model_config = ConfigDict(frozen=True)

    id: MachineId
    name: str = ""
    work_center_id: WorkCenterId
    speed_factor: PositiveFloat = 1.0

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Machine id must not be empty")
        return v.strip()


class DowntimeWindow(BaseModel):
    """A planned downtime window for a machine."""

    model_config = ConfigDict(frozen=True)

    machine_id: MachineId
    start: NonNegativeInt  # time units from horizon start
    end: PositiveInt  # time units from horizon start
    reason: str = ""

    @model_validator(mode="after")
    def start_before_end(self) -> DowntimeWindow:
        if self.start >= self.end:
            raise ValueError(f"Downtime start ({self.start}) must be before end ({self.end})")
        return self


class WorkCenter(BaseModel):
    """A group of interchangeable (or related) machines."""

    model_config = ConfigDict(frozen=True)

    id: WorkCenterId
    name: str = ""
    plant_id: PlantId = ""
    machine_ids: list[MachineId] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("WorkCenter id must not be empty")
        return v.strip()


class MaterialFamily(enum.StrEnum):
    """Material families for changeover logic."""

    STEEL = "steel"
    ALUMINIUM = "aluminium"
    TITANIUM = "titanium"
    COPPER = "copper"
    PLASTIC = "plastic"
    COMPOSITE = "composite"
    GENERIC = "generic"


class Material(BaseModel):
    """A material / item in the inventory."""

    model_config = ConfigDict(frozen=True)

    id: MaterialId
    name: str = ""
    family: MaterialFamily = MaterialFamily.GENERIC
    unit: str = "EA"

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Material id must not be empty")
        return v.strip()


class BOMComponent(BaseModel):
    """A single component entry in a bill of materials."""

    model_config = ConfigDict(frozen=True)

    material_id: MaterialId
    quantity: PositiveFloat
    unit: str = "EA"


class BOM(BaseModel):
    """Bill of Materials for a finished/semi-finished item."""

    model_config = ConfigDict(frozen=True)

    id: BOMId
    product_material_id: MaterialId
    components: list[BOMComponent] = Field(default_factory=list, min_length=0)

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("BOM id must not be empty")
        return v.strip()


class Operation(BaseModel):
    """A single operation in a routing (one step of manufacturing)."""

    model_config = ConfigDict(frozen=True)

    id: OperationId
    sequence: PositiveInt  # 10, 20, 30, ...
    name: str = ""
    eligible_machine_ids: list[MachineId] = Field(min_length=1)
    processing_time: PositiveInt  # time units
    setup_time: NonNegativeInt = 0  # base setup time (may be overridden by changeover matrix)
    material_id: MaterialId | None = None  # material being processed (for changeover logic)

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Operation id must not be empty")
        return v.strip()

    @field_validator("eligible_machine_ids")
    @classmethod
    def no_duplicate_machines(cls, v: list[MachineId]) -> list[MachineId]:
        if len(v) != len(set(v)):
            raise ValueError("Duplicate machine IDs in eligible_machine_ids")
        return v


class Routing(BaseModel):
    """A manufacturing routing: an ordered sequence of operations."""

    model_config = ConfigDict(frozen=True)

    id: RoutingId
    product_material_id: MaterialId
    operations: list[Operation] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Routing id must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def operations_ordered_by_sequence(self) -> Routing:
        seqs = [op.sequence for op in self.operations]
        if seqs != sorted(seqs):
            raise ValueError("Operations must be ordered by sequence number")
        if len(seqs) != len(set(seqs)):
            raise ValueError("Operation sequence numbers must be unique within a routing")
        return self

    def operation_ids(self) -> list[OperationId]:
        """Return ordered list of operation IDs."""
        return [op.id for op in self.operations]


class Order(BaseModel):
    """A production order (job) to be scheduled."""

    model_config = ConfigDict(frozen=True)

    id: OrderId
    routing_id: RoutingId
    quantity: PositiveInt = 1
    priority: OrderPriority = OrderPriority.NORMAL
    release_time: NonNegativeInt = 0  # earliest start (time units from horizon start)
    due_date: PositiveInt | None = None  # deadline (time units from horizon start)
    weight: PositiveFloat = 1.0  # for weighted tardiness
    status: OrderStatus = OrderStatus.PENDING
    material_id: MaterialId | None = None  # output material

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Order id must not be empty")
        return v.strip()


class ChangeoverEntry(BaseModel):
    """Setup time when switching between material families on a machine."""

    model_config = ConfigDict(frozen=True)

    machine_id: MachineId
    from_material: MaterialFamily
    to_material: MaterialFamily
    setup_time: NonNegativeInt  # additional setup time units

    @model_validator(mode="after")
    def not_same_material(self) -> ChangeoverEntry:
        # same-material changeover is allowed (could be 0 or non-zero)
        return self


class ChangeoverMatrix(BaseModel):
    """Sequence-dependent setup times for machine-material transitions."""

    model_config = ConfigDict(frozen=True)

    entries: list[ChangeoverEntry] = Field(default_factory=list)

    def get_setup_time(
        self, machine_id: MachineId, from_mat: MaterialFamily, to_mat: MaterialFamily
    ) -> int:
        """Look up changeover time; returns 0 if no entry found."""
        for e in self.entries:
            if (
                e.machine_id == machine_id
                and e.from_material == from_mat
                and e.to_material == to_mat
            ):
                return e.setup_time
        return 0

    def get_setup_time_any_machine(self, from_mat: MaterialFamily, to_mat: MaterialFamily) -> int:
        """Get the max changeover for any machine (conservative estimate)."""
        times = [
            e.setup_time
            for e in self.entries
            if e.from_material == from_mat and e.to_material == to_mat
        ]
        return max(times) if times else 0


# ---------------------------------------------------------------------------
# Plant: the top-level container
# ---------------------------------------------------------------------------
class Plant(BaseModel):
    """A manufacturing plant with all data needed for scheduling."""

    model_config = ConfigDict(frozen=True)

    id: PlantId
    name: str = ""
    machines: list[Machine] = Field(default_factory=list)
    work_centers: list[WorkCenter] = Field(default_factory=list)
    materials: list[Material] = Field(default_factory=list)
    boms: list[BOM] = Field(default_factory=list)
    routings: list[Routing] = Field(default_factory=list)
    orders: list[Order] = Field(default_factory=list)
    changeover_matrix: ChangeoverMatrix = Field(default_factory=ChangeoverMatrix)
    downtimes: list[DowntimeWindow] = Field(default_factory=list)
    horizon: PositiveInt = 10000  # scheduling horizon in time units

    @field_validator("id")
    @classmethod
    def id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Plant id must not be empty")
        return v.strip()

    def machine_by_id(self, machine_id: MachineId) -> Machine | None:
        """Look up a machine by ID."""
        for m in self.machines:
            if m.id == machine_id:
                return m
        return None

    def routing_by_id(self, routing_id: RoutingId) -> Routing | None:
        """Look up a routing by ID."""
        for r in self.routings:
            if r.id == routing_id:
                return r
        return None

    def order_by_id(self, order_id: OrderId) -> Order | None:
        """Look up an order by ID."""
        for o in self.orders:
            if o.id == order_id:
                return o
        return None

    def material_by_id(self, material_id: MaterialId) -> Material | None:
        """Look up a material by ID."""
        for m in self.materials:
            if m.id == material_id:
                return m
        return None

    def machine_ids(self) -> list[MachineId]:
        """Return all machine IDs."""
        return [m.id for m in self.machines]

    def validate_referential_integrity(self) -> list[str]:
        """Check cross-references and return a list of error messages.

        Checks:
        - Operations reference existing machines
        - Routings reference existing materials
        - Orders reference existing routings
        - Downtimes reference existing machines
        - Changeover entries reference existing machines
        - Work centers reference existing machines
        - BOM components reference existing materials
        """
        errors: list[str] = []
        machine_ids = {m.id for m in self.machines}
        material_ids = {m.id for m in self.materials}
        routing_ids = {r.id for r in self.routings}

        # Routing operations → machines
        for routing in self.routings:
            if routing.product_material_id not in material_ids:
                errors.append(
                    f"Routing {routing.id} references unknown material "
                    f"{routing.product_material_id}"
                )
            for op in routing.operations:
                for mid in op.eligible_machine_ids:
                    if mid not in machine_ids:
                        errors.append(
                            f"Operation {op.id} in routing {routing.id} references "
                            f"unknown machine {mid}"
                        )
                if op.material_id and op.material_id not in material_ids:
                    errors.append(f"Operation {op.id} references unknown material {op.material_id}")

        # Orders → routings
        for order in self.orders:
            if order.routing_id not in routing_ids:
                errors.append(f"Order {order.id} references unknown routing {order.routing_id}")
            if order.material_id and order.material_id not in material_ids:
                errors.append(f"Order {order.id} references unknown material {order.material_id}")

        # Downtimes → machines
        for dt in self.downtimes:
            if dt.machine_id not in machine_ids:
                errors.append(f"Downtime references unknown machine {dt.machine_id}")

        # Changeover entries → machines
        for entry in self.changeover_matrix.entries:
            if entry.machine_id not in machine_ids:
                errors.append(f"Changeover entry references unknown machine {entry.machine_id}")

        # Work centers → machines
        for wc in self.work_centers:
            for mid in wc.machine_ids:
                if mid not in machine_ids:
                    errors.append(f"Work center {wc.id} references unknown machine {mid}")

        # BOM components → materials
        for bom in self.boms:
            if bom.product_material_id not in material_ids:
                errors.append(
                    f"BOM {bom.id} references unknown product material {bom.product_material_id}"
                )
            for comp in bom.components:
                if comp.material_id not in material_ids:
                    errors.append(
                        f"BOM {bom.id} component references unknown material {comp.material_id}"
                    )

        return errors


# ---------------------------------------------------------------------------
# Schedule / Assignment (solver output)
# ---------------------------------------------------------------------------
class TightConstraint(enum.StrEnum):
    """Types of constraints that can be tight (binding) on an assignment."""

    PREDECESSOR_END = "predecessor_end"
    MACHINE_PREDECESSOR_SETUP = "machine_predecessor_setup"
    DOWNTIME_BOUNDARY = "downtime_boundary"
    RELEASE_TIME = "release_time"
    NONE = "none"


class Assignment(BaseModel):
    """Assignment of an operation to a machine with start/end times."""

    model_config = ConfigDict(frozen=True)

    order_id: OrderId
    operation_id: OperationId
    machine_id: MachineId
    start: NonNegativeInt
    end: PositiveInt
    setup_start: NonNegativeInt | None = None  # if there's a changeover setup before this op
    setup_time: NonNegativeInt = 0
    processing_time: PositiveInt = 1
    tight_constraints: list[TightConstraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def start_before_end(self) -> Assignment:
        if self.start >= self.end:
            raise ValueError(f"Assignment start ({self.start}) must be before end ({self.end})")
        return self


class KPIs(BaseModel):
    """Key Performance Indicators for a schedule."""

    model_config = ConfigDict(frozen=True)

    makespan: NonNegativeInt = 0
    total_tardiness: NonNegativeInt = 0
    weighted_tardiness: NonNegativeFloat = 0.0
    on_time_pct: float = Field(default=100.0, ge=0.0, le=100.0)
    num_tardy_orders: NonNegativeInt = 0
    total_setup_time: NonNegativeInt = 0
    throughput: NonNegativeFloat = 0.0  # orders per time unit
    machine_utilization: dict[MachineId, float] = Field(default_factory=dict)
    avg_utilization: float = Field(default=0.0, ge=0.0, le=100.0)


class SolverStats(BaseModel):
    """Statistics from the CP-SAT solver run."""

    model_config = ConfigDict(frozen=True)

    status: SolverStatus = SolverStatus.UNKNOWN
    objective_value: int | None = None
    best_bound: int | None = None
    gap_pct: float | None = None
    wall_time_seconds: float = 0.0
    num_variables: NonNegativeInt = 0
    num_constraints: NonNegativeInt = 0
    num_branches: NonNegativeInt = 0
    infeasibility_core: list[str] = Field(default_factory=list)


class Schedule(BaseModel):
    """A complete schedule: assignments + KPIs + solver stats."""

    model_config = ConfigDict(frozen=True)

    id: str = ""
    plant_id: PlantId = ""
    assignments: list[Assignment] = Field(default_factory=list)
    kpis: KPIs = Field(default_factory=KPIs)
    solver_stats: SolverStats = Field(default_factory=SolverStats)
    objective_type: ObjectiveType = ObjectiveType.MAKESPAN
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def assignments_for_order(self, order_id: OrderId) -> list[Assignment]:
        """Get all assignments for a specific order, sorted by start time."""
        return sorted(
            [a for a in self.assignments if a.order_id == order_id],
            key=lambda a: a.start,
        )

    def assignments_for_machine(self, machine_id: MachineId) -> list[Assignment]:
        """Get all assignments on a specific machine, sorted by start time."""
        return sorted(
            [a for a in self.assignments if a.machine_id == machine_id],
            key=lambda a: a.start,
        )

    def order_completion_time(self, order_id: OrderId) -> int | None:
        """Get the completion time of an order (end of its last operation)."""
        order_assignments = self.assignments_for_order(order_id)
        if not order_assignments:
            return None
        return order_assignments[-1].end
