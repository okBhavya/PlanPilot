"""Unit tests for deterministic CitationVerifier and hallucination rejection."""

from __future__ import annotations

import pytest

from planpilot.explain.facts import Fact, FactType
from planpilot.explain.verifier import CitationVerifier


@pytest.fixture
def sample_facts() -> list[Fact]:
    """Sample fact table for verification testing."""
    return [
        Fact.create(
            fact_id="F1",
            fact_type=FactType.ORDER_COMPLETION,
            description="Order ORD1 completed at 65",
            order_id="ORD1",
        ),
        Fact.create(
            fact_id="F2",
            fact_type=FactType.ORDER_DUE_DATE,
            description="Order ORD1 due date was 50 (tardiness = 15)",
            order_id="ORD1",
        ),
        Fact.create(
            fact_id="F3",
            fact_type=FactType.SETUP_GAP,
            description="Operation OP10 on machine M1 incurred cleanout setup of 30",
            order_id="ORD1",
        ),
        Fact.create(
            fact_id="F4",
            fact_type=FactType.DOWNTIME_WINDOW,
            description="Machine M1 was down for maintenance from 10 to 30",
            machine_id="M1",
        ),
    ]


class TestCitationVerifier:
    def test_valid_grounded_explanation_passes(self, sample_facts: list[Fact]) -> None:
        """Fully cited explanation with verified numbers passes with 100% faithfulness."""
        verifier = CitationVerifier()
        text = (
            "Order ORD1 finished at 65, missing its due date of 50 by 15 minutes [F1] [F2]. "
            "This lateness occurred because a 30m cleanout setup was required on machine M1 [F3]."
        )

        result = verifier.verify(text, sample_facts)
        assert result.valid is True
        assert result.faithfulness_score == 1.0
        assert len(result.errors) == 0
        assert len(result.sentence_results) == 2
        assert result.sentence_results[0].valid is True
        assert result.sentence_results[1].valid is True

    def test_rejects_uncited_sentence(self, sample_facts: list[Fact]) -> None:
        """Verifier rejects any explanation containing an uncited sentence."""
        verifier = CitationVerifier()
        text = (
            "Order ORD1 was delayed [F1]. "
            "The production planner should review this immediately."  # Lacks citation
        )

        result = verifier.verify(text, sample_facts)
        assert result.valid is False
        assert result.faithfulness_score == 0.5
        assert any("lacks citations" in err for err in result.errors)
        assert result.sentence_results[0].valid is True
        assert result.sentence_results[1].valid is False

    def test_rejects_unknown_fact_id(self, sample_facts: list[Fact]) -> None:
        """Verifier rejects sentences referencing non-existent fact IDs."""
        verifier = CitationVerifier()
        text = "Order ORD1 missed its delivery window [F99]."

        result = verifier.verify(text, sample_facts)
        assert result.valid is False
        assert any("unknown fact ID [F99]" in err for err in result.errors)

    def test_rejects_hallucinated_number(self, sample_facts: list[Fact]) -> None:
        """Verifier rejects numbers not grounded in the cited facts."""
        verifier = CitationVerifier()
        # Sentence claims delay was 45 minutes, but cited facts F1 and F2 only contain 65, 50, 15
        text = "Order ORD1 was delayed by 45 minutes past the deadline [F1] [F2]."

        result = verifier.verify(text, sample_facts)
        assert result.valid is False
        assert any("Hallucinated number '45" in err for err in result.errors)

    def test_empty_explanation_rejected(self, sample_facts: list[Fact]) -> None:
        """Empty explanation text is rejected."""
        verifier = CitationVerifier()
        result = verifier.verify("", sample_facts)
        assert result.valid is False
        assert result.faithfulness_score == 0.0
        assert "empty" in result.errors[0].lower()

    def test_sentence_splitting_with_abbreviations_and_numbers(self) -> None:
        """Sentence splitter correctly separates sentences without breaking on decimals."""
        text = (
            "Fact 1 says completion was at 2.5 hours [F1]. Fact 2 notes order ORD1 was late [F2]."
        )
        sentences = CitationVerifier.split_sentences(text)
        assert len(sentences) == 2
        assert "2.5" in sentences[0]
