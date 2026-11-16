"""In-memory implementation of the JobQueue protocol for tests and standalone mode."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from planpilot.queue.protocol import JobInfo, JobStatus


class MemoryJobQueue:
    """Thread-safe, in-memory task queue implementing the JobQueue protocol."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobInfo] = {}
        self._handlers: dict[str, Callable[[dict[str, Any]], Coroutine[Any, Any, Any]]] = {}

    def register_handler(
        self,
        job_type: str,
        handler: Callable[[dict[str, Any]], Coroutine[Any, Any, Any]],
    ) -> None:
        """Register an async handler function for a specific job type."""
        self._handlers[job_type] = handler

    async def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        """Enqueue a job and schedule it for execution."""
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        now = time.time()
        job = JobInfo(
            job_id=job_id,
            job_type=job_type,
            status=JobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job_id] = job

        # If a handler is registered, trigger asynchronous background execution
        if job_type in self._handlers:
            asyncio.create_task(self._run_job(job_id, payload))

        return job_id

    async def _run_job(self, job_id: str, payload: dict[str, Any]) -> None:
        """Execute the registered handler in the background."""
        job = self._jobs[job_id]
        handler = self._handlers[job.job_type]
        self._jobs[job_id] = JobInfo(
            job_id=job_id,
            job_type=job.job_type,
            status=JobStatus.IN_PROGRESS,
            created_at=job.created_at,
            updated_at=time.time(),
        )

        try:
            result = await handler(payload)
            await self.complete(job_id, result)
        except Exception as e:
            await self.fail(job_id, str(e))

    async def get_status(self, job_id: str) -> JobInfo | None:
        """Fetch current status."""
        return self._jobs.get(job_id)

    async def get_result(self, job_id: str) -> Any | None:
        """Retrieve result if completed."""
        job = self._jobs.get(job_id)
        if job and job.status == JobStatus.COMPLETED:
            return job.result
        return None

    async def complete(self, job_id: str, result: Any) -> None:
        """Mark as completed."""
        job = self._jobs.get(job_id)
        if job:
            self._jobs[job_id] = JobInfo(
                job_id=job_id,
                job_type=job.job_type,
                status=JobStatus.COMPLETED,
                created_at=job.created_at,
                updated_at=time.time(),
                result=result,
            )

    async def fail(self, job_id: str, error: str) -> None:
        """Mark as failed."""
        job = self._jobs.get(job_id)
        if job:
            self._jobs[job_id] = JobInfo(
                job_id=job_id,
                job_type=job.job_type,
                status=JobStatus.FAILED,
                created_at=job.created_at,
                updated_at=time.time(),
                error=error,
            )
