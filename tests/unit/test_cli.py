"""Unit tests for the PlanPilot Typer CLI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from planpilot.agents.rule_agent import RuleAgentResponse
from planpilot.cli import app
from planpilot.domain.generator import PlantGenerator
from planpilot.rules.dsl import ForbidSequence
from planpilot.solver.scheduler import solve

runner = CliRunner()


@pytest.fixture
def cli_plant_and_schedule(tmp_path: Path) -> tuple[Path, Path]:
    """Create a temporary plant and solved schedule for CLI tests."""
    plant = PlantGenerator(num_orders=3, num_machines=2, seed=42).generate("P_CLI_TEST")
    sched = solve(plant)
    assert sched is not None

    plant_file = tmp_path / "plant.json"
    plant_file.write_text(plant.model_dump_json(indent=2), encoding="utf-8")

    sched_file = tmp_path / "sched.json"
    sched_file.write_text(sched.model_dump_json(indent=2), encoding="utf-8")

    return plant_file, sched_file


class TestCLI:
    def test_solve_command(self, cli_plant_and_schedule: tuple[Path, Path], tmp_path: Path) -> None:
        plant_file, _ = cli_plant_and_schedule
        out_file = tmp_path / "solved_out.json"

        cmd = ["solve", str(plant_file), "--time-limit", "5", "--out", str(out_file)]
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0
        assert "Schedule solved successfully!" in result.stdout
        assert "Makespan:" in result.stdout
        assert out_file.exists()

    def test_rule_command(self, cli_plant_and_schedule: tuple[Path, Path]) -> None:
        plant_file, _ = cli_plant_and_schedule
        prompt = "Never run aluminium after titanium on Machine M1 without a 30 minute cleanout"

        mock_resp = RuleAgentResponse(
            status="applied",
            message="Rule applied successfully",
            rule=ForbidSequence(
                machine_id="M1",
                from_material="titanium",
                to_material="aluminium",
                unless_cleanout_min=30,
            ),
        )

        with patch("planpilot.cli.RuleAgent") as mock_agent_cls:
            mock_inst = mock_agent_cls.return_value
            mock_inst.process_natural_language_rule.return_value = mock_resp

            result = runner.invoke(app, ["rule", prompt, "--plant", str(plant_file)])
            assert result.exit_code == 0
            assert "Processing rule:" in result.stdout
            assert "Rule Validated & Applied" in result.stdout

    def test_explain_command(self, cli_plant_and_schedule: tuple[Path, Path]) -> None:
        plant_file, sched_file = cli_plant_and_schedule
        plant_data = json.loads(plant_file.read_text(encoding="utf-8"))
        first_order_id = plant_data["orders"][0]["id"]

        result = runner.invoke(app, ["explain", str(plant_file), str(sched_file), first_order_id])
        assert result.exit_code == 0
        assert "Lateness Diagnosis:" in result.stdout
        assert "Order ID:" in result.stdout

    def test_compare_command(self, cli_plant_and_schedule: tuple[Path, Path]) -> None:
        plant_file, sched_file = cli_plant_and_schedule

        cmd = ["compare", str(sched_file), str(sched_file), "--plant", str(plant_file)]
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0
        assert "Scenario Comparison Result" in result.stdout
        assert "Winner:" in result.stdout

    def test_onboard_command(self) -> None:
        # Use existing case_01 from evals/erp_cases
        case_dir = Path("evals/erp_cases/case_01")
        if case_dir.exists():
            result = runner.invoke(app, ["onboard", str(case_dir)])
            assert result.exit_code == 0
            assert "Profiling" in result.stdout
            assert "Validation Report" in result.stdout
