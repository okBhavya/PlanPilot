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

*More sections will be added as phases are completed.*
