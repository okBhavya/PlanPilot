"""Scenario comparison agent for evaluating alternative production schedules."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from planpilot.domain.models import KPIs, Schedule
from planpilot.explain.diff import KPIDiff
from planpilot.llm.client import LLMClient


class ScenarioWeights(BaseModel):
    """Configurable trade-off weights for multi-criteria schedule evaluation."""

    model_config = ConfigDict(frozen=True)

    on_time_weight: float = 0.40  # On-time delivery % priority
    tardiness_weight: float = 0.30  # Total tardiness minimization priority
    makespan_weight: float = 0.15  # Total duration minimization priority
    setup_weight: float = 0.15  # Changeover time minimization priority


class ScenarioComparison(BaseModel):
    """Structured deterministic comparison and recommendation between two schedules."""

    model_config = ConfigDict(frozen=True)

    schedule_a_id: str
    schedule_b_id: str
    kpis_a: KPIs
    kpis_b: KPIs
    kpi_diff: KPIDiff
    recommended_schedule: str  # "Schedule A" or "Schedule B"
    score_a: float
    score_b: float
    confidence: float
    trade_offs: list[str]
    weights_used: dict[str, float]
    summary_markdown: str


SYSTEM_PROMPT = """You are PlanPilot's Production Scenario Analysis Copilot.
You evaluate two alternative schedules (Schedule A and Schedule B) for a plant.

Your job is to provide a clear, executive-level comparison and recommendation
based on the provided KPI deltas and utility scores.
Rules:
1. Always state exact numerical metrics and deltas (makespan, on-time %, tardiness, setup).
2. Explicitly explain the trade-offs and cite the evaluation weights used.
3. Keep the tone concise, authoritative, and actionable for a plant operations manager.
"""


class ScenarioAgent:
    """Agent that compares two schedules and generates grounded, explainable recommendations."""

    def __init__(
        self,
        weights: ScenarioWeights | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.weights = weights or ScenarioWeights()
        self.llm_client = llm_client

    def compare(
        self, schedule_a: Schedule, schedule_b: Schedule
    ) -> ScenarioComparison:
        """Deterministically compare two schedules and synthesize an explainable recommendation."""
        diff = KPIDiff.calculate(schedule_a.kpis, schedule_b.kpis)

        # Compute utility scores (higher is better)
        # 1. On-time delivery rate (0..100)
        on_time_a = schedule_a.kpis.on_time_pct
        on_time_b = schedule_b.kpis.on_time_pct

        # 2. Tardiness penalty (lower tardiness = higher score)
        max_tard = max(schedule_a.kpis.total_tardiness, schedule_b.kpis.total_tardiness, 1)
        tard_score_a = 100.0 * (1.0 - (schedule_a.kpis.total_tardiness / max_tard))
        tard_score_b = 100.0 * (1.0 - (schedule_b.kpis.total_tardiness / max_tard))

        # 3. Makespan efficiency (lower makespan = higher score)
        max_ms = max(schedule_a.kpis.makespan, schedule_b.kpis.makespan, 1)
        ms_score_a = 100.0 * (1.0 - (schedule_a.kpis.makespan / max_ms))
        ms_score_b = 100.0 * (1.0 - (schedule_b.kpis.makespan / max_ms))

        # 4. Setup efficiency (lower setup = higher score)
        max_setup = max(schedule_a.kpis.total_setup_time, schedule_b.kpis.total_setup_time, 1)
        setup_score_a = 100.0 * (1.0 - (schedule_a.kpis.total_setup_time / max_setup))
        setup_score_b = 100.0 * (1.0 - (schedule_b.kpis.total_setup_time / max_setup))

        # Weighted aggregate score
        score_a = (
            (on_time_a * self.weights.on_time_weight)
            + (tard_score_a * self.weights.tardiness_weight)
            + (ms_score_a * self.weights.makespan_weight)
            + (setup_score_a * self.weights.setup_weight)
        )
        score_b = (
            (on_time_b * self.weights.on_time_weight)
            + (tard_score_b * self.weights.tardiness_weight)
            + (ms_score_b * self.weights.makespan_weight)
            + (setup_score_b * self.weights.setup_weight)
        )

        recommended = "Schedule B" if score_b >= score_a else "Schedule A"
        score_diff = abs(score_b - score_a)
        confidence = min(1.0, max(0.5, 0.5 + (score_diff / 50.0)))

        # Formulate explicit trade-offs
        trade_offs: list[str] = []
        if diff.on_time_pct_delta > 0:
            trade_offs.append(
                f"Schedule B improves on-time delivery by +{diff.on_time_pct_delta:.1f}%."
            )
        elif diff.on_time_pct_delta < 0:
            trade_offs.append(
                f"Schedule A delivers higher on-time (+{abs(diff.on_time_pct_delta):.1f}%)."
            )

        if diff.total_tardiness_delta < 0:
            trade_offs.append(
                f"Schedule B reduces total tardiness by {abs(diff.total_tardiness_delta)} minutes."
            )
        elif diff.total_tardiness_delta > 0:
            trade_offs.append(
                f"Schedule A has {diff.total_tardiness_delta} fewer minutes of tardiness."
            )

        if diff.makespan_delta < 0:
            trade_offs.append(
                f"Schedule B finishes {abs(diff.makespan_delta)} minutes faster."
            )
        elif diff.makespan_delta > 0:
            trade_offs.append(
                f"Schedule A completes the entire horizon {diff.makespan_delta} minutes faster."
            )

        if diff.total_setup_time_delta < 0:
            trade_offs.append(
                f"Schedule B saves {abs(diff.total_setup_time_delta)} minutes in cleanouts."
            )
        elif diff.total_setup_time_delta > 0:
            trade_offs.append(
                f"Schedule A incurs {diff.total_setup_time_delta} minutes less setup time."
            )

        if not trade_offs:
            trade_offs.append(
                "Both schedules achieve identical key operational performance metrics."
            )

        weights_dict = {
            "on_time_weight": self.weights.on_time_weight,
            "tardiness_weight": self.weights.tardiness_weight,
            "makespan_weight": self.weights.makespan_weight,
            "setup_weight": self.weights.setup_weight,
        }

        # Build markdown summary
        md_summary = self._generate_markdown(
            schedule_a,
            schedule_b,
            diff,
            recommended,
            score_a,
            score_b,
            confidence,
            trade_offs,
            weights_dict,
        )

        return ScenarioComparison(
            schedule_a_id=schedule_a.id,
            schedule_b_id=schedule_b.id,
            kpis_a=schedule_a.kpis,
            kpis_b=schedule_b.kpis,
            kpi_diff=diff,
            recommended_schedule=recommended,
            score_a=round(score_a, 2),
            score_b=round(score_b, 2),
            confidence=round(confidence, 2),
            trade_offs=trade_offs,
            weights_used=weights_dict,
            summary_markdown=md_summary,
        )

    def _generate_markdown(
        self,
        schedule_a: Schedule,
        schedule_b: Schedule,
        diff: KPIDiff,
        recommended: str,
        score_a: float,
        score_b: float,
        confidence: float,
        trade_offs: list[str],
        weights_dict: dict[str, float],
    ) -> str:
        """Synthesize markdown summary with LLM or deterministic fallback."""
        ms_pct = (
            (diff.makespan_delta / schedule_a.kpis.makespan * 100.0)
            if schedule_a.kpis.makespan > 0
            else 0.0
        )
        tard_pct = (
            (diff.total_tardiness_delta / schedule_a.kpis.total_tardiness * 100.0)
            if schedule_a.kpis.total_tardiness > 0
            else 0.0
        )
        setup_pct = (
            (diff.total_setup_time_delta / schedule_a.kpis.total_setup_time * 100.0)
            if schedule_a.kpis.total_setup_time > 0
            else 0.0
        )

        if self.llm_client:
            user_prompt = f"""
