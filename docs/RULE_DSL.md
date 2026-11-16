# PlanPilot Rule DSL Specification

## 1. Overview

The **PlanPilot Rule DSL** provides a strongly-typed, declarative representation of operational scheduling constraints. In factory operations, planners frequently state business rules in natural language (e.g. *"never run aluminium after titanium on CNC-4 without cleanout"*). 

The Rule DSL bridges natural language and constraint programming by providing:
1. A **Pydantic v2 Discriminated Union** with JSON Schema export.
2. Direct **CP-SAT Mathematical Compilers** without Big-M approximations.
3. A **Five-Stage Deterministic Validation Pipeline** ensuring structural integrity, entity resolution, semantic bounds, cycle/conflict detection, and mathematical feasibility before solver injection.

---

## 2. Rule DSL Grammar & Types

The top-level model is `Rule`, a discriminated union on the `type` field.

```python
Rule = Annotated[
    Union[
        ForbidSequence,
        MinChangeover,
        MachineUnavailable,
        PinToMachine,
        ForbidMachine,
        OrderPrecedence,
        MaxConsecutiveRuns,
        HardDueDate,
        BatchTogether,
    ],
    Field(discriminator="type"),
]
```

---

### 2.1 ForbidSequence
Forbids two material families from executing consecutively on a machine unless separated by a designated cleanout gap.

```json
{
  "type": "forbid_sequence",
  "machine_id": "M1",
  "from_material": "titanium",
  "to_material": "aluminium",
  "unless_cleanout_min": 30
}
```

**CP-SAT Formulation:**
For every pair of operations $(i, j)$ eligible on `machine_id` where $mat(i) = \text{from\_material}$ and $mat(j) = \text{to\_material}$:
$$\text{is\_adjacent}(i, j) \implies \text{start}_j \ge \text{end}_i + \text{cleanout\_min}$$
where $\text{is\_adjacent}(i, j) \iff \text{a\_before\_b}(i, j) \land \neg \bigvee_{k} (\text{a\_before\_b}(i, k) \land \text{a\_before\_b}(k, j))$.

---

### 2.2 MinChangeover
Enforces a universal minimum setup duration between any consecutive operations on a machine.

```json
{
  "type": "min_changeover",
  "machine_id": "M1",
  "min_setup_min": 15
}
```

**CP-SAT Formulation:**
For any consecutive pair $(i, j)$ on `machine_id`:
$$\text{start}_j \ge \text{end}_i + \text{min\_setup\_min}$$

---

### 2.3 MachineUnavailable
Injects a planned downtime blackout window during which no operation may execute on the machine.

```json
{
  "type": "machine_unavailable",
  "machine_id": "M1",
  "start": 100,
  "end": 160,
  "reason": "Spindle overhaul"
}
```

**CP-SAT Formulation:**
A fixed CP-SAT interval variable $I_{\text{downtime}} = [\text{start}, \text{end}, \text{duration}]$ is added to the machine's native `AddNoOverlap` constraint group:
$$\text{NoOverlap}(\{I_{op} \mid op \in \text{eligible}\} \cup \{I_{\text{downtime}}\})$$

---

### 2.4 PinToMachine
Forces an order's operation to execute strictly on a specific machine.

```json
{
  "type": "pin_to_machine",
  "order_id": "ORD-001",
  "operation_id": "OP10",
  "machine_id": "M1"
}
```

**CP-SAT Formulation:**
Sets the presence indicator literal of the specified machine to true, and all alternative machines to false:
$$\text{presence}(op, \text{machine\_id}) = 1, \quad \forall m \ne \text{machine\_id}: \text{presence}(op, m) = 0$$

---

### 2.5 ForbidMachine
Prohibits an operation from executing on a specific machine.

```json
{
  "type": "forbid_machine",
  "order_id": "ORD-001",
  "operation_id": "OP10",
  "machine_id": "M2"
}
```

**CP-SAT Formulation:**
$$\text{presence}(op, \text{machine\_id}) = 0$$

---

### 2.6 OrderPrecedence
Enforces cross-order precedence: Order A must complete before Order B can start, with an optional minimum idle gap.

```json
{
  "type": "order_precedence",
  "before_order_id": "ORD-001",
  "after_order_id": "ORD-002",
  "min_gap_min": 15
}
```

**CP-SAT Formulation:**
For the terminal operation of Order A ($op_A^{\text{final}}$) and initial operation of Order B ($op_B^{\text{init}}$):
$$\text{start}(op_B^{\text{init}}) \ge \text{end}(op_A^{\text{final}}) + \text{min\_gap\_min}$$

---

### 2.7 MaxConsecutiveRuns
Restricts the maximum number of consecutive operations of a given material family on a machine before requiring a material transition.

```json
{
  "type": "max_consecutive_runs",
  "machine_id": "M1",
  "material_family": "steel",
  "max_runs": 3
}
```

**CP-SAT Formulation:**
For any sub-sequence of $K = \text{max\_runs} + 1$ operations sharing `material_family` on `machine_id`, they cannot all be contiguous without an intervening alternative material operation.

---

### 2.8 HardDueDate
Converts a soft tardiness objective into a strict, non-negotiable completion deadline.

```json
{
  "type": "hard_due_date",
  "order_id": "ORD-001",
  "deadline": 250
}
```

**CP-SAT Formulation:**
$$\text{end}(op_{\text{final}}) \le \text{deadline}$$

---

### 2.9 BatchTogether
Forces a set of orders to be scheduled consecutively on a target machine without interleaving jobs from other orders.

```json
{
  "type": "batch_together",
  "order_ids": ["ORD-001", "ORD-002", "ORD-003"],
  "machine_id": "M1"
}
```

**CP-SAT Formulation:**
The cumulative span of the batched operations on `machine_id` equals the sum of their processing times and internal setups, forbidding external jobs from starting within the batch time window:
$$\max_{i \in \text{batch}} \text{end}_i - \min_{i \in \text{batch}} \text{start}_i = \sum_{i \in \text{batch}} \text{duration}_i + \sum \text{internal\_setups}$$

---

## 3. Five-Stage Deterministic Validation Pipeline

Before any candidate rule is compiled into CP-SAT, it passes through 5 ordered validation stages:

| Stage | Name | Checks Performed | Latency |
|---|---|---|---|
| **Stage 1** | **Schema** | Pydantic v2 type checking, non-negative numbers, valid discriminators | $< 0.1\text{ ms}$ |
| **Stage 2** | **Entity Resolution** | Fuzzy name matching to plant `MachineId`, `OrderId`, `MaterialFamily` | $< 0.5\text{ ms}$ |
| **Stage 3** | **Semantic Checks** | Release dates vs due dates, downtime within plant horizon | $< 0.2\text{ ms}$ |
| **Stage 4** | **Static Conflict** | Contradictory pins (Pin vs Forbid), precedence graph cycles ($O(V+E)$ DFS) | $< 1.0\text{ ms}$ |
| **Stage 5** | **CP-SAT Feasibility** | Trial solve with assumption literals to detect mathematical infeasibility | $\sim 40\text{ ms}$ |
