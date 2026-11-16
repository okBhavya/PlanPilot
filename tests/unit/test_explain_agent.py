"""Unit tests for Grounded ExplainAgent and verifier feedback loop."""

from __future__ import annotations

from unittest.mock import MagicMock

from planpilot.agents.explain_agent import ExplainAgent
from planpilot.explain.benchmarks import create_competing_priority_instance
from planpilot.llm.client import LLMClient, LLMResponse, LLMTrace, Provider
from planpilot.solver.scheduler import solve


def make_mock_trace() -> LLMTrace:
    return LLMTrace(
        provider=Provider.OPENAI,
        model="gpt-4o",
        prompt_tokens=80,
        completion_tokens=40,
        total_tokens=120,
        latency_seconds=0.2,
        cost_usd=0.001,
        cached=False,
    )


class TestExplainAgent:
    def test_explain_lateness_grounded_success(self) -> None:
        """ExplainAgent generates grounded explanation, passes verification on first attempt."""
        plant, late_order_id = create_competing_priority_instance()
        schedule = solve(plant)

        mock_llm = MagicMock(spec=LLMClient)
        # Mock LLM provides a faithful explanation with exact citations
        trace = make_mock_trace()
        mock_response = LLMResponse(
            content=(
                "Order ORD_LATE finished at 50, missing due date of 35 by 15m [F1] [F2]. "
                "Machine M1 was occupied by order ORD_RUSH from 0 to 30 [F3]."
            ),
            tool_calls=[],
            trace=trace,
        )
        mock_llm.complete.return_value = mock_response

        agent = ExplainAgent(llm_client=mock_llm)
        resp = agent.explain_lateness(schedule, plant, late_order_id)

        assert resp.order_id == late_order_id
        assert resp.root_cause_type == "competing_order"
        assert resp.faithfulness_score == 1.0
        assert resp.verification.valid is True
        assert len(resp.facts) >= 3
        assert mock_llm.complete.call_count == 1

    def test_explain_lateness_self_correction_loop(self) -> None:
        """If initial LLM output lacks citations, agent prompts model to correct and succeeds."""
        plant, late_order_id = create_competing_priority_instance()
        schedule = solve(plant)

        mock_llm = MagicMock(spec=LLMClient)
        trace = make_mock_trace()

        # Turn 1: Model outputs an uncited explanation (fails verifier)
        turn1_resp = LLMResponse(
            content="Order ORD_LATE was late because Machine 1 had a competing job.",
            tool_calls=[],
            trace=trace,
        )

        # Turn 2: Model corrects itself by adding citations and numbers from facts
        turn2_resp = LLMResponse(
            content=(
                "Order ORD_LATE completed at 50 instead of its due date of 35 [F1] [F2]. "
                "Machine M1 was occupied by order ORD_RUSH from 0 to 30 [F3]."
            ),
            tool_calls=[],
            trace=trace,
        )

        mock_llm.complete.side_effect = [turn1_resp, turn2_resp]

        agent = ExplainAgent(llm_client=mock_llm)
        resp = agent.explain_lateness(schedule, plant, late_order_id, max_retries=1)

        assert resp.verification.valid is True
        assert resp.faithfulness_score == 1.0
        # Model was called twice (initial attempt + 1 retry)
        assert mock_llm.complete.call_count == 2
        # Feedback message was sent on retry
        second_call_messages = mock_llm.complete.call_args_list[1][0][0]
        assert any(
            "Your explanation failed verification" in m.get("content", "")
            for m in second_call_messages
        )

    def test_explain_schedule_diff_grounded(self) -> None:
        """ExplainAgent explains schedule movements with verified citations."""
        plant, late_order_id = create_competing_priority_instance()
        schedule_before = solve(plant)
        schedule_after = solve(plant)

        mock_llm = MagicMock(spec=LLMClient)
        trace = make_mock_trace()
        mock_response = LLMResponse(
            content="Makespan remained at 50 between both runs with delta 0 [F1].",
            tool_calls=[],
            trace=trace,
        )
        mock_llm.complete.return_value = mock_response

        agent = ExplainAgent(llm_client=mock_llm)
        resp = agent.explain_schedule_diff(schedule_before, schedule_after, plant)

        assert resp.verification.valid is True
        assert resp.faithfulness_score == 1.0
        assert len(resp.facts) >= 2
