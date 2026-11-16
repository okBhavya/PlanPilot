"""Explainability package for PlanPilot production scheduling."""

from planpilot.explain.benchmarks import (
    create_cleanout_gap_instance,
    create_competing_priority_instance,
    create_downtime_overlap_instance,
    create_routing_cascade_instance,
    create_supply_delay_instance,
)
from planpilot.explain.diff import (
    AssignmentDelta,
    MovementType,
    ScheduleComparison,
    ScheduleDiffEngine,
)
from planpilot.explain.facts import (
    CriticalChainStep,
    Fact,
    FactExtractor,
    FactType,
    LatenessDiagnosis,
)
from planpilot.explain.verifier import (
    CitationVerifier,
    SentenceVerification,
    VerificationResult,
)

__all__ = [
    "AssignmentDelta",
    "CitationVerifier",
    "CriticalChainStep",
    "Fact",
    "FactExtractor",
    "FactType",
    "LatenessDiagnosis",
    "MovementType",
    "ScheduleComparison",
    "ScheduleDiffEngine",
    "SentenceVerification",
    "VerificationResult",
    "create_cleanout_gap_instance",
    "create_competing_priority_instance",
    "create_downtime_overlap_instance",
    "create_routing_cascade_instance",
    "create_supply_delay_instance",
]
