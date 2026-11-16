# PlanPilot Interview Notes

This document records design decisions, alternatives considered, trade-offs, algorithm complexity, and likely interview questions for each component.

---

## Phase 1 — Domain Models

### Decision: Pydantic v2 Frozen Models

**Choice:** All domain models use `model_config = ConfigDict(frozen=True)` (immutable).

**Why:** Manufacturing scheduling data should be treated as immutable snapshots. A schedule, once computed, is a fixed artifact. Mutations should produce new instances, making it easy to compare versions (schedule diff), track provenance, and avoid bugs from shared mutable state.

**Alternatives considered:**
1. *Mutable Pydantic models:* Simpler for in-place updates but risks subtle bugs when schedules are compared or cached.
2. *Plain dataclasses:* Lighter weight but lose Pydantic's validation, serialization, and schema generation for free.
3. *attrs:* Good immutability story but less ecosystem support for JSON Schema / OpenAPI integration.

**Trade-off:** Frozen models mean every "update" creates a new object. For our problem sizes (tens to hundreds of orders, not millions), the overhead is negligible. The benefit in correctness and debuggability vastly outweighs it.

### Decision: Typed Identifiers as `str` Aliases

**Choice:** `MachineId = str`, `OrderId = str`, etc. — simple type aliases.

**Why:** Readability and grep-ability. When you see `machine_id: MachineId`, the intent is clear. We considered `NewType` but it adds friction without runtime enforcement in Pydantic v2.

**Interview Q:** *Why not use NewType or a branded type?*
**A:** Pydantic v2 doesn't enforce NewType at validation boundaries, so it would give a false sense of safety. We rely on validators and referential integrity checks instead.

### Decision: Referential Integrity as a Method, Not a Validator

**Choice:** `Plant.validate_referential_integrity()` is an explicit method, not a `@model_validator`.

**Why:** Cross-entity validation (order → routing → operation → machine) requires all data to be present. Making it a model validator would mean you can never create a Plant with partial data (e.g., during incremental construction from ERP onboarding). The explicit method lets callers choose when to validate.

**Interview Q:** *How do you ensure data consistency?*
**A:** Layered validation: field-level (Pydantic validators), intra-model (model validators for invariants like "start < end"), and cross-model (explicit `validate_referential_integrity()` called before solving).

### Decision: MaterialFamily Enum for Changeover Logic

**Choice:** Fixed enum for material families rather than arbitrary string tags.

**Why:** The changeover matrix is a core scheduling concept. Using an enum ensures completeness (you can iterate all pairs), prevents typos, and makes the rule DSL type-safe.

**Alternative:** Arbitrary strings with a registry pattern. More flexible but loses compile-time safety and makes the changeover matrix sparse/unpredictable.

**Trade-off:** Adding a new material family requires a code change. In practice, material families change rarely (quarterly at most), so this is acceptable.

### Algorithm Complexity Notes

| Operation | Complexity | Notes |
|-----------|-----------|-------|
| `ChangeoverMatrix.get_setup_time()` | O(n) where n = entries | Linear scan; for production, use a dict-of-dict lookup (O(1)). Sufficient for current problem sizes. |
| `Plant.validate_referential_integrity()` | O(R·O·M + N) | Iterates all routings × operations × eligible machines. Dominated by the number of operations. |
| `PlantGenerator.generate()` | O(N·M + M²·F²) | N=orders, M=machines, F=material families (for changeover matrix). |

---

## Phase 1 — Plant Generator

### Decision: Seeded Deterministic Generation

**Choice:** Generator accepts a `seed` parameter and uses `random.Random(seed)` (instance-level RNG, not global).

**Why:** Reproducibility is critical for:
1. Test determinism — tests must produce the same result every run
2. Eval reproducibility — eval metrics must be reproducible
3. Debugging — can reproduce any issue by sharing the seed

**Interview Q:** *Why Random(seed) instead of random.seed(seed)?*
**A:** `random.seed()` affects the global RNG state, which is fragile (any library using random will interfere). Instance-level RNG (`Random(seed)`) is isolated and thread-safe.

---

## Phase 1 — Benchmark Loaders

### Decision: Standard OR-Library Format

**Choice:** Support the standard text format used by OR-Library for job-shop instances.

**Why:** These are the gold-standard benchmarks in scheduling research. ft06 (6×6, optimal=55) and ft10 (10×10, optimal=930) are universally cited. Including them proves our solver against known optima.

**Interview Q:** *How do you validate your solver?*
**A:** Against OR-Library benchmarks with known optimal solutions: ft06→55, ft10→930. Plus property-based tests (Hypothesis) checking no-overlap and precedence invariants on random instances.

---

## Phase 2 — CP-SAT Scheduler Core

### Decision: Pairwise Ordering Literals vs. Circuit Constraint for Sequence-Dependent Setups

