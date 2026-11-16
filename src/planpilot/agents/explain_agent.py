"""Grounded Explainability Agent.

Produces factual, citation-grounded natural-language answers to:
- "Why is order X late?"
- "Why did job Y move?"
Runs deterministic CitationVerifier to guarantee zero hallucination.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from planpilot.domain.models import Plant, Schedule
from planpilot.explain.diff import ScheduleComparison, ScheduleDiffEngine
from planpilot.explain.facts import Fact, FactExtractor, LatenessDiagnosis
from planpilot.explain.verifier import CitationVerifier, VerificationResult
from planpilot.llm.client import LLMClient


class ExplainAgentResponse(BaseModel):
    """Grounded explanation response returned by the ExplainAgent."""

    model_config = ConfigDict(frozen=True)

    order_id: str | None = None
    question: str
    explanation: str
    faithfulness_score: float
    verification: VerificationResult
    facts: list[Fact]
    root_cause_type: str | None = None


EXPLAIN_SYSTEM_PROMPT = """You are PlanPilot's Grounded Production Scheduling Explainability Agent.
Your job is to explain solver decisions strictly based on the provided mathematical facts.

CRITICAL NON-NEGOTIABLE CITATION RULES:
1. EVERY SINGLE SENTENCE you write MUST contain at least one citation tag [Fn] (e.g. [F1], [F2]).
2. NEVER mention any number, timestamp, duration, or delay that is not in the cited facts.
3. NEVER guess, assume, or speculate. The facts in the table are the sole source of truth.
4. Keep the explanation concise, professional, and clear for a plant production manager.
"""


class ExplainAgent:
    """Agent that explains schedule decisions grounded strictly in solver facts."""

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client
        self.fact_extractor = FactExtractor()
        self.diff_engine = ScheduleDiffEngine()
        self.verifier = CitationVerifier()

    def explain_lateness(
        self,
        schedule: Schedule,
        plant: Plant,
        order_id: str,
        max_retries: int = 2,
    ) -> ExplainAgentResponse:
        """Explain why order_id is late grounded in solver facts."""
        diagnosis: LatenessDiagnosis = self.fact_extractor.diagnose_order_lateness(
            schedule, plant, order_id
        )

        fact_table = diagnosis.format_fact_table()
        user_prompt = (
            f"Question: Why is order {order_id} late?\n\n"
            f"Ground-Truth Solver Facts:\n{fact_table}\n\n"
            f"Please write a 2-3 sentence explanation of why order {order_id} is late. "
            f"Every single sentence must cite [Fn]."
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        explanation = ""
        verification: VerificationResult | None = None

        for _attempt in range(max_retries + 1):
            response = self.llm.complete(messages)
            explanation = response.content
            verification = self.verifier.verify(explanation, diagnosis.facts)

            if verification.valid:
                break

            # If verification failed, provide error feedback to model for self-correction
            feedback = (
                "Your explanation failed verification due to the following errors:\n"
                + "\n".join(f"- {err}" for err in verification.errors)
                + "\n\nPlease rewrite the explanation ensuring EVERY sentence has citations [Fn] "
                "and contains ONLY numbers found in the cited facts."
            )
            messages.append({"role": "assistant", "content": explanation})
            messages.append({"role": "user", "content": feedback})

        assert verification is not None

        return ExplainAgentResponse(
            order_id=order_id,
            question=f"Why is order {order_id} late?",
            explanation=explanation,
            faithfulness_score=verification.faithfulness_score,
            verification=verification,
            facts=diagnosis.facts,
            root_cause_type=diagnosis.root_cause_type,
        )

    def explain_schedule_diff(
        self,
        before: Schedule,
        after: Schedule,
        plant: Plant,
        reason_hint: str = "",
        target_order_id: str | None = None,
        max_retries: int = 2,
    ) -> ExplainAgentResponse:
        """Explain movements and differences between before and after schedules."""
        comparison: ScheduleComparison = self.diff_engine.compare(
            before, after, plant, reason_hint=reason_hint
        )

        fact_table = comparison.format_fact_table()
        target_clause = f" for order {target_order_id}" if target_order_id else ""
        user_prompt = (
            f"Question: Why did assignments move between the schedules{target_clause}?\n\n"
            f"Ground-Truth Comparison Facts:\n{fact_table}\n\n"
            f"Please write a concise explanation. Every sentence must cite [Fn]."
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": EXPLAIN_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        explanation = ""
        verification: VerificationResult | None = None

        for _attempt in range(max_retries + 1):
            response = self.llm.complete(messages)
            explanation = response.content
            verification = self.verifier.verify(explanation, comparison.facts)

            if verification.valid:
                break

            feedback = (
                "Your explanation failed verification:\n"
                + "\n".join(f"- {err}" for err in verification.errors)
                + "\n\nPlease rewrite ensuring every sentence cites [Fn] with only cited numbers."
            )
            messages.append({"role": "assistant", "content": explanation})
            messages.append({"role": "user", "content": feedback})

        assert verification is not None

        return ExplainAgentResponse(
            order_id=target_order_id,
            question=f"Why did assignments move{target_clause}?",
            explanation=explanation,
            faithfulness_score=verification.faithfulness_score,
            verification=verification,
            facts=comparison.facts,
            root_cause_type="schedule_resequencing",
        )
