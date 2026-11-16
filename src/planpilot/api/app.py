"""FastAPI application for PlanPilot AI-native production scheduling."""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import pandas as pd
from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from planpilot.agents.explain_agent import ExplainAgent
from planpilot.agents.onboarding_agent import OnboardingAgent
from planpilot.agents.rule_agent import RuleAgent
from planpilot.agents.scenario_agent import ScenarioAgent, ScenarioWeights
from planpilot.api.auth import verify_api_key
from planpilot.api.schemas import (
    AddOrderRequest,
    CreatePlantRequest,
    ExplainRequest,
    ExplainResponse,
    JobResponse,
    OnboardingUploadResponse,
    PlantResponse,
    RuleNLRequest,
    RuleNLResponse,
    ScenarioCompareRequest,
    ScenarioCompareResponse,
    SolveScheduleRequest,
)
from planpilot.db.models import JobRecord, PlantRecord, RuleRecord, ScheduleRecord
from planpilot.db.session import get_db_session, init_db
from planpilot.domain.models import Order, OrderPriority, OrderStatus, Plant, Schedule
from planpilot.erp.models import ERPFormat, GoLiveStatus
from planpilot.llm.client import LLMClient
from planpilot.queue.memory_queue import MemoryJobQueue
from planpilot.queue.protocol import JobStatus
from planpilot.rules.store import RuleStore
from planpilot.worker.worker import execute_solve_job

# Global queue instance
job_queue = MemoryJobQueue()


async def _async_solve_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Background handler invoked by MemoryJobQueue."""
    return execute_solve_job(payload)


job_queue.register_handler("solve_schedule", _async_solve_handler)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Enforce request body size limit (20MB default)."""

    def __init__(self, app: Any, max_size_bytes: int = 20 * 1024 * 1024) -> None:
        super().__init__(app)
        self.max_size_bytes = max_size_bytes

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size_bytes:
            return Response(
                content=json.dumps({"detail": "Request body exceeds maximum size limit"}),
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                media_type="application/json",
            )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for database initialization."""
    await init_db()
    yield


app = FastAPI(
    title="PlanPilot API",
    description="Production-grade constraint programming scheduler with LLM copilot",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestSizeLimitMiddleware, max_size_bytes=20 * 1024 * 1024)


# In-memory stores for fast retrieval in addition to DB
_in_memory_plants: dict[str, Plant] = {}
_in_memory_schedules: dict[str, Schedule] = {}


@app.get("/health", tags=["System"])
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "PlanPilot API"}


@app.post(
    "/plants",
    response_model=PlantResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Plants"],
)
async def create_plant(
    req: CreatePlantRequest,
    session: AsyncSession = Depends(get_db_session),
) -> PlantResponse:
    """Register a new plant configuration."""
    plant = req.plant
    plant.validate_referential_integrity()

    _in_memory_plants[plant.id] = plant

    record = PlantRecord(
        id=plant.id,
        name=plant.name,
        data_json=plant.model_dump_json(),
    )
    await session.merge(record)
    await session.commit()

    return PlantResponse(
        id=plant.id,
        name=plant.name,
        machines_count=len(plant.machines),
        orders_count=len(plant.orders),
    )


@app.get(
    "/plants/{plant_id}",
    response_model=Plant,
    dependencies=[Depends(verify_api_key)],
    tags=["Plants"],
)
async def get_plant(
    plant_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> Plant:
    """Fetch plant by ID."""
    if plant_id in _in_memory_plants:
        return _in_memory_plants[plant_id]

    stmt = select(PlantRecord).where(PlantRecord.id == plant_id)
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail=f"Plant '{plant_id}' not found")

    plant = Plant.model_validate_json(record.data_json)
    _in_memory_plants[plant_id] = plant
    return plant


@app.post(
    "/orders",
    dependencies=[Depends(verify_api_key)],
    tags=["Orders"],
)
async def add_order(
    req: AddOrderRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Append a new order to an existing plant."""
    plant = await get_plant(req.plant_id, session)

    new_order = Order(
        id=req.order_id,
        routing_id=req.routing_id,
        quantity=req.quantity,
        due_date=req.due_date,
        release_time=req.release_time,
        priority=OrderPriority(req.priority),
        status=OrderStatus.PENDING,
    )

    updated_orders = list(plant.orders) + [new_order]
    updated_plant = plant.model_copy(update={"orders": updated_orders})
    updated_plant.validate_referential_integrity()

    _in_memory_plants[plant.id] = updated_plant

    stmt = select(PlantRecord).where(PlantRecord.id == plant.id)
    res = await session.execute(stmt)
    record = res.scalar_one_or_none()
    if record:
        record.data_json = updated_plant.model_dump_json()
        await session.commit()

    return {"status": "success", "order_id": req.order_id, "plant_id": plant.id}


