"""Arq / Redis implementation of the JobQueue protocol for production worker clusters."""

from __future__ import annotations

import os
from typing import Any

from arq.connections import ArqRedis, create_pool
from arq.jobs import Job as ArqJob

from planpilot.queue.protocol import JobInfo, JobStatus


class ArqJobQueue:
    """Production Redis-backed job queue implementation using arq."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._pool: ArqRedis | None = None

    async def get_pool(self) -> ArqRedis:
        """Lazily initialize Redis connection pool."""
        if self._pool is None:
            self._pool = await create_pool()
        return self._pool

    async def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        """Enqueue a background task to Redis."""
        pool = await self.get_pool()
        job = await pool.enqueue_job(job_type, payload)
        if not job:
            raise RuntimeError(f"Failed to enqueue {job_type} job to Redis")
        return str(job.job_id)

    async def get_status(self, job_id: str) -> JobInfo | None:
        """Query job status from Redis."""
        pool = await self.get_pool()
        arq_job = ArqJob(job_id=job_id, redis=pool)
        raw_status = await arq_job.status()

        status_map = {
            "queued": JobStatus.PENDING,
            "in_progress": JobStatus.IN_PROGRESS,
            "complete": JobStatus.COMPLETED,
            "failed": JobStatus.FAILED,
        }
        status = status_map.get(raw_status, JobStatus.PENDING)

        return JobInfo(
            job_id=job_id,
            job_type="solve_schedule",
            status=status,
        )

    async def get_result(self, job_id: str) -> Any | None:
        """Retrieve completed job result from Redis."""
        pool = await self.get_pool()
        arq_job = ArqJob(job_id=job_id, redis=pool)
        return await arq_job.result()

    async def complete(self, job_id: str, result: Any) -> None:
        """Complete job (handled automatically by Arq worker)."""
        pass

    async def fail(self, job_id: str, error: str) -> None:
        """Fail job (handled automatically by Arq worker)."""
        pass
