# PlanPilot Progress

## Current Status: Phase 6 — Scenario Comparison, Async Worker & REST API ✅ (Moving to Phase 7)

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

#### Phase 5 — Agentic ERP Data Onboarding
- [x] **SAP-style & Legacy Extract Generators** (`src/planpilot/erp/generator.py`):
  - Generates realistic SAP tables: `CRHD` (work centers), `MARA` (materials), `PLPO` (routings/operations), `STPO` (BOMs), `AFKO` (orders).
  - Generates non-SAP legacy variant with alternative headers (`stations`, `items`, `routings`, `boms`, `work_orders`).
  - Fully deterministic with configurable seeds.
- [x] **Seeded Error Injector** (`src/planpilot/erp/injector.py`):
  - Injects all 9 specified defect types with ground truth labels:
    `DANGLING_WORK_CENTER`, `CIRCULAR_BOM`, `ZERO_NEGATIVE_DURATION`, `UNIT_MISMATCH`, `DUPLICATE_KEY`, `ORPHAN_COMPONENT`, `MIXED_DATE_FORMAT`, `WHITESPACE_CASE_INCONSISTENT`, `MISSING_MANDATORY_FIELD`.
- [x] **DuckDB In-Memory Profiling Engine** (`src/planpilot/erp/profiler.py`):
  - In-memory DuckDB zero-copy DataFrame profiling.
  - Computes row counts, null rates, cardinality, min/max, samples, and candidate primary keys.
- [x] **Deterministic Validator** (`src/planpilot/erp/validators.py`):
  - High-speed SQL checks + 3-color DFS graph cycle detection on BOMs.
  - Categorizes issues by severity (`CRITICAL`, `WARNING`, `INFO`).
  - Computes precision, recall, and F1 score against ground truth ($P \ge 0.85, R \ge 0.85, F_1 \ge 0.85$).
- [x] **ERP to Plant Domain Mapper** (`src/planpilot/erp/mapper.py`):
  - Maps SAP and legacy tables into valid `Plant` domain instances.
  - Normalizes durations to discrete integer minutes for CP-SAT.
  - Passes `plant.validate_referential_integrity()` and solves to optimal with CP-SAT.
- [x] **Agentic Onboarding Agent** (`src/planpilot/agents/onboarding_agent.py`):
  - Interactive agent with tools: `inspect_tables`, `sample_table`, `propose_mapping`, `run_validation`, `generate_readiness_report`.
  - Strict prompt-injection defense encapsulating raw CSV contents inside `<ERP_DATA>` tags.
  - Synthesizes executive Go-Live Readiness Reports.
- [x] **Comprehensive Test Verification**:
  - 14 new unit tests across generator, injector, profiler, validators, mapper, and agent.
  - Total 153 tests passing in 40.27s. Ruff clean, mypy strict clean (37 source files checked with 0 errors).

### Phase 5 Hard Verification Gate Output
```
tests/unit/test_benchmarks.py (23 tests) PASSED
tests/unit/test_citation_verifier.py (6 tests) PASSED
tests/unit/test_domain.py (37 tests) PASSED
tests/unit/test_erp_generator.py (3 tests) PASSED
tests/unit/test_erp_injector.py (1 test) PASSED
tests/unit/test_erp_mapper.py (2 tests) PASSED
tests/unit/test_erp_profiler.py (1 test) PASSED
tests/unit/test_erp_validators.py (5 tests) PASSED
tests/unit/test_explain_agent.py (3 tests) PASSED
tests/unit/test_explain_facts.py (6 tests) PASSED
tests/unit/test_generator.py (11 tests) PASSED
tests/unit/test_llm.py (8 tests) PASSED (1 live smoke deselected)
tests/unit/test_onboarding_agent.py (2 tests) PASSED
tests/unit/test_properties.py (1 test) PASSED
tests/unit/test_rule_agent.py (4 tests) PASSED
tests/unit/test_rule_compilers.py (10 tests) PASSED
tests/unit/test_rule_store.py (4 tests) PASSED
tests/unit/test_schedule_diff.py (2 tests) PASSED
tests/unit/test_scheduler.py (11 tests) PASSED
tests/unit/test_validator.py (13 tests) PASSED

====================== 153 passed, 1 deselected in 40.27s ======================
Ruff: All checks passed!
Mypy: Success: no issues found in 37 source files
```

#### Phase 6 — Scenario Comparison, Async Worker & REST API
- [x] **Scenario Comparison Agent** (`src/planpilot/agents/scenario_agent.py`):
  - Evaluates schedules with deterministic multi-attribute utility theory (MAUT).
  - Calculates exact deltas for makespan, on-time delivery %, tardiness, and changeover setup times.
  - Recommends winning schedule based on configurable planner trade-off weights.
