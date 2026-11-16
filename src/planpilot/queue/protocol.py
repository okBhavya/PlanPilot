"""Job queue protocol and data models for asynchronous task execution."""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    """Execution status of an asynchronous job."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class JobInfo(BaseModel):
    """Metadata and status of an asynchronous job."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    job_type: str
    status: JobStatus = JobStatus.PENDING
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    error: str | None = None
    result: Any = None


@runtime_checkable
class JobQueue(Protocol):
    """Protocol defining asynchronous task queue operations."""

    async def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        """Enqueue a new job and return its unique ID."""
        ...

    async def get_status(self, job_id: str) -> JobInfo | None:
        """Fetch the current status of a job."""
        ...

    async def get_result(self, job_id: str) -> Any | None:
        """Retrieve the completed result of a job."""
        ...

    async def complete(self, job_id: str, result: Any) -> None:
        """Mark a job as successfully completed with result data."""
        ...

    async def fail(self, job_id: str, error: str) -> None:
        """Mark a job as failed with an error message."""
        ...
