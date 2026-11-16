"""Unit tests for the Evaluation Harness functions."""

from __future__ import annotations

from pathlib import Path

from evals.harness import evaluate_erp, evaluate_explain


class TestHarness:
    def test_explain_evaluation_metrics(self) -> None:
        explain_file = Path("evals/explain_cases.jsonl")
        if not explain_file.exists():
            return

        res = evaluate_explain(explain_file)
        assert res["total_cases"] >= 25
        assert res["overall_cause_identification_accuracy"] >= 90.0
        assert res["citation_verifier_pass_rate_on_faithful"] == 100.0
        assert res["citation_verifier_rejection_rate_on_hallucinations"] == 100.0

    def test_erp_evaluation_metrics(self) -> None:
        erp_dir = Path("evals/erp_cases")
        if not erp_dir.exists():
            return

        res = evaluate_erp(erp_dir)
        assert res["total_cases"] >= 30
        assert res["precision"] >= 85.0
        assert res["recall"] >= 85.0
        assert res["f1_score"] >= 85.0
        assert res["clean_instances_solved_to_optimal"] >= 2
