"""Tests for the OnboardingAgent."""

from unittest.mock import MagicMock

from planpilot.agents.onboarding_agent import OnboardingAgent
from planpilot.erp.generator import SAPExtractGenerator
from planpilot.erp.models import ERPFormat, GoLiveStatus
from planpilot.llm.client import LLMResponse, LLMTrace, Provider, ToolCall


def test_onboarding_agent_deterministic_flow() -> None:
    """Verify deterministic onboarding methods without an LLM client."""
    gen = SAPExtractGenerator(seed=42)
    clean_data = gen.generate_clean_dataset()

    agent = OnboardingAgent(clean_data, format_type=ERPFormat.SAP)

    val_report = agent.validate_dataset()
    assert val_report.is_valid is True

    readiness = agent.build_readiness_report()
    assert readiness.status == GoLiveStatus.READY
    assert readiness.readiness_score == 1.0
    assert "READY" in readiness.markdown_summary

    plant = agent.to_plant()
    plant.validate_referential_integrity()


def test_onboarding_agent_tool_calling() -> None:
    """Verify LLM tool-calling loop executes inspect, validate, and report tools."""
    gen = SAPExtractGenerator(seed=42)
    clean_data = gen.generate_clean_dataset()

    mock_llm = MagicMock()
    dummy_trace = LLMTrace(provider=Provider.OPENAI, model="gpt-4o")

    # Turn 1: LLM inspects tables
    turn1_response = LLMResponse(
        content="I will inspect tables first.",
        tool_calls=[
            ToolCall(
                id="call_1",
                name="inspect_tables",
                arguments={},
            )
        ],
        trace=dummy_trace,
    )

    # Turn 2: LLM runs validation
    turn2_response = LLMResponse(
        content="Now I will run validation.",
        tool_calls=[
            ToolCall(
                id="call_2",
                name="run_validation",
                arguments={},
            )
        ],
        trace=dummy_trace,
    )

    # Turn 3: LLM generates readiness report
    turn3_response = LLMResponse(
        content="All clean, generating readiness report.",
        tool_calls=[
            ToolCall(
                id="call_3",
                name="generate_readiness_report",
                arguments={
                    "executive_summary": "Data is clean and certified for go-live.",
                    "recommendations": ["Deploy to production scheduler."],
                },
            )
        ],
        trace=dummy_trace,
    )

    # Turn 4: Final text
    turn4_response = LLMResponse(
        content="Onboarding complete.",
        tool_calls=[],
        trace=dummy_trace,
    )

    mock_llm.complete.side_effect = [
        turn1_response,
        turn2_response,
        turn3_response,
        turn4_response,
    ]

    agent = OnboardingAgent(clean_data, format_type=ERPFormat.SAP, llm_client=mock_llm)
    report = agent.run_agentic_onboarding("Please check these SAP tables.")

    assert report.status == GoLiveStatus.READY
    assert "Data is clean and certified" in report.markdown_summary
    assert mock_llm.complete.call_count == 4