@app.post(
    "/rules",
    response_model=RuleNLResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Rules"],
)
async def process_natural_language_rule(
    req: RuleNLRequest,
    session: AsyncSession = Depends(get_db_session),
) -> RuleNLResponse:
    """Translate natural language scheduling rule into validated Rule DSL."""
    plant = await get_plant(req.plant_id, session)

    rule_store = RuleStore(plant)
    rule_agent = RuleAgent(plant=plant, rule_store=rule_store, llm_client=LLMClient())

    agent_resp = rule_agent.process_natural_language_rule(req.prompt)

    if agent_resp.rule:
        rule_record = RuleRecord(
            id=f"rule-{uuid.uuid4().hex[:8]}",
            plant_id=plant.id,
            rule_json=agent_resp.rule.model_dump_json(),
            is_active=True,
            version=1,
        )
        session.add(rule_record)
        await session.commit()

    return RuleNLResponse(
        status=agent_resp.status,
        message=agent_resp.message,
        rule=agent_resp.rule,
        clarification_question=agent_resp.clarification_question,
    )


@app.get(
    "/rules/{plant_id}",
    dependencies=[Depends(verify_api_key)],
    tags=["Rules"],
)
async def list_rules(
    plant_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """List all active rules for a plant."""
    stmt = select(RuleRecord).where(RuleRecord.plant_id == plant_id)
    res = await session.execute(stmt)
    records = res.scalars().all()
    return [
        {
            "id": r.id,
            "rule": json.loads(r.rule_json),
            "is_active": r.is_active,
            "version": r.version,
        }
        for r in records
    ]


@app.post(
    "/schedules",
    response_model=JobResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Schedules"],
)
async def submit_solve_job(
    req: SolveScheduleRequest,
    session: AsyncSession = Depends(get_db_session),
) -> JobResponse:
    """Submit an asynchronous schedule solve job."""
    plant = await get_plant(req.plant_id, session)

    # Fetch rules if provided
    active_rules: list[dict[str, Any]] = []
    if req.rule_ids:
        stmt = select(RuleRecord).where(RuleRecord.id.in_(req.rule_ids))
        res = await session.execute(stmt)
        records = res.scalars().all()
        active_rules = [json.loads(r.rule_json) for r in records]

    payload = {
        "plant": plant.model_dump(mode="json"),
        "rules": active_rules,
        "time_limit_seconds": req.time_limit_seconds,
        "objective": req.objective,
    }

    job_id = await job_queue.enqueue("solve_schedule", payload)

    job_record = JobRecord(
        id=job_id,
        job_type="solve_schedule",
        status=JobStatus.PENDING.value,
        payload_json=json.dumps(payload),
    )
    session.add(job_record)
    await session.commit()

    return JobResponse(job_id=job_id, status=JobStatus.PENDING.value)


@app.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Schedules"],
)
async def get_job_status(
    job_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> JobResponse:
    """Poll asynchronous job status and result."""
    job_info = await job_queue.get_status(job_id)
    if not job_info:
        # Check DB
        stmt = select(JobRecord).where(JobRecord.id == job_id)
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

        sched = (
            Schedule.model_validate_json(record.result_json)
            if record.result_json
            else None
        )
        return JobResponse(
            job_id=record.id,
            status=record.status,
            schedule=sched,
            error=record.error,
        )

    schedule: Schedule | None = None
    if job_info.status == JobStatus.COMPLETED and job_info.result:
        schedule = Schedule.model_validate(job_info.result)
        _in_memory_schedules[schedule.id] = schedule

        # Persist to DB if not already done
        sched_record = ScheduleRecord(
            id=schedule.id,
            plant_id=schedule.plant_id,
            schedule_json=schedule.model_dump_json(),
        )
        await session.merge(sched_record)

        stmt = select(JobRecord).where(JobRecord.id == job_id)
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()
        if record:
            record.status = JobStatus.COMPLETED.value
            record.result_json = schedule.model_dump_json()
        await session.commit()

    return JobResponse(
        job_id=job_id,
        status=job_info.status.value,
        schedule=schedule,
        error=job_info.error,
    )


@app.post(
    "/explain",
    response_model=ExplainResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Explainability"],
)
async def explain_schedule(
    req: ExplainRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ExplainResponse:
    """Grounded explanation of lateness or schedule movement."""
    # Retrieve current schedule
    schedule: Schedule | None = _in_memory_schedules.get(req.schedule_id)
    if not schedule:
        stmt = select(ScheduleRecord).where(ScheduleRecord.id == req.schedule_id)
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Schedule not found")
        schedule = Schedule.model_validate_json(record.schedule_json)
        _in_memory_schedules[schedule.id] = schedule

    plant = await get_plant(schedule.plant_id, session)
    agent = ExplainAgent(llm_client=LLMClient())

    if req.order_id:
        ans = agent.explain_lateness(
            schedule=schedule, plant=plant, order_id=req.order_id
        )
        return ExplainResponse(
            question=f"Why is order {req.order_id} late?",
            explanation=ans.explanation,
            faithfulness_score=ans.faithfulness_score,
            cited_fact_ids=[f.id for f in ans.facts],
        )
    elif req.previous_schedule_id:
        prev_sched: Schedule | None = _in_memory_schedules.get(
            req.previous_schedule_id
        )
        if not prev_sched:
            stmt = select(ScheduleRecord).where(
                ScheduleRecord.id == req.previous_schedule_id
            )
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if not record:
                raise HTTPException(
                    status_code=404, detail="Previous schedule not found"
                )
            prev_sched = Schedule.model_validate_json(record.schedule_json)
            _in_memory_schedules[prev_sched.id] = prev_sched

        ans = agent.explain_schedule_diff(
            before=prev_sched, after=schedule, plant=plant
        )
        return ExplainResponse(
            question="Why did jobs move between schedules?",
            explanation=ans.explanation,
            faithfulness_score=ans.faithfulness_score,
            cited_fact_ids=[f.id for f in ans.facts],
        )

    raise HTTPException(
        status_code=400, detail="Must provide order_id or previous_schedule_id"
    )


@app.post(
    "/scenarios/compare",
    response_model=ScenarioCompareResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Scenarios"],
)
async def compare_scenarios(
    req: ScenarioCompareRequest,
    session: AsyncSession = Depends(get_db_session),
) -> ScenarioCompareResponse:
    """Compare two schedules and return AI recommendation with explainable trade-offs."""
    # Retrieve both schedules
    schedules: dict[str, Schedule] = {}
    for s_id in [req.schedule_a_id, req.schedule_b_id]:
        if s_id in _in_memory_schedules:
            schedules[s_id] = _in_memory_schedules[s_id]
        else:
            stmt = select(ScheduleRecord).where(ScheduleRecord.id == s_id)
            res = await session.execute(stmt)
            record = res.scalar_one_or_none()
            if not record:
                raise HTTPException(status_code=404, detail=f"Schedule '{s_id}' not found")
            schedules[s_id] = Schedule.model_validate_json(record.schedule_json)

    sched_a = schedules[req.schedule_a_id]
    sched_b = schedules[req.schedule_b_id]

    weights = None
    if req.weights:
        weights = ScenarioWeights(**req.weights)

    agent = ScenarioAgent(weights=weights)
    comp = agent.compare(sched_a, sched_b)

    return ScenarioCompareResponse(
        schedule_a_id=comp.schedule_a_id,
        schedule_b_id=comp.schedule_b_id,
        recommended_schedule=comp.recommended_schedule,
        score_a=comp.score_a,
        score_b=comp.score_b,
        confidence=comp.confidence,
        kpis_a=comp.kpis_a,
        kpis_b=comp.kpis_b,
        kpi_diff=comp.kpi_diff,
        trade_offs=comp.trade_offs,
        summary_markdown=comp.summary_markdown,
    )


@app.post(
    "/onboarding",
    response_model=OnboardingUploadResponse,
    dependencies=[Depends(verify_api_key)],
    tags=["Onboarding"],
)
async def onboard_erp_files(
    files: list[UploadFile] = File(...),
    format_type: str = "sap",
    session: AsyncSession = Depends(get_db_session),
) -> OnboardingUploadResponse:
    """Upload messy CSV files, run profiling and validation, and return readiness report."""
    tables: dict[str, pd.DataFrame] = {}

    for f in files:
        # Determine table name from filename (e.g., CRHD.csv -> CRHD)
        table_name = (f.filename or "table").split(".")[0].upper()
        content = await f.read()
        try:
            df = pd.read_csv(io.BytesIO(content))
            tables[table_name] = df
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to parse CSV file '{f.filename}': {e}",
            ) from e

    fmt = (
        ERPFormat.SAP
        if format_type.lower() == "sap"
        else ERPFormat.LEGACY
    )
    agent = OnboardingAgent(tables=tables, format_type=fmt)

    agent.validate_dataset()
    readiness = agent.build_readiness_report()

    plant_created = False
    plant_id: str | None = None

    if readiness.status == GoLiveStatus.READY:
        try:
            plant = agent.to_plant()
            plant.validate_referential_integrity()
            _in_memory_plants[plant.id] = plant

            plant_rec = PlantRecord(
                id=plant.id,
                name=plant.name,
                data_json=plant.model_dump_json(),
            )
            await session.merge(plant_rec)
            await session.commit()

            plant_created = True
            plant_id = plant.id
        except Exception:
            plant_created = False

    return OnboardingUploadResponse(
        status=readiness.status.value,
        readiness_score=readiness.readiness_score,
        total_records=readiness.total_records,
        table_counts=readiness.table_counts,
        critical_errors_count=len(readiness.critical_errors),
        warnings_count=len(readiness.warnings),
        markdown_report=readiness.markdown_summary,
        plant_created=plant_created,
        plant_id=plant_id,
    )
