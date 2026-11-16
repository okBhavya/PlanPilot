"""Tests for OR-Library benchmark loaders."""

from __future__ import annotations

from pathlib import Path

import pytest

from planpilot.solver.benchmarks import (
    KNOWN_OPTIMA,
    load_benchmark,
    parse_orlib_instance,
    orlib_to_plant,
)


BENCHMARKS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "benchmarks"


class TestParseOrlibInstance:
    def test_ft06_dimensions(self) -> None:
        text = (BENCHMARKS_DIR / "ft06.txt").read_text()
        num_jobs, num_machines, jobs = parse_orlib_instance(text)
        assert num_jobs == 6
        assert num_machines == 6
        assert len(jobs) == 6
        # Each job should have 6 operations (one per machine)
        for job in jobs:
            assert len(job) == 6

    def test_ft10_dimensions(self) -> None:
        text = (BENCHMARKS_DIR / "ft10.txt").read_text()
        num_jobs, num_machines, jobs = parse_orlib_instance(text)
        assert num_jobs == 10
        assert num_machines == 10
        assert len(jobs) == 10
        for job in jobs:
            assert len(job) == 10

    def test_la01_dimensions(self) -> None:
        text = (BENCHMARKS_DIR / "la01.txt").read_text()
        num_jobs, num_machines, jobs = parse_orlib_instance(text)
        assert num_jobs == 10
        assert num_machines == 5
        assert len(jobs) == 10

    def test_skips_comment_lines(self) -> None:
        text = "# This is a comment\n2 2\n0 3 1 5\n1 2 0 4\n"
        num_jobs, num_machines, jobs = parse_orlib_instance(text)
        assert num_jobs == 2
        assert num_machines == 2

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty"):
            parse_orlib_instance("")

    def test_malformed_job_raises(self) -> None:
        text = "2 2\n0 3 1 5\n1 2\n"  # Second job missing ops
        with pytest.raises(ValueError, match="tokens"):
            parse_orlib_instance(text)


class TestOrlibToPlant:
    def test_ft06_to_plant(self) -> None:
        text = (BENCHMARKS_DIR / "ft06.txt").read_text()
        n, m, jobs = parse_orlib_instance(text)
        plant = orlib_to_plant("ft06", n, m, jobs)
        assert plant.id == "ft06"
        assert len(plant.machines) == 6
        assert len(plant.orders) == 6
        assert len(plant.routings) == 6
        # Each routing should have 6 operations
        for routing in plant.routings:
            assert len(routing.operations) == 6

    def test_plant_passes_integrity(self) -> None:
        text = (BENCHMARKS_DIR / "ft06.txt").read_text()
        n, m, jobs = parse_orlib_instance(text)
        plant = orlib_to_plant("ft06", n, m, jobs)
        errors = plant.validate_referential_integrity()
        assert errors == [], f"Integrity errors: {errors}"


class TestLoadBenchmark:
    @pytest.mark.parametrize("name", ["ft06", "ft10", "la01", "la02", "la03", "la04", "la05"])
    def test_load_all_benchmarks(self, name: str) -> None:
        plant = load_benchmark(name, BENCHMARKS_DIR)
        assert plant.id == name
        assert len(plant.machines) > 0
        assert len(plant.orders) > 0

    def test_load_nonexistent_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_benchmark("nonexistent_benchmark_xyz", BENCHMARKS_DIR)

    @pytest.mark.parametrize("name", ["ft06", "ft10", "la01", "la02", "la03", "la04", "la05"])
    def test_benchmark_json_roundtrip(self, name: str) -> None:
        """Benchmark plants survive JSON serialization."""
        import json
        from planpilot.domain.models import Plant

        plant = load_benchmark(name, BENCHMARKS_DIR)
        data = json.loads(plant.model_dump_json())
        plant2 = Plant.model_validate(data)
        assert plant.id == plant2.id
        assert len(plant.machines) == len(plant2.machines)
        assert len(plant.orders) == len(plant2.orders)
