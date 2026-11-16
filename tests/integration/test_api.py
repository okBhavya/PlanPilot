"""Comprehensive integration tests for the PlanPilot FastAPI endpoints."""

import asyncio
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from planpilot.api.app import app
from planpilot.db.session import init_db
from planpilot.domain.generator import PlantGenerator
from planpilot.domain.models import ObjectiveType
from planpilot.erp.generator import SAPExtractGenerator
from planpilot.solver.scheduler import solve


@pytest.fixture(autouse=True)
async def setup_test_db() -> None:
    """Ensure database schema is created before tests run."""
    await init_db()


@pytest.mark.asyncio
async def test_api_health_check() -> None:
    """Verify /health returns 200 OK."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "service": "PlanPilot API"}


@pytest.mark.asyncio
async def test_api_plant_lifecycle() -> None:
    """Verify plant registration, retrieval, and order appending."""
    plant = PlantGenerator(num_orders=3, num_machines=2, seed=42).generate(plant_id="PLANT-API-01")

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register Plant
        create_resp = await client.post(
            "/plants",
            json={"plant": plant.model_dump(mode="json")},
        )
        assert create_resp.status_code == 200
        data = create_resp.json()
        assert data["id"] == plant.id
        assert data["machines_count"] == 2
        assert data["orders_count"] == 3

        # 2. Get Plant
        get_resp = await client.get(f"/plants/{plant.id}")
        assert get_resp.status_code == 200
        retrieved_plant = get_resp.json()
        assert retrieved_plant["id"] == plant.id

        # 3. Add Order
        add_order_resp = await client.post(
            "/orders",
            json={
                "plant_id": plant.id,
                "order_id": "ORD-NEW-99",
                "routing_id": plant.orders[0].routing_id,
                "quantity": 5,
                "due_date": 200,
                "release_time": 10,
                "priority": 5,
            },
        )
        assert add_order_resp.status_code == 200
        assert add_order_resp.json()["order_id"] == "ORD-NEW-99"


@pytest.mark.asyncio
async def test_api_async_schedule_solve() -> None:
    """Verify asynchronous schedule solve submission and status polling."""
    plant = PlantGenerator(num_orders=2, num_machines=2, seed=42).generate(plant_id="PLANT-API-02")

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Register plant
        await client.post("/plants", json={"plant": plant.model_dump(mode="json")})

        # Submit solve job
        solve_resp = await client.post(
            "/schedules",
            json={
                "plant_id": plant.id,
                "time_limit_seconds": 3.0,
                "objective": "makespan",
            },
        )
        assert solve_resp.status_code == 200
        job_data = solve_resp.json()
        job_id = job_data["job_id"]
        assert job_id.startswith("job-")

        # Poll job status
        max_attempts = 30
        completed = False
        schedule_data: dict[str, Any] | None = None

        for _ in range(max_attempts):
            poll_resp = await client.get(f"/jobs/{job_id}")
            assert poll_resp.status_code == 200
            status_body = poll_resp.json()
            if status_body["status"] == "completed":
                completed = True
                schedule_data = status_body["schedule"]
                break
            await asyncio.sleep(0.1)

        assert completed is True
        assert schedule_data is not None
        assert "assignments" in schedule_data


@pytest.mark.asyncio
async def test_api_scenario_comparison() -> None:
    """Verify comparing two schedules via /scenarios/compare."""
    plant = PlantGenerator(num_orders=3, num_machines=2, seed=42).generate(plant_id="PLANT-API-03")

    sched_a = solve(plant, objective=ObjectiveType.MAKESPAN, time_limit_seconds=2.0)
    sched_b = solve(plant, objective=ObjectiveType.TOTAL_WEIGHTED_TARDINESS, time_limit_seconds=2.0)

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Preload schedules into in-memory store
        from planpilot.api.app import _in_memory_schedules

        _in_memory_schedules[sched_a.id] = sched_a
        _in_memory_schedules[sched_b.id] = sched_b

        compare_resp = await client.post(
            "/scenarios/compare",
            json={
                "schedule_a_id": sched_a.id,
                "schedule_b_id": sched_b.id,
            },
        )
        assert compare_resp.status_code == 200
        body = compare_resp.json()
        assert body["recommended_schedule"] in {"Schedule A", "Schedule B"}
        assert len(body["trade_offs"]) > 0


@pytest.mark.asyncio
async def test_api_onboarding_upload() -> None:
    """Verify uploading raw CSV files via /onboarding produces readiness report."""
    gen = SAPExtractGenerator(seed=42)
    data = gen.generate_clean_dataset(
        num_products=2, num_components=3, num_work_centers=2, num_orders=2
    )

    files = [
        ("files", (f"{table_name}.csv", df.to_csv(index=False), "text/csv"))
        for table_name, df in data.items()
    ]

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/onboarding?format_type=sap", files=files)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["readiness_score"] == 1.0
        assert body["plant_created"] is True
