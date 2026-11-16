"""Asynchronous worker tasks for production schedule optimization."""

from __future__ import annotations

import os
from typing import Any

from arq.connections import RedisSettings
from pydantic import TypeAdapter

from planpilot.domain.models import ObjectiveType, Plant
from planpilot.rules.dsl import Rule
from planpilot.solver.scheduler import solve

_rule_adapter = TypeAdapter(Rule)


def execute_solve_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Synchronously execute a CP-SAT solve job from a payload dictionary."""
    plant_data = payload.get("plant")
    if not plant_data:
        raise ValueError("Payload missing 'plant' data")

    plant = Plant.model_validate(plant_data)

    rules_data = payload.get("rules", [])
    rules: list[Rule] = [_rule_adapter.validate_python(r) for r in rules_data] if rules_data else []

    time_limit = float(payload.get("time_limit_seconds", 10.0))
    obj_str = payload.get("objective", "makespan")
    objective = (
        ObjectiveType(obj_str)
        if obj_str in [o.value for o in ObjectiveType]
        else ObjectiveType.MAKESPAN
    )

    schedule = solve(
        plant=plant,
        objective=objective,
        rules=rules,
        time_limit_seconds=time_limit,
    )

    return schedule.model_dump(mode="json")


async def solve_schedule_task(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Worker task executed by arq or background runners."""
    return execute_solve_job(payload)


class WorkerSettings:
    """Settings for the arq background worker process."""

    functions = [solve_schedule_task]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379"))
