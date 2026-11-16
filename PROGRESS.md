# PlanPilot Progress

## Current Status: Phase 1 — Domain + Data ✅

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
- [x] Comprehensive test suite:
  - Domain model validation tests (positive + negative cases)
  - JSON round-trip serialization tests
  - Generator determinism test
  - Generator referential integrity test
  - Benchmark loader tests for all instances

### What's Next: Phase 2 — CP-SAT Scheduler Core
- Flexible job-shop CP-SAT model with interval variables
- Alternative machines (optional intervals + exactly-one)
- Precedence, NoOverlap, sequence-dependent setups
- Release times, downtimes, due dates
- Objectives: makespan, weighted tardiness, lexicographic
- Decision trace: tight constraint tracking per assignment
- Infeasibility analysis with assumption literals
- KPI calculation
- Acceptance: ft06 → 55, ft10 → ≤930

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