**Choice:** Pairwise boolean ordering literals (`a_before_b`) with disjunctive setup constraints:
```python
model.add(ov_b.start >= ov_a.end + setup_ab).only_enforce_if(a_before_b)
model.add(ov_a.start >= ov_b.end + setup_ba).only_enforce_if(a_before_b.negated())
```

**Why:**
1. **Flexible Machine Alternatives:** In a Flexible Job Shop (FJSP), operations have alternative eligible machines modelled via optional interval variables (`presence` literals). Pairwise literals integrate natively with optional intervals by conditioning enforcement on `[a_before_b, presence_a, presence_b]`. Formulating `AddCircuit` with optional intervals requires introducing routing arcs for all machine assignments and dummy source/sink nodes per machine, substantially increasing model generation complexity.
2. **Direct Explainability & Critical Path Tracing:** In Phase 4, the explainability engine must trace *why* an operation waited on a machine. With pairwise literals, the boolean variable $b_{ij}$ directly indicates which job preceded which on machine $m$. Querying `solver.value(a_before_b)` yields immediate machine precedence.
3. **DSL Rule Compilability:** In Phase 3, rules like `ForbidSequence(machine, from_mat, to_mat, cleanout_min)` or `OrderPrecedence(order_a, order_b)` compile directly to simple linear implications on the existing pairwise literals (`a_before_b = 0` or $start_b \ge end_a + cleanout$).

**Complexity & Trade-offs:**
- Pairwise requires $O(N_m^2)$ boolean variables and constraints per machine, where $N_m$ is the number of operations on machine $m$. For typical factory work centers ($N_m \le 50$), $N_m^2 \le 2500$ constraints, which modern SAT solvers resolve in milliseconds via lazy clause generation and unit propagation.
- For extremely large $N_m > 500$, `AddCircuit` or reservoir formulations scale with fewer variables ($O(N_m)$ arcs), but sacrifice direct compatibility with optional intervals and explainability.

**Interview Q:** *Why not use AddNoOverlap2D or AddCircuit for setups?*
**A:** `AddNoOverlap2D` is designed for 2D spatial packing, not temporal transition matrices. `AddCircuit` enforces a single Hamiltonian circuit on successor arcs; while compact, it requires dummy start/end nodes and cumulative transit variables, making it brittle when operations conditionally execute on alternative machines. Pairwise literals with optional intervals provide robust propagation and allow direct querying of precedence decisions for explanation.

### Decision: Native AddNoOverlap with Planned Downtime Intervals

**Choice:** Planned machine downtime windows are represented as fixed (mandatory) intervals `model.new_interval_var(dt.start, dt.end - dt.start, dt.end, name)` and included directly in `model.add_no_overlap([op_intervals + dt_intervals])`.

**Why:** CP-SAT's native cumulative and disjunctive propagators use energetic reasoning and edge-finding algorithms. Treating downtimes as fixed intervals allows the solver's internal propagators to prune start/end variable bounds globally without needing manual big-M or boolean implication overhead.

### Decision: Decision Trace & Tight Constraint Extraction

**Choice:** For every assignment in the solved schedule, we determine which constraints are binding (slack = 0):
- `RELEASE_TIME`: $start = release\_time$
- `PREDECESSOR_END`: $start = prev\_op.end$ in the routing
- `MACHINE_PREDECESSOR_SETUP`: $start = prev\_machine\_op.end + setup\_time$ on the same machine
- `DOWNTIME_BOUNDARY`: $start = downtime.end$ or $end = downtime.start$
- `NONE`: Non-zero slack on all incoming constraints

**Why:** In an interview or production APS context, LLMs should NEVER guess why a job started when it did. The solver trace provides mathematical proof of which physical constraint determined the job's schedule. This deterministic trace directly feeds Phase 4's critical-chain analysis.

### Decision: Infeasibility Diagnosis via Assumption Literals

**Choice:** When an instance is infeasible, we use CP-SAT assumption literals (`model.add_assumptions`) to isolate a Minimal Unsatisfiable Core (MUC) via `solver.sufficient_assumptions_for_infeasibility()`.

**Why:**
- Traditional solvers return only "INFEASIBLE" without explanation.
- By associating an assumption literal with each constraint (due dates, release times, downtimes, horizon), CP-SAT computes the exact subset of conflicting constraints.
- We map these literal indices back to human-readable explanations (e.g. `"Order ORD-01: completion <= due date 10 conflicts with minimum routing duration 30"`).

**Interview Q:** *How does assumption-based infeasibility analysis work mathematically?*
**A:** In SAT/CP solvers, each soft constraint $C_i$ is guarded by an indicator literal $a_i \implies C_i$. When solving under assumptions $\{a_1, \dots, a_k\}$, if the solver proves unsatisfiability at the root, the final conflict clause learned by conflict analysis consists entirely of a negated subset of the assumption literals: $\neg a_{i_1} \lor \dots \lor \neg a_{i_m}$. This subset forms a minimal unsatisfiable core (MUC), mathematically proving that the corresponding constraints cannot be satisfied simultaneously.

---

*More sections will be added as phases are completed.*
