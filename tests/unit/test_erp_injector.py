"""Tests for the ERP error injector."""

from planpilot.erp.generator import SAPExtractGenerator
from planpilot.erp.injector import ErrorInjector
from planpilot.erp.models import InjectedErrorType


def test_error_injector_all_errors() -> None:
    """Verify injector creates all 9 specified defect types with accurate ground truth."""
    gen = SAPExtractGenerator(seed=42)
    clean_data = gen.generate_clean_dataset()

    injector = ErrorInjector(seed=42)
    corrupted, ground_truth = injector.inject_all_errors(clean_data)

    assert len(ground_truth) == 9

    injected_types = {gt.error_type for gt in ground_truth}
    expected_types = {
        InjectedErrorType.DANGLING_WORK_CENTER,
        InjectedErrorType.CIRCULAR_BOM,
        InjectedErrorType.ZERO_NEGATIVE_DURATION,
        InjectedErrorType.UNIT_MISMATCH,
        InjectedErrorType.DUPLICATE_KEY,
        InjectedErrorType.ORPHAN_COMPONENT,
        InjectedErrorType.MIXED_DATE_FORMAT,
        InjectedErrorType.WHITESPACE_CASE_INCONSISTENT,
        InjectedErrorType.MISSING_MANDATORY_FIELD,
    }
    assert injected_types == expected_types

    # Verify ground truth fields
    for gt in ground_truth:
        assert gt.table in corrupted
        assert gt.column
        assert gt.details
