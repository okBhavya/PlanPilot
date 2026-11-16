"""Loaders for OR-Library job-shop scheduling benchmark instances.

Supports the standard OR-Library format for job-shop instances (ft06, ft10,
la01-la05, etc.). These instances are converted to the PlanPilot domain model
for benchmarking the CP-SAT solver.

Format:
  Line 1: num_jobs num_machines
  Following lines: pairs of (machine_id, processing_time) for each operation
"""

from __future__ import annotations

from pathlib import Path

from planpilot.domain.models import (
    Machine,
    Material,
    MaterialFamily,
    Operation,
    Order,
    OrderPriority,
    Plant,
    Routing,
    WorkCenter,
)

# Known optimal makespans for OR-Library instances
KNOWN_OPTIMA: dict[str, int] = {
    "ft06": 55,
    "ft10": 930,
    "la01": 666,
    "la02": 655,
    "la03": 597,
    "la04": 590,
    "la05": 593,
}

# Benchmark data directory
_BENCHMARKS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "benchmarks"


def parse_orlib_instance(text: str) -> tuple[int, int, list[list[tuple[int, int]]]]:
    """Parse an OR-Library job-shop instance from text.

    Args:
        text: Raw text content of the benchmark file.

    Returns:
        Tuple of (num_jobs, num_machines, jobs) where jobs is a list of
        operations per job, each operation being (machine_index, processing_time).
    """
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]

    # Skip comment lines (start with #) and find header
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("#") or line.startswith("//"):
            continue
        data_lines.append(line)

    if not data_lines:
        raise ValueError("Empty benchmark file")

    # First data line: num_jobs num_machines
    header = data_lines[0].split()
    num_jobs = int(header[0])
    num_machines = int(header[1])

    # Remaining lines: job definitions
    jobs: list[list[tuple[int, int]]] = []
    for i in range(1, num_jobs + 1):
        if i >= len(data_lines):
            raise ValueError(f"Expected {num_jobs} job lines but only found {len(data_lines) - 1}")
        tokens = list(map(int, data_lines[i].split()))
        if len(tokens) != num_machines * 2:
            raise ValueError(f"Job {i} has {len(tokens)} tokens, expected {num_machines * 2}")
        ops: list[tuple[int, int]] = []
        for j in range(0, len(tokens), 2):
            machine_idx = tokens[j]
            proc_time = tokens[j + 1]
            ops.append((machine_idx, proc_time))
        jobs.append(ops)

    return num_jobs, num_machines, jobs


def orlib_to_plant(
    name: str,
    num_jobs: int,
    num_machines: int,
    jobs: list[list[tuple[int, int]]],
) -> Plant:
    """Convert a parsed OR-Library instance to a Plant domain model.

    In the standard job-shop problem:
    - Each job has exactly num_machines operations (one per machine).
    - Each operation is assigned to exactly one machine (no alternatives).
    - Operations within a job must be processed in order.

    Args:
        name: Instance name (e.g., "ft06").
        num_jobs: Number of jobs.
        num_machines: Number of machines.
        jobs: List of jobs, each a list of (machine_index, processing_time).

    Returns:
        A Plant instance representing the benchmark.
    """
    # Create machines
    machines = [
        Machine(
            id=f"M{i}",
            name=f"Machine {i}",
            work_center_id=f"WC{i}",
        )
        for i in range(num_machines)
    ]

    # Create work centers (one per machine for standard job-shop)
    work_centers = [
        WorkCenter(
            id=f"WC{i}",
            name=f"Work Center {i}",
            plant_id=name,
            machine_ids=[f"M{i}"],
        )
        for i in range(num_machines)
    ]

    # Create a generic material
    material = Material(
        id="MAT-GENERIC",
        name="Generic Material",
        family=MaterialFamily.GENERIC,
    )

    # Create routings and orders
    routings: list[Routing] = []
    orders: list[Order] = []

    for job_idx, job_ops in enumerate(jobs):
        operations: list[Operation] = []
        for op_idx, (machine_idx, proc_time) in enumerate(job_ops):
            operations.append(
                Operation(
                    id=f"J{job_idx}-OP{op_idx * 10 + 10}",
                    sequence=(op_idx + 1) * 10,
                    name=f"Job {job_idx} Op {op_idx}",
                    eligible_machine_ids=[f"M{machine_idx}"],
                    processing_time=proc_time,
                )
            )

        routing = Routing(
            id=f"RTG-J{job_idx}",
            product_material_id="MAT-GENERIC",
            operations=operations,
        )
        routings.append(routing)

        order = Order(
            id=f"ORD-J{job_idx}",
            routing_id=routing.id,
            priority=OrderPriority.NORMAL,
        )
        orders.append(order)

    # Compute horizon: sum of all processing times (upper bound)
    total_proc = sum(pt for job in jobs for _, pt in job)
    horizon = total_proc + 1

    return Plant(
        id=name,
        name=f"OR-Library instance: {name}",
        machines=machines,
        work_centers=work_centers,
        materials=[material],
        routings=routings,
        orders=orders,
        horizon=horizon,
    )


def load_benchmark(name: str, benchmarks_dir: Path | None = None) -> Plant:
    """Load a named OR-Library benchmark instance.

    Args:
        name: Instance name (e.g., "ft06", "ft10", "la01").
        benchmarks_dir: Optional override for the benchmarks directory.

    Returns:
        A Plant instance.

    Raises:
        FileNotFoundError: If the benchmark file doesn't exist.
    """
    base_dir = benchmarks_dir or _BENCHMARKS_DIR
    filepath = base_dir / f"{name}.txt"
    if not filepath.exists():
        raise FileNotFoundError(f"Benchmark file not found: {filepath}")

    text = filepath.read_text(encoding="utf-8")
    num_jobs, num_machines, jobs = parse_orlib_instance(text)
    return orlib_to_plant(name, num_jobs, num_machines, jobs)


def list_benchmarks(benchmarks_dir: Path | None = None) -> list[str]:
    """List available benchmark instance names."""
    base_dir = benchmarks_dir or _BENCHMARKS_DIR
    if not base_dir.exists():
        return []
    return sorted(p.stem for p in base_dir.glob("*.txt"))