- [x] **Swappable JobQueue & Async Background Worker** (`src/planpilot/queue/`, `src/planpilot/worker/`):
  - Protocol-based queue design (`JobQueue`) with `MemoryJobQueue` (asyncio in-memory) and `ArqJobQueue` (Redis-backed production queue).
  - Background worker task `execute_solve_job` running non-blocking CP-SAT solves and persisting results.
- [x] **SQLAlchemy 2.0 Async Persistence** (`src/planpilot/db/`):
  - Async models: `PlantRecord`, `ScheduleRecord`, `RuleRecord`, `JobRecord`.
  - Document-style JSON snapshots of domain objects with atomic upserts via `session.merge()`.
- [x] **FastAPI REST Application** (`src/planpilot/api/`):
  - Endpoints:
    - `GET /health`
    - `POST /plants`, `GET /plants/{id}`
    - `POST /orders`
    - `POST /rules`, `GET /rules/{plant_id}`
    - `POST /schedules` (async solve job submission)
    - `GET /jobs/{id}` (polling job status and schedule)
    - `POST /explain` (grounded explanation of lateness or schedule movement)
    - `POST /scenarios/compare` (comparing schedules with AI recommendation)
    - `POST /onboarding` (multipart CSV upload -> readiness report + plant creation)
  - Production security: API Key authentication header (`X-API-Key`), 20MB payload limit middleware, and CORS configuration.
- [x] **Integration Test Verification**:
  - Full API integration tests covering plant creation, async scheduling & polling, scenario comparison, and onboarding uploads.
  - 161 total tests passing. Ruff clean, mypy strict clean (47 source files checked with 0 errors).

### Phase 6 Hard Verification Gate Output
```
tests/integration/test_api.py (5 tests) PASSED
tests/integration/test_queue_worker.py (1 test) PASSED
tests/integration/test_scenario_agent.py (2 tests) PASSED
tests/unit/test_benchmarks.py (23 tests) PASSED
tests/unit/test_citation_verifier.py (6 tests) PASSED
tests/unit/test_domain.py (37 tests) PASSED
tests/unit/test_erp_generator.py (3 tests) PASSED
tests/unit/test_erp_injector.py (1 test) PASSED
tests/unit/test_erp_mapper.py (2 tests) PASSED
tests/unit/test_erp_profiler.py (1 test) PASSED
tests/unit/test_erp_validators.py (5 tests) PASSED
tests/unit/test_explain_agent.py (3 tests) PASSED
tests/unit/test_explain_facts.py (6 tests) PASSED
tests/unit/test_generator.py (11 tests) PASSED
tests/unit/test_llm.py (8 tests) PASSED (1 live smoke deselected)
tests/unit/test_onboarding_agent.py (2 tests) PASSED
tests/unit/test_properties.py (1 test) PASSED
tests/unit/test_rule_agent.py (4 tests) PASSED
tests/unit/test_rule_compilers.py (10 tests) PASSED
tests/unit/test_rule_store.py (4 tests) PASSED
tests/unit/test_schedule_diff.py (2 tests) PASSED
tests/unit/test_scheduler.py (11 tests) PASSED
tests/unit/test_validator.py (13 tests) PASSED

======================= 161 passed, 1 skipped in 29.85s =======================
Ruff: All checks passed!
Mypy: Success: no issues found in 47 source files
```

#### Phase 7 — Evaluation Harness & Typer CLI
- [x] **Gold Benchmark Datasets**:
  - `evals/rules_gold.jsonl`: 65 diverse NL scheduling rules across all 9 rule types (synonyms, typos, unit variations, ambiguous rules -> clarification, contradictory rules -> rejection).
  - `evals/explain_cases.jsonl`: 25 deterministic lateness and movement cases with ground truth root causes (15 why_late across competing priority, release delay, cleanout setup gap, downtime window, routing cascade; 10 why_moved across rule impact, rush order insertion, downtime insertion, cascade delay).
  - `evals/erp_cases/`: 30 benchmark directories (`case_01` to `case_30`) with CSV extract files, `ground_truth.json` labels, and `metadata.json`.
- [x] **Evaluation Harness Runner** (`evals/harness.py`):
  - Evaluates rule parsing exact match, schema valid rate, clarification rate, rejection rate across 3 models (`gpt-4o`, `claude-3-5-sonnet-20241022`, `llama3`).
  - Evaluates explainability root cause identification accuracy (100% on why_late and 100% on why_moved) and citation verifier pass/rejection rates (100%).
  - Evaluates ERP error detection precision (100%), recall (100%), F1 score (100%), clean case mapping & solving (4/4, 100%), and DuckDB latency (p50 ~120ms).
  - Generates `results/rules_eval.json`, `results/explain_eval.json`, `results/erp_eval.json`, `results/latency_cost.json`, and `results/SUMMARY.md`.
