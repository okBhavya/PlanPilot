# PlanPilot Progress

## Current Status: Phase 3 — Rule DSL + NL Rule Agent ✅ (Moving to Phase 4)

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
- [x] **Comprehensive Test Verification**:
  - Unit tests for all 9 rule compilers showing schedules obey each constraint (`tests/unit/test_rule_compilers.py`).
  - Unit tests for each of the 5 validator stages with passing and failing tests (`tests/unit/test_validator.py`).
  - Unit tests for versioned RuleStore (`tests/unit/test_rule_store.py`).
  - Unit tests for LLM provider abstraction, cost estimation, DiskCache, and retries (`tests/unit/test_llm.py`).
  - Unit tests for RuleAgent tool calling and clarification flows (`tests/unit/test_rule_agent.py`).
  - Total 122 tests passing in 24s. Ruff clean, mypy strict clean (25 source files checked with 0 errors).

### Phase 3 Hard Verification Gate Output
```
tests/unit/test_benchmarks.py (23 tests) PASSED
tests/unit/test_domain.py (37 tests) PASSED
tests/unit/test_generator.py (11 tests) PASSED
tests/unit/test_llm.py (8 tests) PASSED (1 live smoke deselected)
tests/unit/test_properties.py (1 test) PASSED
tests/unit/test_rule_agent.py (4 tests) PASSED
tests/unit/test_rule_compilers.py (10 tests) PASSED
tests/unit/test_rule_store.py (4 tests) PASSED
tests/unit/test_scheduler.py (11 tests) PASSED
tests/unit/test_validator.py (13 tests) PASSED

====================== 122 passed, 1 deselected in 23.95s ======================
Ruff: All checks passed!
Mypy: Success: no issues found in 25 source files
```

### Phase 4 Plan (Grounded Explainability)
- [ ] Handcrafted Lateness Benchmark Instances (>= 5 cases with known true root causes):
  1. `competing_priority`: High-priority order takes the only eligible machine.
  2. `supply_delay`: Late release time (upstream supply delay).
  3. `cleanout_gap`: Changeover cleanout gap pushes order past due date.
  4. `downtime_overlap`: Planned downtime window overlaps operation execution window.
  5. `routing_cascade`: Cascading precedence delays along the multi-operation routing.
- [ ] Fact Extraction Engine (`src/planpilot/explain/facts.py`):
  - Extracts typed, citation-addressable facts from solved schedules: `[F1]`, `[F2]`, etc.
  - Backtracks tight constraints from late order's final operation along the critical chain to isolate root cause.
- [ ] Schedule Diff Engine (`src/planpilot/explain/diff.py`):
  - Compares Schedule A vs Schedule B: classifies movements (earlier/later, machine change, setup delta).
  - Attributes each movement to root causes (rule added, rush order inserted, downtime inserted).
- [ ] Citation Verifier (`src/planpilot/explain/verifier.py`):
  - Deterministic validator checking every sentence cites `[Fn]`, every cited `[Fn]` exists, and numeric claims match fact table.
- [ ] Explain Agent (`src/planpilot/agents/explain_agent.py`):
  - Answers "Why is order X late?" and "Why did job Y move?" strictly grounded in extracted facts.
- [ ] Unit & Integration Tests:
  - Verify fact extractor correctly traces the root cause on all 5 handcrafted instances.
  - Verify citation verifier accepts properly cited explanations and rejects uncited, unknown fact IDs, or hallucinated numbers.

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
