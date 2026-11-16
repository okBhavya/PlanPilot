"""Plant instance generator for PlanPilot.

Generates configurable, deterministic plant instances for testing and
benchmarking. Supports control over number of orders, machines,
alternative machines per operation, material families, changeover
matrices, due dates, priority weights, and downtime windows.
"""

from __future__ import annotations

import random
from typing import Any

from planpilot.domain.models import (
    BOM,
    BOMComponent,
    ChangeoverEntry,
    ChangeoverMatrix,
    DowntimeWindow,
    Machine,
    Material,
    MaterialFamily,
    ObjectiveType,
    Operation,
    Order,
    OrderPriority,
    Plant,
    Routing,
    WorkCenter,
)


def _material_families() -> list[MaterialFamily]:
    """Return all material families except GENERIC."""
    return [f for f in MaterialFamily if f != MaterialFamily.GENERIC]


class PlantGenerator:
    """Generate random but deterministic plant instances.

    All randomness is seeded via the provided `seed` parameter, ensuring
    reproducibility. The generator creates a complete plant with machines,
    work centers, materials, BOMs, routings, orders, changeover matrices,
    and downtime windows.
    """

    def __init__(
        self,
        num_machines: int = 6,
        num_work_centers: int = 3,
        num_materials: int = 8,
        num_orders: int = 10,
        ops_per_routing: tuple[int, int] = (2, 5),
        alt_machines_per_op: tuple[int, int] = (1, 3),
        processing_time_range: tuple[int, int] = (5, 50),
        setup_time_range: tuple[int, int] = (0, 10),
        changeover_time_range: tuple[int, int] = (5, 30),
        changeover_density: float = 0.4,
        due_date_tightness: float = 1.5,
        downtime_probability: float = 0.2,
        downtime_duration_range: tuple[int, int] = (10, 50),
        priority_weights: dict[OrderPriority, float] | None = None,
        seed: int = 42,
    ) -> None:
        self.num_machines = num_machines
        self.num_work_centers = num_work_centers
        self.num_materials = num_materials
        self.num_orders = num_orders
        self.ops_per_routing = ops_per_routing
        self.alt_machines_per_op = alt_machines_per_op
        self.processing_time_range = processing_time_range
        self.setup_time_range = setup_time_range
        self.changeover_time_range = changeover_time_range
        self.changeover_density = changeover_density
        self.due_date_tightness = due_date_tightness
        self.downtime_probability = downtime_probability
        self.downtime_duration_range = downtime_duration_range
        self.priority_weights = priority_weights or {
            OrderPriority.LOW: 0.2,
            OrderPriority.NORMAL: 0.5,
            OrderPriority.HIGH: 0.2,
            OrderPriority.CRITICAL: 0.1,
        }
        self.seed = seed
        self.rng = random.Random(seed)

    def generate(self, plant_id: str = "PLANT-001") -> Plant:
        """Generate a complete plant instance."""
        machines = self._generate_machines()
        work_centers = self._generate_work_centers(machines)
        materials = self._generate_materials()
        boms = self._generate_boms(materials)
        routings = self._generate_routings(materials, machines)
        orders = self._generate_orders(routings, materials)
        changeover_matrix = self._generate_changeover_matrix(machines)
        downtimes = self._generate_downtimes(machines)

        # Estimate horizon based on total processing time
        total_proc = sum(
            op.processing_time
            for r in routings
            for op in r.operations
        )
        horizon = max(int(total_proc * self.due_date_tightness * 2), 1000)

        return Plant(
            id=plant_id,
            name=f"Generated Plant ({self.num_orders} orders, {self.num_machines} machines)",
            machines=machines,
            work_centers=work_centers,
            materials=materials,
            boms=boms,
            routings=routings,
            orders=orders,
            changeover_matrix=changeover_matrix,
            downtimes=downtimes,
            horizon=horizon,
        )

    def _generate_machines(self) -> list[Machine]:
        machines: list[Machine] = []
        for i in range(1, self.num_machines + 1):
            wc_idx = ((i - 1) % self.num_work_centers) + 1
            machines.append(
                Machine(
                    id=f"M{i:02d}",
                    name=f"Machine {i}",
                    work_center_id=f"WC{wc_idx:02d}",
                    speed_factor=round(self.rng.uniform(0.8, 1.2), 2),
                )
            )
        return machines

    def _generate_work_centers(self, machines: list[Machine]) -> list[WorkCenter]:
        wc_machines: dict[str, list[str]] = {}
        for m in machines:
            wc_machines.setdefault(m.work_center_id, []).append(m.id)

        return [
            WorkCenter(
                id=wc_id,
                name=f"Work Center {wc_id}",
                plant_id="PLANT-001",
                machine_ids=mids,
            )
            for wc_id, mids in sorted(wc_machines.items())
        ]

    def _generate_materials(self) -> list[Material]:
        families = _material_families()
        materials: list[Material] = []
        for i in range(1, self.num_materials + 1):
            family = families[(i - 1) % len(families)]
            materials.append(
                Material(
                    id=f"MAT-{i:03d}",
                    name=f"Material {i} ({family.value})",
                    family=family,
                    unit="EA",
                )
            )
        return materials

    def _generate_boms(self, materials: list[Material]) -> list[BOM]:
        boms: list[BOM] = []
        # Create BOMs for half the materials (finished goods)
        finished = materials[: len(materials) // 2]
        raw = materials[len(materials) // 2 :]
        for i, mat in enumerate(finished):
            num_components = self.rng.randint(1, min(3, len(raw)))
            components = [
                BOMComponent(
                    material_id=raw[j % len(raw)].id,
                    quantity=float(self.rng.randint(1, 5)),
                    unit="EA",
                )
                for j in range(num_components)
            ]
            boms.append(
                BOM(
                    id=f"BOM-{i + 1:03d}",
                    product_material_id=mat.id,
                    components=components,
                )
            )
        return boms

    def _generate_routings(
        self, materials: list[Material], machines: list[Machine]
    ) -> list[Routing]:
        routings: list[Routing] = []
        machine_ids = [m.id for m in machines]

        for i in range(1, self.num_orders + 1):
            mat = materials[(i - 1) % len(materials)]
            num_ops = self.rng.randint(*self.ops_per_routing)
            operations: list[Operation] = []

            for j in range(num_ops):
                num_alt = min(
                    self.rng.randint(*self.alt_machines_per_op),
                    len(machine_ids),
                )
                eligible = sorted(self.rng.sample(machine_ids, num_alt))
                proc_time = self.rng.randint(*self.processing_time_range)
                setup_time = self.rng.randint(*self.setup_time_range)

                operations.append(
                    Operation(
                        id=f"R{i:03d}-OP{(j + 1) * 10}",
                        sequence=(j + 1) * 10,
                        name=f"Op {(j + 1) * 10} of Routing {i}",
                        eligible_machine_ids=eligible,
                        processing_time=proc_time,
                        setup_time=setup_time,
                        material_id=mat.id,
                    )
                )

            routings.append(
                Routing(
                    id=f"RTG-{i:03d}",
                    product_material_id=mat.id,
                    operations=operations,
                )
            )
        return routings

    def _generate_orders(
        self, routings: list[Routing], materials: list[Material]
    ) -> list[Order]:
        orders: list[Order] = []
        priorities = list(self.priority_weights.keys())
        weights = list(self.priority_weights.values())

        for i in range(1, self.num_orders + 1):
            routing = routings[(i - 1) % len(routings)]
            total_proc = sum(op.processing_time for op in routing.operations)

            release_time = self.rng.randint(0, max(1, total_proc // 2))
            due_date = release_time + int(total_proc * self.due_date_tightness) + self.rng.randint(
                0, total_proc
            )
            priority = self.rng.choices(priorities, weights=weights, k=1)[0]
            weight = float(priority.value)

            orders.append(
                Order(
                    id=f"ORD-{i:03d}",
                    routing_id=routing.id,
                    quantity=1,
                    priority=priority,
                    release_time=release_time,
                    due_date=due_date,
                    weight=weight,
                    material_id=materials[(i - 1) % len(materials)].id,
                )
            )
        return orders

    def _generate_changeover_matrix(
        self, machines: list[Machine]
    ) -> ChangeoverMatrix:
        entries: list[ChangeoverEntry] = []
        families = _material_families()

        for machine in machines:
            for f1 in families:
                for f2 in families:
                    if f1 == f2:
                        continue
                    if self.rng.random() < self.changeover_density:
                        entries.append(
                            ChangeoverEntry(
                                machine_id=machine.id,
                                from_material=f1,
                                to_material=f2,
                                setup_time=self.rng.randint(
                                    *self.changeover_time_range
                                ),
                            )
                        )
        return ChangeoverMatrix(entries=entries)

    def _generate_downtimes(self, machines: list[Machine]) -> list[DowntimeWindow]:
        downtimes: list[DowntimeWindow] = []
        for machine in machines:
            if self.rng.random() < self.downtime_probability:
                start = self.rng.randint(50, 300)
                duration = self.rng.randint(*self.downtime_duration_range)
                downtimes.append(
                    DowntimeWindow(
                        machine_id=machine.id,
                        start=start,
                        end=start + duration,
                        reason=f"Planned maintenance for {machine.name}",
                    )
                )
        return downtimes
