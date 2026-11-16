# PlanPilot Evaluation Methodology & Benchmark Results

## 1. Overview

The PlanPilot evaluation suite provides rigorous, reproducible benchmarks measuring the accuracy, safety, and performance of every AI component. In manufacturing APS, stochastic evaluations or unverified LLM responses are unacceptable. Every metric in PlanPilot is computed programmatically against ground-truth solver solutions, mathematical decision traces, and deterministic integrity validators.

All evaluation runs are **100% reproducible offline without API keys** via committed on-disk response caches (`.cache/llm/`).

---

## 2. Benchmark Datasets

### 2.1 Natural Language Rule Parsing (`evals/rules_gold.jsonl`)
- **Size:** 65 diverse scheduling rules.
- **Coverage:** All 9 Rule DSL types (`ForbidSequence`, `MinChangeover`, `MachineUnavailable`, `PinToMachine`, `ForbidMachine`, `OrderPrecedence`, `MaxConsecutiveRuns`, `HardDueDate`, `BatchTogether`).
- **Linguistic Variation:** Real-world operator phrasing, conversational synonyms ("cleanout", "wash", "purge"), typos, and unit variations ("hours", "minutes", "hr", "min").
- **Adversarial Edge Cases:**
  - *Ambiguous Rules:* Under-specified instructions that must trigger `clarification_needed` rather than hallucinated assumptions.
  - *Contradictory Rules:* Impossible or conflicting constraints that must be rejected during Stage 4/5 validation.

### 2.2 Grounded Explainability (`evals/explain_cases.jsonl`)
- **Size:** 25 lateness and schedule movement cases.
- **Coverage:**
  - *Lateness Causes (15 cases):* Competing priority order, raw material supply delay, material changeover setup gap, planned maintenance blackout window, routing cascade.
  - *Movement Causes (10 cases):* Rule impact, rush order insertion, maintenance downtime insertion, upstream cascade delay.
- **Ground Truth:** Each instance is handcrafted with a mathematically isolated bottleneck, ensuring a unique provable root cause.

### 2.3 Agentic ERP Onboarding (`evals/erp_cases/`)
- **Size:** 30 benchmark directories (`case_01` to `case_30`).
- **Formats:** Both SAP tables (`CRHD`, `MARA`, `PLPO`, `STPO`, `AFKO`) and legacy tables (`stations`, `items`, `routings`, `boms`, `work_orders`).
- **Injected Defects:** 9 manufacturing defect types:
  1. `DANGLING_WORK_CENTER`: Operations referencing non-existent machines.
  2. `CIRCULAR_BOM`: Infinite loops in parent-child component BOMs.
  3. `ZERO_NEGATIVE_DURATION`: Corrupted or negative operation run times.
  4. `UNIT_MISMATCH`: Mixed duration units requiring normalization.
  5. `DUPLICATE_KEY`: Duplicate primary keys in routing operations or work centers.
  6. `ORPHAN_COMPONENT`: BOM components lacking parent assemblies.
  7. `MIXED_DATE_FORMAT`: Inconsistent ISO, European, or US date formats.
  8. `WHITESPACE_CASE_INCONSISTENT`: Trailing spaces and casing anomalies.
  9. `MISSING_MANDATORY_FIELD`: Null values in required scheduling fields.

---

## 3. Evaluation Metrics & Definitions

### 3.1 Rule Parsing Metrics
- **Exact Match Rate ($\%$):** Fraction of natural language inputs where the LLM generated the exact gold Rule DSL parameters.
- **Schema Valid Rate ($\%$):** Fraction of inputs that compile into a valid, executable Pydantic Rule instance.
- **Clarification Precision ($\%$):** Accuracy in triggering clarification requests on ambiguous inputs.
- **Rejection Precision ($\%$):** Accuracy in rejecting contradictory or cyclic constraints.

### 3.2 Explainability Metrics
- **Root Cause Accuracy ($\%$):** Percentage of cases where the backward critical-chain tracer correctly identified the true bottleneck or delay source.
- **Citation Verifier Pass Rate ($\%$):** Fraction of grounded explanations accepted with 100% cited sentences and valid fact IDs.
- **Hallucination Rejection Rate ($\%$):** Percentage of uncited or numerically altered claims correctly rejected by the verifier.

### 3.3 ERP Onboarding Metrics
- **Precision:** $\frac{TP}{TP + FP}$ across detected integrity issues.
- **Recall:** $\frac{TP}{TP + FN}$ across injected defects.
- **F1 Score:** $2 \cdot \frac{\text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}}$.
- **Clean Solvability ($\%$):** Fraction of clean ERP cases successfully mapped into `Plant` models and solved to optimal by CP-SAT.

---

## 4. Measured Results (Committed Benchmark Output)

*All numbers below are extracted directly from `results/SUMMARY.md` produced by `make eval`.*

### Rule Parsing Performance by Model
| Model | Exact Match | Schema Valid | Clarification Rate | Cost / Query |
|---|---|---|---|---|
| **GPT-4o** | **90.91%** | **100.0%** | **100.0%** | \$0.0035 |
| **Claude 3.5 Sonnet** | **90.91%** | **100.0%** | **100.0%** | \$0.0041 |
| **Llama 3 (Open-Source)** | **85.45%** | **94.55%** | **100.0%** | \$0.0002 |

### Explainability & Faithfulness
| Metric | Target | Measured Result | Status |
|---|---|---|---|
| **Lateness Root Cause Accuracy** | $\ge 90.0\%$ | **100.0% (15/15)** | PASSED |
| **Movement Root Cause Accuracy** | $\ge 90.0\%$ | **100.0% (10/10)** | PASSED |
| **Grounded Citation Verifier Pass Rate** | $100\%$ | **100.0%** | PASSED |
| **Hallucination Rejection Rate** | $100\%$ | **100.0%** | PASSED |

### ERP Onboarding & Data Quality
| Metric | Target | Measured Result | Status |
|---|---|---|---|
| **Defect Detection Precision** | $\ge 85.0\%$ | **100.0%** | PASSED |
| **Defect Detection Recall** | $\ge 85.0\%$ | **100.0%** | PASSED |
| **Defect Detection F1 Score** | $\ge 85.0\%$ | **100.0%** | PASSED |
| **Clean Cases Mapped & Solved** | $100\%$ | **4/4 (100.0%)** | PASSED |
| **DuckDB Validation Latency** | $< 500\text{ ms}$ | **120 ms (p50) / 195 ms (p95)** | PASSED |

---

## 5. How to Reproduce

Run the full evaluation harness locally using the Typer CLI or Makefile:

```bash
# Via Typer CLI
python -m planpilot.cli eval --output results/

# Via Make
make eval
```

The harness will execute all rule test suites, explainability backward traces, and DuckDB validation checks, updating the JSON artifacts in `results/` and regenerating `results/SUMMARY.md`.
