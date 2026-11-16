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

## Phase 3 — Rule DSL & Mathematical Constraint Compilers

### Decision: Pydantic v2 Discriminated Union for Scheduling Rules

**Choice:** The Rule DSL uses a discriminated union `Rule = Annotated[Union[ForbidSequence, ...], Field(discriminator="type")]` with an enum discriminator `RuleType`.

**Why:**
1. **Type Safety & Serialization:** Discriminated unions allow automatic polymorphic deserialization from JSON/dict in Pydantic v2 with zero runtime guesswork.
2. **Schema Generation for LLMs:** Pydantic can emit standard JSON Schema with `anyOf` and discriminator tags, making tool definitions for LLMs crystal clear and deterministic.
3. **Immutability & Hashability:** Rules are frozen models, making them safe to store, diff, and cache.

**Alternatives considered:**
- Class inheritance with manual type field: Prone to missing fields and requires custom deserialization code.
- Dynamic string DSL (e.g., custom grammar parsed via Lark or ANTLR): High implementation and maintenance complexity, poor error messages for LLMs, and lack of typed integration with FastAPI.

---

### Decision: Exact Formulation of Adjacency for `ForbidSequence`

**Challenge:** In manufacturing, "never run aluminium immediately after titanium on Machine 4 without a 30m cleanout" applies to **adjacent** transitions. If another job (e.g. Steel) runs between Titanium and Aluminium, or if Aluminium runs before Titanium, the rule is not violated.

**Mathematical Formulation in CP-SAT:**
For any pair $(A, B)$ on machine $m$ where material of $A$ is `from_material` and material of $B$ is `to_material`:
A violation occurs if and only if:
$$start_B \ge end_A \quad \land \quad start_B < end_A + cleanout \quad \land \quad \forall C \notin \{A, B\}: \neg (end_A \le start_C \land end_C \le start_B)$$

By De Morgan's laws, the valid condition (negation of violation) when both $A$ and $B$ are scheduled on machine $m$ is:
$$\underbrace{(start_A \ge end_B)}_{\text{Option 1: } B \text{ runs before } A} \lor \underbrace{(start_B \ge end_A + cleanout)}_{\text{Option 2: cleanout gap met (if } cleanout > 0)} \lor \bigvee_{C \notin \{A, B\}} \underbrace{(start_C \ge end_A \land start_B \ge end_C)}_{\text{Option 3: } C \text{ intervenes between } A \text{ and } B}$$

In CP-SAT, this is implemented cleanly without Big-M:
- Option 1 is reified via boolean variable $b\_before\_a$
- Option 2 is reified via boolean variable $gap\_ok$
- Option 3 creates a boolean variable $c\_between$ for each third operation $C$ on the machine
- The disjunction is enforced: `model.add_bool_or([b_before_a, gap_ok, ...c_between]).only_enforce_if(pres_a, pres_b)`.

**Interview Q:** *Why not use Big-M for sequencing constraints?*
**A:** Big-M formulations weaken the continuous LP relaxation in MIP solvers and degrade propagation strength in CP-SAT solvers because the domain bounds are inflated by large constants $M$. Using boolean indicator variables with native half-reified constraints (`only_enforce_if`) keeps variable domains tight and allows CP-SAT's SAT engine to prune domains directly during unit propagation.

---

### Decision: Subsegment Bounding for `BatchTogether`

**Choice:** For a batch of operations $S$ on machine $m$:
1. Compute the batch span: $min\_batch\_start = \min_{u \in S} start_u$ and $max\_batch\_end = \max_{u \in S} end_u$ using CP-SAT's native `add_min_equality` and `add_max_equality`.
2. For every outside operation $k \notin S$ on machine $m$, enforce disjunctive separation:
$$(end_k \le min\_batch\_start) \lor (start_k \ge max\_batch\_end)$$

**Complexity:** $O(|S| + |outside|)$ constraints. Extremely tight propagation.

---

### Decision: Combinatorial Invariant for `MaxConsecutiveRuns`

**Choice:** To prevent more than $k = max\_runs$ consecutive jobs of material family $F$ on machine $m$:
For every subset of size $k + 1$ matching operations on machine $m$:
If all $k + 1$ operations are present on machine $m$, there must exist at least one operation $v$ of a **different** material family scheduled between the earliest and latest operation of the subset.
If no other material operations exist on the machine, the sum of presence literals of family $F$ is constrained to $\le max\_runs$.

---

## Phase 3 — Five-Stage Deterministic Validation Pipeline

### Decision: Ordered Fast-Fail Validation Pipeline

**Choice:** Candidate rules must pass 5 sequential validation stages:
1. **Stage 1 (Schema):** Pydantic structural validation (correct fields, valid types, discriminator matching).
2. **Stage 2 (Entity Existence & Resolution):** Fuzzy matching and verification against plant entities (`MachineId`, `OrderId`, `MaterialFamily`).
3. **Stage 3 (Semantic Checks):** Invariant checks (release time vs due date, downtime boundaries vs plant horizon, non-negative setup times).
4. **Stage 4 (Conflict Detection):** Static conflict checks against currently active rules:
   - Direct contradiction: Pin vs Forbid on same (order, op, machine).
   - Incompatible pins: Pinning op to machine $M_1$ when already pinned to $M_2$.
   - Cycle detection: DFS cycle check on the directed graph of `OrderPrecedence` rules ($O(V + E)$).
5. **Stage 5 (Feasibility Check):** Solves a fast trial schedule with a short time limit (e.g. 3.0s) and assumption-based conflict core extraction.

**Why:** Staged fast-fail architecture maximizes responsiveness and minimizes solver overhead. Microsecond checks (Schema, Entity, Semantic, Conflict) catch 95% of invalid or contradictory rules before touching CP-SAT. The solver is only invoked in Stage 5 to verify mathematical feasibility.

**Interview Q:** *How do you detect cycles in OrderPrecedence rules?*
**A:** We construct a directed graph where orders are vertices and precedence rules are directed edges $(u, v)$. When a new rule $(A, B)$ is proposed, we run a Depth-First Search (DFS) starting from $B$. If $A$ is reachable from $B$, adding $(A, B)$ would complete a directed cycle, which is mathematically impossible for scheduling. We catch this in $O(V + E)$ time and return a human-readable conflict error immediately without running the solver.

---

## Phase 3 — LLM Provider Abstraction & Architecture

### Decision: Multi-Provider Abstraction with Pluggable Endpoints

**Choice:** `LLMClient` supports `openai`, `anthropic`, and `openai_compatible` (Ollama, vLLM, Groq). All model names, providers, and API keys are read from environment variables with sensible defaults.

### Decision: SHA256 Disk Response Cache

**Choice:** An on-disk response cache (`DiskCache`) hashes the normalized input payload (model + messages + tools + temperature) with SHA256:
- **Cache Hit:** Returns cached tool calls, content, and token counts with `latency=0.0s` and `cost=0.0`.
- **Why:** Essential for deterministic evaluations, regression testing, and CI cost containment. Evals run in seconds without calling external LLM APIs.

### Decision: Data/Instruction Segregation (Prompt Injection Defense)

**Choice:** All user inputs and natural-language rules are encapsulated in XML data tags (`<USER_SCHEDULING_RULE>...</USER_SCHEDULING_RULE>`). The system prompt instructs the LLM that content inside these tags is purely data to be extracted, never instructions to be followed.

---

*More sections will be added as phases are completed.*
