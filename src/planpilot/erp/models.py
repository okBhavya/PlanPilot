"""Data models for ERP data onboarding, errors, and validation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ERPFormat(StrEnum):
    """Format of raw ERP data extracts."""

    SAP = "sap"
    LEGACY = "legacy"
    CUSTOM = "custom"


class InjectedErrorType(StrEnum):
    """Categories of injected or detected ERP data defects."""

    DANGLING_WORK_CENTER = "dangling_work_center"
    CIRCULAR_BOM = "circular_bom"
    ZERO_NEGATIVE_DURATION = "zero_negative_duration"
    UNIT_MISMATCH = "unit_mismatch"
    DUPLICATE_KEY = "duplicate_key"
    ORPHAN_COMPONENT = "orphan_component"
    MIXED_DATE_FORMAT = "mixed_date_format"
    WHITESPACE_CASE_INCONSISTENT = "whitespace_case_inconsistent"
    MISSING_MANDATORY_FIELD = "missing_mandatory_field"


class ErrorSeverity(StrEnum):
    """Severity of a detected validation error."""

    CRITICAL = "critical"  # Blocks solver ingestion
    WARNING = "warning"  # Degrades quality, can be coerced/defaulted
    INFO = "info"  # Formatting anomaly


class InjectedError(BaseModel):
    """Ground truth record of an error deliberately injected into raw extracts."""

    model_config = ConfigDict(frozen=True)

    error_type: InjectedErrorType
    table: str
    row_idx: int
    column: str
    details: str
    expected_value: Any = None
    injected_value: Any = None


class ValidationError(BaseModel):
    """A defect detected by the deterministic validation engine."""

    model_config = ConfigDict(frozen=True)

    error_type: InjectedErrorType
    severity: ErrorSeverity
    table: str
    row_idx: int | None = None
    column: str | None = None
    message: str
    value: Any = None


class ValidationMetrics(BaseModel):
    """Precision, recall, and F1 score of validation against ground truth."""

    model_config = ConfigDict(frozen=True)

    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


class ValidationReport(BaseModel):
    """Comprehensive validation findings across all onboarded tables."""

    model_config = ConfigDict(frozen=True)

    is_valid: bool
    errors: list[ValidationError] = Field(default_factory=list)
    critical_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    readiness_score: float = 1.0  # 1.0 = clean, 0.0 = completely broken

    def compute_metrics(self, ground_truth: list[InjectedError]) -> ValidationMetrics:
        """Compute precision, recall, and F1 score against ground truth injected errors.

        Matches are determined by (error_type, table).
        """
        if not ground_truth and not self.errors:
            return ValidationMetrics(
                true_positives=0,
                false_positives=0,
                false_negatives=0,
                precision=1.0,
                recall=1.0,
                f1=1.0,
            )

        gt_signatures = {(gt.error_type, gt.table) for gt in ground_truth}
        val_signatures = {(ve.error_type, ve.table) for ve in self.errors}

        tp = len(gt_signatures.intersection(val_signatures))
        fp = len(val_signatures - gt_signatures)
        fn = len(gt_signatures - val_signatures)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * (precision * recall) / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        return ValidationMetrics(
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1=round(f1, 4),
        )


class ColumnMapping(BaseModel):
    """Maps raw source table column names to canonical PlanPilot domain fields."""

    model_config = ConfigDict(frozen=True)

    table_name: str
    mapping: dict[str, str]  # {raw_column_name: canonical_domain_field}


class GoLiveStatus(StrEnum):
    """Overall go-live readiness status of ERP data."""

    READY = "ready"  # 100% clean or warnings only
    ACTION_REQUIRED = "action_required"  # Minor issues that require user attention
    BLOCKED = "blocked"  # Critical errors that prevent solver execution


class ReadinessReport(BaseModel):
    """Executive readiness report for ERP data onboarding."""

    model_config = ConfigDict(frozen=True)

    status: GoLiveStatus
    readiness_score: float
    total_records: int
    table_counts: dict[str, int]
    critical_errors: list[ValidationError]
    warnings: list[ValidationError]
    recommendations: list[str]
    markdown_summary: str
