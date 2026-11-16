"""PlanPilot Command Line Interface (Typer).

Commands:
- solve: Solve plant schedule using CP-SAT with configurable rules and objectives
- rule: Parse, validate, and apply natural-language scheduling rules
- explain: Trace critical-chain root causes for late orders or schedule movements
- compare: Multi-criteria scenario comparison between two schedules
- onboard: Profile, validate, and map messy ERP CSV extracts to plant models
- eval: Run benchmark evaluation harness and generate results
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from planpilot.agents.rule_agent import RuleAgent
from planpilot.agents.scenario_agent import ScenarioAgent, ScenarioWeights
from planpilot.domain.models import Plant, Schedule
from planpilot.erp.mapper import ERPMapper
from planpilot.erp.models import ERPFormat
from planpilot.erp.profiler import DuckDBProfiler
from planpilot.erp.validators import DeterministicValidator
from planpilot.explain.diff import ScheduleDiffEngine
from planpilot.explain.facts import FactExtractor
from planpilot.llm.client import LLMClient, Provider
from planpilot.rules.dsl import Rule
from planpilot.rules.store import RuleStore
from planpilot.solver.scheduler import solve

app = typer.Typer(
    name="planpilot",
    help="PlanPilot: AI Copilot on top of CP-SAT Production Scheduling.",
    add_completion=False,
)


@app.command("solve")
def solve_cmd(
    plant_path: Annotated[Path, typer.Argument(help="Path to plant JSON file", exists=True)],
    rules_path: Annotated[
        Path | None, typer.Option("--rules", help="Path to rules JSON file")
    ] = None,
    time_limit: Annotated[
        int, typer.Option("--time-limit", "-t", help="Solver time limit in seconds")
    ] = 30,
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Output path for solved schedule JSON")
    ] = None,
) -> None:
    """Solve production schedule for a plant instance using OR-Tools CP-SAT."""
    typer.echo(f"Loading plant configuration from {plant_path}...")
    plant_dict = json.loads(plant_path.read_text(encoding="utf-8"))
    plant = Plant.model_validate(plant_dict)

    rules: list[Rule] = []
    if rules_path and rules_path.exists():
        rules_data = json.loads(rules_path.read_text(encoding="utf-8"))
        if isinstance(rules_data, list):
            from pydantic import TypeAdapter

            rules = [TypeAdapter(Rule).validate_python(r) for r in rules_data]

    typer.echo(
        f"Solving schedule for plant '{plant.id}' "
        f"({len(plant.orders)} orders, {len(plant.machines)} machines)..."
    )
    schedule = solve(plant, rules=rules, time_limit_seconds=time_limit)

    if schedule is None:
        typer.secho(
            "Solver returned INFEASIBLE or reached time limit without finding a solution.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    typer.secho("Schedule solved successfully!", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Makespan:         {schedule.kpis.makespan} min")
    typer.echo(f"  On-Time Delivery: {schedule.kpis.on_time_pct:.1f}%")
    typer.echo(f"  Total Tardiness:  {schedule.kpis.total_tardiness} min")
    typer.echo(f"  Total Setup Time: {schedule.kpis.total_setup_time} min")
    typer.echo(f"  Solver Status:    {schedule.solver_stats.status}")
    typer.echo(f"  Solve Wall Time:  {schedule.solver_stats.wall_time_seconds:.3f} s")

    if out:
        out.write_text(schedule.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"Schedule exported to {out}")


@app.command()
def rule(
    prompt: Annotated[str, typer.Argument(help="Natural-language scheduling rule")],
    plant_path: Annotated[
        Path | None, typer.Option("--plant", "-p", help="Plant JSON file for entity resolution")
    ] = None,
    model: Annotated[str, typer.Option("--model", "-m", help="LLM model name")] = "gpt-4o",
    provider: Annotated[
        str, typer.Option("--provider", help="LLM provider: openai, anthropic, openai_compatible")
    ] = "openai",
) -> None:
    """Parse a natural-language rule into a validated Rule DSL constraint."""
    if plant_path and plant_path.exists():
        plant = Plant.model_validate_json(plant_path.read_text(encoding="utf-8"))
    else:
        from planpilot.domain.generator import PlantGenerator

        plant = PlantGenerator(num_orders=5, num_machines=4, seed=42).generate("PLANT_DEFAULT")

    rule_store = RuleStore(plant)
    client = LLMClient(provider=Provider(provider), model=model, enable_cache=True)
    agent = RuleAgent(plant=plant, rule_store=rule_store, llm_client=client)

    typer.echo(f"Processing rule: '{prompt}'")
    resp = agent.process_natural_language_rule(prompt)

    if resp.status == "applied" and resp.rule:
        typer.secho(
            f"Rule Validated & Applied [{resp.rule.type.value}]:", fg=typer.colors.GREEN, bold=True
        )
        typer.echo(resp.rule.model_dump_json(indent=2))
    elif resp.status == "clarification_needed":
        typer.secho("Clarification Needed:", fg=typer.colors.YELLOW, bold=True)
        typer.echo(resp.clarification_question or resp.message)
    elif resp.status == "rejected":
        typer.secho("Rule Rejected:", fg=typer.colors.RED, bold=True)
        typer.echo(resp.message)
    else:
        typer.echo(f"Status: {resp.status} - {resp.message}")


@app.command()
def explain(
    plant_path: Annotated[Path, typer.Argument(help="Plant JSON file", exists=True)],
    schedule_path: Annotated[Path, typer.Argument(help="Schedule A JSON file", exists=True)],
    order_id: Annotated[str, typer.Argument(help="Order ID to explain")],
    why_moved: Annotated[
        Path | None, typer.Option("--why-moved", help="Schedule B JSON file to explain movement")
    ] = None,
) -> None:
    """Explain why an order is late or why a job moved between schedules."""
    plant = Plant.model_validate_json(plant_path.read_text(encoding="utf-8"))
    sched_a = Schedule.model_validate_json(schedule_path.read_text(encoding="utf-8"))

    if why_moved and why_moved.exists():
        sched_b = Schedule.model_validate_json(why_moved.read_text(encoding="utf-8"))
        typer.echo(f"Diffing schedules to explain movement for order '{order_id}'...")
        comp = ScheduleDiffEngine().compare(before=sched_a, after=sched_b, plant=plant)
        target_deltas = [d for d in comp.deltas if d.order_id == order_id]

        if not target_deltas:
            typer.echo(f"No movements detected for order '{order_id}'.")
            return

        typer.secho(f"Schedule Deltas for {order_id}:", fg=typer.colors.CYAN, bold=True)
        for d in target_deltas:
            typer.echo(
                f"  Op {d.operation_id}: {d.movement_type.value} "
                f"(start {d.before_start}->{d.after_start}, "
                f"mach {d.before_machine_id}->{d.after_machine_id})"
            )
            typer.echo(f"    Attributed Cause: {d.attributed_cause}")
    else:
        typer.echo(f"Diagnosing lateness root cause for order '{order_id}'...")
        extractor = FactExtractor()
        diag = extractor.diagnose_order_lateness(sched_a, plant, order_id)

        typer.secho("Lateness Diagnosis:", fg=typer.colors.CYAN, bold=True)
        typer.echo(f"  Order ID:        {diag.order_id}")
        typer.echo(f"  Completion Time: {diag.completion_time}")
        typer.echo(f"  Due Date:        {diag.due_date}")
        typer.echo(f"  Tardiness:       {diag.tardiness} min")
        typer.secho(f"  Primary Cause:   {diag.root_cause_type}", fg=typer.colors.YELLOW, bold=True)
        typer.echo(f"  Explanation:     {diag.root_cause_description}")
        typer.echo("\nCited Facts:")
        for f in diag.facts:
            typer.echo(f"  [{f.id}] {f.description}")


@app.command()
def compare(
    schedule_a_path: Annotated[Path, typer.Argument(help="Baseline Schedule A JSON", exists=True)],
    schedule_b_path: Annotated[Path, typer.Argument(help="Updated Schedule B JSON", exists=True)],
    plant_path: Annotated[Path | None, typer.Option("--plant", help="Plant JSON file")] = None,
    weight_otd: Annotated[
        float, typer.Option("--weight-otd", help="Weight for On-Time Delivery")
    ] = 0.40,
    weight_makespan: Annotated[
        float, typer.Option("--weight-makespan", help="Weight for Makespan")
    ] = 0.15,
    weight_tardiness: Annotated[
        float, typer.Option("--weight-tardiness", help="Weight for Tardiness")
    ] = 0.30,
    weight_setup: Annotated[
        float, typer.Option("--weight-setup", help="Weight for Changeover Setups")
    ] = 0.15,
) -> None:
    """Compare two production schedules with multi-criteria utility weighting."""
    sched_a = Schedule.model_validate_json(schedule_a_path.read_text(encoding="utf-8"))
    sched_b = Schedule.model_validate_json(schedule_b_path.read_text(encoding="utf-8"))

    weights = ScenarioWeights(
        on_time_weight=weight_otd,
        makespan_weight=weight_makespan,
        tardiness_weight=weight_tardiness,
        setup_weight=weight_setup,
    )
    agent = ScenarioAgent(weights=weights)
    res = agent.compare(sched_a, sched_b)

    typer.secho("=== Scenario Comparison Result ===", fg=typer.colors.CYAN, bold=True)
    winner_str = res.recommended_schedule.upper()
    typer.echo(f"Winner: {winner_str} (Score A: {res.score_a:.2f}, Score B: {res.score_b:.2f})")
    typer.echo("\nKPI Deltas (Schedule B vs A):")
    typer.echo(f"  Delta Makespan:   {res.kpi_diff.makespan_delta:+d} min")
    typer.echo(f"  Delta On-Time %:  {res.kpi_diff.on_time_pct_delta:+.2f}%")
    typer.echo(f"  Delta Tardiness:  {res.kpi_diff.total_tardiness_delta:+d} min")
    typer.echo(f"  Delta Setup Time: {res.kpi_diff.total_setup_time_delta:+d} min")
    typer.secho("\nExecutive Recommendation:", fg=typer.colors.GREEN, bold=True)
    typer.echo(res.summary_markdown)


@app.command()
def onboard(
    erp_dir: Annotated[
        Path, typer.Argument(help="Directory containing ERP CSV files", exists=True)
    ],
    out_plant: Annotated[
        Path | None, typer.Option("--out-plant", "-o", help="Output path for mapped Plant JSON")
    ] = None,
) -> None:
    """Profile, validate, and onboard ERP extract tables into a clean plant model."""
    csv_files = list(erp_dir.glob("*.csv"))
    if not csv_files:
        typer.secho(f"No CSV files found in {erp_dir}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"Profiling {len(csv_files)} CSV files in {erp_dir}...")
    tables = {f.stem: pd.read_csv(f) for f in csv_files}

    profiler = DuckDBProfiler()
    profiles = profiler.profile_all(tables)
    typer.echo("Table Summary:")
    for p in profiles.tables.values():
        typer.echo(f"  {p.table_name}: {p.row_count} rows, {len(p.columns)} columns")

    typer.echo("\nRunning deterministic integrity validators...")
    validator = DeterministicValidator()
    report = validator.validate(tables)

    status_color = typer.colors.YELLOW if report.errors else typer.colors.GREEN
    typer.secho(
        f"\nValidation Report: Found {len(report.errors)} issue(s)",
        fg=status_color,
        bold=True,
    )
    for err in report.errors:
        color = typer.colors.RED if err.severity.value == "critical" else typer.colors.YELLOW
        sev_tag = err.severity.value.upper()
        msg = f"  [{sev_tag}] {err.error_type.value} in {err.table}.{err.column}: {err.message}"
        typer.secho(msg, fg=color)

    if report.critical_count == 0:
        typer.secho(
            "\nExtract passed critical checks. Mapping to Plant domain...",
            fg=typer.colors.GREEN,
        )
        mapper = ERPMapper()
        fmt = ERPFormat.SAP if "CRHD" in tables else ERPFormat.LEGACY
        plant = mapper.map_to_plant(tables, format_type=fmt)
        typer.echo(
            f"Successfully constructed Plant '{plant.id}' "
            f"({len(plant.orders)} orders, {len(plant.machines)} machines)"
        )
        if out_plant:
            out_plant.write_text(plant.model_dump_json(indent=2), encoding="utf-8")
            typer.echo(f"Plant saved to {out_plant}")
    else:
        err_msg = (
            f"\nOnboarding BLOCKED: {report.critical_count} critical error(s) must be resolved."
        )
        typer.secho(err_msg, fg=typer.colors.RED, bold=True)


@app.command("eval")
def eval_cmd(
    output: Annotated[
        Path, typer.Option("--output", "-o", help="Output directory for eval results")
    ] = Path("results"),
) -> None:
    """Run full evaluation harness across rules, explainability, ERP onboarding, and performance."""
    from evals.harness import run_full_evaluation

    typer.echo(f"Starting evaluation harness (output: {output})...")
    run_full_evaluation(output_dir=output)
    summary_path = output / "SUMMARY.md"
    typer.secho(
        f"Evaluation completed successfully! See {summary_path}",
        fg=typer.colors.GREEN,
        bold=True,
    )


if __name__ == "__main__":
    app()
