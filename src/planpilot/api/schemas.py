"""Pydantic API request and response schemas for PlanPilot REST API."""

from __future__ import annotations

from pydantic import BaseModel

from planpilot.domain.models import KPIs, Plant, Schedule
from planpilot.explain.diff import KPIDiff
from planpilot.rules.dsl import Rule


class CreatePlantRequest(BaseModel):
    """Payload to create or register a plant."""

    plant: Plant


class PlantResponse(BaseModel):
    """Plant registration response."""

    id: str
    name: str
    machines_count: int
    orders_count: int


class AddOrderRequest(BaseModel):
    """Request to append an order to an existing plant."""

    plant_id: str
    order_id: str
    routing_id: str
    quantity: int = 1
    due_date: int = 100
    release_time: int = 0
    priority: int = 5


class RuleNLRequest(BaseModel):
    """Natural language rule input."""

    plant_id: str
    prompt: str


class RuleNLResponse(BaseModel):
    """Result of natural language rule processing."""

    status: str
    message: str
    rule: Rule | None = None
    clarification_question: str | None = None


class SolveScheduleRequest(BaseModel):
    """Request to schedule an optimization run."""

    plant_id: str
    rule_ids: list[str] | None = None
    time_limit_seconds: float = 10.0
    objective: str = "makespan"


class JobResponse(BaseModel):
    """Status or result of an asynchronous solve job."""

    job_id: str
    status: str
    schedule: Schedule | None = None
    error: str | None = None


class ExplainRequest(BaseModel):
    """Request to explain lateness or movement."""

    schedule_id: str
    order_id: str | None = None
    previous_schedule_id: str | None = None


class ExplainResponse(BaseModel):
    """Grounded explanation response."""

    question: str
    explanation: str
    faithfulness_score: float
    cited_fact_ids: list[str]


class ScenarioCompareRequest(BaseModel):
    """Request to compare two solved schedules."""

    schedule_a_id: str
    schedule_b_id: str
    weights: dict[str, float] | None = None


class ScenarioCompareResponse(BaseModel):
    """Scenario comparison and recommendation output."""

    schedule_a_id: str
    schedule_b_id: str
    recommended_schedule: str
    score_a: float
    score_b: float
    confidence: float
    kpis_a: KPIs
    kpis_b: KPIs
    kpi_diff: KPIDiff
    trade_offs: list[str]
    summary_markdown: str


class OnboardingUploadResponse(BaseModel):
    """Readiness report and mapping result for uploaded ERP extracts."""

    status: str
    readiness_score: float
    total_records: int
    table_counts: dict[str, int]
    critical_errors_count: int
    warnings_count: int
    markdown_report: str
    plant_created: bool
    plant_id: str | None = None
