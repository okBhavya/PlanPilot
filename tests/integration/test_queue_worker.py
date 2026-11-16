"""Integration tests for asynchronous JobQueue and worker execution."""

import asyncio
from typing import Any

import pytest

from planpilot.domain.generator import PlantGenerator
from planpilot.queue.memory_queue import MemoryJobQueue
from planpilot.queue.protocol import JobStatus
from planpilot.worker.worker import execute_solve_job


@pytest.mark.asyncio
async def test_memory_queue_async_solve() -> None:
    """Verify enqueuing a solve job executes asynchronously and returns completed schedule."""
    queue = MemoryJobQueue()

    async def solve_handler(payload: dict[str, Any]) -> dict[str, Any]:
        return execute_solve_job(payload)

    queue.register_handler("solve_schedule", solve_handler)

    plant = PlantGenerator(num_orders=3, num_machines=2, seed=42).generate()

    payload = {
        "plant": plant.model_dump(mode="json"),
        "time_limit_seconds": 3.0,
        "objective": "makespan",
    }

    job_id = await queue.enqueue("solve_schedule", payload)
    assert job_id.startswith("job-")

    # Poll for completion with timeout
    max_wait = 10.0
    start_time = asyncio.get_event_loop().time()
    completed = False

    while asyncio.get_event_loop().time() - start_time < max_wait:
        status_info = await queue.get_status(job_id)
        if status_info and status_info.status == JobStatus.COMPLETED:
            completed = True
            break
        await asyncio.sleep(0.1)

    assert completed is True
    result = await queue.get_result(job_id)
    assert result is not None
    assert "assignments" in result
    assert result["kpis"]["makespan"] > 0