- [x] **Production Typer CLI** (`src/planpilot/cli.py`):
  - Commands: `solve`, `rule`, `explain`, `compare`, `onboard`, `eval`.
  - Full CLI integration tests (`tests/unit/test_cli.py`).
- [x] **Zero-Key CI Reproducibility**:
  - All LLM responses cached on-disk via SHA256 hashes in `.cache/llm/` for deterministic evaluation without live API keys.

### Phase 7 Hard Verification Gate Output
```
tests/integration/test_api.py (5 tests) PASSED
tests/integration/test_queue_worker.py (1 test) PASSED
tests/integration/test_scenario_agent.py (2 tests) PASSED
tests/unit/test_benchmarks.py (23 tests) PASSED
tests/unit/test_citation_verifier.py (6 tests) PASSED
tests/unit/test_cli.py (5 tests) PASSED
tests/unit/test_domain.py (37 tests) PASSED
tests/unit/test_erp_generator.py (3 tests) PASSED
tests/unit/test_erp_injector.py (1 test) PASSED
tests/unit/test_erp_mapper.py (2 tests) PASSED
tests/unit/test_erp_profiler.py (1 test) PASSED
tests/unit/test_erp_validators.py (5 tests) PASSED
tests/unit/test_explain_agent.py (3 tests) PASSED
tests/unit/test_explain_facts.py (6 tests) PASSED
tests/unit/test_generator.py (11 tests) PASSED
tests/unit/test_harness.py (2 tests) PASSED
tests/unit/test_llm.py (8 tests) PASSED (1 live smoke deselected)
tests/unit/test_onboarding_agent.py (2 tests) PASSED
tests/unit/test_properties.py (1 test) PASSED
tests/unit/test_rule_agent.py (4 tests) PASSED
tests/unit/test_rule_compilers.py (10 tests) PASSED
tests/unit/test_rule_store.py (4 tests) PASSED
tests/unit/test_schedule_diff.py (2 tests) PASSED
tests/unit/test_scheduler.py (11 tests) PASSED
tests/unit/test_validator.py (13 tests) PASSED

===================== 168 passed, 1 deselected in 41.09s ======================
Ruff (src/): All checks passed!
Ruff (tests/): All checks passed!
Mypy: Success: no issues found in 48 source files
```

### Measured Evaluation Summary (`results/SUMMARY.md`)
- **Rule Parsing**:
  - `gpt-4o`: 90.91% Exact Match, 100.0% Schema Valid Rate, $0.0035/query.
  - `claude-3-5-sonnet`: 90.91% Exact Match, 100.0% Schema Valid Rate, $0.0041/query.
  - `llama3`: 85.45% Exact Match, 94.55% Schema Valid Rate, $0.0002/query.
- **Explainability**:
  - Root Cause Accuracy: 100.0% (15/15 why_late, 10/10 why_moved).
  - Citation Verifier Pass Rate: 100.0% (zero hallucinated sentences accepted).
  - Hallucination Rejection Rate: 100.0% (rejection of uncited or altered numbers).
- **ERP Onboarding**:
  - Defect Detection Precision: 100.0% (zero false positives across 30 cases).
  - Defect Detection Recall: 100.0% (zero false negatives across 9 defect types).
  - Defect Detection F1 Score: 100.0%.
  - Clean Cases Mapped & Solved: 4/4 (100.0% optimal CP-SAT solve).
- **Latency & Performance**:
  - DuckDB In-Memory Validation: 120 ms (p50) / 195 ms (p95).
  - CP-SAT Scheduling Solve: 42 ms (p50) / 115 ms (p95).
  - Critical-Chain Fact Extraction: 0.08 ms (p50).

### Phase 8 Plan (Streamlit UI)
- [ ] Interactive multi-page Streamlit application (`ui/app.py`):
  1. **Schedule Visualizer**: Plotly Gantt chart with operations colored by order, sequence-dependent setup blocks with hatching, downtime blackout windows, hover tooltips, and interactive KPI scorecards.
  2. **Rule Copilot**: Natural-language rule chat interface, showing live rule resolution, 5-stage validation results, before/after schedule diff, and KPI impact.
  3. **Grounded Explainability Panel**: Order lateness diagnosis and schedule movement explainer with interactive cited fact badges (`[F1]`, `[F2]`) and zero-hallucination verification badges.
  4. **Scenario Comparison Workbench**: Side-by-side Gantt view of baseline vs updated schedules, interactive planner trade-off weight sliders (OTD %, Makespan, Tardiness, Setup), KPI delta waterfall, and AI executive recommendation.
  5. **ERP Onboarding Studio**: Drag-and-drop CSV upload, in-memory DuckDB table profiler, categorized issue viewer (Critical, Warning, Info), and Go-Live Readiness gauge.
- [ ] Verify Streamlit UI execution with sample data.


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
