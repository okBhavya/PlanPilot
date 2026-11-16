# PlanPilot Architecture

## Overview

PlanPilot is a production-grade LLM copilot built on top of a CP-SAT constraint-programming scheduler. It demonstrates the integration of operations research solvers with large language models for advanced production scheduling.

## System Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                      UI (Streamlit)                             │
│  Gantt chart · Rule chat · Explain panel · Scenario compare     │
├─────────────────────────────────────────────────────────────────┤
│                    API (FastAPI + Auth)                          │
│  /rules · /schedules · /explain · /scenarios · /onboarding      │
├──────────────────────────┬──────────────────────────────────────┤
│     LLM Agents           │       Async Worker                   │
│  Rule · Explain ·        │   Solve jobs via JobQueue            │
│  Scenario · Onboarding   │   (arq + Redis)                     │
├──────────────────────────┼──────────────────────────────────────┤
│     Core Solver           │       Explainability                │
│  CP-SAT model builder    │   Fact extraction                   │
│  Constraint compilers    │   Critical-chain tracing            │
│  KPI calculation         │   Schedule diff                     │
│  Infeasibility analysis  │   Citation verification             │
├──────────────────────────┴──────────────────────────────────────┤
│                    Domain Models (Pydantic v2)                   │
│  Plant · Machine · Routing · Operation · Order · Schedule       │
├─────────────────────────────────────────────────────────────────┤
│                    Persistence                                   │
│  PostgreSQL (SQLAlchemy + Alembic) · DuckDB (ERP profiling)     │
└─────────────────────────────────────────────────────────────────┘
```

## Key Design Principles

1. **LLM never decides facts.** The solver and deterministic validators are the source of truth. The LLM proposes structured outputs and writes prose from structured facts only.

2. **Provider-agnostic LLM integration.** Model names and providers are configuration (env vars), never hard-coded. Supports OpenAI, Anthropic, and any OpenAI-compatible endpoint.

3. **Explainability is grounded.** Every explanation must cite solver-produced facts. The citation verifier rejects outputs with uncited claims.

4. **Data is not instructions.** File contents (ERP extracts, user rules) are treated as data, guarding against prompt injection.

## Domain Model

The domain model uses Pydantic v2 with frozen (immutable) models and strict validation:

- **Plant**: Top-level container with machines, work centers, materials, BOMs, routings, orders, changeover matrix, downtimes
- **Machine**: Physical machine with work center assignment and speed factor
- **Routing**: Ordered sequence of Operations for manufacturing a product
- **Operation**: Single manufacturing step with eligible machines and processing time
- **Order**: Production job linking to a routing, with priority, due date, release time
- **ChangeoverMatrix**: Sequence-dependent setup times between material families per machine
- **Schedule**: Solver output with Assignments, KPIs, and SolverStats
- **Assignment**: Operation → Machine mapping with start/end times and tight constraint trace

## Solver

Uses Google OR-Tools CP-SAT for flexible job-shop scheduling:

- **Interval variables** per operation × eligible machine (optional intervals + exactly-one)
- **Precedence constraints** within routings
- **NoOverlap** per machine
- **Sequence-dependent setups** via circuit constraint
- **Release times**, **machine downtime** windows, **due dates**
- **Decision trace** recording which constraints are tight per assignment

## Data Flow

```
User NL rule → Rule Agent → Parsed DSL → Validator → Constraint Compiler → CP-SAT → Schedule + KPIs
                                                                                         ↓
User question → Explain Agent → Fact Extractor → Critical Chain → Cited Explanation
```
