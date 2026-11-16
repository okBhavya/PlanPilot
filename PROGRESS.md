# PlanPilot Progress

## Current Status: Phase 4 — Grounded Explainability ✅ (Moving to Phase 5)

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
  - 83 unit and property tests passing.
  - `ft06` benchmark solves to optimal makespan exactly **55** (`OPTIMAL`).
  - `ft10` benchmark solves to optimal makespan exactly **930** (`OPTIMAL`).

#### Phase 3 — Rule DSL + Natural-Language Rule Agent
- [x] **Rule DSL** (`src/planpilot/rules/dsl.py`):
  - Pydantic v2 discriminated union `Rule` with discriminator `type`.
  - All 9 rule types implemented: `ForbidSequence`, `MinChangeover`, `MachineUnavailable`, `PinToMachine`, `ForbidMachine`, `OrderPrecedence`, `MaxConsecutiveRuns`, `HardDueDate`, `BatchTogether`.
- [x] **Rule Constraint Compilers** (`src/planpilot/rules/compilers.py`):
  - Exact CP-SAT mathematical constraint compilers for all 9 rule types without Big-M.
  - Adjacency and cleanout gap logic for `ForbidSequence`.
  - Subsegment span bounding for `BatchTogether`.
  - Combinatorial non-consecutive window bounding for `MaxConsecutiveRuns`.
  - Fully integrated into `solve(plant, rules=[...])`.
- [x] **Five-Stage Deterministic Validation Pipeline** (`src/planpilot/rules/validator.py`):
  - Stage 1 (Schema): Pydantic structural validation.
  - Stage 2 (Entity): Fuzzy matching and plant entity resolution (`resolve_entity`, `fuzzy_match_entity`).
  - Stage 3 (Semantic): Domain invariant bounds (release times, horizon, valid durations).
  - Stage 4 (Conflict): Direct contradiction and DFS cycle detection on precedence graphs.
  - Stage 5 (Feasibility): Re-solve with CP-SAT and assumption-based conflict core extraction.
- [x] **Versioned Rule Store & Diff Engine** (`src/planpilot/rules/store.py`):
  - Versioned storage, enable/disable toggle, before/after `ScheduleDiff` and `KPIDiff` impact analysis.
- [x] **LLM Provider Abstraction** (`src/planpilot/llm/`):
  - Unified client supporting OpenAI, Anthropic, and OpenAI-compatible endpoints (`client.py`).
  - Per-model pricing registry and cost estimation (`cost.py`).
  - SHA256 prompt hashing on-disk response cache (`cache.py`) for deterministic reproducibility.
  - Tenacity exponential backoff retries on transient errors.
- [x] **Natural-Language Rule Agent** (`src/planpilot/agents/rule_agent.py`):
  - Multi-turn tool-calling loop with `lookup_entities`, `propose_rule`, `ask_clarification`, `list_active_rules`.
  - Prompt injection protection (user prompt encapsulated as data inside XML tags).

#### Phase 4 — Grounded Explainability & Schedule Diff
- [x] **Handcrafted Lateness Benchmark Instances** (`src/planpilot/explain/benchmarks.py`):
  - 5 canonical cases with known mathematical root causes:
    1. `competing_priority`: High-priority order takes machine -> root cause: `competing_order`.
    2. `supply_delay`: Late raw material release -> root cause: `release_delay`.
    3. `cleanout_gap`: Material changeover setup gap -> root cause: `changeover_setup`.
    4. `downtime_overlap`: Maintenance blackout window -> root cause: `downtime_window`.
    5. `routing_cascade`: Multi-operation sequential duration -> root cause: `routing_cascade`.
- [x] **Fact Extraction & Critical-Chain Backward Tracer** (`src/planpilot/explain/facts.py`):
  - Extracts typed, citation-addressable facts (`[F1]`, `[F2]`, ...) from solved schedules.
  - Backtracks tight constraints backwards along the schedule to isolate the true root cause.
  - Computes exact critical chain steps and `LatenessDiagnosis`.
