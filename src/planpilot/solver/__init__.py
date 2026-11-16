"""Solver package for PlanPilot CP-SAT scheduling."""

from planpilot.solver.benchmarks import (
    KNOWN_OPTIMA,
    list_benchmarks,
    load_benchmark,
    orlib_to_plant,
    parse_orlib_instance,
)
from planpilot.solver.infeasibility import InfeasibilityReport, diagnose_infeasibility
from planpilot.solver.scheduler import solve

__all__ = [
    "KNOWN_OPTIMA",
    "InfeasibilityReport",
    "diagnose_infeasibility",
    "list_benchmarks",
    "load_benchmark",
    "orlib_to_plant",
    "parse_orlib_instance",
    "solve",
]
