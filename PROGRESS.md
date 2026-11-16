# PlanPilot Progress

## Current Status: Phase 2 — CP-SAT Scheduler Core ✅

### Completed

#### Phase 1 — Domain + Data
- [x] Project scaffolding: `pyproject.toml`, `Makefile`, `.env.example`, `.gitignore`
- [x] Full directory structure per spec
- [x] **Pydantic v2 domain models** with strict validation:
  - `Machine`, `WorkCenter`, `DowntimeWindow`, `Material`, `MaterialFamily`
  - `BOM`, `BOMComponent`, `Operation`, `Routing`
  - `Order`, `OrderPriority`, `OrderStatus`
  - `ChangeoverEntry`, `ChangeoverMatrix`
  - `Schedule`, `Assignment`, `KPIs`, `SolverStats`, `TightConstraint`
  - `Plant` with referential integrity validator and lookup helpers
- [x] **Plant instance generator**: seeded, deterministic, configurable (N orders, M machines, alt machines, material families, changeover matrices, due dates, priorities, downtimes)
- [x] **OR-Library benchmark loaders**: parser + domain converter for ft06, ft10, la01–la05
- [x] Benchmark data files committed

#### Phase 2 — CP-SAT Scheduler Core
- [x] **Flexible Job-Shop CP-SAT Formulation** (`src/planpilot/solver/scheduler.py`):
  - Optional interval variables per operation × eligible machine with `add_exactly_one` presence constraints.
  - Routing precedence constraints respecting alternative machine presence literals.
  - Native `AddNoOverlap` per machine combining operation intervals and fixed planned downtime windows.
  - Sequence-dependent changeover matrix using pairwise ordering literals (`a_before_b`) with disjunctive setup constraints.
  - Precise calculation of `setup_time` and `setup_start` on every `Assignment`.
  - Configurable optimization objectives: `MAKESPAN`, `TOTAL_WEIGHTED_TARDINESS`, `LEXICOGRAPHIC`.
- [x] **Decision Trace**:
  - Automatically identifies tight (binding) constraints on each scheduled assignment: `PREDECESSOR_END`, `MACHINE_PREDECESSOR_SETUP`, `DOWNTIME_BOUNDARY`, `RELEASE_TIME`, `NONE`.
- [x] **Infeasibility Analysis via Assumption Literals** (`src/planpilot/solver/infeasibility.py`):
  - Builds relaxation model with indicator assumption literals for due dates, release times, downtimes, and horizon.
  - Extracts minimal unsatisfiable cores via `solver.sufficient_assumptions_for_infeasibility()`.
  - Maps literal indices to human-readable explanations in `InfeasibilityReport` and `SolverStats.infeasibility_core`.
- [x] **Comprehensive Test Suite & Property Verification**:
  - 83 unit and property tests passing in 20s.
  - `ft06` benchmark solves to optimal makespan exactly **55** (`OPTIMAL`).
  - `ft10` benchmark solves to optimal makespan exactly **930** (`OPTIMAL`).
  - Hypothesis property tests prove: no machine overlap, routing precedence respected, sequence-dependent setups respected, downtime windows respected, every operation executed exactly once on an eligible machine.
  - Infeasibility core tests correctly isolate impossible due dates and downtime conflicts.

### Phase 2 Hard Verification Gate Output
```
tests/unit/test_benchmarks.py (23 tests) PASSED
tests/unit/test_domain.py (37 tests) PASSED
tests/unit/test_generator.py (11 tests) PASSED
tests/unit/test_properties.py::TestSchedulerProperties::test_schedule_invariants PASSED [86%]
tests/unit/test_scheduler.py::TestOrLibraryBenchmarks::test_ft06_optimal_makespan PASSED [87%] (makespan=55, OPTIMAL)
tests/unit/test_scheduler.py::TestOrLibraryBenchmarks::test_ft10_optimal_or_bounded PASSED [89%] (makespan=930, OPTIMAL)
tests/unit/test_scheduler.py::TestSchedulerFeatures::test_multi_machine_alternatives PASSED [90%]
tests/unit/test_scheduler.py::TestSchedulerFeatures::test_release_time_respected PASSED [91%]
tests/unit/test_scheduler.py::TestSchedulerFeatures::test_downtime_window_respected PASSED [92%]
tests/unit/test_scheduler.py::TestSchedulerFeatures::test_sequence_dependent_setup_times PASSED [93%]
tests/unit/test_scheduler.py::TestSchedulerFeatures::test_weighted_tardiness_objective PASSED [95%]
tests/unit/test_scheduler.py::TestSchedulerFeatures::test_lexicographic_objective PASSED [96%]
tests/unit/test_scheduler.py::TestInfeasibilityDiagnosis::test_infeasible_tight_due_date_diagnosed PASSED [97%]
tests/unit/test_scheduler.py::TestInfeasibilityDiagnosis::test_infeasible_downtime_conflict_diagnosed PASSED [98%]
tests/unit/test_scheduler.py::TestInfeasibilityDiagnosis::test_feasible_instance_returns_not_infeasible PASSED [100%]

============================= 83 passed in 20.28s =============================
```

### What's Next: Phase 3 — Rule DSL + Natural-Language Rule Agent
- Rule DSL as a Pydantic discriminated union:
  - `ForbidSequence(machine, from_material, to_material, unless_cleanout_min)`
  - `MinChangeover(machine, min_setup_min)`
  - `MachineUnavailable(machine, window)`
  - `PinToMachine(order, operation, machine)`
  - `ForbidMachine(order, operation, machine)`
  - `OrderPrecedence(before_order, after_order)`
  - `MaxConsecutiveRuns(material, machine, n)`
  - `HardDueDate(order, deadline)`
  - `BatchTogether(orders, machine)`
- CP-SAT constraint compiler for every rule type with unit test proving schedule obeys it
- Deterministic multi-stage validation pipeline:
  1. Schema validation (Pydantic)
  2. Entity resolution (fuzzy matching against plant machines/materials/orders)
  3. Semantic checks (valid bounds, non-trivial windows)
  4. Conflict detection against active rules
  5. Feasibility check (re-solving with short time limit + assumption core extraction)
- Versioned rule store with before/after KPI diffs
- LLM provider abstraction (OpenAI, Anthropic, OpenAI-compatible OSS) with tenacity retries, call tracing, disk caching
- Unit tests with mocked LLM clients + live smoke tests (skipped when no API key)

### Conventions
- **src layout**: all code under `src/planpilot/`
- **Pydantic v2**: frozen models, field validators, model validators
- **Type hints**: all functions fully typed
- **Testing**: pytest, tests in `tests/unit/` and `tests/integration/`
- **Commits**: conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`)

### Known Issues
- None

### How to Run
```bash
uv venv .venv && .venv\Scripts\activate  # Windows
uv pip install -e ".[dev]"
python -m pytest tests/ -v --tb=short
```