Schedule A KPIs:
- Makespan: {schedule_a.kpis.makespan} min
- On-Time: {schedule_a.kpis.on_time_pct:.1f}%
- Total Tardiness: {schedule_a.kpis.total_tardiness} min
- Total Setup: {schedule_a.kpis.total_setup_time} min
- Composite Score: {score_a:.1f}

Schedule B KPIs:
- Makespan: {schedule_b.kpis.makespan} min
- On-Time: {schedule_b.kpis.on_time_pct:.1f}%
- Total Tardiness: {schedule_b.kpis.total_tardiness} min
- Total Setup: {schedule_b.kpis.total_setup_time} min
- Composite Score: {score_b:.1f}

Key Deltas (B vs A):
- Makespan Delta: {diff.makespan_delta} min ({ms_pct:+.1f}%)
- On-Time Delta: {diff.on_time_pct_delta:+.1f}%
- Tardiness Delta: {diff.total_tardiness_delta} min ({tard_pct:+.1f}%)
- Setup Time Delta: {diff.total_setup_time_delta} min ({setup_pct:+.1f}%)

Evaluation Weights Used:
{json.dumps(weights_dict)}

Recommendation: {recommended} (Confidence: {confidence * 100:.0f}%)
Trade-offs: {json.dumps(trade_offs)}
"""
            try:
                response = self.llm_client.complete(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ]
                )
                if response.content.strip():
                    return response.content.strip()
            except Exception:
                pass

        # Deterministic Markdown Fallback
        lines = [
            f"# Scenario Comparison: **{recommended} Recommended**",
            "",
            f"**Recommendation Confidence:** `{confidence * 100:.0f}%`  ",
            f"**Evaluation Weights:** On-Time `{weights_dict['on_time_weight'] * 100:.0f}%` | "
            f"Tardiness `{weights_dict['tardiness_weight'] * 100:.0f}%` | "
            f"Makespan `{weights_dict['makespan_weight'] * 100:.0f}%` | "
            f"Setup `{weights_dict['setup_weight'] * 100:.0f}%`  ",
            "",
            "## KPI Comparison Table",
            "| Metric | Schedule A | Schedule B | Delta (B - A) | Change |",
            "| :--- | :--- | :--- | :--- | :--- |",
            f"| **Makespan** | {schedule_a.kpis.makespan} min | {schedule_b.kpis.makespan} min | "
            f"{diff.makespan_delta:+d} min | {ms_pct:+.1f}% |",
            f"| **On-Time Delivery** | {schedule_a.kpis.on_time_pct:.1f}% | "
            f"{schedule_b.kpis.on_time_pct:.1f}% | "
            f"{diff.on_time_pct_delta:+.1f}% | - |",
            f"| **Total Tardiness** | {schedule_a.kpis.total_tardiness} min | "
            f"{schedule_b.kpis.total_tardiness} min | "
            f"{diff.total_tardiness_delta:+d} min | {tard_pct:+.1f}% |",
            f"| **Total Setup Time** | {schedule_a.kpis.total_setup_time} min | "
            f"{schedule_b.kpis.total_setup_time} min | "
            f"{diff.total_setup_time_delta:+d} min | {setup_pct:+.1f}% |",
            f"| **Composite Score** | `{score_a:.1f}` | `{score_b:.1f}` | "
            f"`{score_b - score_a:+.1f}` | - |",
            "",
            "## Key Trade-Offs & Operational Rationale",
        ]
        for to in trade_offs:
            lines.append(f"- {to}")

        return "\n".join(lines)
