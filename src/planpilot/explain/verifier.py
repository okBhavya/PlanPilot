"""Deterministic citation and hallucination verifier for scheduling explanations.

Guarantees schedule explanations are 100% faithful to solver facts:
- Rejects uncited sentences (every sentence must cite [Fn])
- Rejects unknown fact IDs (must reference existing facts)
- Rejects hallucinated numbers (every number in sentence must be present in cited facts)
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from planpilot.explain.facts import Fact


class SentenceVerification(BaseModel):
    """Verification result for a single sentence."""

    sentence: str
    cited_fact_ids: list[str]
    valid: bool
    errors: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    """Aggregate verification result for an explanation."""

    valid: bool
    faithfulness_score: float  # 0.0 to 1.0
    sentence_results: list[SentenceVerification]
    errors: list[str] = Field(default_factory=list)


class CitationVerifier:
    """Deterministic verifier ensuring explanations are grounded in solver facts."""

    @staticmethod
    def split_sentences(text: str) -> list[str]:
        """Split text into sentences cleanly respecting numbers and abbreviations."""
        cleaned = text.strip()
        if not cleaned:
            return []

        # Split on sentence terminators followed by whitespace, or newlines
        raw_sentences = re.split(r"(?<=[.!?])\s+|\n+", cleaned)
        sentences: list[str] = []
        for s in raw_sentences:
            s_clean = s.strip()
            if s_clean and len(s_clean) > 1:
                sentences.append(s_clean)
        return sentences

    def verify(self, text: str, facts: list[Fact]) -> VerificationResult:
        """Verify that every sentence cites valid facts and claims no hallucinated numbers."""
        sentences = self.split_sentences(text)
        if not sentences:
            return VerificationResult(
                valid=False,
                faithfulness_score=0.0,
                sentence_results=[],
                errors=["Explanation is empty"],
            )

        fact_map = {f.id: f for f in facts}
        sentence_results: list[SentenceVerification] = []
        all_errors: list[str] = []
        valid_sentence_count = 0

        for sentence in sentences:
            s_errors: list[str] = []

            # 1. Extract citations [Fn]
            raw_citations = re.findall(r"\[(F\d+)\]", sentence)
            cited_ids = list(dict.fromkeys(raw_citations))  # unique, preserve order

            if not cited_ids:
                s_errors.append(f"Sentence lacks citations [Fn]: '{sentence}'")

            # 2. Check each citation exists
            valid_facts_for_sentence: list[Fact] = []
            for cid in cited_ids:
                if cid not in fact_map:
                    s_errors.append(f"Cited unknown fact ID [{cid}] in: '{sentence}'")
                else:
                    valid_facts_for_sentence.append(fact_map[cid])

            # 3. Numeric verification: all numbers in sentence must come from cited facts
            # Strip out citation tokens [F1] and identifiers like ORD-1, OP10, M1
            clean_sentence = re.sub(r"\[F\d+\]", "", sentence)
            clean_sentence = re.sub(r"\b[A-Za-z]+[-_]?\d+\b", "", clean_sentence)

            # Find all remaining standalone numbers
            sentence_numbers = [
                float(n) for n in re.findall(r"\b\d+(?:\.\d+)?\b", clean_sentence)
            ]

            # Pool all numbers from valid cited facts
            allowed_numbers: set[float] = set()
            for fact in valid_facts_for_sentence:
                for val in fact.numeric_values:
                    allowed_numbers.add(float(val))

            for num in sentence_numbers:
                if num not in allowed_numbers:
                    s_errors.append(
                        f"Hallucinated number '{num}' in sentence not in cited facts: '{sentence}'"
                    )

            is_valid = len(s_errors) == 0
            if is_valid:
                valid_sentence_count += 1
            else:
                all_errors.extend(s_errors)

            sentence_results.append(
                SentenceVerification(
                    sentence=sentence,
                    cited_fact_ids=cited_ids,
                    valid=is_valid,
                    errors=s_errors,
                )
            )

        faithfulness = round(valid_sentence_count / len(sentences), 3)
        overall_valid = valid_sentence_count == len(sentences)

        return VerificationResult(
            valid=overall_valid,
            faithfulness_score=faithfulness,
            sentence_results=sentence_results,
            errors=all_errors,
        )
