"""Tests for plant instance generator."""

from __future__ import annotations

from planpilot.domain.generator import PlantGenerator


class TestPlantGenerator:
    def test_deterministic(self) -> None:
        """Two generators with the same seed produce identical plants."""
        g1 = PlantGenerator(seed=42)
        g2 = PlantGenerator(seed=42)
        p1 = g1.generate()
        p2 = g2.generate()
        assert p1.model_dump_json() == p2.model_dump_json()

    def test_different_seeds_differ(self) -> None:
        """Different seeds produce different plants."""
        g1 = PlantGenerator(seed=42)
        g2 = PlantGenerator(seed=99)
        p1 = g1.generate()
        p2 = g2.generate()
        assert p1.model_dump_json() != p2.model_dump_json()

    def test_correct_counts(self) -> None:
        """Generated plant has the requested number of entities."""
        g = PlantGenerator(
            num_machines=8,
            num_orders=15,
            num_materials=10,
            seed=1,
        )
        p = g.generate()
        assert len(p.machines) == 8
        assert len(p.orders) == 15
        assert len(p.materials) == 10
        assert len(p.routings) == 15  # one routing per order

    def test_referential_integrity(self) -> None:
        """Generated plant passes referential integrity checks."""
        g = PlantGenerator(seed=42)
        p = g.generate()
        errors = p.validate_referential_integrity()
        assert errors == [], f"Integrity errors: {errors}"

    def test_json_roundtrip(self) -> None:
        """Generated plant survives JSON round-trip."""
        g = PlantGenerator(seed=42)
        p = g.generate()
        from planpilot.domain.models import Plant
        import json
        data = json.loads(p.model_dump_json())
        p2 = Plant.model_validate(data)
        assert p.id == p2.id
        assert len(p.machines) == len(p2.machines)
        assert len(p.orders) == len(p2.orders)

    def test_operations_have_valid_machines(self) -> None:
        """Every operation's eligible machines exist in the plant."""
        g = PlantGenerator(seed=42)
        p = g.generate()
        machine_ids = {m.id for m in p.machines}
        for routing in p.routings:
            for op in routing.operations:
                for mid in op.eligible_machine_ids:
                    assert mid in machine_ids, f"Op {op.id} has unknown machine {mid}"

    def test_orders_have_valid_routings(self) -> None:
        """Every order references an existing routing."""
        g = PlantGenerator(seed=42)
        p = g.generate()
        routing_ids = {r.id for r in p.routings}
        for order in p.orders:
            assert order.routing_id in routing_ids

    def test_due_dates_after_release(self) -> None:
        """Due dates are always after release times."""
        g = PlantGenerator(seed=42)
        p = g.generate()
        for order in p.orders:
            if order.due_date is not None:
                assert order.due_date > order.release_time, (
                    f"Order {order.id}: due_date={order.due_date} <= "
                    f"release_time={order.release_time}"
                )

    def test_downtimes_reference_valid_machines(self) -> None:
        """All downtime windows reference existing machines."""
        g = PlantGenerator(downtime_probability=0.8, seed=42)
        p = g.generate()
        machine_ids = {m.id for m in p.machines}
        for dt in p.downtimes:
            assert dt.machine_id in machine_ids

    def test_changeover_entries_reference_valid_machines(self) -> None:
        """All changeover matrix entries reference existing machines."""
        g = PlantGenerator(changeover_density=0.8, seed=42)
        p = g.generate()
        machine_ids = {m.id for m in p.machines}
        for entry in p.changeover_matrix.entries:
            assert entry.machine_id in machine_ids

    def test_large_instance(self) -> None:
        """Generating a larger instance completes without error."""
        g = PlantGenerator(
            num_machines=20,
            num_orders=50,
            num_materials=15,
            seed=7,
        )
        p = g.generate()
        assert len(p.machines) == 20
        assert len(p.orders) == 50
        errors = p.validate_referential_integrity()
        assert errors == []
