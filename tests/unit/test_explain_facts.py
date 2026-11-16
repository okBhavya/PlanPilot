"""Unit tests for FactExtractor and critical-chain lateness tracing across 5 canonical instances."""

from __future__ import annotations

from planpilot.domain.models import ObjectiveType, SolverStatus
from planpilot.explain.benchmarks import (
    create_cleanout_gap_instance,
    create_competing_priority_instance,
    create_downtime_overlap_instance,
    create_routing_cascade_instance,
    create_supply_delay_instance,
)
from planpilot.explain.facts import FactExtractor
from planpilot.solver.scheduler import solve


class TestExplainFactExtractor:
    def test_handcrafted_competing_priority(self) -> None:
        """Handcrafted Instance 1: Root cause is competing high-priority order taking machine."""
        plant, late_order_id = create_competing_priority_instance()
        schedule = solve(plant)
        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        extractor = FactExtractor()
        diagnosis = extractor.diagnose_order_lateness(schedule, plant, late_order_id)

        assert diagnosis.tardiness > 0
        assert diagnosis.root_cause_type == "competing_order"
        assert "ORD_RUSH" in diagnosis.root_cause_description
        assert len(diagnosis.critical_chain) > 0
        assert len(diagnosis.facts) >= 3

        # Verify fact table format
        table = diagnosis.format_fact_table()
        assert "[F1]" in table
        assert "[F2]" in table

    def test_handcrafted_supply_delay(self) -> None:
        """Handcrafted Instance 2: Root cause is upstream raw material release delay."""
        plant, late_order_id = create_supply_delay_instance()
        schedule = solve(plant)
        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        extractor = FactExtractor()
        diagnosis = extractor.diagnose_order_lateness(schedule, plant, late_order_id)

        assert diagnosis.tardiness > 0
        assert diagnosis.root_cause_type == "release_delay"
        assert "release time" in diagnosis.root_cause_description.lower()

    def test_handcrafted_cleanout_gap(self) -> None:
        """Handcrafted Instance 3: Root cause is material changeover / cleanout setup gap."""
        plant, late_order_id = create_cleanout_gap_instance()
        schedule = solve(plant, objective=ObjectiveType.TOTAL_WEIGHTED_TARDINESS)
        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        extractor = FactExtractor()
        diagnosis = extractor.diagnose_order_lateness(schedule, plant, late_order_id)

        assert diagnosis.tardiness > 0
        assert diagnosis.root_cause_type == "changeover_setup"
        assert "changeover setup" in diagnosis.root_cause_description.lower()

    def test_handcrafted_downtime_overlap(self) -> None:
        """Handcrafted Instance 4: Root cause is planned machine downtime blackout."""
        plant, late_order_id = create_downtime_overlap_instance()
        schedule = solve(plant)
        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        extractor = FactExtractor()
        diagnosis = extractor.diagnose_order_lateness(schedule, plant, late_order_id)

        assert diagnosis.tardiness > 0
        assert diagnosis.root_cause_type == "downtime_window"
        assert "downtime" in diagnosis.root_cause_description.lower()

    def test_handcrafted_routing_cascade(self) -> None:
        """Handcrafted Instance 5: Root cause is cumulative duration of sequential operations."""
        plant, late_order_id = create_routing_cascade_instance()
        schedule = solve(plant)
        assert schedule.solver_stats.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

        extractor = FactExtractor()
        diagnosis = extractor.diagnose_order_lateness(schedule, plant, late_order_id)

        assert diagnosis.tardiness > 0
        assert diagnosis.root_cause_type == "routing_cascade"
        assert len(diagnosis.critical_chain) >= 4

    def test_global_fact_extraction(self) -> None:
        """Global facts contain makespan, completions, and due dates."""
        plant, _ = create_competing_priority_instance()
        schedule = solve(plant)

        extractor = FactExtractor()
        facts = extractor.extract_facts(schedule, plant)

        assert len(facts) >= 3
        assert any("makespan" in f.description.lower() for f in facts)
        assert any(f.order_id == "ORD_RUSH" for f in facts)
        assert any(f.order_id == "ORD_LATE" for f in facts)
