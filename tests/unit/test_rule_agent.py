"""Unit tests for the Natural-Language RuleAgent and multi-turn tool calling loop."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from planpilot.agents.rule_agent import RuleAgent
from planpilot.domain.models import (
    Machine,
    Material,
    MaterialFamily,
    Operation,
    Order,
    Plant,
    Routing,
    WorkCenter,
)
from planpilot.llm.client import LLMClient, LLMResponse, LLMTrace, Provider, ToolCall
from planpilot.rules.dsl import ForbidSequence, RuleType
from planpilot.rules.store import RuleStore


@pytest.fixture
def agent_plant() -> Plant:
    """Fixture with plant containing Machine 4, titanium and aluminium."""
    m4 = Machine(id="M4", name="CNC Mill 4", work_center_id="WC1")
    wc = WorkCenter(id="WC1", machine_ids=["M4"])

    mat_ti = Material(id="MAT_TI", name="Titanium Sheet", family=MaterialFamily.TITANIUM)
    mat_al = Material(id="MAT_AL", name="Aluminium Billet", family=MaterialFamily.ALUMINIUM)

    op1 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M4"], processing_time=15)
    rtg1 = Routing(id="RTG1", product_material_id="MAT_TI", operations=[op1])

    op2 = Operation(id="OP1", sequence=10, eligible_machine_ids=["M4"], processing_time=20)
    rtg2 = Routing(id="RTG2", product_material_id="MAT_AL", operations=[op2])

    ord1 = Order(id="ORD1", routing_id="RTG1", material_id="MAT_TI")
    ord2 = Order(id="ORD2", routing_id="RTG2", material_id="MAT_AL")

    return Plant(
        id="P_AGENT",
        machines=[m4],
        work_centers=[wc],
        materials=[mat_ti, mat_al],
        routings=[rtg1, rtg2],
        orders=[ord1, ord2],
    )


def make_mock_trace() -> LLMTrace:
    return LLMTrace(
        provider=Provider.OPENAI,
        model="gpt-4o",
        prompt_tokens=50,
        completion_tokens=25,
        total_tokens=75,
        latency_seconds=0.1,
        cost_usd=0.001,
        cached=False,
    )


class TestRuleAgent:
    def test_tool_definitions(self, agent_plant: Plant) -> None:
        """RuleAgent defines the expected tools."""
        rule_store = RuleStore(agent_plant)
        mock_llm = MagicMock(spec=LLMClient)
        agent = RuleAgent(plant=agent_plant, rule_store=rule_store, llm_client=mock_llm)

        tool_names = {t.name for t in agent.tools}
        assert "lookup_entities" in tool_names
        assert "list_active_rules" in tool_names
        assert "propose_rule" in tool_names
        assert "ask_clarification" in tool_names

    def test_multi_turn_rule_resolution_and_application(self, agent_plant: Plant) -> None:
        """Agent performs entity lookup, then proposes and validates a valid rule."""
        rule_store = RuleStore(agent_plant)
        mock_llm = MagicMock(spec=LLMClient)
        agent = RuleAgent(plant=agent_plant, rule_store=rule_store, llm_client=mock_llm)

        # Turn 1: LLM calls lookup_entities for 'Machine 4'
        trace = make_mock_trace()
        tc_lookup = ToolCall(
            id="call_1",
            name="lookup_entities",
            arguments={"query": "Machine 4", "entity_type": "machine"},
        )
        resp_turn1 = LLMResponse(content="", tool_calls=[tc_lookup], trace=trace)

        # Turn 2: LLM calls propose_rule with resolved machine_id "M4"
        tc_propose = ToolCall(
            id="call_2",
            name="propose_rule",
            arguments={
                "rule_json": {
                    "type": "forbid_sequence",
                    "machine_id": "M4",
                    "from_material": "titanium",
                    "to_material": "aluminium",
                    "unless_cleanout_min": 30,
                }
            },
        )
        resp_turn2 = LLMResponse(content="", tool_calls=[tc_propose], trace=trace)

        mock_llm.complete.side_effect = [resp_turn1, resp_turn2]

        user_prompt = "Never run aluminium after titanium on Machine 4 without 30 min cleanout"
        res = agent.process_natural_language_rule(user_prompt)

        assert res.status == "applied"
        assert res.rule is not None
        assert res.rule.type == RuleType.FORBID_SEQUENCE
        assert isinstance(res.rule, ForbidSequence)
        assert res.rule.machine_id == "M4"
        assert res.rule.unless_cleanout_min == 30
        assert len(rule_store.rules) == 1

        # Check conversation history was passed to LLM across turns
        assert mock_llm.complete.call_count == 2
        second_call_messages = mock_llm.complete.call_args_list[1][0][0]
        # Messages should include system, user, assistant(tc1), tool(tc1 result)
        assert any(m.get("role") == "tool" for m in second_call_messages)

    def test_clarification_flow(self, agent_plant: Plant) -> None:
        """When rule is ambiguous, agent requests clarification."""
        rule_store = RuleStore(agent_plant)
        mock_llm = MagicMock(spec=LLMClient)
        agent = RuleAgent(plant=agent_plant, rule_store=rule_store, llm_client=mock_llm)

        trace = make_mock_trace()
        tc_clarify = ToolCall(
            id="call_clarify",
            name="ask_clarification",
            arguments={"question": "Which machine should this cleanout rule apply to?"},
        )
        resp = LLMResponse(content="", tool_calls=[tc_clarify], trace=trace)
        mock_llm.complete.return_value = resp

        res = agent.process_natural_language_rule("Don't run titanium after aluminium")
        assert res.status == "clarification_needed"
        assert "Which machine" in (res.clarification_question or "")

    def test_direct_answer_fallback_to_clarification(self, agent_plant: Plant) -> None:
        """If LLM outputs plain text without calling tools, returns clarification needed."""
        rule_store = RuleStore(agent_plant)
        mock_llm = MagicMock(spec=LLMClient)
        agent = RuleAgent(plant=agent_plant, rule_store=rule_store, llm_client=mock_llm)

        trace = make_mock_trace()
        resp = LLMResponse(
            content="Could you specify the cleanout duration?",
            tool_calls=[],
            trace=trace,
        )
        mock_llm.complete.return_value = resp

        res = agent.process_natural_language_rule("Cleanout between titanium and aluminium")
        assert res.status == "clarification_needed"
        assert res.message == "Could you specify the cleanout duration?"
