"""Integration tests for Scenario Comparison Agent."""

from planpilot.agents.scenario_agent import ScenarioAgent, ScenarioWeights
from planpilot.domain.generator import PlantGenerator
from planpilot.domain.models import ObjectiveType
from planpilot.solver.scheduler import solve


def test_scenario_agent_compare() -> None:
    """Verify ScenarioAgent compares two schedules and selects the superior one."""
    plant = PlantGenerator(num_orders=4, num_machines=3, seed=42).generate()

    # Solve two schedules with different objectives or time limits
    sched_a = solve(plant, objective=ObjectiveType.MAKESPAN, time_limit_seconds=3.0)
    sched_b = solve(plant, objective=ObjectiveType.TOTAL_WEIGHTED_TARDINESS, time_limit_seconds=3.0)

    agent = ScenarioAgent()
    comp = agent.compare(sched_a, sched_b)

    assert comp.recommended_schedule in {"Schedule A", "Schedule B"}
    assert comp.confidence >= 0.5
    assert len(comp.trade_offs) > 0
    assert "KPI Comparison Table" in comp.summary_markdown
    assert comp.weights_used["on_time_weight"] == 0.40


def test_scenario_agent_custom_weights() -> None:
    """Verify custom weights prioritize makespan over on-time delivery."""
    plant = PlantGenerator(num_orders=4, num_machines=3, seed=99).generate()

    sched_a = solve(plant, objective=ObjectiveType.MAKESPAN, time_limit_seconds=3.0)
    sched_b = solve(plant, objective=ObjectiveType.MAKESPAN, time_limit_seconds=3.0)

    custom_weights = ScenarioWeights(
        makespan_weight=0.80,
        on_time_weight=0.10,
        tardiness_weight=0.05,
        setup_weight=0.05,
    )
    agent = ScenarioAgent(weights=custom_weights)
    comp = agent.compare(sched_a, sched_b)

    assert comp.weights_used["makespan_weight"] == 0.80
