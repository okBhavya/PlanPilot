"""Tests for deterministic ERP validators and precision/recall evaluation."""

import pandas as pd

from planpilot.erp.generator import LegacyExtractGenerator, SAPExtractGenerator
from planpilot.erp.injector import ErrorInjector
from planpilot.erp.models import ErrorSeverity, InjectedErrorType
from planpilot.erp.validators import DeterministicValidator


def test_validator_clean_dataset() -> None:
    """Verify clean SAP dataset passes all validations with 100% score."""
    gen = SAPExtractGenerator(seed=42)
    clean_data = gen.generate_clean_dataset()

    validator = DeterministicValidator()
    report = validator.validate(clean_data, is_sap=True)

    assert report.is_valid is True
    assert report.critical_count == 0
    assert report.readiness_score == 1.0


def test_validator_legacy_clean_dataset() -> None:
    """Verify clean legacy dataset passes validation."""
    gen = LegacyExtractGenerator(seed=42)
    clean_data = gen.generate_clean_dataset()

    validator = DeterministicValidator()
    report = validator.validate(clean_data, is_sap=False)

    assert report.is_valid is True
    assert report.critical_count == 0


def test_precision_recall_vs_injected_ground_truth() -> None:
    """Compute precision, recall, and F1 against injected ground truth errors."""
    gen = SAPExtractGenerator(seed=101)
    clean_data = gen.generate_clean_dataset()

    injector = ErrorInjector(seed=101)
    corrupted, ground_truth = injector.inject_all_errors(clean_data)

    validator = DeterministicValidator()
    report = validator.validate(corrupted, is_sap=True)

    assert report.is_valid is False
    assert report.critical_count > 0

    metrics = report.compute_metrics(ground_truth)

    # Acceptance criteria: precision, recall, F1 measured and verified high
    assert metrics.precision >= 0.85
    assert metrics.recall >= 0.85
    assert metrics.f1 >= 0.85


def test_circular_bom_detection() -> None:
    """Verify cycle detection catches multi-hop circular dependencies."""
    # Build 3-hop cycle: A -> B -> C -> A
    df_stpo = pd.DataFrame(
        [
            {"STLNR": "B1", "MATNR": "MAT-A", "IDNRK": "MAT-B", "MENGE": 1.0},
            {"STLNR": "B2", "MATNR": "MAT-B", "IDNRK": "MAT-C", "MENGE": 1.0},
            {"STLNR": "B3", "MATNR": "MAT-C", "IDNRK": "MAT-A", "MENGE": 1.0},
        ]
    )
    tables = {
        "CRHD": pd.DataFrame([{"ARBPL": "M1"}]),
        "MARA": pd.DataFrame([{"MATNR": "MAT-A"}, {"MATNR": "MAT-B"}, {"MATNR": "MAT-C"}]),
        "PLPO": pd.DataFrame([{"PLNNR": "R1", "VORNR": "0010", "ARBPL": "M1", "VGW02": 10.0}]),
        "STPO": df_stpo,
    }

    validator = DeterministicValidator()
    report = validator.validate(tables, is_sap=True)

    circular_errs = [e for e in report.errors if e.error_type == InjectedErrorType.CIRCULAR_BOM]
    assert len(circular_errs) == 1
    assert circular_errs[0].severity == ErrorSeverity.CRITICAL
    assert "MAT-A -> MAT-B -> MAT-C -> MAT-A" in circular_errs[0].message


def test_dangling_work_center_detection() -> None:
    """Verify missing work center reference is caught as critical."""
    tables = {
        "CRHD": pd.DataFrame([{"ARBPL": "WC-01"}]),
        "MARA": pd.DataFrame([{"MATNR": "MAT-A"}]),
        "PLPO": pd.DataFrame(
            [{"PLNNR": "R1", "VORNR": "0010", "ARBPL": "WC-PHANTOM", "VGW02": 10.0}]
        ),
        "STPO": pd.DataFrame(columns=["STLNR", "MATNR", "IDNRK", "MENGE"]),
    }

    validator = DeterministicValidator()
    report = validator.validate(tables, is_sap=True)

    dangling_errs = [
        e for e in report.errors if e.error_type == InjectedErrorType.DANGLING_WORK_CENTER
    ]
    assert len(dangling_errs) == 1
    assert dangling_errs[0].column == "ARBPL"
    assert dangling_errs[0].value == "WC-PHANTOM"