- [x] **Schedule Diff Engine** (`src/planpilot/explain/diff.py`):
  - Compares baseline Schedule A vs updated Schedule B.
  - Detects assignment deltas ($\Delta start$, $\Delta end$, $\Delta machine$, $\Delta setup$).
  - Classifies movement (`EARLIER`, `LATER`, `REASSIGNED`, `UNCHANGED`).
  - Attributes root causes (`RULE_IMPACT`, `RUSH_ORDER_INSERTION`, `DOWNTIME_INSERTION`, `CASCADE`).
  - Computes `KPIDiff` and emits structured comparison facts.
- [x] **Deterministic Citation Verifier** (`src/planpilot/explain/verifier.py`):
  - Strict zero-hallucination verification engine.
  - Requires every sentence to cite at least one `[Fn]`.
  - Rejects unknown fact IDs.
  - Enforces numeric reconciliation: any number mentioned in prose must exist in cited facts.
- [x] **Grounded Explain Agent** (`src/planpilot/agents/explain_agent.py`):
  - Answers "Why is order X late?" and "Why did job Y move?".
  - Prompts LLM with structured fact table only.
  - Automated self-correction retry loop when citation verification fails.
  - Deterministic fallback summary if retries are exhausted.
- [x] **Comprehensive Test Verification**:
  - Handcrafted lateness benchmark tests proving root cause isolation across all 5 instances (`tests/unit/test_explain_facts.py`).
  - Citation verifier tests proving rejection of uncited sentences, unknown IDs, and hallucinated numbers (`tests/unit/test_citation_verifier.py`).
  - Schedule diff tests verifying delta detection and movement attribution (`tests/unit/test_schedule_diff.py`).
  - Explain agent tests verifying grounded answers and self-correction loops (`tests/unit/test_explain_agent.py`).
  - Total 139 unit and integration tests passing in 31s. Ruff clean, mypy strict clean (30 source files checked with 0 errors).

### Phase 4 Hard Verification Gate Output
```
tests/unit/test_benchmarks.py (23 tests) PASSED
tests/unit/test_citation_verifier.py (6 tests) PASSED
tests/unit/test_domain.py (37 tests) PASSED
tests/unit/test_explain_agent.py (3 tests) PASSED
tests/unit/test_explain_facts.py (6 tests) PASSED
tests/unit/test_generator.py (11 tests) PASSED
tests/unit/test_llm.py (8 tests) PASSED (1 live smoke deselected)
tests/unit/test_properties.py (1 test) PASSED
tests/unit/test_rule_agent.py (4 tests) PASSED
tests/unit/test_rule_compilers.py (10 tests) PASSED
tests/unit/test_rule_store.py (4 tests) PASSED
tests/unit/test_schedule_diff.py (2 tests) PASSED
tests/unit/test_scheduler.py (11 tests) PASSED
tests/unit/test_validator.py (13 tests) PASSED

====================== 139 passed, 1 deselected in 31.01s ======================
Ruff: All checks passed!
Mypy: Success: no issues found in 30 source files
```

### Phase 5 Plan (Agentic ERP Data Onboarding)
- [ ] SAP-style Messy Extract Generator (`src/planpilot/erp/generator.py`):
  - Generate CSV/Parquet extracts for SAP tables: `CRHD` (work centers), `PLPO` / `PLKO` (routings/operations), `MAST` / `STPO` (BOMs), `AFKO` / `AFPO` (production orders), `MARA` (materials).
  - Seeded error injection harness: circular BOMs, missing work centers, unit mismatches (min vs hr), orphaned operations, invalid material numbers.
- [ ] DuckDB In-Memory Profiling Engine (`src/planpilot/erp/profiler.py`):
  - DuckDB queries over messy extracts to compute summary statistics, cardinality, null fractions, and orphan checks without external database setup.
- [ ] Deterministic Data Model Mapping & Validation (`src/planpilot/erp/mapper.py`, `src/planpilot/erp/validators.py`):
  - Deterministic transformations mapping SAP fields to PlanPilot domain models.
  - Automated detection and diagnostic reporting of data errors (cycles, broken references).
- [ ] Agentic Onboarding Agent (`src/planpilot/agents/onboarding_agent.py`):
  - Interactive agent guiding data onboarding, explaining anomalies, proposing column mappings, and certifying readiness before solver ingestion.
  - Prompt injection protection against malicious strings inside ERP data extracts.
- [ ] Unit & Integration Tests:
  - Error injector tests verifying all injected defects are caught.
  - DuckDB profiler tests.
  - Onboarding agent tool calling and mapping resolution tests.

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
